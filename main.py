import os
import json
import logging
import traceback
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from dotenv import load_dotenv
import nest_asyncio

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

        # Register command handlers
        self.app.add_handler(CommandHandler("start", self.start))
        self.app.add_handler(CommandHandler("menu", self.menu))
        self.app.add_handler(CommandHandler("create_wallet", self.create_wallet))

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Sends a welcome message when the /start command is issued."""
        await update.message.reply_text(
            "Welcome to the DexScreener Trending Bot!\n"
            "Use /menu to see available commands."
        )

    async def menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Displays the main menu of commands."""
        menu_text = (
            "Available Commands:\n"
            "/create_wallet - Load wallets from the pre-generated wallets.json file.\n"
            "/start - Show the welcome message.\n"
            "/menu - Display this menu."
        )
        await update.message.reply_text(menu_text)

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

            response_lines = [f"✅ Found {len(addresses)} pre-generated wallets."]
            response_lines.extend(addresses)
            
            message_chunk = ""
            for line in response_lines:
                if len(message_chunk) + len(line) + 1 > 4096:
                    await update.message.reply_text(message_chunk)
                    message_chunk = ""
                message_chunk += line + "\\n"
            if message_chunk:
                await update.message.reply_text(message_chunk.strip())

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