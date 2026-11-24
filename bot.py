"""
Antigravity Telegram Bridge
A lightweight, secure, and open-source Telegram bridge for Google Antigravity (agy) CLI.

Author: Muhammad Luqni Baehaqi (https://github.com/luqni)
License: MIT
"""

import io
import os
import sys
import tempfile
import threading
import time
from functools import wraps
from pathlib import Path
from typing import Optional

import psutil
import telebot
from telebot.types import Message

import config
from executor import AgyExecutor, ExecutionResult

# Initialize TeleBot
if not config.TELEGRAM_BOT_TOKEN:
    print("[ERROR] TELEGRAM_BOT_TOKEN is not set in your .env file!", file=sys.stderr)
    print("Please copy .env.example to .env and configure your token.", file=sys.stderr)
    sys.exit(1)

bot = telebot.TeleBot(config.TELEGRAM_BOT_TOKEN, parse_mode=None, threaded=True)
executor = AgyExecutor(binary_path=config.AGY_BINARY, default_timeout=config.TIMEOUT_SECONDS)

# In-memory session state per user
user_conversations: dict[int, str] = {}
user_working_dirs: dict[int, str] = {}
user_models: dict[int, Optional[str]] = {}

# Lock for user session modifications
state_lock = threading.Lock()


def require_auth(func):
    """Decorator to enforce whitelist user authentication."""
    @wraps(func)
    def wrapper(message: Message, *args, **kwargs):
        user_id = message.from_user.id
        if not config.is_allowed_user(user_id):
            warning_text = (
                "⛔ <b>Access Denied</b>\n\n"
                f"Your Telegram User ID is: <code>{user_id}</code>\n\n"
                "To authorize your account, add this numeric ID to <code>ALLOWED_USER_IDS</code> "
                "in your <code>.env</code> file on the host server."
            )
            bot.reply_to(message, warning_text, parse_mode="HTML")
            return
        return func(message, *args, **kwargs)
    return wrapper


def get_user_cwd(user_id: int) -> str:
    """Get the active working directory for a user."""
    with state_lock:
        return user_working_dirs.get(user_id, config.WORKING_DIR)


def get_user_conversation(user_id: int) -> Optional[str]:
    """Get the active Antigravity conversation ID for a user."""
    with state_lock:
        return user_conversations.get(user_id)


def get_user_model(user_id: int) -> Optional[str]:
    """Get the selected model for a user."""
    with state_lock:
        return user_models.get(user_id)


def send_continuous_typing(chat_id: int, stop_event: threading.Event):
    """Keep sending 'typing' action every 4 seconds until stopped."""
    while not stop_event.is_set():
        try:
            bot.send_chat_action(chat_id, "typing")
        except Exception:
            pass
        stop_event.wait(4.0)


def send_smart_reply(message: Message, text: str, footer: str = ""):
    """
    Send response cleanly.
    - If <= 4000 chars: sends as text.
    - If 4000 - 12000 chars: splits into chunks.
    - If > 12000 chars: sends summary and attaches full output as .txt document.
    """
    full_text = f"{text}\n\n{footer}".strip() if footer else text.strip()

    if len(full_text) <= 4000:
        try:
            bot.reply_to(message, full_text, parse_mode="Markdown")
        except Exception:
            bot.reply_to(message, full_text)
        return

    # If excessively long (> 12,000 chars), send preview and attach file
    if len(full_text) > 12000:
        preview = full_text[:2500] + "\n\n... [Output truncated. Complete log attached below] ..."
        if footer:
            preview += f"\n\n{footer}"
        try:
            bot.reply_to(message, preview, parse_mode="Markdown")
        except Exception:
            bot.reply_to(message, preview)

        bio = io.BytesIO(full_text.encode("utf-8"))
        bio.name = f"antigravity_output_{int(time.time())}.txt"
        bot.send_document(message.chat.id, bio, caption="📄 Complete Execution Output")
        return

    # For medium-length outputs (4000 - 12000 chars), split into chunks
    chunk_size = 3800
    chunks = [full_text[i:i + chunk_size] for i in range(0, len(full_text), chunk_size)]
    for idx, chunk in enumerate(chunks):
        try:
            bot.send_message(message.chat.id, chunk, parse_mode="Markdown")
        except Exception:
            bot.send_message(message.chat.id, chunk)


# ==========================================
# Command Handlers
# ==========================================

