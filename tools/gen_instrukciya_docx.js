const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  AlignmentType, LevelFormat, HeadingLevel, BorderStyle, WidthType,
  ShadingType, PageBreak,
} = require("docx");

const CONTENT_W = 9026; // A4, поля 1"
const BLUE = "1F5C8B";
const LIGHT = "EAF2F8";
const WARN = "FDECEA";
const WARN_BORDER = "E74C3C";
const OK = "EAF7EE";

const run = (text, opts = {}) => new TextRun({ text, ...opts });
const b = (text, opts = {}) => new TextRun({ text, bold: true, ...opts });

const p = (children, opts = {}) =>
  new Paragraph({
    spacing: { after: 120, line: 276 },
    children: Array.isArray(children) ? children : [run(children)],
    ...opts,
  });

const h1 = (text) =>
  new Paragraph({ heading: HeadingLevel.HEADING_1, children: [run(text)] });
const h2 = (text) =>
  new Paragraph({ heading: HeadingLevel.HEADING_2, children: [run(text)] });

const bullet = (children, level = 0) =>
  new Paragraph({
    numbering: { reference: "bul", level },
    spacing: { after: 80, line: 276 },
    children: Array.isArray(children) ? children : [run(children)],
  });

const num = (ref, children) =>
  new Paragraph({
    numbering: { reference: ref, level: 0 },
    spacing: { after: 80, line: 276 },
    children: Array.isArray(children) ? children : [run(children)],
  });

const callout = (children, fill /*, border */) =>
  new Paragraph({
    shading: { type: ShadingType.CLEAR, fill },
    indent: { left: 120, right: 120 },
    spacing: { before: 140, after: 180, line: 276 },
    children: Array.isArray(children) ? children : [run(children)],
  });

// ---- таблицы ----
const cellBorder = { style: BorderStyle.SINGLE, size: 1, color: "CCCCCC" };
const borders = { top: cellBorder, left: cellBorder, bottom: cellBorder, right: cellBorder };
const cell = (text, w, { header = false, bold = false } = {}) =>
  new TableCell({
    borders,
    width: { size: w, type: WidthType.DXA },
    shading: header ? { fill: BLUE, type: ShadingType.CLEAR } : { fill: "FFFFFF", type: ShadingType.CLEAR },
    margins: { top: 60, bottom: 60, left: 120, right: 120 },
    children: [
      new Paragraph({
        spacing: { after: 0, line: 260 },
        children: [run(text, { bold: header || bold, color: header ? "FFFFFF" : "000000" })],
      }),
    ],
  });
const table = (widths, rows) =>
  new Table({
    width: { size: widths.reduce((a, c) => a + c, 0), type: WidthType.DXA },
    columnWidths: widths,
    rows: rows.map(
      (r, i) =>
        new TableRow({
          tableHeader: i === 0,
          children: r.map((txt, j) => cell(txt, widths[j], { header: i === 0 })),
        })
    ),
  });

const spacer = () => new Paragraph({ spacing: { after: 60 }, children: [run("")] });

const children = [];

// ===== Титул =====
children.push(
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { after: 60 },
    children: [run("Инструкция для заказчика", { bold: true, size: 40, color: BLUE })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { after: 40 },
    children: [run("Бот, приём оплат (ЮKassa) и хостинг (Beget)", { bold: true, size: 30 })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { after: 200 },
    children: [run("Проект THE MAIN", { italics: true, color: "666666" })],
  }),
  p([
    run("Выполняйте по шагам. Всё, что помечено значком "),
    b("📤"),
    run(" — нужно прислать разработчику. Технической настройкой занимается разработчик; от вас — регистрация, доступы и данные."),
  ]),
  callout(
    [b("Порядок действий: "), run("1) создать бота  →  2) подключить ЮKassa  →  3) купить хостинг (Beget).")],
    LIGHT, BLUE
  )
);

