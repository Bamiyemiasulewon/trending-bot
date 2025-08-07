import os
import asyncio
import logging
from web3 import Web3
from solana.rpc.async_api import AsyncClient
from solders.transaction import Transaction
from solders.system_program import transfer
from decimal import Decimal
from datetime import datetime
from typing import Dict, List, Optional
import random
import traceback

class CentralWalletManager:
    def __init__(self, is_testnet: bool = False):
        self.is_testnet = is_testnet
        
        # Initialize Web3 connections
        self.eth_w3 = Web3(Web3.HTTPProvider(
            os.getenv('ETH_SEPOLIA_RPC') if is_testnet else os.getenv('ETH_MAINNET_RPC')
        ))
        self.bsc_w3 = Web3(Web3.HTTPProvider(
            os.getenv('BSC_TESTNET_RPC') if is_testnet else os.getenv('BSC_MAINNET_RPC')
        ))
        self.sol_client = AsyncClient(
            os.getenv('SOLANA_DEVNET_RPC') if is_testnet else os.getenv('SOLANA_MAINNET_RPC')
        )
        
        # Load central wallet keys
        self._load_central_wallets()
        
        # Configure funding amounts
        self.fund_amounts = {
            'ETH': float(os.getenv('WALLET_FUND_AMOUNT_ETH', 0.0001)),
            'BNB': float(os.getenv('WALLET_FUND_AMOUNT_BNB', 0.0001)),
            'SOL': float(os.getenv('WALLET_FUND_AMOUNT_SOL', 0.0001))
        }
        
        self.confirmations = {
            'ETH': int(os.getenv('ETH_CONFIRMATIONS', 12)),
            'BNB': int(os.getenv('BSC_CONFIRMATIONS', 5)),
            'SOL': int(os.getenv('SOL_CONFIRMATIONS', 1))
        }
        
        self.logger = logging.getLogger('CentralWalletManager')

    def _load_central_wallets(self):
        """Load central wallet configurations"""
        self.central_wallets = {
            'ETH': {
                'address': os.getenv('ETH_CENTRAL_WALLET'),
                'key': os.getenv('ETH_CENTRAL_WALLET_KEY')
            },
            'BNB': {
                'address': os.getenv('BSC_CENTRAL_WALLET'),
                'key': os.getenv('BSC_CENTRAL_WALLET_KEY')
            },
            'SOL': {
                'address': os.getenv('SOL_CENTRAL_WALLET'),
                'key': os.getenv('SOL_CENTRAL_WALLET_KEY')
            }
        }

    async def verify_central_wallet_funding(self, chain: str, amount: float) -> bool:
        """Verify if central wallet has received required funding"""
        try:
            if chain in ['ETH', 'BNB']:
                w3 = self.eth_w3 if chain == 'ETH' else self.bsc_w3
                balance = w3.eth.get_balance(self.central_wallets[chain]['address'])
                return w3.from_wei(balance, 'ether') >= amount
            elif chain == 'SOL':
                response = await self.sol_client.get_balance(self.central_wallets['SOL']['address'])
                balance = response['result']['value'] / 10**9  # Convert lamports to SOL
                return balance >= amount
            return False
        except Exception as e:
            self.logger.error(f"Error verifying central wallet funding: {str(e)}")
            return False

    async def distribute_funds(self, chain: str, wallets: List[str], campaign_id: str) -> Dict:
        """Distribute funds to bot wallets"""
        results = {
            'success': [],
            'failed': [],
            'total_distributed': 0
        }
        
        try:
            # Randomize distribution amount slightly
            base_amount = self.fund_amounts[chain]
            
            for wallet in wallets:
                amount = base_amount + random.uniform(-0.00001, 0.00001)
                success = await self._send_funds(chain, wallet, amount)
                
                if success:
                    results['success'].append(wallet)
                    results['total_distributed'] += amount
                else:
                    results['failed'].append(wallet)
                
                # Add random delay between transactions
                await asyncio.sleep(random.uniform(10, 30))
            
        except Exception as e:
            self.logger.error(f"Error in fund distribution: {str(e)}")
            
        return results

    async def _send_funds(self, chain: str, to_address: str, amount: float) -> bool:
        """Send funds to a single wallet"""
        try:
            if chain in ['ETH', 'BNB']:
                w3 = self.eth_w3 if chain == 'ETH' else self.bsc_w3
                
                nonce = w3.eth.get_transaction_count(
                    self.central_wallets[chain]['address']
                )
                
                # Estimate gas
                gas_price = w3.eth.gas_price
                
                transaction = {
                    'nonce': nonce,
                    'to': to_address,
                    'value': w3.to_wei(amount, 'ether'),
                    'gas': 21000,  # Standard ETH transfer
                    'gasPrice': gas_price
                }
                
                # Sign and send transaction
                signed_txn = w3.eth.account.sign_transaction(
                    transaction,
                    self.central_wallets[chain]['key']
                )
                tx_hash = w3.eth.send_raw_transaction(signed_txn.rawTransaction)
                
                # Wait for confirmation
                receipt = w3.eth.wait_for_transaction_receipt(
                    tx_hash,
                    timeout=600,
                    poll_latency=2
                )
                
                return receipt['status'] == 1
                
            elif chain == 'SOL':
                # Create Solana transfer transaction
                transfer_ix = transfer(
                    TransferParams(
                        from_pubkey=self.central_wallets['SOL']['address'],
                        to_pubkey=to_address,
                        lamports=int(amount * 10**9)  # Convert SOL to lamports
                    )
                )
                
                transaction = Transaction().add(transfer_ix)
                
                # Sign and send transaction
                result = await self.sol_client.send_transaction(
                    transaction,
                    self.central_wallets['SOL']['key']
                )
                
                return 'result' in result and result['result']
                
            return False
        except Exception as e:
            self.logger.error(f"Error sending funds to {to_address}: {str(e)}")
            return False

    async def get_wallet_balance(self, chain: str, address: str) -> Optional[float]:
        """Get wallet balance"""
        try:
            if chain in ['ETH', 'BNB']:
                w3 = self.eth_w3 if chain == 'ETH' else self.bsc_w3
                balance = w3.eth.get_balance(address)
                return float(w3.from_wei(balance, 'ether'))
            elif chain == 'SOL':
                response = await self.sol_client.get_balance(address)
                return float(response['result']['value']) / 10**9
        except Exception as e:
            self.logger.error(f"Error getting wallet balance: {str(e)}")
            return None

    def get_central_wallet_address(self, chain: str) -> str:
        """Get central wallet address for a chain"""
        return self.central_wallets.get(chain, {}).get('address', '')
