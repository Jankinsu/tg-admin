"""Telegram handlers. Phase 1 has no elevated operation route."""
from __future__ import annotations

import asyncio
import logging
import os

from urllib.parse import urlsplit

from telegram import BotCommand, BotCommandScopeChat, Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from .core import AuthState, execute, validate_command

log = logging.getLogger(__name__)

# Only implemented commands appear in the native Telegram menu.
COMMAND_MENU = (
    BotCommand("help", "查看可用命令与使用示例"),
    BotCommand("login", "使用 TOTP 验证码登录"),
    BotCommand("whoami", "查询管理会话状态"),
    BotCommand("run", "执行普通 Linux 命令"),
    BotCommand("logout", "结束管理会话"),
)

HELP_TEXT = """🛠 tg-admin · 当前可用命令（Phase 1）

/help 或 /menu — 显示本帮助
/login 123456 — 使用身份验证器的 6 位动态码登录
/whoami — 检查会话是否仍有效
/run <Linux 命令> — 以 tgexec 普通用户执行命令
/logout — 主动结束管理会话

📋 常用示例（先 /login）：
/run whoami
/run uptime
/run free -h
/run df -h
/run systemctl status docker --no-pager

🔐 说明：
• 登录最长保持 15 分钟，空闲 5 分钟自动退出。
• /run 是非交互式普通权限，不提供 sudo 或 Docker 管理权限。
• 结果回传退出码、标准输出及错误输出。
• /sudo、/docker 仍在后续阶段开发。
• 登录验证码不要分享；Telegram Bot 私聊并非端到端加密。

点输入框旁 Menu 可查看并选择命令；部分命令需要自行补上参数。"""


def read_proxy() -> str | None:
    """Optional Telegram API proxy; never affects executed shell commands."""
    value = os.environ.get("TG_ADMIN_PROXY", "").strip()
    if not value:
        return None
    try:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https", "socks5") or not parts.hostname or not parts.port:
            raise ValueError
    except ValueError as exc:
        raise ValueError("Invalid TG_ADMIN_PROXY; use http://host:port or socks5://host:port") from exc
    return value


def build_application(token: str, proxy: str | None):
    builder = ApplicationBuilder().token(token).concurrent_updates(False)
    if proxy:
        builder = builder.proxy(proxy).get_updates_proxy(proxy)
    return builder.build()


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
        await update.message.reply_text(HELP_TEXT)

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

    app = build_application(token, read_proxy())
    for name, handler in [
        ("start", help_cmd), ("help", help_cmd), ("menu", help_cmd), ("login", login),
        ("logout", logout), ("whoami", whoami), ("run", run_cmd),
    ]:
        app.add_handler(CommandHandler(name, handler))
    log.info("tg-admin Phase 1 starting, no sudo/Docker access enabled")
    await app.initialize()
    await app.start()
    # Register a native command menu for the authorized private chat only.
    # Do not expose admin commands as a global bot menu.
    try:
        await app.bot.set_my_commands(COMMAND_MENU, scope=BotCommandScopeChat(chat_id=chat_id))
    except Exception:
        log.warning("Could not register Telegram command menu; /help still works.")
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
