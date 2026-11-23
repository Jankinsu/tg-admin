import subprocess

import pyotp
import pytest

from tg_admin.core import AuthState, ExecResult, execute, validate_command


def test_login_replay_and_idle_expiry():
    now = [1_700_000_010.0]
    auth = AuthState("JBSWY3DPEHPK3PXP", clock=lambda: now[0])
    code = pyotp.TOTP("JBSWY3DPEHPK3PXP").at(now[0])
    assert "登录成功" in auth.login(code)
    assert auth.authorized(touch=True)
    assert "已经使用" in auth.login(code)
    now[0] += 301
    assert not auth.authorized()


def test_failed_login_lockout():
    now = [1_700_000_010.0]
    auth = AuthState("JBSWY3DPEHPK3PXP", clock=lambda: now[0])
    for _ in range(4):
        auth.login("000000")
    assert "锁定" in auth.login("000000")
    assert "锁定" in auth.login(pyotp.TOTP("JBSWY3DPEHPK3PXP").at(now[0]))


@pytest.mark.parametrize("command", ["", "x\ny", "x\ry", "x\x00y", "x" * 2049])
def test_invalid_commands(command):
    with pytest.raises(ValueError):
        validate_command(command)


def test_runner_command_is_fixed(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, '{"stdout":"ok","stderr":"","code":0,"truncated":false}', "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = execute("id")
    assert result == ExecResult("ok", "", 0, False)
    argv, kwargs = calls[0]
    assert argv[-3:] == ["/usr/bin/python3", "-I", "/opt/tg-admin/runner.py"]
    assert "id" not in argv
    assert kwargs["input"] == '{"command": "id"}'


def test_sudo_error_is_not_echoed(monkeypatch):
    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, "", "sensitive diagnostic")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = execute("id")
    assert result.code == 126
    assert "sensitive diagnostic" not in result.stderr
