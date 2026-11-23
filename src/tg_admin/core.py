"""Authentication state, command validation, and isolated execution."""
from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable

import pyotp

SESSION_SECONDS = 15 * 60
IDLE_SECONDS = 5 * 60
MAX_ATTEMPTS = 5
LOCK_SECONDS = 5 * 60
MAX_COMMAND_LENGTH = 2048
RUNNER = "/opt/tg-admin/runner.py"
RUNNER_CMD = ["/usr/bin/sudo", "-n", "-u", "tgexec", "--", "/usr/bin/python3", "-I", RUNNER]


class AuthState:
    """One private-chat user, with time-based expiry and OTP replay prevention."""

    def __init__(self, secret: str, clock: Callable[[], float] = time.time):
        self.totp = pyotp.TOTP(secret)
        self.clock = clock
        self.valid_until = 0.0
        self.idle_until = 0.0
        self.failed = 0
        self.locked_until = 0.0
        self.last_step = -1

    def login(self, code: str) -> str:
        now = self.clock()
        if now < self.locked_until:
            return "认证暂时锁定，请稍后重试。"
        if not re.fullmatch(r"[0-9]{6}", code):
            return self._failed(now)
        step = int(now // self.totp.interval)
        if step <= self.last_step or not self.totp.verify(code, for_time=now, valid_window=0):
            return self._failed(now)
        self.last_step = step
        self.failed = 0
        self.valid_until = now + SESSION_SECONDS
        self.idle_until = now + IDLE_SECONDS
        return "✅ 登录成功：会话最长 15 分钟，空闲 5 分钟自动过期。"

    def _failed(self, now: float) -> str:
        self.failed += 1
        if self.failed >= MAX_ATTEMPTS:
            self.failed = 0
            self.locked_until = now + LOCK_SECONDS
            return "认证失败次数过多，已暂时锁定 5 分钟。"
        return "验证码错误或已经使用。"

    def authorized(self, *, touch: bool = False) -> bool:
        now = self.clock()
        ok = now < self.valid_until and now < self.idle_until
        if not ok:
            self.valid_until = self.idle_until = 0
        elif touch:
            self.idle_until = min(self.valid_until, now + IDLE_SECONDS)
        return ok

    def logout(self) -> None:
        self.valid_until = self.idle_until = 0


@dataclass(frozen=True)
class ExecResult:
    stdout: str
    stderr: str
    code: int
    truncated: bool = False


def validate_command(command: str) -> str:
    command = command.strip()
    if not command:
        raise ValueError("命令不能为空")
    if len(command) > MAX_COMMAND_LENGTH or "\x00" in command or "\n" in command or "\r" in command:
        raise ValueError("命令过长或包含换行/非法字符")
    return command


def execute(command: str) -> ExecResult:
    """Run one command ONLY as tgexec through fixed sudoers entry."""
    command = validate_command(command)
    try:
        completed = subprocess.run(
            RUNNER_CMD,
            input=json.dumps({"command": command}),
            text=True,
            capture_output=True,
            timeout=28,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return ExecResult("", "执行入口超时；任务状态可能需要通过 SSH 核查。", 124)
    except OSError:
        return ExecResult("", "无法启动命令执行入口，请检查本机安装与权限。", 126)
    if completed.returncode != 0:
        return ExecResult("", "执行入口失败，请检查 sudoers、runner 权限和 systemd 日志。", 126)
    try:
        data = json.loads(completed.stdout)
        if type(data) is not dict:
            raise ValueError("invalid response")
        return ExecResult(
            str(data["stdout"]),
            str(data["stderr"]),
            int(data["code"]),
            bool(data["truncated"]),
        )
    except (ValueError, KeyError, TypeError):
        return ExecResult("", "执行入口返回异常。", 125)
