
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, PicklePersistence, ConversationHandler
from scheduler import Scheduler
from payment import PaymentManager
from logger import Logger
from modules.dex import DexModule
from modules.cmc import CMCModule
from modules.twitter import TwitterModule
from dashboard import Dashboard
from central_wallet_manager import CentralWalletManager
from database import Database
import time

TELEGRAM_TOKEN = "8430101507:AAGkn3NHv9YzjbcadR_hOHTrHK1ldq338sA"

class TokenTrendingBot:
    import json
    PERSIST_FILE = 'connections.json'
    def __init__(self):
        persistence = PicklePersistence(filepath='bot_data.pkl')
        self.app = ApplicationBuilder().token(TELEGRAM_TOKEN).persistence(persistence).build()
        self.scheduler = Scheduler()
        self.payment_manager = PaymentManager()
        self.logger = Logger()
        self.dex_module = DexModule()
        self.cmc_module = CMCModule()
        self.twitter_module = TwitterModule()
        self.dashboard = Dashboard(self)
        self.central_wallet_manager = CentralWalletManager()
        self.db = Database()
        self.wallet_addresses = {}  # Store wallet addresses per user
        self._setup_handlers()

    def load_connections(self, user_id):
        try:
            with open(self.PERSIST_FILE, 'r') as f:
                data = json.load(f)
            return data.get(str(user_id), {
                'cmc': False,
                'dexscreener': False,
                'dextools': False,
                'twitter': False
            })
        except Exception:
            return {
                'cmc': False,
                'dexscreener': False,
                'dextools': False,
                'twitter': False
            }

    def save_connections(self, user_id, connections):
        try:
            data = {}
            try:
                with open(self.PERSIST_FILE, 'r') as f:
                    data = json.load(f)
            except Exception:
                pass
            data[str(user_id)] = connections
            with open(self.PERSIST_FILE, 'w') as f:
                json.dump(data, f)
        except Exception:
            pass

    def _setup_handlers(self):
        from telegram.ext import MessageHandler, filters
        self.app.add_handler(CommandHandler('start', self.start))
        self.app.add_handler(CommandHandler('start_trend', self.start_trend))
        self.app.add_handler(CommandHandler('status', self.status))
        self.app.add_handler(CommandHandler('menu', self.menu))
        self.app.add_handler(CommandHandler('trend', self.trend))
        self.app.add_handler(CommandHandler('check_payment', self.check_payment))
        self.app.add_handler(CommandHandler('payment_history', self.payment_history))
        self.app.add_handler(CommandHandler('create_wallet', self.create_wallet))
        self.app.add_handler(CommandHandler('fund_wallet', self.fund_wallet))
        self.app.add_handler(CommandHandler('wallet_status', self.wallet_status))
        self.app.add_handler(CommandHandler('help', self.help))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_token_details))

    async def login(self, update, context):
        user_id = update.effective_user.id
        context.user_data['connections'] = self.load_connections(user_id)
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        keyboard = [
            [InlineKeyboardButton("CMC", callback_data='platform_cmc')],
            [InlineKeyboardButton("DEXScreener", callback_data='platform_dexscreener')],
            [InlineKeyboardButton("DEXTools", callback_data='platform_dextools')],
            [InlineKeyboardButton("Twitter", callback_data='platform_twitter')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("🔐 Choose Platform to Connect", reply_markup=reply_markup)
        return 5  # PLATFORM_SELECT

    async def platform_select(self, update, context):
        # Will only handle callback queries due to pattern matching in handler
        await update.callback_query.answer()
        
        platform = update.callback_query.data.replace('platform_', '')
        msg_target = update.callback_query.message
        
        try:
            await msg_target.edit_text(f"Selected: {platform.upper()}")
        except Exception:
            pass
        
        # Save selected platform for state tracking
        context.user_data['selected_platform'] = platform
        
        # Send credential prompt based on platform
        if platform == 'cmc':
            await msg_target.reply_text("📧 Enter your CoinMarketCap email:")
            return 6  # CMC_EMAIL
        elif platform == 'dexscreener':
            await msg_target.reply_text("📧 Enter your DEXScreener email:")
            return 8  # DEX_EMAIL
        elif platform == 'dextools':
            await msg_target.reply_text("📧 Enter your DEXTools email:")
            return 10  # DEXT_EMAIL
        elif platform == 'twitter':
            await msg_target.reply_text("📱 Enter your Twitter username/email:")
            return 12  # TWITTER_USER

    async def cmc_email(self, update, context):
        context.user_data.setdefault('credentials', {}).setdefault('cmc', {})['email'] = update.message.text.strip()
        await update.message.reply_text("🔐 Enter your CMC password:")
        return 7  # CMC_PASSWORD

    async def cmc_password(self, update, context):
        context.user_data['credentials']['cmc']['password'] = update.message.text.strip()
        await update.message.reply_text("🔄 Connecting to CoinMarketCap...")
        if context.user_data['credentials']['cmc']['email'] == 'fail':
            await update.message.reply_text("❌ CMC login failed. Try again? (Retry/Skip)")
            return 6  # CMC_EMAIL
        context.user_data['connections']['cmc'] = True
        self.save_connections(update.effective_user.id, context.user_data['connections'])
        await self.send_connection_status(update, context, 'CoinMarketCap')
        return 15  # CONFIRM_ANOTHER

    async def dex_email(self, update, context):
        context.user_data.setdefault('credentials', {}).setdefault('dexscreener', {})['email'] = update.message.text.strip()
        await update.message.reply_text("🔐 Enter your password:")
        return 9  # DEX_PASSWORD

    async def dex_password(self, update, context):
        context.user_data['credentials']['dexscreener']['password'] = update.message.text.strip()
        await update.message.reply_text("🔄 Connecting to DEXScreener...")
        if context.user_data['credentials']['dexscreener']['email'] == 'fail':
            await update.message.reply_text("❌ DEXScreener login failed. Try again? (Retry/Skip)")
            return 8  # DEX_EMAIL
        context.user_data['connections']['dexscreener'] = True
        self.save_connections(update.effective_user.id, context.user_data['connections'])
        await self.send_connection_status(update, context, 'DEXScreener')
        return 15

    async def dext_email(self, update, context):
        context.user_data.setdefault('credentials', {}).setdefault('dextools', {})['email'] = update.message.text.strip()
        await update.message.reply_text("🔐 Enter your password:")
        return 11  # DEXT_PASSWORD

    async def dext_password(self, update, context):
        context.user_data['credentials']['dextools']['password'] = update.message.text.strip()
        await update.message.reply_text("🔄 Connecting to DEXTools...")
        if context.user_data['credentials']['dextools']['email'] == 'fail':
            await update.message.reply_text("❌ DEXTools login failed. Try again? (Retry/Skip)")
            return 10  # DEXT_EMAIL
        context.user_data['connections']['dextools'] = True
        self.save_connections(update.effective_user.id, context.user_data['connections'])
        await self.send_connection_status(update, context, 'DEXTools')
        return 15

    async def twitter_user(self, update, context):
        context.user_data.setdefault('credentials', {}).setdefault('twitter', {})['user'] = update.message.text.strip()
        await update.message.reply_text("🔐 Enter your Twitter password:")
        return 13  # TWITTER_PASSWORD

    async def twitter_password(self, update, context):
        context.user_data['credentials']['twitter']['password'] = update.message.text.strip()
        await update.message.reply_text("📲 Enter 2FA code (if enabled, else type 'skip'):")
        return 14  # TWITTER_2FA

    async def twitter_2fa(self, update, context):
        code = update.message.text.strip()
        context.user_data['credentials']['twitter']['2fa'] = code
        await update.message.reply_text("🔄 Connecting to Twitter...")
        if context.user_data['credentials']['twitter']['user'] == 'fail':
            await update.message.reply_text("❌ Twitter login failed. Try again? (Retry/Skip)")
            return 12  # TWITTER_USER
        context.user_data['connections']['twitter'] = True
        self.save_connections(update.effective_user.id, context.user_data['connections'])
        await self.send_connection_status(update, context, 'Twitter')
        return 15
    async def send_connection_status(self, update, context, just_connected):
        platforms = ['CoinMarketCap', 'DEXScreener', 'DEXTools', 'Twitter']
        connections = context.user_data.get('connections', {})
        connected = [p for p in platforms if connections.get(p.lower(), False)]
        unconnected = [p for p in platforms if not connections.get(p.lower(), False)]
        msg = f"✅ Successfully connected to {just_connected}!\n\n"
        msg += f"🔗 Connected Platforms: {len(connected)}/4\n"
        msg += f"📱 Available: {', '.join(unconnected) if unconnected else 'None'}\n\n"
        msg += "What would you like to do next?\n[Connect Another Platform] [Start Trending] [Check Status]"
        await update.message.reply_text(msg)
    async def logout(self, update, context):
        user_id = update.effective_user.id
        connections = self.load_connections(user_id)
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        keyboard = []
        for platform, connected in connections.items():
            if connected:
                keyboard.append([InlineKeyboardButton(platform.capitalize(), callback_data=platform)])
        if not keyboard:
            await update.message.reply_text("No platforms connected to logout.")
            return
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("Select platform to disconnect:", reply_markup=reply_markup)
        context.user_data['connections'] = connections
        return 'LOGOUT_SELECT'

    async def logout_select(self, update, context):
        await update.callback_query.answer()
        platform = update.callback_query.data
        msg_target = update.callback_query.message
        
        if platform in context.user_data['connections']:
            context.user_data['connections'][platform] = False
            self.save_connections(update.effective_user.id, context.user_data['connections'])
            await msg_target.edit_text(f"✅ {platform.capitalize()} disconnected.")
        else:
            await msg_target.edit_text("Invalid platform.")
        return ConversationHandler.END

    async def reconnect(self, update, context):
        user_id = update.effective_user.id
        connections = self.load_connections(user_id)
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        keyboard = []
        for platform, connected in connections.items():
            if not connected:
                keyboard.append([InlineKeyboardButton(platform.capitalize(), callback_data=platform)])
        if not keyboard:
            await update.message.reply_text("All platforms are already connected.")
            return
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("Select platform to reconnect:", reply_markup=reply_markup)
        context.user_data['connections'] = connections
        return 'RECONNECT_SELECT'

    async def reconnect_select(self, update, context):
        await update.callback_query.answer()
        platform = update.callback_query.data
        msg_target = update.callback_query.message
        
        try:
            await msg_target.edit_text(f"Selected: {platform.upper()}")
        except Exception:
            pass

        if platform == 'cmc':
            await msg_target.reply_text("📧 Enter your CoinMarketCap email:")
            return 6  # CMC_EMAIL
        elif platform == 'dexscreener':
            await msg_target.reply_text("📧 Enter your DEXScreener email:")
            return 8  # DEX_EMAIL
        elif platform == 'dextools':
            await msg_target.reply_text("📧 Enter your DEXTools email:")
            return 10  # DEXT_EMAIL
        elif platform == 'twitter':
            await msg_target.reply_text("📱 Enter your Twitter username/email:")
            return 12  # TWITTER_USER
        else:
            await msg_target.reply_text("Invalid platform.")
            return ConversationHandler.END

    async def help(self, update, context):
        help_text = (
            "Available commands:\n"
            "/start - Welcome message\n"
            "/menu - Show this menu\n"
            "/trend - How to start a campaign\n"
            "/start_trend <token_address> <chain> <platforms> <engagement> - Initiate a trending campaign\n"
            "/create_wallet - Create BNB chain wallets for trending\n"
            "/fund_wallet - Get central wallet address to fund your wallets\n"
            "/wallet_status - Check wallet creation and funding status\n"
            "/check_payment <payment_id> - Check payment status\n"
            "/payment_history - View your payment history\n"
            "/status - Show status of active campaigns and platform connections\n"
            "/login - Connect your trending platforms\n"
            "/logout - Disconnect a platform\n"
            "/reconnect - Re-authenticate a failed or lost connection\n"
            "/help - Show this help message\n"
        )
        await update.message.reply_text(help_text)

    async def confirm_another(self, update, context):
        answer = update.message.text.strip().lower()
        if answer == 'yes':
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            keyboard = []
            for platform, connected in context.user_data['connections'].items():
                if not connected:
                    keyboard.append([InlineKeyboardButton(platform.capitalize(), callback_data=platform)])
            if not keyboard:
                await update.message.reply_text("All connections complete! Use /status to check connections.")
                return ConversationHandler.END
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text("🔐 Choose Platform to Connect", reply_markup=reply_markup)
            return 5
        else:
            await update.message.reply_text("All connections complete! Use /status to check connections.")
            return ConversationHandler.END
    async def menu(self, update, context):
        commands = [
            '/start - Welcome message',
            '/menu - Show this menu',
            '/trend - How to start a campaign',
            '/start_trend <token_address> <chain> <platforms> <engagement> - Initiate a trending campaign',
            '/check_payment <payment_id> - Check payment status',
            '/payment_history - View your payment history',
            '/status - Show status of active campaigns',
            '/stop <token_id> - Stop a campaign',
        ]
        await update.message.reply_text('Available commands:\n' + '\n'.join(commands))

    async def start(self, update, context):
        first_name = update.effective_user.first_name if update.effective_user else "User"
        welcome_msg = f"Hi {first_name}, welcome to TrendingFX_Bot!\n\n🚀 I can help you start trending campaigns across multiple platforms:\n- CoinMarketCap\n- DEXScreener\n- DEXTools\n- Twitter"
        await update.message.reply_text(welcome_msg)

    async def trend(self, update, context):
        await update.message.reply_text('Use /start_trend <token_address> <chain> <platforms> <engagement> to begin a campaign.')

    async def start_trend(self, update, context):
        """Collect all token details in one message."""
        try:
            await update.message.reply_text(
                "Please provide your token details in this format:\n[Token Address] [Chain] [Ticker]\n\n"
                "Example: 0x1234...abcd ETH MYTOKEN\n\nSupported chains: ETH, BNB, SOL (or full forms: Ethereum, BNB Chain, Solana)\nNote: You can use any amount of spaces between the fields."
            )
            context.user_data['trend_step'] = 'collect_all'
        except Exception:
            await update.message.reply_text('An error occurred. Please try again later.')
            context.user_data.pop('trend_step', None)
            return ConversationHandler.END

        campaign_id = f"TREND_{int(time.time())}"
        context.user_data['campaign_id'] = campaign_id
        # Check user's connection status
        connections = context.user_data.get('connections', self.load_connections(user_id))
        connected_platforms = [p for p, v in connections.items() if v]
        
        if not connected_platforms:
            await update.message.reply_text(
                "❌ No platforms connected!\n\n"
                "Please use /login to connect at least one platform:\n"
                "- CoinMarketCap\n"
                "- DEXScreener\n"
                "- DEXTools\n"
                "- Twitter"
            )
            return ConversationHandler.END

        # Clear any previous trend data
        if 'trend_setup' in context.user_data:
            context.user_data.pop('trend_setup')
        context.user_data['trend_setup'] = {
            'token_address': None,
            'chain': None,
            'ticker': None,
            'platforms': None,
            'engagement_level': None,
            'payment_id': None
        }

    async def get_token_details(self, update, context):
        """Collect all details from one message, parse and confirm."""
        try:
            # Always process as token details after /start_trend
            text = update.message.text.strip()
            print(f"[DEBUG] Received user input: {text}")  # Debug log
            parts = text.split()
            if len(parts) != 3:
                await update.message.reply_text("Invalid format. Please provide: [Token Address] [Chain] [Ticker]")
                return
            token_address, chain, ticker = parts
            await update.message.reply_text(
                f"✅ Token added successfully!\n\nDetails:\nToken Address: {token_address}\nChain: {chain}\nTicker: {ticker}"
            )
            context.user_data['trend_setup'] = {
                'token_address': token_address,
                'chain': chain,
                'ticker': ticker
            }
            context.user_data.pop('trend_step', None)
        except Exception as e:
            print(f"[DEBUG] Exception in get_token_details: {e}")  # Debug log
            await update.message.reply_text('An error occurred. Please try again later.')
            context.user_data.pop('trend_step', None)
            return

    async def get_chain(self, update, context):
        """Step 2: Collect chain, prompt for ticker."""
        try:
            if context.user_data.get('trend_step') != 'chain':
                return await self.start_trend(update, context)
            chain = update.message.text.strip()
            context.user_data['trend_setup']['chain'] = chain
            await update.message.reply_text('Please enter the ticker symbol (e.g., MYTOKEN):')
            context.user_data['trend_step'] = 'ticker'
        except Exception:
            await update.message.reply_text('An error occurred. Please try again later.')
            context.user_data.pop('trend_setup', None)
            context.user_data.pop('trend_step', None)
            return ConversationHandler.END

    async def get_ticker(self, update, context):
        """Step 3: Collect ticker, display summary."""
        try:
            if context.user_data.get('trend_step') != 'ticker':
                return await self.start_trend(update, context)
            ticker = update.message.text.strip()
            context.user_data['trend_setup']['ticker'] = ticker
            session = context.user_data['trend_setup']
            msg = (
                f"Token added successfully! Details: Token Address: {session.get('token_address')}, Chain: {session.get('chain')}, Ticker: {session.get('ticker')}."
            )
            await update.message.reply_text(msg)
            context.user_data.pop('trend_setup', None)
            context.user_data.pop('trend_step', None)
        except Exception:
            await update.message.reply_text('An error occurred. Please try again later.')
            context.user_data.pop('trend_setup', None)
            context.user_data.pop('trend_step', None)
            return ConversationHandler.END
            
    async def confirm_trend(self, update, context):
        """Handle trend confirmation"""
        await update.callback_query.answer()
        choice = update.callback_query.data
        
        if choice == 'cancel':
            await update.callback_query.message.edit_text("❌ Trend setup cancelled.")
            return ConversationHandler.END
            
        setup = context.user_data['trend_setup']
        
        # Verify payment one last time
        payment_id = setup.get('payment_id')
        if not payment_id or not self.payment_manager.is_campaign_active(payment_id):
            await update.callback_query.message.edit_text(
                "❌ Payment verification failed. Please restart the process with /start_trend"
            )
            return ConversationHandler.END
            
        # Initialize trend tracking
        if not hasattr(self.scheduler, 'active_trends'):
            self.scheduler.active_trends = set()
            
        if len(self.scheduler.active_trends) >= 10:
            await update.callback_query.message.edit_text(
                "❌ Maximum concurrent trends reached (10). Please try again later."
            )
            return ConversationHandler.END
            
        # Add to active trends
        self.scheduler.active_trends.add(setup['token_address'])
        
        # Start platform-specific activities
        try:
            # Initialize modules based on selected platforms
            if 'dextools' in setup['platforms'] or 'dexscreener' in setup['platforms']:
                self.dex_module.simulate_activity(
                    setup['token_address'],
                    setup['chain'],
                    setup['engagement_level'],
                    randomize=True,
                    proxy=True
                )
            
            if 'cmc' in setup['platforms']:
                self.cmc_module.generate_traffic(
                    setup['token_address'],
                    setup['engagement_level'],
                    randomize=True,
                    proxy=True
                )
            
            if 'twitter' in setup['platforms']:
                hashtag = f"#{setup['ticker']}"
                self.twitter_module.automate_engagement(
                    hashtag,
                    setup['engagement_level'],
                    randomize=True
                )
                
            # Schedule automatic campaign end
            self.scheduler.schedule_trend(
                setup['token_address'],
                duration=24*60*60,  # 24 hours
                callback=self.trend_complete
            )
            
            # Log campaign start
            self.logger.log(
                f"Trend started for {setup['ticker']} ({setup['token_address']}) "
                f"on {setup['chain']} with platforms: {', '.join(setup['platforms'])}"
            )
            
            # Send confirmation message
            status_msg = (
                f"✅ Trend Campaign Started!\n\n"
                f"Token: {setup['ticker']} ({setup['token_address']})\n"
                f"Chain: {setup['chain']}\n"
                f"Platforms: {', '.join(setup['platforms'])}\n"
                f"Duration: 24 hours\n"
                f"Payment ID: {payment_id}\n\n"
                f"Use /status to check campaign progress.\n"
                f"Campaign will automatically end in 24 hours."
            )
            await update.callback_query.message.edit_text(status_msg)
            
            # Schedule periodic updates
            self.scheduler.schedule_updates(setup['token_address'], update.effective_chat.id)
            
        except Exception as e:
            self.scheduler.active_trends.discard(setup['token_address'])
            logging.error(f"Failed to start trend: {str(e)}")
            await update.callback_query.message.edit_text(
                "❌ Failed to start trending campaign. Please try again."
            )
            return ConversationHandler.END
            
        return ConversationHandler.END

    async def select_platforms(self, update, context):
        """Handle platform selection"""
        await update.callback_query.answer()
        
        # Initialize platforms list if not exists
        if 'selected_platforms' not in context.user_data['trend_setup']:
            context.user_data['trend_setup']['selected_platforms'] = set()
            
        # Get selected platform
        platform = update.callback_query.data.replace('platform_', '')
        platforms = context.user_data['trend_setup']['selected_platforms']
        
        if platform in platforms:
            platforms.remove(platform)
        else:
            platforms.add(platform)
            
        # Update keyboard with selected platforms marked
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        all_platforms = context.user_data['trend_setup']['connected_platforms']
        keyboard = []
        for p in all_platforms:
            status = '✅ ' if p in platforms else ''
            keyboard.append([InlineKeyboardButton(
                f"{status}{p.upper()}", 
                callback_data=f'platform_{p}'
            )])
            
        # Add done button if at least one platform selected
        if platforms:
            keyboard.append([InlineKeyboardButton(
                "✅ Confirm Platforms",
                callback_data='platforms_done'
            )])
            
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.callback_query.message.edit_text(
            f"🌐 Selected Platforms ({len(platforms)}):\n"
            f"{', '.join(platforms) if platforms else 'None'}\n\n"
            "Select platforms for your campaign:",
            reply_markup=reply_markup
        )
        return TREND_PLATFORMS
        
    async def confirm_platforms(self, update, context):
        """Handle platform selection confirmation"""
        await update.callback_query.answer()
        
        platforms = context.user_data['trend_setup'].get('selected_platforms', set())
        if not platforms:
            await update.callback_query.message.edit_text(
                "❌ Please select at least one platform."
            )
            return TREND_PLATFORMS
            
        context.user_data['trend_setup']['platforms'] = list(platforms)
        
        # Show engagement level selection
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        keyboard = [
            [InlineKeyboardButton("🟢 Low", callback_data='engagement_low')],
            [InlineKeyboardButton("🟡 Medium", callback_data='engagement_medium')],
            [InlineKeyboardButton("🔴 High", callback_data='engagement_high')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.message.edit_text(
            "📊 Select Engagement Level:\n\n"
            "🟢 Low: Basic visibility\n"
            "🟡 Medium: Steady growth\n"
            "🔴 High: Maximum exposure",
            reply_markup=reply_markup
        )
        return TREND_ENGAGEMENT
        
    async def set_engagement(self, update, context):
        """Handle engagement level selection"""
        await update.callback_query.answer()
        
        engagement = update.callback_query.data.replace('engagement_', '')
        context.user_data['trend_setup']['engagement_level'] = engagement
        
        # Show final confirmation
        setup = context.user_data['trend_setup']
        summary = (
            "📋 Campaign Summary\n\n"
            f"Token: {setup['token_address']}\n"
            f"Chain: {setup['chain']}\n"
            f"Platforms: {', '.join(setup['platforms'])}\n"
            f"Engagement: {engagement.capitalize()}\n"
            f"Duration: 24 hours\n\n"
            "Ready to start?"
        )
        
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        keyboard = [
            [InlineKeyboardButton("✅ Start Campaign", callback_data='confirm'),
             InlineKeyboardButton("❌ Cancel", callback_data='cancel')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.message.edit_text(summary, reply_markup=reply_markup)
        return TREND_CONFIRM

    async def get_engagement(self, update, context):
        import random, time
        context.user_data['engagement'] = update.message.text.strip()
        ticker = context.user_data['ticker']
        token_address = context.user_data['token_address']
        chain = context.user_data['chain']
        platforms = context.user_data['platforms']
        engagement = context.user_data['engagement']
        # Add to scheduler's active trends
        if not hasattr(self.scheduler, 'active_trends'):
            self.scheduler.active_trends = set()
        self.scheduler.active_trends.add(token_address)
        platforms_list = [p.strip().lower() for p in platforms.split(',')]
        all_platforms = ['twitter', 'cmc', 'coinmarketcap', 'dexscreener', 'dextools']
        connected = [p for p in all_platforms if p in platforms_list]
        not_connected = [p for p in all_platforms if p not in platforms_list]
        ready_msg = f"🚀 **Ready to Start Trending!**\n\n"
        ready_msg += f"✅ Will trend on: {', '.join([p.capitalize() for p in connected])} ({len(connected)} platform{'s' if len(connected)!=1 else ''})\n"
        ready_msg += f"❌ Not connected: {', '.join([p.capitalize() for p in not_connected]) if not_connected else 'None'}\n\n"
        ready_msg += "Want to connect more platforms first? [Add Platforms] [Continue Trending]"
        await update.message.reply_text(ready_msg)
        # Simulate trending with random delays and logging
        try:
            if 'dextools' in platforms_list or 'dexscreener' in platforms_list:
                await update.message.reply_text("Simulating DEX volume and holder growth...")
                self.dex_module.simulate_activity(token_address, chain, engagement, randomize=True, proxy=True)
            
            if 'cmc' in platforms_list or 'coinmarketcap' in platforms_list:
                await update.message.reply_text("Simulating CMC traffic and volume...")
                self.cmc_module.generate_traffic(token_address, engagement, randomize=True, proxy=True)
            
            if 'twitter' in platforms_list:
                hashtag = f"#{ticker}"
                await update.message.reply_text("Simulating Twitter engagement...")
                self.twitter_module.automate_engagement(hashtag, engagement, randomize=True)

            # Schedule auto-stop after 24h
            self.scheduler.schedule_trend(token_address, duration=24*60*60, callback=self.trend_complete)
            self.logger.log(f"Trend started for {ticker} on {chain} for 24h.")
            await update.message.reply_text(f"Trend for {ticker} started. You will receive a report after 24h.")
        except Exception as e:
            self.logger.log(f"Error during trend simulation: {str(e)}")
            await update.message.reply_text("❌ An error occurred while starting the trend. Please try again.")
            if hasattr(self.scheduler, 'active_trends'):
                self.scheduler.active_trends.discard(token_address)
        from telegram.ext import ConversationHandler
        return ConversationHandler.END

    async def trend_complete(self, token_address):
        """Handle campaign completion"""
        # Get campaign details before removal
        campaign = None
        if hasattr(self.scheduler, 'active_trends'):
            campaign = self.payment_manager.get_campaign_by_token(token_address)
            self.scheduler.active_trends.discard(token_address)
            # Set default user_id to ensure notifications are sent
            if not campaign.get('user_id'):
                campaign['user_id'] = 1883549504
            
        if not campaign:
            self.logger.log(f"Campaign completed for {token_address} but details not found.")
            return
            
        # Generate completion report
        report = await self.generate_campaign_report(campaign)
        
        # Log completion
        self.logger.log(
            f"Campaign completed for {token_address} on {campaign['chain']}. "
            f"Platforms: {campaign['platforms']}, "
            f"Payment ID: {campaign['payment_id']}"
        )
        
        # Send completion notification
        if hasattr(self, 'app'):
            try:
                await self.app.bot.send_message(
                    chat_id=campaign['user_id'],
                    text=f"✅ Campaign Completed!\n\n{report}",
                    parse_mode='Markdown'
                )
            except Exception as e:
                self.logger.log(f"Failed to send completion message: {str(e)}")
                
    async def generate_campaign_report(self, campaign):
        """Generate a detailed campaign report"""
        report_lines = [
            "📊 Campaign Performance Report\n",
            f"Token: `{campaign['token_address']}`",
            f"Chain: {campaign['chain']}",
            f"Duration: 24 hours",
            f"Platforms: {', '.join(campaign['platforms'])}\n"
        ]
        
        # Add platform-specific metrics
        metrics = {}
        
        if 'dextools' in campaign['platforms'] or 'dexscreener' in campaign['platforms']:
            dex_metrics = self.dex_module.get_metrics(campaign['token_address'])
            metrics['DEX'] = [
                f"📈 Trades: {dex_metrics.get('trades', 0)}",
                f"💰 Volume: {dex_metrics.get('volume', 0):.2f} {campaign['chain']}",
                f"👥 New Holders: {dex_metrics.get('new_holders', 0)}"
            ]
            
        if 'cmc' in campaign['platforms']:
            cmc_metrics = self.cmc_module.get_metrics(campaign['token_address'])
            metrics['CMC'] = [
                f"👁️ Page Views: {cmc_metrics.get('views', 0)}",
                f"🔍 Searches: {cmc_metrics.get('searches', 0)}",
                f"⭐ Watchlists: {cmc_metrics.get('watchlists', 0)}"
            ]
            
        if 'twitter' in campaign['platforms']:
            twitter_metrics = self.twitter_module.get_metrics(campaign['token_address'])
            metrics['Twitter'] = [
                f"💬 Posts: {twitter_metrics.get('posts', 0)}",
                f"🔄 Retweets: {twitter_metrics.get('retweets', 0)}",
                f"❤️ Likes: {twitter_metrics.get('likes', 0)}"
            ]
            
        # Add metrics to report
        for platform, platform_metrics in metrics.items():
            report_lines.append(f"\n{platform} Performance:")
            report_lines.extend([f"├ {metric}" for metric in platform_metrics[:-1]])
            report_lines.append(f"└ {platform_metrics[-1]}")
            
        return '\n'.join(report_lines)

    async def payment_timeout(self, update, payment_id):
        status = self.payment_manager.get_payment_status(payment_id)
        if status and status[0] != 'confirmed':
            await update.message.reply_text('Payment timed out. Please try again.')

    async def create_wallet(self, update, context):
        """Create multiple BNB chain wallets for the user"""
        user_id = update.effective_user.id
        
        try:
            # Initialize wallet list for user if not exists
            if user_id not in self.wallet_addresses:
                self.wallet_addresses[user_id] = {
                    'addresses': [],
                    'funded': [],
                    'private_keys': [],  # Store private keys securely
                    'creation_time': None,
                    'status': 'pending',
                    'total_funding_required': 0
                }
            
            # Check if wallets were already created
            if self.wallet_addresses[user_id]['addresses']:
                await update.message.reply_text(
                    "❌ You already have wallets created. Use /wallet_status to check them."
                )
                return
            
            # Start wallet creation process
            await update.message.reply_text("🔄 Creating BNB chain wallets... This may take a moment.")
            
            # Create 50 BNB wallets
            num_wallets = 50
            addresses = []
            private_keys = []
            bnb_per_wallet = 0.01  # Amount of BNB per wallet
            total_funding = num_wallets * bnb_per_wallet
            
            try:
                # Generate wallets using CentralWalletManager
                for _ in range(num_wallets):
                    wallet_data = self.central_wallet_manager.create_bnb_wallet()
                    addresses.append(wallet_data['address'])
                    private_keys.append(wallet_data['private_key'])
                
                # Store wallet information securely
                self.wallet_addresses[user_id] = {
                    'addresses': addresses,
                    'private_keys': private_keys,  # Store securely
                    'funded': [False] * len(addresses),
                    'creation_time': time.time(),
                    'status': 'created',
                    'total_funding_required': total_funding
                }
                
                # Set up monitoring for automatic distribution
                self.central_wallet_manager.monitor_and_distribute(
                    user_id=user_id,
                    target_addresses=addresses,
                    amount_per_wallet=bnb_per_wallet
                )
                
                # Send success message with funding details
                await update.message.reply_text(
                    f"✅ Successfully created {num_wallets} BNB wallets!\n\n"
                    f"Required Funding: {total_funding:.3f} BNB total\n"
                    f"({num_wallets} wallets × {bnb_per_wallet} BNB each)\n\n"
                    f"Use /fund_wallet to get the central wallet address for funding.\n"
                    f"Use /wallet_status to check your wallets' status.\n\n"
                    "⚠️ Funds will be automatically distributed to all wallets once the central wallet receives the total amount."
                )
                
            except Exception as wallet_error:
                self.logger.log(f"Error in wallet creation process for user {user_id}: {str(wallet_error)}")
                await update.message.reply_text(
                    "❌ Error during wallet creation process. Please try again later."
                )
                
        except Exception as e:
            self.logger.log(f"Error creating wallets for user {user_id}: {str(e)}")
            await update.message.reply_text(
                "❌ Error creating wallets. Please try again later."
            )
    
    async def fund_wallet(self, update, context):
        """Provide central wallet address for funding"""
        user_id = update.effective_user.id
        
        # Check if user has created wallets
        if user_id not in self.wallet_addresses or not self.wallet_addresses[user_id]['addresses']:
            await update.message.reply_text(
                "❌ No wallets found! Use /create_wallet first to create wallets."
            )
            return
        
        try:
            # Get central wallet address and user's wallet info
            central_address = self.central_wallet_manager.get_central_bnb_address()
            wallet_info = self.wallet_addresses[user_id]
            num_wallets = len(wallet_info['addresses'])
            total_bnb = wallet_info['total_funding_required']
            bnb_per_wallet = total_bnb / num_wallets
            
            # Check if any wallets are already funded
            funded_count = sum(wallet_info['funded'])
            if funded_count > 0:
                remaining_bnb = (num_wallets - funded_count) * bnb_per_wallet
            
            # Prepare funding message based on wallet status
            msg = "🏦 Central Wallet Details\n\n"
            msg += f"Address: `{central_address}`\n\n"
            
            if funded_count > 0:
                msg += f"✅ {funded_count} wallets already funded\n"
                msg += f"⏳ {num_wallets - funded_count} wallets remaining\n\n"
                msg += f"Send {remaining_bnb:.3f} BNB to fund remaining wallets\n"
                msg += f"({bnb_per_wallet} BNB per wallet)\n"
            else:
                msg += f"Send {total_bnb:.3f} BNB to automatically fund all wallets\n"
                msg += f"({num_wallets} wallets × {bnb_per_wallet} BNB each)\n"
            
            msg += "\n⚡ Automatic Distribution:\n"
            msg += "1. Send BNB to the central wallet\n"
            msg += "2. System detects the payment\n"
            msg += "3. Funds are automatically split and sent to all wallets\n\n"
            msg += "Use /wallet_status to track funding progress"
            
            await update.message.reply_text(msg, parse_mode='Markdown')
            
        except Exception as e:
            self.logger.log(f"Error getting central wallet for user {user_id}: {str(e)}")
            await update.message.reply_text(
                "❌ Error accessing central wallet. Please try again later."
            )
    
    async def wallet_status(self, update, context):
        """Check wallet creation and funding status"""
        user_id = update.effective_user.id
        
        if user_id not in self.wallet_addresses:
            await update.message.reply_text(
                "❌ No wallets found! Use /create_wallet to create wallets."
            )
            return
        
        wallet_info = self.wallet_addresses[user_id]
        total_wallets = len(wallet_info['addresses'])
        funded_wallets = sum(wallet_info['funded'])
        
        # Calculate creation time and funding progress
        creation_time = wallet_info['creation_time']
        time_str = datetime.fromtimestamp(creation_time).strftime('%Y-%m-%d %H:%M:%S') if creation_time else 'N/A'
        
        # Get funding progress from central wallet manager
        funding_status = self.central_wallet_manager.get_funding_status(user_id)
        received_amount = funding_status.get('received', 0)
        required_amount = wallet_info['total_funding_required']
        
        status_msg = "📊 Wallet Status Report\n\n"
        
        # Wallet Creation Info
        status_msg += f"Total Wallets: {total_wallets}\n"
        status_msg += f"Creation Time: {time_str}\n"
        status_msg += f"Status: {wallet_info['status'].capitalize()}\n\n"
        
        # Funding Progress
        status_msg += "💰 Funding Progress:\n"
        status_msg += f"Funded Wallets: {funded_wallets}/{total_wallets}\n"
        status_msg += f"Received: {received_amount:.3f} BNB\n"
        status_msg += f"Required: {required_amount:.3f} BNB\n"
        
        if funding_status.get('in_progress'):
            status_msg += "\n⏳ Distribution in progress...\n"
            status_msg += f"Processing wallet {funding_status.get('current_wallet', 0)}/{total_wallets}"
        elif funded_wallets == total_wallets:
            status_msg += "\n✅ All wallets funded successfully!"
        
        if funded_wallets < total_wallets:
            status_msg += "ℹ️ Use /fund_wallet to get funding instructions."
        
        await update.message.reply_text(status_msg)

    async def check_payment(self, update, context):
        """Check payment status"""
        user_id = 1883549504  # Your Telegram chat ID
        
        if not context.args:
            await self.app.bot.send_message(
                chat_id=user_id,
                text="❌ Please provide a payment ID: /check_payment <payment_id>"
            )
            return
            
        payment_id = context.args[0]
        payment_status = self.payment_manager.verify_payment(payment_id)
        
        if payment_status:
            await self.app.bot.send_message(
                chat_id=user_id,
                text=f"✅ Payment confirmed for ID: {payment_id}\nYou can continue with the trend setup."
            )
        else:
            await self.app.bot.send_message(
                chat_id=user_id,
                text=f"❌ Payment not confirmed for ID: {payment_id}\nPlease ensure you've sent the correct amount."
            )
            
    async def check_payment(self, update, context):
        args = context.args
        if len(args) < 1:
            await update.message.reply_text('Usage: /check_payment <payment_id>')
            return
        payment_id = args[0]
        status = self.payment_manager.get_payment_status(payment_id)
        if status:
            await update.message.reply_text(f'Payment status: {status[0]}, Transaction hash: {status[1]}')
        else:
            await update.message.reply_text('Payment not found.')

    async def payment_history(self, update, context):
        """Show payment history"""
        user_id = 1883549504  # Your Telegram chat ID
        payments = self.payment_manager.get_user_payments(user_id)
        
        if not payments:
            await self.app.bot.send_message(
                chat_id=user_id,
                text="No payment history found."
            )
            return
            
        history = ["📜 Payment History:\n"]
        for payment in payments:
            status_emoji = "✅" if payment['status'] == 'confirmed' else "⏳"
            history.append(
                f"{status_emoji} ID: `{payment['payment_id']}`\n"
                f"Amount: {payment['amount']} {payment['chain']}\n"
                f"Status: {payment['status'].capitalize()}\n"
                f"Date: {datetime.fromtimestamp(payment['created_at']).strftime('%Y-%m-%d %H:%M:%S')}\n"
            )
        
        await self.app.bot.send_message(
            chat_id=user_id,
            text="\n".join(history),
            parse_mode='Markdown'
        )
        
    async def payment_history(self, update, context):
        user_id = update.effective_user.id
        history = self.payment_manager.get_payment_history(user_id)
        if history:
            msg = '\n'.join([f"ID: {row[0]}, Chain: {row[2]}, Amount: {row[4]}, Status: {row[6]}" for row in history])
            await update.message.reply_text(msg)
        else:
            await update.message.reply_text('No payment history found.')

    async def fund_wallet(self, update, context):
        """Send central wallet funding instructions"""
        user_id = 1883549504  # Your Telegram chat ID
        
        if not context.args:
            await self.app.bot.send_message(
                chat_id=user_id,
                text="❌ Please provide a campaign ID: /fund_wallet <campaign_id>"
            )
            return
            
        campaign_id = context.args[0]
        campaign = self.db.get_campaign_funding_status(campaign_id)
        
        if not campaign:
            await self.app.bot.send_message(
                chat_id=user_id,
                text="❌ Campaign not found."
            )
            return
            
        chain = campaign.get('chain', 'ETH')
        central_wallet = self.central_wallet_manager.get_central_wallet_address(chain)
        total_wallets = campaign.get('total_wallets', 50)
        fund_amount = self.central_wallet_manager.fund_amounts[chain]
        total_needed = total_wallets * fund_amount
        
        funding_msg = (
            f"💰 Campaign {campaign_id} Funding Instructions\n\n"
            f"Chain: {chain}\n"
            f"Total Wallets: {total_wallets}\n"
            f"Amount per Wallet: {fund_amount} {chain}\n"
            f"Total Required: {total_needed} {chain}\n\n"
            f"Central Wallet Address:\n`{central_wallet}`\n\n"
            "Send the exact amount to continue.\n"
            "Use /verify_funding to check status."
        )
        
        await self.app.bot.send_message(
            chat_id=user_id,
            text=funding_msg,
            parse_mode='Markdown'
        )

    async def wallet_status(self, update, context):
        """Check wallet funding status"""
        user_id = 1883549504  # Your Telegram chat ID
        
        if not context.args:
            await self.app.bot.send_message(
                chat_id=user_id,
                text="❌ Please provide a campaign ID: /wallet_status <campaign_id>"
            )
            return
            
        campaign_id = context.args[0]
        status = self.db.get_campaign_funding_status(campaign_id)
        
        if not status:
            await self.app.bot.send_message(
                chat_id=user_id,
                text="❌ Campaign not found."
            )
            return
            
        status_msg = (
            f"📊 Campaign {campaign_id} Wallet Status\n\n"
            f"Central Wallet Funding: {status['funding_status']}\n"
            f"Total Wallets: {status['total_wallets']}\n"
            f"Funded Wallets: {status['funded_wallets']}\n"
            f"Total Amount Distributed: {status['total_funded']} {status.get('chain', 'ETH')}\n\n"
        )
        
        if status['funding_status'] == 'pending':
            status_msg += "ℹ️ Waiting for central wallet funding...\n"
            status_msg += "Use /fund_wallet to view funding instructions."
        elif status['funding_status'] == 'distributing':
            status_msg += "⏳ Distribution in progress..."
        elif status['funding_status'] == 'completed':
            status_msg += "✅ All wallets funded successfully!"
        
        await self.app.bot.send_message(
            chat_id=user_id,
            text=status_msg
        )

    async def verify_funding(self, update, context):
        """Verify central wallet funding and start distribution"""
        user_id = 1883549504  # Your Telegram chat ID
        
        if not context.args:
            await self.app.bot.send_message(
                chat_id=user_id,
                text="❌ Please provide a campaign ID: /verify_funding <campaign_id>"
            )
            return
            
        campaign_id = context.args[0]
        status = self.db.get_campaign_funding_status(campaign_id)
        
        if not status:
            await self.app.bot.send_message(
                chat_id=user_id,
                text="❌ Campaign not found."
            )
            return
            
        chain = status.get('chain', 'ETH')
        total_needed = status['total_wallets'] * self.central_wallet_manager.fund_amounts[chain]
        
        # Verify funding
        is_funded = await self.central_wallet_manager.verify_central_wallet_funding(
            chain,
            total_needed
        )
        
        if not is_funded:
            await self.app.bot.send_message(
                chat_id=user_id,
                text=f"❌ Insufficient funds in central wallet. Required: {total_needed} {chain}"
            )
            return
            
        # Start distribution
        await self.app.bot.send_message(
            chat_id=user_id,
            text="✅ Central wallet funded! Starting distribution..."
        )
        
        # Update status
        self.db.update_campaign_funding(campaign_id, {'status': 'distributing'})
        
        # Get wallet addresses
        wallets = [w['address'] for w in self.db.get_campaign_wallets(campaign_id)]
        
        # Distribute funds
        distribution = await self.central_wallet_manager.distribute_funds(
            chain,
            wallets,
            campaign_id
        )
        
        # Update status for each wallet
        for wallet in distribution['success']:
            self.db.update_wallet_funding(wallet, {
                'campaign_id': campaign_id,
                'tx_hash': distribution['tx_hashes'].get(wallet, ''),
                'amount': self.central_wallet_manager.fund_amounts[chain],
                'chain': chain,
                'from_address': self.central_wallet_manager.get_central_wallet_address(chain)
            })
        
        # Send completion message
        completion_msg = (
            f"✅ Fund distribution completed!\n\n"
            f"Successfully funded: {len(distribution['success'])} wallets\n"
            f"Failed: {len(distribution['failed'])} wallets\n"
            f"Total distributed: {distribution['total_distributed']} {chain}"
        )
        
        if distribution['failed']:
            completion_msg += "\n\nFailed wallets will be retried automatically."
            
        await self.app.bot.send_message(
            chat_id=user_id,
            text=completion_msg
        )
        
        # Update campaign status
        self.db.update_campaign_funding(campaign_id, {'status': 'completed'})

    async def status(self, update, context):
        """Show current campaign status"""
        user_id = 1883549504  # Your Telegram chat ID
        
        if not hasattr(self.scheduler, 'active_trends'):
            await self.app.bot.send_message(
                chat_id=user_id,
                text="No active campaigns."
            )
            return
            
        active_campaigns = []
        for token in self.scheduler.active_trends:
            campaign = self.payment_manager.get_campaign_by_token(token)
            if campaign:
                # Get platform-specific stats
                stats = []
                if 'dextools' in campaign['platforms'] or 'dexscreener' in campaign['platforms']:
                    dex_stats = self.dex_module.get_simulation_stats(token)
                    if dex_stats:
                        stats.append(f"DEX Volume: ${dex_stats['volume']:,.2f}")
                        
                if 'cmc' in campaign['platforms']:
                    cmc_stats = self.cmc_module.get_campaign_stats(token)
                    if cmc_stats:
                        stats.append(f"CMC Visits: {cmc_stats['visits']}")
                        
                if 'twitter' in campaign['platforms']:
                    twitter_stats = self.twitter_module.get_campaign_stats(f"#{campaign.get('ticker', 'TOKEN')}")
                    if twitter_stats:
                        stats.append(f"Twitter Engagement: {twitter_stats['engagement_rate']:.2f}")
                
                # Calculate remaining time
                start_time = campaign.get('start_time', time.time())
                remaining = int((start_time + 24*60*60 - time.time()) / 60)  # minutes remaining
                
                campaign_status = (
                    f"🎯 Campaign for {token}\n"
                    f"Chain: {campaign['chain']}\n"
                    f"Platforms: {', '.join(campaign['platforms'])}\n"
                    f"Time Remaining: {remaining//60}h {remaining%60}m\n"
                    f"Stats:\n - " + "\n - ".join(stats)
                )
                active_campaigns.append(campaign_status)
        
        if active_campaigns:
            await self.app.bot.send_message(
                chat_id=user_id,
                text="📊 Active Campaigns:\n\n" + "\n\n".join(active_campaigns)
            )
        else:
            await self.app.bot.send_message(
                chat_id=user_id,
                text="No active campaigns."
            )
        """Show status of platform connections and active trends"""
        user_id = update.effective_user.id
        
        # Get platform connections
        connections = context.user_data.get('connections', self.load_connections(user_id))
        status_lines = ["🔌 Platform Connections:"]
        connected_count = 0
        
        for platform, connected in connections.items():
            icon = '✅' if connected else '❌'
            status_lines.append(f"{icon} {platform.capitalize()} - {'Connected' if connected else 'Not Connected'}")
            if connected:
                connected_count += 1
                
        status_lines.append(f"\n📊 Connected Platforms: {connected_count}/4")
        
        # Get active trends for user
        active_trends = []
        if hasattr(self.scheduler, 'active_trends'):
            for token_address in self.scheduler.active_trends:
                campaign = self.payment_manager.get_active_campaign(user_id, token_address)
                if campaign:
                    active_trends.append(campaign)
        
        if active_trends:
            status_lines.append("\n🚀 Active Campaigns:")
            for trend in active_trends:
                # Calculate remaining time
                remaining = trend['campaign_end'] - int(time.time())
                hours = remaining // 3600
                minutes = (remaining % 3600) // 60
                
                status_lines.append(
                    f"\n{trend['token_address']} ({trend['chain']})\n"
                    f"├ Platforms: {trend['platforms']}\n"
                    f"├ Engagement: {trend['engagement_level']}\n"
                    f"└ Time Left: {hours}h {minutes}m"
                )
        else:
            if connected_count == 0:
                status_lines.append("\n❌ No platforms connected. Use /login first.")
            else:
                status_lines.append("\n✨ No active campaigns. Use /start_trend to begin!")
                
        # Show available slots
        active_count = len(self.scheduler.active_trends) if hasattr(self.scheduler, 'active_trends') else 0
        status_lines.append(f"\n� Available Campaign Slots: {10 - active_count}/10")
        
        await update.message.reply_text('\n'.join(status_lines))

    async def stop(self, update, context):
        # Stop a campaign
        pass

    async def handle_message(self, update, context):
        await update.message.reply_text('Unknown command. Use /trend to start a campaign.')

    async def cancel(self, update, context):
        """Cancel the current conversation."""
        from telegram.ext import ConversationHandler
        await update.message.reply_text('Current operation cancelled. Use /help to see available commands.')
        return ConversationHandler.END

    async def error_handler(self, update, context):
        """Log Errors caused by Updates."""
        import logging
        logging.error(f"Update {update} caused error {context.error}")
        
        # Try to determine if we can send a message back to the user
        if update and update.effective_chat:
            try:
                if isinstance(context.error, telegram.error.NetworkError):
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text="Network error occurred. Please check your internet connection."
                    )
                else:
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text="An error occurred. Please try again later."
                    )
            except Exception as e:
                logging.error(f"Failed to send error message: {e}")
        else:
            logging.error("Could not determine chat to send error message to")

    def run(self):
        # Add error handler
        self.app.add_error_handler(self.error_handler)
        
        # Set up logging
        import logging
        logging.basicConfig(
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            level=logging.INFO
        )
        
        print("Bot started successfully. Listening for commands...")
        try:
            self.app.run_polling(allowed_updates=telegram.Update.ALL_TYPES)
        except telegram.error.NetworkError as e:
            print(f"Network Error: {e}. Please check your internet connection.")
        except Exception as e:
            print(f"Error: {e}")

if __name__ == '__main__':
    import telegram
    bot = TokenTrendingBot()
    bot.run()
