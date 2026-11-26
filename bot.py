"""
Antigravity Telegram Bridge
A lightweight, secure, and open-source Telegram bridge for Google Antigravity (agy) CLI.

Version: 1.1.0
Features:
- Interactive Approve/Reject inline button flow
- Plan / Dry-run execution mode (/plan and inline plan button)
- Fail-secure / Default-deny timeout for pending executions
- Dynamic safety mode toggle (/safety: Strict vs Direct)
- Resumable Execution (inline [🔁 Resume / Lanjutkan] button and /resume command)
- Session continuity and Multi-turn context
- Visual inspection support (Vision)
- Smart message chunking and output formatting

Author: Muhammad Luqni Baehaqi (https://github.com/luqni)
License: MIT
"""

import io
import os
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from typing import Optional

import psutil
import telebot
from telebot.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import config
from executor import AgyExecutor, ExecutionResult

# Initialize TeleBot
_token = config.TELEGRAM_BOT_TOKEN
if not _token:
    if __name__ == "__main__":
        print("[ERROR] TELEGRAM_BOT_TOKEN is not set in your .env file!", file=sys.stderr)
        print("Please copy .env.example to .env and configure your token.", file=sys.stderr)
        sys.exit(1)
    else:
        # Fallback dummy token for testing / module import
        _token = "123456789:DUMMY_MOCK_TOKEN_FOR_TESTING"

bot = telebot.TeleBot(_token, parse_mode=None, threaded=True)
executor = AgyExecutor(binary_path=config.AGY_BINARY, default_timeout=config.TIMEOUT_SECONDS)


@dataclass
class PendingRequest:
    req_id: str
    user_id: int
    chat_id: int
    message_id: int  # Telegram message ID containing the approval card
    prompt: str
    cwd: str
    model: Optional[str] = None
    image_path: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    timeout_seconds: int = config.APPROVAL_TIMEOUT_SECONDS
    status: str = "pending"  # "pending", "approved", "rejected", "timed_out", "resumed"
    timer: Optional[threading.Timer] = None


# In-memory session state per user
user_conversations: dict[int, str] = {}
user_working_dirs: dict[int, str] = {}
user_models: dict[int, Optional[str]] = {}
user_safety_modes: dict[int, str] = {}  # "strict" or "direct"

# In-memory pending approval requests & resumable tasks
pending_requests: dict[str, PendingRequest] = {}
user_resumable_tasks: dict[int, list[PendingRequest]] = {}

# Lock for user session & request state modifications
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


def get_user_safety_mode(user_id: int) -> str:
    """Get the active safety mode for a user ('strict' or 'direct')."""
    with state_lock:
        return user_safety_modes.get(user_id, config.DEFAULT_SAFETY_MODE)


def set_user_safety_mode(user_id: int, mode: str):
    """Update safety mode preference for a user."""
    with state_lock:
        user_safety_modes[user_id] = mode.lower()


def send_continuous_typing(chat_id: int, stop_event: threading.Event):
    """Keep sending 'typing' action every 4 seconds until stopped."""
    while not stop_event.is_set():
        try:
            bot.send_chat_action(chat_id, "typing")
        except Exception:
            pass
        stop_event.wait(4.0)


