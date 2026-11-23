#!/usr/bin/python3
"""Fixed non-privileged command runner; invoked only as tgexec via sudoers.

This file and its parent directory must be root-owned and not writable by tgbot.
No dependencies outside the Python standard library are required.
"""
from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import sys
import time

MAX_REQUEST = 4096
MAX_OUTPUT = 24576
TIMEOUT = 20


def run(command: str) -> dict:
    if not isinstance(command, str) or not (0 < len(command) <= 2048) or "\x00" in command or "\n" in command or "\r" in command:
        return {"stdout": "", "stderr": "invalid command", "code": 2, "truncated": False}

    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C.UTF-8"}
    proc = subprocess.Popen(
        ["/bin/sh", "-c", command],
        cwd="/",
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    sel = selectors.DefaultSelector()
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    sel.register(proc.stdout, selectors.EVENT_READ, "stdout")
    sel.register(proc.stderr, selectors.EVENT_READ, "stderr")
    deadline = time.monotonic() + TIMEOUT
    total = 0
    truncated = False
    timed_out = False
    try:
        while sel.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            for key, _ in sel.select(timeout=min(remaining, 0.2)):
                chunk = os.read(key.fd, 4096)
                if not chunk:
                    sel.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                available = MAX_OUTPUT - total
                if len(chunk) > available:
                    truncated = True
                    buffers[key.data].extend(chunk[:max(0, available)])
                    break
                buffers[key.data].extend(chunk)
                total += len(chunk)
            if truncated:
                break
        if truncated or timed_out:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        code = proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        timed_out = True
        code = 124
    finally:
        sel.close()
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()
    return {
        "stdout": buffers["stdout"].decode("utf-8", "replace"),
        "stderr": buffers["stderr"].decode("utf-8", "replace") + ("\n执行超时" if timed_out else ""),
        "code": 124 if timed_out else code,
        "truncated": truncated,
    }


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST + 1)
        if len(raw) > MAX_REQUEST:
            raise ValueError("request too large")
        req = json.loads(raw)
        if not isinstance(req, dict) or set(req) != {"command"}:
            raise ValueError("invalid request")
        result = run(req["command"])
    except (ValueError, TypeError, json.JSONDecodeError):
        result = {"stdout": "", "stderr": "invalid request", "code": 2, "truncated": False}
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
