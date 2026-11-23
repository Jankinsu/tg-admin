# Phase 1：在 Debian 13 上试运行

> **开发阶段原型**：本阶段只有普通权限命令、Telegram 私聊白名单和第一组 TOTP；没有第二组认证、root 提权或 Docker Socket 管理。建议先在测试主机/测试账号上验证。首次启用前请审查代码。

## 要求

- Debian 13，Python 3.11+，`sudo`，现有 SSH 管理访问（用于安装及故障恢复）。
- Telegram Bot Token、本人 Telegram 数字 User ID、Bot 私聊 Chat ID。
- 手机上的 TOTP 验证器，例如 Google Authenticator。
- **不能**把 Bot Token、TOTP 密钥、个人 ID 提交到 Git 仓库。

## 1. 通过 SSH 安装（与 tg-auto / tdl-bot 统一）

`tg-admin` 因为需要接收远程命令，使用 root 拥有的 `/opt/tg-admin` 而不是用户可写的 `~/apps`。统一的安装和卸载脚本见 [部署约定](deployment-conventions.md)。

**用户当前已创建 `tgbot`、`tgexec`，不用再创建。**

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
| Docker 不可管理 | Phase 1 刻意未启用，等待 Phase 2/3 |

## 7. 测试

在已安装本项目依赖的开发环境中：

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest -q
```

测试覆盖 TOTP 使用与重放、会话空闲超时、命令检查、执行入口固定路径、非特权 runner 的输出截断。**CI 和真实 Debian 集成测试仍待执行**。

## 8. 停用与卸载

```bash
sudo bash /opt/tg-admin/scripts/uninstall.sh
```

默认仅移除服务与 sudoers，保留凭据和程序目录。明确要求清除本项目数据时，使用 `--purge`（二次确认），**不会删除两个用户或任何 Docker 数据**。详见 [部署约定](deployment-conventions.md)。

