"""Instagram archive bot starter.

This bot deliberately does not scrape arbitrary Instagram profiles. The official
Instagram API only exposes media for authorized/connected accounts. Add an
authorized-source adapter before enabling media retrieval.
"""
import asyncio
import logging
import os
import re
from dataclasses import dataclass
from typing import Dict
from urllib.parse import urlparse

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application, CommandHandler, ContextTypes, MessageHandler, filters,
)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
)
log = logging.getLogger("profile-archive")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
MAX_USERNAME_LENGTH = 30
USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
PROFILE_HOSTS = {"instagram.com", "www.instagram.com", "m.instagram.com"}
# Simple in-memory per-user state. For multi-process deployment, replace with Redis.
active_jobs: Dict[int, asyncio.Task] = {}


@dataclass(frozen=True)
class ProfileRequest:
    username: str


def parse_profile(text: str) -> ProfileRequest | None:
    """Accept @handle, bare handle, or instagram.com/handle profile link."""
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
        if parsed.scheme not in {"http", "https"}:
            return None
        if (parsed.hostname or "").lower() not in PROFILE_HOSTS:
            return None
        parts = [p for p in parsed.path.split("/") if p]
        if not parts:
            return None
        # Only profile paths; reject /p/, /reel/, /stories/, etc.
        value = parts[0]
        if value.lower() in {"p", "reel", "reels", "stories", "explore", "accounts"}:
            return None
    value = value.strip().lstrip("@")
    if len(value) > MAX_USERNAME_LENGTH or not USERNAME_RE.fullmatch(value):
        return None
    return ProfileRequest(username=value)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message:
        return
    await update.effective_message.reply_text(
        "Send an Instagram profile username (for example: @handle) or profile link.\n\n"
        "I can prepare archive jobs, but downloading arbitrary profiles is not enabled: "
        "media retrieval must use an authorized source. This bot does not access private "
        "accounts, bypass login/access controls, or collect leaked/non-consensual material."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message:
        return
    await update.effective_message.reply_text(
        "Commands:\n"
        "/start — begin\n"
        "/help — show help\n"
        "/cancel — cancel your current job\n\n"
        "Send @username or an Instagram profile URL to validate the profile input. "
        "The current build has no media retrieval adapter yet."
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return
    task = active_jobs.pop(user.id, None)
    if task and not task.done():
        task.cancel()
        await msg.reply_text("Your job was cancelled.")
    else:
        await msg.reply_text("You don't have an active job.")


async def run_archive_job(update: Update, request: ProfileRequest) -> None:
    """Placeholder job boundary for the future authorized media-source adapter."""
    msg = update.effective_message
    if not msg:
        return
    await msg.reply_text(
        f"Profile input accepted: @{request.username}\n\n"
        "Archive not started: this version has no authorized media source configured. "
        "The official API does not provide a general endpoint for downloading every "
        "post from any public username. Next development step is an authorized-source "
        "adapter and ZIP packaging pipeline."
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return
    request = parse_profile(msg.text or "")
    if not request:
        await msg.reply_text(
            "I couldn't recognize that as a profile username or profile link. "
            "Send a handle like @example or a URL like https://www.instagram.com/example/."
        )
        return
    existing = active_jobs.get(user.id)
    if existing and not existing.done():
        await msg.reply_text("You already have a job running. Use /cancel before starting another.")
        return

    task = asyncio.create_task(run_archive_job(update, request))
    active_jobs[user.id] = task
    try:
        await task
    except asyncio.CancelledError:
        log.info("Job cancelled for user_id=%s", user.id)
    except Exception:
        log.exception("Unexpected job error")
        await msg.reply_text("The job failed unexpectedly. Please try again later.")
    finally:
        if active_jobs.get(user.id) is task:
            active_jobs.pop(user.id, None)


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("Missing BOT_TOKEN environment variable. Set it in the runtime environment.")
    app = Application.builder().token(BOT_TOKEN).concurrent_updates(True).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    log.info("Bot polling started")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
