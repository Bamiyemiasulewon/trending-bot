import os
import json
import logging
import traceback
import asyncio
import time
import requests
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.request import HTTPXRequest
from dotenv import load_dotenv
from web3 import Web3, HTTPProvider
from trading_cycle import TradingCycle

 

class TokenTrendingBot:
    def __init__(self):
        """Initializes the bot, loads configuration, and sets up handlers."""
        # Load environment variables from project root .env file
        env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
        if os.path.exists(env_path):
            load_dotenv(env_path)
        else:
            load_dotenv()  # Fallback to default .env location
            
        self.token = os.getenv("TELEGRAM_TOKEN")
        if not self.token:
            raise ValueError("TELEGRAM_TOKEN environment variable not found in .env file")
            
        # Initialize the application with the bot token
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
        
        # Initialize BNB price cache
        self._bnb_price_cache = {
            'price': 300.0,  # Default fallback price
            'timestamp': 0,
            'ttl': 300  # 5 minutes in seconds
        }
        
        # Initialize Web3 connection
        self.web3 = None
        self.initialize_web3()
        
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
        
        # Global error handler to prevent crashes on transient network issues
        self.app.add_error_handler(self.handle_application_error)
        
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
        await self.safe_reply_text(
            update,
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
        
        await self.safe_reply_text(update, menu_text, parse_mode='Markdown')

    async def connect_token(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start the token connection process."""
        await self.safe_reply_text(update, 
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
                await self.safe_reply_text(
                    update,
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
                await self.safe_reply_text(
                    update,
                    "❌ Invalid token address. Must start with 0x and be 42 characters long."
                    "\n\nPlease try again with format: `<tokenaddress> <chain> <ticker>`",
                    parse_mode='Markdown'
                )
                return
                
            # Validate chain
            if chain not in ['BNB', 'ETH', 'SOL']:
                await self.safe_reply_text(
                    update,
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
            await self.safe_reply_text(
                update,
                f"✅ Token Connected Successfully!\n\n"
                f"{response}\n\n"
                "You can now use /start_trend to begin the trading cycle.",
                parse_mode=None  # Keep Markdown disabled
            )
            
            # Show the updated menu
            await self.menu(update, context)
            
    def initialize_web3(self):
        """Initialize Web3 connection with endpoint rotation, timeouts, and retry backoff."""
        self.logger.info("Initializing Web3 connection...")

        # Preferred/custom endpoint from environment
        custom_rpc = (os.getenv('BSC_MAINNET_RPC') or '').strip() or None

        # Curated list of reliable public endpoints (ordered by general reliability)
        rpc_urls = [
            custom_rpc,
            'https://bsc.publicnode.com',
            'https://rpc.ankr.com/bsc',
            'https://1rpc.io/bnb',
            'https://bsc-dataseed1.bnbchain.org',
            'https://bsc-dataseed.binance.org/',
            'https://bsc-dataseed1.binance.org/',
            'https://bsc-dataseed2.binance.org',
            'https://bsc-dataseed3.binance.org',
            'https://bsc-dataseed4.binance.org',
            'https://bsc-dataseed1.defibit.io',
            'https://bsc-dataseed2.defibit.io',
            'https://endpoints.omniatech.io/v1/bsc/mainnet/public',
        ]
        # Remove Nones/empties and dedupe while preserving order
        seen = set()
        rpc_urls = [u for u in rpc_urls if u and not (u in seen or seen.add(u))]

        # Retry across rounds with exponential backoff
        max_rounds = 3
        base_timeout = 10  # seconds per request
        for round_idx in range(1, max_rounds + 1):
            self.logger.info(f"Web3 connect attempt round {round_idx}/{max_rounds}...")
            for url in rpc_urls:
                try:
                    self.logger.info(f"Attempting to connect to: {url}")
                    provider = HTTPProvider(url, request_kwargs={'timeout': base_timeout})
                    web3 = Web3(provider)

                    # Validate connection by reading latest block and gas price
                    block = web3.eth.block_number
                    if not isinstance(block, int) or block <= 0:
                        raise RuntimeError("Invalid block number returned")

                    gas_price = web3.eth.gas_price  # will raise if provider isn't healthy

                    # Success path
                    self.web3 = web3
                    self.logger.info(
                        f"Connected to BSC node {url}. Block: {block}, Gas: {web3.from_wei(gas_price, 'gwei'):.2f} Gwei"
                    )
                    return True

                except Exception as e:
                    self.logger.warning(f"Connection failed for {url}: {e}")
                    continue

            # Backoff before next round if none succeeded
            if round_idx < max_rounds:
                backoff = min(30, 2 ** round_idx * 2)  # 4s, 8s; cap 30s
                self.logger.info(f"All endpoints failed in round {round_idx}. Backing off for {backoff}s before retry...")
                time.sleep(backoff)

        # If we get here, all attempts in all rounds have failed
        error_msg = (
            "❌ Failed to connect to any BSC node after multiple attempts.\n\n"
            "Suggestions:\n"
            "1) Verify internet/DNS connectivity (try switching DNS to 1.1.1.1/8.8.8.8).\n"
            "2) Try a VPN or different network.\n"
            "3) Set a working custom RPC in .env: BSC_MAINNET_RPC=your_rpc_url_here"
        )
        self.logger.error("All BSC node connection attempts failed after retries")
        raise ConnectionError(error_msg)
        
    def get_web3_instance(self):
        """Get a working Web3 instance with fallback RPC endpoints."""
        if self.web3 is None:
            self.initialize_web3()
        return self.web3

    def __init__(self):
        """Initializes the bot, loads configuration, and sets up handlers."""
        # Load environment variables from project root .env file
        env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
        if os.path.exists(env_path):
            load_dotenv(env_path)
        else:
            load_dotenv()  # Fallback to default .env location
            
        self.token = os.getenv("TELEGRAM_TOKEN")
        if not self.token:
            raise ValueError("TELEGRAM_TOKEN environment variable not found in .env file")
            
        # Create the application with tuned HTTP timeouts to reduce startup flakiness
        request = HTTPXRequest(
            connect_timeout=20,
            read_timeout=60,
            write_timeout=60,
            pool_timeout=30,
        )
        self.app = Application.builder().token(self.token).request(request).build()
        self.bot = self.app.bot
            
        # This will hold the loaded wallets for the session
        self.wallets = []
        self.trading_cycle = None
        
        # Configure logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)
        
        # Initialize BNB price cache
        self._bnb_price_cache = {
            'price': 300.0,  # Default fallback price
            'timestamp': 0,
            'ttl': 300  # 5 minutes in seconds
        }
        
        # Simple and reliable Web3 connection
        self.logger.info("Initializing Web3 connection...")
        
        # Initialize Web3 connection
        self.web3 = None
        self.initialize_web3()
        
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

    async def _fetch_bnb_price_async(self, session, url, parser):
        """Helper method to fetch BNB price asynchronously"""
        try:
            async with session.get(url, timeout=5) as response:
                response.raise_for_status()
                data = await response.json()
                return parser(data)
        except Exception as e:
            self.logger.debug(f"Price fetch failed for {url}: {str(e)}")
            return None
    
    async def get_bnb_price(self):
        """Fetch the current BNB price in USD with caching and async requests."""
        current_time = time.time()
        
        # Return cached price if still valid
        if current_time - self._bnb_price_cache['timestamp'] < self._bnb_price_cache['ttl']:
            return self._bnb_price_cache['price']
            
        # Define price sources with their URLs and parsers
        price_sources = [
            {
                'url': 'https://api.binance.com/api/v3/ticker/price?symbol=BNBUSDT',
                'parser': lambda d: float(d['price'])
            },
            {
                'url': 'https://api.coingecko.com/api/v3/simple/price?ids=binancecoin&vs_currencies=usd',
                'parser': lambda d: float(d['binancecoin']['usd'])
            },
            {
                'url': 'https://api.pancakeswap.info/api/v2/tokens/0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c',
                'parser': lambda d: float(d['data']['price'])
            }
        ]
        
        # Create a session and fetch all prices in parallel
        try:
            import aiohttp
            import asyncio
            
            async with aiohttp.ClientSession(headers={'User-Agent': 'Mozilla/5.0'}) as session:
                tasks = [
                    self._fetch_bnb_price_async(session, source['url'], source['parser'])
                    for source in price_sources
                ]
                
                # Wait for the first successful response with a timeout of 3 seconds
                done, pending = await asyncio.wait(
                    [asyncio.create_task(t) for t in tasks],
                    return_when=asyncio.FIRST_COMPLETED,
                    timeout=3.0
                )
                
                # Get the first successful result
                for task in done:
                    result = await task
                    if result is not None:
                        # Update cache
                        self._bnb_price_cache = {
                            'price': result,
                            'timestamp': current_time,
                            'ttl': 300  # 5 minutes
                        }
                        return result
                        
        except Exception as e:
            self.logger.warning(f"Async price fetch failed: {str(e)}")
        
        # Return cached price if available, otherwise fallback
        return self._bnb_price_cache['price']

    async def fund_wallet(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start the wallet funding process with dynamic BNB/USD calculation."""
        # Reload environment variables to get the latest values
        env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
        if os.path.exists(env_path):
            load_dotenv(env_path, override=True)
            
        # Update central wallet info from environment
        self.central_wallet_address = os.getenv('CENTRAL_WALLET_ADDRESS', '').strip()
        self.central_wallet_private_key = os.getenv('CENTRAL_WALLET_PRIVATE_KEY', '').strip()
        
        # Validate the wallet address
        if not self.central_wallet_address or not self.central_wallet_private_key:
            await self.safe_reply_text(
                update,
                "❌ Central wallet not fully configured.\n"
                "Please set both CENTRAL_WALLET_ADDRESS and CENTRAL_WALLET_PRIVATE_KEY in your .env file."
            )
            return
            
        try:
            # Convert to checksum address
            self.central_wallet_address = self.web3.to_checksum_address(self.central_wallet_address)
        except Exception as e:
            await self.safe_reply_text(
                update,
                f"❌ Invalid wallet address in configuration: {self.central_wallet_address}\n"
                f"Error: {str(e)}"
            )
            return
            
        # Log the wallet being used
        self.logger.info(f"Using central wallet: {self.central_wallet_address}")
        
        # Start a loading message with the wallet address
        loading_message = await self.safe_reply_text(
            update,
            f"🔍 Fetching data for wallet:\n`{self.central_wallet_address}`",
            parse_mode='Markdown'
        )
            
        try:
            # Get BNB price and wallet balance in parallel
            bnb_price_task = asyncio.create_task(self.get_bnb_price())
            
            # Get wallet balance with timeout
            try:
                # Use web3's async provider if available, otherwise run in executor
                if hasattr(self.web3.provider, 'make_request'):
                    balance_wei = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: self.web3.eth.get_balance(self.central_wallet_address)
                    )
                else:
                    balance_wei = self.web3.eth.get_balance(self.central_wallet_address)
                    
                # Wait for price with timeout
                try:
                    bnb_price = await asyncio.wait_for(bnb_price_task, timeout=3.0)
                except asyncio.TimeoutError:
                    bnb_price = self._bnb_price_cache['price']  # Use cached price if timeout
                    
                balance_bnb = self.web3.from_wei(balance_wei, 'ether')
                balance_usd = float(balance_bnb) * bnb_price
                
            except Exception as e:
                self.logger.error(f"Error getting wallet balance: {str(e)}")
                await loading_message.edit_text("❌ Error fetching wallet data. Please try again.")
                return
            
            # Calculate how many wallets we can fund
            min_required_usd = 2.00  # $1 per wallet + $1 reserve
            if balance_usd < min_required_usd:
                needed_usd = min_required_usd - balance_usd
                needed_bnb = needed_usd / bnb_price
                
                # Create a clickable link for BSCScan
                bsc_scan_link = f"https://bscscan.com/address/{self.central_wallet_address}"
                
                # Format the message with proper escaping for MarkdownV2
                def escape_markdown(text):
                    if text is None:
                        return ""
                    text = str(text)
                    # Escape Markdown special characters
                    escape_chars = r'_*[]()~`>#+-=|{}.!:'
                    return ''.join(f'\\{char}' if char in escape_chars else char for char in text)
                
                # Format numbers with proper escaping
                min_required_escaped = escape_markdown(f"{min_required_usd:.2f}")
                balance_usd_escaped = escape_markdown(f"{balance_usd:.2f}")
                balance_bnb_escaped = escape_markdown(f"{balance_bnb:.6f}")
                bnb_price_escaped = escape_markdown(f"{bnb_price:.4f}")
                needed_usd_escaped = escape_markdown(f"{needed_usd:.2f}")
                
                # Build message with proper MarkdownV2 escaping
                # For code blocks in MarkdownV2, we need to use backticks properly
                message = (
                    "❌ *Need at least ${} to fund 1 wallet*\n"
                    "• *Current*: ${} (`{} BNB`)\n"
                    "• *BNB Price*: ${}\n\n"
                    "Send at least *${}* more to:\n"
                    "```\n{}\n```\n"
                    "[View on BSCScan]({})\n\n"
                    "After sending, use /check_balance to verify the transaction"
                ).format(
                    min_required_escaped,
                    balance_usd_escaped,
                    balance_bnb_escaped,
                    bnb_price_escaped,
                    needed_usd_escaped,
                    self.central_wallet_address,
                    bsc_scan_link
                )
                
                # Edit the loading message with the result
                try:
                    await loading_message.edit_text(
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
                    await loading_message.edit_text(plain_message)
                return
                
            # Calculate how many wallets we can fund (retain $1, $1 per wallet)
            if not self.wallets:
                await self.safe_reply_text(update, "❌ No wallets loaded. Run /create_wallet first.")
                return
            max_wallets = int((balance_usd - 1.00) / 1.00)  # Leave $1 for gas
            max_wallets = max(0, min(max_wallets, len(self.wallets), 34))  # Cap by available wallets and 34
            
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
            
            # Replace with safe sender
            await self.safe_reply_text(
                update,
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
            await self.safe_reply_text(update, "❌ An error occurred while checking balances. Please try again later.")

    async def handle_interactive_messages(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle all interactive text messages.
        
        Currently routes funding confirmation when a funding plan is proposed
        by /fund_wallet or /check_balance (Option A flow).
        """
        try:
            # Token connection flow
            if context.user_data.get('awaiting_token_info'):
                return await self.handle_token_info(update, context)

            # Funding confirmation flow
            if context.user_data.get('awaiting_funding_confirmation'):
                return await self.handle_funding_confirmation(update, context)

            # Default fallback
            await self.safe_reply_text(update, "I didn't understand that. Use /menu to see available commands.")
        except Exception as e:
            self.logger.error(f"Error in handle_interactive_messages: {str(e)}", exc_info=True)
            await self.safe_reply_text(update, "❌ An unexpected error occurred while processing your message.")

    async def handle_funding_confirmation(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle funding confirmation from user."""
        user_input = update.message.text.strip().lower()
        
        if user_input != 'confirm':
            await self.safe_reply_text(
                update,
                "Please type `confirm` to proceed with funding, "
                "or /cancel to abort the funding process.",
                parse_mode='Markdown'
            )
            return
            
        # Get funding details
        funding_details = context.user_data.get('funding_details', {})
        if not funding_details:
            await self.safe_reply_text(update, "❌ Error: Missing funding details. Please try /fund_wallet again.")
            return
            
        # Clear the funding confirmation flag
        context.user_data.pop('awaiting_funding_confirmation', None)
        
        # Start the funding process
        await self.distribute_funds(update, context, funding_details)
    
    async def distribute_funds(self, update: Update, context: ContextTypes.DEFAULT_TYPE, funding_details: dict):
        """Distribute funds from central wallet to trading wallets based on available balance."""
        if not self.wallets:
            await self.safe_reply_text(update, "❌ No wallets found to fund.")
            return
            
        if not self.central_wallet_private_key or not self.central_wallet_address:
            await self.safe_reply_text(update, "❌ Central wallet not configured.")
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
                    await self.safe_reply_text(
                        update,
                        f"❌ Insufficient balance. Need at least ${1 + (1.0 / funding_details['bnb_price']):.2f} "
                        f"worth of BNB to fund 1 wallet.\n\n"
                        f"Send more BNB to:\n`{self.central_wallet_address}`",
                        parse_mode='Markdown'
                    )
                    return
                
                max_wallets = adjusted_wallets
                funding_details['max_wallets'] = max_wallets
                
                await self.safe_reply_text(update, f"⚠️ Balance decreased. Will now fund {max_wallets} wallets.", parse_mode='Markdown')
                
            # Start funding process
            status_msg = await self.safe_reply_text(update, "🔄 Starting to fund wallets...")
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
            await self.safe_reply_text(update, f"❌ Error during funding process: {str(e)}\n\nPlease check the logs and try again.")
    
    async def check_balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Check the balance of the central wallet."""
        if not hasattr(self, 'central_wallet_address') or not self.central_wallet_address:
            await self.safe_reply_text(update, "❌ Central wallet not configured.")
            return
            
        try:
            # Show typing action (PTB v20+)
            try:
                await context.bot.send_chat_action(
                    chat_id=update.effective_chat.id,
                    action=ChatAction.TYPING
                )
            except Exception as _:
                pass
            
            # Get BNB price
            bnb_price = await self.get_bnb_price()
            if not bnb_price:
                raise ValueError("Could not fetch BNB price")
                
            # Check central wallet balance
            balance_wei = self.web3.eth.get_balance(self.central_wallet_address)
            balance_bnb = self.web3.from_wei(balance_wei, 'ether')
            balance_usd = float(balance_bnb) * bnb_price
            
            # Format response for only the central wallet (classic Markdown, cleaner spacing)
            addr_short = f"{self.central_wallet_address[:6]}...{self.central_wallet_address[-4:]}"
            response_text = (
                "💰 *Wallet Balance*\n\n"
                "*Central Wallet*\n"
                f"• Address: `{self._escape_markdown(addr_short)}`\n"
                f"• Balance: `{balance_bnb:.6f} BNB`\n"
                f"• USD: `${balance_usd:.2f}`\n"
                f"• BscScan: https://bscscan.com/address/{self.central_wallet_address}\n"
            )
            # Use the safe_reply_text method which already handles disable_web_page_preview
            await self.safe_reply_text(
                update=update,
                text=response_text,
                parse_mode='Markdown'
            )
        except Exception as e:
            self.logger.error(f"Error in check_balance: {str(e)}")
            await self.safe_reply_text(update, "❌ An error occurred while checking balances. Please try again later.")
    
    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Cancel any ongoing operation."""
        cancelled = False
        
        # Cancel token connection if in progress
        if 'awaiting_token_info' in context.user_data:
            for key in ['awaiting_token_info', 'token_address', 'chain']:
                if key in context.user_data:
                    del context.user_data[key]
            await self.safe_reply_text(update, "❌ Token connection cancelled. Use /connect to start over.")
            cancelled = True
            
        # Cancel funding process if in progress
        if 'awaiting_funding_confirmation' in context.user_data:
            for key in ['awaiting_funding_confirmation', 'funding_amount', 'funding_gas_price']:
                if key in context.user_data:
                    del context.user_data[key]
            await self.safe_reply_text(update, "❌ Wallet funding cancelled. Use /fund_wallet to start over.")
            cancelled = True
            
        if not cancelled:
            await self.safe_reply_text(update, "❌ No operation to cancel.")
            
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
        
        await self.safe_reply_text(
            update,
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
            await self.safe_reply_text(update, "❌ No wallets loaded. Please use /create_wallet first.")
            return
            
        if not self.token_config.get('address'):
            await self.safe_reply_text(update, "❌ No token connected. Please use /connect first.")
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
        await self.safe_reply_text(update, result)
        
        # Update the menu to show the new status
        await self.menu(update, context)
        
    async def stop_trend(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Stop the trending trading cycle"""
        if not hasattr(self, 'trading_cycle') or not self.trading_cycle:
            await self.safe_reply_text(update, "❌ No active trading cycle to stop.")
            return
            
        # Stop the trading cycle
        result = await self.trading_cycle.stop()
        await self.safe_reply_text(update, result)
        
        # Update the menu to show the new status
        await self.menu(update, context)
        
    def _escape_markdown(self, text: str) -> str:
        """Escape a minimal set of Markdown characters to avoid Telegram parse errors.
        This targets classic 'Markdown' (not MarkdownV2) issues like parentheses and a few common symbols.
        """
        if not text:
            return text
        # Escape backslashes first
        text = text.replace('\\', '\\\\')
        # Minimal escapes for classic Markdown
        replacements = {
            '(': '\\(',
            ')': '\\)',
            '*': '\\*',
            '_': '\\_',
            '[': '\\[',
            ']': '\\]',
            '`': '\\`',
        }
        for k, v in replacements.items():
            text = text.replace(k, v)
        return text

    def _split_message(self, text: str, max_len: int = 4000) -> list:
        """Split a message into chunks under max_len, preferring line or word boundaries."""
        if len(text) <= max_len:
            return [text]
        chunks = []
        start = 0
        while start < len(text):
            end = min(len(text), start + max_len)
            # Prefer to break at a newline
            split_at = text.rfind('\n', start, end)
            if split_at == -1 or split_at <= start + max_len * 0.6:
                # Try space if no good newline
                split_at = text.rfind(' ', start, end)
            if split_at == -1 or split_at <= start:
                split_at = end
            chunk = text[start:split_at]
            chunks.append(chunk)
            start = split_at
        return [c for c in chunks if c]

    async def safe_reply_text(self, update: Update, text: str, parse_mode=None, max_retries=3, initial_delay=1, **kwargs):
        """Safely send a reply with retry logic for timeouts and other transient errors.
        
        Args:
            update: The update object from the Telegram bot
            text: The message text to send
            parse_mode: The parse mode for the message (e.g., 'Markdown', 'HTML')
            max_retries: Maximum number of retry attempts
            initial_delay: Initial delay between retries in seconds
            **kwargs: Additional arguments to pass to reply_text
        """
        delay = initial_delay
        last_exception = None

        # Set default kwargs if not provided
        kwargs.setdefault('disable_web_page_preview', True)
        kwargs.setdefault('read_timeout', 30)
        kwargs.setdefault('write_timeout', 30)
        kwargs.setdefault('connect_timeout', 30)
        kwargs.setdefault('pool_timeout', 30)

        # Pre-escape Markdown if requested to avoid parse errors
        effective_text = text
        if parse_mode:
            kwargs['parse_mode'] = parse_mode
            if parse_mode == 'Markdown':
                effective_text = self._escape_markdown(effective_text)

        # Decide chunk size conservatively
        max_len = 3500 if kwargs.get('parse_mode') else 4000
        chunks = self._split_message(effective_text, max_len=max_len)

        last_result = None
        for idx, chunk in enumerate(chunks):
            delay = initial_delay
            for attempt in range(max_retries):
                try:
                    last_result = await update.message.reply_text(chunk, **kwargs)
                    # Small pause between chunks to avoid rate limits
                    if idx < len(chunks) - 1:
                        await asyncio.sleep(0.6)
                    break
                except Exception as e:
                    last_exception = e
                    self.logger.warning(f"Attempt {attempt + 1} failed: {str(e)}")

                    # If Telegram rejects Markdown entities, fall back to plain text once per chunk
                    msg = str(e).lower()
                    if ("can't parse entities" in msg or 'parse entities' in msg or 'bad request: markdown' in msg) and kwargs.get('parse_mode'):
                        self.logger.info("Falling back to plain text due to Markdown parse error")
                        kwargs.pop('parse_mode', None)
                        # Recompute chunks for plain text on first failure
                        remaining_text = '\n'.join([chunk] + chunks[idx + 1:])
                        chunks = self._split_message(remaining_text, max_len=4000)
                        # restart sending from current index with plain text
                        break

                    if attempt < max_retries - 1:  # Don't sleep on the last attempt
                        await asyncio.sleep(delay)
                        delay *= 2  # Exponential backoff
            else:
                # Exhausted retries for this chunk; abort
                error_msg = f"❌ Failed to send message chunk {idx + 1}/{len(chunks)} after {max_retries} attempts: {str(last_exception)}"
                self.logger.error(error_msg)
                # Attempt to notify with minimal text
                try:
                    return await update.message.reply_text(
                        "⚠️ " + error_msg[:3800],
                        disable_web_page_preview=True
                    )
                except Exception:
                    raise
            # If we broke out of inner loop due to markdown fallback, restart outer loop
            if not kwargs.get('parse_mode') and idx < len(chunks) and chunk not in chunks:
                # chunks were recomputed; restart loop to send from the beginning of new chunks
                return await self.safe_reply_text(update, text, parse_mode=None, max_retries=max_retries, initial_delay=initial_delay, **kwargs)

        return last_result

    async def create_wallet(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Reads pre-generated wallets from wallets.json and displays them."""
        # Check if token details are connected
        if not all(key in self.token_config for key in ['address', 'chain', 'ticker']):
            await self.safe_reply_text(update, "❌ Please connect token details first using /connect command.")
            return
            
        await self.safe_reply_text(update, "🔎 Checking for pre-generated wallets...")
        
        try:
            # Construct the absolute path to wallets.json relative to this script
            bot_dir = os.path.dirname(os.path.abspath(__file__))
            wallets_file_path = os.path.join(bot_dir, '..', 'hardhat-scripts', 'wallets.json')
            self.logger.info(f"Looking for wallets at: {wallets_file_path}")

            if not os.path.exists(wallets_file_path):
                error_msg = (
                    "❌ `wallets.json` not found.\n\n"
                    "Please run the wallet generation script manually in your terminal first.\n"
                    "Expected location: `hardhat-scripts/wallets.json`"
                )
                await self.safe_reply_text(update, error_msg, parse_mode='Markdown')
                return

            # Read the file with explicit encoding
            with open(wallets_file_path, 'r', encoding='utf-8') as f:
                try:
                    wallets = json.load(f)
                except json.JSONDecodeError as e:
                    error_msg = (
                        "❌ Error reading `wallets.json`. The file is corrupted or empty.\n\n"
                        f"Error: {str(e)}\n\n"
                        "Please run the wallet generation script again."
                    )
                    await self.safe_reply_text(update, error_msg, parse_mode='Markdown')
                    return
            
            if not isinstance(wallets, list):
                error_msg = "❌ Invalid format in `wallets.json`. Expected a list of wallet objects."
                await self.safe_reply_text(update, error_msg, parse_mode='Markdown')
                return
            
            self.wallets = wallets
            addresses = [w.get('address') for w in wallets if w and isinstance(w, dict) and w.get('address')]

            if not addresses:
                error_msg = "⚠️ `wallets.json` is empty or malformed. Please run the generation script again."
                await self.safe_reply_text(update, error_msg, parse_mode='Markdown')
                return

            response = f"✅ Found {len(addresses)} pre-generated wallets.\n\n"
            response += "\n".join(addresses)
            
            # Split the message into chunks to avoid hitting Telegram's message length limit
            chunk_size = 3000  # Conservative chunk size to account for markdown characters
            chunks = [response[i:i + chunk_size] for i in range(0, len(response), chunk_size)]
            
            for i, chunk in enumerate(chunks):
                try:
                    # Send chunks without Markdown parse mode to avoid entity errors on large messages
                    await self.safe_reply_text(update, chunk)
                    # Small delay between chunks to avoid rate limiting
                    if i < len(chunks) - 1:
                        await asyncio.sleep(1)
                except Exception as e:
                    self.logger.error(f"Error sending chunk {i+1}/{len(chunks)}: {str(e)}")
                    continue

        except Exception as e:
            tb_str = traceback.format_exc()
            self.logger.error(f"Critical error in create_wallet: {tb_str}")
            error_msg = (
                "❌ An unexpected error occurred while processing wallets.\n\n"
                f"Error: {str(e)}\n\n"
                "Please check the logs for more details."
            )
            await self.safe_reply_text(update, error_msg)

    def run(self):
        """Starts the bot."""
        logging.basicConfig(
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            level=logging.INFO
        )
        logging.info("Bot starting...")
        # Drop pending updates and ensure clean polling startup
        self.app.run_polling(drop_pending_updates=True)

    async def handle_application_error(self, update: object, context: ContextTypes.DEFAULT_TYPE):
        """Global error handler to log and suppress non-fatal errors."""
        try:
            self.logger.error("Application error", exc_info=context.error)
        except Exception:
            # Ensure we never raise from the error handler
            pass

if __name__ == '__main__':
    bot = TokenTrendingBot()
    bot.run()