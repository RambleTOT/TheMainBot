#!/usr/bin/env bash
# Резервная копия базы PostgreSQL. Запускается ежедневно через systemd-таймер
# (themain-backup.timer). Хранит 14 последних копий.
set -euo pipefail
DB_NAME="themain"
DIR="/opt/themain/backups"
mkdir -p "$DIR"
STAMP="$(date +%F_%H%M)"
FILE="$DIR/themain_${STAMP}.sql.gz"

sudo -u postgres pg_dump "$DB_NAME" | gzip > "$FILE"
echo "Backup: $FILE"

# оставить только 14 самых свежих
ls -1t "$DIR"/themain_*.sql.gz 2>/dev/null | tail -n +15 | xargs -r rm -f
