#!/usr/bin/env bash
# ============================================================================
# THE MAIN — установка на сервер (Ubuntu 24.04, Beget VPS).
# Ставит: Python-окружение, PostgreSQL, systemd-сервисы (бот + админка),
# nginx + HTTPS (Let's Encrypt) на домен.
#
# Запуск от root:
#   1) залейте код проекта в /opt/themain  (git clone или scp)
#   2) отредактируйте блок НАСТРОЙКИ ниже (DOMAIN, EMAIL, DB_PASS)
#   3) создайте /opt/themain/.env из deploy/.env.production.example и заполните
#   4) bash /opt/themain/deploy/deploy.sh
# Скрипт идемпотентный — можно запускать повторно.
# ============================================================================
set -euo pipefail

# ------------------------- НАСТРОЙКИ (отредактировать) -------------------------
DOMAIN="themainpsychology.ru"
DOMAIN_WWW="www.themainpsychology.ru"     # оставьте пустым "", если www не настроен в DNS
EMAIL="admin@themainpsychology.ru"        # для Let's Encrypt (уведомления о сертификате)
APP_USER="themain"
APP_DIR="/opt/themain"
DB_NAME="themain"
DB_USER="themain"
DB_PASS="CHANGE_DB_PASSWORD"              # ДОЛЖЕН совпадать с паролем в DATABASE_URL в .env
# ------------------------------------------------------------------------------

[[ $EUID -eq 0 ]] || { echo "Запустите от root"; exit 1; }
[[ -f "$APP_DIR/bot/main.py" ]] || { echo "Код не найден в $APP_DIR (сначала залейте проект)"; exit 1; }

echo "==> Пакеты"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3-venv python3-pip postgresql nginx certbot python3-certbot-nginx ufw

echo "==> Пользователь приложения"
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"

echo "==> Python venv + зависимости"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"
"$APP_DIR/.venv/bin/pip" install "asyncpg>=0.29"   # драйвер PostgreSQL

echo "==> .env"
[[ -f "$APP_DIR/.env" ]] || { echo "Нет $APP_DIR/.env — создайте из deploy/.env.production.example и заполните"; exit 1; }
chown -R "$APP_USER":"$APP_USER" "$APP_DIR"
chmod 600 "$APP_DIR/.env"

echo "==> PostgreSQL: роль и база"
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1 \
  || sudo -u postgres psql -c "CREATE ROLE $DB_USER LOGIN PASSWORD '$DB_PASS';"
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1 \
  || sudo -u postgres createdb -O "$DB_USER" "$DB_NAME"

echo "==> Автозапуск БД на загрузке"
systemctl enable postgresql || true

echo "==> systemd сервисы (бот + админка + оплаты + ежедневный бэкап)"
cp "$APP_DIR/deploy/themain-bot.service"    /etc/systemd/system/themain-bot.service
cp "$APP_DIR/deploy/themain-admin.service"  /etc/systemd/system/themain-admin.service
cp "$APP_DIR/deploy/themain-pay.service"    /etc/systemd/system/themain-pay.service
cp "$APP_DIR/deploy/themain-backup.service" /etc/systemd/system/themain-backup.service
cp "$APP_DIR/deploy/themain-backup.timer"   /etc/systemd/system/themain-backup.timer
systemctl daemon-reload
# enable = автозапуск при загрузке сервера; --now = запустить сейчас
systemctl enable --now themain-bot.service
systemctl enable --now themain-admin.service
systemctl enable --now themain-pay.service
systemctl enable --now themain-backup.timer

echo "==> nginx"
cp "$APP_DIR/deploy/nginx-themain.conf" /etc/nginx/sites-available/themain
ln -sf /etc/nginx/sites-available/themain /etc/nginx/sites-enabled/themain
rm -f /etc/nginx/sites-enabled/default
systemctl enable nginx || true

# Бутстрап TLS: конфиг ссылается на боевой сертификат Let's Encrypt, которого при ПЕРВОМ
# запуске ещё нет. Без него `nginx -t` упал бы (cannot load certificate) и оборвал скрипт
# ДО certbot. Кладём временный самоподписанный + минимальный options-ssl, чтобы nginx
# поднялся; ниже certbot заменит их настоящими.
CERT_DIR="/etc/letsencrypt/live/$DOMAIN"
if [ ! -f "$CERT_DIR/fullchain.pem" ]; then
  echo "  (нет боевого сертификата — временный самоподписанный до certbot)"
  mkdir -p "$CERT_DIR"
  if [ ! -f /etc/letsencrypt/options-ssl-nginx.conf ]; then
    cat > /etc/letsencrypt/options-ssl-nginx.conf <<'EOF'
ssl_session_cache shared:le_nginx_SSL:10m;
ssl_session_timeout 1440m;
ssl_protocols TLSv1.2 TLSv1.3;
ssl_prefer_server_ciphers off;
EOF
  fi
  openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
    -keyout "$CERT_DIR/privkey.pem" -out "$CERT_DIR/fullchain.pem" \
    -subj "/CN=$DOMAIN" >/dev/null 2>&1
fi

nginx -t
systemctl reload nginx

echo "==> Фаервол"
ufw allow OpenSSH || true
ufw allow 'Nginx Full' || true
yes | ufw enable || true

echo "==> HTTPS (Let's Encrypt)"
CERT_DOMAINS=(-d "$DOMAIN")
[[ -n "$DOMAIN_WWW" ]] && CERT_DOMAINS+=(-d "$DOMAIN_WWW")
certbot --nginx "${CERT_DOMAINS[@]}" --non-interactive --agree-tos -m "$EMAIL" --redirect || \
  echo "!! certbot не прошёл — проверьте, что DNS $DOMAIN указывает на этот сервер и порт 80 открыт, затем запустите certbot вручную."

echo
echo "==> Готово. Статус:"
systemctl --no-pager --lines=0 status themain-bot.service   || true
systemctl --no-pager --lines=0 status themain-admin.service || true
systemctl --no-pager --lines=0 status themain-pay.service   || true
echo "Бот:   journalctl -u themain-bot -f"
echo "Админ: https://$DOMAIN/admin  (вход по ADMIN_PASSWORD)"