@bot.message_handler(commands=["start"])
@require_auth
def handle_start(message: Message):
    user_id = message.from_user.id
    cwd = get_user_cwd(user_id)
    welcome_text = (
        "🚀 <b>Antigravity Telegram Bridge</b>\n"
        "<i>Seamless remote control for Google Antigravity (agy) CLI.</i>\n\n"
        f"📁 <b>Working Directory:</b> <code>{cwd}</code>\n"
        f"⚙️ <b>Binary:</b> <code>{config.AGY_BINARY}</code>\n\n"
        "<b>Available Commands:</b>\n"
        "• <code>/newchat</code> — Reset and start a fresh conversation\n"
        "• <code>/cd &lt;path&gt;</code> — Change active working directory\n"
        "• <code>/pwd</code> — Display current working directory\n"
        "• <code>/status</code> — Inspect system resources & CLI status\n"
        "• <code>/model &lt;name&gt;</code> — Switch or inspect model\n"
        "• <code>/help</code> — Show detailed usage help\n\n"
        "💬 <b>Just send any message</b> to converse with Antigravity, or send a screenshot for visual debugging!"
    )
    bot.reply_to(message, welcome_text, parse_mode="HTML")


@bot.message_handler(commands=["help"])
@require_auth
def handle_help(message: Message):
    help_text = (
        "📖 <b>Antigravity Telegram Bridge — Command Guide</b>\n\n"
        "<b>Natural Conversation:</b>\n"
        "Simply type whatever you want! Your agent will retain context across messages "
        "using Antigravity's multi-turn session engine.\n\n"
        "<b>Visual Inspection:</b>\n"
        "Send a screenshot or image with an optional caption. The bridge passes it directly "
        "to the agent for visual error analysis and debugging.\n\n"
        "<b>Navigation Commands:</b>\n"
        "• <code>/cd /path/to/project</code> — Switch to another folder\n"
        "• <code>/cd ..</code> — Go up one directory level\n"
        "• <code>/pwd</code> — Show current working folder\n\n"
        "<b>Session Management:</b>\n"
        "• <code>/newchat</code> (or <code>/reset</code>) — Start a completely fresh session\n"
        "• <code>/model [name]</code> — View or override model (e.g. <code>/model gemini-2.5-pro</code>)\n"
        "• <code>/status</code> — Check CPU, RAM, disk, and agent health\n"
    )
    bot.reply_to(message, help_text, parse_mode="HTML")


@bot.message_handler(commands=["newchat", "reset"])
@require_auth
def handle_newchat(message: Message):
    user_id = message.from_user.id
    with state_lock:
        old_id = user_conversations.pop(user_id, None)

    msg = "✨ <b>New conversation started!</b>\nPrevious context has been reset."
    if old_id:
        msg += f"\n<i>(Previous Session: <code>{old_id[:8]}...</code>)</i>"
    bot.reply_to(message, msg, parse_mode="HTML")


@bot.message_handler(commands=["cd"])
@require_auth
def handle_cd(message: Message):
    user_id = message.from_user.id
    args = message.text.split(maxsplit=1)

    if len(args) < 2:
        cwd = get_user_cwd(user_id)
        bot.reply_to(message, f"📁 Current directory: <code>{cwd}</code>", parse_mode="HTML")
        return

    target_path = args[1].strip()
    current_cwd = get_user_cwd(user_id)

    # Expand user tilde and resolve relative or absolute path
    resolved_path = Path(current_cwd).joinpath(Path(target_path).expanduser()).resolve()

    if not resolved_path.exists():
        bot.reply_to(message, f"❌ Directory does not exist:\n<code>{resolved_path}</code>", parse_mode="HTML")
        return

    if not resolved_path.is_dir():
        bot.reply_to(message, f"❌ Path is not a directory:\n<code>{resolved_path}</code>", parse_mode="HTML")
        return

    with state_lock:
        user_working_dirs[user_id] = str(resolved_path)

    bot.reply_to(message, f"✅ Working directory switched to:\n<code>{resolved_path}</code>", parse_mode="HTML")


@bot.message_handler(commands=["pwd"])
@require_auth
def handle_pwd(message: Message):
    user_id = message.from_user.id
    cwd = get_user_cwd(user_id)
    bot.reply_to(message, f"📁 Current working directory:\n<code>{cwd}</code>", parse_mode="HTML")


@bot.message_handler(commands=["model"])
@require_auth
def handle_model(message: Message):
    user_id = message.from_user.id
    args = message.text.split(maxsplit=1)

    if len(args) < 2:
        current_model = get_user_model(user_id) or "Default (configured in agy settings)"
        bot.reply_to(
            message,
            f"🧠 <b>Active Model:</b> <code>{current_model}</code>\n\n"
            "To change, use: <code>/model &lt;model_name&gt;</code>\n"
            "To reset to default, use: <code>/model default</code>",
            parse_mode="HTML"
        )
        return

    new_model = args[1].strip()
    with state_lock:
        if new_model.lower() in ("default", "reset", "clear"):
            user_models.pop(user_id, None)
            bot.reply_to(message, "✅ Model preference reset to default.", parse_mode="HTML")
        else:
            user_models[user_id] = new_model
            bot.reply_to(message, f"✅ Model set to: <code>{new_model}</code>", parse_mode="HTML")