// ===== Часть 1. Бот =====
children.push(
  h1("Часть 1. Создание бота в Telegram (BotFather)"),
  p([run("Бот создаётся на "), b("вашем"), run(" аккаунте Telegram — так он полностью принадлежит вам.")]),
  num("botf", [run("В поиске Telegram найдите "), b("@BotFather"), run(" (с синей галочкой — официальный бот).")]),
  num("botf", [run("Откройте его и нажмите "), b("«Запустить» / Start"), run(".")]),
  num("botf", [run("Отправьте команду "), b("/newbot"), run(".")]),
  num("botf", [run("Введите "), b("имя бота"), run(" — отображаемое название, любое (например: THE MAIN).")]),
  num("botf", [run("Введите "), b("username бота"), run(" — латиницей, обязательно заканчивается на "), b("bot"), run(" (например: themain_access_bot). Если занято — придумайте другой.")]),
  num("botf", [run("BotFather пришлёт "), b("токен"), run(" — длинную строку вида 123456789:AAE-xxxxxxxxxxxxxxxxxxxx.")]),
  callout(
    [b("⚠️ Токен = полный доступ к боту."), run(" Никому не показывайте. Если случайно утёк — перевыпустите командой /token.")],
    WARN, WARN_BORDER
  ),
  p([b("📤 Прислать разработчику: "), run("username бота и токен (токен — "), b("безопасным способом"), run(", не в общем чате: отдельным личным сообщением или архивом с паролем).")]),
  p([run("Аватар, описание и меню команд настроит разработчик — вам об этом думать не нужно.", { italics: true, color: "666666" })]),
);

// ===== Часть 2. ЮKassa =====
children.push(
  new Paragraph({ pageBreakBefore: true, heading: HeadingLevel.HEADING_1, children: [run("Часть 2. Приём оплат — ЮKassa")] }),

  h2("2.0. Важно: не перепутать сервисы"),
  bullet([run("Нужна именно "), b("ЮKassa"), run(" (сайт "), b("yookassa.ru"), run(") — приём платежей для бизнеса: карты, СБП, автосписания, чеки 54-ФЗ.")]),
  bullet([b("ЮMoney"), run(" (кошелёк) — это другое, не подойдёт.")]),
  bullet([run("Оформляем на ваше "), b("ИП"), run(".")]),

  h2("2.1. Что подготовить заранее"),
  bullet("ИНН и ОГРНИП ИП."),
  bullet("Паспортные данные ИП."),
  bullet([b("Расчётный счёт ИП"), run(" в банке — на него будут приходить деньги.")]),
  bullet("Система налогообложения (например, «УСН доходы») — понадобится для чеков."),
  bullet("Телефон и e-mail."),

  h2("2.2. Регистрация в ЮKassa (шаги)"),
  num("ykreg", [run("Зайдите на "), b("yookassa.ru"), run(" → «Подключить» (регистрация для бизнеса).")]),
  num("ykreg", "Войдите или создайте аккаунт (Яндекс ID / Ю ID)."),
  num("ykreg", [run("Выберите тип — "), b("ИП"), run(", заполните данные ИП и реквизиты расчётного счёта.")]),
  num("ykreg", [run("Создайте "), b("магазин"), run(" (точка приёма оплат). Тип приёма — "), b("«Через API»"), run(" (для Telegram-бота). Если попросят сайт и описание — см. п. 2.3.")]),
  num("ykreg", [run("Подпишите оферту/договор (электронно) и отправьте на проверку. Обычно "), b("1–3 рабочих дня"), run(".")]),
  callout([b("💡 "), run("Пока идёт модерация, разработчик уже начинает интеграцию на тестовом магазине (тестовые ключи доступны сразу) — время не теряется.")], OK, "27AE60"),

  h2("2.3. Возможное требование модерации — простой сайт"),
  p([run("ЮKassa при проверке часто просит "), b("сайт/страницу"), run(" с описанием услуги, "), b("офертой"), run(", "), b("политикой конфиденциальности"), run(" и контактами. У бота своего сайта нет, поэтому:")]),
  bullet("либо у вас уже есть лендинг проекта — дайте его адрес;"),
  bullet("либо разработчик сделает простую страницу-оферту (1 страница) — сообщите, нужно ли."),
  bullet([run("Тексты "), b("оферты"), run(" (с условием автосписаний) и "), b("политики"), run(" — желательно от юриста.")]),

  h2("2.4. Настройка чеков (54-ФЗ)"),
  p("Чеки при оплате от физлиц обязательны. В кабинете ЮKassa:"),
  num("ykchk", [run("Раздел про "), b("чеки / онлайн-кассу"), run(". Если своей кассы нет — подключите кассовое решение через ЮKassa (часть — платная, небольшая сумма).")]),
  num("ykchk", [run("Укажите "), b("систему налогообложения"), run(" и "), b("ставку НДС"), run(" (для УСН обычно «без НДС»).")]),
  num("ykchk", [b("📤 "), run("Пришлите разработчику, какие поставили систему налогообложения и ставку НДС (или дайте доступ, чтобы он настроил чеки).")]),

  h2("2.5. Включить способы оплаты и автосписания"),
  bullet([b("Банковские карты"), run(" — обычно включены по умолчанию.")]),
  bullet([b("СБП"), run(" — при необходимости активируйте отдельно (комиссия ниже, чем по картам).")]),
  bullet([b("Автоплатежи / сохранение способа оплаты"), run(" — нужно для автопродления. Если опции нет — напишите в поддержку ЮKassa: «включите сохранение платёжного метода для рекуррентных платежей».")]),

  h2("2.6. Ключи для разработчика (самое важное)"),
  num("ykkey", [run("Раздел "), b("«Настройки» → «API-ключи»"), run(" (или «Интеграция»).")]),
  num("ykkey", [run("Возьмите "), b("shopId"), run(" (идентификатор магазина) и создайте "), b("секретный ключ"), run(" — и боевой, и тестовый.")]),
  num("ykkey", [b("⚠️ Секретный ключ = доступ к деньгам."), run(" Пришлите отдельно и безопасно (личным сообщением / архивом с паролем). Ключ всегда можно перевыпустить.")]),
  num("ykkey", [b("Webhook"), run(" (уведомления об оплате): разработчик даст адрес вида https://ваш-домен/yookassa — его указывают в кабинете ЮKassa (или дайте доступ разработчику).")]),

  callout([b("📤 Итог по ЮKassa — прислать разработчику:")], LIGHT, BLUE),
  bullet("shopId (тест и боевой)."),
  bullet("Секретный API-ключ (тест и боевой) — безопасно."),
  bullet("Параметры чека: система налогообложения + ставка НДС (или доступ в кабинет)."),
  bullet("Подтверждение: карты, СБП, автоплатежи включены."),
  bullet("Решение по странице-оферте (есть сайт / делаем страницу)."),
);

