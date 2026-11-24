# Phase 1：在 Debian 13 上试运行

> **历史阶段文档（Phase 1 已通过 Debian 13 实机验证）**：本文聚焦 L1 登录和普通 Shell 的首次安装；完整程序现已支持 Phase 2 的独立 TOTP 单次提权。高权限功能须按 [Phase 2 指南](phase2.md) 明确启用。

## 要求

- Debian 13，Python 3.11+，`sudo`，现有 SSH 管理访问（用于安装及故障恢复）。
- Telegram Bot Token、本人 Telegram 数字 User ID、Bot 私聊 Chat ID。
- 手机上的 TOTP 验证器，例如 Google Authenticator。
- **不能**把 Bot Token、TOTP 密钥、个人 ID 提交到 Git 仓库。

## 1. 通过 SSH 安装（与 tg-auto / tdl-bot 统一）

`tg-admin` 因为需要接收远程命令，使用 root 拥有的 `/opt/tg-admin` 而不是用户可写的 `~/apps`。统一的安装和卸载脚本见 [部署约定](deployment-conventions.md)。

首次安装要求系统中已存在 `tgbot`、`tgexec` 两个非 root 账户，且都不属于 `sudo` 或 `docker` 组。如尚未创建，请先创建并检查账户配置；安装器不会代建。

如果是**全新服务器**，先检查账户是否存在：

```bash
id tgbot
id tgexec
```

缺失时才分别创建（不要对已有账户重复执行）：

```bash
sudo useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin tgbot
sudo useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin tgexec
```

确认两者均不在 `sudo`、`docker` 组，且这两个账户都不是 root。已部署实例不要更改现有账户。

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip sudo
sudo git clone https://github.com/Jankinsu/tg-admin.git /opt/tg-admin
sudo chown -R root:root /opt/tg-admin
sudo chmod -R go-w /opt/tg-admin

cd /opt/tg-admin
sudo bash scripts/install.sh
```

安装会检测所需账户，创建保密配置模板、安装依赖和 systemd/sudoers 文件，**不会自动启动 Bot**。如果目录已存在，请先检查而不是重复 clone。

## 2. 创建第一组 TOTP 密钥

只在自己的 SSH 终端运行：

```bash
/opt/tg-admin/.venv/bin/python -c 'import pyotp; print(pyotp.random_base32())'
```

将生成的密钥加入手机身份验证器；可以使用身份验证器的**手动输入密钥**功能，类型选“基于时间”。

Bot 配置已由安装脚本自动创建在 `/etc/tg-admin/bot.env`，使用 SSH 编辑：

```bash
sudoedit /etc/tg-admin/bot.env
```

将其中四个示例字段替换为真实 Bot Token、Telegram 数字 User ID、私聊 Chat ID、L1 TOTP 密钥。文件权限由安装脚本设为 `root:tgbot 0640`。

### Telegram API 代理（与 tg-auto 一致）

`tg-auto` 用 `TG_AUTO_PROXY=socks5://127.0.0.1:2080` 连接本地 Throne。tg-admin 采用同样格式：

```dotenv
TG_ADMIN_PROXY=socks5://127.0.0.1:2080
```

将其追加到 `/etc/tg-admin/bot.env`。仅影响 Bot 对 Telegram API 的连接，**不会自动为 `/run` 命令配置代理**。如需直连，设为 `TG_ADMIN_PROXY=`。

在部署前确认本地 Throne 监听器确实在端口 `2080`，且进程可访问该监听地址。不要直接假设端口或代理已启动。

更新现有安装：

```bash
sudo git -c http.proxy=socks5h://127.0.0.1:2080 -C /opt/tg-admin pull --ff-only
cd /opt/tg-admin
sudo bash scripts/install.sh
sudoedit /etc/tg-admin/bot.env
sudo systemctl restart tg-admin.service
sudo systemctl status tg-admin.service --no-pager -l
```

**注意：**已经存在的 `bot.env` 不会被安装脚本覆盖。仅在你尚未配置代理时才需要补充 `TG_ADMIN_PROXY`；Git 上的 `socks5h` 代理配置和 Bot 运行时的 `TG_ADMIN_PROXY` 是两个独立设置。对于 HTTP 代理，可写 `TG_ADMIN_PROXY=http://127.0.0.1:2080`。

## 3. 验证并启动

安装脚本已经检查 sudoers 语法，且只开放 `tgbot -> tgexec` 的固定执行入口。建议额外核查：

