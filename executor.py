"""
Subprocess executor for Google Antigravity (agy) CLI.
Handles command construction, timeouts, ANSI stripping, and JSON output parsing.
"""

import json
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Any

# Regex to strip ANSI escape codes (colors, spinners, terminal codes)
ANSI_ESCAPE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from terminal output."""
    if not text:
        return ""
    return ANSI_ESCAPE.sub('', text)


@dataclass
class ExecutionResult:
    success: bool
    response: str
    conversation_id: Optional[str] = None
    duration_seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    error: Optional[str] = None
    raw_stdout: str = ""
    raw_stderr: str = ""


class AgyExecutor:
    """Executes agy CLI commands non-interactively with session continuity."""

    def __init__(self, binary_path: str, default_timeout: int = 300):
        self.binary_path = binary_path
        self.default_timeout = default_timeout

    def run(
        self,
        prompt: str,
        cwd: str,
        conversation_id: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[int] = None,
        image_path: Optional[str] = None,
    ) -> ExecutionResult:
        """
        Execute a prompt using agy CLI.
        Supports multi-turn conversations via conversation_id and visual inputs via image_path.
        """
        start_time = time.time()
        actual_timeout = timeout or self.default_timeout

        # Construct prompt if image attachment is provided
        final_prompt = prompt
        if image_path and os.path.exists(image_path):
            abs_img = os.path.abspath(image_path)
            if not final_prompt or not final_prompt.strip():
                final_prompt = f"Please inspect and analyze this attached image/screenshot: {abs_img}"
            else:
                final_prompt = f"[Attached Image: {abs_img}]\n\n{final_prompt}"

        # Build command arguments
        cmd = [
            self.binary_path,
            "-p", final_prompt,
            "--output-format", "json",
            "--dangerously-skip-permissions",
        ]

        if conversation_id:
            cmd.extend(["--conversation", conversation_id])

        if model:
            cmd.extend(["--model", model])

        # Prepare environment: ensure user's ~/.local/bin is in PATH
        env = os.environ.copy()
        user_home = os.path.expanduser("~")
        local_bin = os.path.join(user_home, ".local", "bin")
        current_path = env.get("PATH", "")
        if local_bin not in current_path:
            env["PATH"] = f"{local_bin}{os.pathsep}{current_path}"
        env["HOME"] = user_home

        # Ensure cwd exists
        effective_cwd = cwd if os.path.isdir(cwd) else str(Path.home())

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=effective_cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )

            try:
                raw_stdout, raw_stderr = proc.communicate(timeout=actual_timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                raw_stdout, raw_stderr = proc.communicate()
                duration = time.time() - start_time
                return ExecutionResult(
                    success=False,
                    response="",
                    duration_seconds=duration,
                    error=f"⏱️ Execution timed out after {actual_timeout} seconds.",
                    raw_stdout=strip_ansi(raw_stdout),
                    raw_stderr=strip_ansi(raw_stderr),
                )

            duration = time.time() - start_time
            clean_stdout = strip_ansi(raw_stdout).strip()
            clean_stderr = strip_ansi(raw_stderr).strip()

            # Attempt to parse structured JSON response from agy
            if clean_stdout:
                try:
                    data = json.loads(clean_stdout)
                    resp_text = data.get("response", "")
                    cid = data.get("conversation_id", conversation_id)
                    usage = data.get("usage", {})
                    status = data.get("status", "SUCCESS")

                    return ExecutionResult(
                        success=(status == "SUCCESS" and proc.returncode == 0),
                        response=resp_text.strip(),
                        conversation_id=cid,
                        duration_seconds=round(data.get("duration_seconds", duration), 2),
                        input_tokens=usage.get("input_tokens", 0),
                        output_tokens=usage.get("output_tokens", 0),
                        total_tokens=usage.get("total_tokens", 0),
                        error=None if proc.returncode == 0 else clean_stderr,
                        raw_stdout=clean_stdout,
                        raw_stderr=clean_stderr,
                    )
                except json.JSONDecodeError:
                    # Output was not JSON, fallback to raw text parsing
                    pass

            # Fallback for plain text output
            if proc.returncode == 0:
                return ExecutionResult(
                    success=True,
                    response=clean_stdout or "Done (no output returned).",
                    conversation_id=conversation_id,
                    duration_seconds=round(duration, 2),
                    raw_stdout=clean_stdout,
                    raw_stderr=clean_stderr,
                )
            else:
                err_msg = clean_stderr or clean_stdout or f"Process exited with code {proc.returncode}"
                return ExecutionResult(
                    success=False,
                    response="",
                    conversation_id=conversation_id,
                    duration_seconds=round(duration, 2),
                    error=err_msg,
                    raw_stdout=clean_stdout,
                    raw_stderr=clean_stderr,
                )

        except FileNotFoundError:
            duration = time.time() - start_time
            return ExecutionResult(
                success=False,
                response="",
                duration_seconds=duration,
                error=(
                    f"❌ Antigravity CLI (`agy`) binary was not found at `{self.binary_path}`.\n"
                    f"Please make sure Google Antigravity CLI is installed and added to your PATH."
                ),
            )
        except Exception as e:
            duration = time.time() - start_time
            return ExecutionResult(
                success=False,
                response="",
                duration_seconds=duration,
                error=f"❌ Internal execution error: {str(e)}",
            )