// ===== Часть 3. Хостинг =====
children.push(
  new Paragraph({ pageBreakBefore: true, heading: HeadingLevel.HEADING_1, children: [run("Часть 3. Хостинг — Beget")] }),
  p([run("Бот и база данных работают круглосуточно на сервере. Под вашу нагрузку ("), b("1 000–2 000 пользователей в месяц"), run(") нужен небольшой сервер — это недорого.")]),

  callout(
    [b("⚠️ Берём VPS, а НЕ «виртуальный хостинг».  "), run("У Beget два разных продукта: «Виртуальный хостинг» — для обычных сайтов, боту НЕ подойдёт. Нужен «VPS» / «Облачные серверы» — берите именно его.")],
    WARN, WARN_BORDER
  ),

  h2("3.1. Что берём (конфигурация)"),
  table([2600, 6426], [
    ["Параметр", "Значение"],
    ["Продукт", "VPS / Облачный сервер (не виртуальный хостинг)"],
    ["ОС", "Ubuntu 24.04"],
    ["CPU", "2 ядра (2 vCPU)"],
    ["RAM", "4 ГБ"],
    ["Диск", "30–50 ГБ NVMe/SSD"],
    ["Регион", "Россия"],
    ["Бэкапы", "Включить, если есть опция"],
  ]),
  spacer(),

  h2("3.2. Как купить VPS на Beget (шаги)"),
  num("beg", [run("Зайдите на "), b("beget.com"), run(" и зарегистрируйтесь "), b("на свои данные"), run(" (аккаунт должен быть ваш).")]),
  num("beg", [run("В панели откройте раздел "), b("«VPS»"), run(" (облачные серверы). "), b("Не «Виртуальный хостинг».")]),
  num("beg", [run("Нажмите "), b("«Создать / Заказать сервер»"), run(".")]),
  num("beg", [run("ОС — "), b("Ubuntu 24.04"), run(".")]),
  num("beg", [run("Конфигурация: "), b("2 CPU, 4 ГБ RAM, 30–50 ГБ NVMe"), run(".")]),
  num("beg", [run("Включите "), b("резервные копии"), run(", если есть опция.")]),
  num("beg", "Оплатите (пополните баланс)."),
  num("beg", [run("После создания у сервера появятся "), b("IP-адрес"), run(" и данные доступа (root-пароль).")]),

  h2("3.3. Домен (нужен для безопасной оплаты)"),
  p("Уведомления об оплате приходят по защищённому адресу (HTTPS), поэтому нужен домен. Удобно купить его там же, на Beget — всё в одной панели:"),
  num("dom", [run("В панели Beget откройте "), b("«Домены»"), run(" и зарегистрируйте домен "), b(".ru"), run(" (например, themain-bot.ru).")]),
  num("dom", [run("Управление DNS — в той же панели. Разработчику нужен доступ к DNS (или вы добавите одну запись по его инструкции). Сертификат HTTPS настроит разработчик "), b("бесплатно"), run(".")]),

  h2("3.4. Примерная стоимость"),
  table([4513, 4513], [
    ["Статья", "Стоимость"],
    ["VPS (2 CPU / 4 ГБ)", "≈ 700–1 000 ₽ / мес"],
    ["Домен .ru", "≈ 200–600 ₽ / год"],
    ["Итого", "≈ до 1 000 ₽ / мес"],
  ]),
  spacer(),

  callout([b("📤 Итог по хостингу — прислать разработчику:")], LIGHT, BLUE),
  bullet("IP-адрес сервера."),
  bullet("Доступ к серверу: root-пароль или добавить SSH-ключ разработчика (он пришлёт ключ)."),
  bullet("Имя домена + доступ к DNS в панели Beget (или готовность добавить запись по инструкции)."),
);

