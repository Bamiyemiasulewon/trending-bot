import os
from web3 import Web3
from eth_account import Account
from solana.keypair import Keypair
import json
import logging
from typing import Dict, List, Optional
from pathlib import Path

class WalletManager:
    def __init__(self, config_path: str = None):
        self.config_path = config_path or os.path.join(os.path.dirname(__file__), 'wallet_config.json')
        self.wallets: Dict[str, List[Dict]] = {
            'ETH': [],
            'BNB': [],
            'SOL': []
        }
        self.load_wallets()
        
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
            
    def create_eth_wallet(self) -> Dict:
        """Create a new Ethereum/BSC wallet"""
        account = Account.create()
        wallet = {
            'address': account.address,
            'private_key': account.key.hex(),
            'balance': 0
        }
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
        
    async def check_wallet_balances(self, chain: str, provider_url: str):
        """Check and update balances for all wallets on a chain"""
        chain = chain.upper()
        if chain not in self.wallets:
            raise ValueError(f"Unsupported chain: {chain}")
            
        if chain in ['ETH', 'BNB']:
            w3 = Web3(Web3.HTTPProvider(provider_url))
            for wallet in self.wallets[chain]:
                try:
                    balance = w3.eth.get_balance(wallet['address'])
                    wallet['balance'] = w3.from_wei(balance, 'ether')
                except Exception as e:
                    logging.error(f"Failed to check balance for {wallet['address']}: {str(e)}")
        else:  # SOL
            # Implement Solana balance check
            pass
            
        self.save_wallets()
        
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
