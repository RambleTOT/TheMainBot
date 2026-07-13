"""Платёжный слой.

Сейчас используется MockProvider (демо). Реальный приём денег с автопродлением
и онлайн-кассой (54-ФЗ) подключается отдельным провайдером (ЮKassa / CloudPayments
и т.п.) — после согласования с заказчиком. Интерфейс PaymentProvider позволяет
заменить провайдера, не трогая остальной код.
"""
import uuid
from dataclasses import dataclass
from typing import Protocol


@dataclass
class PaymentResult:
    payment_id: str
    confirmation_url: str | None  # ссылка, куда отправить пользователя для оплаты
    status: str                   # pending | succeeded | failed


class PaymentProvider(Protocol):
    name: str

    async def create_payment(
        self,
        *,
        amount_kop: int,
        description: str,
        user_id: int,
        recurring: bool,
        return_url: str | None = None,
    ) -> PaymentResult:
        ...


class MockProvider:
    """Демо-провайдер: оплату «подтверждает» пользователь кнопкой в боте."""

    name = "mock"

    async def create_payment(self, *, amount_kop, description, user_id, recurring, return_url=None):
        return PaymentResult(
            payment_id=f"mock-{uuid.uuid4().hex[:12]}",
            confirmation_url=None,
            status="pending",
        )


# ---------------------------------------------------------------------------
# ЗАГОТОВКА под реального провайдера (пример — ЮKassa). НЕ используется, пока
# не согласован провайдер и не получены ключи. Псевдо-API для ориентира:
#
# class YooKassaProvider:
#     name = "yookassa"
#     def __init__(self, shop_id, secret_key): ...
#     async def create_payment(self, *, amount_kop, description, user_id, recurring, return_url):
#         # 1) Создать платёж через API ЮKassa c флагом save_payment_method=True (для рекуррента)
#         # 2) Передать данные чека (54-ФЗ): позиции, НДС, email/телефон покупателя
#         # 3) Вернуть confirmation_url (страница оплаты)
#         ...
#     async def charge_recurring(self, *, amount_kop, payment_method_id, description):
#         # Автосписание по сохранённому payment_method_id (для автопродления)
#         ...
#
# Важно для production:
#   - Платёж подтверждается ВЕБХУКОМ от провайдера (не кнопкой в боте).
#   - Проверять подпись/источник вебхука, обрабатывать идемпотентно
#     (один и тот же платёж может прийти несколько раз).
#   - Хранить payment_method_id (токен карты) для автосписаний — в зашифрованном виде.
#   - Формировать чеки (онлайн-касса) по 54-ФЗ.
# ---------------------------------------------------------------------------


def get_provider() -> PaymentProvider:
    return MockProvider()
