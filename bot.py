"""Telegram media archive helper.

Accepts a profile label, then packages media the user sends to the bot into ZIP.
It does not scrape Instagram profiles or bypass platform access controls.
"""
import asyncio
import logging
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, ContextTypes, MessageHandler, filters,
)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
)
log = logging.getLogger("profile-archive")
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
PROFILE_HOSTS = {"instagram.com", "www.instagram.com", "m.instagram.com"}
MAX_ITEMS = 200
MAX_SINGLE_FILE = 45 * 1024 * 1024
MAX_ZIP_BYTES = 45 * 1024 * 1024


@dataclass
class ArchiveSession:
    label: str
    files: list[Path] = field(default_factory=list)
    names: set[str] = field(default_factory=set)
    tempdir: tempfile.TemporaryDirectory | None = None

    def close(self) -> None:
        if self.tempdir:
            self.tempdir.cleanup()
            self.tempdir = None
        self.files.clear()
        self.names.clear()


sessions: dict[int, ArchiveSession] = {}
session_locks: dict[int, asyncio.Lock] = {}


def parse_profile(text: str) -> str | None:
    value = (text or "").strip()
    if not value or len(value) > 2048:
        return None
    if value.startswith("@"):
        value = value[1:]
    elif "://" in value:
        try:
            parsed = urlparse(value)
        except ValueError:
            return None
        if parsed.scheme not in {"http", "https"} or (parsed.hostname or "").lower() not in PROFILE_HOSTS:
            return None
        parts = [part for part in parsed.path.split("/") if part]
        if not parts or parts[0].lower() in {"p", "reel", "reels", "stories", "explore", "accounts"}:
            return None
        value = parts[0]
    value = value.lstrip("@")
    return value if USERNAME_RE.fullmatch(value) else None


def safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return cleaned[:100] or "media"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "Profile Archive Bot\n\n"
        "Send /archive @username to create a ZIP collection, then send the photos/videos "
        "you want included and use /zip.\n"
        "This build packages media sent to the bot; it does not automatically fetch all "
        "posts from Instagram usernames. Use only media you have the right to save."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "/archive @username — start a labeled archive\n"
        "/status — show collected item count\n"
        "/zip — package collected media and send the ZIP\n"
        "/cancel — discard the current collection\n\n"
        "Send photos, videos, or media documents after starting an archive. "
        "The bot does not scrape Instagram or access private/login-restricted content."
    )


