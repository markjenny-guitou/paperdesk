#!/usr/bin/env bash
# Run from the uploaded source checkout: bash deploy/install-alinux.sh PUBLIC_IP
set -Eeuo pipefail
umask 022
APP_DIR=/opt/haoge-papers
DATA_DIR=/var/lib/haoge-papers
PUBLIC_HOST=${1:?Pass the server public IPv4 address or host name}
[[ $EUID -eq 0 ]] || { echo 'Run as root.' >&2; exit 1; }
[[ $PUBLIC_HOST =~ ^[a-zA-Z0-9.-]+$ ]] || { echo 'Invalid host.' >&2; exit 1; }
[[ $(pwd -P) == "$APP_DIR" && -f app.py && -f journal_catalog.py ]] || {
    echo "Place the source checkout in $APP_DIR first." >&2; exit 1;
}

# Use the distribution's supported Python; never replace /usr/bin/python3.
dnf -y install python3.11 python3.11-pip nginx openssl
id haogepapers >/dev/null 2>&1 || useradd --system --no-create-home --shell /sbin/nologin haogepapers
install -d -m 0750 -o haogepapers -g haogepapers "$DATA_DIR"
chmod 0755 "$APP_DIR"
python3.11 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/python" -m pip install --disable-pip-version-check \
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
"$APP_DIR/.venv/bin/python" -m unittest discover -s tests -q

# A distinct random application password; no SSH/GitHub credential is stored here.
umask 077
if [[ ! -s /etc/nginx/haoge-papers.htpasswd ]]; then
    APP_PASSWORD=$(openssl rand -hex 16)
    PASSWORD_HASH=$(printf '%s' "$APP_PASSWORD" | openssl passwd -6 -stdin)
    printf 'haoge:%s\n' "$PASSWORD_HASH" > /etc/nginx/haoge-papers.htpasswd
    printf 'Service: Haoge Papers\nURL: http://%s/\nUsername: haoge\nPassword: %s\n' \
        "$PUBLIC_HOST" "$APP_PASSWORD" > "$DATA_DIR/.access-credentials"
    chown root:root "$DATA_DIR/.access-credentials"
    chmod 0600 "$DATA_DIR/.access-credentials"
    unset APP_PASSWORD PASSWORD_HASH
fi
chown root:nginx /etc/nginx/haoge-papers.htpasswd
chmod 0640 /etc/nginx/haoge-papers.htpasswd
umask 022

install -m 0644 deploy/haoge-papers.service /etc/systemd/system/haoge-papers.service
NGINX_CONFIG=/etc/nginx/conf.d/haoge-papers.conf
if [[ -f $NGINX_CONFIG ]]; then
    cp -p "$NGINX_CONFIG" "$NGINX_CONFIG.backup.$(date +%s)"
fi
sed "s/__PUBLIC_HOST__/$PUBLIC_HOST/g" deploy/nginx.conf.template > "$NGINX_CONFIG"
chmod 0644 "$NGINX_CONFIG"
nginx -t
systemctl daemon-reload
systemctl enable --now haoge-papers
systemctl restart haoge-papers

"$APP_DIR/.venv/bin/python" - <<'PY'
import time
import urllib.request
for attempt in range(40):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8766/api/status', timeout=2) as r:
            if r.status == 200: break
    except OSError:
        time.sleep(.5)
else:
    raise SystemExit('Backend health check failed; inspect journalctl -u haoge-papers')
PY
systemctl enable --now nginx
systemctl reload nginx
echo 'Haoge Papers is running. Credentials are stored in /var/lib/haoge-papers/.access-credentials (root only).'
