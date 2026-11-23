# Phase 1：在 Debian 13 上试运行

> **开发阶段原型**：本阶段只有普通权限命令、Telegram 私聊白名单和第一组 TOTP；没有第二组认证、root 提权或 Docker Socket 管理。建议先在测试主机/测试账号上验证。首次启用前请审查代码。

## 要求

- Debian 13，Python 3.11+，`sudo`，现有 SSH 管理访问（用于安装及故障恢复）。
- Telegram Bot Token、本人 Telegram 数字 User ID、Bot 私聊 Chat ID。
- 手机上的 TOTP 验证器，例如 Google Authenticator。
- **不能**把 Bot Token、TOTP 密钥、个人 ID 提交到 Git 仓库。

## 1. 通过 SSH 安装依赖与专用账户

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip sudo
sudo useradd --system --user-group --home-dir /nonexistent --shell /usr/sbin/nologin tgbot
sudo useradd --system --user-group --home-dir /nonexistent --shell /usr/sbin/nologin tgexec
sudo git clone https://github.com/Jankinsu/tg-admin.git /opt/tg-admin
sudo chown -R root:root /opt/tg-admin
sudo chmod -R go-w /opt/tg-admin

sudo python3 -m venv /opt/tg-admin/.venv
sudo /opt/tg-admin/.venv/bin/pip install -e /opt/tg-admin
```

如果账户已存在，跳过对应的 `useradd`；如果 `/opt/tg-admin` 已存在，先核对其内容，不要直接覆盖。部署过程切勿修改服务器现有 Docker 配置。

## 2. 创建第一组 TOTP 密钥

只在自己的 SSH 终端运行：

```bash
/opt/tg-admin/.venv/bin/python -c 'import pyotp; print(pyotp.random_base32())'
```

将生成的密钥加入手机身份验证器；可以使用身份验证器的**手动输入密钥**功能，类型选“基于时间”。

Bot 配置写入 `/etc/tg-admin/bot.env`：

```bash
sudo install -d -m 0750 -o root -g tgbot /etc/tg-admin
sudo install -m 0640 -o root -g tgbot /dev/null /etc/tg-admin/bot.env
sudoedit /etc/tg-admin/bot.env
```

内容格式（**替换所有示例值**）：

```dotenv
TG_ADMIN_BOT_TOKEN=YOUR_BOT_TOKEN
TG_ADMIN_USER_ID=YOUR_NUMERIC_USER_ID
TG_ADMIN_CHAT_ID=YOUR_PRIVATE_CHAT_ID
TG_ADMIN_TOTP_SECRET=YOUR_BASE32_TOTP_SECRET
```

这些配置通过 systemd 的 EnvironmentFile 注入 Bot 进程。在第一版中它们只供 `tgbot` 读取，不提供给 `tgexec`。请勿通过聊天或截图公开。

## 3. 安装最小 sudoers 规则

```bash
sudo install -m 0440 -o root -g root \
  /opt/tg-admin/deploy/tg-admin.sudoers /etc/sudoers.d/tg-admin
sudo visudo -cf /etc/sudoers.d/tg-admin
```

**务必确认该规则只允许以 `tgexec` 身份执行固定 runner，而不是以 root 身份执行任意 shell。**

验证：

```bash
sudo -u tgbot sudo -n -l
```

应只出现对应 `(tgexec)` 固定 runner 授权。若该账户从其他规则继承了 root 权限，先修正再继续。

## 4. 安装服务并启动

```bash
sudo install -m 0644 -o root -g root \
  /opt/tg-admin/deploy/tg-admin.service /etc/systemd/system/tg-admin.service
sudo systemctl daemon-reload
sudo systemctl enable --now tg-admin.service
sudo systemctl status tg-admin.service --no-pager
```

服务器不需要开放新的入站端口；Bot 使用 Telegram API 长轮询。Debian 必须能访问 Telegram API。

## 5. 手机试用

在与 Bot 的**私聊**中：

```text
/help
/login 123456
/whoami
/run whoami
/run uptime
/run free -h
/logout
```

`123456` 只是占位例子，实际应使用手机身份验证器显示的动态码。登录消息会尝试删除，但 Telegram 私聊**不是端到端加密**，且无法保证消息已经完全从所有相关系统移除。

预期：`/run whoami` 返回 `tgexec`，而非 `root` 或 `tgbot`。`/run sudo ...` 不应获得额外权限；现阶段不存在 `/sudo` 命令。

如需观察服务日志：

```bash
sudo journalctl -u tg-admin -n 100 --no-pager
```

## 6. 常见故障

| 症状 | 检查 |
| --- | --- |
| Bot 无法响应 | Telegram API 网络连通性、Token、systemd 日志 |
| `/login` 总失败 | 系统时间同步、TOTP 密钥是否录入正确、是否重复使用同一周期验证码 |
| `/run` 返回执行入口失败 | `sudo -u tgbot sudo -n -l`、脚本路径、文件权限 |
| `/run whoami` 不是 tgexec | **立即停用 Bot**，检查 sudoers 和运行账户 |
| Docker 不可管理 | Phase 1 刻意未启用，等待 Phase 2/3 |

## 7. 测试

在已安装本项目依赖的开发环境中：

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest -q
```

测试覆盖 TOTP 使用与重放、会话空闲超时、命令检查、执行入口固定路径、非特权 runner 的输出截断。**CI 和真实 Debian 集成测试仍待执行**。

## 8. 停用与卸载（手动）

```bash
sudo systemctl disable --now tg-admin.service
sudo rm -f /etc/systemd/system/tg-admin.service
sudo rm -f /etc/sudoers.d/tg-admin
sudo systemctl daemon-reload
```

确认不再需要该服务后，可进一步清理 `/etc/tg-admin`、`/opt/tg-admin` 及专用账户。**删除前自行备份需要保留的配置**。以上操作不会主动删除任何 Docker 容器、镜像、数据卷或 Docker Compose 项目。

完整的一键安装与卸载脚本将在 Phase 5 提供。
