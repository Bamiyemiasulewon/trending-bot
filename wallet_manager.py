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
from dotenv import load_dotenv

# Load environment variables from project root .env if present
_ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
if os.path.exists(_ENV_PATH):
    load_dotenv(_ENV_PATH)
else:
    load_dotenv()

from trading_config import (
    MIN_WALLET_BALANCE,
    TOP_UP_AMOUNT,
    PANCAKE_ROUTER,
    TOKEN_PAIRS,
    GAS_PRICE,
    SLIPPAGE,  # decimal, e.g., 0.01 for 1%
    TRADE_AMOUNT,
    TRADING_MODES
)

class WalletManager:
    def __init__(self, config_path: str = None, rpc_url: str = None):
        self.config_path = config_path or os.path.join(os.path.dirname(__file__), 'wallet_config.json')
        self.wallets: Dict[str, List[Dict]] = {'BNB': []}  # Focus on BNB chain only
        self.load_wallets()
        
        # Initialize Web3
        self.rpc_url = rpc_url or os.getenv('BSC_MAINNET_RPC') or 'https://bsc.publicnode.com'
        
        self.w3 = Web3(HTTPProvider(self.rpc_url))
        # Inject POA middleware when available; ignore if already handled by web3
        try:
            from web3.middleware import geth_poa_middleware  # type: ignore
            self.w3.middleware_onion.inject(geth_poa_middleware, layer=0)
        except Exception:
            pass
        
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
        """Check and update balances for all wallets with detailed reporting.
        
        Args:
            chain: The blockchain network (ETH, BNB, SOL)
            provider_url: Optional RPC URL. If not provided, uses the instance's Web3 provider.
            min_balance: Minimum balance threshold to consider a wallet as 'funded'
            
        Returns:
            dict: Detailed wallet information and balances
        """
        chain = chain.upper()
        if chain not in self.wallets:
            raise ValueError(f"Unsupported chain: {chain}")
        
        w3 = Web3(HTTPProvider(provider_url)) if provider_url else self.w3
        
        result = {
            'total_wallets': len(self.wallets[chain]),
            'funded_wallets': 0,
            'total_balance': 0.0,
            'low_balance_wallets': [],
            'error_wallets': [],
            'active_wallets': [],
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
        
    def get_total_balance(self, chain: str) -> dict:
        """
        Get total balance across all wallets for a specific chain
        
        Args:
            chain: The blockchain network (ETH, BNB, SOL)
            
        Returns:
            dict: {
                'total_balance': float,  # Total balance in native token
                'wallet_count': int,     # Number of wallets with balance
                'wallets': List[Dict]    # List of wallets with balances
            }
        """
        chain = chain.upper()
        if chain not in self.wallets:
            raise ValueError(f"Unsupported chain: {chain}")
            
        result = {
            'total_balance': 0.0,
            'wallet_count': 0,
            'wallets': []
        }
        
        for wallet in self.wallets[chain]:
            if 'balance' in wallet and isinstance(wallet['balance'], (int, float)) and wallet['balance'] > 0:
                result['total_balance'] += wallet['balance']
                result['wallet_count'] += 1
                result['wallets'].append({
                    'address': wallet['address'],
                    'balance': wallet['balance']
                })
                
        return result
        
    async def consolidate_funds(self, chain: str, destination_address: str, gas_price_gwei: float = None, 
                              max_gas_fee: float = 0.001) -> dict:
        """
        Transfer funds from all wallets to a destination address, leaving enough for gas fees.
        
        Args:
            chain: The blockchain network (ETH, BNB, SOL)
            destination_address: The address to send funds to
            gas_price_gwei: Gas price in Gwei (if None, uses current network gas price)
            max_gas_fee: Maximum gas fee to leave in each wallet (in native token)
            
        Returns:
            dict: {
                'success': bool,
                'total_sent': float,
                'tx_hashes': List[str],
                'errors': List[Dict]
            }
        """
        chain = chain.upper()
        if chain not in ['ETH', 'BNB']:
            raise ValueError("Only ETH and BNB chains are currently supported for consolidation")
            
        if not self.w3.is_address(destination_address):
            raise ValueError("Invalid destination address")
            
        destination_address = self.w3.to_checksum_address(destination_address)
        result = {
            'success': False,
            'total_sent': 0.0,
            'tx_hashes': [],
            'errors': []
        }
        
        # Get current gas price if not provided
        if gas_price_gwei is None:
            gas_price_wei = self.w3.eth.gas_price
        else:
            gas_price_wei = self.w3.to_wei(gas_price_gwei, 'gwei')
            
        # Estimate gas cost for a standard transfer (21,000 gas for ETH/BNB transfer)
        gas_limit = 21000
        gas_cost_wei = gas_price_wei * gas_limit
        gas_cost_eth = self.w3.from_wei(gas_cost_wei, 'ether')
        
        # Process each wallet
        for wallet in self.wallets[chain]:
            try:
                if 'private_key' not in wallet or not wallet.get('is_active', True):
                    continue
                    
                wallet_address = self.w3.to_checksum_address(wallet['address'])
                balance_wei = await self._get_balance_with_retry(self.w3, wallet_address)
                
                if balance_wei <= 0:
                    continue
                    
                balance_eth = self.w3.from_wei(balance_wei, 'ether')
                
                # Calculate amount to send (leave enough for gas)
                if balance_eth <= gas_cost_eth + max_gas_fee:
                    continue  # Skip if not enough to cover gas + max_gas_fee
                    
                amount_to_send_wei = balance_wei - (gas_cost_wei + self.w3.to_wei(max_gas_fee, 'ether'))
                
                # Build and send transaction
                nonce = self.w3.eth.get_transaction_count(wallet_address)
                tx = {
                    'nonce': nonce,
                    'to': destination_address,
                    'value': amount_to_send_wei,
                    'gas': gas_limit,
                    'gasPrice': gas_price_wei,
                    'chainId': 56 if chain == 'BNB' else 1  # BSC or ETH mainnet
                }
                
                # Sign and send transaction
                signed_tx = self.w3.eth.account.sign_transaction(tx, wallet['private_key'])
                tx_hash = self.w3.eth.send_raw_transaction(signed_tx.rawTransaction)
                
                # Wait for transaction receipt
                receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
                
                if receipt.status == 1:  # Success
                    result['tx_hashes'].append(tx_hash.hex())
                    result['total_sent'] += float(self.w3.from_wei(amount_to_send_wei, 'ether'))
                    
                    # Update wallet balance
                    wallet['balance'] = float(self.w3.from_wei(
                        self.w3.eth.get_balance(wallet_address), 'ether'
                    ))
                else:
                    result['errors'].append({
                        'wallet': wallet_address,
                        'error': 'Transaction failed',
                        'tx_hash': tx_hash.hex()
                    })
                    
            except Exception as e:
                self.logger.error(f"Error consolidating from {wallet.get('address')}: {str(e)}")
                result['errors'].append({
                    'wallet': wallet.get('address', 'unknown'),
                    'error': str(e)
                })
                continue
                
        result['success'] = len(result['errors']) == 0 or len(result['tx_hashes']) > 0
        self.save_wallets()
        return result
        
    def validate_wallet_address(self, address: str, chain: str) -> tuple[bool, str]:
        """
        Validate a wallet address for the specified chain
        
        Args:
            address: The wallet address to validate
            chain: The blockchain network (ETH, BNB, SOL)
            
        Returns:
            tuple: (is_valid: bool, error_message: str)
        """
        if not address or not isinstance(address, str):
            return False, "Address cannot be empty"
            
        address = address.strip()
        chain = chain.upper()
        
        if chain in ['ETH', 'BNB']:
            # Remove common prefixes if present
            if address.startswith('0x'):
                address = address[2:]
                
            # Validate length (40 hex chars without 0x)
            if len(address) != 40:
                return False, f"Invalid {chain} address length. Expected 40 hex characters (excluding 0x)."
                
            # Validate hex characters
            try:
                int(address, 16)
            except ValueError:
                return False, f"Invalid {chain} address format. Must be a valid hexadecimal number."
                
            # Convert to checksum address and validate
            try:
                checksum_address = self.w3.to_checksum_address(f"0x{address}")
                if checksum_address != f"0x{address}" and checksum_address.lower() != f"0x{address.lower()}":
                    return False, f"Invalid {chain} address checksum. Did you mean {checksum_address}?"
                return True, ""
            except Exception as e:
                return False, f"Invalid {chain} address: {str(e)}"
                
        elif chain == 'SOL':
            import base58
            # Remove URI scheme if present
            if ':' in address:
                address = address.split(':')[-1]
                
            # Validate length (32-44 chars)
            if len(address) < 32 or len(address) > 44:
                return False, "Invalid Solana address length. Expected 32-44 base58 characters."
                
            # Validate base58 encoding
            try:
                decoded = base58.b58decode(address)
                # Solana public keys are 32 bytes when decoded
                if len(decoded) != 32:
                    return False, "Invalid Solana address format. Decoded address must be 32 bytes."
                return True, ""
            except Exception as e:
                return False, f"Invalid Solana address: {str(e)}"
                
        return False, f"Unsupported blockchain: {chain}"
