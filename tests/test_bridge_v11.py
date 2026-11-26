"""
Comprehensive unit and integration tests for Antigravity Telegram Bridge v1.1.
Verifies safety toggles, approval card generation, fail-secure timeouts,
resumable checkpoints, dry-run plan mode, and executor argument construction.
"""

import os
import sys
import time
import threading
from unittest.mock import MagicMock, patch

import pytest

# Ensure bridge directory is on PYTHONPATH
BRIDGE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BRIDGE_DIR not in sys.path:
    sys.path.insert(0, BRIDGE_DIR)

import config
from executor import AgyExecutor, ExecutionResult, strip_ansi
import bot


class TestConfigAndSecurity:
    """Test configuration defaults and whitelist verification."""

    def test_safety_defaults(self):
        assert config.DEFAULT_SAFETY_MODE in ("strict", "direct")
        assert config.APPROVAL_TIMEOUT_SECONDS >= 10
        assert "DRY-RUN / PLAN MODE" in config.PLAN_PROMPT_PREFIX

    def test_user_authorization_guard(self):
        with patch.object(config, "ALLOWED_USER_IDS", {12345, 67890}):
            assert config.is_allowed_user(12345) is True
            assert config.is_allowed_user(67890) is True
            assert config.is_allowed_user(99999) is False

        with patch.object(config, "ALLOWED_USER_IDS", set()):
            # Empty whitelist must deny all
            assert config.is_allowed_user(12345) is False


class TestExecutorPlanMode:
    """Test AgyExecutor command construction, ANSI stripping, and plan mode prefixing."""

    def test_ansi_stripping(self):
        colored = "\x1b[31mError:\x1b[0m \x1b[1;32mEverything failed\x1b[0m"
        assert strip_ansi(colored) == "Error: Everything failed"

    @patch("subprocess.Popen")
    def test_plan_mode_prompt_injection(self, mock_popen):
        # Mock successful subprocess run
        mock_process = MagicMock()
        mock_process.communicate.return_value = (
            '{"status": "SUCCESS", "response": "Plan created", "usage": {"total_tokens": 120}}',
            "",
        )
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        test_executor = AgyExecutor(binary_path="/usr/bin/agy", default_timeout=30)
        res = test_executor.run(
            prompt="Refactor database schema",
            cwd="/tmp",
            is_plan_mode=True,
        )

        assert res.success is True
        assert res.is_plan is True
        assert res.response == "Plan created"
        assert res.total_tokens == 120

        # Verify command arguments contained the planning prefix
        cmd_called = mock_popen.call_args[0][0]
        assert "-p" in cmd_called
        p_index = cmd_called.index("-p")
        passed_prompt = cmd_called[p_index + 1]
        assert config.PLAN_PROMPT_PREFIX in passed_prompt
        assert "Refactor database schema" in passed_prompt


