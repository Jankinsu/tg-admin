#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
[[ $(id -u) -eq 0 ]] || { echo 'Run as root: sudo bash scripts/enable-l2.sh' >&2; exit 1; }
repo=/opt/tg-admin
config=/etc/tg-admin
state=/var/lib/tg-admin
rule=/etc/sudoers.d/tg-admin-l2
[[ -f "$repo/privileged.py" && -f "$repo/deploy/tg-admin-l2.sudoers" ]] || { echo 'Missing Phase 2 agent; update repo first.' >&2; exit 1; }
for p in /opt "$repo" "$repo/privileged.py" "$repo/deploy" "$repo/deploy/tg-admin-l2.sudoers" "$config" "$state" "$rule"; do
  [[ ! -L "$p" ]] || { echo "Refusing symlink: $p" >&2; exit 1; }
done
for p in "$repo" "$repo/privileged.py" "$repo/deploy"; do
  [[ $(stat -c %u "$p") == 0 ]] || { echo "Not root owned: $p" >&2; exit 1; }
  mode=$(stat -c %a "$p")
  [[ $((8#$mode & 0022)) -eq 0 ]] || { echo "Writable privileged path: $p" >&2; exit 1; }
done
[[ -f "$config/bot.env" ]] || { echo "Missing $config/bot.env" >&2; exit 1; }
install -d -o root -g root -m 0700 "$state"
# Do not execute the environment file as a shell script. Parse only two expected keys.
python3 - <<'PY'
import json
from pathlib import Path
import re
p = Path("/etc/tg-admin/bot.env")
items = {}
for line in p.read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    if k in ("TG_ADMIN_USER_ID", "TG_ADMIN_CHAT_ID"):
        items[k] = v.strip().strip('"').strip("'")
if any(not re.fullmatch(r"[1-9][0-9]*", items.get(k, "")) for k in ("TG_ADMIN_USER_ID", "TG_ADMIN_CHAT_ID")):
    raise SystemExit("Set numeric User ID and private Chat ID in bot.env first.")
target = Path("/etc/tg-admin/l2-identity.json")
expected = {"user_id": int(items["TG_ADMIN_USER_ID"]), "chat_id": int(items["TG_ADMIN_CHAT_ID"])}
if target.exists():
    if json.loads(target.read_text()) != expected:
        raise SystemExit("L2 identity differs from bot.env. Disable L2 and review before changing.")
else:
    target.write_text(json.dumps(expected))
    target.chmod(0o600)
PY
chown root:root "$config/l2-identity.json"
chmod 0600 "$config/l2-identity.json"
secret="$config/l2.secret"
if [[ ! -e "$secret" ]]; then
  # Generate independent 160-bit Base32 TOTP seed and show only on operator console.
  python3 - <<'PY'
import base64, secrets
from pathlib import Path
secret = base64.b32encode(secrets.token_bytes(20)).decode("ascii")
p = Path("/etc/tg-admin/l2.secret")
with p.open("x", encoding="ascii") as f:
    f.write(secret + "\n")
p.chmod(0o600)
print("\nL2 TOTP secret (enter manually into your authenticator, then store securely):")
print(secret)
print("Label suggestion: tg-admin-L2")
PY
else
  echo 'Existing L2 TOTP secret preserved; script will not show it again.'
fi
chown root:root "$secret"
chmod 0600 "$secret"
tmp=$(mktemp /etc/sudoers.d/.tg-admin-l2.XXXXXXXX)
trap 'rm -f -- "$tmp"' EXIT
install -m 0440 -o root -g root "$repo/deploy/tg-admin-l2.sudoers" "$tmp"
visudo -cf "$tmp" >/dev/null
if [[ -e "$rule" ]] && ! cmp -s "$repo/deploy/tg-admin-l2.sudoers" "$rule"; then
  backup=$(mktemp "$rule.backup.XXXXXXXX")
  cp -p "$rule" "$backup"
  echo "Backed up previous L2 sudoers: $backup"
fi
install -m 0440 -o root -g root "$repo/deploy/tg-admin-l2.sudoers" "$rule"
visudo -cf "$rule" >/dev/null
echo 'L2 sudoers installed. Bot restart is NOT automatic.'
echo 'Verify: sudo -u tgbot sudo -n -l'
echo 'Then: sudo systemctl restart tg-admin.service'
