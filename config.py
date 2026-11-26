"""
Configuration manager for Antigravity Telegram Bridge.
Loads and validates settings from .env file and environment variables.
"""

import os
import shutil
from pathlib import Path
from dotenv import load_dotenv

# Base directory for the bridge
BASE_DIR = Path(__file__).resolve().parent

# Load environment variables from .env
ENV_PATH = BASE_DIR / ".env"
if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH)
else:
    load_dotenv()

# Telegram Bot Token (from @BotFather)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

# Allowed User IDs (Whitelisted admins)
_raw_allowed = os.getenv("ALLOWED_USER_IDS", "").strip() or os.getenv("TELEGRAM_ALLOWED_USER_ID", "").strip()
ALLOWED_USER_IDS: set[int] = set()

if _raw_allowed:
    for item in _raw_allowed.split(","):
        cleaned = item.strip()
        if cleaned.isdigit():
            ALLOWED_USER_IDS.add(int(cleaned))

# Working Directory (default to ~/projects or current working directory)
_raw_working_dir = os.getenv("WORKING_DIR", "").strip()
if _raw_working_dir:
    WORKING_DIR = str(Path(_raw_working_dir).expanduser().resolve())
else:
    candidate = Path.home() / "projects"
    WORKING_DIR = str(candidate) if candidate.exists() else str(Path.home())

# Ensure working directory exists
if not os.path.exists(WORKING_DIR):
    try:
        os.makedirs(WORKING_DIR, exist_ok=True)
    except Exception:
        WORKING_DIR = str(Path.home())

# Execution Timeout in seconds (default: 300s = 5 minutes)
try:
    TIMEOUT_SECONDS = int(os.getenv("TIMEOUT_SECONDS", "300"))
except ValueError:
    TIMEOUT_SECONDS = 300

# Safety & Confirmation Settings (v1.1)
# DEFAULT_SAFETY_MODE can be "strict" (requires inline approval) or "direct" (immediate execution)
DEFAULT_SAFETY_MODE = os.getenv("DEFAULT_SAFETY_MODE", "strict").strip().lower()
if DEFAULT_SAFETY_MODE not in ("strict", "direct"):
    DEFAULT_SAFETY_MODE = "strict"

# Approval timeout for Fail-Secure / Default-Deny (default: 180s = 3 minutes)
try:
    APPROVAL_TIMEOUT_SECONDS = int(os.getenv("APPROVAL_TIMEOUT_SECONDS", "180"))
except ValueError:
    APPROVAL_TIMEOUT_SECONDS = 180

# Maximum resumable tasks remembered in memory per user
try:
    MAX_RESUME_HISTORY = int(os.getenv("MAX_RESUME_HISTORY", "10"))
except ValueError:
    MAX_RESUME_HISTORY = 10

# Dry-run / Plan Prompt Prefix
PLAN_PROMPT_PREFIX = os.getenv(
    "PLAN_PROMPT_PREFIX",
    (
        "You are operating in DRY-RUN / PLAN MODE. "
        "Analyze the following request and formulate a comprehensive, step-by-step execution plan "
        "and risk/impact assessment. "
        "DO NOT execute file modifications, destructive shell commands, or mutation actions yet. "
        "Present the planned steps clearly to the user for review:\n\n"
    ),
)

# Path to agy executable
_custom_agy_path = os.getenv("AGY_PATH", "").strip()


def find_agy_binary() -> str:
    """Auto-detect the absolute path to the agy CLI binary."""
    if _custom_agy_path and os.path.isfile(_custom_agy_path) and os.access(_custom_agy_path, os.X_OK):
        return _custom_agy_path

    # Check system PATH
    found = shutil.which("agy")
    if found:
        return found

    # Check common user install paths
    candidates = [
        Path.home() / ".local" / "bin" / "agy",
        Path.home() / "bin" / "agy",
        Path("/usr/local/bin/agy"),
        Path("/opt/homebrew/bin/agy"),
    ]
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return str(c)

    return "agy"


AGY_BINARY = find_agy_binary()


def is_allowed_user(user_id: int) -> bool:
    """Check if a Telegram user is authorized to control the bridge."""
    if not ALLOWED_USER_IDS:
        # If no user IDs are configured, deny all for security
        return False
    return user_id in ALLOWED_USER_IDS
