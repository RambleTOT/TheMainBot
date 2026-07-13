# Развёртывание и постоянная работа THE MAIN (Beget VPS, Ubuntu 24.04)

Домен: **themainpsychology.ru**. Админка: **https://themainpsychology.ru/admin**.
Бот работает на long-polling, админка — за nginx+HTTPS, данные — в PostgreSQL.

> ⚠️ Секреты (токен бота, пароли) задаются **только на сервере** в файле `.env`.
> Не пересылайте их в чатах. Токен при необходимости перевыпускается в @BotFather.

---

## Что обеспечивает бесперебойную работу (главное)

| Механизм | Что даёт |
|----------|----------|
| **systemd + `Restart=always`** | Бот/админка **сами перезапускаются** при любой ошибке или падении (через ~5 сек), без лимита попыток. |
| **systemd `enable`** | Сервисы **сами стартуют при загрузке** сервера — после перезагрузки, обновления или сбоя питания. |
| **PostgreSQL на диске** | Подписки, платежи, тексты **не теряются** при перезапусках/выключении. |
| **Ежедневные бэкапы базы** | Копия БД каждый день (03:30), хранится 14 дней — защита от потери данных. |
| **Авто-продление HTTPS** | Сертификат Let's Encrypt обновляется автоматически (таймер certbot). |
| **Тексты/цены — в админке** | Меняются **без перезапуска и без деплоя**, мгновенно. |

Итог: чтобы бот «упал» надолго, должен целиком лежать сервер. Пока сервер жив — бот жив.

---

## Установка (один раз)

### 1. DNS
В панели Beget → Домены → DNS: A-запись `themainpsychology.ru` → IP сервера.
Проверка: `ping themainpsychology.ru` показывает ваш IP.

### 2. Зайти на сервер и залить код
Подключение по SSH — см. `deploy/SSH_подключение.md`. Затем код в `/opt/themain`:
```bash
# вариант git (удобно для будущих обновлений одной командой):
apt-get update && apt-get install -y git
git clone <URL-репозитория> /opt/themain

# ИЛИ scp/rsync с вашего компьютера:
rsync -av --exclude '.venv' --exclude 'node_modules' --exclude '__pycache__' \
      --exclude '*.db' --exclude '.env' --exclude '.git' \
      /путь/к/Bot/  root@СЕРВЕР:/opt/themain/
```

### 3. Заполнить .env
```bash
cp /opt/themain/deploy/.env.production.example /opt/themain/.env
python3 -c "import secrets; print('ADMIN_SECRET =', secrets.token_urlsafe(32))"
nano /opt/themain/.env
```
Заполнить: `BOT_TOKEN`, `ADMIN_PASSWORD`, `ADMIN_SECRET`, пароль базы в `DATABASE_URL`,
ресурсы. Для стенда — `TEST_MODE=true`; для боевого приёма оплат (после ЮKassa) — `false`.

### 4. Проверить настройки скрипта и запустить установку
```bash
nano /opt/themain/deploy/deploy.sh   # DOMAIN, EMAIL, DB_PASS (= пароль из DATABASE_URL)
bash /opt/themain/deploy/deploy.sh
```
Скрипт ставит зависимости, PostgreSQL, systemd-сервисы (бот, админка, бэкап-таймер),
nginx и выпускает HTTPS-сертификат. **Идемпотентный** — можно запускать повторно.

### 5. Проверить
```bash
systemctl status themain-bot themain-admin --no-pager
journalctl -u themain-bot -n 30 --no-pager
```
- Напишите боту `/start` в Telegram.
- Откройте `https://themainpsychology.ru/admin` (вход по `ADMIN_PASSWORD`).
- Добавьте бота **администратором** в канал (права «Пригласительные ссылки» и «Блокировать»),
  отправьте в канале `/id`, впишите ID в `.env` (`RES_PRIVATKA_CHAT_ID`) и `bash deploy/update.sh`.

---

## Как быстро вносить изменения

### А. Тексты, цены, кнопки-контент — через админку (без деплоя, мгновенно)
`https://themainpsychology.ru/admin` → **Тексты** / **Тарифы и цены**. Бот подхватывает
изменения сразу, перезапуск не нужен. Это 90% правок.

### Б. Изменения в коде — одной командой
```bash
# если код через git — сначала запушьте правки, затем на сервере:
bash /opt/themain/deploy/update.sh
```
`update.sh` делает `git pull`, ставит зависимости и перезапускает сервисы (простой ~1–2 сек).
Если код заливаете через rsync — сначала залейте, потом `update.sh` (он перезапустит сервисы).

---

## Проверка отказоустойчивости (можно убедиться самому)
```bash
# 1) «убить» бота — systemd поднимет его снова за ~5 сек:
systemctl kill themain-bot ; sleep 6 ; systemctl status themain-bot --no-pager

# 2) перезагрузить сервер — сервисы стартуют сами:
reboot
#   после перезагрузки: systemctl status themain-bot themain-admin
```
Обрыв связи с Telegram бот переживает сам (aiogram переподключается на long-polling).

---

## Бэкапы и восстановление
- Копии базы: `/opt/themain/backups/themain_ДАТА.sql.gz` (ежедневно 03:30, хранится 14 шт.).
- Сделать копию прямо сейчас: `bash /opt/themain/deploy/backup.sh`
- Проверить таймер: `systemctl list-timers themain-backup`
- Восстановить из копии:
  ```bash
  gunzip -c /opt/themain/backups/themain_2026-09-01_0330.sql.gz | sudo -u postgres psql themain
  ```
  (при полном пересоздании базы — сначала `sudo -u postgres createdb -O themain themain`)

> Рекомендуется раз в неделю скачивать свежий бэкап к себе на компьютер
> (`scp root@СЕРВЕР:/opt/themain/backups/… .`) — на случай отказа диска сервера.

---

## Логи и диагностика
```bash
journalctl -u themain-bot -f       # живые логи бота
journalctl -u themain-admin -f     # логи админки
journalctl -u themain-bot -p err -n 100 --no-pager   # только ошибки
systemctl status themain-bot       # статус, аптайм, последние строки
```

---

## Откат при неудачном обновлении (rollback)
```bash
cd /opt/themain
git log --oneline -n 5          # найти предыдущий рабочий коммит
git checkout <хеш_коммита>
bash deploy/update.sh
# вернуться на последнюю версию: git checkout <ветка> && bash deploy/update.sh
```
Если проблема в данных — восстановите базу из бэкапа (см. выше).

---

## Дополнительно (по желанию)
- **Авто-обновления безопасности ОС:**
  ```bash
  apt-get install -y unattended-upgrades && dpkg-reconfigure -plow unattended-upgrades
  ```
- **Ограничить админку по IP:** блок `allow/deny` в `deploy/nginx-themain.conf`, затем
  `nginx -t && systemctl reload nginx`.
- **Уведомления о падении в Telegram** — можем добавить на Этапе 2 (алерт в служебный чат).

---

## Важно про оплату
Текущая версия — **демо-оплата** (ЮKassa ещё не подключена). Бот полностью работает как стенд
(навигация, подписки, админка), но **реальные деньги не принимает** до Этапа 2 (интеграция
ЮKassa: платёж, вебхук, автосписания, чеки 54-ФЗ). Тогда же переключим `TEST_MODE=false`.
