import os
import json
import logging
import traceback
import asyncio
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from dotenv import load_dotenv
import nest_asyncio
from web3 import Web3, HTTPProvider
from trading_cycle import TradingCycle

# Apply nest_asyncio to allow the Telegram bot's async event loop
# to coexist with other async operations.
nest_asyncio.apply()

class TokenTrendingBot:
    def __init__(self):
        """Initializes the bot, loads configuration, and sets up handlers."""
        load_dotenv()
        self.token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not self.token:
            raise ValueError("TELEGRAM_BOT_TOKEN environment variable not set.")
        
        self.app = Application.builder().token(self.token).build()

        # This will hold the loaded wallets for the session
        self.wallets = []
        self.trading_cycle = None
        
        # Configure logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)
        
        # Initialize Web3 with multiple fallback RPC endpoints
        bsc_rpc_urls = [
            'https://bsc-dataseed1.defibit.io/',  # Moved most reliable first
            'https://bsc-dataseed2.defibit.io/',
            'https://bsc-dataseed3.defibit.io/',
            'https://bsc-dataseed4.defibit.io/',
            'https://bsc-dataseed1.ninicoin.io/',
            'https://bsc-dataseed2.ninicoin.io/',
            'https://bsc-dataseed3.ninicoin.io/',
            'https://bsc-dataseed4.ninicoin.io/',
            'https://bsc-dataseed.binance.org/',  # Moved default to end as it's often rate-limited
        ]
        
        self.web3 = None
        for url in bsc_rpc_urls:
            try:
                self.logger.info(f"Attempting to connect to: {url}")
                # Add headers to mimic browser request
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                }
                provider = HTTPProvider(
                    url, 
                    request_kwargs={
                        'timeout': 15,  # Increased timeout
                        'headers': headers
                    }
                )
                web3 = Web3(provider)
                
                # Test connection with a simple call
                block = web3.eth.block_number
                if not isinstance(block, int) or block < 0:
                    raise ValueError("Invalid block number received")
                    
                self.logger.info(f"Successfully connected to BSC node. Current block: {block}")
                self.web3 = web3
                self.logger.info(f"Successfully connected to BSC node: {url}")
                
                # Test a transaction call to ensure full functionality
                try:
                    gas_price = web3.eth.gas_price
                    if not isinstance(gas_price, int) or gas_price <= 0:
                        raise ValueError("Invalid gas price received")
                    self.logger.info(f"Node fully operational. Current gas price: {web3.from_wei(gas_price, 'gwei')} Gwei")
                except Exception as e:
                    self.logger.warning(f"Node partially functional: {str(e)}")
                    
                break
            except Exception as e:
                self.logger.error(f"Failed to connect to {url}: {str(e)}", exc_info=True)
                continue
        
        # If all connection attempts failed
        if not self.web3:
            # Try a direct connection test using requests as a fallback
            try:
                import requests
                test_url = 'https://bsc-dataseed.binance.org/'
                self.logger.info(f"Testing direct connection to {test_url}")
                response = requests.get(test_url, timeout=10)
                if response.status_code == 200:
                    self.logger.info("Direct connection test successful, but Web3 connection failed")
                else:
                    self.logger.error(f"Direct connection failed with status {response.status_code}")
            except Exception as e:
                self.logger.error(f"Direct connection test also failed: {str(e)}")
                
            error_msg = (
                "❌ Failed to connect to any BSC node. This could be due to:\n"
                "1. No internet connection\n"
                "2. Firewall blocking the connection\n"
                "3. RPC nodes being temporarily unavailable\n\n"
                "Troubleshooting steps:\n"
                "1. Check your internet connection\n"
                "2. Try using a VPN if you're in a restricted network\n"
                "3. Try again in a few minutes\n"
                "4. Set a custom RPC URL in your .env file:\n"
                "BSC_MAINNET_RPC=your_rpc_url_here"
            )
            self.logger.error("All BSC node connection attempts failed")
            raise ValueError(error_msg)
            
        self.logger.info("BSC node connection established successfully")

        # Initialize token configuration
        self.token_config = {
            'address': '',
            'chain': '',
            'ticker': ''
        }
        self.load_token_config()

        # Register command handlers
        self.app.add_handler(CommandHandler("start", self.start))
        self.app.add_handler(CommandHandler("menu", self.menu))
        self.app.add_handler(CommandHandler("create_wallet", self.create_wallet))
        self.app.add_handler(CommandHandler("connect", self.connect_token))
        self.app.add_handler(CommandHandler("fund_wallet", self.fund_wallet))
        self.app.add_handler(CommandHandler("check_balance", self.check_balance))
        self.app.add_handler(CommandHandler("cancel", self.cancel))
        self.app.add_handler(CommandHandler("start_trend", self.start_trend))
        self.app.add_handler(CommandHandler("stop_trend", self.stop_trend))
        
        # Add message handler for interactive commands
        self.app.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            self.handle_interactive_messages
        ))
        
        # Initialize central wallet with validation
        self.central_wallet_address = os.getenv('CENTRAL_WALLET_ADDRESS', '').strip()
        self.central_wallet_private_key = os.getenv('CENTRAL_WALLET_PRIVATE_KEY', '').strip()
        
        if not self.central_wallet_address or not self.central_wallet_private_key:
            self.logger.warning("Central wallet not fully configured. Wallet funding will not work.")
        elif self.web3 and self.central_wallet_address:
            try:
                # Validate wallet address format
                if not self.web3.is_address(self.central_wallet_address):
                    self.logger.error(f"Invalid wallet address format: {self.central_wallet_address}")
                    self.central_wallet_address = ''
                else:
                    # Convert to checksum address
                    self.central_wallet_address = self.web3.to_checksum_address(self.central_wallet_address)
                    self.logger.info(f"Central wallet configured: {self.central_wallet_address}")
            except Exception as e:
                self.logger.error(f"Error validating central wallet: {str(e)}")
                self.central_wallet_address = ''

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Sends a welcome message when the /start command is issued."""
        await update.message.reply_text(
            "Welcome to the DexScreener Trending Bot!\n"
            "Use /menu to see available commands."
        )

    def load_token_config(self):
        """Load token configuration from file if it exists."""
        try:
            config_path = os.path.join(os.path.dirname(__file__), 'token_config.json')
            if os.path.exists(config_path):
                with open(config_path, 'r') as f:
                    self.token_config = json.load(f)
        except Exception as e:
            logging.error(f"Error loading token config: {e}")

    def save_token_config(self):
        """Save token configuration to file."""
        try:
            config_path = os.path.join(os.path.dirname(__file__), 'token_config.json')
            with open(config_path, 'w') as f:
                json.dump(self.token_config, f, indent=4)
        except Exception as e:
            logging.error(f"Error saving token config: {e}")

    async def menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Displays the main menu of commands."""
        menu_text = (
            "📋 *Available Commands*\n\n"
            "🔹 /connect - Connect a token (format: `0x... CHAIN $TICKER`)\n"
            "🔹 /create_wallet - Load wallets from wallets.json\n"
            f"🔹 {'⏸️' if self.trading_cycle and self.trading_cycle.is_running else '▶️'} /{'stop' if self.trading_cycle and self.trading_cycle.is_running else 'start'}_trend - {'Stop' if self.trading_cycle and self.trading_cycle.is_running else 'Start'} trending mode\n"
            "🔹 /menu - Display this menu\n\n"
            "*Current Status*:\n"
        )
        
        if self.token_config['address']:
            menu_text += (
                f"• Token: `{self.token_config['address']}`\n"
                f"• Chain: {self.token_config['chain']}\n"
                f"• Ticker: {self.token_config['ticker']}\n"
            )
            
            if self.trading_cycle:
                status = "🟢 RUNNING" if self.trading_cycle.is_running else "🔴 STOPPED"
                menu_text += f"• Trading Cycle: {status} (Cycle: {self.trading_cycle.current_cycle})\n"
            
            if self.wallets:
                group_a = len(self.wallets[:17])
                group_b = len(self.wallets[17:34])
                menu_text += f"• Wallets: {len(self.wallets)} total ({group_a} in Group A, {group_b} in Group B)"
        else:
            menu_text += "No token connected. Use /connect to add one."
        
        await update.message.reply_text(menu_text, parse_mode='Markdown')

    async def connect_token(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start the token connection process."""
        await update.message.reply_text(
            "🔗 Please enter the token details in this format (single line):\n"
            "`<tokenaddress> <chain> <ticker>`\n\n"
            "Example:\n"
            "`0x123...abc BNB TOKEN`\n\n"
            "Type /cancel to cancel.",
            parse_mode='Markdown'
        )
        context.user_data['awaiting_token_info'] = 'all'
        
    async def handle_interactive_messages(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle all interactive messages based on current state."""
        if 'awaiting_token_info' in context.user_data:
            await self.handle_token_info(update, context)
        elif 'awaiting_funding_confirmation' in context.user_data:
            await self.handle_funding_confirmation(update, context)

    async def handle_token_info(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle the token information collection process."""
        if 'awaiting_token_info' not in context.user_data:
            return
            
        user_input = update.message.text.strip()
        
        if context.user_data['awaiting_token_info'] == 'all':
            # Parse the input: tokenaddress chain ticker
            parts = user_input.split()
            if len(parts) < 3:
                await update.message.reply_text(
                    "❌ Invalid format. Please enter: `<tokenaddress> <chain> <ticker>`\n"
                    "Example: `0x123...abc BNB TOKEN`",
                    parse_mode='Markdown'
                )
                return
                
            token_address = parts[0]
            chain = parts[1].upper()
            ticker = parts[2].upper()
            
            # Validate token address
            if not token_address.startswith('0x') or len(token_address) != 42:
                await update.message.reply_text(
                    "❌ Invalid token address. Must start with 0x and be 42 characters long."
                    "\n\nPlease try again with format: `<tokenaddress> <chain> <ticker>`",
                    parse_mode='Markdown'
                )
                return
                
            # Validate chain
            if chain not in ['BNB', 'ETH', 'SOL']:
                await update.message.reply_text(
                    "❌ Invalid chain. Please use BNB, ETH, or SOL."
                    "\n\nPlease try again with format: `<tokenaddress> <chain> <ticker>`",
                    parse_mode='Markdown'
                )
                return
                
            # Save token info
            self.token_config = {
                'address': token_address,
                'chain': chain,
                'ticker': ticker
            }
            self.save_token_config()
            
            # Clear the state
            del context.user_data['awaiting_token_info']
            
            # Initialize trading cycle if not already done
            if not hasattr(self, 'trading_cycle') or not self.trading_cycle:
                self.trading_cycle = TradingCycle(
                    wallet_manager=self,
                    web3=self.web3,
                    token_address=token_address
                )
            else:
                self.trading_cycle.token_address = token_address
                
            # Format the response
            response = f"{token_address}    {chain}    {ticker}"
            
            # Send the response in the requested format
            await update.message.reply_text(
                f"✅ Token Connected Successfully!\n\n"
                f"{response}\n\n"
                "You can now use /start_trend to begin the trading cycle.",
                parse_mode=None  # Keep Markdown disabled
            )
            
            # Show the updated menu
            await self.menu(update, context)
            
    def get_web3_instance(self):
        """Get a working Web3 instance with fallback RPC endpoints."""
        rpc_endpoints = [
            'https://bsc-dataseed1.defibit.io',
            'https://bsc-dataseed2.defibit.io',
            'https://bsc-dataseed1.binance.org',
            'https://bsc-dataseed2.binance.org',
            'https://bsc-dataseed3.binance.org',
            'https://bsc-dataseed4.binance.org',
            'https://bsc-dataseed.binance.org',
            'https://bsc-dataseed1.ninicoin.io',
            'https://bsc-dataseed2.ninicoin.io',
            'https://bsc.nodereal.io',
        ]
        
        for endpoint in rpc_endpoints:
            try:
                provider = HTTPProvider(endpoint, request_kwargs={
                    'timeout': 10,
                    'proxies': {
                        'http': '',
                        'https': ''
                    }
                })
                w3 = Web3(provider)
                if w3.is_connected():
                    return w3
            except Exception as e:
                self.logger.warning(f"Failed to connect to {endpoint}: {str(e)}")
                continue
                
        raise Exception("Could not connect to any BSC RPC endpoint")

    async def get_bnb_price(self):
        """Fetch the current BNB price in USD with multiple fallbacks."""
        # Try Binance first
        try:
            url = "https://api.binance.com/api/v3/ticker/price?symbol=BNBUSDT"
            response = requests.get(url, timeout=10, 
                                 headers={'User-Agent': 'Mozilla/5.0'},
                                 verify=True)
            response.raise_for_status()
            data = response.json()
            return float(data['price'])
        except Exception as e:
            self.logger.warning(f"Failed to fetch BNB price from Binance: {str(e)}")
            
        # Fallback to CoinGecko
        try:
            url = "https://api.coingecko.com/api/v3/simple/price?ids=binancecoin&vs_currencies=usd"
            response = requests.get(url, timeout=10,
                                 headers={'User-Agent': 'Mozilla/5.0'},
                                 verify=True)
            response.raise_for_status()
            data = response.json()
            return float(data['binancecoin']['usd'])
        except Exception as e:
            self.logger.warning(f"Failed to fetch BNB price from CoinGecko: {str(e)}")
            
        # Fallback to PancakeSwap API
        try:
            url = "https://api.pancakeswap.info/api/v2/tokens/0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"  # WBNB token
            response = requests.get(url, timeout=10,
                                 headers={'User-Agent': 'Mozilla/5.0'},
                                 verify=True)
            response.raise_for_status()
            data = response.json()
            return float(data['data']['price'])
        except Exception as e:
            self.logger.warning(f"Failed to fetch BNB price from PancakeSwap: {str(e)}")
            
        # Final fallback to hardcoded price
        self.logger.warning("Using hardcoded BNB price as fallback")
        return 300.0  # Conservative estimate if all APIs fail

    async def fund_wallet(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start the wallet funding process with dynamic BNB/USD calculation."""
        if not hasattr(self, 'central_wallet_address') or not self.central_wallet_address:
            await update.message.reply_text(
                "❌ Central wallet not configured. "
                "Please set CENTRAL_WALLET_ADDRESS and CENTRAL_WALLET_PRIVATE_KEY in your .env file."
            )
            return
            
        # Get current BNB price
        try:
            bnb_price = await self.get_bnb_price()
            if not bnb_price:
                raise ValueError("Could not fetch BNB price")
                
            # Get central wallet balance
            balance_wei = self.web3.eth.get_balance(self.central_wallet_address)
            balance_bnb = self.web3.from_wei(balance_wei, 'ether')
            balance_usd = float(balance_bnb) * bnb_price
            
            # Calculate how many wallets we can fund
            min_required_usd = 2.00  # $1 per wallet + $1 reserve
            if balance_usd < min_required_usd:
                needed_usd = min_required_usd - balance_usd
                needed_bnb = needed_usd / bnb_price
                
                # Create a clickable link for BSCScan
                bsc_scan_link = f"https://bscscan.com/address/{self.central_wallet_address}"
                
                # Format the message with proper escaping for MarkdownV2
                # Escape all special MarkdownV2 characters in dynamic content
                min_required_escaped = str(min_required_usd).replace('.', '\.')
                balance_usd_escaped = str(round(balance_usd, 2)).replace('.', '\.')
                balance_bnb_escaped = str(round(balance_bnb, 6)).replace('.', '\.')
                bnb_price_escaped = str(round(bnb_price, 4)).replace('.', '\.')
                needed_usd_escaped = str(round(needed_usd, 2)).replace('.', '\.')
                
                message = (
                    f"❌ *Need at least ${min_required_escaped} to fund 1 wallet*\n"
                    f"• *Current*: ${balance_usd_escaped} \\(`{balance_bnb_escaped} BNB\\)\n"
                    f"• *BNB Price*: \${bnb_price_escaped}\n\n"
                    f"Send at least *\${needed_usd_escaped}* more to\:\n"
                    f"```\n{self.central_wallet_address}\n```\n"
                    f"[View on BSCScan]({bsc_scan_link})\n\n"
                    "After sending, use /check\\_balance to verify the transaction\."
                )
                
                # Send the message with MarkdownV2 parse mode
                try:
                    await update.message.reply_text(
                        message,
                        parse_mode='MarkdownV2',
                        disable_web_page_preview=True
                    )
                except Exception as e:
                    self.logger.error(f"Error sending message: {str(e)}")
                    # Fallback to plain text if Markdown parsing fails
                    plain_message = (
                        f"❌ Need at least ${min_required_usd:.2f} to fund 1 wallet\n"
                        f"• Current: ${balance_usd:.2f} ({balance_bnb:.6f} BNB)\n"
                        f"• BNB Price: ${bnb_price:.4f}\n\n"
                        f"Send at least ${needed_usd:.2f} more to:\n"
                        f"{self.central_wallet_address}\n\n"
                        f"View on BSCScan: {bsc_scan_link}\n\n"
                        "After sending, use /check_balance to verify the transaction."
                    )
                    await update.message.reply_text(plain_message)
                return
                
            # Calculate how many wallets we can fund
            max_wallets = int((balance_usd - 1.00) / 1.00)  # Leave $1 for gas
            max_wallets = min(max_wallets, 34)  # Max 34 wallets
            
            bnb_per_wallet = 1.00 / bnb_price  # $1 worth of BNB per wallet
            total_bnb = bnb_per_wallet * max_wallets
            
            funding_details = {
                'max_wallets': max_wallets,
                'bnb_per_wallet': bnb_per_wallet,
                'total_bnb': total_bnb,
                'bnb_price': bnb_price
            }
            
            context.user_data['funding_details'] = funding_details
            context.user_data['awaiting_funding_confirmation'] = True
            
            await update.message.reply_text(
                f"💰 *Funding Plan*\n"
                f"• Wallets to fund: {max_wallets}\n"
                f"• BNB per wallet: {bnb_per_wallet:.6f} (${1.00:.2f})\n"
                f"• Total BNB needed: {total_bnb:.6f}\n"
                f"• Current BNB price: ${bnb_price:.2f}\n\n"
                f"Type `confirm` to proceed or /cancel to cancel.",
                parse_mode='Markdown'
            )
            
        except Exception as e:
            self.logger.error(f"Error in fund_wallet: {str(e)}", exc_info=True)
            await update.message.reply_text(
                "❌ An error occurred while checking balances. Please try again later."
            )

    async def handle_funding_confirmation(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle funding confirmation from user."""
        user_input = update.message.text.strip().lower()
        
        if user_input != 'confirm':
            await update.message.reply_text(
                "Please type `confirm` to proceed with funding, "
                "or /cancel to abort the funding process.",
                parse_mode='Markdown'
            )
            return
            
        # Get funding details
        funding_details = context.user_data.get('funding_details', {})
        if not funding_details:
            await update.message.reply_text("❌ Error: Missing funding details. Please try /fund_wallet again.")
            return
            
        # Clear the funding confirmation flag
        context.user_data.pop('awaiting_funding_confirmation', None)
        
        # Start the funding process
        await self.distribute_funds(update, context, funding_details)
    
    async def distribute_funds(self, update: Update, context: ContextTypes.DEFAULT_TYPE, funding_details: dict):
        """Distribute funds from central wallet to trading wallets based on available balance."""
        if not self.wallets:
            await update.message.reply_text("❌ No wallets found to fund.")
            return
            
        if not self.central_wallet_private_key or not self.central_wallet_address:
            await update.message.reply_text("❌ Central wallet not configured.")
            return
            
        bnb_per_wallet = funding_details['bnb_per_wallet']
        max_wallets = funding_details['max_wallets']
        
        try:
            # Recheck balance in case it changed
            balance_wei = self.web3.eth.get_balance(self.central_wallet_address)
            balance_bnb = float(self.web3.from_wei(balance_wei, 'ether'))
            
            # Adjust if balance changed since plan was made
            min_balance_needed = funding_details['total_bnb'] + (1.0 / funding_details['bnb_price'])
            if balance_bnb < min_balance_needed:
                adjusted_wallets = int((balance_bnb - (1.0 / funding_details['bnb_price'])) / bnb_per_wallet)
                if adjusted_wallets < 1:
                    await update.message.reply_text(
                        f"❌ Insufficient balance. Need at least ${1 + (1.0 / funding_details['bnb_price']):.2f} "
                        f"worth of BNB to fund 1 wallet.\n\n"
                        f"Send more BNB to:\n`{self.central_wallet_address}`"
                    )
                    return
                
                max_wallets = adjusted_wallets
                funding_details['max_wallets'] = max_wallets
                
                await update.message.reply_text(
                    f"⚠️ Balance decreased. Will now fund {max_wallets} wallets."
                )
                
            # Start funding process
            status_msg = await update.message.reply_text("🔄 Starting to fund wallets...")
            success_count = 0
            funded_wallets = []
            
            for i, wallet in enumerate(self.wallets[:max_wallets], 1):
                try:
                    # Check if we still have enough balance (in case of network fees)
                    current_balance = float(self.web3.from_wei(
                        self.web3.eth.get_balance(self.central_wallet_address), 'ether'
                    ))
                    
                    if current_balance < bnb_per_wallet * 1.1:  # Include 10% buffer for gas
                        self.logger.warning(f"Insufficient balance to continue funding. Stopping at {i-1} wallets.")
                        break
                    
                    # Prepare transaction
                    nonce = self.web3.eth.get_transaction_count(self.central_wallet_address)
                    
                    # Calculate gas cost
                    gas_price = self.web3.eth.gas_price
                    gas_limit = 21000  # Standard transfer gas limit
                    gas_cost = self.web3.from_wei(gas_price * gas_limit, 'ether')
                    
                    # Amount to send (BNB per wallet minus gas cost)
                    send_amount = bnb_per_wallet - float(gas_cost)
                    
                    if send_amount <= 0:
                        self.logger.error(f"Gas cost {gas_cost} exceeds send amount {bnb_per_wallet}")
                        continue
                    
                    tx = {
                        'nonce': nonce,
                        'to': wallet['address'],
                        'value': self.web3.to_wei(send_amount, 'ether'),
                        'gas': gas_limit,
                        'gasPrice': gas_price,
                        'chainId': 56  # BSC Mainnet
                    }
                    
                    # Sign and send transaction
                    signed_tx = self.web3.eth.account.sign_transaction(tx, self.central_wallet_private_key)
                    tx_hash = self.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
                    tx_receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash)
                    
                    if tx_receipt.status == 1:
                        success_count += 1
                        funded_wallets.append(wallet['address'])
                        self.logger.info(f"Successfully funded {wallet['address']} with {send_amount:.6f} BNB")
                        
                        # Update status message every 2 wallets
                        if success_count % 2 == 0 or i == max_wallets:
                            await status_msg.edit_text(
                                f"🔄 Funding in progress... {success_count}/{max_wallets} wallets funded\n"
                                f"• Current balance: {current_balance:.6f} BNB\n"
                                f"• Last funded: {wallet['address'][:10]}...{wallet['address'][-6:]}"
                            )
                    
                except Exception as e:
                    self.logger.error(f"Error funding wallet {wallet['address']}: {str(e)}"
                                   f"\n{traceback.format_exc()}")
            
            # Update list of funded wallets for trading
            self.funded_wallets = funded_wallets
            
            # Save funded wallets to config
            self.token_config['funded_wallets'] = self.funded_wallets
            with open('token_config.json', 'w') as f:
                json.dump(self.token_config, f, indent=2)
            
            # Final status update
            final_balance = float(self.web3.from_wei(
                self.web3.eth.get_balance(self.central_wallet_address), 'ether'
            ))
            
            await status_msg.edit_text(
                f"✅ Funding complete!\n\n"
                f"• Successfully funded: {success_count}/{max_wallets} wallets\n"
                f"• Amount per wallet: {bnb_per_wallet:.6f} BNB (≈$1.00)\n"
                f"• Total distributed: {success_count * bnb_per_wallet:.6f} BNB\n"
                f"• Remaining balance: {final_balance:.6f} BNB\n\n"
                f"These wallets are now ready for trading with /start_trend"
            )
            
        except Exception as e:
            self.logger.error(f"Error in distribute_funds: {str(e)}\n{traceback.format_exc()}")
            await update.message.reply_text(
                f"❌ Error during funding process: {str(e)}\n\n"
                "Please check the logs and try again."
            )
    
    async def check_balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Check the balance of the central wallet and all trading wallets."""
        if not hasattr(self, 'central_wallet_address') or not self.central_wallet_address:
            await update.message.reply_text("❌ Central wallet not configured.")
            return
            
        try:
            # Show typing action
            await update.message.reply_chat_action('typing')
            
            # Get BNB price
            bnb_price = await self.get_bnb_price()
            if not bnb_price:
                raise ValueError("Could not fetch BNB price")
                
            # Check central wallet balance
            balance_wei = self.web3.eth.get_balance(self.central_wallet_address)
            balance_bnb = self.web3.from_wei(balance_wei, 'ether')
            balance_usd = float(balance_bnb) * bnb_price
            
            # Format the response
            response = (
                "💰 *Wallet Balances*\n\n"
                f"*Central Wallet* (`{self.central_wallet_address[:6]}...{self.central_wallet_address[-4:]}`):\n"
                f"• {balance_bnb:.6f} BNB (${balance_usd:.2f})\n"
            )
            
            # Add BSCScan link for central wallet
            bsc_scan_link = f"https://bscscan.com/address/{self.central_wallet_address}"
            response += f"• [View on BSCScan]({bsc_scan_link})\n\n"
            
            # Check trading wallets if they exist
            if hasattr(self, 'wallets') and self.wallets:
                response += "*Trading Wallets:*\n"
                total_balance_bnb = 0
                
                for i, wallet in enumerate(self.wallets, 1):
                    try:
                        wallet_balance_wei = self.web3.eth.get_balance(wallet['address'])
                        wallet_balance_bnb = self.web3.from_wei(wallet_balance_wei, 'ether')
                        wallet_balance_usd = float(wallet_balance_bnb) * bnb_price
                        total_balance_bnb += float(wallet_balance_bnb)
                        
                        wallet_link = f"https://bscscan.com/address/{wallet['address']}"
                        response += (
                            f"{i}. `{wallet['address'][:6]}...{wallet['address'][-4:]}`\n"
                            f"   • Balance: {wallet_balance_bnb:.6f} BNB (${wallet_balance_usd:.2f})\n"
                            f"   • [View on BSCScan]({wallet_link})\n\n"
                        )
                    except Exception as e:
                        self.logger.error(f"Error checking wallet {wallet['address']}: {str(e)}")
                        response += f"{i}. `{wallet['address'][:6]}...`: Error checking balance\n\n"
                
                # Add total
                total_balance_usd = total_balance_bnb * bnb_price
                response += (
                    f"*Total Trading Wallets Balance:*\n"
                    f"• {total_balance_bnb:.6f} BNB (${total_balance_usd:.2f})\n"
                )
            
            # Add refresh button
            response += "\n🔄 Use /check_balance to refresh"
            
            await update.message.reply_text(
                response,
                parse_mode='Markdown',
                disable_web_page_preview=True
            )
        except Exception as e:
            self.logger.error(f"Error in check_balance: {str(e)}")
            await update.message.reply_text(
                "❌ An error occurred while checking balances. Please try again later."
            )
    
    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Cancel any ongoing operation."""
        cancelled = False
        
        # Cancel token connection if in progress
        if 'awaiting_token_info' in context.user_data:
            for key in ['awaiting_token_info', 'token_address', 'chain']:
                if key in context.user_data:
                    del context.user_data[key]
            await update.message.reply_text("❌ Token connection cancelled. Use /connect to start over.")
            cancelled = True
            
        # Cancel funding process if in progress
        if 'awaiting_funding_confirmation' in context.user_data:
            for key in ['awaiting_funding_confirmation', 'funding_amount', 'funding_gas_price']:
                if key in context.user_data:
                    del context.user_data[key]
            await update.message.reply_text("❌ Wallet funding cancelled. Use /fund_wallet to start over.")
            cancelled = True
            
        if not cancelled:
            await update.message.reply_text("❌ No operation to cancel.")
            
        # Save the token configuration
        self.token_config = {
            'address': token_address,
            'chain': chain.upper(),
            'ticker': ticker.upper()
        }
        self.save_token_config()
        
        # Update the .env file with the new token address
        env_path = os.path.join(os.path.dirname(__file__), '.env')
        if os.path.exists(env_path):
            with open(env_path, 'r') as f:
                lines = f.readlines()
                
            # Update or add DEFAULT_TOKEN_ADDRESS
            updated = False
            for i, line in enumerate(lines):
                if line.startswith('DEFAULT_TOKEN_ADDRESS'):
                    lines[i] = f'DEFAULT_TOKEN_ADDRESS={token_address}\n'
            if not updated:
                lines.append(f'DEFAULT_TOKEN_ADDRESS={token_address}\n')
                
            with open(env_path, 'w') as f:
                f.writelines(lines)
        
        await update.message.reply_text(
            f"✅ *Token Connected Successfully!*\n\n"
            f"• *Address:* `{token_address}`\n"
            f"• *Chain:* {chain.upper()}\n"
            f"• *Ticker:* {ticker.upper()}",
            parse_mode='Markdown'
        )
        
        # Initialize trading cycle if not already done
        if not hasattr(self, 'trading_cycle') or not self.trading_cycle:
            self.trading_cycle = TradingCycle(
                wallet_manager=self,
                web3=self.web3,
                token_address=token_address
            )
            
    async def start_trend(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start the trending trading cycle"""
        if not self.wallets:
            await update.message.reply_text(
                "❌ No wallets loaded. Please use /create_wallet first."
            )
            return
            
        if not self.token_config.get('address'):
            await update.message.reply_text(
                "❌ No token connected. Please use /connect first."
            )
            return
            
        # Initialize trading cycle if not already done
        if not hasattr(self, 'trading_cycle') or not self.trading_cycle:
            self.trading_cycle = TradingCycle(
                wallet_manager=self,
                web3=self.web3,
                token_address=self.token_config['address']
            )
        
        # Set the wallets for the trading cycle
        self.trading_cycle.wallets = self.wallets
        
        # Start the trading cycle
        result = await self.trading_cycle.start()
        await update.message.reply_text(result)
        
        # Update the menu to show the new status
        await self.menu(update, context)
        
    async def stop_trend(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Stop the trending trading cycle"""
        if not hasattr(self, 'trading_cycle') or not self.trading_cycle:
            await update.message.reply_text("❌ No active trading cycle to stop.")
            return
            
        result = await self.trading_cycle.stop()
        await update.message.reply_text(result)
        
        # Update the menu to show the new status
        await self.menu(update, context)

    async def create_wallet(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Reads pre-generated wallets from wallets.json and displays them."""
        await update.message.reply_text("🔎 Checking for pre-generated wallets...")
        try:
            # Construct the absolute path to wallets.json relative to this script
            bot_dir = os.path.dirname(os.path.abspath(__file__))
            wallets_file_path = os.path.join(bot_dir, '..', 'hardhat-scripts', 'wallets.json')

            if not os.path.exists(wallets_file_path):
                await update.message.reply_text(
                    "❌ [wallets.json](cci:7://file:///c:/Users/User/bot1/hardhat-scripts/wallets.json:0:0-0:0) not found.\\n\\n"
                    "Please run the wallet generation script manually in your terminal first."
                )
                return

            with open(wallets_file_path, 'r') as f:
                wallets = json.load(f)
            
            self.wallets = wallets
            addresses = [w.get('address') for w in wallets if w.get('address')]

            if not addresses:
                await update.message.reply_text("⚠️ [wallets.json](cci:7://file:///c:/Users/User/bot1/hardhat-scripts/wallets.json:0:0-0:0) is empty or malformed. Please run the generation script again.")
                return

            response = f"✅ Found {len(addresses)} pre-generated wallets.\n\n"
            response += "\n".join(addresses)
            
            # Split the message into chunks of 4000 characters to avoid hitting Telegram's message length limit
            chunk_size = 4000
            for i in range(0, len(response), chunk_size):
                chunk = response[i:i + chunk_size]
                await update.message.reply_text(chunk)

        except json.JSONDecodeError:
            await update.message.reply_text("❌ Error reading [wallets.json](cci:7://file:///c:/Users/User/bot1/hardhat-scripts/wallets.json:0:0-0:0). The file is corrupted or empty. Please run the generation script again.")
        except Exception as e:
            tb_str = traceback.format_exc()
            logging.error(f"Critical error in create_wallet: {tb_str}")
            await update.message.reply_text(f"An unexpected critical error occurred. Please check the logs.")

    def run(self):
        """Starts the bot."""
        logging.basicConfig(
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            level=logging.INFO
        )
        logging.info("Bot starting...")
        self.app.run_polling()

if __name__ == '__main__':
    bot = TokenTrendingBot()
    bot.run()