class TestSafetyModesAndResumableExecution:
    """Test safety toggle, default-deny timeout, and resumption flow."""

    def setup_method(self):
        # Reset bot state before each test
        with bot.state_lock:
            bot.user_safety_modes.clear()
            bot.pending_requests.clear()
            bot.user_resumable_tasks.clear()

    def test_safety_mode_toggle(self):
        test_uid = 99991
        # Default should match config
        assert bot.get_user_safety_mode(test_uid) == config.DEFAULT_SAFETY_MODE

        bot.set_user_safety_mode(test_uid, "direct")
        assert bot.get_user_safety_mode(test_uid) == "direct"

        bot.set_user_safety_mode(test_uid, "strict")
        assert bot.get_user_safety_mode(test_uid) == "strict"

    def test_inline_keyboards_generation(self):
        req_id = "test_req_123"
        approval_kb = bot.create_approval_keyboard(req_id)
        assert approval_kb.keyboard is not None

        # Flatten button callbacks
        callbacks = [btn.callback_data for row in approval_kb.keyboard for btn in row]
        assert f"approve:{req_id}" in callbacks
        assert f"plan:{req_id}" in callbacks
        assert f"reject:{req_id}" in callbacks

        resume_kb = bot.create_resumable_keyboard(req_id)
        resume_callbacks = [btn.callback_data for row in resume_kb.keyboard for btn in row]
        assert f"resume:{req_id}" in resume_callbacks

    @patch("bot.bot.edit_message_text")
    def test_fail_secure_default_deny_timeout(self, mock_edit_msg):
        test_uid = 88881
        req_id = "req_timeout_test"

        # Register pending request
        mock_timer = MagicMock()
        req = bot.PendingRequest(
            req_id=req_id,
            user_id=test_uid,
            chat_id=12345,
            message_id=777,
            prompt="git push origin main --force",
            cwd="/tmp/project",
            timeout_seconds=180,
            timer=mock_timer,
        )

        with bot.state_lock:
            bot.pending_requests[req_id] = req

        # Trigger timeout callback
        bot.on_request_timeout(req_id)

        # Assert status transitioned to timed_out
        assert req.status == "timed_out"

        # Assert request was placed into user's resumable queue
        assert test_uid in bot.user_resumable_tasks
        assert len(bot.user_resumable_tasks[test_uid]) == 1
        assert bot.user_resumable_tasks[test_uid][0].req_id == req_id

        # Assert telegram message was edited to inform user
        mock_edit_msg.assert_called_once()
        call_kwargs = mock_edit_msg.call_args[1]
        assert "Default-Deny Timeout" in call_kwargs["text"]

    def test_resumable_history_pruning(self):
        test_uid = 88882
        with patch.object(config, "MAX_RESUME_HISTORY", 3):
            for i in range(5):
                rid = f"req_{i}"
                r = bot.PendingRequest(
                    req_id=rid,
                    user_id=test_uid,
                    chat_id=123,
                    message_id=i,
                    prompt=f"Task {i}",
                    cwd="/tmp",
                )
                with bot.state_lock:
                    bot.pending_requests[rid] = r
                with patch("bot.bot.edit_message_text"):
                    bot.on_request_timeout(rid)

            # Only 3 items should remain
            assert len(bot.user_resumable_tasks[test_uid]) == 3
            # The newest task (Task 4) should be at index 0
            assert bot.user_resumable_tasks[test_uid][0].req_id == "req_4"

    @patch("bot.bot.edit_message_text")
    @patch("bot.bot.answer_callback_query")
    def test_callback_reject(self, mock_answer, mock_edit_msg):
        test_uid = 77771
        req_id = "req_reject_test"
        mock_timer = MagicMock()
        req = bot.PendingRequest(
            req_id=req_id,
            user_id=test_uid,
            chat_id=123,
            message_id=456,
            prompt="rm -rf /",
            cwd="/tmp",
            timer=mock_timer,
        )
        with bot.state_lock:
            bot.pending_requests[req_id] = req

        call = MagicMock()
        call.from_user.id = test_uid
        call.data = f"reject:{req_id}"
        call.message.chat.id = 123
        call.message.message_id = 456

        with patch.object(config, "is_allowed_user", return_value=True):
            bot.handle_interactive_task_callbacks(call)

        mock_timer.cancel.assert_called_once()
        assert req.status == "rejected"
        assert req in bot.user_resumable_tasks[test_uid]
        mock_answer.assert_called_once()
        mock_edit_msg.assert_called_once()

    @patch("threading.Thread")
    @patch("bot.bot.edit_message_text")
    @patch("bot.bot.answer_callback_query")
    def test_callback_approve(self, mock_answer, mock_edit_msg, mock_thread):
        test_uid = 77772
        req_id = "req_approve_test"
        mock_timer = MagicMock()
        req = bot.PendingRequest(
            req_id=req_id,
            user_id=test_uid,
            chat_id=123,
            message_id=456,
            prompt="echo 'hello world'",
            cwd="/tmp",
            timer=mock_timer,
        )
        with bot.state_lock:
            bot.pending_requests[req_id] = req

        call = MagicMock()
        call.from_user.id = test_uid
        call.data = f"approve:{req_id}"
        call.message.chat.id = 123
        call.message.message_id = 456

        with patch.object(config, "is_allowed_user", return_value=True):
            bot.handle_interactive_task_callbacks(call)

        mock_timer.cancel.assert_called_once()
        assert req.status == "approved"
        mock_thread.assert_called_once()
        mock_thread.return_value.start.assert_called_once()