def send_smart_reply(
    chat_id: int,
    text: str,
    footer: str = "",
    reply_to_message_id: Optional[int] = None,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
):
    """
    Send response cleanly with Markdown formatting and smart fallback.
    - If <= 4000 chars: sends as single text message.
    - If 4000 - 12000 chars: splits into sequential chunks.
    - If > 12000 chars: sends summary and attaches full output as .txt document.
    """
    full_text = f"{text}\n\n{footer}".strip() if footer else text.strip()

    if len(full_text) <= 4000:
        try:
            return bot.send_message(
                chat_id,
                full_text,
                parse_mode="Markdown",
                reply_to_message_id=reply_to_message_id,
                reply_markup=reply_markup,
            )
        except Exception:
            return bot.send_message(
                chat_id,
                full_text,
                reply_to_message_id=reply_to_message_id,
                reply_markup=reply_markup,
            )

    # If excessively long (> 12,000 chars), send preview and attach file
    if len(full_text) > 12000:
        preview = full_text[:2500] + "\n\n... [Output truncated. Complete log attached below] ..."
        if footer:
            preview += f"\n\n{footer}"
        try:
            bot.send_message(
                chat_id,
                preview,
                parse_mode="Markdown",
                reply_to_message_id=reply_to_message_id,
                reply_markup=reply_markup,
            )
        except Exception:
            bot.send_message(
                chat_id,
                preview,
                reply_to_message_id=reply_to_message_id,
                reply_markup=reply_markup,
            )

        bio = io.BytesIO(full_text.encode("utf-8"))
        bio.name = f"antigravity_output_{int(time.time())}.txt"
        bot.send_document(chat_id, bio, caption="📄 Complete Execution Output")
        return

    # For medium-length outputs (4000 - 12000 chars), split into chunks
    chunk_size = 3800
    chunks = [full_text[i:i + chunk_size] for i in range(0, len(full_text), chunk_size)]
    for idx, chunk in enumerate(chunks):
        markup = reply_markup if idx == len(chunks) - 1 else None
        try:
            bot.send_message(chat_id, chunk, parse_mode="Markdown", reply_markup=markup)
        except Exception:
            bot.send_message(chat_id, chunk, reply_markup=markup)


# ==========================================
# Core Task Execution Runner
# ==========================================

def execute_and_reply(
    chat_id: int,
    user_id: int,
    prompt: str,
    cwd: str,
    model: Optional[str] = None,
    image_path: Optional[str] = None,
    reply_to_message_id: Optional[int] = None,
    is_plan_mode: bool = False,
    origin_req_id: Optional[str] = None,
):
    """Executes a prompt through the AgyExecutor in background and formats results."""
    stop_typing = threading.Event()
    typing_thread = threading.Thread(target=send_continuous_typing, args=(chat_id, stop_typing))
    typing_thread.daemon = True
    typing_thread.start()

    conv_id = get_user_conversation(user_id)

    try:
        result = executor.run(
            prompt=prompt,
            cwd=cwd,
            conversation_id=conv_id,
            model=model,
            image_path=image_path,
            is_plan_mode=is_plan_mode,
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

        if is_plan_mode:
            # Provide inline actions to execute the generated plan directly
            plan_markup = InlineKeyboardMarkup(row_width=2)
            plan_req_id = origin_req_id or uuid.uuid4().hex[:8]

            # Register as pending task for plan execution approval
            with state_lock:
                pending_requests[plan_req_id] = PendingRequest(
                    req_id=plan_req_id,
                    user_id=user_id,
                    chat_id=chat_id,
                    message_id=0,
                    prompt=prompt,
                    cwd=cwd,
                    model=model,
                    image_path=image_path,
                    status="plan_generated",
                )

            btn_execute_plan = InlineKeyboardButton(
                "🚀 Eksekusi Rencana Ini",
                callback_data=f"plan_exec:{plan_req_id}",
            )
            btn_dismiss = InlineKeyboardButton(
                "❌ Tutup",
                callback_data=f"dismiss:{plan_req_id}",
            )
            plan_markup.add(btn_execute_plan, btn_dismiss)

            header = "📋 **Rencana Eksekusi (Dry-Run Plan)**\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            send_smart_reply(
                chat_id=chat_id,
                text=f"{header}{result.response}",
                footer=footer,
                reply_to_message_id=reply_to_message_id,
                reply_markup=plan_markup,
            )
        else:
            send_smart_reply(
                chat_id=chat_id,
                text=result.response,
                footer=footer,
                reply_to_message_id=reply_to_message_id,
            )
    else:
        err = result.error or "Command failed with no output."
        bot.send_message(
            chat_id,
            f"❌ <b>Execution Error:</b>\n<pre>{err}</pre>",
            parse_mode="HTML",
            reply_to_message_id=reply_to_message_id,
        )


# ==========================================
# Safety, Approval & Fail-Secure Handlers
# ==========================================