```bash
sudo -u tgbot sudo -n -l
sudo visudo -cf /etc/sudoers.d/tg-admin
sudo systemctl enable --now tg-admin.service
sudo systemctl status tg-admin.service --no-pager
```

如果账户继承了额外的 root 或 Docker 权限，不要继续启动，先修正权限。

## 4. 后续更新与卸载

与 `tg-auto`、`tdl-bot` 保持一致的行为：**安装默认不重启，卸载默认保留配置和代码，`--purge` 才清理**。具体命令与保护范围见 [deployment-conventions.md](deployment-conventions.md)。

## Telegram 原生命令菜单（现行版本）

程序已提供 `/help`、`/login`、`/whoami`、`/run`、`/sudo`、`/approve`、`/cancel` 和 `/logout`。点击 Menu 中的带参数命令时，Bot 会发送 ForceReply 输入提示；用户直接回复参数即可，处理完成后 Bot 会尽量删除原提示消息，减少回复栏残留。

原来的 `/login 123456`、`/run uptime` 等完整命令格式继续有效。**普通安装不会开启 L2**；高权限功能应参照 [Phase 2 指南](phase2.md) 独立启用。

### 更新已安装的实例

```bash
sudo git -c http.proxy=socks5h://127.0.0.1:2080 \
  -C /opt/tg-admin pull --ff-only
cd /opt/tg-admin
sudo bash scripts/install.sh --restart
```

本例 Git 使用本机 SOCKS5 代理；未配置代理时删去 `-c http.proxy=...`。安装器保留既有配置。若服务尚未启动，可使用 `sudo systemctl enable --now tg-admin.service`。

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

预期：`/run whoami` 返回 `tgexec`，而非 `root` 或 `tgbot`。`/run sudo ...` 不应获得额外权限；高权限命令必须使用 `/sudo`，并在独立配置 L2 后以第二组 TOTP 授权。

如需观察服务日志：

```bash
sudo journalctl -u tg-admin -n 100 --no-pager
```

## 启动报错 203/EXEC：Python 权限不足

如果 `systemctl status tg-admin` 显示 `Failed to execute /opt/tg-admin/.venv/bin/python: Permission denied`，通常是安装器的 `umask 077` 使虚拟环境目录只允许 root 访问。先通过 SSH 检查：

```bash
namei -l /opt/tg-admin/.venv/bin/python
sudo -u tgbot /opt/tg-admin/.venv/bin/python -V
```

**立即修复已安装的实例**（不改变目录所有者、不授予写权限）：

```bash
sudo chmod -R a+rX /opt/tg-admin/.venv
sudo -u tgbot /opt/tg-admin/.venv/bin/python -V
sudo systemctl restart tg-admin.service
sudo systemctl status tg-admin.service --no-pager -l
```

安装脚本现已在依赖安装后自动设置虚拟环境只读/可遍历权限，并在安装时验证 `tgbot` 可以启动解释器。修复后如果出现新的错误，再查看 `sudo journalctl -u tg-admin -n 80 --no-pager`；不要公开其中的任何凭据。

## 6. 常见故障

| 症状 | 检查 |
| --- | --- |
| Bot 无法响应 | Telegram API 网络连通性、Token、systemd 日志 |
| `/login` 总失败 | 系统时间同步、TOTP 密钥是否录入正确、是否重复使用同一周期验证码 |
| `/run` 返回执行入口失败 | `sudo -u tgbot sudo -n -l`、脚本路径、文件权限 |
| `/run whoami` 不是 tgexec | **立即停用 Bot**，检查 sudoers 和运行账户 |
| Docker 不可管理 | 检查是否已按 Phase 2 指南启用 L2，并使用 `/sudo docker ps -a`；`/docker` 专用菜单仍未开发 |

## 7. 测试

在已安装本项目依赖的开发环境中：

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest -q
```

测试覆盖 TOTP 使用与重放、会话空闲超时、命令检查、执行入口固定路径、非特权 runner 的输出截断。**CI 已执行，Phase 1 主要功能也已在 Debian 13 实机验证**；完整异常及安全审查仍待继续。

## 8. 停用与卸载

```bash
sudo bash /opt/tg-admin/scripts/uninstall.sh
```

默认仅移除服务与 sudoers，保留凭据和程序目录。明确要求清除本项目数据时，使用 `--purge`（二次确认），**不会删除两个用户或任何 Docker 数据**。详见 [部署约定](deployment-conventions.md)。

