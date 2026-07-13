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

echo "==> Перезапуск сервисов (простой ~1-2 сек)"
systemctl restart themain-bot themain-admin

sleep 2
systemctl --no-pager --lines=0 status themain-bot themain-admin || true
echo "==> Готово. Логи: journalctl -u themain-bot -f"
