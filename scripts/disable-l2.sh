#!/usr/bin/env bash
set -Eeuo pipefail
[[ $(id -u) -eq 0 ]] || { echo 'Run as root.' >&2; exit 1; }
[[ ! -L /etc/sudoers.d/tg-admin-l2 ]] || { echo 'Refusing symlinked sudoers.' >&2; exit 1; }
rm -f /etc/sudoers.d/tg-admin-l2
echo 'L2 root sudoers disabled. Secret, identity and audit state retained.'
echo 'Optional: sudo systemctl restart tg-admin.service to clear in-memory pending request.'
