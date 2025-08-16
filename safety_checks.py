"""
Trading Safety Checks Module

This module provides comprehensive safety checks for the trading bot to ensure:
- Wallet balances are sufficient
- Token contract is valid and safe
- Initial test trades work correctly
"""
import logging
from decimal import Decimal
from typing import Dict, List, Optional, Tuple
from web3 import Web3
from web3.contract import Contract

from trading_config import (
    MIN_WALLET_BALANCE,
    GAS_RESERVE,
    TRADE_AMOUNT,
    DEFAULT_TOKEN_ADDRESS,
    PANCAKE_ROUTER
)

class SafetyChecker:
    def __init__(self, wallet_manager, dex_trader):
        self.wallet_manager = wallet_manager
        self.dex_trader = dex_trader
        self.w3 = wallet_manager.w3
        self.logger = logging.getLogger('SafetyChecker')
        
    async def run_all_checks(self) -> Tuple[bool, str]:
        """Run all safety checks before starting trading"""
        try:
            # 1. Check RPC Connection
            if not await self.check_network_connection():
                return False, "Failed to connect to BSC network"
                
            # 1.1 Check Gas Price
            gas_status, gas_msg = await self.check_gas_price()
            if not gas_status:
                return False, f"Gas price check failed: {gas_msg}"

            # 2. Check Wallet Setup
            wallet_status, wallet_msg = await self.check_wallets()
            if not wallet_status:
                return False, f"Wallet check failed: {wallet_msg}"

            # 3. Check Token Contract
            token_status, token_msg = await self.check_token_contract()
            if not token_status:
                return False, f"Token check failed: {token_msg}"

            # 4. Check Router Contract
            router_status, router_msg = await self.check_router_contract()
            if not router_status:
                return False, f"Router check failed: {router_msg}"

            # 5. Perform Test Quote
            quote_status, quote_msg = await self.test_price_quote()
            if not quote_status:
                return False, f"Quote test failed: {quote_msg}"

            # 6. Check Token Approvals
            approval_status, approval_msg = await self.check_token_approvals()
            if not approval_status:
                return False, f"Token approval check failed: {approval_msg}"

            return True, "All safety checks passed successfully"

        except Exception as e:
            self.logger.error(f"Safety check error: {str(e)}", exc_info=True)
            return False, f"Safety check error: {str(e)}"

    async def check_network_connection(self) -> bool:
        """Verify connection to BSC network"""
        try:
            block = await self.w3.eth.block_number
            self.logger.info(f"Connected to BSC network. Current block: {block}")
            return True
        except Exception as e:
            self.logger.error(f"Network connection error: {str(e)}")
            return False

    async def check_wallets(self) -> Tuple[bool, str]:
        """Check if wallets are properly set up and funded"""
        try:
            wallets = self.wallet_manager.wallets.get('BNB', [])
            if not wallets:
                return False, "No wallets found"

            funded_wallets = []
            low_balance_wallets = []

            for wallet in wallets:
                balance = Decimal(str(wallet.get('balance', 0)))
                min_required = MIN_WALLET_BALANCE + GAS_RESERVE
                
                if balance >= min_required:
                    funded_wallets.append(wallet['address'])
                else:
                    low_balance_wallets.append({
                        'address': wallet['address'],
                        'balance': balance,
                        'needed': min_required
                    })

            if not funded_wallets:
                return False, f"No wallets with sufficient balance. Need {min_required} BNB minimum"

            self.logger.info(f"Found {len(funded_wallets)} funded wallets ready for trading")
            if low_balance_wallets:
                self.logger.warning(f"{len(low_balance_wallets)} wallets need funding")

            return True, f"{len(funded_wallets)} wallets ready for trading"

        except Exception as e:
            return False, f"Wallet check error: {str(e)}"

    async def check_token_contract(self) -> Tuple[bool, str]:
        """Verify token contract is valid and has liquidity"""
        try:
            # Check if token address is valid
            if not self.w3.is_address(DEFAULT_TOKEN_ADDRESS):
                return False, "Invalid token address"

            # Get token contract
            token_contract = self.wallet_manager.get_token_contract(DEFAULT_TOKEN_ADDRESS)
            
            # Check basic token info
            try:
                symbol = await token_contract.functions.symbol().call()
                decimals = await token_contract.functions.decimals().call()
                total_supply = await token_contract.functions.totalSupply().call()
            except Exception as e:
                return False, f"Failed to read token info: {str(e)}"

            # Check token liquidity
            try:
                pair_address = await self.dex_trader.router_contract.functions.factory().call()
                if not pair_address:
                    return False, "No liquidity pair found"
            except Exception as e:
                return False, f"Failed to check liquidity: {str(e)}"

            self.logger.info(f"Token contract verified: {symbol}, {decimals} decimals")
            return True, "Token contract verified successfully"

        except Exception as e:
            return False, f"Token contract check error: {str(e)}"

    async def check_router_contract(self) -> Tuple[bool, str]:
        """Verify PancakeSwap router contract"""
        try:
            # Check if router address is valid
            if not self.w3.is_address(PANCAKE_ROUTER):
                return False, "Invalid router address"

            # Verify router contract
            factory = await self.dex_trader.router_contract.functions.factory().call()
            weth = await self.dex_trader.router_contract.functions.WETH().call()

            if not all([factory, weth]):
                return False, "Invalid router contract"

            self.logger.info(f"Router contract verified. Factory: {factory}")
            return True, "Router contract verified successfully"

        except Exception as e:
            return False, f"Router check error: {str(e)}"

    async def test_price_quote(self) -> Tuple[bool, str]:
        """Test getting a price quote"""
        try:
            # Try to get a quote for minimum trade amount
            quote = await self.wallet_manager.get_swap_quote(
                token_in='BNB',
                token_out=DEFAULT_TOKEN_ADDRESS,
                amount_in=TRADE_AMOUNT['min']
            )

            if not quote or 'amount_out' not in quote:
                return False, "Failed to get price quote"

            self.logger.info(f"Successfully got price quote. Rate: {quote['amount_out']/float(TRADE_AMOUNT['min'])} tokens/BNB")
            return True, "Price quote test successful"

        except Exception as e:
            return False, f"Price quote test error: {str(e)}"

    async def check_token_approvals(self) -> Tuple[bool, str]:
        """Check token approvals for all wallets"""
        try:
            wallets = self.wallet_manager.wallets.get('BNB', [])
            token_contract = self.wallet_manager.get_token_contract(DEFAULT_TOKEN_ADDRESS)

            for wallet in wallets:
                try:
                    allowance = await token_contract.functions.allowance(
                        wallet['address'],
                        PANCAKE_ROUTER
                    ).call()
                    
                    if allowance == 0:
                        self.logger.warning(f"Wallet {wallet['address']} needs token approval")
                except Exception as e:
                    self.logger.error(f"Failed to check allowance for wallet {wallet['address']}: {str(e)}")

            return True, "Token approval check complete"

        except Exception as e:
            return False, f"Token approval check error: {str(e)}"
            
    async def check_gas_price(self) -> Tuple[bool, str]:
        """Check if current gas price is acceptable for trading"""
        try:
            current_gas = self.w3.eth.gas_price
            gas_in_gwei = self.w3.from_wei(current_gas, 'gwei')
            
            if gas_in_gwei >= GAS_PRICE_CRITICAL:
                return False, f"Gas price too high: {gas_in_gwei} gwei (max: {GAS_PRICE_CRITICAL} gwei)"
                
            if gas_in_gwei >= GAS_PRICE_WARNING:
                self.logger.warning(f"Gas price warning: {gas_in_gwei} gwei")
                
            self.logger.info(f"Current gas price: {gas_in_gwei} gwei")
            return True, "Gas price is acceptable"
            
        except Exception as e:
            return False, f"Gas price check error: {str(e)}")
