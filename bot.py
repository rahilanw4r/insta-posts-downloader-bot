import logging
import os
import re
from urllib.parse import urlparse

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
INSTAGRAM_HOSTS = {"instagram.com", "www.instagram.com", "m.instagram.com"}
URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)


def valid_instagram_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        return parsed.scheme in {"http", "https"} and (parsed.hostname or "").lower() in INSTAGRAM_HOSTS
    except ValueError:
        return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Welcome! Send an Instagram post, Reel, or carousel URL.\n\n"
        "Note: this initial version checks links only; media downloading is not enabled yet. "
        "Only save content you own or have permission to download."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Send a public Instagram URL to have its format checked. "
        "Private or login-restricted content is not accessed. Downloading is not implemented in this starter."
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    found = URL_RE.findall(message.text or "")
    cleaned = [item.rstrip(".,!?)]}") for item in found]
    url = next((item for item in cleaned if valid_instagram_url(item)), None)
    if not url:
        await message.reply_text("Please send a valid link from instagram.com.")
        return
    await message.reply_text(
        "Instagram link recognized. The downloader feature is not implemented in this starter yet. "
        "Use only content you own or have permission to save; private/login-restricted media won't be accessed."
    )


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("Missing BOT_TOKEN environment variable. Set it before running the bot.")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info("Starting bot polling")
    app.run_polling()


if __name__ == "__main__":
    main()