@bot.message_handler(commands=["status"])
@require_auth
def handle_status(message: Message):
    user_id = message.from_user.id
    cwd = get_user_cwd(user_id)
    conv_id = get_user_conversation(user_id) or "None (New Session)"
    model = get_user_model(user_id) or "Default"

    # Host metrics
    cpu_pct = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()
    mem_used_gb = round(mem.used / (1024 ** 3), 2)
    mem_total_gb = round(mem.total / (1024 ** 3), 2)
    disk = psutil.disk_usage(cwd)
    disk_free_gb = round(disk.free / (1024 ** 3), 2)
    disk_total_gb = round(disk.total / (1024 ** 3), 2)

    status_text = (
        "📊 <b>System & Bridge Status</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💻 <b>CPU Load:</b> <code>{cpu_pct}%</code>\n"
        f"🧠 <b>RAM Usage:</b> <code>{mem_used_gb} GB / {mem_total_gb} GB ({mem.percent}%)</code>\n"
        f"💾 <b>Disk Space:</b> <code>{disk_free_gb} GB free / {disk_total_gb} GB</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📁 <b>Directory:</b> <code>{cwd}</code>\n"
        f"💬 <b>Conversation:</b> <code>{conv_id}</code>\n"
        f"🤖 <b>Model:</b> <code>{model}</code>\n"
        f"⚙️ <b>Binary:</b> <code>{config.AGY_BINARY}</code>\n"
    )
    bot.reply_to(message, status_text, parse_mode="HTML")


# ==========================================
# Photo / Screenshot Handler (Vision)
# ==========================================

@bot.message_handler(content_types=["photo"])
@require_auth
def handle_photo(message: Message):
    user_id = message.from_user.id
    cwd = get_user_cwd(user_id)
    conv_id = get_user_conversation(user_id)
    model = get_user_model(user_id)
    caption = message.caption or "Please analyze this image or screenshot."

    # Download highest-resolution photo
    photo_file = message.photo[-1]
    file_info = bot.get_file(photo_file.file_id)
    downloaded = bot.download_file(file_info.file_path)

    # Save to a temporary file
    temp_img = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    temp_img_path = temp_img.name
    try:
        temp_img.write(downloaded)
        temp_img.close()

        stop_typing = threading.Event()
        typing_thread = threading.Thread(target=send_continuous_typing, args=(message.chat.id, stop_typing))
        typing_thread.daemon = True
        typing_thread.start()

        try:
            result = executor.run(
                prompt=caption,
                cwd=cwd,
                conversation_id=conv_id,
                model=model,
                image_path=temp_img_path,
            )
        finally:
            stop_typing.set()

        if result.success:
            if result.conversation_id:
                with state_lock:
                    user_conversations[user_id] = result.conversation_id

            footer = f"⚡ <i>{result.duration_seconds}s</i>"
            if result.total_tokens > 0:
                footer += f" | <i>{result.total_tokens:,} tokens</i>"

            send_smart_reply(message, result.response, footer=footer)
        else:
            bot.reply_to(message, f"❌ <b>Error:</b>\n{result.error or 'Failed to process image'}", parse_mode="HTML")

    finally:
        # Cleanup temporary image
        if os.path.exists(temp_img_path):
            try:
                os.remove(temp_img_path)
            except Exception:
                pass


# ==========================================
# Text Message Handler
# ==========================================

@bot.message_handler(func=lambda msg: True, content_types=["text"])
@require_auth
def handle_text(message: Message):
    user_id = message.from_user.id
    cwd = get_user_cwd(user_id)
    conv_id = get_user_conversation(user_id)
    model = get_user_model(user_id)
    prompt = message.text.strip()

    if not prompt:
        return

    stop_typing = threading.Event()
    typing_thread = threading.Thread(target=send_continuous_typing, args=(message.chat.id, stop_typing))
    typing_thread.daemon = True
    typing_thread.start()

    try:
        result = executor.run(
            prompt=prompt,
            cwd=cwd,
            conversation_id=conv_id,
            model=model,
        )
    finally:
        stop_typing.set()

    if result.success:
        if result.conversation_id:
            with state_lock:
                user_conversations[user_id] = result.conversation_id

        footer = f"⚡ <i>{result.duration_seconds}s</i>"
        if result.total_tokens > 0:
            footer += f" | <i>{result.total_tokens:,} tokens</i>"

        send_smart_reply(message, result.response, footer=footer)
    else:
        err = result.error or "Command failed with no output."
        bot.reply_to(message, f"❌ <b>Execution Error:</b>\n<pre>{err}</pre>", parse_mode="HTML")


def main():
    print("=" * 60)
    print("🚀 Antigravity Telegram Bridge is starting...")
    print(f"• Binary Path: {config.AGY_BINARY}")
    print(f"• Default Working Directory: {config.WORKING_DIR}")
    print(f"• Whitelisted User IDs: {list(config.ALLOWED_USER_IDS) if config.ALLOWED_USER_IDS else 'None (All blocked)'}")
    print("=" * 60)
    print("Listening for incoming Telegram messages (Press Ctrl+C to stop)...")

    bot.infinity_polling(timeout=20, long_polling_timeout=20)


if __name__ == "__main__":
    main()