def create_approval_keyboard(req_id: str) -> InlineKeyboardMarkup:
    """Builds interactive inline keyboard for strict-mode approval."""
    markup = InlineKeyboardMarkup(row_width=2)
    btn_approve = InlineKeyboardButton("✅ Setujui & Jalankan", callback_data=f"approve:{req_id}")
    btn_plan = InlineKeyboardButton("📋 Plan / Dry-Run", callback_data=f"plan:{req_id}")
    btn_reject = InlineKeyboardButton("❌ Batalkan", callback_data=f"reject:{req_id}")
    markup.add(btn_approve, btn_plan)
    markup.add(btn_reject)
    return markup


def create_resumable_keyboard(req_id: str) -> InlineKeyboardMarkup:
    """Builds inline button to resume a cancelled or timed-out task."""
    markup = InlineKeyboardMarkup(row_width=1)
    btn_resume = InlineKeyboardButton("🔁 Resume / Lanjutkan Sekarang", callback_data=f"resume:{req_id}")
    markup.add(btn_resume)
    return markup


def on_request_timeout(req_id: str):
    """Fail-secure callback when user confirmation times out."""
    with state_lock:
        req = pending_requests.get(req_id)
        if not req or req.status != "pending":
            return
        req.status = "timed_out"

        # Save to user's resumable list
        history = user_resumable_tasks.setdefault(req.user_id, [])
        history.insert(0, req)
        if len(history) > config.MAX_RESUME_HISTORY:
            history.pop()

    # Inform user and provide Resumable button
    timeout_text = (
        "⏱️ <b>Permintaan Kedaluwarsa (Default-Deny Timeout)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Instruksi <b>tidak dieksekusi</b> demi keamanan karena tidak ada konfirmasi "
        f"dalam {req.timeout_seconds} detik.\n\n"
        f"📁 <b>Direktori:</b> <code>{req.cwd}</code>\n"
        f"💬 <b>Prompt:</b> <code>{req.prompt[:150]}{'...' if len(req.prompt) > 150 else ''}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 <i>Anda dapat melanjutkan eksekusi tugas ini kapan saja dengan tombol di bawah:</i>"
    )

    try:
        bot.edit_message_text(
            chat_id=req.chat_id,
            message_id=req.message_id,
            text=timeout_text,
            parse_mode="HTML",
            reply_markup=create_resumable_keyboard(req_id),
        )
    except Exception:
        # If edit fails, send a fresh message
        bot.send_message(
            chat_id=req.chat_id,
            text=timeout_text,
            parse_mode="HTML",
            reply_markup=create_resumable_keyboard(req_id),
        )


def register_pending_approval(
    message: Message,
    prompt: str,
    cwd: str,
    model: Optional[str] = None,
    image_path: Optional[str] = None,
):
    """Registers a prompt under strict mode and presents approval buttons to the user."""
    req_id = uuid.uuid4().hex[:8]
    user_id = message.from_user.id
    chat_id = message.chat.id

    prompt_preview = prompt.strip()
    if len(prompt_preview) > 200:
        prompt_preview = prompt_preview[:200] + "..."

    card_text = (
        "🛡️ <b>Konfirmasi Eksekusi (Mode Ketat)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📁 <b>Direktori:</b> <code>{cwd}</code>\n"
        f"💬 <b>Instruksi:</b> <code>{prompt_preview}</code>\n"
        f"⏱️ <b>Timeout:</b> {config.APPROVAL_TIMEOUT_SECONDS}s (Fail-Secure)\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Silakan tentukan aksi untuk perintah di atas:"
    )

    sent_msg = bot.reply_to(
        message,
        card_text,
        parse_mode="HTML",
        reply_markup=create_approval_keyboard(req_id),
    )

    # Set up background timer for default-deny timeout
    timer = threading.Timer(config.APPROVAL_TIMEOUT_SECONDS, on_request_timeout, args=[req_id])
    timer.daemon = True

    req = PendingRequest(
        req_id=req_id,
        user_id=user_id,
        chat_id=chat_id,
        message_id=sent_msg.message_id,
        prompt=prompt,
        cwd=cwd,
        model=model,
        image_path=image_path,
        timeout_seconds=config.APPROVAL_TIMEOUT_SECONDS,
        timer=timer,
    )

    with state_lock:
        pending_requests[req_id] = req

    timer.start()


