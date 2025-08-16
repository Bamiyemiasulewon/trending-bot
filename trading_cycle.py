import asyncio
import logging
import random
import json
import time
import os
import math
import requests
from datetime import datetime, timezone
from typing import Dict, List, Tuple, Optional
from decimal import Decimal
from web3 import Web3
from web3.types import TxReceipt
from dotenv import load_dotenv

load_dotenv()

class TradingCycle:
    def __init__(self, wallet_manager, web3: Web3, token_address: str, chain: str = 'BNB'):
        self.wallet_manager = wallet_manager
        self.web3 = web3
        self.token_address = token_address
        # Normalize chain and set chain-specific params
        self.chain = (chain or 'BNB').upper()
        self.is_running = False
        self.current_cycle = 0
        self.wallets = []
        self.token_contract = None
        self.router_contract = None
        
        # Wallet balance constraints
        self.min_wallet_balance = 0.00085  # native units fallback (e.g., BNB/ETH)
        # Optional USD-based minimum; if >0, overrides native minimum using live price
        try:
            self.min_wallet_balance_usd = float(os.getenv('MIN_WALLET_BALANCE_USD', '0'))
        except Exception:
            self.min_wallet_balance_usd = 0.0
        try:
            self.max_wallet_balance_pct = float(os.getenv('MAX_WALLET_BALANCE_PCT', '0.25'))
        except Exception:
            self.max_wallet_balance_pct = 0.25  # Maximum percentage of wallet balance to use (25% by default)
        
        # Base buy ranges (native) and current scaled values (tuned for tiny balances)
        env_min_buy_wei = os.getenv('BASE_MIN_BUY_WEI')
        env_max_buy_wei = os.getenv('BASE_MAX_BUY_WEI')
        try:
            self.base_min_buy_wei = int(env_min_buy_wei) if env_min_buy_wei else Web3.to_wei(0.0002, 'ether')
        except Exception:
            self.base_min_buy_wei = Web3.to_wei(0.0002, 'ether')
        try:
            self.base_max_buy_wei = int(env_max_buy_wei) if env_max_buy_wei else Web3.to_wei(0.0004, 'ether')
        except Exception:
            self.base_max_buy_wei = Web3.to_wei(0.0004, 'ether')
        self.min_buy_amount = int(self.base_min_buy_wei)
        self.max_buy_amount = int(self.base_max_buy_wei)
        # Gas buffer in native units (float), overridable via env GAS_BUFFER
        try:
            self.gas_buffer = float(os.getenv('GAS_BUFFER', '0.0001'))
        except Exception:
            self.gas_buffer = 0.0001
        self.max_trades_per_wallet = 15  # buy + sell combined (per day)
        self.trade_counts = {}  # legacy total counter (kept for compatibility)
        self.trade_counts_today = {}  # address -> int (per UTC day)
        self.last_reset_date = None  # 'YYYY-MM-DD' string
        # Wait before starting sells (in seconds), default 15 minutes
        self.wait_before_sell = int(os.getenv('TRADE_WAIT_BEFORE_SELL', '900'))
        # Sell scheduling and buyback configuration
        self.sell_delay_min = int(os.getenv('SELL_RANDOM_DELAY_MIN_SEC', '60'))
        self.sell_delay_max = int(os.getenv('SELL_RANDOM_DELAY_MAX_SEC', '900'))
        self.buyback_delay = int(os.getenv('BUYBACK_DELAY_SEC', '1800'))  # 30 minutes
        # Preserve a minimum native balance to ensure future gas (smaller default for tiny balances)
        self.min_native_balance_to_keep = Web3.to_wei(float(os.getenv('MIN_NATIVE_BALANCE_TO_KEEP', '0.0002')), 'ether')
        # Cooldown between actions from the same wallet to avoid back-to-back self-swaps
        try:
            self.min_wallet_cooldown_sec = int(os.getenv('MIN_WALLET_COOLDOWN_SEC', '90'))
        except Exception:
            self.min_wallet_cooldown_sec = 90
        # Organic behavior controls
        try:
            self.skip_prob_per_cycle = float(os.getenv('SKIP_PROB_PER_CYCLE', '0.12'))  # 12% chance a wallet skips this cycle
        except Exception:
            self.skip_prob_per_cycle = 0.12
        try:
            self.quiet_cycle_prob = float(os.getenv('QUIET_CYCLE_PROB', '0.10'))  # 10% chance the whole cycle has a quiet pause at start
        except Exception:
            self.quiet_cycle_prob = 0.10
        try:
            self.quiet_cycle_pause_min = int(os.getenv('QUIET_CYCLE_PAUSE_MIN_SEC', '45'))
            self.quiet_cycle_pause_max = int(os.getenv('QUIET_CYCLE_PAUSE_MAX_SEC', '180'))
        except Exception:
            self.quiet_cycle_pause_min, self.quiet_cycle_pause_max = 45, 180
        try:
            self.gas_fast_jitter = float(os.getenv('GAS_FAST_JITTER', '0.05'))  # ±5% jitter
        except Exception:
            self.gas_fast_jitter = 0.05
        try:
            self.slippage_jitter = float(os.getenv('SLIPPAGE_JITTER', '0.02'))  # ±2% relative jitter
        except Exception:
            self.slippage_jitter = 0.02
        # Lognormal sizing parameters (multiplicative factor around 1.0)
        try:
            self.size_logn_sigma = float(os.getenv('SIZE_LOGN_SIGMA', '0.25'))  # shape (0.25 ~ gentle)
        except Exception:
            self.size_logn_sigma = 0.25
        # Trading safety/config
        try:
            self.slippage = float(os.getenv('SLIPPAGE', '0.12'))  # 0.12 = 12%
        except Exception:
            self.slippage = 0.12
        # Extra allowance to account for fee-on-transfer or high tax tokens (in bps)
        try:
            self.fot_allowance_bps = int(os.getenv('FOT_ALLOWANCE_BPS', '500'))  # 500 = 5%
        except Exception:
            self.fot_allowance_bps = 500
        # Maximum slippage cap (decimal). Allows increasing over retries for illiquid pairs
        try:
            self.max_slippage = float(os.getenv('MAX_SLIPPAGE', '0.30'))  # up to 30%
        except Exception:
            self.max_slippage = 0.30
        # If true, last retry will set minOut=1 to avoid revert (use with caution)
        self.allow_last_resort_minout1 = os.getenv('ALLOW_LAST_RESORT_MINOUT1', 'false').lower() == 'true'
        # Swap deadline seconds (was fixed 120)
        try:
            self.deadline_sec = int(os.getenv('DEADLINE_SEC', '120'))
        except Exception:
            self.deadline_sec = 120
        self.retry_max = int(os.getenv('TRADE_RETRY_MAX', '2'))
        self.retry_backoff = float(os.getenv('TRADE_RETRY_BACKOFF', '1.5'))
        self.approve_infinite = os.getenv('APPROVE_INFINITE', 'false').lower() == 'true'
        # Dynamic amount scaling across cycles
        self.buy_amount_scale = 1.0
        self.buy_scale_min = float(os.getenv('BUY_SCALE_MIN', '0.5'))
        self.buy_scale_max = float(os.getenv('BUY_SCALE_MAX', '2.0'))
        self.buy_scale_step_min = float(os.getenv('BUY_SCALE_STEP_MIN', '0.1'))
        self.buy_scale_step_max = float(os.getenv('BUY_SCALE_STEP_MAX', '0.3'))
        self.adjust_interval_min_cycles = int(os.getenv('ADJUST_INTERVAL_MIN_CYCLES', '2'))
        self.adjust_interval_max_cycles = int(os.getenv('ADJUST_INTERVAL_MAX_CYCLES', '3'))
        # Sell fraction range (portion of token balance to sell)
        self.sell_frac_low = float(os.getenv('SELL_FRAC_LOW', '0.4'))
        self.sell_frac_high = float(os.getenv('SELL_FRAC_HIGH', '0.7'))
        self._cycles_until_adjust = random.randint(self.adjust_interval_min_cycles, self.adjust_interval_max_cycles)
        # Track last action timestamps to enforce cooldowns
        self._last_action_ts = {}
        # Persist daily counters
        self.state_dir = os.path.join(os.path.dirname(__file__), 'data')
        self.state_file = os.path.join(self.state_dir, 'trade_state.json')
        # Chain-specific configuration
        # Defaults to BNB (BSC)
        if self.chain == 'ETH':
            # Uniswap V2 router and common tokens for ETH mainnet
            self.chain_id = 1
            self.router_address_raw = '0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D'  # Uniswap V2
            self.wnative_address_raw = '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2'  # WETH
            self.stable1_address_raw = '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eb48'  # USDC
            self.stable2_address_raw = '0xdAC17F958D2ee523a2206206994597C13D831ec7'  # USDT
        else:
            # BNB Chain (BSC): PancakeSwap V2 router and common tokens
            self.chain = 'BNB'
            self.chain_id = 56
            self.router_address_raw = '0x10ED43C718714eb63d5aA57B78B54704E256024E'  # Pancake V2
            self.wnative_address_raw = '0xBB4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c'  # WBNB
            self.stable1_address_raw = '0xe9e7cea3dedca5984780bafc599bd69add087d56'  # BUSD (legacy)
            self.stable2_address_raw = '0x55d398326f99059fF775485246999027B3197955'  # USDT

        self.load_abis()
        
        # Initialize token contract
        if not self.web3.is_address(self.token_address):
            raise ValueError(f"Invalid token address: {self.token_address}")
        self.token_address = self.web3.to_checksum_address(self.token_address)
        self.token_contract = self.web3.eth.contract(
            address=self.token_address,
            abi=self.token_abi
        )
        
        # Verify token contract has required methods and is tradable
        try:
            # Check basic token functions
            self.token_contract.functions.balanceOf(self.token_address).call()
            decimals = self.token_contract.functions.decimals().call()
            symbol = self.token_contract.functions.symbol().call()
            
            # Check if token has liquidity by trying to get a quote
            paths = self._candidate_paths(self.token_address)
            logging.info(f"Checking liquidity for {symbol} ({self.token_address})...")
            
            # Try to get a quote with minimal amount
            test_amount = 1  # Smallest possible amount
            test_quote, _ = self._best_out_quote(test_amount, paths)
            
            if not test_quote or test_quote == 0:
                raise ValueError(f"No liquidity found for token {symbol} - cannot trade")
                
            logging.info(f"Token {symbol} has sufficient liquidity")
            
        except Exception as e:
            raise ValueError(f"Token contract validation failed: {str(e)}")
            
        # Initialize router contract
        self.router_contract = self.web3.eth.contract(
            address=self.router_address,
            abi=self.router_abi
        )

    def load_abis(self):
        """Load ABIs for token and router contracts"""
        # Minimal ABIs required for operations
        self.token_abi = [
            {"constant": True, "inputs": [{"name": "owner", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
            {"constant": True, "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}], "name": "allowance", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
            {"constant": False, "inputs": [{"name": "spender", "type": "address"}, {"name": "value", "type": "uint256"}], "name": "approve", "outputs": [{"name": "", "type": "bool"}], "type": "function"},
            {"constant": False, "inputs": [{"name": "to", "type": "address"}, {"name": "value", "type": "uint256"}], "name": "transfer", "outputs": [{"name": "", "type": "bool"}], "type": "function"},
            {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
            {"constant": True, "inputs": [], "name": "symbol", "outputs": [{"name": "", "type": "string"}], "type": "function"},
        ]
        # PancakeSwap V2 Router ABI subset
        self.router_abi = [
            {"inputs": [{"internalType": "uint256", "name": "amountOutMin", "type": "uint256"}, {"internalType": "address[]", "name": "path", "type": "address[]"}, {"internalType": "address", "name": "to", "type": "address"}, {"internalType": "uint256", "name": "deadline", "type": "uint256"}], "name": "swapExactETHForTokensSupportingFeeOnTransferTokens", "outputs": [], "stateMutability": "payable", "type": "function"},
            {"inputs": [{"internalType": "uint256", "name": "amountIn", "type": "uint256"}, {"internalType": "uint256", "name": "amountOutMin", "type": "uint256"}, {"internalType": "address[]", "name": "path", "type": "address[]"}, {"internalType": "address", "name": "to", "type": "address"}, {"internalType": "uint256", "name": "deadline", "type": "uint256"}], "name": "swapExactTokensForETHSupportingFeeOnTransferTokens", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
            {"inputs": [{"internalType": "uint256", "name": "amountIn", "type": "uint256"}, {"internalType": "address[]", "name": "path", "type": "address[]"}], "name": "getAmountsOut", "outputs": [{"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}], "stateMutability": "view", "type": "function"}
        ]
        
        if self.token_address:
            self.token_contract = self.web3.eth.contract(
                address=self.web3.to_checksum_address(self.token_address),
                abi=self.token_abi
            )
        
        # Chain-mapped router and tokens
        self.router_address = self.web3.to_checksum_address(self.router_address_raw)
        # Keep legacy attribute names for minimal code changes
        self.wbnb_address = self.web3.to_checksum_address(self.wnative_address_raw)
        # Map stables: on ETH, "busd" will act as USDC to reuse paths
        self.busd_address = self.web3.to_checksum_address(self.stable1_address_raw)
        self.usdt_address = self.web3.to_checksum_address(self.stable2_address_raw)
        self.router_contract = self.web3.eth.contract(
            address=self.router_address,
            abi=self.router_abi
        )

    # --- Price helpers (CoinGecko) ---
    def _coingecko_id(self) -> str:
        try:
            return 'binancecoin' if (self.chain or 'BNB').upper() == 'BNB' else 'ethereum'
        except Exception:
            return 'binancecoin'

    def _fetch_native_price_usd(self) -> Optional[float]:
        try:
            cid = self._coingecko_id()
            resp = requests.get(
                'https://api.coingecko.com/api/v3/simple/price',
                params={'ids': cid, 'vs_currencies': 'usd'},
                timeout=5
            )
            data = resp.json()
            price = float(data.get(cid, {}).get('usd', 0))
            if price > 0:
                return price
        except Exception as e:
            logging.warning(f"Price fetch failed: {e}")
        return None

    def _compute_min_required_native(self) -> float:
        """Return the minimum required native balance (in BNB/ETH) considering USD threshold if set."""
        try:
            if getattr(self, 'min_wallet_balance_usd', 0) and self.min_wallet_balance_usd > 0:
                price = self._fetch_native_price_usd()
                if price and price > 0:
                    usd_native = float(self.min_wallet_balance_usd) / float(price)
                    return max(float(self.min_wallet_balance), float(usd_native))
        except Exception:
            pass
        return float(self.min_wallet_balance)

    async def start(self):
        """Start the trading cycle with wallet validation"""
        if self.is_running:
            return "Trading cycle is already running"
            
        # Check if we have enough funded wallets
        funded_wallets = await self._get_funded_wallets()
        if len(funded_wallets) < 2:  # Need at least 2 wallets (1 for each group)
            # Compose threshold text depending on configured mode
            min_native = self._compute_min_required_native()
            thresh_txt = f"{min_native:.6f} {self.chain}"
            if self.min_wallet_balance_usd and self.min_wallet_balance_usd > 0:
                thresh_txt += f" (~${self.min_wallet_balance_usd:.2f})"
            return f"❌ Not enough funded wallets. Need at least 2 wallets with ≥ {thresh_txt}"
            
        self.is_running = True
        self.current_cycle = 0
        
        # Use only funded wallets and initialize trade counts
        self.wallets = funded_wallets
        # Split into two groups (A/B) roughly evenly
        half = max(1, len(self.wallets) // 2)
        self.group_a = self.wallets[:half]
        self.group_b = self.wallets[half:]
        for wallet in self.wallets:
            addr = wallet.get('address')
            if addr and addr not in self.trade_counts:
                self.trade_counts[addr] = 0
            if addr and addr not in self.trade_counts_today:
                self.trade_counts_today[addr] = 0
        
        # Start the trading cycle in the background
        asyncio.create_task(self._run_trading_cycle())
        return "✅ Trading cycle started successfully"

    async def _get_funded_wallets(self):
        """Query on-chain balances and return wallets meeting the min native balance.
        - Sources base wallet list from wallet_manager.wallets
        - Attaches private keys from wallet_manager.wallet_private_keys
        - Logs funded vs unfunded with thresholds
        - Returns: List[{'address', 'private_key'(optional), 'balance'}] where balance is in native units (float)
        """
        try:
            manager = getattr(self, 'wallet_manager', None)
            if manager is None:
                logging.warning("No wallet_manager available; cannot fetch wallets")
                return []

            base_wallets = list(getattr(manager, 'wallets', []) or [])
            # If no base wallets are loaded, try to refresh via manager if available
            if not base_wallets and hasattr(manager, '_refresh_funded_from_chain'):
                try:
                    await manager._refresh_funded_from_chain(self.chain or 'BNB')
                except Exception as e:
                    logging.warning(f"Refresh funded wallets failed: {e}")
                base_wallets = list(getattr(manager, 'wallets', []) or [])

            key_map = getattr(manager, 'wallet_private_keys', {}) if hasattr(manager, 'wallet_private_keys') else {}

            funded: list[dict] = []
            # Determine min required in native; include USD mode if configured
            price_usd = None
            if getattr(self, 'min_wallet_balance_usd', 0) and self.min_wallet_balance_usd > 0:
                price_usd = self._fetch_native_price_usd()
            if price_usd and price_usd > 0:
                min_required = max(float(self.min_wallet_balance), float(self.min_wallet_balance_usd) / float(price_usd))
            else:
                min_required = float(getattr(self, 'min_wallet_balance', 0.00085) or 0.00085)
            # Iterate, query balances with pacing
            for idx, w in enumerate(base_wallets):
                try:
                    if not isinstance(w, dict):
                        continue
                    addr = w.get('address') or w.get('addr')
                    if not addr:
                        logging.warning(f"Skipping wallet without address at index {idx}")
                        continue
                    # checksum and query balance
                    checksum = self.web3.to_checksum_address(addr) if hasattr(self.web3, 'to_checksum_address') else addr
                    wei = self.web3.eth.get_balance(checksum)
                    bal = float(self.web3.from_wei(wei, 'ether'))
                    # Build threshold text for logs
                    thresh_txt = f"{min_required:.6f} {self.chain}"
                    if price_usd and price_usd > 0 and self.min_wallet_balance_usd > 0:
                        thresh_txt += f" (~${self.min_wallet_balance_usd:.2f})"
                    if bal >= min_required:
                        logging.info(f"✅ Wallet {addr[:6]}...{addr[-6:]} has {bal:.6f} {self.chain} (≥ {thresh_txt} required)")
                        entry = {'address': addr, 'balance': bal}
                        pk = key_map.get(addr)
                        if pk:
                            entry['private_key'] = pk
                        funded.append(entry)
                    else:
                        logging.info(f"⚠️ Wallet {addr[:6]}...{addr[-6:]} has {bal:.6f} {self.chain} (< {thresh_txt} required)")
                    # small delay to avoid rate limits on public RPCs
                    await asyncio.sleep(0.05)
                except Exception as e:
                    logging.warning(f"Balance check failed for wallet index {idx}: {e}")
                    await asyncio.sleep(0.1)

            return funded
        except Exception as e:
            logging.error(f"Error in _get_funded_wallets: {e}", exc_info=True)
            return []

    async def stop(self):
        """Stop the trading cycle"""
        self.is_running = False
        return "🛑 Trading cycle stopped"

    async def _run_trading_cycle(self):
        """Main trading cycle loop following deterministic stagger rules."""
        while self.is_running:
            # Check if we crossed UTC midnight and reset daily counters
            self._ensure_daily_reset()
            self.current_cycle += 1
            logging.info(f"Starting trading cycle {self.current_cycle}")
            # Occasionally start with a quiet pause to avoid predictable cadence bursts
            try:
                if random.random() < max(0.0, min(1.0, self.quiet_cycle_prob)):
                    pause = random.randint(max(0, self.quiet_cycle_pause_min), max(0, self.quiet_cycle_pause_max))
                    if pause > 0:
                        logging.info(f"Quiet cycle pause for {pause}s before buys")
                        await asyncio.sleep(pause)
            except Exception as e:
                logging.debug(f"Quiet pause skipped: {e}")
            # Occasionally adjust trade sizes/fractions every N cycles (2-3 by default)
            try:
                self._maybe_adjust_trade_sizes()
            except Exception as e:
                logging.warning(f"Adjust trade sizes error: {e}")

            # Shuffle group order each cycle to avoid fixed patterns
            try:
                random.shuffle(self.group_a)
                random.shuffle(self.group_b)
            except Exception:
                pass
            # 1) Group A buys (staggered with jitter)
            await self._process_group_trades_staggered(self.group_a, 'buy')

            # 2) Group B buys after Group A completes (same stagger)
            await self._process_group_trades_staggered(self.group_b, 'buy')

            # 3) Wait before sells begin
            await asyncio.sleep(max(0, self.wait_before_sell))

            # 4) Randomized sells for Group A then Group B (sell tokens back to native)
            await self._process_group_trades_randomized(self.group_a, 'sell', self.sell_delay_min, self.sell_delay_max)

            # 5) Randomized sells for Group B
            await self._process_group_trades_randomized(self.group_b, 'sell', self.sell_delay_min, self.sell_delay_max)

            # 6) After all wallets sell, wait buyback delay before next cycle's buys
            await asyncio.sleep(max(0, self.buyback_delay))

    async def _process_group_trades_staggered(self, group: List[Dict], action: str):
        """Process buy/sell for a group with deterministic stagger and trade limits."""
        for idx, wallet in enumerate(group):
            if not self.is_running:
                break
            addr = wallet.get('address')
            if not addr:
                continue
            # Respect per-day max trades per wallet
            if self.trade_counts_today.get(addr, 0) >= self.max_trades_per_wallet:
                logging.info(f"Skip {addr}: reached daily max trades {self.max_trades_per_wallet}")
                continue
            # Per-wallet skip probability each cycle to desynchronize further
            try:
                if random.random() < max(0.0, min(1.0, self.skip_prob_per_cycle)):
                    logging.info(f"Skipping {addr} this cycle (skip_prob)")
                    continue
            except Exception:
                pass

            # Stagger delay with jitter: wallet0~[0-3]s, wallet1~10±3s, then +5s steps each with ±3s jitter
            base_delay = 0 if idx == 0 else 10 + (idx - 1) * 5
            jitter = random.randint(-3, 3)
            delay = max(0, base_delay + jitter)
            if delay > 0:
                await asyncio.sleep(delay)

            # Enforce per-wallet cooldown to avoid back-to-back actions
            try:
                now = int(time.time())
                last = int(self._last_action_ts.get(addr, 0))
                remain = self.min_wallet_cooldown_sec - (now - last)
                if remain > 0:
                    await asyncio.sleep(remain)
            except Exception:
                pass

            # Randomize size using lognormal factor around 1.0 (more human-like than uniform)
            base_amount = random.uniform(float(self.min_buy_amount), float(self.max_buy_amount))
            amount = int(base_amount * self._lognormal_factor())

            try:
                if action == 'buy':
                    receipt = await self._buy_tokens(wallet, amount)
                else:
                    receipt = await self._sell_tokens(wallet)
                # Increment counters only on success
                if self._tx_success(receipt):
                    self._increment_trade(addr)
                    self._last_action_ts[addr] = int(time.time())
            except Exception as e:
                logging.error(f"Error processing {action} for {addr}: {e}")

    async def _process_group_trades_randomized(self, group: List[Dict], action: str, min_delay: int, max_delay: int):
        """Process actions for a group with per-wallet randomized delays within [min_delay, max_delay]."""
        min_d = max(0, int(min_delay))
        max_d = max(min_d, int(max_delay))
        for wallet in group:
            if not self.is_running:
                break
            addr = wallet.get('address')
            if not addr:
                continue
            if self.trade_counts_today.get(addr, 0) >= self.max_trades_per_wallet:
                logging.info(f"Skip {addr}: reached daily max trades {self.max_trades_per_wallet}")
                continue
            # Per-wallet skip probability each cycle
            try:
                if random.random() < max(0.0, min(1.0, self.skip_prob_per_cycle)):
                    logging.info(f"Skipping {addr} this cycle (skip_prob)")
                    continue
            except Exception:
                pass
            delay = random.randint(min_d, max_d) if max_d > 0 else 0
            if delay > 0:
                await asyncio.sleep(delay)
            # Enforce per-wallet cooldown before executing
            try:
                now = int(time.time())
                last = int(self._last_action_ts.get(addr, 0))
                remain = self.min_wallet_cooldown_sec - (now - last)
                if remain > 0:
                    await asyncio.sleep(remain)
            except Exception:
                pass
            # For sells, no need to pick a native amount; for buys, reuse randomized amount range
            base_amount = random.uniform(float(self.min_buy_amount), float(self.max_buy_amount))
            amount = int(base_amount * self._lognormal_factor())
            try:
                if action == 'buy':
                    receipt = await self._buy_tokens(wallet, amount)
                else:
                    receipt = await self._sell_tokens(wallet)
                if self._tx_success(receipt):
                    self._increment_trade(addr)
                    self._last_action_ts[addr] = int(time.time())
            except Exception as e:
                logging.error(f"Error processing {action} for {addr}: {e}")

    # --- Utility: daily reset and trade counting ---
    def _current_utc_date_str(self) -> str:
        return datetime.now(timezone.utc).strftime('%Y-%m-%d')

    def _ensure_daily_reset(self, force_init: bool = False):
        today = self._current_utc_date_str()
        if force_init or self.last_reset_date is None:
            self.last_reset_date = today
            # initialize map if empty
            if not self.trade_counts_today:
                for w in (getattr(self, 'group_a', []) + getattr(self, 'group_b', [])):
                    addr = w.get('address')
                    if addr:
                        self.trade_counts_today.setdefault(addr, 0)
            return
        if today != self.last_reset_date:
            logging.info(f"UTC midnight reached. Resetting daily trade counters (prev {self.last_reset_date} -> {today})")
            # Reset all daily counters
            for k in list(self.trade_counts_today.keys()):
                self.trade_counts_today[k] = 0
            self.last_reset_date = today
            self._save_daily_counters()

    def _increment_trade(self, addr: str):
        self.trade_counts[addr] = self.trade_counts.get(addr, 0) + 1
        self.trade_counts_today[addr] = self.trade_counts_today.get(addr, 0) + 1
        try:
            if self.trade_counts[addr] % 5 == 0:
                self._save_daily_counters()
        except Exception:
            pass

    @staticmethod
    def _tx_success(receipt) -> bool:
        try:
            if receipt is None:
                return False
            status = receipt.get('status') if isinstance(receipt, dict) else getattr(receipt, 'status', 1)
            return status == 1
        except Exception:
            return True

    # --- Persistence helpers ---
    def _load_daily_counters(self):
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r') as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    day = data.get('date')
                    if day == self._current_utc_date_str():
                        self.trade_counts_today.update(data.get('counts_today', {}))
                        self.last_reset_date = day
                        logging.info("Loaded daily counters from disk")
        except Exception as e:
            logging.warning(f"Failed to load daily counters: {e}")

    def _save_daily_counters(self):
        try:
            os.makedirs(self.state_dir, exist_ok=True)
            with open(self.state_file, 'w') as f:
                json.dump({'date': self._current_utc_date_str(), 'counts_today': self.trade_counts_today}, f)
        except Exception as e:
            logging.warning(f"Failed to save daily counters: {e}")

    # --- Routing and quoting helpers ---
    def _candidate_paths(self, token: str):
        token = self.web3.to_checksum_address(token)
        # Try direct path first, then through stables if needed
        return [
            [self.wbnb_address, token],  # Direct path WBNB -> TOKEN
            [self.wbnb_address, self.busd_address, token],  # WBNB -> BUSD -> TOKEN
            [self.wbnb_address, self.usdt_address, token],  # WBNB -> USDT -> TOKEN
            [self.wbnb_address, self.usdt_address, token],
            [self.wbnb_address, self.busd_address, token],
        ]

    def _reverse_candidate_paths(self, token: str):
        token = self.web3.to_checksum_address(token)
        return [
            [token, self.wbnb_address],
            [token, self.usdt_address, self.wbnb_address],
            [token, self.busd_address, self.wbnb_address],
        ]

    def _best_out_quote(self, amount_in_wei: int, path_list: List[List[str]]):
        best = None
        best_path = None
        for path in path_list:
            try:
                quoted_out = self.router_contract.functions.getAmountsOut(
                    amount_in_wei,
                    path
                ).call()[-1]  # last element is the output amount
                
                logging.info(f"Quote for path {[self.web3.to_checksum_address(addr) for addr in path]} "
                           f"with {amount_in_wei} wei: {quoted_out} tokens")
                
                if quoted_out > 0 and (best is None or quoted_out > best):
                    best = quoted_out
                    best_path = path
                    logging.info(f"New best quote: {best} tokens via path {[self.web3.to_checksum_address(addr) for addr in best_path]}")
                    
            except Exception as e:
                logging.warning(f"Quote failed for path {[self.web3.to_checksum_address(addr) for addr in path]}: {str(e)}")
                continue
        return best, best_path

    def _min_out_with_slippage(self, quoted_out: int) -> int:
        # Use decimal slippage (e.g., 0.01 for 1%)
        min_out = int(quoted_out * (1 - self.slippage))
        return max(min_out, 1) if quoted_out > 0 else 0

    async def _buy_tokens(self, wallet: Dict, amount_wei: int) -> Optional[TxReceipt]:
        """Execute a buy with path discovery, safe minOut, gas estimate, and retries."""
        if not self.token_contract or not self.router_contract:
            logging.error("Token or router contract not initialized")
            return None

        # Log token info for debugging
        try:
            symbol = self.token_contract.functions.symbol().call()
            logging.info(f"Attempting to buy {symbol} (0x{self.token_address[-4:]}) for wallet {wallet['address'][:6]}...{wallet['address'][-4:]}")
        except:
            logging.warning(f"Could not get token symbol for {self.token_address}")

        acct = self.web3.eth.account.from_key(wallet['private_key'])
        from_addr = self.web3.to_checksum_address(acct.address)

        # Verify token contract is valid and has liquidity
        try:
            token_balance = self.token_contract.functions.balanceOf(from_addr).call()
            token_decimals = self.token_contract.functions.decimals().call()
            token_symbol = self.token_contract.functions.symbol().call()
            logging.info(f"Token contract verified. Symbol: {token_symbol}, Decimals: {token_decimals}, Balance: {token_balance}")

            # Check if token has liquidity by trying to get a quote
            paths = self._candidate_paths(self.token_address)
            path_descriptions = []
            for path in paths:
                path_desc = []
                for addr in path:
                    try:
                        if addr.lower() == self.wbnb_address.lower():
                            path_desc.append('WBNB')
                        elif addr.lower() == self.busd_address.lower():
                            path_desc.append('BUSD')
                        elif addr.lower() == self.usdt_address.lower():
                            path_desc.append('USDT')
                        else:
                            path_desc.append(addr[:6] + '...' + addr[-4:])
                    except:
                        path_desc.append(addr[:6] + '...' + addr[-4:])
                path_descriptions.append(' → '.join(path_desc))

            logging.info(f"Checking trading paths:\n" + '\n'.join([f"  {i+1}. {path}" for i, path in enumerate(path_descriptions)]))

            # Check if any path returns a valid quote with minimal amount (1 wei)
            test_quote, best_path = self._best_out_quote(1, paths)
            if not test_quote or test_quote == 0:
                logging.error("❌ No valid trading path found. Possible reasons:")
                logging.error("  1. Token has no liquidity on PancakeSwap V2")
                logging.error("  2. Token is not paired with WBNB or supported stables")
                logging.error("  3. Token contract has trading restrictions")
                logging.error(f"  4. Token address might be incorrect: {self.token_address}")
                return None

            logging.info(f"✅ Found valid trading path with quote: {test_quote} tokens per wei")

        except Exception as e:
            logging.error(f"Token contract verification failed: {e}")
            return None
        token = self.web3.to_checksum_address(self.token_address)
        # Balance check with wallet balance percentage limit
        balance = self.web3.eth.get_balance(from_addr)
        gas_buf = Web3.to_wei(self.gas_buffer, 'ether')

        # Calculate available balance after reserving gas buffer and minimum native balance
        required_floor = gas_buf + self.min_native_balance_to_keep
        available = balance - required_floor

        # Apply wallet balance percentage limit
        max_available = int(balance * self.max_wallet_balance_pct)
        available = min(available, max_available)

        if available <= 0:
            logging.warning(
                f"Insufficient {self.chain} in {from_addr} — bal={self.web3.from_wei(balance,'ether'):.6f}, "
                f"required_floor={self.web3.from_wei(required_floor,'ether'):.6f} (gas_buf+reserve), "
                f"max_{int(self.max_wallet_balance_pct*100)}%={self.web3.from_wei(max_available,'ether'):.6f}"
            )
            return None

        # If requested amount exceeds available, scale it down to <= available (with a small safety margin)
        if amount_wei > available:
            scaled = int(available * 0.95)  # leave ~5% headroom for gas price fluctuations
            if scaled < self.base_min_buy_wei:
                logging.warning(
                    f"Insufficient {self.chain} for min buy in {from_addr} — bal={self.web3.from_wei(balance,'ether'):.6f}, "
                    f"min_buy={self.web3.from_wei(self.base_min_buy_wei,'ether'):.6f}, "
                    f"available={self.web3.from_wei(available,'ether'):.6f} (max {int(self.max_wallet_balance_pct*100)}% of balance)"
                )
                return None
            logging.info(
                f"Auto-scaling buy for {from_addr}: requested={self.web3.from_wei(amount_wei,'ether'):.6f} -> "
                f"scaled={self.web3.from_wei(scaled,'ether'):.6f} (available={self.web3.from_wei(available,'ether'):.6f}, "
                f"max {int(self.max_wallet_balance_pct*100)}% of {self.web3.from_wei(balance,'ether'):.6f})"
            )
            amount_wei = scaled
        # Build swap parameters
        deadline = int(time.time()) + int(self.deadline_sec)
        # Quote best path
        quoted_out, path = self._best_out_quote(amount_wei, self._candidate_paths(token))
        if not path or not quoted_out:
            logging.warning("No valid buy route/quote; skipping trade")
            return None
        base_slippage = self.slippage
        base_gas_price = int(self.web3.eth.gas_price)
        fast_multiplier = float(os.getenv('GAS_FAST_MULTIPLIER', '1.2'))  # Fast mode
        attempt = 0
        while attempt <= self.retry_max:
            try:
                # Dynamically adjust gas price and slippage on each retry
                # Add small jitter per attempt for organic variation
                jitter = random.uniform(1 - self.gas_fast_jitter, 1 + self.gas_fast_jitter)
                gas_price = int(base_gas_price * fast_multiplier * jitter * (1 + 0.2 * attempt))
                # Grow slippage with attempts, plus jitter, but cap at configured max
                slip_j = 1 + random.uniform(-self.slippage_jitter, self.slippage_jitter)
                slippage = min(base_slippage * slip_j * (1 + 0.5 * attempt), self.max_slippage)
                # Always fetch latest nonce
                nonce = self.web3.eth.get_transaction_count(from_addr, 'pending')
                # Fetch a fresh quote and path for this attempt
                quoted_out, path = self._best_out_quote(amount_wei, self._candidate_paths(token))
                if not path or not quoted_out:
                    logging.warning("No valid buy route/quote; skipping trade")
                    return None
                # Apply extra allowance for fee-on-transfer (tax) tokens
                fot_allow = max(0.0, float(self.fot_allowance_bps) / 10000.0)
                effective_cut = min(0.99, slippage + fot_allow)  # don't exceed 99%
                min_out = int(quoted_out * (1 - effective_cut))
                # Last resort: allow minOut=1 on final attempt if enabled
                if self.allow_last_resort_minout1 and attempt == self.retry_max:
                    min_out = max(1, min_out)
                logging.info(
                    f"[Auto] Buy attempt {attempt}: gas_price={gas_price}, slippage={slippage:.4f}, fot_allow={fot_allow:.4f}, "
                    f"quoted_out={quoted_out}, min_out={min_out}, nonce={nonce}, path={path}"
                )
                func = self.router_contract.functions.swapExactETHForTokensSupportingFeeOnTransferTokens(
                    int(min_out), path, from_addr, int(deadline)
                )
                tx_dict = {
                    'from': from_addr,
                    'value': int(amount_wei),
                    'gasPrice': gas_price,
                    'nonce': nonce,
                    'chainId': self.chain_id,
                }
                try:
                    gas_est = func.estimate_gas(tx_dict)
                    tx_dict['gas'] = min(int(gas_est * 1.3), 500000)
                except Exception:
                    tx_dict['gas'] = 300000
                tx = func.build_transaction(tx_dict)
                signed = acct.sign_transaction(tx)
                tx_hash = self.web3.eth.send_raw_transaction(signed.raw_transaction)
                logging.info(f"Buy submitted {from_addr} -> {tx_hash.hex()} path={path} minOut={min_out}")
                receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash)
                return receipt
            except Exception as e:
                attempt += 1
                logging.warning(f"Buy attempt {attempt} failed: {e}")
                if attempt > self.retry_max:
                    logging.error("Buy failed after retries")
                    break
                await asyncio.sleep(self.retry_backoff ** attempt)
        return None

    # --- Organic sizing helper ---
    def _lognormal_factor(self) -> float:
        """Return a multiplicative factor sampled from a lognormal distribution with mean≈1.
        For lognormal, mean = exp(mu + 0.5*sigma^2). Choose mu so mean≈1.
        """
        try:
            sigma = max(0.0, float(self.size_logn_sigma))
        except Exception:
            sigma = 0.25
        mu = -0.5 * (sigma ** 2)
        # Cap extreme tails for safety
        factor = math.exp(random.gauss(mu, sigma))
        return float(max(0.5, min(2.0, factor)))
    # --- Dynamic sizing helpers ---
    def _clamp(self, val: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, val))

    def _maybe_adjust_trade_sizes(self):
        """Every few cycles, randomly increase or decrease buy size scale and sell fraction band."""
        self._cycles_until_adjust -= 1
        if self._cycles_until_adjust > 0:
            return
        # Reset next interval first
        self._cycles_until_adjust = random.randint(self.adjust_interval_min_cycles, self.adjust_interval_max_cycles)
        # Random up/down direction and step
        direction = -1 if random.random() < 0.5 else 1
        step = random.uniform(self.buy_scale_step_min, self.buy_scale_step_max)
        new_scale = self._clamp(self.buy_amount_scale * (1 + direction * step), self.buy_scale_min, self.buy_scale_max)
        self.buy_amount_scale = new_scale
        # Apply to current buy amounts
        self.min_buy_amount = int(self.base_min_buy_wei * self.buy_amount_scale)
        self.max_buy_amount = int(self.base_max_buy_wei * self.buy_amount_scale)
        # Adjust sell fraction band similarly (gentler)
        sf_step = step * 0.5
        if direction < 0:
            self.sell_frac_low = self._clamp(self.sell_frac_low * (1 - sf_step), 0.05, 0.9)
            self.sell_frac_high = self._clamp(self.sell_frac_high * (1 - sf_step), 0.1, 0.98)
        else:
            self.sell_frac_low = self._clamp(self.sell_frac_low * (1 + sf_step), 0.05, 0.9)
            self.sell_frac_high = self._clamp(self.sell_frac_high * (1 + sf_step), 0.1, 0.98)
        # Ensure ordering
        if self.sell_frac_high <= self.sell_frac_low:
            self.sell_frac_high = min(0.98, self.sell_frac_low + 0.05)
        logging.info(
            f"Adjusted sizes: buy_scale={self.buy_amount_scale:.2f}, min_buy={self.min_buy_amount} wei, max_buy={self.max_buy_amount} wei, "
            f"sell_frac=[{self.sell_frac_low:.2f},{self.sell_frac_high:.2f}]"
        )

    async def _sell_tokens(self, wallet: Dict) -> Optional[TxReceipt]:
        """Execute a sell with reverse path discovery, safe minOut, gas estimate, and retries."""
        acct = self.web3.eth.account.from_key(wallet['private_key'])
        from_addr = self.web3.to_checksum_address(acct.address)
        token = self.web3.to_checksum_address(self.token_address)
        token_c = self.web3.eth.contract(address=token, abi=self.token_abi)
        # Balance check (token amount)
        token_balance = token_c.functions.balanceOf(from_addr).call()
        if token_balance <= 0:
            logging.warning(f"No token balance to sell in {from_addr}")
            return None
        # Ensure enough native gas balance to execute approval/sell
        native_bal = self.web3.eth.get_balance(from_addr)
        gas_buf = Web3.to_wei(self.gas_buffer, 'ether')
        if native_bal < gas_buf:
            logging.warning(
                f"Insufficient {self.chain} gas for sell in {from_addr} — bal={self.web3.from_wei(native_bal,'ether'):.6f}, "
                f"required_gas_buf={self.web3.from_wei(gas_buf,'ether'):.6f}"
            )
            return None
        # Sell a randomized fraction to avoid uniform behavior, dynamic range
        low = max(0.05, min(self.sell_frac_low, 0.95))
        high = max(low + 0.01, min(self.sell_frac_high, 0.98))
        sell_fraction = random.uniform(low, high)
        amount_in = max(int(token_balance * sell_fraction), 1)
        # Approve if needed
        allowance = token_c.functions.allowance(from_addr, self.router_address).call()
        if allowance < amount_in:
            try:
                approve_amount = Web3.to_wei(2**64 - 1, 'wei') if self.approve_infinite else int(amount_in)
            except Exception:
                approve_amount = int(amount_in)
            gas_price = int(self.web3.eth.gas_price * random.uniform(0.98, 1.05))
            nonce = self.web3.eth.get_transaction_count(from_addr)
            approve_tx = token_c.functions.approve(self.router_address, int(approve_amount)).build_transaction({
                'from': from_addr,
                'gas': 100000,
                'gasPrice': gas_price,
                'nonce': nonce,
                'chainId': self.chain_id,
            })
            signed_approve = acct.sign_transaction(approve_tx)
            approve_hash = self.web3.eth.send_raw_transaction(signed_approve.raw_transaction)
            logging.info(f"Approve submitted {from_addr} -> tx {approve_hash.hex()}")
            self.web3.eth.wait_for_transaction_receipt(approve_hash)
        deadline = int(time.time()) + 120
        # Quote best reverse path with simple auto-scale on failure
        attempts = 0
        quoted_out, path = self._best_out_quote(int(amount_in), self._reverse_candidate_paths(token))
        while (not path or not quoted_out) and attempts < 3 and amount_in > 1:
            attempts += 1
            amount_in = max(int(amount_in * 0.5), 1)
            quoted_out, path = self._best_out_quote(int(amount_in), self._reverse_candidate_paths(token))
        if not path or not quoted_out:
            logging.warning("No valid sell route/quote after scaling; skipping trade")
            return None
        base_slippage = self.slippage
        base_gas_price = int(self.web3.eth.gas_price)
        fast_multiplier = float(os.getenv('GAS_FAST_MULTIPLIER', '1.2'))  # Fast mode
        attempt = 0
        while attempt <= self.retry_max:
            try:
                j = random.uniform(1 - self.gas_fast_jitter, 1 + self.gas_fast_jitter)
                gas_price = int(base_gas_price * fast_multiplier * j * (1 + 0.2 * attempt))
                slip_j = 1 + random.uniform(-self.slippage_jitter, self.slippage_jitter)
                slippage = min(base_slippage * slip_j * (1 + 0.5 * attempt), self.max_slippage)
                nonce = self.web3.eth.get_transaction_count(from_addr, 'pending')
                # Fetch a fresh quote and path for this attempt
                quoted_out, path = self._best_out_quote(int(amount_in), self._reverse_candidate_paths(token))
                if not path or not quoted_out:
                    logging.warning("No valid sell route/quote; skipping trade")
                    return None
                min_out = int(quoted_out * (1 - slippage))
                logging.info(f"[Auto] Sell attempt {attempt}: gas_price={gas_price}, slippage={slippage}, quoted_out={quoted_out}, min_out={min_out}, nonce={nonce}")
                func = self.router_contract.functions.swapExactTokensForETHSupportingFeeOnTransferTokens(
                    int(amount_in), int(min_out), path, from_addr, int(deadline)
                )
                tx_dict = {
                    'from': from_addr,
                    'gasPrice': gas_price,
                    'nonce': nonce,
                    'chainId': self.chain_id,
                }
                try:
                    gas_est = func.estimate_gas({'from': from_addr})
                    tx_dict['gas'] = min(int(gas_est * 1.3), 600000)
                except Exception:
                    tx_dict['gas'] = 400000
                tx = func.build_transaction(tx_dict)
                signed = acct.sign_transaction(tx)
                tx_hash = self.web3.eth.send_raw_transaction(signed.raw_transaction)
                logging.info(f"Sell submitted {from_addr} -> {tx_hash.hex()} path={path} minOut={min_out}")
                receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash)
                return receipt
            except Exception as e:
                attempt += 1
                logging.warning(f"Sell attempt {attempt} failed: {e}")
                if attempt > self.retry_max:
                    logging.error("Sell failed after retries")
                    break
                await asyncio.sleep(self.retry_backoff ** attempt)
        return None

    async def _transfer_half_tokens(self, from_wallet: Dict, to_address: str) -> Optional[TxReceipt]:
        """Transfer half of tokens from one wallet to another"""
        try:
            acct = self.web3.eth.account.from_key(from_wallet['private_key'])
            from_addr = self.web3.to_checksum_address(acct.address)
            token = self.web3.to_checksum_address(self.token_address)
            token_c = self.web3.eth.contract(address=token, abi=self.token_abi)

            token_balance = token_c.functions.balanceOf(from_addr).call()
            if token_balance <= 0:
                return None

            transfer_amount = token_balance // 2
            if transfer_amount == 0:
                return None

            gas_price = int(self.web3.eth.gas_price * random.uniform(0.98, 1.05))
            nonce = self.web3.eth.get_transaction_count(from_addr)
            tx = token_c.functions.transfer(self.web3.to_checksum_address(to_address), int(transfer_amount)).build_transaction({
                'from': from_addr,
                'gas': 120000,
                'gasPrice': gas_price,
                'nonce': nonce,
                'chainId': 56,
            })
            signed = acct.sign_transaction(tx)
            tx_hash = self.web3.eth.send_raw_transaction(signed.rawTransaction)
            logging.info(f"Transfer half submitted {from_addr} -> tx {tx_hash.hex()}")
            receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash)
            return receipt
        except Exception as e:
            logging.error(f"Error in _transfer_half_tokens: {str(e)}", exc_info=True)
            return None
