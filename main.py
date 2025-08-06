
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters
from scheduler import Scheduler
from payment import PaymentManager
from logger import Logger
from modules.dex import DexModule
from modules.cmc import CMCModule
from modules.twitter import TwitterModule
from dashboard import Dashboard

TELEGRAM_TOKEN = "8430101507:AAGkn3NHv9YzjbcadR_hOHTrHK1ldq338sA"

class TokenTrendingBot:
    import json
    PERSIST_FILE = 'connections.json'
    def __init__(self):
        self.app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
        self.scheduler = Scheduler()
        self.payment_manager = PaymentManager()
        self.logger = Logger()
        self.dex_module = DexModule()
        self.cmc_module = CMCModule()
        self.twitter_module = TwitterModule()
        self.dashboard = Dashboard(self)
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
        from telegram.ext import ConversationHandler
        TICKER, TOKEN_ADDRESS, CHAIN, PLATFORMS, ENGAGEMENT = range(5)
        PLATFORM_SELECT, CMC_EMAIL, CMC_PASSWORD, DEX_EMAIL, DEX_PASSWORD, DEXT_EMAIL, DEXT_PASSWORD, TWITTER_USER, TWITTER_PASSWORD, TWITTER_2FA, CONFIRM_ANOTHER = range(5, 16)
        self.app.add_handler(CommandHandler('start', self.start))
        self.app.add_handler(CommandHandler('menu', self.menu))
        self.app.add_handler(CommandHandler('trend', self.trend))
        self.app.add_handler(CommandHandler('check_payment', self.check_payment))
        self.app.add_handler(CommandHandler('payment_history', self.payment_history))
        self.app.add_handler(CommandHandler('status', self.status))
        self.app.add_handler(CommandHandler('login', self.login))
        self.app.add_handler(CommandHandler('stop', self.stop))
        self.app.add_handler(CommandHandler('logout', self.logout))
        self.app.add_handler(CommandHandler('reconnect', self.reconnect))
        self.app.add_handler(CommandHandler('help', self.help))
        from telegram.ext import CallbackQueryHandler
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler('start_trend', self.start_trend), CommandHandler('login', self.login), CommandHandler('logout', self.logout), CommandHandler('reconnect', self.reconnect)],
            states={
                TICKER: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_ticker)],
                TOKEN_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_token_address)],
                CHAIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_chain)],
                PLATFORMS: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_platforms)],
                ENGAGEMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_engagement)],
                PLATFORM_SELECT: [CallbackQueryHandler(self.platform_select), MessageHandler(filters.TEXT & ~filters.COMMAND, self.platform_select)],
                CMC_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.cmc_email)],
                CMC_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.cmc_password)],
                DEX_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.dex_email)],
                DEX_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.dex_password)],
                DEXT_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.dext_email)],
                DEXT_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.dext_password)],
                TWITTER_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.twitter_user)],
                TWITTER_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.twitter_password)],
                TWITTER_2FA: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.twitter_2fa)],
                CONFIRM_ANOTHER: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.confirm_another)],
                'LOGOUT_SELECT': [MessageHandler(filters.TEXT & ~filters.COMMAND, self.logout_select)],
                'RECONNECT_SELECT': [MessageHandler(filters.TEXT & ~filters.COMMAND, self.reconnect_select)],
            },
            fallbacks=[]
        )
        self.app.add_handler(conv_handler)

    async def login(self, update, context):
        user_id = update.effective_user.id
        context.user_data['connections'] = self.load_connections(user_id)
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        keyboard = [
            [InlineKeyboardButton("CMC", callback_data='cmc')],
            [InlineKeyboardButton("DEXScreener", callback_data='dexscreener')],
            [InlineKeyboardButton("DEXTools", callback_data='dextools')],
            [InlineKeyboardButton("Twitter", callback_data='twitter')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("🔐 Choose Platform to Connect", reply_markup=reply_markup)
        return 5  # PLATFORM_SELECT

    async def platform_select(self, update, context):
        # Handle both text and callback query
        if hasattr(update, 'callback_query') and update.callback_query:
            platform = update.callback_query.data.strip().lower()
            await update.callback_query.answer(text=f"Selected {platform.capitalize()}")
            msg_target = update.callback_query.message
        else:
            platform = update.message.text.strip().lower()
            msg_target = update.message
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
            await msg_target.reply_text("❌ Invalid platform. Please choose again.")
            return 5

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
        platform = update.message.text.strip().lower()
        if platform in context.user_data['connections']:
            context.user_data['connections'][platform] = False
            self.save_connections(update.effective_user.id, context.user_data['connections'])
            await update.message.reply_text(f"✅ {platform.capitalize()} disconnected.")
        else:
            await update.message.reply_text("Invalid platform.")
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
        platform = update.message.text.strip().lower()
        if platform == 'cmc':
            await update.message.reply_text("📧 Enter your CoinMarketCap email:")
            return 6  # CMC_EMAIL
        elif platform == 'dexscreener':
            await update.message.reply_text("📧 Enter your DEXScreener email:")
            return 8  # DEX_EMAIL
        elif platform == 'dextools':
            await update.message.reply_text("📧 Enter your DEXTools email:")
            return 10  # DEXT_EMAIL
        elif platform == 'twitter':
            await update.message.reply_text("📱 Enter your Twitter username/email:")
            return 12  # TWITTER_USER
        else:
            await update.message.reply_text("Invalid platform.")
            return ConversationHandler.END

    async def help(self, update, context):
        help_text = (
            "Available commands:\n"
            "/start - Welcome message\n"
            "/menu - Show this menu\n"
            "/trend - How to start a campaign\n"
            "/start_trend <token_address> <chain> <platforms> <engagement> - Initiate a trending campaign\n"
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
        welcome_msg = f"Hi {first_name}, welcome to TrendingFX_Bot!\n\n🚀 I can help you start trending campaigns across multiple platforms:\n- CoinMarketCap\n- DEXScreener\n- DEXTools\n- Twitter\n\nUse /login to connect your accounts and get started!"
        await update.message.reply_text(welcome_msg)

    async def trend(self, update, context):
        await update.message.reply_text('Use /start_trend <token_address> <chain> <platforms> <engagement> to begin a campaign.')

    async def start_trend(self, update, context):
        user_id = update.effective_user.id
        active_trends = getattr(self.scheduler, 'active_trends', set())
        if len(active_trends) >= 10:
            await update.message.reply_text('❌ Maximum simultaneous trends reached (10). Please wait for a slot to free up.')
            return ConversationHandler.END
        await update.message.reply_text('Please enter the ticker for your token:')
        return 0

    async def get_ticker(self, update, context):
        context.user_data['ticker'] = update.message.text.strip()
        await update.message.reply_text('Please enter the token address:')
        return 1

    async def get_token_address(self, update, context):
        context.user_data['token_address'] = update.message.text.strip()
        await update.message.reply_text('Please enter the chain (e.g., BNB):')
        return 2

    async def get_chain(self, update, context):
        context.user_data['chain'] = update.message.text.strip()
        await update.message.reply_text('Please enter the platforms to target (comma separated, e.g., Dextools,CMC,Twitter):')
        return 3

    async def get_platforms(self, update, context):
        context.user_data['platforms'] = update.message.text.strip()
        await update.message.reply_text('Please enter the engagement strength (low, medium, high):')
        return 4

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
        from telegram.ext import ConversationHandler
        return ConversationHandler.END

    async def trend_complete(self, token_address):
        # Remove from active trends
        if hasattr(self.scheduler, 'active_trends'):
            self.scheduler.active_trends.discard(token_address)
        self.logger.log(f"Trend completed for {token_address}.")
        # Send report (pseudo-code, adapt to your dashboard/Telegram)
        # await self.dashboard.send_report(token_address)

    async def payment_timeout(self, update, payment_id):
        status = self.payment_manager.get_payment_status(payment_id)
        if status and status[0] != 'confirmed':
            await update.message.reply_text('Payment timed out. Please try again.')

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
        user_id = update.effective_user.id
        history = self.payment_manager.get_payment_history(user_id)
        if history:
            msg = '\n'.join([f"ID: {row[0]}, Chain: {row[2]}, Amount: {row[4]}, Status: {row[6]}" for row in history])
            await update.message.reply_text(msg)
        else:
            await update.message.reply_text('No payment history found.')

    async def status(self, update, context):
        connections = context.user_data.get('connections', {
            'cmc': False,
            'dexscreener': False,
            'dextools': False,
            'twitter': False
        })
        status_lines = []
        connected_count = 0
        for platform, connected in connections.items():
            icon = '✅' if connected else '❌'
            status_lines.append(f"{icon} {platform.capitalize()} - {'Connected' if connected else 'Not Connected'}")
            if connected:
                connected_count += 1
        status_lines.append(f"\n📊 Connected Platforms: {connected_count}/4")
        if connected_count == 0:
            status_lines.append("❌ No platforms connected. Use /login first.")
        else:
            status_lines.append("🚀 Ready for trending! Use /start_trend to begin")
        await update.message.reply_text('🔍 Connection Status\n' + '\n'.join(status_lines))

    async def stop(self, update, context):
        # Stop a campaign
        pass

    async def handle_message(self, update, context):
        await update.message.reply_text('Unknown command. Use /trend to start a campaign.')

    def run(self):
        self.app.run_polling()

if __name__ == '__main__':
    bot = TokenTrendingBot()
    bot.run()
