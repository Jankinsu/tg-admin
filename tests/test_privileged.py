"""Phase 2 authorization logic without invoking sudo or changing the host."""
import base64
import hashlib
import hmac
import importlib.util
from pathlib import Path
import sqlite3


def module():
    file = Path(__file__).resolve().parents[1] / "privileged.py"
    spec = importlib.util.spec_from_file_location("privileged", file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def otp_at(secret, now):
    key = base64.b32decode(secret)
    n = int(now // 30)
    digest = hmac.new(key, n.to_bytes(8, "big"), hashlib.sha1).digest()
    o = digest[-1] & 15
    return f"{(int.from_bytes(digest[o:o+4], 'big') & 0x7fffffff) % 1000000:06d}"


SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
USER = 123
CHAT = 123


def ctx(tmp_path):
    m = module()
    conn = sqlite3.connect(tmp_path / "auth.db")
    m.init_db(conn)
    return m, conn


def request(m, conn, cmd, now=1700000010):
    return m.action(conn, dict(version=1, action="create", command=cmd, user_id=USER, chat_id=CHAT),
                    user_id=USER, chat_id=CHAT, secret=SECRET, now=now)


def execute(m, conn, rid, otp, now=1700000010):
    return m.action(conn, dict(version=1, action="execute", request_id=rid, otp=otp, user_id=USER, chat_id=CHAT),
                    user_id=USER, chat_id=CHAT, secret=SECRET, now=now)


def test_create_and_single_use(tmp_path):
    m, conn = ctx(tmp_path)
    created = request(m, conn, "id")
    rid = created["request_id"]
    assert created["ok"] and created["command"] == "id"
    ok = execute(m, conn, rid, otp_at(SECRET, 1700000010))
    assert ok["authorized"] and ok["command"] == "id"
    assert not execute(m, conn, rid, otp_at(SECRET, 1700000010))["ok"]


def test_failed_otp_does_not_execute(tmp_path):
    m, conn = ctx(tmp_path)
    rid = request(m, conn, "echo hi")["request_id"]
    assert not execute(m, conn, rid, "999xxx")["ok"]
    assert conn.execute("SELECT status FROM requests WHERE id=?", (rid,)).fetchone()[0] == "pending"


def test_second_request_cancels_first(tmp_path):
    m, conn = ctx(tmp_path)
    first = request(m, conn, "echo one")["request_id"]
    second = request(m, conn, "echo two")["request_id"]
    code = otp_at(SECRET, 1700000010)
    assert not execute(m, conn, first, code)["ok"]
    assert execute(m, conn, second, code)["command"] == "echo two"


def test_expired_and_cancelled(tmp_path):
    m, conn = ctx(tmp_path)
    first = request(m, conn, "id")["request_id"]
    assert not execute(m, conn, first, otp_at(SECRET, 1700000100), now=1700000100)["ok"]
    second = request(m, conn, "id", now=1700000200)["request_id"]
    cancelled = m.action(conn, dict(version=1, action="cancel", request_id=second, user_id=USER, chat_id=CHAT),
                         user_id=USER, chat_id=CHAT, secret=SECRET, now=1700000200)
    assert cancelled["cancelled"]
    assert not execute(m, conn, second, otp_at(SECRET, 1700000200), now=1700000200)["ok"]


def test_identity_binding(tmp_path):
    m, conn = ctx(tmp_path)
    rid = request(m, conn, "id")["request_id"]
    bad = m.action(conn, dict(version=1, action="execute", request_id=rid,
                              otp=otp_at(SECRET, 1700000010), user_id=1, chat_id=CHAT),
                   user_id=USER, chat_id=CHAT, secret=SECRET, now=1700000010)
    assert not bad["ok"]


def test_replay_otp_on_new_request(tmp_path):
    m, conn = ctx(tmp_path)
    code = otp_at(SECRET, 1700000010)
    first = request(m, conn, "id")["request_id"]
    assert execute(m, conn, first, code)["ok"]
    second = request(m, conn, "id")["request_id"]
    assert not execute(m, conn, second, code)["ok"]


def test_otp_lockout(tmp_path):
    m, conn = ctx(tmp_path)
    rid = request(m, conn, "id")["request_id"]
    for _ in range(5):
        assert not execute(m, conn, rid, "000000a")["ok"]
    assert not execute(m, conn, rid, otp_at(SECRET, 1700000010))["ok"]


def test_no_shell_run_before_authorized(tmp_path, monkeypatch):
    m, conn = ctx(tmp_path)
    rid = request(m, conn, "touch /tmp/should-not-happen")["request_id"]
    monkeypatch.setattr(m, "run_command", lambda command: (_ for _ in ()).throw(AssertionError("executed")))
    result = execute(m, conn, rid, "not-an-otp")
    assert not result["ok"]


def test_agent_runs_bounded_output():
    m = module()
    r = m.run_command("printf safe")
    assert r["stdout"] == "safe" and r["code"] == 0
