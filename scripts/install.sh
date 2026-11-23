#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

usage() {
  echo 'Usage: sudo bash scripts/install.sh [--enable-service] [--restart]'
  echo 'Run from root-owned /opt/tg-admin checkout. Does not clone, ask for secrets or start by default.'
  echo 'Re-running preserves bot.env and makes a timestamped backup of changed service/sudoers files.'
}
enable=0
restart=0
for arg in "$@"; do
  case "$arg" in
    --enable-service) enable=1 ;;
    --restart) restart=1 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done
[[ $(id -u) -eq 0 ]] || { echo 'Run as root: sudo bash scripts/install.sh' >&2; exit 1; }
[[ "$enable" -eq 0 || "$restart" -eq 0 ]] || { echo 'Choose only one: --enable-service or --restart' >&2; exit 2; }

repo=/opt/tg-admin
config=/etc/tg-admin
envfile="$config/bot.env"
unit=/etc/systemd/system/tg-admin.service
sudoers=/etc/sudoers.d/tg-admin
for cmd in python3 systemctl visudo stat install; do
  command -v "$cmd" >/dev/null || { echo "Missing: $cmd" >&2; exit 1; }
done
[[ -f "$repo/pyproject.toml" && -f "$repo/runner.py" && -f "$repo/deploy/tg-admin.service" && -f "$repo/deploy/tg-admin.sudoers" ]] || {
  echo "Clone repository to $repo before running this installer." >&2; exit 1;
}
# Nothing on the privileged executable path may be controlled by a non-root user.
for path in /opt "$repo" "$repo/runner.py" "$repo/src" "$repo/src/tg_admin" "$repo/deploy" "$repo/scripts"; do
  [[ ! -L "$path" ]] || { echo "Refusing symlink: $path" >&2; exit 1; }
  [[ "$(stat -c %u "$path")" == 0 ]] || { echo "Not owned by root: $path" >&2; exit 1; }
  mode=$(stat -c %a "$path")
  [[ $((8#$mode & 0022)) -eq 0 ]] || { echo "Group/other writable: $path" >&2; exit 1; }
done
for path in "$config" "$envfile" "$unit" "$sudoers" "$repo/.venv"; do
  [[ ! -L "$path" ]] || { echo "Refusing symlink: $path" >&2; exit 1; }
done
for user in tgbot tgexec; do
  id "$user" >/dev/null 2>&1 || { echo "Missing account $user; create the two system users first." >&2; exit 1; }
  [[ "$(id -u "$user")" != 0 ]] || { echo "Invalid root account: $user" >&2; exit 1; }
  if id -nG "$user" | tr ' ' '\n' | grep -Eq '^(sudo|docker)$'; then
    echo "$user belongs to sudo or docker group; refusing." >&2; exit 1
  fi
done
if [[ -e "$repo/.venv" ]]; then
  [[ "$(stat -c %u "$repo/.venv")" == 0 ]] || { echo 'venv must be root-owned.' >&2; exit 1; }
fi

# Prepare dependencies without starting/changing the running service.
if [[ ! -x "$repo/.venv/bin/python" ]]; then
  python3 -m venv "$repo/.venv" || { echo 'venv failed; install python3-venv.' >&2; exit 1; }
fi
"$repo/.venv/bin/python" -m pip install "$repo"
"$repo/.venv/bin/python" -m compileall -q "$repo/src" "$repo/runner.py"
python3 -m compileall -q "$repo/runner.py"

install -d -o root -g tgbot -m 0750 "$config"
if [[ ! -e "$envfile" ]]; then
  install -m 0640 -o root -g tgbot /dev/null "$envfile"
  cat > "$envfile" <<'ENV'
TG_ADMIN_BOT_TOKEN=replace_me
TG_ADMIN_USER_ID=0
TG_ADMIN_CHAT_ID=0
TG_ADMIN_TOTP_SECRET=replace_me
ENV
  echo "Created $envfile. Set credentials before starting."
else
  echo "Preserving existing credentials: $envfile"
fi
chown root:tgbot "$envfile"
chmod 0640 "$envfile"

# Validate sudoers in a temporary root-only file before replacing the active rule.
tmp=$(mktemp /etc/sudoers.d/.tg-admin.XXXXXXXX)
trap 'rm -f -- "$tmp"' EXIT
install -m 0440 -o root -g root "$repo/deploy/tg-admin.sudoers" "$tmp"
visudo -cf "$tmp" >/dev/null

install_unit() {
  local src="$1" dest="$2" mode="$3"
  if [[ -e "$dest" ]] && ! cmp -s "$src" "$dest"; then
    local backup
    backup=$(mktemp "$dest.backup.XXXXXXXX")
    cp -p -- "$dest" "$backup"
    echo "Backed up: $backup"
  fi
  install -m "$mode" -o root -g root "$src" "$dest"
}
install_unit "$repo/deploy/tg-admin.sudoers" "$sudoers" 0440
visudo -cf "$sudoers" >/dev/null
install_unit "$repo/deploy/tg-admin.service" "$unit" 0644
systemctl daemon-reload
echo 'Installed tg-admin: configuration preserved; no Docker changes.'
if [[ "$enable" -eq 1 ]]; then
  if grep -Eq '^(TG_ADMIN_BOT_TOKEN=replace_me|TG_ADMIN_USER_ID=0|TG_ADMIN_CHAT_ID=0|TG_ADMIN_TOTP_SECRET=replace_me)$' "$envfile"; then
    echo 'Fill bot.env before --enable-service.' >&2; exit 1
  fi
  systemctl enable --now tg-admin.service
elif [[ "$restart" -eq 1 ]]; then
  if systemctl is-active --quiet tg-admin.service; then
    systemctl restart tg-admin.service
  else
    echo 'Service not running; --restart does not enable it.'
  fi
else
  echo 'Service not started/restarted. After editing /etc/tg-admin/bot.env:'
  echo '  sudo systemctl enable --now tg-admin.service'
fi
echo 'Check: sudo systemctl status tg-admin.service --no-pager'
