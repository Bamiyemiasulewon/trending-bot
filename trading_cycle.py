import asyncio
import logging
import random
import json
import time
import os
from datetime import datetime, timezone
from typing import Dict, List, Tuple, Optional
from decimal import Decimal
from web3 import Web3
from web3.types import TxReceipt

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
        
        # Minimum balance required for a wallet to be considered funded (in native token)
        self.min_wallet_balance = 0.00085  # slightly less than 0.001 BNB/ETH/SOL
        
        # Base buy ranges (native) and current scaled values (tuned for tiny balances)
        self.base_min_buy_wei = Web3.to_wei(0.0002, 'ether')  # 0.0002 native
        self.base_max_buy_wei = Web3.to_wei(0.0004, 'ether')  # 0.0004 native
        self.min_buy_amount = int(self.base_min_buy_wei)
        self.max_buy_amount = int(self.base_max_buy_wei)
        self.gas_buffer = 0.0001  # Smaller buffer for gas in native
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
        # Trading safety/config
        self.slippage_bps = int(os.getenv('SLIPPAGE_BPS', '100'))  # 100 = 1%
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

    async def start(self):
        """Start the trading cycle with wallet validation"""
        if self.is_running:
            return "Trading cycle is already running"
            
        # Check if we have enough funded wallets
        funded_wallets = await self._get_funded_wallets()
        if len(funded_wallets) < 2:  # Need at least 2 wallets (1 for each group)
            return f"❌ Not enough funded wallets. Need at least 2 wallets with ≥ {self.min_wallet_balance} {self.chain}"
            
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
                    if bal >= min_required:
                        logging.info(f"✅ Wallet {addr[:6]}...{addr[-6:]} has {bal:.6f} {self.chain} (≥ {min_required} required)")
                        entry = {'address': addr, 'balance': bal}
                        pk = key_map.get(addr)
                        if pk:
                            entry['private_key'] = pk
                        funded.append(entry)
                    else:
                        logging.info(f"⚠️ Wallet {addr[:6]}...{addr[-6:]} has {bal:.6f} {self.chain} (< {min_required} required)")
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
            # Occasionally adjust trade sizes/fractions every N cycles (2-3 by default)
            try:
                self._maybe_adjust_trade_sizes()
            except Exception as e:
                logging.warning(f"Adjust trade sizes error: {e}")

            # 1) Group A buys (staggered: 0s, 10s, 15s, 20s, ...)
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

            # Stagger delay: wallet0=0s, wallet1=10s, wallet2=15s, wallet3=20s, ...
            delay = 0 if idx == 0 else 10 + (idx - 1) * 5
            if delay > 0:
                await asyncio.sleep(delay)

            # Randomize amount within ±3%
            base_amount = random.uniform(float(self.min_buy_amount), float(self.max_buy_amount))
            amount = int(base_amount * random.uniform(0.97, 1.03))

            try:
                if action == 'buy':
                    receipt = await self._buy_tokens(wallet, amount)
                else:
                    receipt = await self._sell_tokens(wallet)
                # Increment counters only on success
                if self._tx_success(receipt):
                    self._increment_trade(addr)
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
            delay = random.randint(min_d, max_d) if max_d > 0 else 0
            if delay > 0:
                await asyncio.sleep(delay)
            # For sells, no need to pick a native amount; for buys, reuse randomized amount range
            base_amount = random.uniform(float(self.min_buy_amount), float(self.max_buy_amount))
            amount = int(base_amount * random.uniform(0.97, 1.03))
            try:
                if action == 'buy':
                    receipt = await self._buy_tokens(wallet, amount)
                else:
                    receipt = await self._sell_tokens(wallet)
                if self._tx_success(receipt):
                    self._increment_trade(addr)
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
        return [
            [self.wbnb_address, token],
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
                amounts = self.router_contract.functions.getAmountsOut(int(amount_in_wei), path).call()
                out_amt = int(amounts[-1])
                if out_amt > 0 and (best is None or out_amt > best):
                    best = out_amt
                    best_path = path
            except Exception:
                continue
        return best, best_path

    def _min_out_with_slippage(self, quoted_out: int) -> int:
        min_out = (quoted_out * (10_000 - self.slippage_bps)) // 10_000
        return max(min_out, 1) if quoted_out > 0 else 0

    async def _buy_tokens(self, wallet: Dict, amount_wei: int) -> Optional[TxReceipt]:
        """Execute a buy with path discovery, safe minOut, gas estimate, and retries."""
        acct = self.web3.eth.account.from_key(wallet['private_key'])
        from_addr = self.web3.to_checksum_address(acct.address)
        token = self.web3.to_checksum_address(self.token_address)
        # Balance check
        balance = self.web3.eth.get_balance(from_addr)
        gas_buf = Web3.to_wei(self.gas_buffer, 'ether')
        # Ensure we keep minimum native for future gas
        if balance < amount_wei + gas_buf + self.min_native_balance_to_keep:
            logging.warning(f"Insufficient BNB in {from_addr}")
            return None
        # Build swap parameters
        deadline = int(time.time()) + 120
        # Quote best path
        quoted_out, path = self._best_out_quote(amount_wei, self._candidate_paths(token))
        if not path or not quoted_out:
            logging.warning("No valid buy route/quote; skipping trade")
            return None
        amount_out_min = self._min_out_with_slippage(quoted_out)
        # Retry loop
        attempt = 0
        while attempt <= self.retry_max:
            try:
                gas_price = int(self.web3.eth.gas_price * random.uniform(0.98, 1.05))
                nonce = self.web3.eth.get_transaction_count(from_addr)
                func = self.router_contract.functions.swapExactETHForTokensSupportingFeeOnTransferTokens(
                    int(amount_out_min), path, from_addr, int(deadline)
                )
                tx_dict = {
                    'from': from_addr,
                    'value': int(amount_wei),
                    'gasPrice': gas_price,
                    'nonce': nonce,
                    'chainId': self.chain_id,
                }
                # Estimate gas with cap
                try:
                    gas_est = func.estimate_gas(tx_dict)
                    tx_dict['gas'] = min(int(gas_est * 1.2), 500000)
                except Exception:
                    tx_dict['gas'] = 300000
                tx = func.build_transaction(tx_dict)
                signed = acct.sign_transaction(tx)
                tx_hash = self.web3.eth.send_raw_transaction(signed.raw_transaction)
                logging.info(f"Buy submitted {from_addr} -> {tx_hash.hex()} path={path} minOut={amount_out_min}")
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
        # Quote best reverse path
        quoted_out, path = self._best_out_quote(int(amount_in), self._reverse_candidate_paths(token))
        if not path or not quoted_out:
            logging.warning("No valid sell route/quote; skipping trade")
            return None
        amount_out_min = self._min_out_with_slippage(quoted_out)
        # Retry loop
        attempt = 0
        while attempt <= self.retry_max:
            try:
                gas_price = int(self.web3.eth.gas_price * random.uniform(0.98, 1.05))
                nonce = self.web3.eth.get_transaction_count(from_addr)
                func = self.router_contract.functions.swapExactTokensForETHSupportingFeeOnTransferTokens(
                    int(amount_in), int(amount_out_min), path, from_addr, int(deadline)
                )
                tx_dict = {
                    'from': from_addr,
                    'gasPrice': gas_price,
                    'nonce': nonce,
                    'chainId': self.chain_id,
                }
                # Estimate gas with cap
                try:
                    gas_est = func.estimate_gas({'from': from_addr})
                    tx_dict['gas'] = min(int(gas_est * 1.2), 600000)
                except Exception:
                    tx_dict['gas'] = 400000
                tx = func.build_transaction(tx_dict)
                signed = acct.sign_transaction(tx)
                tx_hash = self.web3.eth.send_raw_transaction(signed.raw_transaction)
                logging.info(f"Sell submitted {from_addr} -> {tx_hash.hex()} path={path} minOut={amount_out_min}")
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
