from tg_admin.input_flow import PendingInput, INPUT_TTL
from tg_admin.__main__ import COMMAND_MENU, HELP_TEXT
import pytest


def test_pending_reply_must_match_exact_prompt():
    pending = PendingInput.make("run", prompt_id=101, now=500, ttl=120)
    assert pending.matches(101, now=619.99)
    assert not pending.matches(100, now=501)
    assert not pending.matches(None, now=501)
    assert not pending.matches(101, now=620)


def test_secret_reply_expires_and_new_prompt_supersedes_old():
    previous = PendingInput.make("login", prompt_id=10, now=100)
    current = PendingInput.make("approve", prompt_id=11, now=110, ttl=60)
    assert current.matches(11, now=120)
    assert not current.matches(previous.prompt_id, now=120)
    assert not current.matches(11, now=170)
    assert INPUT_TTL == 120


def test_reject_invalid_prompt_kind():
    with pytest.raises(ValueError):
        PendingInput.make("arbitrary", prompt_id=12)


def test_menu_commands_have_parameter_help_and_reply_flow():
    names = {command.command for command in COMMAND_MENU}
    for name in ("login", "run", "sudo", "approve"):
        assert name in names
        assert f"/{name}" in HELP_TEXT
    assert "回复" in HELP_TEXT
    assert "原来的完整命令仍可直接发送" in HELP_TEXT
