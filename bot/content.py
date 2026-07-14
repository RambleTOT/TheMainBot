"""Редактируемый контент бота: тексты и тарифы хранятся в БД.

Значения по умолчанию (DEFAULT_SETTINGS / DEFAULT_TARIFFS) заполняются при первом
запуске и дальше редактируются из админ-панели. Бот всегда читает актуальные
значения из БД, поэтому правки в админке применяются сразу.
"""
from dataclasses import dataclass

from sqlalchemy import select

from .database import Setting, Tariff, get_sessionmaker


# ------------------------- тексты по умолчанию -------------------------
# Форматирование HTML (parse_mode=HTML): допустимы <b>, <i>, <a href="...">.

DEFAULT_SETTINGS: dict[str, str] = {
    "welcome_text": (
        "Приветствую тебя, воин!\n\n"
        "Этот бот поможет тебе сделать решающий шаг и вступить в закрытое братство "
        "мужчин — <b>THE MAIN SCHOOL</b>\n\n"
        "<b>Почему тебе это нужно?</b> — <a href=\"https://example.com\">рассказал тут</a>.\n\n"
        "Внутри тебя ждёт пробуждающая доза мужества, после которой ещё никто "
        "не уходил без результата.\n\n"
        "<b>Готов вступить к нам?</b> — выбирай ниже тарифный план 👇\n\n"
        "Мы ждём тебя.\n"
        "С уважением, Main 🐘"
    ),
    "tariffs_intro": "Выбери свой тарифный план 👇",
    "tariffs_disclaimer": (
        "Оплачивая любой тариф на подписку, вы подтверждаете согласие с офертой и "
        "автосписанием средств, согласно вашему тарифному плану.\n\n"
        "Автоплатёж можно выключить в любой момент в этом боте!"
    ),
    "offer_url": "https://graph.org/OFERTA-07-09-2",

    "tariff_card": (
        "Тариф: {title} {emoji}\n"
        "Стоимость: {price} RUB\n"
        "Срок действия: {duration}"
    ),

    "pay_intro": (
        "Оплата тарифа «{title}» — {price} RUB.\n\n"
        "⚠️ Демо-режим: реальная оплата ещё не подключена. "
        "Нажмите «✅ Я оплатил (тест)», чтобы проверить сценарий."
    ),

    "thanks_text": (
        "Спасибо, что с нами, воин! 🎉\n"
        "Добро пожаловать в THE MAIN SCHOOL."
    ),

    "my_sub_none": (
        "❌ У Вас нет действующей подписки.\n\n"
        "Ознакомьтесь с тарифами, нажав на кнопку ниже."
    ),
    "my_sub_active": (
        "✅ Ваша действующая подписка: {tariff} {emoji}\n\n"
        "Срок окончания подписки: {date}\n\n"
        "Автоплатёж: {autopay}"
    ),
    "autopay_on": "включен",
    "autopay_off": "отключен",
    "forever_date_label": "безлимитный",

    "stop_ar_confirm": (
        "Остановить автоматическое продление подписки?\n\n"
        "Доступ к ресурсам сохранится до конца оплаченного периода, "
        "но автосписания на новый срок не будет."
    ),
    "stop_ar_done": (
        "Автопродление остановлено. ✅\n"
        "Доступ сохранится до конца оплаченного периода."
    ),

    "sub_expired": (
        "Срок вашей подписки истёк. Доступ к ресурсам закрыт. 🔒\n"
        "Чтобы продлить — откройте «💰 Тарифы»."
    ),
    "sub_renewed": "Подписка автоматически продлена ✅\nАктивна до: {date} (МСК)",

    "privatka_text": (
        "<b>ЧТО ТЫ ПОЛУЧИШЬ В THE MAIN SCHOOL?</b>\n\n"
        "Никакой бесполезной теории. Только то, что работает на практике.\n\n"
        "🐘 <b>База знаний</b>\n\n"
        "Здесь ты найдёшь материалы о том, как:\n\n"
        "• знакомиться с девушками, выстраивать отношения и получать секс;\n"
        "• понимать женскую и мужскую психологию;\n"
        "• развить уверенность, самооценку и внутреннюю опору;\n"
        "• сформировать мужской стержень и чувство собственной ценности;\n"
        "• найти дисциплину, мотивацию и смысл двигаться вперёд;\n"
        "• привести в порядок здоровье, тело и стиль;\n"
        "• использовать практические техники общения и влияния.\n\n"
        "🤝 <b>Закрытое комьюнити</b>\n\n"
        "Одному меняться тяжело.\n\n"
        "Поэтому внутри тебя ждёт мужское окружение, которое помогает становиться сильнее.\n\n"
        "• Общение с единомышленниками.\n"
        "• Поддержка и обмен опытом.\n"
        "• Советы и разборы реальных жизненных ситуаций.\n"
        "• Возможность найти друзей и настоящее братство.\n\n"
        "❤️‍🔥 <b>Форум и обратная связь</b>\n\n"
        "Ты не останешься один на один со своими вопросами.\n\n"
        "• Разборы твоих личных ситуаций.\n"
        "• Ответы на вопросы.\n"
        "• Эксклюзивные материалы, которых нет в открытом доступе.\n\n"
        "—Что изменится в тебе—\n\n"
        "После вступления в приватку ты:\n\n"
        "• перестанешь бояться знакомиться и проявлять инициативу;\n"
        "• научишься строить здоровые отношения;\n"
        "• станешь увереннее, спокойнее и устойчивее;\n"
        "• начнёшь уважать себя и меньше зависеть от чужого мнения;\n"
        "• обретёшь окружение, которое будет мотивировать тебя становиться лучше.\n\n"
        "<b>THE MAIN SCHOOL</b> — вступи и измени свою жизнь по цене кириешек."
    ),

    # Подарочная подписка
    "gift_intro": (
        "🎁 Подарочная подписка «{title}» — {price} RUB.\n\n"
        "После оплаты вы получите одноразовую ссылку — отправьте её другу, "
        "и он активирует доступ."
    ),
    "gift_paid": (
        "🎁 Подарок оплачен!\n\n"
        "Отправьте другу эту одноразовую ссылку — по ней он активирует тариф «{title}»:\n"
        "{link}"
    ),
    "gift_redeem_success": (
        "🎁 Вам подарили подписку «{title}»!\n\n"
        "Подписка активна до: {date} (МСК)."
    ),
    "gift_redeem_forever": (
        "🎁 Вам подарили подписку «{title}»!\n\n"
        "У вас теперь вечный доступ ♾️"
    ),
    "gift_invalid": "Ссылка-подарок недействительна или уже использована.",

    "fallback": "Выбери действие в меню ниже 👇",
    "resource_soon": "Ссылка появится после настройки ресурсов.",
    "access_blocked": "Доступ к ресурсам ограничен. Если это ошибка — напишите в поддержку.",
}

