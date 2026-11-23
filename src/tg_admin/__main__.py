"""Telegram handlers. Phase 1 has no elevated operation route."""
from __future__ import annotations

import asyncio
import logging
import os

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from .core import AuthState, execute, validate_command

log = logging.getLogger(__name__)


def load_config() -> tuple[str, int, int, str]:
    token = os.environ["TG_ADMIN_BOT_TOKEN"].strip()
    uid = int(os.environ["TG_ADMIN_USER_ID"])
    cid = int(os.environ["TG_ADMIN_CHAT_ID"])
    secret = os.environ["TG_ADMIN_TOTP_SECRET"].strip()
    if not token or not secret or uid <= 0 or cid <= 0:
        raise ValueError("invalid config")
    return token, uid, cid, secret


async def main_bot() -> None:
    token, user_id, chat_id, secret = load_config()
    auth = AuthState(secret)

    def allowed(update: Update) -> bool:
        return (
            update.effective_user is not None
            and update.effective_chat is not None
            and update.effective_chat.type == "private"
            and update.effective_user.id == user_id
            and update.effective_chat.id == chat_id
        )

    async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not allowed(update):
            return
        await update.message.reply_text("/login <6位验证码>\n/logout\n/whoami\n/run <命令>\n目前仅支持普通权限，/sudo 尚未实现。")

    async def login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not allowed(update):
            return
        if len(context.args) != 1:
            await update.message.reply_text("用法：/login <6位验证码>（私聊消息不是端到端加密）")
            return
        code = context.args[0]
        try:
            await update.message.delete()
        except Exception:
            pass  # Telegram may deny deletion; never log the code.
        await update.effective_chat.send_message(auth.login(code))

    async def logout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not allowed(update):
            return
        auth.logout()
        await update.message.reply_text("管理会话已退出。")

    async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not allowed(update):
            return
        await update.message.reply_text("✅ 管理会话有效" if auth.authorized() else "🔒 未登录或会话已过期")

    async def run_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not allowed(update):
            return
        if not auth.authorized(touch=True):
            await update.message.reply_text("请先通过 /login 完成 TOTP 认证。")
            return
        raw = update.message.text or ""
        command = raw.partition(" ")[2]
        try:
            command = validate_command(command)
        except ValueError as exc:
            await update.message.reply_text(str(exc))
            return
        result = await asyncio.to_thread(execute, command)
        out = f"exit={result.code}\nSTDOUT:\n{result.stdout or '(empty)'}\nSTDERR:\n{result.stderr or '(empty)'}"
        if result.truncated:
            out += "\n[输出已达到 24KiB 上限，进程已终止]"
        # Telegram text cap 4096; split conservatively. Do not log commands/outputs.
        for index in range(0, len(out), 3500):
            await update.message.reply_text(out[index:index + 3500])

    app = ApplicationBuilder().token(token).concurrent_updates(False).build()
    for name, handler in [
        ("start", help_cmd), ("help", help_cmd), ("login", login),
        ("logout", logout), ("whoami", whoami), ("run", run_cmd),
    ]:
        app.add_handler(CommandHandler(name, handler))
    log.info("tg-admin Phase 1 starting, no sudo/Docker access enabled")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    try:
        await asyncio.Event().wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # HTTP client request URLs may contain the Bot Token: never log them.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    try:
        asyncio.run(main_bot())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
