# tg-admin

> 通过 Telegram 私聊远程管理自己的 Debian 服务器：查看系统状态、执行 Shell 命令、使用独立 TOTP 单次提权，并操作现有 Docker Engine。
>
> **Full control when needed. Minimal privilege by default.**

**项目状态（2026-11）：Phase 1 已通过 Debian 13 实机验证；Phase 2 的基本提权、Docker 查询与重放/超时/取消流程已通过实机测试；Phase 3 正在开发。当前仍是个人使用的开发版本，尚非经过完整安全审计的稳定发行版。**

## 已经可以做什么？

- **普通命令**：在独立的 `tgexec` 账户下执行 `uptime`、`free -h`、`df -h`、`systemctl status` 等非交互式 Shell 命令。
- **单次提权**：`/sudo <完整命令>` 先展示命令，之后使用独立于登录验证的第二组 TOTP 执行**一次** root 命令。每条新的高权限命令都要重新授权。
- **管理已有 Docker 环境**：已实测 `/sudo docker ps -a` 可读取部署 Bot 之前就存在的容器，无需迁移或建立 Rootless Docker。容器启停、日志、部署等通用 Docker CLI 命令可经 `/sudo` 提交，但尚未逐一完成实机验收，也没有专用的 `/docker` 图形化管理菜单。
- **手机友好的菜单**：Telegram 原生命令菜单、ForceReply 参数提示、回复完成后自动清理旧提示；同时兼容直接输入完整命令。
- **状态与结果回传**：输出退出码、stdout 和 stderr；支持超时、输出截断及基本失败反馈。
- **可选本地代理**：`TG_ADMIN_PROXY` 可通过 Throne 等 HTTP/SOCKS5 代理访问 Telegram API。

**用途**：当不方便使用 SSH 时，手机可与 Bot 交互管理服务器；尤其适用于“AI 提供诊断建议及命令 → 用户审查后通过 Telegram 执行 → 回传结果继续分析”的流程。**Bot 不是 SSH 故障恢复通道，也不自动执行 AI 指令。**

## 命令速览

| 菜单命令 | 当前行为 |
| --- | --- |
| `/help` | 查看命令和示例；`/start`、`/menu` 也能显示帮助 |
| `/login` | 第一组 TOTP，登录普通管理会话 |
| `/whoami` | 查询普通管理会话状态 |
| `/run` | 用 `tgexec` 运行普通 Shell 命令 |
| `/sudo` | 提交一条完整的 root 命令，**仅登记等待授权，不会直接执行** |
| `/approve` | 输入第二组 TOTP，授权执行当前待处理请求一次 |
| `/cancel` | 取消待授权请求 |
| `/logout` | 登出并取消待授权请求 |

**使用方法 A：点击 Menu。** 对 `/login`、`/run`、`/sudo`、`/approve`，Bot 会发出参数提示。直接回复提示消息即可；执行完成后，Bot 会尽量删除旧提示，避免聊天输入框一直停留在回复状态。其他无参数命令点击后直接处理。

**使用方法 B：直接发送完整命令**（两种输入方式始终兼容）：

```text
/login 123456
/run uptime
/run free -h
/sudo id
/approve 654321
/sudo docker ps -a
/approve <第二组新验证码>
/logout
```

示例验证码是假数据。第一组和第二组必须来自**不同 TOTP 密钥**；第二组验证码在同一个 30 秒周期内不能重复使用。

高权限命令经过如下流程：

```text
私聊白名单 → /login 第一组 TOTP → L1 会话
                                       │
/run <命令> ──────────────→ tgexec 普通命令执行
                                       │
/sudo <完整命令> → root 保存请求并回显命令（尚未执行）
                  → /approve 第二组 TOTP
                  → root 核对请求/验证码/有效期并一次性消费
                  → root 执行这条命令 → 返回执行结果
```

## 部署到 Debian 13

要求：Python 3.11+、`git`、`sudo`、`python3-venv`、systemd、Telegram Bot Token、个人 User ID / 私聊 Chat ID、TOTP 验证器，以及独立可用的 SSH 恢复入口。

程序安装在由 root 拥有的 `/opt/tg-admin`；`tgbot` 运行 Bot，`tgexec` 执行普通命令。**两个账户均不得加入 `sudo` 或 `docker` 组。**安装器要求账户已经存在；首次安装和账户准备请参见 [Phase 1 安装指南](docs/phase1.md)。

### 首次安装

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip sudo

# 请先按 Phase 1 指南创建 tgbot / tgexec 账户（如果尚不存在）
sudo git clone https://github.com/Jankinsu/tg-admin.git /opt/tg-admin
sudo chown -R root:root /opt/tg-admin
sudo chmod -R go-w /opt/tg-admin

cd /opt/tg-admin
sudo bash scripts/install.sh
sudoedit /etc/tg-admin/bot.env
sudo systemctl enable --now tg-admin.service
sudo systemctl status tg-admin.service --no-pager -l
```

`bot.env` 中应配置 `TG_ADMIN_BOT_TOKEN`、`TG_ADMIN_USER_ID`、`TG_ADMIN_CHAT_ID`、`TG_ADMIN_TOTP_SECRET`，必要时设置 `TG_ADMIN_PROXY=socks5://127.0.0.1:2080`（端口仅为本地 Throne 示例）。