# Метаданные для админки: подпись, многострочный ли, подсказка о плейсхолдерах.
SETTINGS_META: list[tuple[str, str, bool, str]] = [
    ("welcome_text", "Приветствие /start", True, "HTML. Ссылка: <a href=\"URL\">текст</a>. Кастом-эмодзи: <tg-emoji emoji-id=\"ID\">🔥</tg-emoji>."),
    ("tariffs_intro", "Заголовок экрана «Тарифы»", False, ""),
    ("tariffs_disclaimer", "Дисклеймер на экране тарифов", True, ""),
    ("offer_url", "Ссылка на оферту", False, ""),
    ("tariff_card", "Карточка тарифа", True, "Плейсхолдеры: {title} {emoji} {price} {duration}"),
    ("pay_intro", "Экран оплаты (демо)", True, "Плейсхолдеры: {title} {price}"),
    ("thanks_text", "Благодарность после оплаты", True, ""),
    ("my_sub_none", "«Моя подписка»: нет подписки", True, ""),
    ("my_sub_active", "«Моя подписка»: активна", True, "Плейсхолдеры: {tariff} {emoji} {date} {autopay}"),
    ("autopay_on", "Автоплатёж: подпись «включен»", False, ""),
    ("autopay_off", "Автоплатёж: подпись «отключен»", False, ""),
    ("forever_date_label", "Подпись срока для «Навсегда»", False, ""),
    ("stop_ar_confirm", "Остановка автопродления: вопрос", True, ""),
    ("stop_ar_done", "Остановка автопродления: подтверждение", True, ""),
    ("sub_expired", "Уведомление об окончании подписки", True, ""),
    ("sub_renewed", "Уведомление об автопродлении", True, "Плейсхолдер: {date}"),
    ("privatka_text", "Подробнее о приватке", True, "HTML."),
    ("gift_intro", "Подарок: экран оплаты", True, "Плейсхолдеры: {title} {price}"),
    ("gift_paid", "Подарок: после оплаты (ссылка)", True, "Плейсхолдеры: {title} {link}"),
    ("gift_redeem_success", "Подарок: активирован (срочный)", True, "Плейсхолдеры: {title} {date}"),
    ("gift_redeem_forever", "Подарок: активирован (навсегда)", True, "Плейсхолдер: {title}"),
    ("gift_invalid", "Подарок: недействительная ссылка", True, ""),
    ("fallback", "Ответ на непонятную команду", False, ""),
    ("resource_soon", "Ресурс ещё не настроен", False, ""),
    ("access_blocked", "Сообщение заблокированному пользователю", True, ""),
]

# Сообщения, к которым можно прикрепить картинку (управляется загрузкой в админке).
IMAGE_MSGS: dict[str, str] = {
    "welcome_text": "Приветствие (/start)",
    "tariffs_intro": "Экран «Тарифы»",
    "privatka_text": "Подробнее о приватке",
    "thanks_text": "После оплаты",
}


