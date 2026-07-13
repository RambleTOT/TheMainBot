"""Обработчики команд и кнопок."""
import logging
import os
from html import escape as _esc

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    ChatJoinRequest,
    FSInputFile,
    LinkPreviewOptions,
    Message,
)

from . import content
from . import keyboards as kb
from . import texts
from .access import grant_access
from .config import get_config
from .services import (
    SubInfo,
    activate_subscription,
    create_gift_code,
    duration_text,
    fmt_msk,
    fmt_price,
    get_active_subscription,
    is_blocked,
    redeem_gift,
    stop_autorenew,
    upsert_user,
)

log = logging.getLogger(__name__)
router = Router()

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


# ------------------------- вспомогательное -------------------------

async def _active_sub_text(sub: SubInfo) -> str:
    tariff = await content.get_tariff(sub.tariff_id)
    title = tariff.title if tariff else sub.tariff_id
    emoji = tariff.emoji if tariff else ""
    if sub.is_forever:
        date = await content.get_setting("forever_date_label")
        autopay = "—"
    else:
        date = fmt_msk(sub.expires_at)
        key = "autopay_on" if sub.autorenew else "autopay_off"
        autopay = await content.get_setting(key)
    return await content.get_text(
        "my_sub_active", tariff=_esc(title), emoji=_esc(emoji), date=date, autopay=_esc(autopay)
    )


async def _send_welcome(message: Message) -> None:
    text = await content.get_text("welcome_text")
    image = await content.get_setting("welcome_image")
    if image:
        photo = FSInputFile(image) if os.path.exists(image) else image
        try:
            await message.answer_photo(photo, caption=text, reply_markup=kb.main_menu())
            return
        except Exception:  # noqa: BLE001
            log.exception("Не удалось отправить фото приветствия, шлю текстом")
    await message.answer(text, reply_markup=kb.main_menu(), link_preview_options=NO_PREVIEW)


# ------------------------- /start -------------------------

@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, bot: Bot) -> None:
    await upsert_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    arg = command.args or ""
    if arg.startswith("gift_"):
        await _send_welcome(message)
        await _handle_gift_redeem(message, bot, arg[len("gift_"):])
        return
    await _send_welcome(message)


async def _handle_gift_redeem(message: Message, bot: Bot, code: str) -> None:
    result = await redeem_gift(code, message.from_user.id)
    if result is None:
        await message.answer(await content.get_text("gift_invalid"))
        return
    sub, tariff = result
    links = await grant_access(bot, message.from_user.id)
    if tariff.is_forever:
        text = await content.get_text("gift_redeem_forever", title=_esc(tariff.title))
    else:
        text = await content.get_text(
            "gift_redeem_success", title=_esc(tariff.title), date=fmt_msk(sub.expires_at)
        )
    await message.answer(text, reply_markup=kb.subscription_kb(sub, links))


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    """Утилита для настройки: показывает chat_id (вызвать в нужном канале/группе)."""
    await message.answer(f"chat_id: {message.chat.id}\nuser_id: {message.from_user.id}")


# ------------------------- Тарифы -------------------------

async def _show_tariffs(message: Message) -> None:
    tariffs = await content.get_tariffs(active_only=True)
    offer = await content.get_setting("offer_url")
    text = await content.get_text("tariffs_intro")
    await message.answer(text, reply_markup=kb.tariffs_kb(tariffs, offer or None))


@router.message(F.text == texts.BTN_TARIFFS)
async def show_tariffs(message: Message) -> None:
    await _show_tariffs(message)


@router.callback_query(F.data == "open_tariffs")
async def open_tariffs(cq: CallbackQuery) -> None:
    await _show_tariffs(cq.message)
    await cq.answer()


@router.callback_query(F.data.startswith("t:"))
async def tariff_detail(cq: CallbackQuery) -> None:
    code = cq.data.split(":", 1)[1]
    tariff = await content.get_tariff(code)
    if tariff is None or not tariff.is_active:
        await cq.answer("Тариф недоступен", show_alert=True)
        return
    card = await content.get_text(
        "tariff_card", title=_esc(tariff.title), emoji=_esc(tariff.emoji),
        price=fmt_price(tariff.price_rub), duration=duration_text(tariff),
    )
    disclaimer = await content.get_text("tariffs_disclaimer")
    offer = await content.get_setting("offer_url")
    text = f"{card}\n\n{disclaimer}" if disclaimer else card
    await cq.message.answer(text, reply_markup=kb.tariff_detail_kb(code, offer or None))
    await cq.answer()


# ------------------------- Оплата (демо) -------------------------

@router.callback_query(F.data.startswith("p:"))
async def pay(cq: CallbackQuery) -> None:
    await _pay_screen(cq, gift=False, key="pay_intro")


@router.callback_query(F.data.startswith("gp:"))
async def pay_gift(cq: CallbackQuery) -> None:
    await _pay_screen(cq, gift=True, key="gift_intro")