# ==========================================
# Command Handlers
# ==========================================

@bot.message_handler(commands=["start"])
@require_auth
def handle_start(message: Message):
    user_id = message.from_user.id
    cwd = get_user_cwd(user_id)
    safety_mode = get_user_safety_mode(user_id)
    safety_icon = "🛡️ Mode Ketat (Strict)" if safety_mode == "strict" else "⚡ Mode Bebas (Direct)"

    welcome_text = (
        "🚀 <b>Antigravity Telegram Bridge v1.1</b>\n"
        "<i>Seamless remote control for Google Antigravity (agy) CLI.</i>\n\n"
        f"📁 <b>Working Directory:</b> <code>{cwd}</code>\n"
        f"🔒 <b>Safety Mode:</b> <code>{safety_icon}</code>\n"
        f"⚙️ <b>Binary:</b> <code>{config.AGY_BINARY}</code>\n\n"
        "<b>Available Commands:</b>\n"
        "• <code>/safety</code> — Toggle strict approval vs direct execution\n"
        "• <code>/plan &lt;task&gt;</code> — Dry-run simulation without file mutation\n"
        "• <code>/resume</code> — Resume the latest timed-out or cancelled request\n"
        "• <code>/newchat</code> — Reset and start a fresh conversation\n"
        "• <code>/cd &lt;path&gt;</code> — Change active working directory\n"
        "• <code>/pwd</code> — Display current working directory\n"
        "• <code>/status</code> — Inspect system resources & CLI status\n"
        "• <code>/model &lt;name&gt;</code> — Switch or inspect model\n"
        "• <code>/help</code> — Show detailed usage guide\n\n"
        "💬 <b>Just send any message</b> to converse with Antigravity, or send a screenshot for visual debugging!"
    )
    bot.reply_to(message, welcome_text, parse_mode="HTML")


@bot.message_handler(commands=["help"])
@require_auth
def handle_help(message: Message):
    help_text = (
        "📖 <b>Antigravity Telegram Bridge v1.1 — Command Guide</b>\n\n"
        "<b>🛡️ Safety & Approval System:</b>\n"
        "• <code>/safety</code> — Switch between <b>Mode Ketat</b> (requires approval before running) "
        "and <b>Mode Bebas</b> (immediate execution).\n"
        "• <code>/plan &lt;instruksi&gt;</code> — Request a step-by-step Dry-Run plan without modifying files.\n"
        "• <code>/resume</code> — Re-execute tasks that expired due to default-deny timeout.\n\n"
        "<b>💬 Natural Conversation & Vision:</b>\n"
        "• Type instructions naturally; context is retained across messages.\n"
        "• Send a screenshot or photo with a caption for visual debugging.\n\n"
        "<b>📂 Navigation:</b>\n"
        "• <code>/cd /path/to/project</code> — Switch active folder\n"
        "• <code>/cd ..</code> — Go up one level\n"
        "• <code>/pwd</code> — Show current active folder\n\n"
        "<b>⚙️ Session & System:</b>\n"
        "• <code>/newchat</code> (or <code>/reset</code>) — Start a completely fresh session\n"
        "• <code>/model [name]</code> — View or override model (e.g. <code>/model gemini-2.5-pro</code>)\n"
        "• <code>/status</code> — Check CPU, RAM, disk, and agent health\n"
    )
    bot.reply_to(message, help_text, parse_mode="HTML")


