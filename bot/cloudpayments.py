"""CloudPayments: проверка подписи вебхука, подпись платёжной ссылки, чек 54-ФЗ,
автосписание по сохранённому токену.

Подпись вебхука: заголовок X-Content-HMAC = base64( HMAC-SHA256( raw_body, API_SECRET ) ).
Токен карты приходит в уведомлении Pay и используется для рекуррента.
"""
import base64
import hashlib
import hmac as _hmac
import logging

import aiohttp
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import get_config

log = logging.getLogger(__name__)

CP_API = "https://api.cloudpayments.ru"


# ------------------------- подпись вебхука -------------------------

def hmac_ok(body: bytes, header: str | None) -> bool:
    """Проверка подписи вебхука CloudPayments (X-Content-HMAC / Content-HMAC)."""
    secret = get_config().cp_api_secret
    if not secret or not header:
        return False
    digest = _hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    expected = base64.b64encode(digest)  # bytes
    # Сравниваем в БАЙТАХ: подпись — всегда ASCII-base64. Заголовок Starlette отдаёт как
    # latin-1-строку; не-ASCII символ в str-сравнении уронил бы compare_digest (TypeError).
    try:
        provided = header.strip().encode("ascii")
    except (UnicodeEncodeError, AttributeError):
        return False
    return _hmac.compare_digest(expected, provided)


# ------------------------- подпись платёжной ссылки -------------------------

def _signer() -> URLSafeTimedSerializer:
    # подписываем параметры /pay, чтобы пользователь не подменил uid/тариф
    return URLSafeTimedSerializer(get_config().admin_secret or "themain", salt="cp-pay-v1")


def sign_pay(payload: dict) -> str:
    return _signer().dumps(payload)


def load_pay(token: str, max_age: int = 3600) -> dict | None:
    try:
        return _signer().loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired, Exception):  # noqa: BLE001
        return None


# ------------------------- чек 54-ФЗ -------------------------

def build_receipt(*, label: str, amount_rub: int, email: str | None) -> dict:
    cfg = get_config()
    item = {
        "label": label,
        "price": amount_rub,
        "quantity": 1,
        "amount": amount_rub,
        "vat": None if not cfg.cp_vat else int(cfg.cp_vat),  # УСН -> без НДС
        "method": 4,   # полный расчёт
        "object": 4,   # услуга
    }
    receipt: dict = {"Items": [item], "taxationSystem": cfg.cp_taxation_system}
    if email:
        receipt["email"] = email
    return receipt


# ------------------------- автосписание по токену -------------------------

async def charge_token(*, amount_rub: int, account_id: int, token: str,
                       description: str, label: str, email: str | None = None,
                       invoice_id: str | None = None) -> dict:
    """Списание по сохранённому токену (автопродление). {'ok': bool, 'raw': {...}}."""
    cfg = get_config()
    body: dict = {
        "Amount": amount_rub,
        "Currency": "RUB",
        "AccountId": str(account_id),
        "Token": token,
        "Description": description,
        "JsonData": {"CloudPayments": {"CustomerReceipt": build_receipt(
            label=label, amount_rub=amount_rub, email=email)}},
    }
    if invoice_id:
        body["InvoiceId"] = invoice_id
    auth = aiohttp.BasicAuth(cfg.cp_public_id or "", cfg.cp_api_secret or "")
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(f"{CP_API}/payments/tokens/charge", json=body, auth=auth,
                              timeout=aiohttp.ClientTimeout(total=30)) as r:
                data = await r.json(content_type=None)
    except Exception as e:  # noqa: BLE001
        log.exception("CloudPayments charge_token error: %s", e)
        return {"ok": False, "raw": {"error": str(e)}}
    return {"ok": bool(isinstance(data, dict) and data.get("Success")), "raw": data}
