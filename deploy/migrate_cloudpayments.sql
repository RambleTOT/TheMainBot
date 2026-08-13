-- Миграция БД под CloudPayments (PostgreSQL).
--
-- Зачем: init_db() при старте создаёт только НЕДОСТАЮЩИЕ таблицы, но НЕ добавляет
-- новые колонки в уже существующие. На боевой базе, развёрнутой до интеграции
-- CloudPayments, нужно один раз выполнить этот скрипт, иначе бот упадёт при первой
-- оплате (нет колонок card_token / cp_transaction_id).
--
-- Безопасно запускать повторно (IF NOT EXISTS). Для СВЕЖЕЙ базы не требуется.
--
-- Запуск на сервере:
--   sudo -u postgres psql themain -f /opt/themain/deploy/migrate_cloudpayments.sql

ALTER TABLE users    ADD COLUMN IF NOT EXISTS card_token        varchar(256);
ALTER TABLE users    ADD COLUMN IF NOT EXISTS email             varchar(320);
ALTER TABLE payments ADD COLUMN IF NOT EXISTS cp_transaction_id varchar(64);

-- Уникальный индекс = защита от повторной обработки одного вебхука (идемпотентность).
-- Имя совпадает с тем, что создаёт SQLAlchemy (ix_<таблица>_<колонка>).
CREATE UNIQUE INDEX IF NOT EXISTS ix_payments_cp_transaction_id ON payments (cp_transaction_id);
