"""Telegram handlers. Phase 1 has no elevated operation route."""
from __future__ import annotations

import asyncio
import logging
import os

from urllib.parse import urlsplit

from telegram import BotCommand, BotCommandScopeChat, Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from .core import AuthState, execute, validate_command
from .privilege import AgentError, approve_request, cancel_request, create_request

log = logging.getLogger(__name__)

# Only implemented commands appear in the native Telegram menu.
COMMAND_MENU = (
    BotCommand("help", "查看可用命令与使用示例"),
    BotCommand("login", "使用 TOTP 验证码登录"),
    BotCommand("whoami", "查询管理会话状态"),
    BotCommand("run", "执行普通 Linux 命令"),
    BotCommand("sudo", "高权限命令：发起单次授权"),
    BotCommand("approve", "使用第二组 TOTP 授权执行"),
    BotCommand("cancel", "取消待执行高权限命令"),
    BotCommand("logout", "结束管理会话"),
)

HELP_TEXT = """🛠 tg-admin · 当前可用命令（Phase 2）

/help 或 /menu — 显示本帮助
/login 123456 — 使用身份验证器的 6 位动态码登录
/whoami — 检查会话是否仍有效
/run <Linux 命令> — 以 tgexec 普通用户执行命令
/logout — 主动结束管理会话
/sudo <命令> — 提交高权限命令并预览
/approve 123456 — 用第二组 TOTP 仅授权当前命令一次
/cancel — 取消当前待授权命令

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
• /sudo 为第二组 TOTP 单次授权；命令提交 60 秒后失效。
• 授权前请检查完整命令，高权限执行会修改真实系统。
• /docker 便捷命令仍在后续阶段开发，可使用 /sudo docker ...。
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
    pending_request: str | None = None

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
        nonlocal pending_request
        if not allowed(update):
            return
        auth.logout()
        if pending_request:
            rid, pending_request = pending_request, None
            try:
                await asyncio.to_thread(cancel_request, rid, user_id, chat_id)
            except AgentError:
                log.warning("Could not cancel pending privilege request during logout.")
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


    async def sudo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        nonlocal pending_request
        if not allowed(update):
            return
        if not auth.authorized(touch=True):
            await update.message.reply_text("请先完成 /login。")
            return
        raw = update.message.text or ""
        try:
            command = validate_command(raw.partition(" ")[2])
            outcome = await asyncio.to_thread(create_request, command, user_id, chat_id)
        except (ValueError, AgentError) as exc:
            await update.message.reply_text(f"无法创建授权请求：{exc}")
            return
        if not outcome.get("ok"):
            await update.message.reply_text("无法创建授权请求：" + outcome.get("error", "未知错误"))
            return
        pending_request = outcome["request_id"]
        await update.message.reply_text(
            "🔐 高权限请求（尚未执行）\n\n"
            f"{outcome['command']}\n\n"
            "请检查以上完整命令。60 秒内用第二组身份验证器发送 /approve <6位码>。"
            "任何新 /sudo 命令将撤销前一个请求。\n使用 /cancel 放弃。"
        )

    async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        nonlocal pending_request
        if not allowed(update):
            return
        if not pending_request:
            await update.message.reply_text("当前没有待授权命令。")
            return
        rid, pending_request = pending_request, None
        try:
            await asyncio.to_thread(cancel_request, rid, user_id, chat_id)
        except AgentError:
            log.warning("Cancel request could not reach privileged agent.")
        await update.message.reply_text("已取消当前待授权请求。")

    async def approve_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        nonlocal pending_request
        if not allowed(update):
            return
        raw_code = context.args[0] if len(context.args) == 1 else ""
        try:
            await update.message.delete()
        except Exception:
            pass
        if not auth.authorized(touch=True):
            await update.effective_chat.send_message("管理会话已过期，请重新 /login。")
            return
        if not pending_request:
            await update.effective_chat.send_message("当前没有待授权命令，请先 /sudo <命令>。")
            return
        if not (len(raw_code) == 6 and raw_code.isascii() and raw_code.isdigit()):
            await update.effective_chat.send_message("请输入第二组身份验证器的 6 位数字码：/approve 123456")
            return
        rid, pending_request = pending_request, None
        await update.effective_chat.send_message("⏳ 正在验证并执行本次高权限请求…")
        try:
            outcome = await asyncio.to_thread(approve_request, rid, raw_code, user_id, chat_id)
        except AgentError as exc:
            await update.effective_chat.send_message(f"授权执行失败：{exc}")
            return
        if not outcome.get("ok"):
            await update.effective_chat.send_message("未执行：" + outcome.get("error", "认证失败"))
            return
        text = (
            f"🔐 单次高权限操作完成\nexit={outcome['code']}\n"
            f"STDOUT:\n{outcome['stdout'] or '(empty)'}\n"
            f"STDERR:\n{outcome['stderr'] or '(empty)'}"
        )
        if outcome.get("truncated"):
            text += "\n[输出超过上限，已终止执行进程]"
        for index in range(0, len(text), 3500):
            await update.effective_chat.send_message(text[index:index + 3500])

    app = build_application(token, read_proxy())
    for name, handler in [
        ("start", help_cmd), ("help", help_cmd), ("menu", help_cmd), ("login", login),
        ("logout", logout), ("whoami", whoami), ("run", run_cmd),
        ("sudo", sudo_cmd), ("approve", approve_cmd), ("cancel", cancel_cmd),
    ]:
        app.add_handler(CommandHandler(name, handler))
    log.info("tg-admin starting with optional L2 one-shot privileged agent")
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
