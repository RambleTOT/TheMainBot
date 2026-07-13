"""Клавиатуры: постоянное нижнее меню (reply) и inline-кнопки."""
from aiogram.types import InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from . import texts
from .config import get_config
from .content import TariffDTO
from .services import SubInfo


def main_menu() -> ReplyKeyboardMarkup:
    """Постоянное меню снизу — видно всегда."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=texts.BTN_TARIFFS)],
            [KeyboardButton(text=texts.BTN_MY_SUB)],
            [KeyboardButton(text=texts.BTN_INFO)],
        ],
        resize_keyboard=True,
    )


def tariffs_kb(tariffs: list[TariffDTO], offer_url: str | None = None) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for t in tariffs:
        label = f"{t.emoji} {t.title}".strip()
        b.button(text=label, callback_data=f"t:{t.code}")
    if offer_url:
        b.button(text=texts.BTN_OFFER, url=offer_url)
    b.adjust(1)
    return b.as_markup()


def tariff_detail_kb(code: str, offer_url: str | None = None) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=texts.BTN_PAY, callback_data=f"p:{code}")
    b.button(text=texts.BTN_PAY_GIFT, callback_data=f"gp:{code}")
    if offer_url:
        b.button(text=texts.BTN_OFFER, url=offer_url)
    b.button(text=texts.BTN_BACK, callback_data="open_tariffs")
    b.adjust(1)
    return b.as_markup()


def pay_kb(code: str, *, gift: bool = False, confirmation_url: str | None = None,
           test: bool = True) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if confirmation_url:
        b.button(text=texts.BTN_PAY, url=confirmation_url)
    if test:
        success = f"gs:{code}" if gift else f"s:{code}"
        b.button(text=texts.BTN_PAID_TEST, callback_data=success)
    b.button(text=texts.BTN_BACK, callback_data=f"t:{code}")
    b.adjust(1)
    return b.as_markup()


def subscription_kb(sub: SubInfo | None, links: dict[str, str] | None = None) -> InlineKeyboardMarkup:
    """Кнопки под постом подписки: ресурсы + (опц.) остановка автопродления."""
    cfg = get_config()
    links = links or {}
    b = InlineKeyboardBuilder()
    for r in cfg.resources:
        url = links.get(r.key) or r.url
        if url:
            b.button(text=r.title, url=url)
        else:
            b.button(text=r.title, callback_data="res_soon")
    if sub and not sub.is_forever and sub.autorenew and sub.status == "active":
        b.button(text=texts.BTN_STOP_AR, callback_data="ar_stop")
    b.adjust(1)
    return b.as_markup()


def my_sub_none_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=texts.BTN_SUBSCRIBE, callback_data="open_tariffs")
    return b.as_markup()


def stop_ar_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=texts.BTN_STOP_AR_YES, callback_data="ar_stop_yes")
    b.button(text=texts.BTN_BACK_HAND, callback_data="ar_back")
    b.adjust(1)
    return b.as_markup()