**普通安装不会自动启用 root 提权。** 经过检查后，按照 [Phase 2 指南](docs/phase2.md) 单独执行 `sudo bash scripts/enable-l2.sh`，在 SSH 终端录入**另一组** TOTP 密钥至身份验证器，并重启服务。绝不把任何真实密钥提交到仓库或发到聊天中。

### 更新现有实例

```bash
sudo git -c http.proxy=socks5h://127.0.0.1:2080 \
  -C /opt/tg-admin pull --ff-only

cd /opt/tg-admin
sudo bash scripts/install.sh --restart
sudo systemctl status tg-admin.service --no-pager -l
```

上述 Git 命令采用本地 `127.0.0.1:2080` 的 SOCKS5 代理；没有配置该代理时去掉 `-c http.proxy=...`。更新会保留 `/etc/tg-admin/bot.env` 和已经配置的 L2 密钥及授权状态，且普通安装脚本不会主动增加 L2 root 授权。

**撤销 root 权限（保留普通 Bot）**：

```bash
sudo bash /opt/tg-admin/scripts/disable-l2.sh
sudo systemctl restart tg-admin.service
```

**卸载**：

```bash
sudo bash /opt/tg-admin/scripts/uninstall.sh
```

默认停止服务并移除 L1/L2 sudoers，保留代码、配置、L2 审计状态；`--purge` 才会删除本项目代码、凭据及状态。两种操作都不主动删除 Docker 容器、镜像和数据卷。详情参见 [部署约定](docs/deployment-conventions.md)。

## 安全边界

- Telegram 端按配置的 User ID + **私人 Chat ID** 双重检查；菜单也仅注册到该私聊，但菜单可见性不是权限校验。
- **L1**：普通 TOTP 会话最长 15 分钟，空闲 5 分钟过期，具有错误次数限制。
- **L2**：第二组独立 TOTP、root 保存待执行完整命令、60 秒请求有效期、单次消费与验证码重放防护；不缓存长期 root 会话。授权记录在 root 管理的 SQLite 中。
- Bot 以 `tgbot` 运行；普通执行账户 `tgexec` 不掌握 Bot Token 或 L2 密钥。高权限执行器 `privileged.py` 以 root 身份按需调用，sudoers 只许可固定执行入口。
- **重要限制**：Telegram Bot 私聊**不是端到端加密**。尽管验证码消息会尝试删除，无法保证完全消除其暴露风险；若 Bot 本身已被攻陷，目前的 L2 Telegram 验证流程也不能提供独立可信的授权确认。
- **一次 root 授权不是沙箱**：被授权的 Shell 命令可修改系统、启动后台进程、访问 Docker Socket，甚至永久改变权限。不要在不理解命令后果时直接执行。
- 当前实现的 root 命令为非交互式执行，约 120 秒超时、合计 24 KiB 输出上限。长时间镜像拉取、交互安装器或大日志可能不适合直接使用。
- 本项目仍需进一步进行请求篡改、异常中断、长期可靠性和安全审查测试。始终保留独立 SSH 管理能力。

## 真实进度与路线图

| 阶段 | 状态 | 当前已验证或待实现内容 |
| --- | --- | --- |
| Phase 0：需求与架构 | ✅ 完成 | 角色隔离、两级认证和单次授权设计 |
| Phase 1：普通管理 | ✅ 实机通过 | Telegram 连接、L1 TOTP、普通 Shell、结果回传、原生命令菜单 |
| Phase 2：单次提权 | ✅ 核心实机通过 | `/sudo id` root 执行、Docker 列表、重放/超时/取消；异常中断等补测 |
| Phase 3：Docker 管理 | 🚧 进行中 | 已用 `/sudo docker ps -a` 读取现有容器；专用交互、日志/启停等未完成 |
| Phase 4：部署与维护 | ⏳ 计划中 | Docker Compose、新容器、WebUI 端口、卷和更新流程 |
| Phase 5：工程化发布 | 🚧 部分完成 | 安装/卸载脚本、CI、部署文档已具备；安全审查与正式发布未完成 |

**下一步**：在已通过验证的 L2 单次授权上开发 Docker 容器信息、日志、启停和 Telegram 快捷交互，仍保留对现有 Docker Engine 的完整管理能力，不迁移容器。

## 项目结构

```text
tg-admin/
├── src/tg_admin/
│   ├── __main__.py       # Telegram 命令、菜单、ForceReply
│   ├── input_flow.py     # 回复消息绑定与超时
│   ├── core.py           # L1 会话与普通执行入口
│   └── privilege.py      # L2 执行器调用端
├── runner.py             # tgexec 普通 Shell 执行器
├── privileged.py         # root 独立 TOTP 验证与单次授权执行器
├── deploy/               # systemd 与 L1/L2 sudoers
├── scripts/              # install / uninstall / enable-l2 / disable-l2
├── tests/                # 单元测试
├── docs/                 # 设计和部署文档
├── .env.example          # 不包含真实密钥的配置模板
└── pyproject.toml
```

## 更多文档

- [Phase 1：首次安装、L1 TOTP 与普通命令](docs/phase1.md)
- [Phase 2：单次提权、TOTP 初始化、验证与回退](docs/phase2.md)
- [部署约定：安装、更新、卸载与目录权限](docs/deployment-conventions.md)
- [架构基线：安全边界与设计取舍](docs/architecture.md)

## License

仓库尚未明确附加开源许可证；在正式选择并添加许可证前，不对代码的再分发和修改授权作出承诺。