// ===== Часть 4. Владение =====
children.push(
  h1("Часть 4. Кому что принадлежит"),
  p("Всё регистрируйте на себя — тогда проект полностью ваш:"),
  bullet([b("Бот"), run(" (@BotFather) — на вашем аккаунте Telegram; разработчику — только токен.")]),
  bullet([b("Кабинет ЮKassa"), run(" — на ваше ИП (деньги идут напрямую вам).")]),
  bullet([b("Сервер и домен"), run(" (Beget) — на ваш аккаунт и вашу оплату.")]),
  bullet([b("Все пароли/ключи"), run(" — у вас; разработчику — рабочие копии для настройки и поддержки.")]),
);

// ===== Финальный чек-лист =====
children.push(
  new Paragraph({ pageBreakBefore: true, heading: HeadingLevel.HEADING_1, children: [run("Финальный чек-лист: что прислать разработчику")] }),
  num("fin", [b("Бот: "), run("username + токен (безопасно).")]),
  num("fin", [b("ЮKassa: "), run("shopId + секретный ключ, тест и боевой (безопасно).")]),
  num("fin", [b("ЮKassa: "), run("параметры чека (СНО + ставка НДС) или доступ в кабинет.")]),
  num("fin", [b("ЮKassa: "), run("подтверждение — карты, СБП, автоплатежи включены.")]),
  num("fin", [b("Beget: "), run("IP сервера + доступ (root-пароль или SSH-ключ).")]),
  num("fin", [b("Beget: "), run("домен + доступ к DNS.")]),
  num("fin", "Решение по странице-оферте (есть сайт / делаем страницу)."),
  num("fin", "(когда будут) тексты оферты и политики конфиденциальности."),
);

const numberedRefs = ["botf", "ykreg", "ykchk", "ykkey", "beg", "dom", "fin"];
const numberingConfig = [
  {
    reference: "bul",
    levels: [
      { level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 620, hanging: 320 } } } },
      { level: 1, format: LevelFormat.BULLET, text: "◦", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 1200, hanging: 320 } } } },
    ],
  },
  ...numberedRefs.map((reference) => ({
    reference,
    levels: [
      { level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 620, hanging: 320 } } } },
    ],
  })),
];

const doc = new Document({
  styles: {
    default: { document: { run: { font: "Arial", size: 22 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 30, bold: true, font: "Arial", color: BLUE },
        paragraph: { spacing: { before: 300, after: 160 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 25, bold: true, font: "Arial", color: "000000" },
        paragraph: { spacing: { before: 200, after: 100 }, outlineLevel: 1 } },
    ],
  },
  numbering: { config: numberingConfig },
  sections: [
    {
      properties: {
        page: {
          size: { width: 11906, height: 16838 },
          margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 },
        },
      },
      children,
    },
  ],
});

Packer.toBuffer(doc).then((buffer) => {
  fs.writeFileSync("/Users/rambletot/Bot/Инструкция_заказчику_ЮKassa_и_хостинг.docx", buffer);
  console.log("OK: docx written");
});
