"""Instagram link and public-profile Reels downloader Telegram bot.

Accepts a post/Reel URL for a single post, or @username/profile URL to archive
public Reels using Instaloader. Does not log in or bypass access controls.
"""
import asyncio
import logging
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    level=os.getenv("LOG_LEVEL", "INFO").upper())
log = logging.getLogger("ig-one-click")
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
MAX_FILE_BYTES = 45 * 1024 * 1024
URL_RE = re.compile(r"https?://[^\\s<>]+")
USERNAME_RE = re.compile(r"^@?([A-Za-z0-9._]{1,30})$")
ALLOWED_HOSTS = {"instagram.com", "www.instagram.com", "m.instagram.com"}


def instagram_url(raw):
    raw = raw.strip().rstrip(".,!?)]}")
    try:
        p = urlparse(raw)
    except ValueError:
        return None
    if p.scheme not in {"http", "https"} or (p.hostname or "").lower() not in ALLOWED_HOSTS:
        return None
    return raw, [x for x in p.path.split("/") if x]


def single_post_url(raw):
    parsed = instagram_url(raw)
    if not parsed:
        return None
    url, parts = parsed
    if len(parts) >= 2 and parts[0].lower() in {"p", "reel", "reels", "tv"} and re.fullmatch(r"[A-Za-z0-9_-]+", parts[1]):
        return url
    return None


def profile_username(raw):
    parsed = instagram_url(raw)
    if parsed:
        _, parts = parsed
        if len(parts) == 1 and USERNAME_RE.fullmatch(parts[0]):
            return parts[0]
        return None
    m = USERNAME_RE.fullmatch(raw.strip())
    return m.group(1) if m else None


def download_post(url, folder):
    opts = {
        "outtmpl": str(Path(folder) / "%(id)s_%(playlist_index)s.%(ext)s"),
        "format": "best[filesize<45M]/best", "noplaylist": False,
        "ignoreerrors": False, "quiet": True, "no_warnings": True,
        "retries": 2, "socket_timeout": 25, "max_filesize": MAX_FILE_BYTES,
        "restrictfilenames": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    return [p for p in Path(folder).iterdir() if p.is_file() and p.suffix.lower() not in {".part", ".ytdl"}]


def download_profile_reels(username, folder):
    # Public profile only; no login/session, and no access-control workarounds.
    output = Path(folder) / "profile"
    output.mkdir(parents=True, exist_ok=True)
    cmd = [
        "instaloader", "--reels", "--no-metadata-json", "--no-captions",
        "--no-compress-json", "--dirname-pattern", str(output / "{target}"),
        username,
    ]
    result = subprocess.run(cmd, cwd=folder, capture_output=True, text=True, timeout=900)
    media = [p for p in output.rglob("*") if p.is_file() and p.suffix.lower() in {".mp4", ".jpg", ".jpeg"}]
    if result.returncode and not media:
        raise RuntimeError("Instaloader could not access this public profile.")
    return media


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "Instagram Downloader\\n\\n"
        "Send an Instagram post/Reel URL to download that post, or send @username/profile URL "
        "to archive the public profile’s Reels into a ZIP.\\n"
        "Profile archives can take time and may be large. Public access is not guaranteed; "
        "I do not log in or bypass restrictions. Only save content you have permission to download."
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    raw = msg.text.strip()
    url_match = URL_RE.search(raw)
    if url_match:
        raw_url = url_match.group(0)
        post_url = single_post_url(raw_url)
        username = profile_username(raw_url)
        if post_url:
            kind, value = "post", post_url
        elif username:
            kind, value = "profile", username
        else:
            await msg.reply_text("Send a valid Instagram post/Reel link or profile URL.")
            return
    else:
        username = profile_username(raw)
        if not username:
            await msg.reply_text("Send a public Instagram @username, profile URL, or post/Reel URL.")
            return
        kind, value = "profile", username

    status = await msg.reply_text("Starting download…")
    with tempfile.TemporaryDirectory(prefix="igdl_") as tmp:
        try:
            if kind == "post":
                files = await asyncio.to_thread(download_post, value, tmp)
            else:
                await status.edit_text(f"Fetching public Reels from @{value}. This may take a while…")
                files = await asyncio.to_thread(download_profile_reels, value, tmp)

            files = [p for p in files if p.exists() and p.stat().st_size <= MAX_FILE_BYTES]
            if not files:
                await status.edit_text("No downloadable media found. The profile may be private, restricted, empty, or unavailable.")
                return

            if kind == "post" and len(files) == 1:
                await status.edit_text("Download complete. Sending media…")
                with files[0].open("rb") as stream:
                    await msg.reply_document(document=stream, filename=files[0].name, read_timeout=120, write_timeout=120)
            else:
                archive = Path(tmp) / "instagram_archive.zip"
                with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=5) as z:
                    for p in files:
                        z.write(p, arcname=p.relative_to(Path(tmp)))
                if archive.stat().st_size > MAX_FILE_BYTES:
                    await status.edit_text(
                        f"Collected {len(files)} files, but the ZIP exceeds Telegram’s 45 MiB send limit. "
                        "Try a smaller profile or download individual post links."
                    )
                    return
                await status.edit_text(f"Collected {len(files)} files. Sending ZIP…")
                with archive.open("rb") as stream:
                    await msg.reply_document(document=stream, filename="instagram_archive.zip", read_timeout=120, write_timeout=120)
            await status.delete()
        except subprocess.TimeoutExpired:
            await status.edit_text("Profile download timed out after 15 minutes. Try again later or use individual post links.")
        except Exception as exc:
            log.warning("Download failed (%s)", type(exc).__name__)
            await status.edit_text(
                "Download failed. Instagram may be rate-limiting or restricting access, "
                "or this profile/post may be unavailable. No login bypass is attempted."
            )


def main():
    if not BOT_TOKEN:
        raise SystemExit("Missing BOT_TOKEN environment variable. Set it in the runtime environment.")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    log.info("Polling started")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
