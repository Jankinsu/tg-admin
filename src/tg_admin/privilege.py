"""Non-root client for one-shot elevated authorization agent.

A root-owned sudoers rule allows only this fixed agent, never arbitrary sudo.
"""
from __future__ import annotations

import json
import subprocess

from .core import ExecResult, validate_command

PRIVILEGED_CMD = [
    "/usr/bin/sudo", "-n", "--",
    "/usr/bin/python3", "-I", "/opt/tg-admin/privileged.py",
]


class AgentError(RuntimeError):
    pass


def request_agent(payload: dict, *, timeout: int = 8) -> dict:
    try:
        result = subprocess.run(
            PRIVILEGED_CMD,
            input=json.dumps({"version": 1, **payload}),
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AgentError("特权执行入口不可用或响应超时") from exc
    if result.returncode:
        raise AgentError("特权执行入口未获得授权；请检查安装与 sudoers")
    try:
        data = json.loads(result.stdout)
        if not isinstance(data, dict) or type(data.get("ok")) is not bool:
            raise ValueError("bad output")
        return data
    except (ValueError, TypeError) as exc:
        raise AgentError("特权执行入口返回无效数据") from exc


def create_request(command: str, user_id: int, chat_id: int) -> dict:
    return request_agent({
        "action": "create", "command": validate_command(command),
        "user_id": user_id, "chat_id": chat_id,
    })


def cancel_request(request_id: str, user_id: int, chat_id: int) -> dict:
    return request_agent({
        "action": "cancel", "request_id": request_id,
        "user_id": user_id, "chat_id": chat_id,
    })


def approve_request(request_id: str, otp: str, user_id: int, chat_id: int) -> dict:
    # Long-running authorized Docker pulls etc may need up to 120s.
    return request_agent({
        "action": "execute", "request_id": request_id, "otp": otp,
        "user_id": user_id, "chat_id": chat_id,
    }, timeout=130)
