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
    def __init__(self, wallet_manager, web3: Web3, token_address: str):
        self.wallet_manager = wallet_manager
        self.web3 = web3
        self.token_address = token_address
        self.is_running = False
        self.current_cycle = 0
        self.wallets = []
        self.token_contract = None
        self.router_contract = None
        self.min_buy_amount = Web3.to_wei(0.01, 'ether')  # 0.01 BNB
        self.max_buy_amount = Web3.to_wei(0.05, 'ether')  # 0.05 BNB
        self.gas_buffer = 0.0005  # Buffer for gas in BNB
        self.max_trades_per_wallet = 15  # buy + sell combined (per day)
        self.trade_counts = {}  # legacy total counter (kept for compatibility)
        self.trade_counts_today = {}  # address -> int (per UTC day)
        self.last_reset_date = None  # 'YYYY-MM-DD' string
        # Wait before starting sells (in seconds), default 15 minutes
        self.wait_before_sell = int(os.getenv('TRADE_WAIT_BEFORE_SELL', '900'))
        # Trading safety/config
        self.slippage_bps = int(os.getenv('SLIPPAGE_BPS', '100'))  # 100 = 1%
        self.retry_max = int(os.getenv('TRADE_RETRY_MAX', '2'))
        self.retry_backoff = float(os.getenv('TRADE_RETRY_BACKOFF', '1.5'))
        self.approve_infinite = os.getenv('APPROVE_INFINITE', 'false').lower() == 'true'
        # Persist daily counters
        self.state_dir = os.path.join(os.path.dirname(__file__), 'data')
        self.state_file = os.path.join(self.state_dir, 'trade_state.json')
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
        
        # Constants
        self.router_address = self.web3.to_checksum_address('0x10ED43C718714eb63d5aA57B78B54704E256024E')
        self.wbnb_address = self.web3.to_checksum_address('0xBB4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c')
        # Common stable routes on BSC
        self.busd_address = self.web3.to_checksum_address('0xe9e7cea3dedca5984780bafc599bd69add087d56')  # BUSD (legacy)
        self.usdt_address = self.web3.to_checksum_address('0x55d398326f99059fF775485246999027B3197955')  # USDT
        self.router_contract = self.web3.eth.contract(
            address=self.router_address,
            abi=self.router_abi
        )

    async def start(self):
        """Start the trading cycle"""
        if self.is_running:
            return "Trading cycle is already running"
        
        self.is_running = True
        self.current_cycle = 0
        # Initialize/reset daily counters if needed
        self._ensure_daily_reset(force_init=True)
        # Load counters from disk if available
        self._load_daily_counters()
        
        # Split wallets into Group A (1-17) and Group B (18-34)
        self.group_a = self.wallets[:17]
        self.group_b = self.wallets[17:34]
        # Initialize trade counts for known wallets
        for w in (self.group_a + self.group_b):
            addr = w.get('address')
            if addr and addr not in self.trade_counts:
                self.trade_counts[addr] = 0
            if addr and addr not in self.trade_counts_today:
                self.trade_counts_today[addr] = 0
        
        # Start the trading cycle in the background
        asyncio.create_task(self._run_trading_cycle())
        return "✅ Trading cycle started successfully"

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

            # 1) Group A buys (staggered: 0s, 10s, 15s, 20s, ...)
            await self._process_group_trades_staggered(self.group_a, 'buy')

            # 2) Group B buys after Group A completes (same stagger)
            await self._process_group_trades_staggered(self.group_b, 'buy')

            # 3) Wait before sells begin
            await asyncio.sleep(max(0, self.wait_before_sell))

            # 4) Group A sells half to Group B (staggered)
            await self._process_group_sell_half(self.group_a, self.group_b)

            # 5) Group B sells half back to Group A (staggered)
            await self._process_group_sell_half(self.group_b, self.group_a)

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
                    receipt = await self._sell_tokens(wallet, amount)
                # Increment counters only on success
                if self._tx_success(receipt):
                    self._increment_trade(addr)
            except Exception as e:
                logging.error(f"Error processing {action} for {addr}: {e}")

    async def _process_group_sell_half(self, group_from: List[Dict], group_to: List[Dict]):
        """Process selling half holdings from group_from to mapped wallets in group_to with stagger."""
        for i, wallet_from in enumerate(group_from):
            if i >= len(group_to) or not self.is_running:
                break
            addr_from = wallet_from.get('address')
            if not addr_from:
                continue
            # Respect daily max trades
            if self.trade_counts_today.get(addr_from, 0) >= self.max_trades_per_wallet:
                logging.info(f"Skip {addr_from}: reached daily max trades {self.max_trades_per_wallet}")
                continue

            # Stagger delay same as buys
            delay = 0 if i == 0 else 10 + (i - 1) * 5
            if delay > 0:
                await asyncio.sleep(delay)

            wallet_to = group_to[i]
            receipt = await self._transfer_half_tokens(wallet_from, wallet_to['address'])
            # Count as a trade on success
            if self._tx_success(receipt):
                self._increment_trade(addr_from)

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
        if balance < amount_wei + Web3.to_wei(self.gas_buffer, 'ether'):
            logging.warning(f"Insufficient BNB in {from_addr}")
            return None
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
                    'chainId': 56,
                }
                # Estimate gas with cap
                try:
                    gas_est = func.estimate_gas(tx_dict)
                    tx_dict['gas'] = min(int(gas_est * 1.2), 500000)
                except Exception:
                    tx_dict['gas'] = 300000
                tx = func.build_transaction(tx_dict)
                signed = acct.sign_transaction(tx)
                tx_hash = self.web3.eth.send_raw_transaction(signed.rawTransaction)
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

    async def _sell_tokens(self, wallet: Dict, amount_wei: int) -> Optional[TxReceipt]:
        """Execute a sell with reverse path discovery, safe minOut, gas estimate, and retries."""
        acct = self.web3.eth.account.from_key(wallet['private_key'])
        from_addr = self.web3.to_checksum_address(acct.address)
        token = self.web3.to_checksum_address(self.token_address)
        token_c = self.web3.eth.contract(address=token, abi=self.token_abi)
        # Balance check (token amount)
        token_balance = token_c.functions.balanceOf(from_addr).call()
        if token_balance < amount_wei:
            logging.warning(f"Insufficient token balance in {from_addr}")
            return None
        # Approve if needed
        allowance = token_c.functions.allowance(from_addr, self.router_address).call()
        if allowance < amount_wei:
            try:
                approve_amount = Web3.to_wei(2**64 - 1, 'wei') if self.approve_infinite else int(amount_wei)
            except Exception:
                approve_amount = int(amount_wei)
            gas_price = int(self.web3.eth.gas_price * random.uniform(0.98, 1.05))
            nonce = self.web3.eth.get_transaction_count(from_addr)
            approve_tx = token_c.functions.approve(self.router_address, int(approve_amount)).build_transaction({
                'from': from_addr,
                'gas': 100000,
                'gasPrice': gas_price,
                'nonce': nonce,
                'chainId': 56,
            })
            signed_approve = acct.sign_transaction(approve_tx)
            approve_hash = self.web3.eth.send_raw_transaction(signed_approve.rawTransaction)
            logging.info(f"Approve submitted {from_addr} -> tx {approve_hash.hex()}")
            self.web3.eth.wait_for_transaction_receipt(approve_hash)
        deadline = int(time.time()) + 120
        # Quote best reverse path
        quoted_out, path = self._best_out_quote(int(amount_wei), self._reverse_candidate_paths(token))
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
                    int(amount_wei), int(amount_out_min), path, from_addr, int(deadline)
                )
                tx_dict = {
                    'from': from_addr,
                    'gasPrice': gas_price,
                    'nonce': nonce,
                    'chainId': 56,
                }
                # Estimate gas with cap
                try:
                    gas_est = func.estimate_gas({'from': from_addr})
                    tx_dict['gas'] = min(int(gas_est * 1.2), 600000)
                except Exception:
                    tx_dict['gas'] = 400000
                tx = func.build_transaction(tx_dict)
                signed = acct.sign_transaction(tx)
                tx_hash = self.web3.eth.send_raw_transaction(signed.rawTransaction)
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
