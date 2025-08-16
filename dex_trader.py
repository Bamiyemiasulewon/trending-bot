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
from web3             from modules.path_optimizer import PathOptimizer
            from modules.quote_manager import QuoteManager
            
            if not hasattr(self, 'path_optimizer'):
                self.path_optimizer = PathOptimizer()
            if not hasattr(self, 'quote_manager'):
                self.quote_manager = QuoteManager()
            
            # Get optimized quote
            quotes = []
            for _ in range(2):  # Only 2 quotes needed with optimization
                quote = await self.quote_manager.get_optimized_quote(
                    self.wallet_manager,
                    'BNB',
                    DEFAULT_TOKEN_ADDRESS,
                    amount,
                    self.path_optimizer
                )
                if quote:
                    quotes.append(quote)
                await asyncio.sleep(0.5)  # Reduced delay
            
            if not quotes:
                self.logger.error("Failed to get any quotes for swap")
                return
                
            # Analyze price stability
            amounts_out = [Decimal(str(q['amount_out'])) for q in quotes]
            avg_amount = sum(amounts_out) / len(amounts_out)
            variance = max(abs(amt - avg_amount) / avg_amount for amt in amounts_out)
            
            # If price is too volatile, adjust slippage or abort
            if variance > 0.02:  # 2% variance threshold
                self.logger.warning(f"High price volatility detected: {variance*100:.2f}% variance")
                if variance > 0.05:  # 5% variance threshold
                    self.logger.error("Price too volatile, aborting trade")
                    return
                    
            # Use the most recent quote
            quote = quotes[-1]
            
            # Calculate price impact
            price_impact = quote.get('price_impact', 0)
            if price_impact > 0.1:  # 10% impact threshold
                self.logger.error(f"Price impact too high: {price_impact*100:.2f}%")
                return
                
            # Calculate minimum output with dynamic slippage based on conditions
            base_slippage = SLIPPAGE
            dynamic_slippage = base_slippage
            
            # Adjust slippage based on conditions
            if variance > 0.01:  # Price volatility adjustment
                dynamic_slippage *= (1 + variance * 5)
            if price_impact > 0.05:  # Price impact adjustment
                dynamic_slippage *= (1 + price_impact * 2)
            
            # Cap maximum slippage
            dynamic_slippage = min(dynamic_slippage, 0.15)  # 15% max slippage
            
            # Execute the swap with dynamic parameters
            tx_hash = await self._execute_swap(
                wallet=wallet,
                token_in='BNB',
                token_out=DEFAULT_TOKEN_ADDRESS,
                amount_in=amount,
                slippage=dynamic_slippage,
                quote=quote  # Pass the fresh quote
            )eb3
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
    
    async def _check_gas_price(self) -> bool:
        """Monitor gas price and decide if it's safe to trade"""
        try:
            current_gas = self.w3.eth.gas_price
            gas_in_gwei = self.w3.from_wei(current_gas, 'gwei')
            
            # Only pause trading if we're consistently above critical for multiple checks
            if gas_in_gwei >= GAS_PRICE_CRITICAL:
                # Add some tolerance (10% above critical) before completely stopping
                if gas_in_gwei >= (GAS_PRICE_CRITICAL * 1.1):
                    self.logger.error(f"Gas price very high: {gas_in_gwei} gwei - Pausing trading")
                    return False
                else:
                    self.logger.warning(f"Gas price critical: {gas_in_gwei} gwei - Will execute urgent trades only")
                    return True
                
            if gas_in_gwei >= GAS_PRICE_WARNING:
                self.logger.info(f"Gas price elevated: {gas_in_gwei} gwei - Continuing with normal trading")
                
            return True
            
        except Exception as e:
            self.logger.error(f"Gas price check error: {str(e)}")
            return False
    
    async def start(self):
        """Start the trading bot"""
        self.running = True
        self.logger.info("Starting DEX Trading Bot...")
        
        try:
            # Run safety checks
            from safety_checks import SafetyChecker
            checker = SafetyChecker(self.wallet_manager, self)
            status, message = await checker.run_all_checks()
            
            if not status:
                self.logger.error(f"Safety checks failed: {message}")
                self.running = False
                return
                
            self.logger.info("Safety checks passed successfully")
            
            # Perform test trade with minimal amount
            test_status = await self._perform_test_trade()
            if not test_status:
                self.logger.error("Test trade failed - stopping bot for safety")
                self.running = False
                return
                
            self.logger.info("Test trade completed successfully")
            
            # Check wallet balances and top up if needed
            await self._check_wallet_balances()
            
            # Start trading loop
            last_gas_check = 0
            while self.running:
                try:
                    current_time = time.time()
                    
                    # Check gas price periodically
                    if current_time - last_gas_check >= GAS_PRICE_CHECK_INTERVAL:
                        if not await self._check_gas_price():
                            self.logger.info("Waiting for gas price to decrease...")
                            await asyncio.sleep(GAS_PRICE_CHECK_INTERVAL)
                            last_gas_check = current_time
                            continue
                        last_gas_check = current_time
                    
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
    
    async def _perform_test_trade(self) -> bool:
        """Perform a minimal test trade to verify everything works"""
        try:
            # Find wallet with highest balance for test
            wallet = None
            max_balance = 0
            for w in self.wallet_manager.wallets['BNB']:
                balance = Decimal(str(w.get('balance', 0)))
                if balance > max_balance:
                    wallet = w
                    max_balance = balance

            if not wallet:
                self.logger.error("No wallet available for test trade")
                return False

            # Use 1/4 of minimum trade amount for test
            test_amount = TRADE_AMOUNT['min'] / 4
            
            self.logger.info(f"Attempting test trade with {test_amount} BNB...")
            
            # Try to execute a tiny buy order
            tx_hash = await self._execute_swap(
                wallet=wallet,
                token_in='BNB',
                token_out=DEFAULT_TOKEN_ADDRESS,
                amount_in=test_amount,
                slippage=SLIPPAGE
            )

            if not tx_hash:
                self.logger.error("Test buy failed")
                return False

            self.logger.info(f"Test buy successful: {tx_hash}")
            return True

        except Exception as e:
            self.logger.error(f"Test trade error: {str(e)}", exc_info=True)
            return False

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
        
        # Get active wallets with sufficient balance for trade + gas
        active_wallets = []
        for w in self.wallet_manager.wallets['BNB']:
            if not w.get('is_active', True):
                continue
            
            balance = Decimal(str(w.get('balance', 0)))
            if balance >= (MIN_WALLET_BALANCE + GAS_RESERVE):
                # Calculate maximum safe trade amount for this wallet
                max_trade = balance - GAS_RESERVE
                w['max_safe_trade'] = min(float(max_trade), float(TRADE_AMOUNT['max']))
                if w['max_safe_trade'] >= float(TRADE_AMOUNT['min']):
                    active_wallets.append(w)
        
        if not active_wallets:
            self.logger.warning("No active wallets with sufficient balance for buying")
            return
            
        # Select a random wallet
        wallet = random.choice(active_wallets)
        
        # Calculate 50% of wallet balance for maximum trade size
        wallet_balance = Decimal(str(wallet.get('balance', 0)))
        gas_reserve = GAS_RESERVE
        available_balance = wallet_balance - gas_reserve
        
        if available_balance <= 0:
            self.logger.warning(f"Insufficient balance after gas reserve. Balance: {wallet_balance}, Gas Reserve: {gas_reserve}")
            return
            
        # Calculate trade amount as 50% of available balance
        max_trade_amount = available_balance * Decimal('0.5')  # 50% of available balance
        min_trade_amount = max_trade_amount * Decimal('0.7')  # 70% of max trade for randomization
        
        # Determine final trade amount with some randomization
        amount = Decimal(str(random.uniform(
            float(min_trade_amount),
            float(max_trade_amount)
        )))
        
        # Double check we're not using too much
        current_balance = Decimal(str(wallet.get('balance', 0)))
        if (current_balance - Decimal(str(amount))) < GAS_RESERVE:
            self.logger.warning(f"Skipping trade - would leave insufficient gas reserve. Balance: {current_balance}, Trade: {amount}, Gas Reserve: {GAS_RESERVE}")
            return
        
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
                slippage=SLIPPAGE  # decimal, e.g., 0.01 for 1%
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
        slippage: float = 0.5,
        tax_buffer: float = 0.05,  # Default 5% tax buffer for safety
        retry_on_failure: bool = True,
        max_attempts: int = 3,
        check_price_impact: bool = True
    ) -> Optional[str]:
        """Execute a token swap with advanced error handling and dynamic adjustments."""
        base_slippage = slippage
        w3 = self.wallet_manager.w3
        base_gas_price = int(w3.eth.gas_price)
        max_retries = 3
        last_error = None
        
        # Adjust gas limit based on token type and trade size
        estimated_gas = 300000  # Increased from typical 200000 for safety
        last_error = None
        
        for attempt in range(max_retries + 1):
            try:
                # Exponential backoff delay between retries
                if attempt > 0:
                    delay = min(2 ** attempt, 30)  # Max 30 seconds
                    self.logger.info(f"Waiting {delay}s before retry {attempt}")
                    await asyncio.sleep(delay)
                
                # Dynamic adjustments increase more aggressively with each retry
                gas_price = int(base_gas_price * (1 + 0.3 * attempt))  # 30% increase per retry
                slippage_adj = min(base_slippage * (1 + attempt), 0.30)  # More aggressive slippage scaling, max 30%
                
                # Always fetch latest nonce and gas price
                nonce = w3.eth.get_transaction_count(wallet['address'], 'pending')
                current_gas = w3.eth.gas_price
                if current_gas > base_gas_price:
                    gas_price = max(gas_price, int(current_gas * 1.1))  # At least 10% above current

                # Fetch a fresh quote for this attempt
                quote = await self.wallet_manager.get_swap_quote(
                    token_in=token_in,
                    token_out=token_out,
                    amount_in=amount_in
                )
                if not quote or 'amount_out' not in quote:
                    self.logger.error(f"Failed to fetch quote for swap attempt {attempt}")
                    if attempt < max_retries:
                        continue
                    else:
                        raise Exception("Failed to get valid quote after max retries")

                quoted_out = Decimal(str(quote['amount_out']))
                
                # Price impact protection
                if 'price_impact' in quote and float(quote['price_impact']) > 0.15:  # 15% impact
                    self.logger.warning(f"High price impact detected: {quote['price_impact']}. Skipping trade.")
                    return None
                
                # Calculate amountOutMin with dynamic adjustments
                total_buffer = slippage_adj + tax_buffer
                if attempt > 0:  # Add extra safety margin on retries
                    total_buffer += 0.02 * attempt  # Additional 2% per retry
                
                min_out = quoted_out * (1 - total_buffer)
                min_out = int(min_out)
                
                self.logger.info(
                    f"[Attempt {attempt}] Swap Details:\n"
                    f"  Gas Price: {w3.from_wei(gas_price, 'gwei')} gwei\n"
                    f"  Slippage: {slippage_adj * 100}%\n"
                    f"  Tax Buffer: {tax_buffer * 100}%\n"
                    f"  Amount In: {amount_in}\n"
                    f"  Quoted Out: {quoted_out}\n"
                    f"  Min Out: {min_out}\n"
                    f"  Total Buffer: {total_buffer * 100}%"
                )

                # Prepare the transaction with optimized parameters
                tx = await self.wallet_manager.prepare_transaction(
                    wallet=wallet,
                    token_in=token_in,
                    token_out=token_out,
                    amount_in=amount_in,
                    slippage=slippage_adj,
                    gas_price=gas_price,
                    nonce=nonce,
                    amount_out_min=min_out
                )
                if not tx:
                    self.logger.error("Failed to prepare transaction")
                    continue
                signed_tx = w3.eth.account.sign_transaction(
                    tx, 
                    private_key=wallet['private_key']
                )
                tx_hash = w3.eth.send_raw_transaction(
                    signed_tx.raw_transaction
                ).hex()
                receipt = await w3.eth.wait_for_transaction_receipt(tx_hash)
                if receipt.status == 1:
                    self.logger.info(f"Transaction successful: {tx_hash}")
                    return tx_hash
                else:
                    self.logger.error(f"Transaction failed: {tx_hash}")
                    continue
            except Exception as e:
                last_error = str(e)
                self.logger.error(
                    f"Swap attempt {attempt} failed:\n"
                    f"  Error: {last_error}\n"
                    f"  Slippage: {slippage_adj * 100}%\n"
                    f"  Gas Price: {w3.from_wei(gas_price, 'gwei')} gwei",
                    exc_info=True
                )
                
                # Specific handling for INSUFFICIENT_OUTPUT_AMOUNT
                if "INSUFFICIENT_OUTPUT_AMOUNT" in last_error:
                    self.logger.info("Detected INSUFFICIENT_OUTPUT_AMOUNT error - attempting recovery")
                    
                    try:
                        # Get fresh quote to check price movement
                        new_quote = await self.wallet_manager.get_swap_quote(
                            token_in=token_in,
                            token_out=token_out,
                            amount_in=amount_in
                        )
                        
                        if new_quote and 'amount_out' in new_quote:
                            price_change = (Decimal(str(new_quote['amount_out'])) / quoted_out) - 1
                            
                            if abs(price_change) > 0.05:  # 5% price movement
                                self.logger.warning(f"Significant price movement detected: {price_change*100:.2f}%")
                                # Update quote and adjust slippage for next attempt
                                quoted_out = Decimal(str(new_quote['amount_out']))
                                slippage_adj = min(base_slippage * (1 + abs(price_change) * 3), 0.15)
                            
                            # Check if trade size might be too large
                            if 'price_impact' in new_quote and new_quote['price_impact'] > 0.05:
                                self.logger.warning("High price impact detected - considering reducing trade size")
                                if attempt == max_retries - 1:  # On last retry
                                    # Try with 75% of original amount
                                    amount_in = amount_in * Decimal('0.75')
                                    self.logger.info(f"Reducing trade size to {amount_in} for final attempt")
                    except Exception as quote_error:
                        self.logger.error(f"Error during recovery: {str(quote_error)}")
                    
                    # Add exponential backoff delay
                    delay = 2 ** attempt
                    self.logger.info(f"Waiting {delay} seconds before retry")
                    await asyncio.sleep(delay)
                    
                elif "execution reverted" in last_error.lower():
                    # Add a longer delay for reverted transactions
                    await asyncio.sleep(5)
                
                if attempt == max_retries:
                    self.logger.error(
                        f"Failed to execute swap after {max_retries} attempts.\n"
                        f"Final error: {last_error}"
                    )
                    return None
                
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