async def archive_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, msg = update.effective_user, update.effective_message
    if not user or not msg:
        return
    raw = " ".join(context.args)
    label = parse_profile(raw)
    if not label:
        await msg.reply_text("Usage: /archive @username (or provide an Instagram profile URL).")
        return
    old = sessions.pop(user.id, None)
    if old:
        old.close()
    td = tempfile.TemporaryDirectory(prefix=f"igarchive_{user.id}_")
    sessions[user.id] = ArchiveSession(label=label, tempdir=td)
    await msg.reply_text(
        f"Collection started for @{label}.\n"
        "Now send the photos/videos you want included, then type /zip. "
        "Automatic Instagram profile fetching is not enabled in this build."
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, msg = update.effective_user, update.effective_message
    if not user or not msg:
        return
    session = sessions.get(user.id)
    if not session:
        await msg.reply_text("No collection active. Start one with /archive @username.")
        return
    await msg.reply_text(f"Archive for @{session.label}: {len(session.files)} item(s) collected.")


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, msg = update.effective_user, update.effective_message
    if not user or not msg:
        return
    session = sessions.pop(user.id, None)
    if session:
        session.close()
        await msg.reply_text("Collection discarded.")
    else:
        await msg.reply_text("No active collection to discard.")


async def receive_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, msg = update.effective_user, update.effective_message
    if not user or not msg:
        return
    session = sessions.get(user.id)
    if not session or not session.tempdir:
        await msg.reply_text("Start a collection first with /archive @username.")
        return
    if len(session.files) >= MAX_ITEMS:
        await msg.reply_text(f"Collection limit reached ({MAX_ITEMS} files). Use /zip or /cancel.")
        return

    media = msg.document or msg.video or (msg.photo[-1] if msg.photo else None)
    if not media:
        return
    if media.file_size and media.file_size > MAX_SINGLE_FILE:
        await msg.reply_text("That file is too large for this collection. Maximum per item is 45 MiB.")
        return

    lock = session_locks.setdefault(user.id, asyncio.Lock())
    async with lock:
        # Check session again in case the user cancelled while waiting for the lock.
        session = sessions.get(user.id)
        if not session or not session.tempdir:
            await msg.reply_text("Collection was cancelled. Start a new one with /archive @username.")
            return
        if len(session.files) >= MAX_ITEMS:
            await msg.reply_text(f"Collection limit reached ({MAX_ITEMS} files).")
            return
        original = getattr(media, "file_name", None) or f"media_{len(session.files)+1}"
        suffix = Path(original).suffix[:12]
        base = safe_name(Path(original).stem)
        unique = f"{len(session.files)+1:04d}_{base}{suffix}"
        target = Path(session.tempdir.name) / unique
        try:
            tg_file = await context.bot.get_file(media.file_id)
            await tg_file.download_to_drive(custom_path=str(target))
        except Exception:
            log.exception("Media download from Telegram failed")
            target.unlink(missing_ok=True)
            await msg.reply_text("Couldn't retrieve that Telegram media file. Please try again.")
            return
        session.files.append(target)
        await msg.reply_text(f"Added {len(session.files)}/{MAX_ITEMS}: {unique}")


async def zip_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, msg = update.effective_user, update.effective_message
    if not user or not msg:
        return
    lock = session_locks.setdefault(user.id, asyncio.Lock())
    async with lock:
        session = sessions.get(user.id)
        if not session or not session.files or not session.tempdir:
            await msg.reply_text("No media collected yet. Start with /archive @username and send media.")
            return
        label = session.label
        workdir = Path(session.tempdir.name)
        zip_path = workdir / f"{safe_name(label)}_archive.zip"
        try:
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                for path in session.files:
                    archive.write(path, arcname=path.name)
            size = zip_path.stat().st_size
            if size > MAX_ZIP_BYTES:
                zip_path.unlink(missing_ok=True)
                await msg.reply_text(
                    "The ZIP exceeds the 45 MiB send limit. Split the collection into smaller batches "
                    "by starting separate archives."
                )
                return
            await msg.reply_text(f"Creating ZIP complete: {len(session.files)} file(s). Sending…")
            with zip_path.open("rb") as stream:
                await msg.reply_document(
                    document=stream,
                    filename=zip_path.name,
                    caption=f"Archive for @{label} • {len(session.files)} file(s)",
                    read_timeout=120,
                    write_timeout=120,
                )
        except Exception:
            log.exception("ZIP creation or delivery failed")
            await msg.reply_text("ZIP creation or delivery failed. Try fewer or smaller files.")
            return
        finally:
            # Keep collection if archive is too large or delivery failed; remove only after success.
            pass
        removed = sessions.pop(user.id, None)
        if removed:
            removed.close()


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg:
        return
    username = parse_profile(msg.text or "")
    if username:
        await msg.reply_text(
            f"Recognized profile: @{username}\n"
            f"To start a manual media collection, use /archive @{username}, send the files, then /zip. "
            "Automatic profile downloading is not available in this build."
        )
    else:
        await msg.reply_text("Send /archive @username to start, then send media and use /zip.")


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("Missing BOT_TOKEN environment variable. Set it in the runtime environment.")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("archive", archive_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("zip", zip_command))
    media_filter = filters.PHOTO | filters.VIDEO | filters.Document.ALL
    app.add_handler(MessageHandler(media_filter, receive_media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    log.info("Polling started")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
