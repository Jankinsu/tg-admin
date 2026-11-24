# 与 tdl-bot / tg-auto 一致的部署约定

## 共同习惯

已对照 `Jankinsu/tdl-bot`、`Jankinsu/tg-auto` 的安装/卸载脚本：

- 一个仓库维护 `scripts/install.sh`、`scripts/uninstall.sh`。
- 安装脚本可重复运行，保留已创建的配置和凭据；配置文件不放入 Git。
- 默认**不自动启动或重启**服务；启动需显式 `--enable-service`，重启需 `--restart`。
- 更新现有 service / sudoers 文件前备份不同版本。
- 卸载默认只停服务并撤销管理权限，保留配置和代码；`--purge` 才删除。
- 不干涉 Docker 容器、其他 Bot、Throne、青龙或公共软件包。

## tg-admin 为什么是系统级部署

`tdl-bot` 和 `tg-auto` 不执行远程任意 Shell 命令，因此使用普通用户的 `~/apps`、`~/.config` 和 `systemctl --user`。

`tg-admin` 的 Bot 会接受远程命令，后续还需独立 root 单次授权。**普通命令用户不能修改 Bot 代码、认证配置、runner、服务文件或 sudoers 目标**。因此采用：

| 类别 | 路径或账户 |
| --- | --- |
| 程序源码与 Python 虚拟环境 | `/opt/tg-admin`（root 所有） |
| Bot Token、TOTP 与用户白名单 | `/etc/tg-admin/bot.env`（root:tgbot，0640） |
| Bot 常驻服务 | `/etc/systemd/system/tg-admin.service` |
| 普通命令 sudoers（切换到 tgexec） | `/etc/sudoers.d/tg-admin` |
| 可选 L2 root 授权（须手动启用） | `/etc/sudoers.d/tg-admin-l2` |
| L2 独立密钥与身份 | `/etc/tg-admin/l2.secret`、`l2-identity.json`（root 独占） |
| 授权请求状态 | `/var/lib/tg-admin/authorization.sqlite3`（root 独占） |
| Bot 进程账户 | `tgbot`，无 root / docker 组权限 |
| 普通命令账户 | `tgexec`，无 root / docker 组权限 |

**只改变部署位置与权限，不改变操作风格。** 已存在的专用账户不会被重建；安装脚本要求两个账户存在，缺失时提示先创建。当前已实现 Phase 2 的独立 L2 授权，但普通 `install.sh` **不会自动启用 root 特权入口**。

## 新部署步骤（SSH 执行）

首次部署到新服务器时，先准备两个独立非 root 系统账户 `tgbot` 与 `tgexec`（不得属于 `sudo` 或 `docker` 组），随后执行：

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip sudo

# 假如目录尚不存在：
sudo git clone https://github.com/Jankinsu/tg-admin.git /opt/tg-admin
sudo chown -R root:root /opt/tg-admin
sudo chmod -R go-w /opt/tg-admin

cd /opt/tg-admin
sudo bash scripts/install.sh
```

脚本只安装 Python 依赖、建立保密配置模板、验证 sudoers、放置 systemd 文件；**默认不启动 Bot**。

使用 SSH 编辑 `/etc/tg-admin/bot.env`，填入真实 `TG_ADMIN_BOT_TOKEN`、`TG_ADMIN_USER_ID`、`TG_ADMIN_CHAT_ID` 和新生成的 L1 TOTP 密钥，配置步骤见 [Phase 1 文档](phase1.md)。

```bash
sudo systemctl enable --now tg-admin.service
sudo systemctl status tg-admin.service --no-pager
```

## 更新/卸载

Git 仓库由 root 持有，因此通过 SSH 用明确的 root 命令更新：

```bash
sudo git -c http.proxy=socks5h://127.0.0.1:2080 -C /opt/tg-admin pull --ff-only
cd /opt/tg-admin
sudo bash scripts/install.sh           # 不主动重启运行中的服务
sudo bash scripts/install.sh --restart # 仅在明确需要时重启已运行服务
```

仅使用以上命令更新时，L2 的独立 sudoers 授权状态不受影响。若需要首次开放高权限管理，先阅读 [Phase 2 文档](phase2.md)，再通过 SSH 显式执行 `sudo bash /opt/tg-admin/scripts/enable-l2.sh`；禁用时执行 `sudo bash /opt/tg-admin/scripts/disable-l2.sh` 并重启 Bot。

上例 Git 使用本机 `127.0.0.1:2080` SOCKS5 代理，若不使用代理，请删除 `-c http.proxy=...`。更新前仍应审查代码及高权限路径。

默认卸载：

```bash
cd /opt/tg-admin
sudo bash scripts/uninstall.sh
```

彻底卸载（仅 tg-admin 程序、机密配置及 L2 授权状态，不删除用户或 Docker 数据）：

```bash
sudo bash /opt/tg-admin/scripts/uninstall.sh --purge
```

**特别提醒**：当前 Phase 1 和 Phase 2 的主要操作已经通过 Debian 13 实机验证，但这仍是高权限远程管理 Bot 的开发版本。首次启用前审查代码与权限，保持 SSH 可用；Git pull 之后不要盲目执行更新脚本。
