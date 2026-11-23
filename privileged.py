#!/usr/bin/python3
"""Root-owned one-shot authorization agent for tg-admin Phase 2.

Invoked ONLY via exact sudoers command; never import app-controlled code.
No network listener, no imported third-party modules, no root session cache.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import selectors
import signal
import sqlite3
import subprocess
import sys
import time

STATE_DIR = Path("/var/lib/tg-admin")
DB_PATH = STATE_DIR / "authorization.sqlite3"
SECRET_PATH = Path("/etc/tg-admin/l2.secret")
MAX_INPUT = 4096
MAX_COMMAND = 2048
MAX_OUTPUT = 24576
TTL_SECONDS = 60
LOCK_SECONDS = 300
MAX_FAILURES = 5
RUN_TIMEOUT = 120
VERSION = 1


def response(ok: bool, **values):
    return {"ok": ok, **values}


def verify_otp(secret: str, otp: str, now: float, last_counter: int) -> int | None:
    """RFC 6238 SHA1 6-digit TOTP with strict one-step replay prevention."""
    if not isinstance(otp, str) or not re.fullmatch(r"[0-9]{6}", otp):
        return None
    key = base64.b32decode(secret.upper(), casefold=True)
    if len(key) < 14:
        raise ValueError("L2 secret too short")
    counter = int(now // 30)
    if counter <= last_counter:
        return None
    data = counter.to_bytes(8, "big")
    digest = hmac.new(key, data, hashlib.sha1).digest()
    offset = digest[-1] & 15
    number = (int.from_bytes(digest[offset:offset + 4], "big") & 0x7fffffff) % 1_000_000
    return counter if hmac.compare_digest(f"{number:06d}", otp) else None


def init_db(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS requests (
          id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, chat_id INTEGER NOT NULL,
          command TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL,
          status TEXT NOT NULL CHECK(status IN ('pending','consumed','cancelled','expired'))
        );
        CREATE TABLE IF NOT EXISTS auth (
          id INTEGER PRIMARY KEY CHECK(id=1), last_counter INTEGER NOT NULL,
          failed INTEGER NOT NULL, locked_until REAL NOT NULL
        );
        INSERT OR IGNORE INTO auth VALUES (1,-1,0,0);
    """)


def validate_request(obj, user_id, chat_id):
    if type(user_id) is not int or type(chat_id) is not int or user_id <= 0 or chat_id <= 0:
        raise ValueError("invalid identity")
    if type(obj.get("user_id")) is not int or type(obj.get("chat_id")) is not int:
        raise ValueError("invalid identity")
    if obj["user_id"] != user_id or obj["chat_id"] != chat_id:
        raise ValueError("identity mismatch")


