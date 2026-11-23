#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
usage() {
  echo 'Usage: sudo bash scripts/uninstall.sh [--purge] [--yes]'
  echo 'Default: stop/disable tg-admin and remove only its systemd unit + sudoers.'
  echo '--purge: additionally remove /opt/tg-admin and /etc/tg-admin after explicit confirmation.'
  echo 'Keeps tgbot/tgexec accounts, any Docker resources, other services and system packages.'
}
purge=0
yes=0
for arg in "$@"; do
  case "$arg" in
    --purge) purge=1 ;;
    --yes) yes=1 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done
[[ $(id -u) -eq 0 ]] || { echo 'Run via sudo/root.' >&2; exit 1; }
repo=/opt/tg-admin
config=/etc/tg-admin
unit=/etc/systemd/system/tg-admin.service
sudoers=/etc/sudoers.d/tg-admin
for path in "$repo" "$config" "$unit" "$sudoers"; do
  [[ ! -L "$path" ]] || { echo "Refusing symlink: $path" >&2; exit 1; }
done
if [[ "$purge" -eq 1 && "$yes" -ne 1 ]]; then
  [[ -t 0 ]] || { echo 'Noninteractive purge requires --purge --yes' >&2; exit 1; }
  echo 'Will remove tg-admin code and credentials:'
  printf '  %s\n' "$repo" "$config"
  read -r -p 'Type DELETE tg-admin to confirm: ' answer
  [[ "$answer" == 'DELETE tg-admin' ]] || { echo 'Cancelled; nothing removed.'; exit 1; }
fi
systemctl disable --now tg-admin.service 2>/dev/null || true
rm -f -- "$unit" "$sudoers"
systemctl daemon-reload
systemctl reset-failed tg-admin.service 2>/dev/null || true
echo 'Stopped tg-admin; removed its systemd service and sudoers.'
if [[ "$purge" -eq 1 ]]; then
  for path in "$repo" "$config"; do
    if [[ -d "$path" ]]; then
      [[ "$(stat -c %u "$path")" == 0 ]] || { echo "Refusing non-root-owned directory: $path" >&2; exit 1; }
      rm -rf -- "$path"
    fi
  done
  echo 'Removed tg-admin code and credentials; preserve any backups separately.'
else
  echo 'Preserved code and credentials for reinstall.'
fi
echo 'Unchanged: tgbot/tgexec accounts, Docker containers/images/volumes, tdl-bot, tg-auto, Throne, QingLong, system packages.'
