#!/usr/bin/env bash
# Быстрое обновление бота после изменений в коде.
# Запуск на сервере:  bash /opt/themain/deploy/update.sh
# (тексты и цены менять НЕ здесь, а в админке — без перезапуска)
set -euo pipefail
APP_DIR="/opt/themain"
cd "$APP_DIR"

echo "==> Обновление кода"
if [ -d .git ]; then
    git pull --ff-only
else
    echo "  (git не используется — код обновлён вручную через scp/rsync)"
fi

echo "==> Зависимости"
"$APP_DIR/.venv/bin/pip" install -q -r requirements.txt

echo "==> Обновление systemd-юнитов (на случай изменений, в т.ч. themain-pay)"
cp "$APP_DIR/deploy/themain-bot.service"   /etc/systemd/system/themain-bot.service
cp "$APP_DIR/deploy/themain-admin.service" /etc/systemd/system/themain-admin.service
cp "$APP_DIR/deploy/themain-pay.service"   /etc/systemd/system/themain-pay.service
systemctl daemon-reload
systemctl enable themain-pay.service >/dev/null 2>&1 || true

echo "==> Обновление конфига nginx"
cp "$APP_DIR/deploy/nginx-themain.conf" /etc/nginx/sites-available/themain
nginx -t && systemctl reload nginx

echo "==> Перезапуск сервисов (простой ~1-2 сек)"
systemctl restart themain-bot themain-admin themain-pay

sleep 2
systemctl --no-pager --lines=0 status themain-bot themain-admin themain-pay || true
echo "==> Готово. Логи: journalctl -u themain-bot -f  |  оплаты: journalctl -u themain-pay -f"