def action(conn, req, *, user_id: int, chat_id: int, secret: str, now: float | None = None):
    if now is None:
        now = time.time()
    if not isinstance(req, dict) or req.get("version") != VERSION:
        return response(False, error="bad request")
    op = req.get("action")
    try:
        validate_request(req, user_id, chat_id)
    except ValueError:
        return response(False, error="not authorized")
    if op == "create":
        command = req.get("command")
        if not isinstance(command, str) or not command.strip() or len(command) > MAX_COMMAND or any(c in command for c in "\r\n\x00"):
            return response(False, error="invalid command")
        command = command.strip()
        request_id = secrets.token_hex(16)
        with conn:
            conn.execute("UPDATE requests SET status='expired' WHERE status='pending' AND expires<=?", (now,))
            # Only one pending request for this user; a new command invalidates the old.
            conn.execute("UPDATE requests SET status='cancelled' WHERE status='pending' AND user_id=? AND chat_id=?", (user_id, chat_id))
            conn.execute("INSERT INTO requests VALUES (?,?,?,?,?,?,?)", (request_id, user_id, chat_id, command, now, now + TTL_SECONDS, "pending"))
        return response(True, request_id=request_id, command=command, expires_in=TTL_SECONDS)

    request_id = req.get("request_id")
    if not isinstance(request_id, str) or not re.fullmatch(r"[a-f0-9]{32}", request_id):
        return response(False, error="invalid request id")
    if op == "cancel":
        with conn:
            changed = conn.execute(
                "UPDATE requests SET status='cancelled' WHERE id=? AND user_id=? AND chat_id=? AND status='pending'",
                (request_id, user_id, chat_id),
            ).rowcount
        return response(bool(changed), cancelled=bool(changed))
    if op != "execute":
        return response(False, error="invalid action")

    # Root-owned SQLite lock serializes OTP verification and consuming a request.
    # Execute only after the transaction commits. No token or OTP ever stored.
    conn.execute("BEGIN IMMEDIATE")
    try:
        record = conn.execute(
            "SELECT command,expires,status FROM requests WHERE id=? AND user_id=? AND chat_id=?",
            (request_id, user_id, chat_id),
        ).fetchone()
        if record is None or record[2] != "pending":
            conn.rollback()
            return response(False, error="request missing or consumed")
        command, expires, _ = record
        if now >= expires:
            conn.execute("UPDATE requests SET status='expired' WHERE id=?", (request_id,))
            conn.commit()
            return response(False, error="request expired")
        last_counter, failed, locked_until = conn.execute(
            "SELECT last_counter,failed,locked_until FROM auth WHERE id=1"
        ).fetchone()
        if now < locked_until:
            conn.rollback()
            return response(False, error="verification temporarily locked")
        counter = verify_otp(secret, req.get("otp"), now, last_counter)
        if counter is None:
            failed += 1
            conn.execute("UPDATE auth SET failed=?,locked_until=? WHERE id=1",
                         (0 if failed >= MAX_FAILURES else failed,
                          now + LOCK_SECONDS if failed >= MAX_FAILURES else 0))
            conn.commit()
            return response(False, error="invalid or already used code")
        conn.execute("UPDATE auth SET last_counter=?,failed=0,locked_until=0 WHERE id=1", (counter,))
        conn.execute("UPDATE requests SET status='consumed' WHERE id=?", (request_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return response(True, command=command, request_id=request_id, authorized=True)


def run_command(command):
    """Execute the single authorized command, cap output and terminate timed-out groups."""
    proc = subprocess.Popen(
        ["/bin/sh", "-c", command],
        cwd="/", env={"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/root", "LANG": "C.UTF-8"},
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True,
    )
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ, "stdout")
    sel.register(proc.stderr, selectors.EVENT_READ, "stderr")
    deadline = time.monotonic() + RUN_TIMEOUT
    total = 0
    truncated = timed_out = False
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
                size = min(len(chunk), MAX_OUTPUT - total)
                buffers[key.data].extend(chunk[:size])
                total += size
                if size < len(chunk):
                    truncated = True
                    break
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
        code = proc.wait()
        timed_out = True
    finally:
        sel.close()
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()
    return dict(
        stdout=buffers["stdout"].decode("utf-8", "replace"),
        stderr=buffers["stderr"].decode("utf-8", "replace") + ("\nTimed out" if timed_out else ""),
        code=124 if timed_out else code,
        truncated=truncated,
    )


def main():
    if os.geteuid() != 0:
        print(json.dumps(response(False, error="requires root")))
        return 1
    raw = sys.stdin.buffer.read(MAX_INPUT + 1)
    if len(raw) > MAX_INPUT:
        print(json.dumps(response(False, error="request too large")))
        return 0
    try:
        req = json.loads(raw)
        # Identity must come from protected root-owned config, not the caller's claim.
        config = Path("/etc/tg-admin/l2-identity.json")
        with config.open("r", encoding="utf-8") as f:
            identity = json.load(f)
        secret = SECRET_PATH.read_text(encoding="ascii").strip()
        STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        if STATE_DIR.is_symlink() or STATE_DIR.stat().st_uid != 0 or STATE_DIR.stat().st_mode & 0o077:
            raise ValueError("unsafe state directory")
        conn = sqlite3.connect(DB_PATH, timeout=10)
        try:
            init_db(conn)
            result = action(conn, req, user_id=identity["user_id"], chat_id=identity["chat_id"], secret=secret)
        finally:
            conn.close()
        if result.get("authorized"):
            command = result.pop("command")
            result.pop("authorized")
            result.update(run_command(command))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, OSError, sqlite3.Error, KeyError, TypeError):
        print(json.dumps(response(False, error="privileged agent unavailable or invalid request")))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