def image_setting_key(msg_key: str) -> str:
    return f"img::{msg_key}"


DEFAULT_TARIFFS: list[tuple[str, str, str, int, int | None, int]] = [
    # code, title, emoji, price_rub, months, sort_order
    ("1m", "1 месяц", "🫖", 1190, 1, 10),
    ("3m", "3 месяца", "⚡️", 2990, 3, 20),
    ("6m", "6 месяцев", "💪", 5790, 6, 30),
    ("12m", "12 месяцев", "🏆", 11090, 12, 40),
    ("forever", "Навсегда", "🎖", 13090, None, 50),
]


# ------------------------- DTO -------------------------

@dataclass
class TariffDTO:
    code: str
    title: str
    emoji: str
    price_rub: int
    months: int | None
    sort_order: int
    is_active: bool

    @property
    def is_forever(self) -> bool:
        return self.months is None


def _dto(t: Tariff) -> TariffDTO:
    return TariffDTO(t.code, t.title, t.emoji, t.price_rub, t.months, t.sort_order, t.is_active)


# ------------------------- безопасное форматирование -------------------------

class _SafeDict(dict):
    def __missing__(self, key):  # noqa: D401
        return "{" + key + "}"


def safe_format(template: str, **kwargs) -> str:
    """Форматирует шаблон, не падая, если админ убрал/сломал плейсхолдер."""
    try:
        return template.format_map(_SafeDict(kwargs))
    except Exception:  # noqa: BLE001
        return template


# ------------------------- тексты -------------------------

async def get_setting(key: str) -> str:
    sm = get_sessionmaker()
    async with sm() as s:
        row = await s.get(Setting, key)
        if row is not None:
            return row.value
    return DEFAULT_SETTINGS.get(key, "")


async def get_text(key: str, **kwargs) -> str:
    return safe_format(await get_setting(key), **kwargs)


async def set_setting(key: str, value: str) -> None:
    sm = get_sessionmaker()
    async with sm() as s:
        row = await s.get(Setting, key)
        if row is None:
            s.add(Setting(key=key, value=value))
        else:
            row.value = value
        await s.commit()


async def get_all_settings() -> dict[str, str]:
    """Все известные настройки: значение из БД либо дефолт."""
    sm = get_sessionmaker()
    async with sm() as s:
        res = await s.execute(select(Setting))
        overrides = {r.key: r.value for r in res.scalars()}
    return {key: overrides.get(key, default) for key, default in DEFAULT_SETTINGS.items()}


# ------------------------- тарифы -------------------------

async def get_tariffs(active_only: bool = True) -> list[TariffDTO]:
    sm = get_sessionmaker()
    async with sm() as s:
        stmt = select(Tariff)
        if active_only:
            stmt = stmt.where(Tariff.is_active.is_(True))
        stmt = stmt.order_by(Tariff.sort_order, Tariff.price_rub)
        res = await s.execute(stmt)
        return [_dto(t) for t in res.scalars()]


async def get_tariff(code: str) -> TariffDTO | None:
    sm = get_sessionmaker()
    async with sm() as s:
        t = await s.get(Tariff, code)
        return _dto(t) if t else None


async def upsert_tariff(
    code: str, title: str, emoji: str, price_rub: int,
    months: int | None, sort_order: int, is_active: bool,
) -> None:
    sm = get_sessionmaker()
    async with sm() as s:
        t = await s.get(Tariff, code)
        if t is None:
            s.add(Tariff(code=code, title=title, emoji=emoji, price_rub=price_rub,
                         months=months, sort_order=sort_order, is_active=is_active))
        else:
            t.title, t.emoji, t.price_rub = title, emoji, price_rub
            t.months, t.sort_order, t.is_active = months, sort_order, is_active
        await s.commit()


async def delete_tariff(code: str) -> None:
    sm = get_sessionmaker()
    async with sm() as s:
        t = await s.get(Tariff, code)
        if t is not None:
            await s.delete(t)
            await s.commit()


# ------------------------- сидирование -------------------------

async def seed_defaults() -> None:
    """Заполняет тексты и тарифы значениями по умолчанию при первом запуске."""
    sm = get_sessionmaker()
    async with sm() as s:
        res = await s.execute(select(Setting.key))
        existing_keys = {row[0] for row in res.all()}
        for key, value in DEFAULT_SETTINGS.items():
            if key not in existing_keys:
                s.add(Setting(key=key, value=value))

        res = await s.execute(select(Tariff.code))
        existing_codes = {row[0] for row in res.all()}
        for code, title, emoji, price, months, order in DEFAULT_TARIFFS:
            if code not in existing_codes:
                s.add(Tariff(code=code, title=title, emoji=emoji, price_rub=price,
                             months=months, sort_order=order, is_active=True))
        await s.commit()
