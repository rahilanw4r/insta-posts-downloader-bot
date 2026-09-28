"""One-click Instagram post/reel downloader Telegram bot.

Downloads individual Instagram post/reel URLs that yt-dlp can access.
Does not scrape whole profiles, bypass login, or evade access controls.
"""
import asyncio
import logging
import os
import re
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
)
log = logging.getLogger("ig-one-click")
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ALLOWED_HOSTS = {"instagram.com", "www.instagram.com", "m.instagram.com"}
MAX_FILE_BYTES = 45 * 1024 * 1024
URL_RE = re.compile(r"https?://[^\s<>]+")


def valid_post_url(raw: str) -> str | None:
    raw = (raw or "").strip().rstrip(".,!?)]}")
    if len(raw) > 2048:
        return None
    try:
        p = urlparse(raw)
    except ValueError:
        return None
    if p.scheme not in {"http", "https"} or (p.hostname or "").lower() not in ALLOWED_HOSTS:
        return None
    parts = [x for x in p.path.split("/") if x]
    if len(parts) < 2 or parts[0].lower() not in {"p", "reel", "reels", "tv"}:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_-]+", parts[1]):
        return None
    return raw


def download_post(url: str, folder: str) -> list[Path]:
    opts = {
        "outtmpl": str(Path(folder) / "%(id)s_%(playlist_index)s.%(ext)s"),
        "format": "best[filesize<45M]/best",
        "noplaylist": False,
        "ignoreerrors": False,
        "quiet": True,
        "no_warnings": True,
        "retries": 2,
        "socket_timeout": 25,
        "max_filesize": MAX_FILE_BYTES,
        "restrictfilenames": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    return [p for p in Path(folder).iterdir() if p.is_file() and p.suffix.lower() not in {".part", ".ytdl"}]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "Instagram One-Click Downloader\n\n"
        "Send me a public Instagram post, Reel, or carousel URL. "
        "I’ll try to download its media and send it here.\n\n"
        "This handles individual post links—not full profile archives. "
        "Some posts may require login or be unavailable to the downloader. "
        "Only download content you have permission to save."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg or not msg.text:
        return
    match = URL_RE.search(msg.text)
    if not match:
        await msg.reply_text("Paste an Instagram post or Reel URL to download.")
        return
    url = valid_post_url(match.group(0))
    if not url:
        await msg.reply_text("That link doesn’t look like an Instagram post/Reel. Send a /p/, /reel/, /reels/, or /tv/ link.")
        return

    status = await msg.reply_text("Link received. Trying to fetch the media…")
    temp = tempfile.TemporaryDirectory(prefix="igdl_")
    try:
        try:
            files = await asyncio.to_thread(download_post, url, temp.name)
        except Exception as exc:
            log.warning("Instagram fetch failed: %s", type(exc).__name__)
            await status.edit_text(
                "Couldn’t fetch this post. Instagram may require login, restrict automated access, "
                "or the URL may be unavailable. Try a publicly accessible post link; don’t send account passwords or session cookies."
            )
            return

        files = [p for p in files if p.exists() and p.stat().st_size <= MAX_FILE_BYTES]
        if not files:
            await status.edit_text("No downloadable media was found, or the media exceeds the bot’s 45 MiB per-file limit.")
            return

        if len(files) == 1:
            await status.edit_text("Download complete. Sending media…")
            with files[0].open("rb") as stream:
                await msg.reply_document(document=stream, filename=files[0].name, read_timeout=120, write_timeout=120)
        else:
            archive = Path(temp.name) / "instagram_media.zip"
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=5) as z:
                for f in files:
                    z.write(f, arcname=f.name)
            if archive.stat().st_size > MAX_FILE_BYTES:
                await status.edit_text("This carousel is too large to send as one ZIP (45 MiB limit).")
                return
            await status.edit_text(f"Downloaded {len(files)} media items. Sending ZIP…")
            with archive.open("rb") as stream:
                await msg.reply_document(document=stream, filename="instagram_media.zip", read_timeout=120, write_timeout=120)
        await status.delete()
    except Exception:
        log.exception("Media delivery failed")
        await status.edit_text("Media was fetched, but Telegram delivery failed. Try a smaller post or try again.")
    finally:
        temp.cleanup()


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("Missing BOT_TOKEN environment variable. Set it in the runtime environment.")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    log.info("Polling started")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
