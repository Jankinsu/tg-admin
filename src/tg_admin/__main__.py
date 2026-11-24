"""Telegram command handlers and reply-based parameter input."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from urllib.parse import urlsplit

from telegram import BotCommand, BotCommandScopeChat, ForceReply, Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

from .core import AuthState, execute, validate_command
from .input_flow import PendingInput
from .privilege import AgentError, approve_request, cancel_request, create_request

log = logging.getLogger(__name__)

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

HELP_TEXT = """🛠 tg-admin · 可用命令

点 Menu 选择带参数的命令后，按提示直接回复参数：
/login — 回复第一组 TOTP 验证码
/run — 回复普通 Linux 命令
/sudo — 回复需要 root 权限的命令
/approve — 回复第二组 TOTP 验证码（仅执行待授权命令一次）

无需参数的命令：
/whoami — 检查管理会话
/cancel — 取消待授权命令
/logout — 退出管理会话
/help 或 /menu — 查看帮助

原来的完整命令仍可直接发送：
/login 123456
/run whoami
/run uptime
/run free -h
/run systemctl status docker --no-pager
/sudo docker ps -a
/approve 123456

🔐 第一组 TOTP 登录最长 15 分钟，空闲 5 分钟失效。
普通命令由 tgexec 执行；高权限命令需第二组 TOTP，每次仅授权一条，60 秒后过期。
/docker 专用菜单尚未实现，可以使用 /sudo docker ...。
验证码消息会尽量删除，但 Telegram Bot 私聊不是端到端加密。
点击 Menu 发送命令后，客户端通常会进入“回复”状态；请回复 Bot 的提示消息。"""


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


async def delete_if_possible(message) -> None:
    try:
        await message.delete()
    except Exception:
        pass  # Best effort only; never log a submitted OTP.


async def main_bot() -> None:
    token, user_id, chat_id, secret = load_config()
    auth = AuthState(secret)
    pending_request: str | None = None
    pending_input: PendingInput | None = None

    async def clear_prompt(update: Update) -> None:
        """Remove the ForceReply anchor after completion or command switching.

        Telegram controls the actual client-side reply bar; deleting its
        original message avoids leaving a stale ForceReply anchor around.
        """
        nonlocal pending_input
        previous, pending_input = pending_input, None
        if previous is not None:
            try:
                await update.effective_chat.delete_message(previous.prompt_id)
            except Exception:
                log.debug("Unable to remove previous ForceReply prompt.")

    def allowed(update: Update) -> bool:
        return (
            update.effective_user is not None
            and update.effective_chat is not None
            and update.effective_chat.type == "private"
            and update.effective_user.id == user_id
            and update.effective_chat.id == chat_id
        )

    async def prompt_for(update: Update, kind: str, description: str, ttl: int = 120) -> None:
        nonlocal pending_input
        await clear_prompt(update)
        sent = await update.effective_chat.send_message(
            description,
            reply_markup=ForceReply(
                selective=True,
                input_field_placeholder={
                    "login": "输入第一组 6 位验证码",
                    "approve": "输入第二组 6 位验证码",
                    "run": "输入普通 Linux 命令",
                    "sudo": "输入需 root 执行的命令",
                }[kind],
            ),
        )
        pending_input = PendingInput.make(kind, sent.message_id, ttl=ttl)

    async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        nonlocal pending_input
        if not allowed(update):
            return
        await clear_prompt(update)
        await update.message.reply_text(HELP_TEXT)

    async def do_login(update: Update, code: str) -> None:
        await delete_if_possible(update.message)
        await update.effective_chat.send_message(auth.login(code))

    async def login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        nonlocal pending_input
        if not allowed(update):
            return
        await clear_prompt(update)
        if not context.args:
            await prompt_for(update, "login", "🔐 请直接回复这条消息，输入第一组 TOTP 的 6 位验证码。\n提示：验证码消息会尝试删除，但 Bot 私聊并非端到端加密。")
            return
        if len(context.args) != 1:
            await update.message.reply_text("用法：/login <6位验证码>")
            return
        await do_login(update, context.args[0])

    async def logout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        nonlocal pending_request, pending_input
        if not allowed(update):
            return
        await clear_prompt(update)
        auth.logout()
        if pending_request:
            rid, pending_request = pending_request, None
            try:
                await asyncio.to_thread(cancel_request, rid, user_id, chat_id)
            except AgentError:
                log.warning("Could not cancel pending privilege request during logout.")
        await update.message.reply_text("管理会话已退出。")

    async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        nonlocal pending_input
        if not allowed(update):
            return
        await clear_prompt(update)
        await update.message.reply_text("✅ 管理会话有效" if auth.authorized() else "🔒 未登录或会话已过期")

    async def do_run(update: Update, command: str) -> None:
        try:
            command = validate_command(command)
        except ValueError as exc:
            await update.effective_chat.send_message(str(exc))
            return
        result = await asyncio.to_thread(execute, command)
        out = f"exit={result.code}\nSTDOUT:\n{result.stdout or '(empty)'}\nSTDERR:\n{result.stderr or '(empty)'}"
        if result.truncated:
            out += "\n[输出已达到 24KiB 上限，进程已终止]"
        for index in range(0, len(out), 3500):
            await update.effective_chat.send_message(out[index:index + 3500])

    async def run_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        nonlocal pending_input
        if not allowed(update):
            return
        await clear_prompt(update)
        if not auth.authorized(touch=True):
            await update.message.reply_text("请先通过 /login 完成 TOTP 认证。")
            return
        command = (update.message.text or "").partition(" ")[2]
        if not command.strip():
            await prompt_for(update, "run", "💻 请直接回复这条消息，输入要由 tgexec 执行的 Linux 命令。")
            return
        await do_run(update, command)

    async def do_sudo(update: Update, command: str) -> None:
        nonlocal pending_request
        try:
            command = validate_command(command)
            outcome = await asyncio.to_thread(create_request, command, user_id, chat_id)
        except (ValueError, AgentError) as exc:
            await update.effective_chat.send_message(f"无法创建授权请求：{exc}")
            return
        if not outcome.get("ok"):
            await update.effective_chat.send_message("无法创建授权请求：" + outcome.get("error", "未知错误"))
            return
        pending_request = outcome["request_id"]
        await update.effective_chat.send_message(
            "🔐 高权限请求（尚未执行）\n\n"
            f"{outcome['command']}\n\n"
            "请检查以上完整命令，60 秒内点 /approve 并回复第二组验证码，"
            "或直接发送 /approve <6位验证码>。\n"
            "任何新 /sudo 命令将撤销前一个请求。使用 /cancel 放弃。"
        )

    async def sudo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        nonlocal pending_input
        if not allowed(update):
            return
        await clear_prompt(update)
        if not auth.authorized(touch=True):
            await update.message.reply_text("请先完成 /login。")
            return
        command = (update.message.text or "").partition(" ")[2]
        if not command.strip():
            await prompt_for(update, "sudo", "🛡️ 请直接回复这条消息，输入需要 root 执行的完整命令。提交后仍需单独授权，不会立即执行。")
            return
        await do_sudo(update, command)

    async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        nonlocal pending_request, pending_input
        if not allowed(update):
            return
        await clear_prompt(update)
        if not pending_request:
            await update.message.reply_text("当前没有待授权命令。")
            return
        rid, pending_request = pending_request, None
        try:
            await asyncio.to_thread(cancel_request, rid, user_id, chat_id)
        except AgentError:
            log.warning("Cancel request could not reach privileged agent.")
        await update.message.reply_text("已取消当前待授权请求。")

    async def do_approve(update: Update, code: str) -> None:
        nonlocal pending_request
        await delete_if_possible(update.message)
        if not auth.authorized(touch=True):
            await update.effective_chat.send_message("管理会话已过期，请重新 /login。")
            return
        if not pending_request:
            await update.effective_chat.send_message("当前没有待授权命令，请先 /sudo <命令>。")
            return
        if not (len(code) == 6 and code.isascii() and code.isdigit()):
            await update.effective_chat.send_message("请输入第二组身份验证器的 6 位数字码。可再次点击 /approve。")
            return
        rid, pending_request = pending_request, None
        await update.effective_chat.send_message("⏳ 正在验证并执行本次高权限请求…")
        try:
            outcome = await asyncio.to_thread(approve_request, rid, code, user_id, chat_id)
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

    async def approve_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        nonlocal pending_input
        if not allowed(update):
            return
        await clear_prompt(update)
        if not auth.authorized(touch=True):
            await delete_if_possible(update.message)
            await update.effective_chat.send_message("管理会话已过期，请重新 /login。")
            return
        if not pending_request:
            await delete_if_possible(update.message)
            await update.effective_chat.send_message("当前没有待授权命令，请先 /sudo <命令>。")
            return
        if not context.args:
            await prompt_for(update, "approve", "🔐 请直接回复这条消息，输入第二组 TOTP 的 6 位动态验证码。\n本次授权请求只有 60 秒有效期。", ttl=60)
            return
        if len(context.args) != 1:
            await delete_if_possible(update.message)
            await update.effective_chat.send_message("用法：/approve <第二组6位验证码>")
            return
        await do_approve(update, context.args[0])

    async def reply_params(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        nonlocal pending_input
        if not allowed(update) or not pending_input:
            return
        message = update.message
        reply = message.reply_to_message
        if not reply or not pending_input.matches(reply.message_id):
            # Intentionally ignore messages that aren't replies to the active prompt.
            # This prevents accidental execution of unrelated private-chat text.
            if reply and reply.message_id == pending_input.prompt_id:
                await clear_prompt(update)
                await update.effective_chat.send_message("输入已超时，请重新从 Menu 选择命令。")
            return
        kind = pending_input.kind
        await clear_prompt(update)
        value = message.text or ""
        if kind in ("login", "approve"):
            if kind == "login":
                await do_login(update, value.strip())
            else:
                await do_approve(update, value.strip())
        elif kind == "run":
            if not auth.authorized(touch=True):
                await update.effective_chat.send_message("会话已过期，请重新 /login。")
                return
            await do_run(update, value)
        elif kind == "sudo":
            if not auth.authorized(touch=True):
                await update.effective_chat.send_message("会话已过期，请重新 /login。")
                return
            await do_sudo(update, value)

    app = build_application(token, read_proxy())
    for name, handler in [
        ("start", help_cmd), ("help", help_cmd), ("menu", help_cmd), ("login", login),
        ("logout", logout), ("whoami", whoami), ("run", run_cmd),
        ("sudo", sudo_cmd), ("approve", approve_cmd), ("cancel", cancel_cmd),
    ]:
        app.add_handler(CommandHandler(name, handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reply_params))
    log.info("tg-admin starting with native menu and reply-based command input")
    await app.initialize()
    await app.start()
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
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    try:
        asyncio.run(main_bot())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