@bot.message_handler(commands=["safety"])
@require_auth
def handle_safety(message: Message):
    user_id = message.from_user.id
    args = message.text.split(maxsplit=1)

    if len(args) > 1:
        target = args[1].strip().lower()
        if target in ("strict", "ketat", "safe", "guard", "guarded"):
            set_user_safety_mode(user_id, "strict")
            bot.reply_to(
                message,
                "🛡️ <b>Mode Ketat Diaktifkan!</b>\nSetiap instruksi akan meminta persetujuan inline sebelum dieksekusi.",
                parse_mode="HTML",
            )
            return
        elif target in ("direct", "bebas", "santai", "relax", "relaxed", "auto"):
            set_user_safety_mode(user_id, "direct")
            bot.reply_to(
                message,
                "⚡ <b>Mode Bebas Diaktifkan!</b>\nInstruksi akan langsung dieksekusi tanpa konfirmasi manual.",
                parse_mode="HTML",
            )
            return
        elif target in ("toggle", "switch"):
            current = get_user_safety_mode(user_id)
            new_mode = "direct" if current == "strict" else "strict"
            set_user_safety_mode(user_id, new_mode)
            label = "🛡️ Mode Ketat" if new_mode == "strict" else "⚡ Mode Bebas"
            bot.reply_to(message, f"🔄 Mode keamanan dialihkan ke: <b>{label}</b>", parse_mode="HTML")
            return

    # Display status card with interactive toggle buttons
    current_mode = get_user_safety_mode(user_id)
    strict_check = " (Aktif)" if current_mode == "strict" else ""
    direct_check = " (Aktif)" if current_mode == "direct" else ""

    markup = InlineKeyboardMarkup(row_width=2)
    btn_strict = InlineKeyboardButton(f"🛡️ Mode Ketat{strict_check}", callback_data="safety:set:strict")
    btn_direct = InlineKeyboardButton(f"⚡ Mode Bebas{direct_check}", callback_data="safety:set:direct")
    markup.add(btn_strict, btn_direct)

    safety_text = (
        "🛡️ <b>Pengaturan Mode Keamanan (Safety Guard)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• Status Saat Ini: <b>{'🛡️ Mode Ketat (Guarded)' if current_mode == 'strict' else '⚡ Mode Bebas (Direct)'}</b>\n"
        f"• Timeout Fail-Secure: <b>{config.APPROVAL_TIMEOUT_SECONDS} detik</b>\n\n"
        "<b>Pilihan Mode:</b>\n"
        "• <b>Mode Ketat:</b> Menampilkan tombol <i>[Approve]</i>, <i>[Plan]</i>, dan <i>[Reject]</i>. "
        "Jika didiamkan selama 3 menit, perintah otomatis dibatalkan demi keamanan.\n"
        "• <b>Mode Bebas:</b> Eksekusi instan tanpa tombol konfirmasi (cocok untuk pengerjaan cepat).\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Klik tombol di bawah untuk mengubah mode:"
    )
    bot.reply_to(message, safety_text, parse_mode="HTML", reply_markup=markup)


@bot.message_handler(commands=["plan", "dryrun"])
@require_auth
def handle_plan(message: Message):
    """Executes a prompt explicitly in Dry-Run / Plan mode."""
    user_id = message.from_user.id
    cwd = get_user_cwd(user_id)
    model = get_user_model(user_id)

    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip():
        bot.reply_to(
            message,
            "💡 <b>Format Penggunaan:</b>\n<code>/plan &lt;instruksi atau tugas&gt;</code>\n\n"
            "<i>Contoh:</i> <code>/plan refaktor struktur database pada modul billing</code>",
            parse_mode="HTML",
        )
        return

    prompt = args[1].strip()
    status_msg = bot.reply_to(
        message,
        "📋 <i>Menganalisis dan menyusun rencana eksekusi (Dry-Run Plan)...</i>",
        parse_mode="HTML",
    )

    threading.Thread(
        target=execute_and_reply,
        kwargs={
            "chat_id": message.chat.id,
            "user_id": user_id,
            "prompt": prompt,
            "cwd": cwd,
            "model": model,
            "reply_to_message_id": status_msg.message_id,
            "is_plan_mode": True,
        },
        daemon=True,
    ).start()


