import os
import json
import logging
import random
import asyncio
from typing import Dict, List, Optional, Tuple
from pathlib import Path
from decimal import Decimal
from web3 import Web3, HTTPProvider
# POA middleware is included by default in web3.py v7 for BSC
from eth_account import Account
from eth_abi import encode_abi
from hexbytes import HexBytes

from trading_config import (
    MIN_WALLET_BALANCE,
    TOP_UP_AMOUNT,
    PANCAKE_ROUTER,
    TOKEN_PAIRS,
    GAS_PRICE,
    SLIPPAGE,
    TRADE_AMOUNT,
    TRADING_MODES
)

class WalletManager:
    def __init__(self, config_path: str = None, rpc_url: str = None):
        self.config_path = config_path or os.path.join(os.path.dirname(__file__), 'wallet_config.json')
        self.wallets: Dict[str, List[Dict]] = {'BNB': []}  # Focus on BNB chain only
        self.load_wallets()
        
        # Initialize Web3
        self.rpc_url = rpc_url or os.getenv('BSC_MAINNET_RPC')
        if not self.rpc_url:
            raise ValueError("BSC_MAINNET_RPC not found in environment variables")
            
        self.w3 = Web3(HTTPProvider(self.rpc_url))
        self.w3.middleware_onion.inject(geth_poa_middleware, layer=0)
        
        # Load PancakeSwap Router ABI
        with open(os.path.join(os.path.dirname(__file__), 'pancakeswap_router_abi.json')) as f:
            self.router_abi = json.load(f)
            
        self.router_contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(PANCAKE_ROUTER),
            abi=self.router_abi
        )
        
        # ERC20 ABI for token interactions
        self.erc20_abi = [
            {
                'constant': True,
                'inputs': [{'name': '_owner', 'type': 'address'}],
                'name': 'balanceOf',
                'outputs': [{'name': 'balance', 'type': 'uint256'}],
                'type': 'function'
            },
            {
                'constant': False,
                'inputs': [
                    {'name': '_spender', 'type': 'address'},
                    {'name': '_value', 'type': 'uint256'}
                ],
                'name': 'approve',
                'outputs': [{'name': 'success', 'type': 'bool'}],
                'type': 'function'
            }
        ]
        
        self.logger = logging.getLogger('WalletManager')
        self.logger.setLevel(logging.INFO)
        
    def load_wallets(self):
        """Load wallet configurations from file"""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r') as f:
                    self.wallets = json.load(f)
            except Exception as e:
                logging.error(f"Failed to load wallet config: {str(e)}")
                
    def save_wallets(self):
        """Save wallet configurations to file"""
        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            
            with open(self.config_path, 'w') as f:
                json.dump(self.wallets, f, indent=2)
        except Exception as e:
            logging.error(f"Failed to save wallet config: {str(e)}")
            
    def create_bnb_wallet(self) -> Dict:
        """Create a new BSC wallet"""
        account = Account.create()
        wallet = {
            'address': account.address,
            'private_key': account.key.hex(),
            'balance': 0,
            'nonce': 0,
            'last_used': None,
            'is_active': True
        }
        self.wallets['BNB'].append(wallet)
        self.save_wallets()
        return wallet
        
    def create_sol_wallet(self) -> Dict:
        """Create a new Solana wallet"""
        keypair = Keypair()
        wallet = {
            'address': str(keypair.public_key),
            'private_key': keypair.secret_key.hex(),
            'balance': 0
        }
        return wallet
        
    def generate_wallets(self, chain: str, count: int = 50):
        """Generate multiple wallets for a specific chain"""
        chain = chain.upper()
        if chain not in ['ETH', 'BNB', 'SOL']:
            raise ValueError(f"Unsupported chain: {chain}")
            
        for _ in range(count):
            wallet = self.create_sol_wallet() if chain == 'SOL' else self.create_eth_wallet()
            self.wallets[chain].append(wallet)
            
        self.save_wallets()
        
    def get_available_wallet(self, chain: str) -> Optional[Dict]:
        """Get a wallet with sufficient balance for the specified chain"""
        chain = chain.upper()
        if chain not in self.wallets:
            raise ValueError(f"Unsupported chain: {chain}")
            
        # Find a wallet with sufficient balance
        for wallet in self.wallets[chain]:
            if wallet['balance'] > 0:  # You might want to set a minimum balance threshold
                return wallet
                
        return None
        
    def update_wallet_balance(self, chain: str, address: str, balance: float):
        """Update the balance of a specific wallet"""
        chain = chain.upper()
        if chain not in self.wallets:
            raise ValueError(f"Unsupported chain: {chain}")
            
        for wallet in self.wallets[chain]:
            if wallet['address'].lower() == address.lower():
                wallet['balance'] = balance
                self.save_wallets()
                break
                
    def get_wallet_count(self, chain: str) -> int:
        """Get the number of wallets for a specific chain"""
        chain = chain.upper()
        if chain not in self.wallets:
            raise ValueError(f"Unsupported chain: {chain}")
            
        return len(self.wallets[chain])
        
    async def check_wallet_balances(self, chain: str, provider_url: str = None, min_balance: float = 0.00085) -> dict:
        """Check and update balances for all wallets on a chain with improved error handling and retries.
        
        Args:
            chain: The blockchain network (ETH, BNB, SOL)
            provider_url: Optional RPC URL. If not provided, uses the instance's Web3 provider.
            min_balance: Minimum balance threshold to consider a wallet as 'funded'
            
        Returns:
            dict: {
                'total_wallets': int,
                'funded_wallets': int,
                'total_balance': float,
                'wallets': List[dict]  # List of wallet details with balances
            }
        """
        chain = chain.upper()
        if chain not in self.wallets:
            raise ValueError(f"Unsupported chain: {chain}")
            
        result = {
            'total_wallets': len(self.wallets[chain]),
            'funded_wallets': 0,
            'total_balance': 0.0,
            'wallets': []
        }
        
        try:
            if chain in ['ETH', 'BNB']:
                w3 = Web3(Web3.HTTPProvider(provider_url)) if provider_url else self.w3
                if not w3.is_connected():
                    raise ConnectionError("Failed to connect to Web3 provider")
                    
                for wallet in self.wallets[chain]:
                    wallet_info = wallet.copy()
                    try:
                        # Get balance with retry logic
                        balance_wei = await self._get_balance_with_retry(w3, wallet['address'])
                        balance = float(w3.from_wei(balance_wei, 'ether'))
                        
                        # Update wallet info
                        wallet_info['balance'] = balance
                        wallet_info['is_funded'] = balance >= min_balance
                        
                        # Update result counters
                        if wallet_info['is_funded']:
                            result['funded_wallets'] += 1
                            result['total_balance'] += balance
                            
                    except Exception as e:
                        self.logger.error(f"Error checking balance for {wallet['address']}: {str(e)}")
                        wallet_info['error'] = str(e)
                        wallet_info['is_funded'] = False
                    
                    result['wallets'].append(wallet_info)
                    
            elif chain == 'SOL':
                # TODO: Implement Solana balance checking
                pass
                
            # Save updated balances
            self.save_wallets()
            
        except Exception as e:
            self.logger.error(f"Error in check_wallet_balances: {str(e)}")
            raise
            
        return result
        
    async def _get_balance_with_retry(self, w3, address: str, max_retries: int = 3, delay: float = 1.0) -> int:
        """Helper method to get wallet balance with retry logic"""
        last_error = None
        for attempt in range(max_retries):
            try:
                # Add a small delay between retries
                if attempt > 0:
                    await asyncio.sleep(delay * attempt)
                return w3.eth.get_balance(address)
            except Exception as e:
                last_error = e
                self.logger.warning(f"Attempt {attempt + 1} failed for {address}: {str(e)}")
        
        self.logger.error(f"Failed to get balance for {address} after {max_retries} attempts: {str(last_error)}")
        raise last_error
        
    def get_funded_wallets(self, chain: str, min_balance: float = 0.00085) -> List[Dict]:
        """Get a list of wallets with balance >= min_balance"""
        chain = chain.upper()
        if chain not in self.wallets:
            raise ValueError(f"Unsupported chain: {chain}")
            
        return [
            w for w in self.wallets[chain] 
            if isinstance(w.get('balance'), (int, float)) and w['balance'] >= min_balance
        ]
        
    def get_total_distributed(self, chain: str) -> float:
        """Get total distributed amount for a chain"""
        chain = chain.upper()
        if chain not in self.wallets:
            raise ValueError(f"Unsupported chain: {chain}")
            
        return sum(
            w.get('distributed', 0) 
            for w in self.wallets[chain] 
            if isinstance(w.get('distributed'), (int, float))
        )
        
    def rotate_wallets(self, chain: str, used_addresses: List[str]):
        """Mark wallets as used and rotate to ensure even distribution"""
        chain = chain.upper()
        if chain not in self.wallets:
            raise ValueError(f"Unsupported chain: {chain}")
            
        # Update last used timestamp for wallets
        current_time = time.time()
        for wallet in self.wallets[chain]:
            if wallet['address'] in used_addresses:
                wallet['last_used'] = current_time
                
        self.save_wallets()
