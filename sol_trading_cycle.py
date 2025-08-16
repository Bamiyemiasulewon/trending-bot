import asyncio
import logging
import time
import base64
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import requests
from solana.rpc.api import Client as SolClient
from solana.rpc.types import TxOpts
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import VersionedTransaction
from solders.keypair import Keypair as SKeypair
import base58


@dataclass
class SolWallet:
    address: str
    private_key: str  # can be a mnemonic or base58 secret; the wallet_manager should normalize


class SolanaTradingCycle:
    """
    Solana trending/trading cycle using Jupiter v6 for swaps.
    Public API mirrors the EVM TradingCycle:
      - start() -> str
      - stop() -> str
      - is_running, current_cycle, wallets, token_address
    """

    def __init__(self, wallet_manager, client, token_address: str):
        self.wallet_manager = wallet_manager
        self.client = client  # solana.rpc.api.Client
        self.token_address = token_address
        self.is_running = False
        self.current_cycle = 0
        self.wallets: List[Dict] = []
        # Behavior knobs (seconds)
        self.wait_before_sell = int(getattr(wallet_manager, 'wait_before_sell', 900) or 900)
        # Daily counters similar to EVM version
        self.max_trades_per_wallet = 15
        self.trade_counts_today: Dict[str, int] = {}
        self.last_reset_date: Optional[str] = None
        # Slippage (decimal, e.g., 0.01 for 1%)
        import os
        try:
            self.slippage = float(os.getenv('SLIPPAGE', '0.01'))
        except Exception:
            self.slippage = 0.01

    async def start(self) -> str:
        if self.is_running:
            return "Trading cycle is already running"
        self.is_running = True
        self.current_cycle = 0
        # Init counters for known wallets
        for w in self.wallets:
            addr = w.get('address')
            if addr and addr not in self.trade_counts_today:
                self.trade_counts_today[addr] = 0
        asyncio.create_task(self._run_trading_cycle())
        return "✅ Solana trading cycle started successfully"

    async def stop(self) -> str:
        self.is_running = False
        return "🛑 Solana trading cycle stopped"

    async def _run_trading_cycle(self):
        while self.is_running:
            self.current_cycle += 1
            logging.info(f"[SOL] Starting trading cycle {self.current_cycle}")
            group_a = self.wallets[:17]
            group_b = self.wallets[17:34]
            await self._process_group_trades_staggered(group_a, 'buy')
            await self._process_group_trades_staggered(group_b, 'buy')
            await asyncio.sleep(max(0, self.wait_before_sell))
            # Sell phase for both groups (no token transfers required)
            await self._process_group_trades_staggered(group_a, 'sell')
            await self._process_group_trades_staggered(group_b, 'sell')

    async def _process_group_trades_staggered(self, group: List[Dict], action: str):
        for idx, wallet in enumerate(group):
            if not self.is_running:
                break
            addr = wallet.get('address')
            if not addr:
                continue
            if self.trade_counts_today.get(addr, 0) >= self.max_trades_per_wallet:
                logging.info(f"[SOL] Skip {addr}: reached daily max trades {self.max_trades_per_wallet}")
                continue
            delay = 0 if idx == 0 else 10 + (idx - 1) * 5
            if delay > 0:
                await asyncio.sleep(delay)
            try:
                if action == 'buy':
                    ok = await self._buy_tokens(wallet)
                else:
                    ok = await self._sell_tokens(wallet)
                if ok:
                    self.trade_counts_today[addr] = self.trade_counts_today.get(addr, 0) + 1
            except Exception as e:
                logging.error(f"[SOL] Error processing {action} for {addr}: {e}")

    # removed transfer-half logic; selling is executed directly in the sell phase

    # --- Trade ops via Jupiter ---
    async def _buy_tokens(self, wallet: Dict) -> bool:
        """Buy target token using SOL via Jupiter v6."""
        try:
            kp = self._load_keypair(wallet)
            user_pubkey = str(Pubkey.from_string(wallet['address']))
            # Use small SOL amount (e.g., 0.01 SOL) in lamports with safety margin
            amount_lamports = int(0.01 * 1_000_000_000)
            if not self._has_min_balance(wallet['address'], amount_lamports + 200_000):
                logging.warning(f"[SOL] Insufficient SOL for buy in {wallet['address']}")
                return False
            base_slippage = self.slippage
            max_retries = 3
            for attempt in range(max_retries + 1):
                slippage_adj = min(base_slippage * (1 + 0.5 * attempt), 0.20)
                slippage_bps = int(slippage_adj * 10000)
                quote = self._jup_quote(
                    input_mint="So11111111111111111111111111111111111111112",
                    output_mint=self.token_address,
                    amount=amount_lamports,
                    slippage_bps=slippage_bps,
                )
                logging.info(f"[SOL] Buy attempt {attempt}: slippage={slippage_adj} (bps={slippage_bps}), quote={quote}")
                if not quote:
                    logging.warning("[SOL] No quote from Jupiter")
                    if attempt == max_retries:
                        return False
                    await asyncio.sleep(2 ** attempt)
                    continue
                swap_tx_b64 = self._jup_swap_tx(quote, user_pubkey)
                if not swap_tx_b64:
                    if attempt == max_retries:
                        return False
                    await asyncio.sleep(2 ** attempt)
                    continue
                return self._sign_and_send_b64_tx(swap_tx_b64, kp)
            return False
        except Exception as e:
            logging.error(f"[SOL] buy failed: {e}")
            return False

    async def _sell_tokens(self, wallet: Dict) -> bool:
        """Sell target token back to SOL via Jupiter v6.
        """
        try:
            kp = self._load_keypair(wallet)
            user_pubkey = str(Pubkey.from_string(wallet['address']))
            # Determine actual SPL token balance and sell half
            balance_units, decimals = self._get_spl_balance(wallet['address'], self.token_address)
            if balance_units <= 0:
                logging.info(f"[SOL] No {self.token_address} balance for {wallet['address']}; skipping sell")
                return False
            amount_units = max(balance_units // 2, 1)
            # Ensure swap is meaningful (avoid dust)
            if amount_units < 10 ** max(decimals - 2, 0):
                logging.info(f"[SOL] Balance too small to sell for {wallet['address']}")
                return False
            base_slippage = self.slippage
            max_retries = 3
            for attempt in range(max_retries + 1):
                slippage_adj = min(base_slippage * (1 + 0.5 * attempt), 0.20)
                slippage_bps = int(slippage_adj * 10000)
                quote = self._jup_quote(
                    input_mint=self.token_address,
                    output_mint="So11111111111111111111111111111111111111112",
                    amount=amount_units,
                    slippage_bps=slippage_bps,
                )
                logging.info(f"[SOL] Sell attempt {attempt}: slippage={slippage_adj} (bps={slippage_bps}), quote={quote}")
                if not quote:
                    logging.warning("[SOL] No sell quote from Jupiter")
                    if attempt == max_retries:
                        return False
                    await asyncio.sleep(2 ** attempt)
                    continue
                swap_tx_b64 = self._jup_swap_tx(quote, user_pubkey)
                if not swap_tx_b64:
                    if attempt == max_retries:
                        return False
                    await asyncio.sleep(2 ** attempt)
                    continue
                return self._sign_and_send_b64_tx(swap_tx_b64, kp)
            return False
        except Exception as e:
            logging.error(f"[SOL] sell failed: {e}")
            return False


    # --- Jupiter helpers ---
    def _jup_quote(self, input_mint: str, output_mint: str, amount: int, slippage_bps: int) -> Optional[Dict]:
        try:
            url = (
                "https://quote-api.jup.ag/v6/quote"
                f"?inputMint={input_mint}&outputMint={output_mint}&amount={amount}&slippageBps={slippage_bps}"
            )
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            data = r.json()
            # v6 returns an object with routes; if empty, return None
            return data
        except Exception as e:
            logging.warning(f"[SOL] Jupiter quote error: {e}")
            return None

    def _get_spl_balance(self, owner_address: str, mint_address: str) -> Tuple[int, int]:
        """Return (balance_in_smallest_units, decimals) for owner's associated accounts of a mint.
        Uses getTokenAccountsByOwner with jsonParsed encoding.
        """
        try:
            owner = str(Pubkey.from_string(owner_address))
            mint = str(Pubkey.from_string(mint_address))
        except Exception:
            return 0, 0
        try:
            # Direct RPC to ensure jsonParsed support regardless of solana-py helpers
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTokenAccountsByOwner",
                "params": [
                    owner,
                    {"mint": mint},
                    {"encoding": "jsonParsed"}
                ],
            }
            # Use underlying endpoint from client
            rpc_url = self.client._provider.endpoint_uri  # type: ignore[attr-defined]
            r = requests.post(rpc_url, json=payload, timeout=15)
            r.raise_for_status()
            data = r.json()
            value = (data.get("result") or {}).get("value", [])
            total = 0
            decimals = 0
            for acc in value:
                try:
                    info = acc["account"]["data"]["parsed"]["info"]
                    ta = info["tokenAmount"]
                    amt = int(ta.get("amount", "0"))
                    dec = int(ta.get("decimals", 0))
                    total += amt
                    decimals = max(decimals, dec)
                except Exception:
                    continue
            return total, decimals
        except Exception as e:
            logging.warning(f"[SOL] get_spl_balance failed: {e}")
            return 0, 0

    def _jup_swap_tx(self, quote_response: Dict, user_pubkey: str) -> Optional[str]:
        try:
            url = "https://quote-api.jup.ag/v6/swap"
            payload = {
                "quoteResponse": quote_response,
                "userPublicKey": user_pubkey,
                "wrapAndUnwrapSol": True,
                "dynamicComputeUnitLimit": True,
                "useSharedAccounts": True,
            }
            r = requests.post(url, json=payload, timeout=20)
            r.raise_for_status()
            data = r.json()
            swap_tx = data.get("swapTransaction")
            return swap_tx
        except Exception as e:
            logging.warning(f"[SOL] Jupiter swap build error: {e}")
            return None

    def _sign_and_send_b64_tx(self, tx_b64: str, keypair: SKeypair) -> bool:
        try:
            raw = base64.b64decode(tx_b64)
            # Deserialize v0 transaction, sign, and send
            vtx = VersionedTransaction.from_bytes(raw)
            vtx_signed = vtx.sign([keypair])
            resp = self.client.send_raw_transaction(bytes(vtx_signed), opts=TxOpts(skip_preflight=True, max_retries=3))
            sig = None
            try:
                sig = resp.get("result") or resp.get("value") or resp
            except Exception:
                pass
            logging.info(f"[SOL] swap submitted: {sig}")
            # Basic confirmation wait
            time.sleep(1.0)
            return True
        except Exception as e:
            logging.error(f"[SOL] send failed: {e}")
            return False

    def _load_keypair(self, wallet: Dict) -> SKeypair:
        secret = (wallet.get('private_key') or '').strip()
        # Try base58-encoded 64-byte secret key
        try:
            sk_bytes = base58.b58decode(secret)
            if len(sk_bytes) in (64, 32):
                if len(sk_bytes) == 32:
                    # Not enough bytes for solders, bail
                    raise ValueError("Expected 64-byte base58 secret for solders Keypair")
                return SKeypair.from_bytes(sk_bytes)
        except Exception:
            pass
        # Not supported formats
        raise ValueError("Unsupported SOL private_key format; expected base58-encoded 64-byte secret")

    def _has_min_balance(self, address: str, lamports_needed: int) -> bool:
        try:
            bal = self.client.get_balance(Pubkey.from_string(address)).value
            return int(bal) >= int(lamports_needed)
        except Exception:
            return False
