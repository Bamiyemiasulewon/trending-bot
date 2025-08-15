import random
import time
import threading
from web3 import Web3
from log_manager import LogManager

class DexTrendManager:
    def __init__(self):
        self.active_trends = {}
        self.log_manager = LogManager()

    def start_trend(self, req, background_tasks):
        trend_id = f"{req.token_address}-{int(time.time())}"
        wallets = req.wallets
        duration = req.duration
        self.active_trends[trend_id] = {
            "token_address": req.token_address,
            "wallets": wallets,
            "start_time": time.time(),
            "duration": duration,
            "status": "running"
        }
        background_tasks.add_task(self._run_trend, trend_id, req)
        return trend_id

    def _run_trend(self, trend_id, req):
        end_time = time.time() + req.duration
        while time.time() < end_time and self.active_trends.get(trend_id, {}).get("status") == "running":
            for pk in req.wallets:
                self._simulate_activity(pk, req.token_address, req.min_gas, req.max_gas)
                time.sleep(random.randint(30, 300))  # random sleep between actions
        self.active_trends[trend_id]["status"] = "completed"

    def _simulate_activity(self, private_key, token_address, min_gas, max_gas):
        # Connect to BNB RPC
        w3 = Web3(Web3.HTTPProvider(os.getenv("BNB_RPC_URL")))
        acct = w3.eth.account.from_key(private_key)
        router_address = '0x10ED43C718714eb63d5aA57B78B54704E256024E'  # PancakeSwap Router
        router_abi = [
            # Only the swapExactETHForTokens function ABI
            {
                "inputs": [
                    {"internalType": "uint256", "name": "amountOutMin", "type": "uint256"},
                    {"internalType": "address[]", "name": "path", "type": "address[]"},
                    {"internalType": "address", "name": "to", "type": "address"},
                    {"internalType": "uint256", "name": "deadline", "type": "uint256"}
                ],
                "name": "swapExactETHForTokens",
                "outputs": [{"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}],
                "stateMutability": "payable",
                "type": "function"
            }
        ]
        router = w3.eth.contract(address=router_address, abi=router_abi)
        # Randomize buy amount and gas
        buy_amount = w3.toWei(random.uniform(0.01, 0.1), 'ether')
        gas_price = w3.toWei(random.uniform(5, 10), 'gwei')
        # Prepare swap path (BNB -> token)
        WBNB = '0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c'
        path = [WBNB, token_address]
        deadline = int(time.time()) + 600
        # Build transaction
        try:
            tx = router.functions.swapExactETHForTokens(
                0, path, acct.address, deadline
            ).build_transaction({
                'from': acct.address,
                'value': buy_amount,
                'gas': 300000,
                'gasPrice': gas_price,
                'nonce': w3.eth.get_transaction_count(acct.address)
            })
            signed_tx = w3.eth.account.sign_transaction(tx, private_key)
            tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            self.log_manager.log_activity(acct.address, tx_hash.hex(), time.time(), buy_amount, "buy")
        except Exception as e:
            print(f"Buy failed: {e}")
        # Optionally simulate sell (stub, real sell logic can be added)
        if random.random() > 0.5:
            # For demo, just log a sell
            self.log_manager.log_activity(acct.address, '0xSELLTXHASH', time.time(), buy_amount, "sell")

    def stop_trend(self, trend_id):
        if trend_id in self.active_trends:
            self.active_trends[trend_id]["status"] = "stopped"

    def get_status(self, token):
        return {tid: info for tid, info in self.active_trends.items() if info["token_address"] == token}

    def get_all_status(self):
        return self.active_trends