async def _pay_screen(cq: CallbackQuery, *, gift: bool, key: str) -> None:
    code = cq.data.split(":", 1)[1]
    tariff = await content.get_tariff(code)
    if tariff is None or not tariff.is_active:
        await cq.answer("Тариф недоступен", show_alert=True)
        return
    cfg = get_config()
    # PRODUCTION: здесь создаётся платёж у ЮKassa и возвращается confirmation_url;
    # подтверждение — вебхуком от провайдера, а не кнопкой пользователя.
    text = await content.get_text(key, title=_esc(tariff.title), price=fmt_price(tariff.price_rub))
    await cq.message.answer(text, reply_markup=kb.pay_kb(code, gift=gift, test=cfg.test_mode))
    await cq.answer()


@router.callback_query(F.data.startswith("s:"))
async def paid_test(cq: CallbackQuery, bot: Bot) -> None:
    """ДЕМО-подтверждение оплаты (себе). В production заменяется вебхуком провайдера."""
    cfg = get_config()
    if not cfg.test_mode:
        await cq.answer("Тестовая оплата отключена", show_alert=True)
        return
    code = cq.data.split(":", 1)[1]
    tariff = await content.get_tariff(code)
    if tariff is None:
        await cq.answer("Тариф не найден", show_alert=True)
        return
    sub = await activate_subscription(cq.from_user.id, tariff, is_recurring=not tariff.is_forever)
    links = await grant_access(bot, cq.from_user.id)
    thanks = await content.get_text("thanks_text")
    details = await _active_sub_text(sub)
    await cq.message.answer(f"{thanks}\n\n{details}", reply_markup=kb.subscription_kb(sub, links))
    await cq.answer("Оплата подтверждена ✅")


@router.callback_query(F.data.startswith("gs:"))
async def paid_gift_test(cq: CallbackQuery, bot: Bot) -> None:
    """ДЕМО-подтверждение оплаты подарка. Выдаёт одноразовую ссылку для друга."""
    cfg = get_config()
    if not cfg.test_mode:
        await cq.answer("Тестовая оплата отключена", show_alert=True)
        return
    code = cq.data.split(":", 1)[1]
    tariff = await content.get_tariff(code)
    if tariff is None:
        await cq.answer("Тариф не найден", show_alert=True)
        return
    gift_code = await create_gift_code(cq.from_user.id, tariff)
    me = await bot.me()
    link = f"https://t.me/{me.username}?start=gift_{gift_code}"
    text = await content.get_text("gift_paid", title=_esc(tariff.title), link=_esc(link))
    await cq.message.answer(text, link_preview_options=NO_PREVIEW)
    await cq.answer("Подарок оплачен 🎁")


# ------------------------- Моя подписка -------------------------

@router.message(F.text == texts.BTN_MY_SUB)
async def my_subscription(message: Message) -> None:
    sub = await get_active_subscription(message.from_user.id)
    if sub is None:
        await message.answer(await content.get_text("my_sub_none"), reply_markup=kb.my_sub_none_kb())
        return
    await message.answer(await _active_sub_text(sub), reply_markup=kb.subscription_kb(sub))


# ------------------------- Остановка автопродления -------------------------

@router.callback_query(F.data == "ar_stop")
async def ar_stop(cq: CallbackQuery) -> None:
    await cq.message.answer(await content.get_text("stop_ar_confirm"), reply_markup=kb.stop_ar_kb())
    await cq.answer()


@router.callback_query(F.data == "ar_stop_yes")
async def ar_stop_yes(cq: CallbackQuery) -> None:
    ok = await stop_autorenew(cq.from_user.id)
    if ok:
        await cq.message.answer(await content.get_text("stop_ar_done"))
    else:
        await cq.message.answer(await content.get_text("my_sub_none"), reply_markup=kb.my_sub_none_kb())
    await cq.answer()


@router.callback_query(F.data == "ar_back")
async def ar_back(cq: CallbackQuery) -> None:
    sub = await get_active_subscription(cq.from_user.id)
    if sub:
        await cq.message.answer(await _active_sub_text(sub), reply_markup=kb.subscription_kb(sub))
    await cq.answer()


# ------------------------- Подробнее / прочее -------------------------

@router.message(F.text == texts.BTN_INFO)
async def info(message: Message) -> None:
    await message.answer(await content.get_text("privatka_text"), link_preview_options=NO_PREVIEW)


@router.callback_query(F.data == "res_soon")
async def res_soon(cq: CallbackQuery) -> None:
    await cq.answer(await content.get_text("resource_soon"), show_alert=True)


# ------------------------- Заявки на вступление в ресурсы -------------------------

@router.chat_join_request()
async def on_join_request(update: ChatJoinRequest) -> None:
    """Одобряем заявку только если у пользователя активная подписка и он не заблокирован.

    Так доступ нельзя получить по пересланной ссылке: впускает не ссылка, а бот —
    и только того, кто реально оплатил. Бот должен быть админом ресурса.
    """
    uid = update.from_user.id
    sub = await get_active_subscription(uid)
    if sub is not None and not await is_blocked(uid):
        await update.approve()
        log.info("Join approved: user=%s chat=%s", uid, update.chat.id)
    else:
        await update.decline()
        log.info("Join declined: user=%s chat=%s", uid, update.chat.id)


@router.message()
async def fallback(message: Message) -> None:
    await message.answer(await content.get_text("fallback"), reply_markup=kb.main_menu())
