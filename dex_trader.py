"""
DEX Trading Bot for BNB Chain

This module handles automated trading on PancakeSwap with multiple wallets
to create volume and trend on DEXScreener.
"""
import asyncio
import logging
import random
import time
from decimal import Decimal
from typing import Dict, List, Optional, Tuple
from web3 import Web3
from web3.types import TxReceipt

from trading_config import (
    TRADE_AMOUNT,
    TRADE_INTERVAL,
    TRADING_MODES,
    SLIPPAGE,
    MIN_WALLET_BALANCE,
    TOP_UP_AMOUNT,
    DEFAULT_TOKEN_ADDRESS,
    PANCAKE_ROUTER,
    TOKEN_PAIRS,
    LOG_LEVEL
)
from wallet_manager import WalletManager

class DEXTrader:
    def __init__(self, rpc_url: str = None):
        """Initialize the DEX trader"""
        self.logger = self._setup_logging()
        self.wallet_manager = WalletManager(rpc_url=rpc_url)
        self.running = False
        self.current_trades = {}
        
    def _setup_logging(self) -> logging.Logger:
        """Configure logging"""
        logger = logging.getLogger('DEXTrader')
        logger.setLevel(LOG_LEVEL)
        
        # Create console handler
        ch = logging.StreamHandler()
        ch.setLevel(LOG_LEVEL)
        
        # Create formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        ch.setFormatter(formatter)
        
        # Add handler to logger
        if not logger.handlers:
            logger.addHandler(ch)
            
        return logger
    
    async def start(self):
        """Start the trading bot"""
        self.running = True
        self.logger.info("Starting DEX Trading Bot...")
        
        try:
            # Check wallet balances and top up if needed
            await self._check_wallet_balances()
            
            # Start trading loop
            while self.running:
                try:
                    # Execute trades based on trading modes
                    if TRADING_MODES['buy']:
                        await self._execute_buys()
                    
                    if TRADING_MODES['sell']:
                        await self._execute_sells()
                    
                    # Sleep for a random interval between trades
                    sleep_time = random.randint(
                        TRADE_INTERVAL['min'], 
                        TRADE_INTERVAL['max']
                    )
                    self.logger.info(f"Waiting {sleep_time} seconds until next trade...")
                    await asyncio.sleep(sleep_time)
                    
                except Exception as e:
                    self.logger.error(f"Error in trading loop: {str(e)}", exc_info=True)
                    await asyncio.sleep(30)  # Wait before retrying
                    
        except asyncio.CancelledError:
            self.logger.info("Trading bot stopped by user")
        except Exception as e:
            self.logger.error(f"Fatal error in trading bot: {str(e)}", exc_info=True)
        finally:
            self.running = False
            self.logger.info("Trading bot stopped")
    
    async def stop(self):
        """Stop the trading bot"""
        self.logger.info("Stopping trading bot...")
        self.running = False
    
    async def _check_wallet_balances(self):
        """Check wallet balances and top up if needed"""
        self.logger.info("Checking wallet balances...")
        
        # Get wallets needing top-up
        wallets_needing_topup = self.wallet_manager.check_wallets_balance()
        
        if not wallets_needing_topup:
            self.logger.info("All wallets have sufficient balance")
            return
            
        self.logger.info(f"Found {len(wallets_needing_topup)} wallets needing top-up")
        
        # Top up wallets
        for wallet_info in wallets_needing_topup:
            try:
                self.logger.info(
                    f"Topping up wallet {wallet_info['address']} "
                    f"(balance: {wallet_info['current_balance']} BNB)"
                )
                
                # Get a wallet with sufficient balance to send from
                sender_wallet = self.wallet_manager.get_available_wallet('BNB')
                if not sender_wallet:
                    self.logger.warning("No wallet with sufficient balance for top-up")
                    break
                    
                # Send BNB to the wallet
                tx_hash = await self.wallet_manager.send_bnb(
                    sender_wallet['private_key'],
                    wallet_info['address'],
                    TOP_UP_AMOUNT
                )
                
                self.logger.info(
                    f"Sent {TOP_UP_AMOUNT} BNB to {wallet_info['address']}. "
                    f"Tx: {tx_hash}"
                )
                
                # Wait for transaction to be mined
                await asyncio.sleep(5)  # Adjust based on network conditions
                
            except Exception as e:
                self.logger.error(
                    f"Failed to top up wallet {wallet_info['address']}: {str(e)}",
                    exc_info=True
                )
    
    async def _execute_buys(self):
        """Execute buy orders using available wallets"""
        self.logger.info("Executing buy orders...")
        
        # Get active wallets with sufficient balance
        active_wallets = [
            w for w in self.wallet_manager.wallets['BNB'] 
            if w.get('is_active', True) and 
            Decimal(str(w.get('balance', 0))) >= MIN_WALLET_BALANCE
        ]
        
        if not active_wallets:
            self.logger.warning("No active wallets with sufficient balance for buying")
            return
            
        # Select a random wallet
        wallet = random.choice(active_wallets)
        
        # Determine trade amount
        if TRADE_AMOUNT['min'] == TRADE_AMOUNT['max']:
            amount = TRADE_AMOUNT['min']
        else:
            amount = random.uniform(TRADE_AMOUNT['min'], TRADE_AMOUNT['max'])
        
        # Execute the trade
        try:
            self.logger.info(
                f"Buying tokens with wallet {wallet['address']} "
                f"(amount: {amount} BNB)"
            )
            
            # Execute the swap (BNB to token)
            tx_hash = await self._execute_swap(
                wallet=wallet,
                token_in='BNB',
                token_out=DEFAULT_TOKEN_ADDRESS,
                amount_in=Decimal(str(amount)),
                slippage=SLIPPAGE
            )
            
            if tx_hash:
                self.logger.info(f"Buy order executed. Tx: {tx_hash}")
                # Update wallet nonce and last used time
                wallet['nonce'] += 1
                wallet['last_used'] = int(time.time())
                self.wallet_manager.save_wallets()
                
        except Exception as e:
            self.logger.error(f"Error executing buy order: {str(e)}", exc_info=True)
    
    async def _execute_sells(self):
        """Execute sell orders using available wallets"""
        self.logger.info("Executing sell orders...")
        
        # Get active wallets with token balance
        active_wallets = []
        for wallet in self.wallet_manager.wallets['BNB']:
            if not wallet.get('is_active', True):
                continue
                
            # Check token balance
            token_balance = await self.wallet_manager.get_token_balance(
                wallet['address'],
                DEFAULT_TOKEN_ADDRESS
            )
            
            if token_balance > 0:
                wallet['token_balance'] = token_balance
                active_wallets.append(wallet)
        
        if not active_wallets:
            self.logger.warning("No active wallets with token balance for selling")
            return
            
        # Select a random wallet with tokens
        wallet = random.choice(active_wallets)
        
        # Execute the trade
        try:
            self.logger.info(
                f"Selling tokens from wallet {wallet['address']} "
                f"(balance: {wallet['token_balance']} tokens)"
            )
            
            # Execute the swap (token to BNB)
            tx_hash = await self._execute_swap(
                wallet=wallet,
                token_in=DEFAULT_TOKEN_ADDRESS,
                token_out='BNB',
                amount_in=Decimal(str(wallet['token_balance'])),
                slippage=SLIPPAGE
            )
            
            if tx_hash:
                self.logger.info(f"Sell order executed. Tx: {tx_hash}")
                # Update wallet nonce and last used time
                wallet['nonce'] += 1
                wallet['last_used'] = int(time.time())
                self.wallet_manager.save_wallets()
                
        except Exception as e:
            self.logger.error(f"Error executing sell order: {str(e)}", exc_info=True)
    
    async def _execute_swap(
        self,
        wallet: Dict,
        token_in: str,
        token_out: str,
        amount_in: Decimal,
        slippage: float = 0.5
    ) -> Optional[str]:
        """Execute a token swap"""
        try:
            # Prepare the transaction
            tx = await self.wallet_manager.prepare_transaction(
                wallet=wallet,
                token_in=token_in,
                token_out=token_out,
                amount_in=amount_in,
                slippage=slippage
            )
            
            if not tx:
                self.logger.error("Failed to prepare transaction")
                return None
            
            # Sign the transaction
            signed_tx = self.wallet_manager.w3.eth.account.sign_transaction(
                tx, 
                private_key=wallet['private_key']
            )
            
            # Send the transaction
            tx_hash = self.wallet_manager.w3.eth.send_raw_transaction(
                signed_tx.raw_transaction
            ).hex()
            
            # Wait for transaction receipt
            receipt = await self.wallet_manager.w3.eth.wait_for_transaction_receipt(tx_hash)
            
            if receipt.status == 1:
                self.logger.info(f"Transaction successful: {tx_hash}")
                return tx_hash
            else:
                self.logger.error(f"Transaction failed: {tx_hash}")
                return None
                
        except Exception as e:
            self.logger.error(f"Error executing swap: {str(e)}", exc_info=True)
            return None


async def main():
    """Main function to run the DEX trader"""
    # Initialize the trader
    trader = DEXTrader()
    
    try:
        # Start the trading bot
        await trader.start()
    except KeyboardInterrupt:
        # Handle graceful shutdown
        await trader.stop()
    except Exception as e:
        print(f"Fatal error: {str(e)}")
        import traceback
        traceback.print_exc()
    finally:
        print("Bot stopped")


if __name__ == "__main__":
    asyncio.run(main())
