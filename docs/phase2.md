# Phase 2：独立二次认证与单次高权限执行

> 状态：**代码与单元测试已提交；尚未在 Debian 13 实机验证。**  
> 不会修改现有 Docker，也不需要搬迁现有容器。

## Telegram 菜单的参数回复模式

Telegram 原生命令菜单点击后会立即发送命令。tg-admin 因而采用 **ForceReply**：

| 点击 Menu | Bot 提示 | 用户操作 |
| --- | --- | --- |
| /login | 输入第一组 TOTP | 直接回复 6 位码 |
| /run | 输入普通 Shell 命令 | 回复命令，按 tgexec 权限执行 |
| /sudo | 输入高权限命令 | 回复命令，仅登记待执行请求 |
| /approve | 输入第二组 TOTP | 回复 6 位码，完成该请求的单次授权 |
| /whoami、/cancel、/logout | 无需参数 | 点击即执行 |

- 也继续支持原有的 **完整命令格式**（例如 \`/run uptime\` 和 \`/approve 123456\`），方便复制 AI 提供的命令。
- 仅接受对当前有效提示消息的回复；其他聊天文本不会自动当作 Shell 命令运行。
- 回复输入状态保存在内存中，默认 120 秒后失效；\`/approve\` 输入提示最多 60 秒，而 root 侧高权限请求仍独立执行 **60 秒有效期** 检查。
- 切换至其他命令会结束旧的输入提示；重启 Bot 也会清空未完成的输入状态。
- 验证码消息尽量从 Telegram 聊天删除，但不构成端到端加密或彻底删除的保证。
- Telegram 客户端对 ForceReply 的表现可能有所区别；若未自动进入回复状态，可手动选择“回复”Bot 的提示消息。
- 升级后不需要重新生成 TOTP 或修改原有的 \`bot.env\`。

## 使用方式

```text
/login 111111                 # 第一组 TOTP：开启普通会话
/sudo docker ps -a            # 发起 root 命令，尚未执行
/approve 222222               # 第二组 TOTP：只授权上一条命令一次
/sudo systemctl restart nginx # 另一条命令需要重新验证
/cancel                       # 取消当前待授权命令
/logout                       # 登出，并取消待授权请求
```

111111、222222 为示例，**两组验证器必须使用不同密钥**。每次 `/sudo` 都在 root 拥有的 SQLite 中登记完整命令及其请求 ID，限时 60 秒。重复请求、过期请求、验证码重放、无效身份一律拒绝。无持久 root Shell 或 sudo 凭据缓存。高权限执行器会完整执行这**一次**已授权的 root Shell 命令，不实现命令白名单。

**重要：**Bot 私聊非端到端加密；第二组验证码虽然会尝试删除消息，仍可能暴露给 Telegram、被攻陷的 Bot 或相关日志系统。不要将此方案等同于抗 Bot 入侵的独立 Passkey/WebAuthn 认证。

## 实现分工

| 组件 | 身份 | 职责 |
| --- | --- | --- |
| `tg-admin` Bot | `tgbot` | Telegram 白名单、L1 TOTP、命令预览、回传 |
| `runner.py` | `tgexec` | 原 Phase 1 的非特权 Shell |
| `privileged.py` | root（按需运行） | 保存待执行请求、核对第二组 TOTP、原子消费、执行一次 |
| `/etc/tg-admin/l2.secret` | 仅 root 可读 | 独立的高权限 TOTP 密钥 |
| `/etc/tg-admin/l2-identity.json` | 仅 root 可读 | 固定 Telegram User ID / 私聊 Chat ID |
| `/var/lib/tg-admin/authorization.sqlite3` | 仅 root 可读写 | 保存请求状态、验证码计数器及锁定状态 |

sudoers 独立存放在 `/etc/sudoers.d/tg-admin-l2`，只允许 `tgbot` 执行固定的 `/usr/bin/python3 -I /opt/tg-admin/privileged.py`；不允许普通 `sudo`、`docker` 或任意程序路径。

## 先升级程序（不会自动开启 L2）

通过已配置 Throne 的 Debian 服务器 SSH 执行：

```bash
sudo git -c http.proxy=socks5h://127.0.0.1:2080 \
  -C /opt/tg-admin pull --ff-only
