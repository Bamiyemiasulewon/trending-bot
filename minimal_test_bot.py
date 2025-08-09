import asyncio
import nest_asyncio
from telegram import Update

# nest_asyncio allows the bot to run in environments with a pre-existing event loop.
nest_asyncio.apply()

from telegram.ext import Application, CommandHandler, ContextTypes

# Use the same token
TELEGRAM_TOKEN = "8430101507:AAGkn3NHv9YzjbcadR_hOHTrHK1ldq338sA"

async def hello(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A minimal command handler that just sends a reply."""
    print("Executing /hello command.")
    await update.message.reply_text(f'Hello! If you see this, the minimal bot is working.')

async def main() -> None:
    """Starts the minimal bot."""
    print("Starting minimal_test_bot.py...")
    
    # Create the Application and pass it your bot's token.
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Add the command handler
    application.add_handler(CommandHandler("hello", hello))

    print("Bot is running. Send /hello to test.")
    
    # Run the bot until the user presses Ctrl-C
    await application.run_polling()

if __name__ == '__main__':
    # This robust setup prevents the 'event loop is already running' error.
    try:
        # In some environments (like certain IDEs or terminals), an event loop
        # is already running. We need to get it instead of creating a new one.
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # If no loop is running, we create a new one.
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    try:
        print("Starting bot... Press Ctrl+C to stop.")
        # Run the main async function until it's complete.
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        print("\nBot stopped by user.")
    finally:
        print("Cleaning up...")
        # You can add cleanup tasks here if needed
        pass