@bot.message_handler(commands=["resume"])
@require_auth
def handle_resume(message: Message):
    """Resumes the most recently expired or cancelled task for the user."""
    user_id = message.from_user.id
    with state_lock:
        history = user_resumable_tasks.get(user_id, [])
        if not history:
            bot.reply_to(
                message,
                "ℹ️ <b>Tidak ada instruksi tertunda.</b>\nSemua tugas sebelumnya sudah selesai atau belum ada timeout.",
                parse_mode="HTML",
            )
            return
        target_req = history.pop(0)

    status_msg = bot.reply_to(
        message,
        f"🔁 <b>Melanjutkan Instruksi Tertunda...</b>\n<code>{target_req.prompt[:150]}</code>",
        parse_mode="HTML",
    )

    threading.Thread(
        target=execute_and_reply,
        kwargs={
            "chat_id": message.chat.id,
            "user_id": user_id,
            "prompt": target_req.prompt,
            "cwd": target_req.cwd,
            "model": target_req.model,
            "image_path": target_req.image_path,
            "reply_to_message_id": status_msg.message_id,
            "is_plan_mode": False,
        },
        daemon=True,
    ).start()


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
            parse_mode="HTML",
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
    safety_mode = get_user_safety_mode(user_id)

    # Host metrics
    cpu_pct = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()
    mem_used_gb = round(mem.used / (1024 ** 3), 2)
    mem_total_gb = round(mem.total / (1024 ** 3), 2)
    disk = psutil.disk_usage(cwd)
    disk_free_gb = round(disk.free / (1024 ** 3), 2)
    disk_total_gb = round(disk.total / (1024 ** 3), 2)

    status_text = (
        "📊 <b>System & Bridge Status v1.1</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💻 <b>CPU Load:</b> <code>{cpu_pct}%</code>\n"
        f"🧠 <b>RAM Usage:</b> <code>{mem_used_gb} GB / {mem_total_gb} GB ({mem.percent}%)</code>\n"
        f"💾 <b>Disk Space:</b> <code>{disk_free_gb} GB free / {disk_total_gb} GB</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📁 <b>Directory:</b> <code>{cwd}</code>\n"
        f"🔒 <b>Safety Mode:</b> <code>{safety_mode.upper()}</code>\n"
        f"💬 <b>Conversation:</b> <code>{conv_id}</code>\n"
        f"🤖 <b>Model:</b> <code>{model}</code>\n"
        f"⚙️ <b>Binary:</b> <code>{config.AGY_BINARY}</code>\n"
    )
    bot.reply_to(message, status_text, parse_mode="HTML")


# ==========================================
# Callback Query Handlers (Inline Buttons)
# ==========================================

@bot.callback_query_handler(func=lambda call: call.data.startswith("safety:set:"))
def handle_safety_callback(call: CallbackQuery):
    """Handles safety mode toggles from inline keyboard."""
    user_id = call.from_user.id
    if not config.is_allowed_user(user_id):
        bot.answer_callback_query(call.id, "⛔ Akses ditolak", show_alert=True)
        return

    new_mode = call.data.split(":")[-1]
    set_user_safety_mode(user_id, new_mode)
    label = "🛡️ Mode Ketat (Guarded)" if new_mode == "strict" else "⚡ Mode Bebas (Direct)"

    bot.answer_callback_query(call.id, f"Mode diubah ke {new_mode.upper()}")
    try:
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"✅ <b>Pengaturan Tersimpan</b>\nMode keamanan kini aktif sebagai: <b>{label}</b>",
            parse_mode="HTML",
        )
    except Exception:
        pass