cd /opt/tg-admin
sudo bash scripts/install.sh
```

普通 `install.sh` **不会自动添加 L2 sudoers**；这一步仅更新程序与依赖。已有 `bot.env` 和代理设置保留。

## 单独开启 L2（需要 SSH）

在已审查代码、确保 SSH 可恢复之后，执行：

```bash
cd /opt/tg-admin
sudo bash scripts/enable-l2.sh
```

启用脚本会：

1. 检查 `/opt/tg-admin` 等代码目录及特权执行文件由 root 拥有，不可被普通用户修改。
2. 从现有 `/etc/tg-admin/bot.env` 提取数字 User ID / Chat ID，保存为 root 独占的 L2 身份配置。
3. **第一次启用**生成新的、独立的 160-bit Base32 TOTP 密钥，**仅在执行 SSH 终端显示一次**。用 Google Authenticator 或其他 TOTP 验证器手动新增一项（如 `tg-admin-L2`），妥善保管恢复密钥。
4. 配置新的精确 sudoers 授权，先通过 `visudo` 语法检查。
5. 不自动重启 Bot，不修改现有 Docker、systemd 服务或网络规则。

启用成功后核对：

```bash
sudo visudo -cf /etc/sudoers.d/tg-admin-l2
sudo -u tgbot sudo -n -l
sudo systemctl restart tg-admin.service
sudo systemctl status tg-admin.service --no-pager -l
```

应能看到普通 `tgexec` runner 和 root `privileged.py` 两条**固定**授权，且没有更广泛的 root 命令权限。

请勿把 **L2 密钥、二维码或真实验证码**发到 ChatGPT、Telegram 聊天日志或公开 GitHub 仓库。

## 按风险渐进验收

首次建议只测试无副作用的命令：

```text
/login <第一组动态码>
/sudo id
/approve <第二组动态码>
```

预期返回 `uid=0(root)`，说明仅这一条命令获得 root 权限。

之后测试：

```text
/sudo docker ps -a
/approve <新的第二组动态码>
```

可以读取现有容器列表。**同一个 30 秒周期内已经使用过的第二组验证码不能重复使用**；下一次授权需要等待新的验证码。

先验证重复授权、错误码、过期及 `/cancel` 都被拒绝，再测试对已有容器的无破坏性管理。不要一开始就执行 `docker rm`、删除数据卷、修改 SSH 或防火墙。

## 禁用及回退

若怀疑 L2 有问题，优先通过 SSH：

```bash
sudo bash /opt/tg-admin/scripts/disable-l2.sh
sudo systemctl restart tg-admin.service
```

禁用脚本**只撤销 L2 sudoers**，保留 L1 Bot、普通命令通道、L2 密钥和授权审计状态。以后重新启用不会重置密钥及验证码重放状态。

完全卸载 tg-admin 时，`scripts/uninstall.sh` 会撤销 L1/L2 两条 sudoers 规则；默认保留凭据。只有 `--purge` 才同时删除本项目代码、配置和 L2 授权状态。

## 安全边界与已知限制

- 特权入口只能在收到第二组 TOTP 后执行已登记命令；但 L2 验证码目前经 Telegram 私聊传输，Bot 遭攻陷仍可能在验证码到达时滥用授权。
- 一次 root 命令足以对系统做永久改变，**单次授权约束的是验证与调用次数，不是命令本身的副作用**。
- 高权限命令使用非交互式 `/bin/sh -c`，不会提供 TTY 或独立终端会话。
- 输出总量上限 24 KiB，超过后终止执行进程组；执行时限 120 秒，超长镜像拉取可能中断。后续按实际需求优化后台任务管理。
- 不自动识别 `sudo` 关键字：普通 `/run sudo ...` 不允许提权，必须明确使用 `/sudo`。
- 目前没有经过真实 Debian 13 授权安全审查、异常关机恢复或故障注入测试；完成验收后才能标记 Phase 2 为稳定。