@bot.callback_query_handler(func=lambda call: any(call.data.startswith(p) for p in ["approve:", "plan:", "reject:", "resume:", "plan_exec:", "dismiss:"]))
def handle_interactive_task_callbacks(call: CallbackQuery):
    """Handles task approval, planning, rejection, and resumption."""
    user_id = call.from_user.id
    if not config.is_allowed_user(user_id):
        bot.answer_callback_query(call.id, "⛔ Akses ditolak", show_alert=True)
        return

    action, req_id = call.data.split(":", 1)

    with state_lock:
        req = pending_requests.get(req_id)

    # 1. Action: Reject
    if action == "reject":
        if req and req.timer:
            req.timer.cancel()
        if req:
            req.status = "rejected"
            # Add to resumable list in case user wants to resume later
            history = user_resumable_tasks.setdefault(user_id, [])
            history.insert(0, req)
            if len(history) > config.MAX_RESUME_HISTORY:
                history.pop()

        bot.answer_callback_query(call.id, "❌ Perintah dibatalkan")
        try:
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text="❌ <b>Perintah Dibatalkan</b>\nInstruksi tidak dijalankan.",
                parse_mode="HTML",
                reply_markup=create_resumable_keyboard(req_id),
            )
        except Exception:
            pass
        return

    # 2. Action: Dismiss
    if action == "dismiss":
        bot.answer_callback_query(call.id, "Ditutup")
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        return

    # 3. Action: Approve
    if action == "approve":
        if not req or req.status != "pending":
            bot.answer_callback_query(call.id, "⚠️ Tugas sudah tidak aktif atau kedaluwarsa", show_alert=True)
            return

        if req.timer:
            req.timer.cancel()
        req.status = "approved"

        bot.answer_callback_query(call.id, "🚀 Menjalankan instruksi...")
        try:
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=(
                    "🚀 <b>Instruksi Disetujui!</b>\n"
                    f"💬 <code>{req.prompt[:120]}{'...' if len(req.prompt) > 120 else ''}</code>\n"
                    "<i>Sedang dieksekusi di background...</i>"
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

        threading.Thread(
            target=execute_and_reply,
            kwargs={
                "chat_id": req.chat_id,
                "user_id": req.user_id,
                "prompt": req.prompt,
                "cwd": req.cwd,
                "model": req.model,
                "image_path": req.image_path,
                "reply_to_message_id": req.message_id,
                "is_plan_mode": False,
            },
            daemon=True,
        ).start()
        return

    # 4. Action: Plan (Dry-Run from approval card)
    if action == "plan":
        if not req or req.status != "pending":
            bot.answer_callback_query(call.id, "⚠️ Tugas sudah tidak aktif", show_alert=True)
            return

        if req.timer:
            req.timer.cancel()
        req.status = "planning"

        bot.answer_callback_query(call.id, "📋 Menyusun Dry-Run Plan...")
        try:
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=(
                    "📋 <b>Menyusun Rencana Eksekusi (Dry-Run)...</b>\n"
                    f"💬 <code>{req.prompt[:120]}{'...' if len(req.prompt) > 120 else ''}</code>\n"
                    "<i>Menganalisis dampak sistem tanpa mengubah file...</i>"
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

        threading.Thread(
            target=execute_and_reply,
            kwargs={
                "chat_id": req.chat_id,
                "user_id": req.user_id,
                "prompt": req.prompt,
                "cwd": req.cwd,
                "model": req.model,
                "image_path": req.image_path,
                "reply_to_message_id": req.message_id,
                "is_plan_mode": True,
                "origin_req_id": req.req_id,
            },
            daemon=True,
        ).start()
        return

    # 5. Action: Plan Execution (User approved the generated plan)
    if action == "plan_exec":
        if not req:
            bot.answer_callback_query(call.id, "⚠️ Rencana tidak ditemukan", show_alert=True)
            return

        bot.answer_callback_query(call.id, "🚀 Menjalankan rencana...")
        status_msg = bot.send_message(
            call.message.chat.id,
            "🚀 <b>Rencana Disetujui!</b> Memulai eksekusi riil...",
            parse_mode="HTML",
        )

        threading.Thread(
            target=execute_and_reply,
            kwargs={
                "chat_id": req.chat_id,
                "user_id": req.user_id,
                "prompt": req.prompt,
                "cwd": req.cwd,
                "model": req.model,
                "image_path": req.image_path,
                "reply_to_message_id": status_msg.message_id,
                "is_plan_mode": False,
            },
            daemon=True,
        ).start()
        return

    # 6. Action: Resume
    if action == "resume":
        target_req = req
        if not target_req:
            # Look up in user resumable history
            with state_lock:
                history = user_resumable_tasks.get(user_id, [])
                for item in history:
                    if item.req_id == req_id:
                        target_req = item
                        break

        if not target_req:
            bot.answer_callback_query(call.id, "⚠️ Tugas tidak ditemukan dalam riwayat", show_alert=True)
            return

        bot.answer_callback_query(call.id, "🔁 Melanjutkan eksekusi...")
        status_msg = bot.send_message(
            call.message.chat.id,
            f"🔁 <b>Melanjutkan Tugas yang Tertunda:</b>\n<code>{target_req.prompt[:120]}</code>",
            parse_mode="HTML",
        )

        threading.Thread(
            target=execute_and_reply,
            kwargs={
                "chat_id": target_req.chat_id,
                "user_id": target_req.user_id,
                "prompt": target_req.prompt,
                "cwd": target_req.cwd,
                "model": target_req.model,
                "image_path": target_req.image_path,
                "reply_to_message_id": status_msg.message_id,
                "is_plan_mode": False,
            },
            daemon=True,
        ).start()
        return


# ==========================================
# Photo / Screenshot Handler (Vision)
# ==========================================

@bot.message_handler(content_types=["photo"])
@require_auth
def handle_photo(message: Message):
    user_id = message.from_user.id
    cwd = get_user_cwd(user_id)
    model = get_user_model(user_id)
    safety_mode = get_user_safety_mode(user_id)
    caption = message.caption or "Please analyze this image or screenshot."

    # Download highest-resolution photo
    photo_file = message.photo[-1]
    file_info = bot.get_file(photo_file.file_id)
    downloaded = bot.download_file(file_info.file_path)

    # Save to a temporary file
    temp_img = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    temp_img_path = temp_img.name
    temp_img.write(downloaded)
    temp_img.close()

    # Route based on safety mode
    if safety_mode == "strict":
        register_pending_approval(
            message=message,
            prompt=caption,
            cwd=cwd,
            model=model,
            image_path=temp_img_path,
        )
    else:
        status_msg = bot.reply_to(message, "⚡ <i>Memproses gambar...</i>", parse_mode="HTML")
        threading.Thread(
            target=execute_and_reply,
            kwargs={
                "chat_id": message.chat.id,
                "user_id": user_id,
                "prompt": caption,
                "cwd": cwd,
                "model": model,
                "image_path": temp_img_path,
                "reply_to_message_id": status_msg.message_id,
                "is_plan_mode": False,
            },
            daemon=True,
        ).start()


# ==========================================
# Text Message Handler
# ==========================================

@bot.message_handler(func=lambda msg: True, content_types=["text"])
@require_auth
def handle_text(message: Message):
    user_id = message.from_user.id
    cwd = get_user_cwd(user_id)
    model = get_user_model(user_id)
    safety_mode = get_user_safety_mode(user_id)
    prompt = message.text.strip()

    if not prompt:
        return

    # In strict mode: require user approval before dispatching execution
    if safety_mode == "strict":
        register_pending_approval(
            message=message,
            prompt=prompt,
            cwd=cwd,
            model=model,
        )
    else:
        # Direct mode: execute immediately
        status_msg = bot.reply_to(message, "⚡ <i>Menjalankan instruksi...</i>", parse_mode="HTML")
        threading.Thread(
            target=execute_and_reply,
            kwargs={
                "chat_id": message.chat.id,
                "user_id": user_id,
                "prompt": prompt,
                "cwd": cwd,
                "model": model,
                "reply_to_message_id": status_msg.message_id,
                "is_plan_mode": False,
            },
            daemon=True,
        ).start()


def main():
    print("=" * 60)
    print("🚀 Antigravity Telegram Bridge v1.1 is starting...")
    print(f"• Binary Path: {config.AGY_BINARY}")
    print(f"• Default Working Directory: {config.WORKING_DIR}")
    print(f"• Default Safety Mode: {config.DEFAULT_SAFETY_MODE.upper()}")
    print(f"• Fail-Secure Timeout: {config.APPROVAL_TIMEOUT_SECONDS}s")
    print(f"• Whitelisted User IDs: {list(config.ALLOWED_USER_IDS) if config.ALLOWED_USER_IDS else 'None (All blocked)'}")
    print("=" * 60)
    print("Listening for incoming Telegram messages (Press Ctrl+C to stop)...")

    bot.infinity_polling(timeout=20, long_polling_timeout=20)


if __name__ == "__main__":
    main()
