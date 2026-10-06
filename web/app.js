/* Модель 850 — витрина выпуска (контракт — поле `schema` выпуска).
 *
 * Правила витрины (они же — предмет tests/test_screens*.py):
 *  • Числа и слова компании — только из выпуска `/api/model`: имя и тикер —
 *    `meta.company`, единица периода — `meta.period_unit` (модель) и
 *    `nowcast.period_unit` (ближайший отчёт). Своих формул у витрины нет.
 *    Единственный пересчёт назван книгой (A-P1c, A-V9): ползунок λ двигает точку
 *    (низ + λ·(верх − низ)) и полосу — из низа и верха каждого прогона выпуска
 *    (`headline.low_draws` / `high_draws`), квантиль тип 7, шаг печати выпуска.
 *    При λ книги печатается выпуск как есть.
 *  • Цена — внутренняя стоимость: (V0 − D)·(1 − g) на акцию (`fair_value.method`).
 *  • Блока нет в выпуске — карточка так и говорит; ничего не выдумывается.
 *  • Один экран упал — остальные живут: `render` ловит исключение.
 *  • Графики рисуются в настоящих пикселях ширины своей карточки
 *    (ResizeObserver), поэтому подпись 12 px — это 12 px на любом экране.
 *  • Текст всегда в токенах текста; цвет ряда — только у отметок.
 *  • Главные графики можно открыть таблицей (кнопка «Таблица»); малые — там,
 *    где те же числа уже стоят рядом таблицей или плитками.
 */
"use strict";

const API = "/api/model";

const STALE_HOURS = 96;

const SVG_NS = "http://www.w3.org/2000/svg";

let DATA = null;          // выпуск целиком

let LAMBDA = null;        // null — λ книги; число — положение ползунка

let CURRENT = "overview"; // открытый экран

/* ── DOM ── */

function el(tag, attrs, ...kids) {
  const node = document.createElement(tag);
  setAttrs(node, attrs);
  append(node, kids);
  return node;
}

function sv(tag, attrs, ...kids) {
  const node = document.createElementNS(SVG_NS, tag);
  setAttrs(node, attrs);
  append(node, kids);
  return node;
}

function setAttrs(node, attrs) {
  if (!attrs) return;
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "text") node.textContent = value;
    else if (key === "on") for (const [ev, fn] of Object.entries(value)) node.addEventListener(ev, fn);
    else if (key === "tip") setTip(node, value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
}

function append(node, kids) {
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false || kid === "") continue;
    node.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
}

const $ = (selector, root = document) => root.querySelector(selector);

// Точка в конце фразы — если её там ещё нет («п.п.» уже кончается точкой).
function sentence(textContent) {
  return /[.!?…]$/.test(textContent) ? textContent : textContent + ".";
}

// Форма слова при числе: 1 мир, 3 мира, 36 клеток.
function plural(n, [one, few, many]) {
  const a = Math.abs(Math.trunc(n)) % 100, b = a % 10;
  return a > 10 && a < 20 ? many : b === 1 ? one : b >= 2 && b <= 4 ? few : many;
}

const isNum = (x) => typeof x === "number" && Number.isFinite(x);

const cls = (...names) => names.filter(Boolean).join(" ") || null;

/* ── числа по-русски ── */

// Разряды — узкий неразрывный пробел, дробь — запятая, минус — настоящий минус.
const NBSP = "\u00a0";

const THIN = "\u202f";

const MINUS = "\u2212";

const FORMATS = new Map();

function numberFormat(digits) {
  if (!FORMATS.has(digits)) {
    FORMATS.set(digits, new Intl.NumberFormat("ru-RU", {
      minimumFractionDigits: digits, maximumFractionDigits: digits, useGrouping: true }));
  }
  return FORMATS.get(digits);
}

const TIME_FORMAT = new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit" });

const MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
  "сентября", "октября", "ноября", "декабря"];

const MONTHS_SHORT = ["янв.", "февр.", "марта", "апр.", "мая", "июня", "июля", "авг.",
  "сент.", "окт.", "нояб.", "дек."];

function parseDay(iso) {
  // Дата без времени — календарный день, а не полночь по Гринвичу: иначе
  // зритель к западу от Гринвича увидел бы вчерашнее число.
  if (typeof iso !== "string") return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (m) return new Date(+m[1], +m[2] - 1, +m[3]);
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : new Date(t);
}

const fmt = {
  num(x, digits = 0) {
    if (!isNum(x)) return "—";
    const scale = 10 ** digits;
    const rounded = Math.round(x * scale) / scale;
    const text = numberFormat(digits).format(rounded === 0 ? 0 : rounded);
    return text.replace(/-/g, MINUS).replace(/[\u00a0\u202f ]/g, THIN);
  },
  signed(x, digits = 0) {
    if (!isNum(x)) return "—";
    const text = fmt.num(x, digits);
    return x > 0 && text !== fmt.num(0, digits) ? "+" + text : text;
  },
  rub(x, digits = 0) { return isNum(x) ? fmt.num(x, digits) + THIN + "₽" : "—"; },
  signedRub(x, digits = 0) { return isNum(x) ? fmt.signed(x, digits) + THIN + "₽" : "—"; },
  bn(x, digits = 1) { return isNum(x) ? fmt.num(x, digits) + NBSP + "млрд" + NBSP + "₽" : "—"; },
  pct(share, digits = 1) { return isNum(share) ? fmt.num(share * 100, digits) + THIN + "%" : "—"; },
  signedPct(share, digits = 1) { return isNum(share) ? fmt.signed(share * 100, digits) + THIN + "%" : "—"; },
  pp(share, digits = 2) { return isNum(share) ? fmt.signed(share * 100, digits) + NBSP + "п.п." : "—"; },
  x(multiple, digits = 2) { return isNum(multiple) ? fmt.num(multiple, digits) + "×" : "—"; },
  // Переменная дня здесь не `d`: `d.` в этом файле — только выпуск (тест
  // контракта сверяет каждое `d.<блок>` с объявленными блоками).
  date(iso) {
    const day = parseDay(iso);
    if (!day) return "—";
    return `${String(day.getDate()).padStart(2, "0")}.${String(day.getMonth() + 1).padStart(2, "0")}.${day.getFullYear()}`;
  },
  dateLong(iso) {
    const day = parseDay(iso);
    return day ? `${day.getDate()}${NBSP}${MONTHS[day.getMonth()]} ${day.getFullYear()}` : "—";
  },
  dateShort(iso) {
    const day = parseDay(iso);
    return day ? `${day.getDate()}${NBSP}${MONTHS_SHORT[day.getMonth()]}` : "—";
  },
  monthYear(iso) {
    const day = parseDay(iso);
    const names = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август",
      "сентябрь", "октябрь", "ноябрь", "декабрь"];
    return day ? `${names[day.getMonth()]} ${day.getFullYear()}` : "—";
  },
  // Время — через Intl: часы зрителя читает только правило темы в index.html.
  time(iso) {
    const day = parseDay(iso);
    return day ? TIME_FORMAT.format(day) : "—";
  },
  days(n) { return isNum(n) ? `${fmt.num(n)}${NBSP}${plural(n, ["день", "дня", "дней"])}` : "—"; },
  // Число суждения книги в том виде, в каком его пишет выпуск («0.55», «сдвиг -0.004»): точка →
  // запятая, сдвиг траектории — в пунктах, минус — настоящий.
  bookValue(text, unit) {
    if (text === null || text === undefined) return "—";
    if (isNum(text)) return formatByUnit(text, unit);
    const s = String(text).trim();
    const shift = /^сдвиг ([+-]?\d*\.?\d+)$/.exec(s);
    if (shift) return "сдвиг " + fmt.pp(Number(shift[1]), Math.abs(Number(shift[1])) < 0.001 ? 2 : 1);
    if (/^[+-]?\d*\.?\d+$/.test(s)) return formatByUnit(Number(s), unit);
    return s.replace(/(\d)\.(\d)/g, "$1,$2").replace(/(\d)%/g, "$1" + THIN + "%").replace(/-(?=\d)/g, MINUS);
  },
};

// Какого рода число у суждения книги — единица из выпуска (`unit`: pct, bn, bn_per_m2, times,
// years, plain).
function formatByUnit(value, unit) {
  const kind = unit || "plain";
  const decimals = (v) => {
    const s = String(v);
    const i = s.indexOf(".");
    return i < 0 ? 0 : Math.min(4, s.length - i - 1);
  };
  if (kind === "pct") return fmt.num(value * 100, Math.max(0, decimals(value) - 2)) + THIN + "%";
  if (kind === "bn") return fmt.num(value, decimals(value)) + NBSP + "млрд" + NBSP + "₽";
  if (kind === "bn_per_m2") return fmt.num(value, decimals(value)) + NBSP + "млрд" + NBSP + "₽/тыс." + NBSP + "м²";
  if (kind === "times") return fmt.num(value, decimals(value)) + "×";
  if (kind === "years") return fmt.num(value, 0) + NBSP + (value === 1 ? "год" : value >= 2 && value <= 4 ? "года" : "лет");
  return fmt.num(value, decimals(value));
}

/* ── подсказки ── */

// Одна подсказка на страницу.
const TIPS = new WeakMap();

let tipOwner = null;

function setTip(node, content) {
  TIPS.set(node, content);
  node.setAttribute("data-tip", "");
  if (!node.hasAttribute("tabindex")) node.setAttribute("tabindex", "0");
}

function tipTarget(node) {
  while (node && node !== document) {
    if (node.nodeType === 1 && TIPS.has(node)) return node;
    node = node.parentNode;
  }
  return null;
}

function tipBody(content) {
  if (typeof content === "string") return [el("div", {}, content)];
  const out = [];
  if (content.title) out.push(el("div", { class: "tip-title" }, content.title));
  for (const [k, v] of content.rows || []) {
    out.push(el("div", { class: "tip-row" }, el("span", { class: "k" }, k), el("span", { class: "v" }, v)));
  }
  if (content.note) out.push(el("div", { class: "tip-note" }, content.note));
  return out;
}

function showTip(owner, x, y) {
  const box = $("#tip");
  if (!box) return;
  if (tipOwner && tipOwner !== owner) tipOwner.classList.remove("is-hot");
  tipOwner = owner;
  owner.classList.add("is-hot");
  box.replaceChildren(...tipBody(TIPS.get(owner)));
  box.hidden = false;
  placeTip(x, y);
}

function placeTip(x, y) {
  const box = $("#tip");
  if (!box || box.hidden) return;
  const pad = 12;
  const w = box.offsetWidth, h = box.offsetHeight;
  let left = x + 14, top = y - h - 12;
  if (left + w > innerWidth - pad) left = Math.max(pad, x - w - 14);
  if (top < pad) top = Math.min(innerHeight - h - pad, y + 18);
  box.style.left = `${left}px`;
  box.style.top = `${top}px`;
}

function hideTip() {
  const box = $("#tip");
  if (box) box.hidden = true;
  if (tipOwner) tipOwner.classList.remove("is-hot");
  tipOwner = null;
}

function wireTips() {
  document.addEventListener("pointerover", (e) => {
    const owner = tipTarget(e.target);
    if (owner) showTip(owner, e.clientX, e.clientY);
  });
  document.addEventListener("pointermove", (e) => { if (tipOwner) placeTip(e.clientX, e.clientY); });
  document.addEventListener("pointerout", (e) => {
    const owner = tipTarget(e.target);
    if (owner && !owner.contains(e.relatedTarget)) hideTip();
  });
  document.addEventListener("focusin", (e) => {
    const owner = tipTarget(e.target);
    if (!owner) return;
    const r = owner.getBoundingClientRect();
    showTip(owner, r.left + r.width / 2, r.top);
  });
  document.addEventListener("focusout", hideTip);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") hideTip(); });
  window.addEventListener("scroll", hideTip, { passive: true });
}

/* ── графики: основа ── */

// График — функция ширины: draw(width) → <svg>.
const DRAWS = new WeakMap();

const SIZE = typeof ResizeObserver === "function"
  ? new ResizeObserver((entries) => {
    for (const entry of entries) {
      const host = entry.target;
      const width = Math.floor(entry.contentRect.width);
      if (width > 0 && String(width) !== host.dataset.w) {
        host.dataset.w = width;
        paint(host);
      }
    }
  })
  : null;

function chart(draw, label) {
  const host = el("div", { class: "chart", "aria-label": label || null });
  DRAWS.set(host, draw);
  if (SIZE) SIZE.observe(host);
  else requestAnimationFrame(() => { host.dataset.w = host.clientWidth || 640; paint(host); });
  return host;
}

function paint(host) {
  const draw = DRAWS.get(host);
  const width = Number(host.dataset.w);
  if (!draw || !width) return;
  try {
    host.replaceChildren(draw(width));
  } catch (error) {
    console.error(error);
    host.replaceChildren(el("p", { class: "empty broken" }, `График не отрисовался: ${error.message}`));
  }
}

function repaint(host) { if (host && host.dataset.w) paint(host); }

function svgBox(width, height, label) {
  return sv("svg", { width, height, viewBox: `0 0 ${width} ${height}`, role: "img",
    "aria-label": label || null, focusable: "false" });
}

function scale(d0, d1, r0, r1) {
  const k = (r1 - r0) / ((d1 - d0) || 1);
  const f = (v) => r0 + (v - d0) * k;
  f.d = [d0, d1];
  f.r = [r0, r1];
  return f;
}

function niceStep(span, count) {
  const raw = Math.abs(span) / Math.max(1, count);
  if (!raw) return 1;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const r = raw / mag;
  return (r <= 1 ? 1 : r <= 2 ? 2 : r <= 5 ? 5 : 10) * mag;
}

function ticks(d0, d1, count) {
  const step = niceStep(d1 - d0, count);
  const out = [];
  for (let v = Math.ceil(d0 / step - 1e-9) * step; v <= d1 + step * 1e-6; v += step) {
    out.push(Number(v.toPrecision(12)));
  }
  return out;
}

// Ширина подписи — по тому же шрифту, которым её нарисует SVG: без замера
// подписи наезжали бы друг на друга на узкой карточке.
let MEASURE = null;

let FAMILY = null;

function textWidth(text, size = 12, weight = 500) {
  if (!MEASURE) {
    MEASURE = document.createElement("canvas").getContext("2d");
    FAMILY = getComputedStyle(document.body).fontFamily;
  }
  MEASURE.font = `${weight} ${size}px ${FAMILY}`;
  return MEASURE.measureText(String(text)).width;
}

function text(x, y, content, attrs = {}) {
  return sv("text", { x, y, ...attrs }, content);
}

function line(x1, y1, x2, y2, attrs = {}) {
  return sv("line", { x1, y1, x2, y2, ...attrs });
}

// Разводит подписи по вертикальным ярусам, чтобы они не наезжали: подпись
// встаёт в первый ярус, где слева от неё свободно. Возвращает номер яруса.
function stackLabels(items, gap = 8) {
  const rows = [];
  for (const item of items.sort((a, b) => a.x0 - b.x0)) {
    let row = rows.findIndex((right) => item.x0 >= right + gap);
    if (row < 0) { row = rows.length; rows.push(-Infinity); }
    rows[row] = item.x1;
    item.row = row;
  }
  return rows.length;
}

// Подпись с «ореолом» цвета карточки: читается поверх линий сетки и отметок.
function label(x, y, content, attrs = {}) {
  return text(x, y, content, { ...attrs, class: cls("halo", attrs.class || "label") });
}

/* ── компоненты ── */

function card(opts, ...body) {
  const { title, sub, link, tools, span = 12, extra, id } = opts || {};
  const head = (title || sub || tools || link)
    ? el("div", { class: "card-head" },
      el("div", {}, title ? el("h2", {}, title) : null, sub ? el("p", { class: "sub" }, sub) : null),
      tools || null,
      link ? el("a", { class: "card-link", href: `#${link[0]}` }, link[1]) : null)
    : null;
  return el("section", { class: cls("card", `span-${span}`, extra), id: id || null }, head, ...body);
}

function empty(message) {
  return el("p", { class: "empty" }, message);
}

function missing(block) {
  return empty(`В этом выпуске нет блока «${block}» — выпуск собран быстрой сборкой, без медленных блоков.`);
}

function badge(textContent, kind) {
  return el("span", { class: cls("badge", kind && `badge-${kind}`) }, textContent);
}

function detailsBlock(summary, body) {
  return el("details", {}, el("summary", {}, summary), el("div", { class: "detail-text" }, body));
}

// Таблица данных.
function dataTable(columns, rows, opts = {}) {
  const head = el("thead", {}, el("tr", {},
    columns.map((c) => el("th", { class: c.num ? "num" : null, scope: "col" }, c.title))));
  const body = el("tbody");
  for (const row of rows) {
    const detail = opts.detail ? opts.detail(row) : null;
    const tr = el("tr", { class: cls(opts.rowClass && opts.rowClass(row), detail && "has-detail") });
    for (const c of columns) {
      tr.append(el("td", { class: cls(c.num && "num", c.cls) }, c.value(row)));
    }
    body.append(tr);
    if (detail) body.append(el("tr", { class: "detail" }, el("td", { colspan: columns.length }, detail)));
  }
  return el("div", { class: "scroll" },
    el("table", { class: cls("data", opts.cls) }, opts.caption ? el("caption", {}, opts.caption) : null, head, body));
}

// График с «таблицей-двойником»: кнопка в шапке карточки меняет вид. Таблица
// строится лениво — из тех же чисел выпуска, что и график.
function withTable(chartNode, makeTable) {
  const box = el("div", { class: "fig" }, chartNode);
  let tableNode = null;
  const button = el("button", { class: "view-toggle", type: "button", "aria-pressed": "false" }, "Таблица");
  // Данные графика сменились (ползунок λ) — открытая таблица перестраивается,
  // закрытая строится заново при следующем открытии.
  const refresh = () => {
    const open = button.getAttribute("aria-pressed") === "true";
    if (tableNode) tableNode.remove();
    tableNode = open ? el("div", { class: "fig-table" }, makeTable()) : null;
    if (tableNode) box.append(tableNode);
  };
  button.addEventListener("click", () => {
    const open = button.getAttribute("aria-pressed") === "true";
    if (!open) {
      tableNode = tableNode || el("div", { class: "fig-table" }, makeTable());
      chartNode.hidden = true;
      box.append(tableNode);
      button.textContent = "График";
      button.setAttribute("aria-pressed", "true");
    } else {
      chartNode.hidden = false;
      if (tableNode) tableNode.remove();
      button.textContent = "Таблица";
      button.setAttribute("aria-pressed", "false");
    }
  });
  return { box, button, refresh };
}

function kpi(value, labelText, opts = {}) {
  return el("div", { class: "kpi", tip: opts.tip || null },
    el("div", { class: "kpi-value" }, value, opts.unit ? el("span", { class: "unit" }, " " + opts.unit) : null),
    el("div", { class: "kpi-label" }, labelText));
}

function legend(items) {
  return el("div", { class: "legend" }, items.map(([key, name]) =>
    el("span", {}, el("i", { class: cls("key", key) }), name)));
}

function screenHead(eyebrow, title, lede) {
  return el("header", { class: "screen-head" },
    eyebrow ? el("span", { class: "eyebrow" }, eyebrow) : null,
    el("h1", {}, title),
    lede ? el("p", {}, lede) : null);
}

// Выбор одного из нескольких (сценарий, режим): aria-pressed у кнопок.
function chooser(options, current, onPick, labelText) {
  const group = el("div", { class: "chooser", role: "group", "aria-label": labelText });
  for (const opt of options) {
    const button = el("button", { type: "button", "aria-pressed": String(opt.value === current) },
      opt.label, opt.hint ? el("span", { class: "w" }, opt.hint) : null);
    button.addEventListener("click", () => {
      for (const b of group.children) b.setAttribute("aria-pressed", String(b === button));
      onPick(opt.value);
    });
    group.append(button);
  }
  return group;
}

/* ── заголовок и λ ── */

// Квантиль тип 7 (линейная интерполяция), как `model.uncertainty.quantile`:
// другой тип дал бы на тех же прогонах другие числа, чем печатает выпуск.
function quantile7(sorted, q) {
  const n = sorted.length;
  if (!n) return NaN;
  const h = (n - 1) * q;
  const lo = Math.floor(h);
  const hi = Math.min(lo + 1, n - 1);
  return sorted[lo] + (h - lo) * (sorted[hi] - sorted[lo]);
}

// Округление печати — как в ядре: Python `round` округляет половину к чётному.
function roundHalfEven(x) {
  const r = Math.round(x);
  return Math.abs(x - Math.trunc(x)) === 0.5 ? 2 * Math.round(x / 2) : r;
}

function bookLambda(d) {
  const head = d.fair_value.headline;
  return isNum(head && head.own_macro_confidence) ? head.own_macro_confidence
    : isNum(d.fair_value.own_macro_confidence) ? d.fair_value.own_macro_confidence : 0.5;
}

function lambdaNow(d) { return LAMBDA === null ? bookLambda(d) : LAMBDA; }

function atBookLambda(d) { return LAMBDA === null || Math.abs(LAMBDA - bookLambda(d)) < 1e-9; }

// Центры прогонов при λ: низ + λ·(верх − низ) каждого прогона (книга 1.4, §5 п. 4).
function centresAt(head, lam) {
  const n = Math.min(head.low_draws.length, head.high_draws.length);
  const out = new Array(n);
  for (let i = 0; i < n; i++) {
    const lo = head.low_draws[i];
    out[i] = lo + lam * (head.high_draws[i] - lo);
  }
  return out;
}

// Медиана и полосы при λ. При λ книги — числа выпуска без пересчёта; иначе —
// правило книги на прогонах выпуска, шаг печати выпуска.
function headlineAt(head, lam) {
  if (lam === null || Math.abs(lam - head.own_macro_confidence) < 1e-9) {
    return { median: head.median, printed_median: head.printed_median,
      band80: head.band80, printed_band80: head.printed_band80,
      band50: head.band50, printed_band50: head.printed_band50, release: true };
  }
  const c = centresAt(head, lam).sort((a, b) => a - b);
  const q = (p) => quantile7(c, p);
  const print = (v) => roundHalfEven(v / head.print_step) * head.print_step;
  const median = q(0.5);
  const band80 = [q(0.1), q(0.9)];
  const band50 = [q(0.25), q(0.75)];
  return { median, printed_median: print(median), band80, printed_band80: band80.map(print),
    band50, printed_band50: band50.map(print), release: false };
}

// Точка при центральных значениях всех суждений на оси ставок: низ + λ·(верх − низ).
function pointAt(d, lam) {
  const fv = d.fair_value;
  if (lam === null || Math.abs(lam - bookLambda(d)) < 1e-9) return fv.central;
  const view = fv.rates_view || fv;
  return view.low + lam * (view.high - view.low);
}

function printedPoint(d, lam) {
  const fv = d.fair_value;
  if (lam === null || Math.abs(lam - bookLambda(d)) < 1e-9) {
    return isNum(fv.headline && fv.headline.printed_point) ? fv.headline.printed_point : fv.printed_central;
  }
  const step = (fv.headline && fv.headline.print_step) || 50;
  return roundHalfEven(pointAt(d, lam) / step) * step;
}

// Строка таблицы выпуска при λ ползунка: P(ниже рынка), перцентиль рынка и среднее прогонов —
// `headline.by_lambda`, EV центра против V* — `ev_first_line.by_lambda` (выпуск считает их на
// каждом положении ползунка, шаг 0,05; решение владельца 25.09.2026).
function atLambda(rows, lam) {
  if (!Array.isArray(rows) || !isNum(lam)) return null;
  return rows.find((r) => isNum(r.lambda) && Math.abs(r.lambda - lam) < 1e-9) || null;
}

/* ── распределение (герой) ── */

// Плотность центров 2 000 прогонов по суждениям книги (ядро Гаусса с отражением у нуля — цена акции
// не бывает отрицательной), под ней полосы 80 % и 50 % и «ящик» P10–P25–P50–P75–P90, линия рынка и
// точка при центральных значениях.
function distributionChart(d) {
  const head = d.fair_value.headline;
  return chart((W) => {
    const lam = lambdaNow(d);
    const centres = centresAt(head, lam).sort((a, b) => a - b);
    const hl = headlineAt(head, LAMBDA);
    const point = pointAt(d, LAMBDA);
    const market = isNum(head.market) ? head.market : d.market.price;
    const narrow = W < 520;
    const H = narrow ? 238 : 286;
    const m = { l: 10, r: 12, t: 40, b: 58 };
    const base = H - m.b;
    const hi = Math.max(quantile7(centres, 0.995), market * 1.12, hl.band80[1] * 1.08);
    const unit = niceStep(hi, 8);
    const xmax = Math.ceil(hi / unit) * unit;
    const x = scale(0, xmax, m.l, W - m.r);

    // Ширина ядра — правило Сильвермана; это сглаживание картинки, а не число.
    const n = centres.length;
    const mean = centres.reduce((s, v) => s + v, 0) / n;
    const sd = Math.sqrt(centres.reduce((s, v) => s + (v - mean) ** 2, 0) / Math.max(1, n - 1));
    const iqr = quantile7(centres, 0.75) - quantile7(centres, 0.25);
    const bw = Math.max(xmax / 200, 0.9 * Math.min(sd, iqr / 1.34 || sd) * n ** -0.2);
    // Ядро считается по мелкой гистограмме, а не по 2 000 точкам: ползунок
    // перерисовывает график на каждое движение, и на телефоне это заметно.
    const bins = 480;
    const width = xmax / bins;
    const counts = new Float64Array(bins);
    // Хвост за краем шкалы не рисуется: сваленный в последний столбик, он дал
    // бы на краю ложный горб.
    for (const c of centres) if (c >= 0 && c < xmax) counts[Math.floor(c / width)] += 1;
    const density = (v) => {
      let sum = 0;
      for (let j = 0; j < bins; j++) {
        if (!counts[j]) continue;
        const c = (j + 0.5) * width;
        const a = (v - c) / bw;
        const b = (v + c) / bw;
        if (a > -5 && a < 5) sum += counts[j] * Math.exp(-0.5 * a * a);
        if (b > -5 && b < 5) sum += counts[j] * Math.exp(-0.5 * b * b);
      }
      return sum;
    };
    const samples = Math.max(90, Math.min(260, Math.round((W - m.l - m.r) / 2.5)));
    const grid = [];
    for (let i = 0; i <= samples; i++) {
      const v = (xmax * i) / samples;
      grid.push([v, density(v)]);
    }
    const ymax = Math.max(...grid.map((p) => p[1])) * 1.1 || 1;
    const y = scale(0, ymax, base, m.t);
    const areaOn = (a, b) => {
      const pts = [[a, density(a)], ...grid.filter((p) => p[0] > a && p[0] < b), [b, density(b)]];
      return `M${x(a)},${base} ` + pts.map(([v, f]) => `L${x(v).toFixed(1)},${y(f).toFixed(1)}`).join(" ")
        + ` L${x(b)},${base} Z`;
    };
    const top = "M" + grid.map(([v, f]) => `${x(v).toFixed(1)},${y(f).toFixed(1)}`).join(" L");

    const svg = svgBox(W, H, "Распределение справедливой цены по суждениям книги");
    svg.append(
      sv("path", { d: areaOn(0, xmax), fill: "var(--model-wash-1)" }),
      sv("path", { d: areaOn(hl.band80[0], hl.band80[1]), fill: "var(--model-wash-2)" }),
      sv("path", { d: areaOn(hl.band50[0], hl.band50[1]), fill: "var(--model-wash-3)" }),
      sv("path", { d: top, fill: "none", stroke: "var(--model)", "stroke-width": 2, "stroke-linejoin": "round" }),
      line(m.l, base, W - m.r, base, { class: "axisline" }));

    // «Ящик»: усы P10–P90, ящик P25–P75, медиана.
    const ys = base + 20;
    svg.append(
      line(x(hl.band80[0]), ys, x(hl.band80[1]), ys, { stroke: "var(--model)", "stroke-width": 1.5 }),
      line(x(hl.band80[0]), ys - 5, x(hl.band80[0]), ys + 5, { stroke: "var(--model)", "stroke-width": 1.5 }),
      line(x(hl.band80[1]), ys - 5, x(hl.band80[1]), ys + 5, { stroke: "var(--model)", "stroke-width": 1.5 }),
      sv("rect", { x: x(hl.band50[0]), y: ys - 6, width: Math.max(2, x(hl.band50[1]) - x(hl.band50[0])),
        height: 12, rx: 3, fill: "var(--model-wash-3)", stroke: "var(--model)", "stroke-width": 1.5 }),
      line(x(hl.median), ys - 8, x(hl.median), ys + 8, { stroke: "var(--ink)", "stroke-width": 2.5 }));

    // Медиана — линия через всю плотность, рынок — через плотность и ящик.
    svg.append(
      line(x(hl.median), m.t - 4, x(hl.median), base, { stroke: "var(--ink)", "stroke-width": 1.5 }),
      line(x(market), m.t - 4, x(market), ys + 10, { stroke: "var(--market)", "stroke-width": 2 }));

    // Точка при центральных значениях — ромб на оси.
    const px = x(point);
    svg.append(sv("path", { d: `M${px},${base - 6} L${px + 6},${base} L${px},${base + 6} L${px - 6},${base} Z`,
      fill: "var(--surface)", stroke: "var(--ink)", "stroke-width": 1.6 }));

    // Подписи сверху: медиана и рынок, ярусами без наездов.
    const printedMedian = fmt.rub(hl.printed_median);
    const tops = [
      { x: x(hl.median), text: `медиана ${printedMedian}`, cls: "label-strong", pref: "left" },
      { x: x(market), text: `рынок ${fmt.rub(market)}`, cls: "label-strong", pref: "right" },
    ].map((t) => {
      const w = textWidth(t.text, 13, 640);
      let x0 = t.pref === "left" ? t.x - w - 6 : t.x + 6;
      if (x0 < 2) x0 = t.x + 6;
      if (x0 + w > W - 2) x0 = t.x - w - 6;
      return { ...t, w, x0, x1: x0 + w };
    });
    const rows = stackLabels(tops, 10);
    for (const t of tops) {
      svg.append(label(t.x0, 14 + t.row * 16 + (rows === 1 ? 6 : 0), t.text, { class: t.cls }));
    }
    const pointText = `точка ${fmt.rub(printedPoint(d, LAMBDA))}`;
    const pw = textWidth(pointText, 12.5, 520);
    let plx = px + 9;
    if (plx + pw > W - 2) plx = px - pw - 9;
    svg.append(label(plx, base - 9, pointText, { class: "label" }));

    // Ось цены: единица — у последней подписи, крайние подписи не вылезают за край.
    const marks = ticks(0, xmax, narrow ? 4 : 7);
    marks.forEach((t, i) => {
      const tx = x(t);
      const last = i === marks.length - 1;
      const anchor = t === 0 ? "start" : tx > W - 34 ? "end" : "middle";
      svg.append(line(tx, ys + 14, tx, ys + 18, { class: "axisline" }),
        text(anchor === "end" ? W - 1 : tx, H - 6, t === 0 ? "0" : fmt.num(t) + (last ? THIN + "₽" : ""), { class: "tick", "text-anchor": anchor }));
    });

    // Цели подсказок — шире самих отметок.
    const hot = (x0, w, tipContent) => sv("rect", { class: "hit", x: x0, y: m.t - 8, width: w,
      height: ys + 12 - (m.t - 8), tip: tipContent });
    svg.append(
      hot(x(hl.band50[0]), Math.max(6, x(hl.band50[1]) - x(hl.band50[0])), {
        title: "Полоса 50 % (P25–P75)", rows: [["от", fmt.rub(hl.printed_band50[0])], ["до", fmt.rub(hl.printed_band50[1])]],
        note: hl.release ? "числа выпуска" : "пересчитано ползунком λ из прогонов выпуска" }),
      hot(x(hl.median) - 8, 16, { title: "Медиана по суждениям книги",
        rows: [["печать", fmt.rub(hl.printed_median)], ["точно", fmt.rub(hl.median)]] }),
      hot(x(market) - 8, 16, { title: "Рыночная цена", rows: [[ticker(d), fmt.rub(market)]],
        note: d.meta && d.meta.valuation_date ? `на ${fmt.date(d.meta.valuation_date)}` : null }),
      hot(px - 8, 16, { title: "Точка при центральных значениях всех суждений",
        rows: [["печать", fmt.rub(printedPoint(d, LAMBDA))], ["точно", fmt.rub(point)]] }));
    return svg;
  }, "Распределение справедливой цены: медиана, полосы 80 и 50 процентов, рынок и точка");
}

/* ── ряд «диапазон книги → что нужно рынку» ── */

function rangeRowChart(row) {
  return chart((W) => {
    const H = 34;
    const [r0, r1] = row.range;
    const vals = [r0, r1, row.book_value];
    if (isNum(row.value)) vals.push(row.value);
    let lo = Math.min(...vals), hi = Math.max(...vals);
    const pad = (hi - lo) * 0.12 || Math.abs(hi) * 0.1 || 0.01;
    lo -= pad; hi += pad;
    const x = scale(lo, hi, 8, W - 8);
    const svg = svgBox(W, H);
    const cy = H / 2;
    svg.append(
      line(8, cy, W - 8, cy, { class: "gridline" }),
      line(x(r0), cy, x(r1), cy, { stroke: "var(--model-wash-3)", "stroke-width": 8, "stroke-linecap": "round" }),
      line(x(row.book_value), cy - 8, x(row.book_value), cy + 8, { stroke: "var(--ink)", "stroke-width": 2 }));
    if (isNum(row.value)) {
      const vx = Math.max(8, Math.min(W - 8, x(row.value)));
      svg.append(sv("circle", { cx: vx, cy, r: 6, fill: "var(--market)", stroke: "var(--surface)", "stroke-width": 2 }));
    }
    svg.append(sv("rect", { class: "hit", x: 0, y: 0, width: W, height: H, tip: {
      title: row.name,
      rows: [["нужно рынку", row.value === null ? "недостижимо" : reverseValue(row, row.value)],
        ["в книге", reverseValue(row, row.book_value, true)],
        ["диапазон книги", `${reverseValue(row, r0, true)} … ${reverseValue(row, r1, true)}`]] } }));
    return svg;
  }, `${row.name}: диапазон книги и значение, при котором точка равна рынку`);
}

// Значение оси обратного DCF в её единице: сдвиг — в пунктах, доля — в
// процентах, бета — числом (единицу называет выпуск: `unit`).
function reverseValue(row, value, bookSide = false) {
  if (!isNum(value)) return "—";
  // Значения книги — столько знаков, сколько в них есть (5 %, 5,57 %, 10,05 %);
  // найденные бисекцией — ровно два знака: у них нет «круглого» вида.
  if (row.unit === "pp") return bookSide && value === 0 ? "0" : fmt.pp(value, bookSide ? exactDigits(value * 100, 2) : 2);
  if (row.unit === "number") return fmt.num(value, bookSide ? exactDigits(value, 3) : 3);
  if (row.unit === "mult") return "×" + fmt.num(value, bookSide ? exactDigits(value, 2) : 2);
  if (row.unit === "times") return fmt.num(value, bookSide ? exactDigits(value, 2) : 2) + "×";
  return fmt.num(value * 100, bookSide ? exactDigits(value * 100, 2) : 2) + THIN + "%";
}

function exactDigits(v, max) {
  for (let k = 0; k < max; k++) {
    if (Math.abs(Math.round(v * 10 ** k) - v * 10 ** k) < 1e-6) return k;
  }
  return max;
}

/* ── EV: модель против V* ── */

function dumbbellChart(rows, opts = {}) {
  return chart((W) => {
    const rowH = 46;
    const H = rows.length * rowH + 24;
    const values = rows.flatMap((r) => [r.a, r.b]).filter(isNum);
    if (opts.extra) values.push(...opts.extra.filter(isNum));
    const lo = Math.min(...values), hi = Math.max(...values);
    const pad = (hi - lo) * 0.18 || 10;
    const x = scale(lo - pad, hi + pad, 8, W - 8);
    const svg = svgBox(W, H);
    for (const t of ticks(lo - pad, hi + pad, W < 420 ? 3 : 6)) {
      // Подпись по центру деления: у края карточки она вышла бы за SVG
      // (V* у правого края — «690» обрезалась). Такую подпись не рисуем.
      const half = textWidth(fmt.num(t), 12, 400) / 2;
      svg.append(line(x(t), 0, x(t), H - 20, { class: "gridline" }));
      if (x(t) - half >= 0 && x(t) + half <= W) {
        svg.append(text(x(t), H - 4, fmt.num(t), { class: "tick", "text-anchor": "middle" }));
      }
    }
    rows.forEach((r, i) => {
      const cy = i * rowH + 30;
      svg.append(label(8, cy - 13, r.name, { class: "label" }));
      if (isNum(r.a) && isNum(r.b)) {
        svg.append(line(x(r.a), cy, x(r.b), cy, { stroke: "var(--axis)", "stroke-width": 2 }));
      }
      const dots = [[r.a, "var(--model)", r.aLabel], [r.b, "var(--market)", r.bLabel]];
      for (const [v, color, name] of dots) {
        if (!isNum(v)) continue;
        svg.append(sv("circle", { cx: x(v), cy, r: 6, fill: color, stroke: "var(--surface)", "stroke-width": 2,
          tip: { title: `${r.name}: ${name}`, rows: [[name, fmt.bn(v, 1)]] } }));
      }
      if (isNum(r.a) && isNum(r.b)) {
        const left = Math.min(r.a, r.b), right = Math.max(r.a, r.b);
        const lt = fmt.num(left, 1), rt = fmt.num(right, 1);
        svg.append(label(x(left) - 10, cy + 4, lt, { class: "label", "text-anchor": "end" }),
          label(x(right) + 10, cy + 4, rt, { class: "label" }));
      }
    });
    return svg;
  }, opts.label || "Стоимость бизнеса по слоям: модель против рынка");
}

/* ── простые столбцы по годам ── */

function columnsChart(items, opts = {}) {
  // items: [{key, value, label}] по порядку; opts.ref: {value, text}
  return chart((W) => {
    const H = opts.height || 220;
    const m = { l: 8, r: 8, t: 22, b: 26 };
    const values = items.map((i) => i.value).filter(isNum);
    const refs = (opts.refs || []).filter((r) => isNum(r.value));
    const vmax = Math.max(...values, ...refs.map((r) => r.value), 1) * 1.12;
    const y = scale(0, vmax, H - m.b, m.t);
    const band = (W - m.l - m.r) / Math.max(1, items.length);
    const bw = Math.min(28, band * 0.56);
    // Подписи оси прореживаются от последней, чтобы не наезжать друг на друга.
    const widest = Math.max(...items.map((it) => textWidth(it.label || String(it.key), 12, 400)));
    const every = Math.max(1, Math.ceil((widest + 8) / band));
    const svg = svgBox(W, H, opts.label);
    for (const t of ticks(0, vmax, 4)) {
      svg.append(line(m.l, y(t), W - m.r, y(t), { class: "gridline" }));
    }
    items.forEach((it, i) => {
      const cx = m.l + band * (i + 0.5);
      if (isNum(it.value) && it.value > 0) {
        const top = y(it.value), h = H - m.b - top;
        const r = Math.min(4, h / 2);
        svg.append(sv("path", {
          d: `M${cx - bw / 2},${H - m.b} V${top + r} Q${cx - bw / 2},${top} ${cx - bw / 2 + r},${top} H${cx + bw / 2 - r} Q${cx + bw / 2},${top} ${cx + bw / 2},${top + r} V${H - m.b} Z`,
          fill: opts.color || "var(--model)",
          tip: { title: it.tipTitle || String(it.key), rows: [[opts.valueName || "значение", opts.fmt ? opts.fmt(it.value) : fmt.num(it.value, 1)]] } }));
        const shortText = opts.short ? opts.short(it.value) : fmt.num(it.value, 0);
        if (shortText && textWidth(shortText, 12.5, 520) < band - 2) svg.append(label(cx, top - 6, shortText, { "text-anchor": "middle" }));
      }
      if ((items.length - 1 - i) % every === 0) {
        svg.append(text(cx, H - 8, it.label || String(it.key), { class: "tick", "text-anchor": "middle" }));
      }
    });
    svg.append(line(m.l, H - m.b, W - m.r, H - m.b, { class: "axisline" }));
    for (const ref of refs) {
      const ry = y(ref.value);
      svg.append(line(m.l, ry, W - m.r, ry, { stroke: ref.color || "var(--market)", "stroke-width": 1.5 }),
        label(W - m.r, ry - 6, ref.text, { "text-anchor": "end", class: "label" }));
    }
    return svg;
  }, opts.label);
}

/* ── линии ── */

// Линии на числовой или порядковой оси X. series: [{name, color, points:
// [[x, y]], dots, dashed?, width}], opts.xType: "number" | "band", opts.yFmt.
function linesChart(series, opts = {}) {
  return chart((W) => {
    const H = opts.height || 240;
    const m = { l: opts.left || 44, r: opts.right || 16, t: opts.top || 16, b: 28 };
    const all = series.flatMap((s) => s.points).filter((p) => isNum(p[1]));
    const errs = series.flatMap((s) => (s.errors || []).flatMap((e) => [e[1], e[2]]));
    const areas = series.flatMap((s) => (s.area || []).flatMap((p) => [p[1], p[2]]));
    const bandKeys = opts.xType === "band" ? opts.categories : null;
    const map = opts.xMap || ((v) => v);
    let x;
    if (bandKeys) {
      const step = (W - m.l - m.r) / bandKeys.length;
      x = (k) => m.l + step * (bandKeys.indexOf(k) + 0.5);
    } else {
      const xs = all.map((p) => p[0]);
      const base = scale(map(opts.xMin ?? Math.min(...xs)), map(opts.xMax ?? Math.max(...xs)), m.l, W - m.r);
      x = (v) => base(map(v));
      x.d = [opts.xMin ?? Math.min(...xs), opts.xMax ?? Math.max(...xs)];
    }
    const yvals = all.map((p) => p[1]).concat(errs, areas).filter(isNum);
    let y0 = opts.yMin ?? Math.min(...yvals);
    let y1 = opts.yMax ?? Math.max(...yvals);
    const pad = (y1 - y0) * 0.1 || Math.abs(y1) * 0.1 || 1;
    if (opts.yMin === undefined) y0 -= pad;
    if (opts.yMax === undefined) y1 += pad;
    const y = scale(y0, y1, H - m.b, m.t);
    const svg = svgBox(W, H, opts.label);
    for (const t of ticks(y0, y1, opts.yTicks || 4)) {
      svg.append(line(m.l, y(t), W - m.r, y(t), { class: "gridline" }),
        text(m.l - 8, y(t) + 4, opts.yFmt ? opts.yFmt(t) : fmt.num(t), { class: "tick", "text-anchor": "end" }));
    }
    if (bandKeys) {
      // Подписи прореживаются от ПОСЛЕДНЕЙ: крайняя справа видна всегда и не
      // наезжает на предыдущую.
      const step = (W - m.l - m.r) / bandKeys.length;
      const widest = Math.max(...bandKeys.map((k) => textWidth(opts.xFmt ? opts.xFmt(k) : k, 12, 400)));
      const every = Math.max(1, Math.ceil((widest + 10) / step));
      bandKeys.forEach((k, i) => {
        if ((bandKeys.length - 1 - i) % every === 0) {
          svg.append(text(x(k), H - 8, opts.xFmt ? opts.xFmt(k) : k, { class: "tick", "text-anchor": "middle" }));
        }
      });
    } else {
      for (const t of opts.xTicks || ticks(x.d[0], x.d[1], W < 420 ? 4 : 7)) {
        svg.append(text(x(t), H - 8, opts.xFmt ? opts.xFmt(t) : fmt.num(t), { class: "tick", "text-anchor": "middle" }));
      }
    }
    svg.append(line(m.l, H - m.b, W - m.r, H - m.b, { class: "axisline" }));
    for (const ref of opts.hrefs || []) {
      svg.append(line(m.l, y(ref.value), W - m.r, y(ref.value), { stroke: ref.color, "stroke-width": 1.5 }),
        label(W - m.r, y(ref.value) - 6, ref.text, { "text-anchor": "end", class: "label" }));
    }
    for (const s of series) {
      const pts = s.points.filter((p) => isNum(p[1]));
      if (s.area) {
        const up = s.area.map((p) => `${x(p[0]).toFixed(1)},${y(p[2]).toFixed(1)}`);
        const down = s.area.slice().reverse().map((p) => `${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`);
        svg.append(sv("path", { d: `M${up.join(" L")} L${down.join(" L")} Z`, fill: s.areaFill || "var(--model-wash-2)" }));
      }
      if (pts.length > 1 && s.line !== false) {
        svg.append(sv("path", { d: "M" + pts.map((p) => `${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join(" L"),
          fill: "none", stroke: s.color, "stroke-width": s.width || 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
      }
      for (const [px, lo, hi] of s.errors || []) {
        svg.append(line(x(px), y(lo), x(px), y(hi), { stroke: s.color, "stroke-width": 2, "stroke-linecap": "round" }));
      }
      if (s.dots) {
        for (const p of pts) {
          svg.append(sv("circle", { cx: x(p[0]), cy: y(p[1]), r: s.r || 4.5, fill: s.color, stroke: "var(--surface)", "stroke-width": 2,
            tip: { title: `${s.name}: ${opts.xFmt ? opts.xFmt(p[0]) : p[0]}`, rows: [[s.name, opts.tipFmt ? opts.tipFmt(p[1]) : fmt.num(p[1], 2)]] } }));
        }
      }
    }
    return svg;
  }, opts.label);
}

/* ── HTML-полосы (водопад, торнадо) ── */

// Горизонтальные полосы в HTML, а не в SVG: длинные русские подписи
// переносятся сами, а сама полоса — процент ширины дорожки.
function barTrack(segments, domain, opts = {}) {
  const [d0, d1] = domain;
  const pos = (v) => (100 * (v - d0)) / ((d1 - d0) || 1);
  const track = el("div", { class: "bt-track" });
  if (isNum(opts.center)) track.append(el("i", { class: "bt-center", style: `left:${pos(opts.center)}%` }));
  for (const seg of segments) {
    const a = Math.min(seg.from, seg.to), b = Math.max(seg.from, seg.to);
    track.append(el("i", { class: cls("bt-bar", seg.cls), style: `left:${pos(a)}%;width:${Math.max(0.4, pos(b) - pos(a))}%`,
      tip: seg.tip || null }));
  }
  for (const mark of opts.marks || []) {
    track.append(el("i", { class: cls("bt-mark", mark.cls), style: `left:${pos(mark.at)}%`, tip: mark.tip || null }));
  }
  return track;
}

/* ── компания, периоды, словари ── */

// Имя и тикер — из выпуска (`meta.company`): литералов компании в витрине нет.
function company(d) { return (d && d.meta && d.meta.company && d.meta.company.name) || "компания"; }
function ticker(d) { return (d && d.meta && d.meta.company && d.meta.company.ticker) || "акция"; }

// Слова единицы периода: модель считает `meta.period_unit`, ближайший отчёт —
// `nowcast.period_unit`. Подпись периода — по его идентификатору (2026H2, 2026Q3).
const PERIOD_WORDS = {
  half: { one: "полугодие", gen: "полугодия", many: "полугодия", loc: "полугодии", dat: "полугодиям", ins: "полугодием" },
  quarter: { one: "квартал", gen: "квартала", many: "кварталы", loc: "квартале", dat: "кварталам", ins: "кварталом" },
};
function unitWords(unit) { return PERIOD_WORDS[unit] || PERIOD_WORDS.half; }
function modelUnit(d) { return unitWords(d.meta && d.meta.period_unit); }
function reportUnit(d) { return unitWords(d.nowcast && d.nowcast.period_unit); }

// «2026H2» → «2П 2026», «2026Q3» → «3 кв. 2026», «2026FY» → «2026 год»; короткая
// форма для осей — «2П’26», «3К’26».
function periodLabel(id) {
  const m = /^(\d{4})(?:H([12])|Q([1-4])|(FY))$/.exec(String(id || ""));
  if (!m) return String(id || "—");
  if (m[2]) return `${m[2]}П${NBSP}${m[1]}`;
  if (m[3]) return `${m[3]}${NBSP}кв.${NBSP}${m[1]}`;
  return `${m[1]}${NBSP}год`;
}
function periodShort(id) {
  const m = /^20(\d\d)(?:H([12])|Q([1-4])|(FY))$/.exec(String(id || ""));
  if (!m) return String(id || "—");
  return m[2] ? `${m[2]}П’${m[1]}` : m[3] ? `${m[3]}К’${m[1]}` : `’${m[1]}`;
}
function yearOf(id) { return String(id || "").slice(0, 4); }

// Имена слоёв — словами справочника; название слоя из выпуска — в подсказке.
const LAYER_NAMES = {
  analytical: "свой макро-взгляд",
  market_implied: "веса, вменённые рынком",
  macro_neutral: "рыночные ставки как есть",
};
const CAPEX_NAMES = { low: "низкий", base: "базовый", high: "высокий" };
// Имена наивных эталонов журнала. У эталонов ретро-проверки подпись приходит
// из выпуска (`retro.*.benchmarks[].title`) и сильнее словаря.
const BENCH_NAMES = {
  yoy_plus_shift: "тот же квартал год назад + сдвиг прошлого квартала г/г",
  yoy_growth_carried: "тот же квартал год назад × рост прошлого квартала г/г",
  seasonal_naive: "тот же квартал год назад",
  last_period: "прошлый период",
  mean_of_year: "среднее четырёх последних кварталов",
  model_expectation: "ожидание модели без индикаторов",
  guidance: "гайденс компании",
  previous_period_scaled_by_rates: "прошлый период × отношение средних ставок",
};
const GATE_NAMES = {
  margin_range: "Маржа вне исторического коридора",
  ev_ebitda: "EV/EBITDA вне коридора",
  capex_range: "Capex вне коридора",
  terminal_share: "Доля терминала вне коридора",
  interest_cover: "Покрытие процентов ниже единицы",
  equity_sign: "Капитал по DCF неположителен",
  credit_lines: "Путь долга выше лимита линий",
  zero_openings: "Ноль открытий дольше 2 лет",
  real_rate: "Реальная ставка мира вне 2–13 %",
  ev_grows_with_rates: "Стоимость растёт при росте ставок",
  book_update: "Книгу пора обновлять",
  guidance_gap: "Гайденс компании вне пути модели",
  limited_liability: "Капитал близок к нулю: нужна смена метода",
  security_change: "Карточка акции сменилась после даты книги",
};

// Подпись величины журнала — из выпуска (`nowcast.target_titles`).
function targetName(d, target) {
  const titles = (d.nowcast && d.nowcast.target_titles) || {};
  return titles[target] || String(target).replace(/^[^.]*\./, "").replace(/_/g, " ");
}

// Значение показателя журнала в его единице: маржа — доля выручки, выручка и
// проценты — млрд ₽.
function targetValue(target, value) {
  if (!isNum(value)) return "—";
  return /margin/.test(target) ? fmt.pct(value, 2) : fmt.bn(value, 1);
}

function benchTitle(d, key) {
  const retro = (d.nowcast && d.nowcast.retro) || {};
  for (const block of [retro.margin, retro.revenue, retro.interest]) {
    const row = block && (block.benchmarks || []).find((b) => b.method === key);
    if (row && row.title) return row.title;
  }
  return BENCH_NAMES[key] || key;
}

function layerTitle(d, name) {
  return LAYER_NAMES[name] || (d.layers[name] && d.layers[name].title) || name;
}

function worldWeights(d, name) {
  const w = d.worlds || {};
  const keys = Object.keys(w);
  if (name === "analytical") return keys.map((k) => `${k} ${fmt.pct(w[k].probability, 0)}`).join(" · ");
  if (name === "market_implied") return keys.map((k) => `${k} ${fmt.pct(w[k].probability_market, 0)}`).join(" · ");
  const cells = Object.keys((d.layers[name] || {}).cell_weights || {});
  const only = [...new Set(cells.map((c) => c.split("|")[0]))];
  return only.length === 1 ? `только ${only[0]}` : only.join(" · ");
}

// Внутренние ссылки текстов выпуска («(D1)», «(`ключ`)», «(решение ведущего B15)») на
// экран не идут: зрителю они ничего не говорят.
function clean(t) {
  return String(t || "").replace(/\s*\((?:D\d+|решение ведущего [A-Z]\d+|`[^`]*`)\)/g, "");
}

// Дата события календаря: день, «≈ день (окно …)» или месяц.
function whenText(e, long = false) {
  if (!e || !e.when) return "—";
  const day = long ? fmt.dateLong(e.when) : fmt.date(e.when);
  if (e.precision === "day") return day;
  if (e.precision === "month") return fmt.monthYear(e.when);
  const win = e.earliest && e.latest && e.earliest !== e.latest
    ? ` (окно ${fmt.dateShort(e.earliest)}–${fmt.dateShort(e.latest)})` : "";
  return `≈${NBSP}${day}${win}`;
}

/* ── экран «Оценка» ── */

function screenOverview(d) {
  const root = el("div", { class: "screen" });
  const row = (title, ...cards) => el("div", { class: "section" },
    el("span", { class: "eyebrow row-label" }, title), el("div", { class: "grid" }, ...cards));
  root.append(hero(d));
  root.append(row("Почему такая оценка", pricedTeaser(d), bandDrivers(d)));
  root.append(row("Что дальше", reportTeaser(d), eventsCard(d, 6)));
  root.append(row("Изменения и метод", changesCard(d, 6), methodNote(d)));
  return root;
}

// Почему точка выше медианы — причина книги (A-V9): диапазоны главных суждений асимметричны, и их
// широкая сторона тянет медиану вниз.
function pointVsMedianText(d, lam) {
  const head = d.fair_value.headline;
  const point = pointAt(d, lam);
  const medianAt = headlineAt(head, lam).median;
  if (point > medianAt) {
    const rows = Object.fromEntries((d.judgements || []).map((j) => [j.key, j]));
    const shift = (t) => {
      const m = /^сдвиг ([+-]?\d*\.?\d+)$/.exec(String(t || "").trim());
      return m ? Number(m[1]) : null;
    };
    const pulls = (head.contributions || []).map((c) => ({ c, j: rows[c.judgement_key] })).filter(({ c, j }) => {
      const lo = j ? shift(j.low_label) : null, hi = j ? shift(j.high_label) : null;
      if (lo === null || hi === null || Math.abs(Math.abs(hi) - Math.abs(lo)) < 1e-9) return false;
      return (Math.abs(hi) > Math.abs(lo)) === (c.rank_corr < 0);
    }).slice(0, 2);
    const detail = pulls.length
      ? " — " + pulls.map(({ c, j }) => `${lowerFirst(shortAxis(c.axis))} (${rangeText(j.low_label, j.high_label, j.unit)})`).join(" и ")
        + " шире в сторону, снижающую цену"
      : "";
    return `Точка выше медианы: диапазоны главных суждений асимметричны${detail}.`;
  }
  return "Точка не выше медианы.";
}

function hero(d) {
  const fv = d.fair_value;
  const head = fv.headline;
  if (!head || !Array.isArray(head.low_draws) || !head.low_draws.length) return heroWithoutBand(d);
  const market = isNum(head.market) ? head.market : d.market.price;
  const first = fv.ev_first_line;
  const view = fv.rates_view;

  const copy = el("div", { class: "hero-copy", id: "fv-hero" });
  const tiles = el("div", { class: "tiles", id: "fv-tiles" });
  const plot = distributionChart(d);
  const table = () => {
    const hl = headlineAt(head, LAMBDA);
    const book = atBookLambda(d);
    const at = book ? null : atLambda(head.by_lambda, LAMBDA);
    const frozen = book || at ? "" : " (при λ книги)";
    const rows = [
      ["P10 — нижний край полосы 80 %", fmt.rub(hl.band80[0]), fmt.rub(hl.printed_band80[0])],
      ["P25 — нижний край полосы 50 %", fmt.rub(hl.band50[0]), fmt.rub(hl.printed_band50[0])],
      ["Медиана", fmt.rub(hl.median), fmt.rub(hl.printed_median)],
      ["P75 — верхний край полосы 50 %", fmt.rub(hl.band50[1]), fmt.rub(hl.printed_band50[1])],
      ["P90 — верхний край полосы 80 %", fmt.rub(hl.band80[1]), fmt.rub(hl.printed_band80[1])],
      ["Точка при центральных значениях", fmt.rub(pointAt(d, LAMBDA)), fmt.rub(printedPoint(d, LAMBDA))],
      ["Среднее прогонов" + frozen, fmt.rub(at ? at.mean : head.mean), "—"],
      ["Рыночная цена", fmt.rub(market), "—"],
      ["P(центр ниже рынка)" + frozen, fmt.pct(at ? at.p_below_market : head.p_below_market, 1), "—"],
    ];
    return dataTable([
      { title: "Величина", value: (r) => r[0], cls: "name" },
      { title: "Точно", num: true, value: (r) => r[1] },
      { title: "Печать", num: true, value: (r) => r[2] },
    ], rows, { caption: book ? "Числа выпуска"
      : `λ = ${fmt.num(LAMBDA, 2)}: квантили и точка — пересчёт ползунком из прогонов выпуска, `
        + (at ? "среднее и P(ниже рынка) — таблица выпуска для этого λ" : "среднее и P(ниже рынка) — при λ книги") });
  };
  const fig = withTable(plot, table);

  const out = el("output", { class: "lambda-out", for: "lambda" });
  const reset = el("button", { class: "lambda-reset", type: "button", hidden: true }, "вернуть λ книги");
  const slider = el("input", { id: "lambda", type: "range", min: "0", max: "1", step: "0.05",
    value: String(bookLambda(d)), "aria-label": "Вес собственного взгляда на инфляцию и ставки, λ" });
  const lambdaBox = el("div", { class: "lambda" },
    el("div", { class: "lambda-top" },
      el("span", { class: "lambda-title" }, "Взгляд на инфляцию и ставки"), out),
    el("div", { class: "lambda-ends" },
      el("span", {}, el("i", { class: "key key-market" }), "рыночные ставки как есть"),
      el("span", {}, "свой макро-взгляд", el("i", { class: "key key-model" }))),
    slider,
    el("p", { class: "lambda-note" },
      view ? `Ось ставок — не интервал, а вклад собственного взгляда: низ ${fmt.rub(view.low)} — мир форвардов ОФЗ (рынок прав целиком), `
        + `а не худший случай: мира выше форвардов в сетке нет; верх ${fmt.rub(view.high)} — свой макро-взгляд. `
        + "Ползунок пересчитывает точку и полосу из прогонов выпуска по правилу книги." : "Ползунок пересчитывает точку и полосу из прогонов выпуска по правилу книги.",
      " ", reset));

  const chartCard = el("section", { class: "card hero-chart" },
    el("div", { class: "card-head" },
      el("div", {},
        el("h2", {}, "Распределение справедливой цены"),
        el("p", { class: "sub" }, `${fmt.num(head.draws)} прогонов по ${fmt.num((head.contributions || []).length)} суждениям книги в их диапазонах`)),
      fig.button),
    fig.box,
    legend([["key-b80", "80 % прогонов"], ["key-b50", "50 %"], ["key-line key-ink", "медиана"],
      ["key-line key-market", "рынок"], ["key-diamond", "точка при центральных значениях"]]),
    lambdaBox);

  const update = () => {
    const lam = LAMBDA;
    const hl = headlineAt(head, lam);
    const book = atBookLambda(d);
    const at = book ? null : atLambda(head.by_lambda, lam);
    const pBelow = at ? at.p_below_market : head.p_below_market;
    const percentile = at ? at.market_percentile : head.market_percentile;
    copy.replaceChildren(
      el("span", { class: "eyebrow" }, `Справедливая стоимость акции ${ticker(d)} · ${fmt.date(d.meta.valuation_date)}`),
      el("div", { class: "hero-figure", id: "fv-headline" },
        el("span", { class: "hero-approx" }, "≈"),
        el("span", { class: "hero-value", id: "kpi-central" }, fmt.num(hl.printed_median)),
        el("span", { class: "hero-unit" }, "₽")),
      el("p", { class: "hero-caption" }, "медиана по суждениям книги ", d.meta.book_version,
        el("span", { class: "muted" }, ` · точно ${fmt.rub(hl.median)}${book ? "" : ` · при λ = ${fmt.num(lam, 2)}`}`)),
      el("div", { class: "bands" },
        el("div", { class: "band-row" }, el("i", { class: "band-key b80" }),
          el("span", { class: "what" }, "полоса 80 %"),
          el("span", { class: "range" }, `${fmt.num(hl.printed_band80[0])}–${fmt.num(hl.printed_band80[1])}${THIN}₽`)),
        el("div", { class: "band-row" }, el("i", { class: "band-key b50" }),
          el("span", { class: "what" }, "полоса 50 %"),
          el("span", { class: "range" }, `${fmt.num(hl.printed_band50[0])}–${fmt.num(hl.printed_band50[1])}${THIN}₽`))),
      el("div", { class: "verdict", id: "fv-ev" },
        el("div", { class: "verdict-top" },
          el("span", { class: "verdict-num" }, fmt.pct(pBelow, pBelow > 0 && pBelow < 0.01 ? 2 : 0)),
          el("span", { class: "verdict-title" }, `вероятность, что справедливая цена ниже рыночной — ${fmt.rub(market)}`)),
        el("div", { class: "meter", role: "img", "aria-label": `P(ниже рынка) ${fmt.pct(pBelow, 1)}` },
          el("i", { style: `width:${Math.max(0, Math.min(100, pBelow * 100))}%` })),
        el("span", { class: "verdict-note" },
          `P(ниже рынка) по ${fmt.num(head.draws)} прогонам`,
          isNum(percentile) ? ` · рынок — на ${fmt.num(percentile * 100, percentile > 0 && percentile < 0.01 ? 2 : 0)}-м перцентиле распределения центра` : "",
          book ? "" : at ? ` · при λ = ${fmt.num(lam, 2)}` : " · при λ книги")));
    tiles.replaceChildren(...heroTiles(d, lam, first, view));
    const bookLam = bookLambda(d);
    out.value = book ? `λ = ${fmt.num(bookLam, 2)} · книга` : `λ = ${fmt.num(lam, 2)} · точка ${fmt.rub(printedPoint(d, lam))}`;
    reset.hidden = book;
  };
  slider.addEventListener("input", () => {
    const v = Number(slider.value);
    LAMBDA = Math.abs(v - bookLambda(d)) < 1e-9 ? null : v;
    update();
    repaint(plot);
    fig.refresh();
  });
  reset.addEventListener("click", () => {
    LAMBDA = null;
    slider.value = String(bookLambda(d));
    update();
    repaint(plot);
    fig.refresh();
    slider.focus();
  });
  if (LAMBDA !== null) slider.value = String(LAMBDA);
  update();

  return el("div", {},
    el("div", { class: "hero" }, copy, chartCard),
    tiles);
}

function heroTiles(d, lam, first, view) {
  const out = [];
  if (first) {
    // При λ, отличном от книги, — строка выпуска для этого λ (`by_lambda`);
    // выпуск без таблицы — числа при λ книги с подписью.
    const book = atBookLambda(d);
    const at = book ? null : atLambda(first.by_lambda, lam);
    const ev = at || first;
    const where = book ? "" : at ? ` (при λ = ${fmt.num(lam, 2)})` : " (при λ книги)";
    out.push(el("section", { class: "card tile" },
      el("span", { class: "tile-label" }, "Стоимость бизнеса: модель против рынка"),
      el("span", { class: "tile-value" }, fmt.signedPct(ev.gap, 2)),
      el("p", { class: "tile-note" },
        `EV модели (${diagWords(first).center}) ${fmt.bn(ev.v0, 0)} против рыночного V* ${fmt.bn(ev.v_star, 0)}`,
        where,
        ". V* — стоимость бизнеса, при которой та же функция «EV → цена» даёт рыночную цену.")));
    out.push(el("section", { class: "card tile" },
      el("span", { class: "tile-label" }, "Цена 1 % стоимости бизнеса"),
      el("span", { class: "tile-value" }, fmt.num(ev.rub_per_1pct_ev), el("span", { class: "unit" }, "₽ на акцию")),
      el("p", { class: "tile-note" },
        `Капитализация ${fmt.bn(d.market.cap, 1)} при рыночном V* ${fmt.bn(ev.v_star, 0)}${where}: `
        + `каждый процент спора о стоимости бизнеса — ${fmt.rub(ev.rub_per_1pct_ev)} в цене акции (рынок ${fmt.rub(d.market.price)}).`)));
  }
  out.push(el("section", { class: "card tile" },
    el("span", { class: "tile-label" }, "Точка при центральных значениях всех суждений"),
    el("span", { class: "tile-value" }, fmt.num(printedPoint(d, lam)), el("span", { class: "unit" }, "₽")),
    el("p", { class: "tile-note", id: "fv-lede" }, ledeHtml(d, lam))));
  if (view) {
    out.push(el("section", { class: "card tile" },
      el("span", { class: "tile-label" }, "Вклад взгляда на инфляцию и ставки"),
      el("span", { class: "tile-value" }, fmt.signed(view.rub), el("span", { class: "unit" }, "₽")),
      el("p", { class: "tile-note" },
        `Взгляд на инфляцию и ставки: рыночные ставки ${fmt.rub(view.printed_low)} — свой взгляд ${fmt.rub(view.printed_high)} (точно ${fmt.num(view.low)} и ${fmt.num(view.high)}). `
        + "Это ось «чей взгляд», а не интервал неопределённости.")));
  }
  return out;
}

// Вводная к точке: точное значение и почему она не совпадает с медианой.
function ledeHtml(d, lam) {
  const point = pointAt(d, lam);
  return `Все суждения книги в центре своих диапазонов: точно ${fmt.rub(point)}. ` + pointVsMedianText(d, lam);
}

// Выпуск без прогонов по суждениям (быстрая сборка без медленных блоков): крупно — точка; полосы
// нет, и витрина не притворяется, что она есть.
function heroWithoutBand(d) {
  const fv = d.fair_value;
  const market = isNum(fv.market) ? fv.market : d.market.price;
  const plot = chart((W) => {
    const H = 120;
    const hi = Math.max(fv.high, market) * 1.25;
    const x = scale(0, hi, 10, W - 12);
    const svg = svgBox(W, H, "Ось ставок, точка и рынок");
    const cy = 56;
    svg.append(line(x(fv.low), cy, x(fv.high), cy, { stroke: "var(--model-wash-3)", "stroke-width": 14, "stroke-linecap": "round" }),
      line(x(fv.central), cy - 14, x(fv.central), cy + 14, { stroke: "var(--ink)", "stroke-width": 2.5 }),
      line(x(market), cy - 22, x(market), cy + 22, { stroke: "var(--market)", "stroke-width": 2 }));
    const items = [{ x: x(fv.central), text: `точка ${fmt.rub(fv.printed_central)}` }, { x: x(market), text: `рынок ${fmt.rub(market)}` }]
      .map((t) => { const w = textWidth(t.text, 13, 640); const x0 = Math.min(Math.max(2, t.x - w / 2), W - w - 2); return { ...t, x0, x1: x0 + w }; });
    stackLabels(items, 10);
    for (const t of items) svg.append(label(t.x0, t.row === 0 ? 18 : H - 30, t.text, { class: "label-strong" }));
    for (const t of ticks(0, hi, W < 420 ? 4 : 6)) svg.append(text(x(t), H - 4, fmt.num(t), { class: "tick", "text-anchor": t === 0 ? "start" : "middle" }));
    return svg;
  }, "Ось ставок, точка и рынок");
  return el("div", { class: "hero" },
    el("div", { class: "hero-copy", id: "fv-hero" },
      el("span", { class: "eyebrow" }, `Справедливая стоимость акции ${ticker(d)} · ${fmt.date(d.meta.valuation_date)}`),
      el("div", { class: "hero-figure", id: "fv-headline" },
        el("span", { class: "hero-approx" }, "≈"),
        el("span", { class: "hero-value", id: "kpi-central" }, fmt.num(fv.printed_central)),
        el("span", { class: "hero-unit" }, "₽")),
      el("p", { class: "hero-caption" }, "точка при центральных значениях всех суждений",
        el("span", { class: "muted" }, ` · точно ${fmt.rub(fv.central)}`)),
      el("div", { class: "bands" },
        el("div", { class: "band-row" }, el("i", { class: "band-key b80" }),
          el("span", { class: "what" }, "ось ставок — не интервал"),
          el("span", { class: "range" }, `${fmt.num(fv.printed_low)}–${fmt.num(fv.printed_high)}${THIN}₽`))),
      el("div", { class: "verdict" },
        el("div", { class: "verdict-top" },
          el("span", { class: "verdict-num" }, fmt.rub(market)),
          el("span", { class: "verdict-title" }, "рыночная цена")),
        el("span", { class: "verdict-note" }, "В выпуске нет прогонов по суждениям — он собран быстрой сборкой: медианы и честной полосы нет, крупно печатается точка."))),
    el("section", { class: "card hero-chart" },
      el("div", { class: "card-head" }, el("div", {},
        el("h2", {}, "Точка и рынок на оси ставок"),
        el("p", { class: "sub" }, "Полосы по суждениям в этом выпуске нет — ползунок λ её не пересчитывает и не обещает."))),
      plot,
      legend([["key-b50", "ось ставок: рыночные ставки — свой взгляд"], ["key-line key-ink", "точка"], ["key-line key-market", "рынок"]])));
}

// Для какого числа выпуск решил «что в цене», «EV против V*» и нейтральную маржу: `target:
// "median"` (книга 1.5) — печатаемая медиана, иначе — точка при центральных значениях.
function diagWords(block) {
  return block && block.target === "median"
    ? { median: true, subj: "медиана", full: "медиана", acc: "медиану", gen: "медианы", center: "медиана" }
    : { median: false, subj: "точка", full: "точка при центральных значениях", acc: "точку при центральных значениях", gen: "точки", center: "центр" };
}

function upperFirst(text) {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

// Строка «что даст отчёт» для числа диагностик: медиана и её ось ставок
// (книга 1.5) или точка.
function reportRow(d, r) {
  return diagWords(d.next_report_neutral).median && isNum(r.median)
    ? { value: r.median, low: r.median_low, high: r.median_high }
    : { value: r.central, low: r.low, high: r.high };
}

// Заголовок «Что в цене» — из решённых значений: есть ли решение внутри
// диапазона книги и какую долю пути от значения книги к краю оно занимает.
function pricedHeadline(rows) {
  if (!rows.length) return "Что заложено в рыночную цену";
  const share = (r) => {
    const end = r.value >= r.book_value ? r.range[1] : r.range[0];
    return end === r.book_value ? 0 : (r.value - r.book_value) / (end - r.book_value);
  };
  const inside = rows.filter((r) => r.inside_range).map((r) => ({ r, s: share(r) })).sort((a, b) => a.s - b.s);
  if (!inside.length) return "Ни одно суждение в пределах книги не объясняет рыночную цену";
  return `Рыночную цену объясняет одно суждение внутри диапазона книги: ${lowerFirst(shortAxis(inside[0].r.name))} — ${fmt.pct(inside[0].s, 0)} пути к краю`;
}

function pricedTeaser(d) {
  const block = d.reverse_dcf;
  const rows = block && Array.isArray(block.rows) ? block.rows : null;
  if (!rows) {
    return card({ title: "Что заложено в цену", span: 6, link: ["market", "Подробно"] }, missing("обратный DCF по осям книги"));
  }
  const inside = rows.filter((r) => r.inside_range);
  const outside = rows.filter((r) => !r.inside_range);
  return card({ title: "Что заложено в цену", span: 6, link: ["market", "Все оси"],
    sub: `Значение одного суждения, при котором ${diagWords(block).full} равна рынку (${fmt.rub(block.market)}); остальные — как в книге.` },
  inside.length
    ? el("div", { class: "rows" }, inside.map((r) => el("div", { class: "rrow" },
      el("div", { class: "rrow-name" }, r.name),
      rangeRowChart(r),
      el("div", { class: "rrow-need" }, el("span", { class: "v" }, reverseValue(r, r.value)),
        el("span", { class: "b" }, `книга ${reverseValue(r, r.book_value, true)} · ${badgeText(r)}`)))))
    : empty("Ни одно суждение в пределах своего диапазона не даёт рыночную цену."),
  outside.length ? el("p", { class: "card-foot" }, "Вне диапазона книги: ",
    outside.map((r) => `${lowerFirst(shortAxis(r.name))} ${r.value === null ? "— недостижимо" : reverseValue(r, r.value)}`).join("; ")) : null);
}

function badgeText(row) {
  return row.value === null ? "недостижимо" : row.inside_range ? "в диапазоне книги" : "вне диапазона";
}

// «Операционная касса» внутри фразы — со строчной; аббревиатуры (ERP, σ_EV) — как есть.
function lowerFirst(textContent) {
  const s = String(textContent);
  return s.length > 1 && s[1] === s[1].toLowerCase() && s[1] !== s[1].toUpperCase() ? s[0].toLowerCase() + s.slice(1) : s;
}

function shortAxis(name) {
  return String(name).replace(/\s*\(.*\)\s*/g, " ").replace(/,\s*%.*$/, "").trim();
}

function bandDrivers(d) {
  const head = d.fair_value.headline;
  const rows = head && Array.isArray(head.contributions) ? head.contributions : null;
  if (!rows || !rows.length) {
    return card({ title: "Что определяет полосу", span: 6 }, missing("вклад суждений в полосу"));
  }
  const top = rows.slice(0, 6);
  const rest = rows.slice(6);
  const max = Math.max(...rows.map((r) => r.share));
  // Ось полосы → строка таблицы суждений по ключу выпуска (`judgement_key`):
  // в подсказке — диапазон книги, в котором ось гуляет.
  const judgements = Object.fromEntries((d.judgements || []).map((j) => [j.key, j]));
  const range = (r) => {
    const j = r.judgement_key ? judgements[r.judgement_key] : null;
    return j ? [["диапазон книги", rangeText(j.low_label, j.high_label, j.unit)]] : [];
  };
  const bar = (r) => el("div", { class: "hbar", tip: { title: r.axis, rows: [["доля полосы", fmt.pct(r.share, 1)], ["ранговая корреляция", fmt.num(r.rank_corr, 2)], ...range(r)],
    note: r.rank_corr < 0 ? "больше значение — ниже цена" : "больше значение — выше цена" } },
  el("span", { class: "hbar-name" }, r.axis),
  el("span", { class: "hbar-track" }, el("i", { class: "hbar-fill", style: `width:${(100 * r.share) / max}%` })),
  el("span", { class: "hbar-val" }, fmt.pct(r.share, r.share < 0.1 ? 1 : 0)));
  return card({ title: "Что определяет полосу", span: 6, link: ["book", "Все суждения"],
    sub: `Вклад суждения — доля квадрата ранговой корреляции с центром по ${fmt.num(head.draws)} прогонам.` },
  el("div", { class: "hbars" }, top.map(bar)),
  rest.length ? el("div", { class: "card-foot" }, detailsBlock(`Ещё ${rest.length} суждений`, el("div", { class: "hbars" }, rest.map(bar)))) : null);
}

// Ближайший отчёт — квартальный (`nowcast.period_unit`), а в правило обновления
// и в «что даст отчёт» идёт период модели (`meta.period_unit`).
function reportTeaser(d) {
  const fact = d.calendar && d.calendar.next_fact;
  const now = d.nowcast || {};
  const nc = now.margin;
  const bench = nc ? mainBenchmark(d, now.target, nc.period) : null;
  const guide = now.guidance;
  const rows = d.next_report_value || [];
  const neutral = d.next_report_neutral || {};
  const half = neutral.period || (nc && nc.half);
  return card({ title: "Ближайший отчёт", span: 6, link: ["report", "Подробно"],
    sub: fact ? fact.title : "календаря отчётов в выпуске нет" },
  fact ? el("div", { class: "countdown" },
    el("span", { class: "big" }, fmt.num(fact.days)),
    el("span", { class: "ink-2" }, `${plural(fact.days, ["день", "дня", "дней"])} до ${whenText(fact, true)}`)) : null,
  nc ? el("div", { class: "kpis", style: "margin-top:14px" },
    kpi(fmt.pct(nc.value, 2), `нау-каст маржи ${periodLabel(nc.period)}, ± ${fmt.num(nc.std_error * 100, 2)} п.п.`),
    bench ? kpi(fmt.pct(bench.value, 2), `главный эталон: ${bench.title}`) : null,
    guide && isNum(guide.quarter_benchmark)
      ? kpi(fmt.pct(guide.quarter_benchmark, 2), `эталон гайденса (≥${NBSP}${fmt.pct(guide.fy_margin_min, 0)} за ${guide.year})`) : null,
    isNum(nc.half_value) ? kpi(fmt.pct(nc.half_value, 2), `маржа ${periodLabel(nc.half)}, которой соответствует прогноз`) : null)
    : empty("Нау-каста в выпуске нет."),
  rows.length ? el("div", { style: "margin-top:14px" },
    el("span", { class: "tile-label" }, `Если маржа ${periodLabel(half)} выйдет …, ${diagWords(neutral).full} станет:`),
    impactChart(d, true)) : null,
  el("p", { class: "card-foot" }, neutralSentence(d),
    `В правило обновления идёт ${modelUnit(d).one} ${periodLabel(half)}; нау-каст ${reportUnit(d).gen} к цене не подключён — он показывает, что даст отчёт, и копит зачёт против наивных эталонов.`));
}

// Нейтральная маржа и наклон — числами выпуска (`next_report_neutral`): при каком факте отчёта
// точка при центральных значениях не сдвинется и сколько рублей стоит каждая десятая пункта маржи.
function neutralSentence(d) {
  const n = d.next_report_neutral || {};
  if (!isNum(n.margin)) return n.error ? "Нейтральная маржа в этом выпуске не посчитана. " : "";
  const slope = isNum(n.slope_rub_per_0p1pp) ? `; каждые 0,1${NBSP}п.п. — около ${fmt.rub(n.slope_rub_per_0p1pp)}` : "";
  return `Нейтральная маржа ${periodLabel(n.period)} — ${fmt.pct(n.margin, 2)}: при таком факте ${diagWords(n).full} не изменится (${fmt.rub(n.central)}), выше — вырастет, ниже — снизится${slope}. `;
}

function mainBenchmark(d, target, period) {
  const naive = (d.nowcast && d.nowcast.naive) || {};
  const bench = naive[`${target} ${period}`];
  if (!bench || !bench.main || !isNum(bench.naive[bench.main])) return null;
  return { key: bench.main, value: bench.naive[bench.main], title: benchTitle(d, bench.main), forecast: bench.forecast };
}

function eventsCard(d, limit) {
  const cal = d.calendar || {};
  const events = (cal.events || []).slice(0, limit || 99);
  const fact = cal.next_fact;
  const items = events.map((e) => ({ ...e, fact: !!fact && e.id === fact.id }));
  if (fact && !items.some((e) => e.id === fact.id)) items.push({ ...fact, fact: true });
  if (!items.length) return card({ title: "События", span: 6 }, empty("Ближайших событий в выпуске нет."));
  return card({ title: "События", span: 6, sub: `отсчёт — от даты оценки ${fmt.date(cal.today || d.meta.valuation_date)}` },
    el("ol", { class: "timeline" }, items.map((e) => el("li", { class: cls(e.fact && "is-fact") },
      el("span", { class: "tl-date" }, e.precision === "month" ? fmt.monthYear(e.when)
        : `${e.precision === "day" ? "" : "≈" + NBSP}${fmt.dateShort(e.when)}`,
      el("span", { class: "muted" }, `через ${fmt.days(e.days)}`)),
      el("span", { class: "tl-rail" }, el("i")),
      el("span", { class: "tl-body" }, el("strong", {}, e.title),
        e.precision === "window" && e.earliest && e.latest
          ? el("span", { class: "muted tl-window" }, `окно ${fmt.dateShort(e.earliest)}–${fmt.dateShort(e.latest)}`) : null,
        e.note ? detailsBlock("подробнее", ruText(e.note)) : null)))));
}

function changesCard(d, span) {
  const ch = d.changes || {};
  const items = ch.items || [];
  return card({ title: "Что изменилось с прошлого выпуска", span: span || 6,
    sub: ch.note ? ch.note.charAt(0).toUpperCase() + ch.note.slice(1) : "" },
  items.length ? el("ul", { class: "list" },
    items.map((i) => el("li", {},
      el("span", { class: "t" }, i.title, i.note ? el("span", { class: "muted" }, i.note) : null),
      el("span", { class: "v" }, fmt.signedRub(i.delta_price, 1)))),
    ch.total ? el("li", { class: "total" }, el("span", { class: "t" }, el("strong", {}, "Итого, точка при центральных значениях")),
      el("span", { class: "v" }, fmt.signedRub(ch.total.delta_price, 1))) : null)
    : empty(sentence("Разложения нет: " + (ch.note || "в выпуске нет блока изменений"))),
  ch.previous ? el("p", { class: "card-foot" }, `Прошлый выпуск ${ch.previous}. Шаги по порядку: ставки → наблюдения → факты → книга → код → перекат даты; эффект шага зависит от порядка.`) : null);
}

function methodNote(d) {
  const formula = `(V0${NBSP}−${NBSP}D)·(1${NBSP}−${NBSP}g)`;
  return card({ title: "Как читать эту оценку", span: 6 },
    el("div", { class: "prose" },
      el("p", {}, el("strong", {}, "Крупное число — медиана, а не точка. "),
        "Каждый из прогонов сдвигает суждения книги в их диапазонах одновременно (треугольно, мода — значение книги) и пересчитывает всю сетку, три слоя и цену. Полоса 80 % честнее одной цифры."),
      el("p", {}, el("strong", {}, "Стоимость бизнеса считается первой. "),
        `Сетка сценариев — миры ставок × режимы маржи × уровни capex — даёт стоимость бизнеса V0; цена акции — внутренняя стоимость ${formula} на ${fmt.num(d.meta.shares_mln, 3)} млн акций в обращении, где D — требования моста, g — дисконт за управление ${fmt.pct(d.meta.governance_discount, 2)}.`),
      el("p", {}, "Спор с рынком ведётся на уровне стоимости бизнеса: экран «Что в цене» показывает, какое значение одного суждения делает медиану равной рыночной цене.")),
    el("p", { class: "card-foot" }, el("a", { href: "#model" }, "Как устроен расчёт →")));
}

/* ── экран «Что в цене» ── */

function screenMarket(d) {
  const block = d.reverse_dcf;
  const rows = block && Array.isArray(block.rows) ? block.rows : [];
  const root = el("div", { class: "screen" },
    screenHead("Что заложено в цену", pricedHeadline(rows),
      `Рынок платит ${fmt.rub(d.market.price)} за акцию (${fmt.date(d.meta.valuation_date)}). Ниже — какое значение одного суждения делает ${diagWords(block).acc} равной рынку, насколько модель и рынок расходятся в стоимости бизнеса и как это соотносится с аналогами.`));
  root.append(el("div", { class: "grid" }, reverseDcfCard(d)));
  root.append(el("div", { class: "grid section" }, evLayersCard(d), sellsideCard(d.market.sellside, d)));
  root.append(el("div", { class: "grid section" }, peersCard(d)));
  root.append(el("div", { class: "grid section" }, spreadCard(d)));
  return root;
}

function reverseDcfCard(d) {
  const block = d.reverse_dcf;
  if (!block || !Array.isArray(block.rows)) return card({ title: "Обратный DCF по осям книги" }, missing("обратный DCF"));
  const legendRow = legend([["key-b50", "диапазон книги"], ["key-line key-ink", "значение книги"], ["key-dot key-market", "нужно рынку"]]);
  const table = () => dataTable([
    { title: "Ось", value: (r) => r.name, cls: "name" },
    { title: "Нужно рынку", num: true, value: (r) => r.value === null ? "недостижимо" : reverseValue(r, r.value) },
    { title: "В книге", num: true, value: (r) => reverseValue(r, r.book_value, true) },
    { title: "Диапазон книги", num: true, value: (r) => `${reverseValue(r, r.range[0], true)} … ${reverseValue(r, r.range[1], true)}` },
    { title: "", value: (r) => r.value === null ? badge("недостижимо", "out") : r.inside_range ? badge("✓ в диапазоне", "good") : badge("вне диапазона", "out") },
  ], block.rows);
  const rowsBox = el("div", { class: "rows" }, block.rows.map((r) => el("div", { class: "rrow" },
    el("div", { class: "rrow-name" }, r.name,
      el("span", { class: "hint" }, `книга ${reverseValue(r, r.book_value, true)} · диапазон ${reverseValue(r, r.range[0], true)} … ${reverseValue(r, r.range[1], true)}`)),
    rangeRowChart(r),
    el("div", { class: "rrow-need" },
      r.value === null
        ? [el("span", { class: "v muted" }, "недостижимо"), el("span", { class: "b" }, "цена не достигается на всём отрезке поиска")]
        : [el("span", { class: "v" }, reverseValue(r, r.value)), " ",
          r.inside_range ? badge("✓ в диапазоне", "good") : badge("вне диапазона", "out")]))));
  const fig = withTable(rowsBox, table);
  return card({ title: "Обратный DCF по осям книги", tools: fig.button,
    sub: `${block.criterion ? block.criterion.charAt(0).toUpperCase() + block.criterion.slice(1) : ""}: рынок ${fmt.rub(block.market)} на ${fmt.date(block.valuation_date)}. Остальные суждения — как в книге; «недостижимо» — цена не достигается на всём отрезке поиска.` },
  legendRow, fig.box);
}

function evLayersCard(d) {
  const fv = d.fair_value;
  const first = fv.ev_first_line;
  const rows = [
    { name: `${diagWords(first).center} (λ = ${fmt.num(first.own_macro_confidence, 2)})`, a: first.v0, b: first.v_star, aLabel: "EV модели", bLabel: "V* рынка", gap: first.gap },
    ...["analytical", "macro_neutral"].filter((k) => first.layers && first.layers[k]).map((k) => ({
      name: layerTitle(d, k), a: first.layers[k].v0, b: first.layers[k].v_star, aLabel: "V0 слоя", bLabel: "V* слоя", gap: first.layers[k].gap })),
  ];
  const plot = dumbbellChart(rows, { label: "EV модели и рыночный V* по слоям, млрд ₽" });
  const fig = withTable(plot, () => dataTable([
    { title: "Слой", value: (r) => r.name, cls: "name" },
    { title: "EV модели, млрд ₽", num: true, value: (r) => fmt.num(r.a, 1) },
    { title: "V* рынка, млрд ₽", num: true, value: (r) => fmt.num(r.b, 1) },
    { title: "Разрыв", num: true, value: (r) => fmt.signedPct(r.gap, 2) },
  ], rows));
  const cpd = first.cap_plus_d;
  return card({ title: "Стоимость бизнеса: модель против V*", span: 7, tools: fig.button,
    sub: "V* — стоимость бизнеса, при которой та же функция «EV → цена» даёт рыночную цену. Спор с рынком — на уровне EV, где рычаг не раздувает его." },
  legend([["key-dot key-model", "EV модели (V0)"], ["key-dot key-market", "рыночный V*"]]),
  fig.box,
  el("div", { class: "kpis", style: "margin-top:14px" },
    kpi(fmt.x(first.ev_ebitda, 2), `EV модели / EBITDA LTM ${fmt.num(first.ebitda_ltm, 1)}`),
    kpi(fmt.x(first.ev_ebitda_v_star, 2), "V* / EBITDA LTM"),
    kpi(fmt.rub(first.rub_per_1pct_ev), `цена 1 % EV для ${diagWords(first).gen}`)),
  cpd ? el("p", { class: "card-foot" }, `Справочно «капитализация + D»: ${fmt.bn(cpd.ev, 1)} (${fmt.signedPct(cpd.gap, 2)} к модели, ${fmt.x(cpd.ev_ebitda, 2)}). V* от него отличается требованиями моста сверх чистого долга и дисконтом за управление.`) : null);
}

// Цели инвестдомов: агрегаты и список последних целей каждого дома с датами
// (D16); рынок и медиана модели — на одной шкале с разбросом целей.
function sellsideCard(s, d) {
  if (!s || !isNum(s.median)) return card({ title: "Цели инвестдомов", span: 5 }, empty("Целей инвестдомов в выпуске нет."));
  const head = d.fair_value.headline;
  const model = head ? head.printed_median : d.fair_value.printed_central;
  const plot = chart((W) => {
    const vals = [s.min, s.max, s.median, d.market.price, model].filter(isNum);
    const hi = Math.max(...vals) * 1.06;
    const x = scale(0, hi, 8, W - 12);
    const cy = 14;
    const marks = [
      { v: d.market.price, color: "var(--market)", t: `рынок ${fmt.rub(d.market.price)}` },
      { v: model, color: "var(--model)", t: `модель ${fmt.rub(model)}` },
    ];
    const items = [{ x: x(s.median), text: `медиана целей ${fmt.rub(s.median)}` }, ...marks.map((mk) => ({ x: x(mk.v), text: mk.t }))]
      .map((t) => { const w = textWidth(t.text, 12.5); const x0 = Math.min(Math.max(2, t.x - w / 2), W - w - 2); return { ...t, x0, x1: x0 + w }; });
    const rows = stackLabels(items, 10);
    const H = cy + 22 + rows * 16 + 22;
    const svg = svgBox(W, H, "Цели инвестдомов, рыночная цена и медиана модели");
    svg.append(line(x(s.min), cy, x(s.max), cy, { stroke: "var(--axis)", "stroke-width": 8, "stroke-linecap": "round",
      tip: { title: "Цели инвестдомов", rows: [["минимум", fmt.rub(s.min)], ["медиана", fmt.rub(s.median)], ["максимум", fmt.rub(s.max)], ["целей", fmt.num(s.n)]] } }));
    for (const h of s.houses || []) {
      if (isNum(h.target)) svg.append(line(x(h.target), cy - 5, x(h.target), cy + 5, { stroke: "var(--ink-2)", "stroke-width": 1.2 }));
    }
    svg.append(line(x(s.median), cy - 10, x(s.median), cy + 10, { stroke: "var(--ink)", "stroke-width": 2 }));
    for (const mk of marks) svg.append(sv("circle", { cx: x(mk.v), cy, r: 6, fill: mk.color, stroke: "var(--surface)", "stroke-width": 2 }));
    for (const t of items) svg.append(label(t.x0, cy + 30 + t.row * 16, t.text));
    for (const t of ticks(0, hi, W < 420 ? 3 : 5)) svg.append(text(x(t), H - 3, fmt.num(t), { class: "tick", "text-anchor": t === 0 ? "start" : "middle" }));
    return svg;
  }, "Цели инвестдомов, рыночная цена и медиана модели");
  const houses = (s.houses || []).slice().sort((a, b) => String(b.date).localeCompare(String(a.date)));
  return card({ title: "Цели инвестдомов", span: 5, sub: `на ${fmt.date(s.as_of)} · ${fmt.num(s.n)} целей · справочно` },
    plot,
    el("div", { class: "kpis", style: "margin-top:10px" },
      kpi(fmt.rub(s.median), "медиана целей"), kpi(`${fmt.num(s.min)}–${fmt.num(s.max)}${THIN}₽`, "размах"), kpi(fmt.rub(s.mean), "среднее")),
    houses.length ? el("div", { style: "margin-top:14px" }, dataTable([
      { title: "Дом", value: (h) => /^https:\/\//.test(h.source || "")
        ? el("a", { href: h.source, rel: "noopener noreferrer", target: "_blank" }, h.house) : h.house, cls: "name" },
      { title: "Рейтинг", value: (h) => h.rating || "—", cls: "txt" },
      { title: "Цель, ₽", num: true, value: (h) => fmt.num(h.target) },
      { title: "Дата", num: true, value: (h) => fmt.date(h.date) },
    ], houses, { cls: "compact" })) : null,
    s.note ? el("p", { class: "card-foot" }, sentence(`Оговорка книги: ${s.note}`)) : null);
}

// Аналоги — только живые на одной базе (EV/EBITDA до МСФО 16 при живых ценах) плюс строка эмитента
// на проформенной EBITDA LTM (`subject_key`) и строки модели из первой строки выпуска (тот же
// знаменатель).
function peersCard(d) {
  const block = d.market.peers_same_base;
  const line_ = d.fair_value.ev_first_line;
  const rows = block.rows.map((r) => ({ ...r, kind: "market" }));
  if (line_) {
    rows.push({ key: "model", title: `${company(d)} — EV модели`, ev: line_.v0, ev_ebitda: line_.ev_ebitda, ebitda_ltm: line_.ebitda_ltm, kind: "model" });
    rows.push({ key: "vstar", title: `${company(d)} — рыночный V*`, ev: line_.v_star, ev_ebitda: line_.ev_ebitda_v_star, ebitda_ltm: line_.ebitda_ltm, kind: "model" });
  }
  return card({ title: "Аналоги на одной базе", sub: `EV/EBITDA до МСФО 16, балансы на ${fmt.date(block.as_of)}, цены живые` },
    dataTable([
      { title: "Компания", value: (r) => r.title, cls: "name" },
      { title: "EV / EBITDA", num: true, value: (r) => isNum(r.ev_ebitda) ? fmt.x(r.ev_ebitda, 2) : (r.missing || "—") },
      { title: "EV, млрд ₽", num: true, value: (r) => isNum(r.ev) ? fmt.num(r.ev, 1) : "—" },
      { title: "Капитализация", num: true, value: (r) => isNum(r.cap) ? fmt.num(r.cap, 1) : "—" },
      { title: "Чистый долг", num: true, value: (r) => isNum(r.net_debt) ? fmt.num(r.net_debt, 1) : "—" },
      { title: "EBITDA LTM", num: true, value: (r) => isNum(r.ebitda_ltm) ? fmt.num(r.ebitda_ltm, 1) : "—" },
      { title: "Цена", num: true, value: (r) => isNum(r.price) ? `${fmt.rub(r.price)} · ${fmt.dateShort(r.price_date)}` : "—" },
    ], rows, {
      rowClass: (r) => (r.kind === "model" ? "is-muted" : r.key === block.subject_key ? "is-pick" : null),
      detail: (r) => (r.note || r.source) ? detailsBlock("Источник и оговорка", [r.note, r.note && r.source ? " " : "", r.source ? `Источник: ${r.source}` : ""].join("")) : null,
    }),
    el("div", { class: "card-foot" },
      el("p", {}, `Строка ${company(d)} — на проформенной EBITDA LTM (с приобретёнными сетями за весь период); мультипликаторы аналогов задают порядок величины, а не цену.`),
      block.basis ? detailsBlock("Как считается база", block.basis) : null));
}

// Конец оси «по одному суждению» называет своё суждение, если в оси их
// несколько (ставка: β и ERP; аренда и касса).
const SHORT_KEYS = { "valuation.erp": "ERP", "valuation.beta_u": "β", "margin.cash_lease_adj_pct": "аренда",
  "financing.operating_cash_pct": "касса" };

function endName(axis, end) {
  if (!axis.keys || axis.keys.length < 2 || axis.low.key === axis.high.key) return "";
  return (SHORT_KEYS[end.key] || String(end.label).replace(/^A-\S+\s*/, "")) + " ";
}

function spreadCard(d) {
  const spread = d.fair_value.judgement_spread;
  if (!spread || !Array.isArray(spread.axes) || !spread.axes.length) {
    return card({ title: "Цена по одному суждению" }, missing("разброс по главным суждениям"));
  }
  const center = d.fair_value.central;
  const all = spread.axes.flatMap((a) => [a.low.price, a.high.price]).concat(center);
  // Запас 6 % до края шкалы: метка конца (14 px, по центру значения) у самого края
  // выходила бы за дорожку на узком экране (375 px — дорожка ≈127 px: 7 / 127 ≈ 5,5 %).
  const dom = [0, Math.ceil((Math.max(...all) * 1.06) / 500) * 500];
  const rowsBox = el("div", { class: "tornado" }, spread.axes.map((a) => el("div", { class: "tn-row" },
    el("div", { class: "tn-name" }, a.title),
    el("div", { class: "tn-bar" },
      barTrack([
        { from: a.low.price, to: Math.min(center, a.high.price), cls: "is-down" },
        { from: Math.max(center, a.low.price), to: a.high.price, cls: "is-up" },
      ].filter((s) => s.to > s.from), dom, { center, marks: [
        { at: a.low.price, cls: "end", tip: { title: a.low.label, rows: [["значение", fmt.bookValue(a.low.value, a.low.unit)], ["точка", fmt.rub(a.low.price)]] } },
        { at: a.high.price, cls: "end", tip: { title: a.high.label, rows: [["значение", fmt.bookValue(a.high.value, a.high.unit)], ["точка", fmt.rub(a.high.price)]] } },
      ] })),
    el("div", { class: "tn-vals" }, `${fmt.num(a.low.price)} … ${fmt.num(a.high.price)}${THIN}₽`,
      el("span", { class: "hint" }, `${endName(a, a.low)}${fmt.bookValue(a.low.value, a.low.unit)} / ${endName(a, a.high)}${fmt.bookValue(a.high.value, a.high.unit)}`)))));
  return card({ title: "Цена по одному суждению при остальных в центре",
    sub: `Главные суждения книги по одному, каждое на краях своего диапазона; вертикаль — точка при центральных значениях ${fmt.rub(center)}. Это не полоса: полоса двигает все суждения сразу.` },
  rowsBox,
  el("div", { class: "tn-row tn-axis-row", "aria-hidden": "true" }, el("span"),
    el("div", { class: "tn-axis" }, el("span", {}, fmt.rub(dom[0])), el("span", {}, fmt.rub(dom[1]))), el("span")),
  spread.missing && spread.missing.length ? el("p", { class: "card-foot" }, `Не найдены в таблице суждений: ${spread.missing.join(", ")}.`) : null);
}

/* ── экран «Расчёт» ── */

// Раздел экрана: подпись-надстрочник и сетка карточек.
function rowSection(title, ...cards) {
  return el("div", { class: "section" },
    el("span", { class: "eyebrow row-label" }, title), el("div", { class: "grid" }, ...cards));
}

function screenModel(d) {
  const root = el("div", { class: "screen" },
    screenHead("Расчёт",
      "Как из допущений получается цена",
      "Стоимость бизнеса оценивается первой — сеткой сценариев с явными вероятностями; цена акции — внутренняя стоимость за вычетом требований и дисконта за управление. Путь выручки стоит на сети по форматам и периметре покупок; стратегия компании — сверка пути, а не вход. Все числа — из выпуска."));
  root.append(el("div", { class: "grid" }, flowCard(d)));
  root.append(el("div", { class: "grid section" }, layersCard(d)));
  root.append(el("div", { class: "grid section" }, mappingCard(d), governanceCard(d)));
  root.append(rowSection("Сеть, периметр и стратегия", networkCard(d)));
  root.append(el("div", { class: "grid section" }, perimeterCard(d)));
  root.append(el("div", { class: "grid section" }, integrationCard(d)));
  root.append(el("div", { class: "grid section" }, strategyCard(d)));
  root.append(rowSection("Сценарии", regimesCard(d), varianceCard(d)));
  root.append(el("div", { class: "grid section" }, worldsCard(d)));
  root.append(el("div", { class: "grid section" }, gridCard(d)));
  root.append(el("div", { class: "grid section" }, scenariosCard(d)));
  return root;
}

function flowCard(d) {
  const fv = d.fair_value;
  const head = fv.headline;
  const grid = d.grid || [];
  const worlds = new Set(grid.map((c) => c.world)).size;
  const regimes = new Set(grid.map((c) => c.regime)).size;
  const capex = new Set(grid.map((c) => c.capex)).size;
  const v0 = Object.values(d.layers || {}).map((layer) => layer.v0).filter(isNum);
  const [h0, h1] = d.meta.horizon;
  const steps = [
    ["Сетка сценариев", `${fmt.num(grid.length)} ${plural(grid.length, ["клетка", "клетки", "клеток"])}`,
      `${worlds} ${plural(worlds, ["мир", "мира", "миров"])} ставок × ${regimes} ${plural(regimes, ["режим", "режима", "режимов"])} маржи × `
      + `${capex} ${plural(capex, ["уровень", "уровня", "уровней"])} capex; APV, шаг — ${modelUnit(d).one}, ${periodLabel(h0)} — ${periodLabel(h1)}, база ${d.meta.basis}.`],
    ["Слои", v0.length ? `V0 ${fmt.num(Math.min(...v0), 0)}–${fmt.num(Math.max(...v0), 0)}` : "—",
      "Ожидаемая стоимость бизнеса при разных весах миров: рыночные ставки как есть, вменённые рынком веса, свой макро-взгляд (млрд ₽)."],
    ["Цена акции", `g ${fmt.pct(d.meta.governance_discount, 2)}`,
      `Внутренняя стоимость: (V0${NBSP}−${NBSP}D)·(1${NBSP}−${NBSP}g) на ${fmt.num(d.meta.shares_mln, 3)} млн акций в обращении; D — требования моста, g — дисконт за управление.`],
    ["Точка", `${fmt.num(fv.low)} → ${fmt.num(fv.central)} → ${fmt.num(fv.high)}`,
      `Все суждения в центре: низ (рыночные ставки) + λ·(верх − низ), λ = ${fmt.num(fv.own_macro_confidence, 2)}; ₽ на акцию.`],
    ["Полоса по суждениям", head ? `медиана ${fmt.num(head.median)}` : "нет в выпуске",
      head ? `${fmt.num(head.draws)} прогонов по ${fmt.num(head.contributions.length)} суждениям в их диапазонах; печать шагом ${fmt.num(head.print_step)} ₽ → ${fmt.rub(head.printed_median)}.` : "Прогонов по суждениям в выпуске нет (быстрая сборка)."],
  ];
  return card({ title: "Пять шагов от допущений к цене" },
    el("div", { class: "flow" }, steps.map(([t, big, p]) => el("div", { class: "flow-step" },
      el("h3", {}, t), el("div", { class: "big" }, big), el("p", {}, p)))));
}

function layersCard(d) {
  const L = d.layers;
  const order = ["macro_neutral", "market_implied", "analytical"].filter((k) => L[k]);
  const mix = d.mixes && d.mixes.headline;
  return card({ title: "Слои: стоимость бизнеса, требования и цена",
    sub: "Внутренняя стоимость слоя — (V0 − D)·(1 − g) на акцию; P10–P90 — разброс цены по клеткам сетки при весах слоя." },
  dataTable([
    { title: "Слой", value: (k) => el("span", { tip: { title: L[k].title, rows: [["веса миров", worldWeights(d, k)]] } }, layerTitle(d, k),
      el("span", { class: "hint" }, worldWeights(d, k))), cls: "name" },
    { title: "V0, млрд ₽", num: true, value: (k) => fmt.num(L[k].v0, 1) },
    { title: "D, млрд ₽", num: true, value: (k) => fmt.num(L[k].claims, 1) },
    { title: "Внутренняя, ₽", num: true, value: (k) => el("strong", {}, fmt.num(L[k].intrinsic)) },
    { title: "Клетки P10–P90, ₽", num: true, value: (k) => `${fmt.num(L[k].p10)}–${fmt.num(L[k].p90)}` },
    { title: "P(выше рынка)", num: true, value: (k) => fmt.pct(L[k].p_above_market, 0) },
    { title: "P(капитал ≤ 0)", num: true, value: (k) => fmt.pct(L[k].p_equity_nonpositive, 0) },
  ], order),
  el("p", { class: "card-foot" }, `Точка при λ = ${fmt.num(d.fair_value.own_macro_confidence, 2)}: ${fmt.rub(d.fair_value.central)} (${fmt.rub(d.fair_value.low)} → ${fmt.rub(d.fair_value.high)} по оси ставок).`,
    mix ? ` ${upperFirst(clean(mix.title))}; V0 смеси ${fmt.bn(mix.v0, 1)}.` : ""));
}

// Отображение V0 → цена — внутренняя стоимость (`fair_value.method`): опционной части капитала,
// страйка и калибровки σ у модели нет.
function mappingCard(d) {
  const fv = d.fair_value;
  const gate = (d.gates || []).find((g) => g.key === "limited_liability");
  const mix = d.mixes && d.mixes.headline;
  return card({ title: "От стоимости бизнеса к цене акции", span: 5,
    sub: `Метод — внутренняя стоимость (${fv.method}): цена = (V0${NBSP}−${NBSP}D)·(1${NBSP}−${NBSP}g) / акции.` },
  el("div", { class: "kpis" },
    mix ? kpi(fmt.bn(mix.v0, 1), "V0 смеси заголовка (= V0 точки)") : null,
    kpi(fmt.pct(d.meta.governance_discount, 2), "дисконт за управление g"),
    kpi(`${fmt.num(d.meta.shares_mln, 3)} млн`, "акций в обращении"),
    kpi(fmt.pct(fv.p_equity_nonpositive, 0), "P(капитал ≤ 0) по клеткам")),
  el("div", { class: "note-box", style: "margin-top:16px" },
    gate ? [el("strong", {}, "Гейт «капитал близок к нулю» сработал. "), sentence(upperFirst(gate.message || gate.explanation || "нужна новая версия книги со сменой метода"))]
      : fv.headline && fv.headline.draws
        ? [el("strong", {}, "Гейт «капитал близок к нулю» не сработал: "), "доля прогонов полосы с V0 у порога требований ниже порога книги — внутренняя стоимость остаётся методом."]
        : "Гейт «капитал близок к нулю» считается по прогонам полосы; в выпуске без полосы его нет."));
}

// Дисконт за управление — подписанные каналы листа (D5): сумма со знаком = g.
// Рядом — «g на охвате 850oa»: те же каналы без тех, что вне охвата шкалы 850oa.
function governanceCard(d) {
  const g = d.governance;
  if (!g || !Array.isArray(g.components)) return card({ title: "Дисконт за управление", span: 7 }, empty("Разложения g в выпуске нет."));
  let run = 0;
  const steps = g.components.map((c) => { const from = run; run += c.signed; return { c, from, to: run }; });
  const lo = Math.min(0, ...steps.map((s) => Math.min(s.from, s.to)));
  const hi = Math.max(g.sum_signed, ...steps.map((s) => Math.max(s.from, s.to)));
  // Имя канала — до двоеточия и без пояснения в скобках в конце («(а)–(б)» — часть имени).
  const short = (name) => String(name).replace(/:.*$/, "").replace(/\s+\([^()]*\)\s*$/, "");
  const ps = g.price_sensitivity;
  return card({ title: `Дисконт за управление: ${fmt.pct(g.discount, 2)}`, span: 7,
    sub: "Подписанные взаимоисключающие каналы листа книги; их сумма со знаком — g. Строка канала — п.п. цены капитала." },
  el("div", { class: "wf" },
    steps.map(({ c, from, to }) => el("div", { class: "wf-row" },
      el("span", { class: "wf-name" }, short(c.name), c.in_850oa_scope ? null : el("span", { class: "hint" }, "вне охвата 850oa")),
      el("span", { class: "wf-bar" }, barTrack([{ from, to, cls: c.signed < 0 ? "is-up" : "is-down",
        tip: { title: c.name, rows: [["вклад", fmt.pp(c.signed, 2)]], note: c.basis } }], [lo, hi])),
      el("span", { class: "wf-val" }, fmt.pp(c.signed, 2)))),
    el("div", { class: "wf-row is-total" },
      el("span", { class: "wf-name" }, "g — сумма со знаком"),
      el("span", { class: "wf-bar" }, barTrack([{ from: 0, to: g.sum_signed, cls: "is-total" }], [lo, hi])),
      el("span", { class: "wf-val" }, fmt.pct(g.sum_signed, 2))),
    el("div", { class: "wf-row" },
      el("span", { class: "wf-name" }, short(g.on_850oa_scope_title)),
      el("span", { class: "wf-bar" }, barTrack([{ from: 0, to: g.on_850oa_scope, cls: "is-total" }], [lo, hi])),
      el("span", { class: "wf-val" }, fmt.pct(g.on_850oa_scope, 2)))),
  el("div", { class: "card-foot" },
    ps && isNum(ps.price_low) ? el("p", {}, `Цена точки на краях оси g: при g = ${fmt.bookValue(ps.low, "pct")} — ${fmt.rub(ps.price_low)}, при g = ${fmt.bookValue(ps.high, "pct")} — ${fmt.rub(ps.price_high)}.`) : null,
    (g.out_of_850oa_scope || []).length ? el("p", {}, sentence(`Вне охвата шкалы 850oa: ${g.out_of_850oa_scope.map(short).join("; ")}`)) : null,
    detailsBlock("Основания каналов", el("dl", { class: "equations" }, g.components.map((c) => [el("dt", {}, fmt.pp(c.signed, 2)), el("dd", {}, `${c.name}. ${ruText(c.basis)}`)])))));
}

function regimesCard(d) {
  const rp = d.regime_prob;
  if (!rp) return card({ title: "Режимы маржи", span: 6 }, empty("Нет в выпуске."));
  const order = ["stress", "floor", "partial", "full"].filter((k) => k in rp.unconditional);
  const colors = { stress: "var(--neg)", floor: "var(--axis)", partial: "var(--third)", full: "var(--model)" };
  const bar = el("div", { class: "stackbar" }, order.map((k) => el("i", {
    style: `flex:${rp.unconditional[k]} 1 0;background:${colors[k]}`,
    tip: { title: rp.titles[k] || k, rows: [["вероятность", fmt.pct(rp.unconditional[k], 2)]] } })));
  const same = Object.values(rp.given_world || {}).every((w) => order.every((k) => Math.abs(w[k] - rp.unconditional[k]) < 1e-9));
  return card({ title: "Режимы маржи EBITDA", span: 6,
    sub: same ? "Одна конвенция: вероятности режимов одинаковы во всех мирах и слоях." : "Вероятности режимов зависят от мира — см. таблицу." },
  bar,
  el("div", { class: "legend" }, order.map((k) => el("span", {}, el("i", { class: "key", style: `background:${colors[k]}` }),
    `${rp.titles[k] || k} ${fmt.pct(rp.unconditional[k], 2)}`))),
  referenceClass(rp, order),
  el("p", { class: "card-foot" },
    `Обновление по факту отчёта (A-P2u): разброс правила ${fmt.num(rp.sigma_pp * 100, 1)} п.п.; `,
    Object.keys(rp.observations || {}).length ? `наблюдений внесено: ${Object.keys(rp.observations).join(", ")}.` : "наблюдений не внесено — к нау-касту правило не подключено."));
}

// Референс-класс режимов маржи (блок книги `reference_class_margin`; справочно):
// доли исходов по режимам с 95 % интервалом против вероятностей книги.
function referenceClass(rp, order) {
  const rc = rp.reference_class;
  if (!rc || !rc.share) return null;
  const ci = rc.share_ci95 || {};
  const targets = rc.mean_target_by_regime || {};
  const withTargets = Object.keys(targets).length > 0;
  return el("div", { style: "margin-top:14px" },
    el("span", { class: "tile-label" }, `Референс-класс: ${fmt.num(rc.n)} случаев — справочно, движок его не читает`),
    dataTable([
      { title: "Режим", value: (k) => rp.titles[k] || k, cls: "name" },
      { title: "Книга", num: true, value: (k) => fmt.pct(rp.unconditional[k], 1) },
      { title: "Класс", num: true, value: (k) => isNum(rc.share[k]) ? fmt.pct(rc.share[k], 0) : "—" },
      { title: "95 % интервал", num: true, value: (k) => Array.isArray(ci[k])
        ? `${fmt.num(ci[k][0] * 100, 0)}–${fmt.num(ci[k][1] * 100, 0)}${THIN}%` : "—" },
      withTargets ? { title: "Средняя цель LT в классе", num: true, value: (k) => isNum(targets[k]) ? fmt.pct(targets[k], 2) : "—" } : null,
    ].filter(Boolean), order.filter((k) => k in rc.share), { cls: "compact" }),
    el("p", { class: "muted small", style: "margin-top:6px" },
      `Ожидаемая долгосрочная маржа: книга ${fmt.pct(rc.e_m_lt_book, 2)}, класс ${fmt.pct(rc.e_m_lt, 2)}.`),
    rc.note ? detailsBlock("Правило класса", ruText(rc.note)) : null);
}

function worldsCard(d) {
  const W_ = d.worlds || {};
  const keys = Object.keys(W_);
  if (!keys.length) return card({ title: "Миры ставок" }, empty("Нет в выпуске."));
  const colors = ["var(--model)", "var(--third)", "var(--market)"];
  const tenor = (k) => (k === "LT" ? 25 : Number(k));
  const series = keys.map((k, i) => ({
    name: `${k} — ${W_[k].title}`, color: colors[i % colors.length], dots: true,
    points: Object.entries(W_[k].zero_curve).map(([t, v]) => [tenor(t), v]).sort((a, b) => a[0] - b[0]),
  }));
  const obs = d.live && d.live.observed_curve;
  if (obs && Object.keys(obs).length) {
    series.push({ name: `кривая ОФЗ ${fmt.date(d.live.observed_curve_date)}`, color: "var(--ink)", width: 1.5, dots: false,
      points: Object.entries(obs).map(([t, v]) => [Number(t), v]).sort((a, b) => a[0] - b[0]) });
  }
  const plot = linesChart(series, { height: 250, yFmt: (v) => fmt.num(v * 100, 0) + THIN + "%", tipFmt: (v) => fmt.pct(v, 2),
    xTicks: [1, 3, 5, 10, 25], xFmt: (t) => (t === 25 ? "LT" : `${fmt.num(t)} г.`), xMin: 0, xMax: 26, xMap: Math.sqrt,
    label: "Бескупонные кривые миров ставок" });
  const fig = withTable(plot, () => dataTable([
    { title: "Мир", value: (k) => `${k} — ${W_[k].title}`, cls: "name" },
    ...Object.keys(W_[keys[0]].zero_curve).map((t) => ({ title: t === "LT" ? "LT" : `${t} г.`, num: true, value: (k) => fmt.pct(W_[k].zero_curve[t], 2) })),
  ], keys));
  const by = d.fair_value.by_world || {};
  return card({ title: "Миры ставок", tools: fig.button,
    sub: "Мир — одна согласованная макротраектория: ставки, инфляция и дисконт. Веса миров задают слои." },
  el("div", { class: "split" },
    el("div", {}, legend(series.map((s, i) => [i < keys.length ? ["key-line key-model", "key-line key-third", "key-line key-market"][i % 3] : "key-line key-ink", s.name])), fig.box),
    dataTable([
      { title: "Мир", value: (k) => el("span", {}, `${k} — ${W_[k].title}`), cls: "name" },
      { title: "Свой вес", num: true, value: (k) => fmt.pct(W_[k].probability, 0) },
      { title: "Вменённый рынком", num: true, value: (k) => fmt.pct(W_[k].probability_market, 0) },
      { title: "Инфляция LT", num: true, value: (k) => fmt.pct(W_[k].lt_inflation, 1) },
      { title: "Цена мира", num: true, value: (k) => isNum(by[k]) ? fmt.rub(by[k]) : "—" },
    ], keys)),
  el("p", { class: "card-foot" }, "Цена мира — оценка при весе этого мира 100 %. Низ оси ставок — рыночный мир, а не нижняя граница миров: миры не упорядочены по цене."));
}

// Цвет клетки — порядок её цены к рыночной (×0,5 … ×3): шкала от выпуска, а не
// от литералов витрины.
const HEAT_STEPS = [0.5, 0.75, 1, 1.5, 2, 3];

function heatClass(price, market) {
  if (!isNum(price) || price <= 0 || !isNum(market) || market <= 0) return "h0";
  let i = 0;
  while (i < HEAT_STEPS.length && price >= HEAT_STEPS[i] * market) i++;
  return `h${i + 1}`;
}

function gridCard(d) {
  const grid = d.grid || [];
  if (!grid.length) return card({ title: "Сетка сценариев" }, empty("Нет в выпуске."));
  const market = d.market.price;
  const worlds = [...new Set(grid.map((c) => c.world))];
  const regimes = ["stress", "floor", "partial", "full"].filter((r) => grid.some((c) => c.regime === r));
  const capex = ["low", "base", "high"].filter((x) => grid.some((c) => c.capex === x));
  const titles = (d.regime_prob && d.regime_prob.titles) || {};
  const panels = worlds.map((w) => el("div", {},
    el("div", { class: "heat-title" }, el("strong", {}, `${w} — ${(d.worlds[w] || {}).title || ""}`),
      el("span", {}, isNum((d.worlds[w] || {}).probability) ? `свой вес ${fmt.pct(d.worlds[w].probability, 0)}` : "")),
    el("table", { class: "heat" },
      el("thead", {}, el("tr", {}, el("th", { class: "row-h" }, "режим / capex"), capex.map((x) => el("th", {}, CAPEX_NAMES[x] || x)))),
      el("tbody", {}, regimes.map((r) => el("tr", {},
        el("th", { class: "row-h" }, titles[r] || r),
        capex.map((x) => {
          const c = grid.find((g) => g.world === w && g.regime === r && g.capex === x);
          if (!c) return el("td", {}, "—");
          const flags = [c.over_credit_limit ? "⚠" : "", c.equity <= 0 ? "∅" : ""].join("");
          return el("td", { class: heatClass(c.price_published, market), tip: { title: `${w} · ${titles[r] || r} · capex ${CAPEX_NAMES[x] || x}`,
            rows: [["цена клетки", fmt.rub(c.price_published)], ["вероятность", fmt.pct(c.probability, 2)], ["EV", fmt.bn(c.ev, 1)],
              ["EV/EBITDA", fmt.x(c.ev_ebitda, 2)], ["капитал по DCF", fmt.bn(c.equity, 1)], ["макс. ЧД/EBITDA", fmt.x(c.max_leverage, 2)],
              ["макс. валовой долг", fmt.bn(c.max_gross_debt, 0)]],
            note: [c.over_credit_limit ? "путь долга выше лимита линий" : "", c.equity <= 0 ? "капитал по DCF ≤ 0" : ""].filter(Boolean).join("; ") || null } },
          flags ? el("span", { class: "flag", "aria-label": "флаг" }, flags) : null,
          el("span", { class: "p" }, fmt.num(c.price_published)),
          el("span", { class: "w" }, fmt.pct(c.probability, 1)));
        })))))));
  const steps = ["var(--seq-0)", "var(--seq-1)", "var(--seq-2)", "var(--seq-3)", "var(--seq-4)", "var(--seq-5)", "var(--seq-6)"];
  const scaleBar = el("div", { class: "scale" }, el("span", {}, `цена клетки к рынку (${fmt.rub(market)}):`),
    ...steps.flatMap((c, i) => [el("i", { style: `background:${c}` }),
      i === 0 ? `<${NBSP}×${fmt.num(HEAT_STEPS[0], 2)}` : `×${fmt.num(HEAT_STEPS[i - 1], 2)}${i === steps.length - 1 ? "+" : ""}`]),
    el("span", { class: "gap" }, "⚠ долг выше лимита линий · ∅ капитал по DCF ≤ 0"));
  return card({ title: `Сетка: ${fmt.num(grid.length)} сценарных клеток`,
    sub: "Каждая клетка — полный расчёт APV при своём мире, режиме маржи и уровне capex; в клетке — цена акции (внутренняя стоимость) и вероятность. Цвет — порядок цены к рынку." },
  el("div", { class: "heat-wrap" }, panels), scaleBar);
}

function scenariosCard(d) {
  const S = d.scenarios || [];
  if (!S.length) return card({ title: "Сценарии" }, empty("Нет в выпуске."));
  return card({ title: "Сценарные клетки — справочно",
    sub: "Именованные клетки сетки с весом своей группы. Это иллюстрации механики, а не заголовок: одна клетка не несёт неопределённости суждений." },
  el("div", { class: "grid" }, S.map((s) => el("div", { class: "span-4 scenario" },
    el("div", { class: "scenario-head" }, el("strong", {}, s.title), badge(`вес группы ${fmt.pct(s.weight, 0)}`, "model")),
    el("p", { class: "muted small" }, s.narrative),
    el("div", { class: "kpis", style: "margin-top:10px" },
      kpi(fmt.rub(s.price_published), "цена клетки"),
      kpi(fmt.bn(s.ev, 0), `EV · ${fmt.x(s.ev_ebitda_ltm, 2)} EBITDA`),
      kpi(fmt.pct(s.terminal_share, 0), "доля терминала в EV"),
      kpi(fmt.pct(s.r_long, 1), "ставка дисконта LT"))))));
}

function varianceCard(d) {
  const v = d.variance && d.variance.ev;
  if (!v) return card({ title: "Что разводит клетки сетки", span: 6 }, empty("Нет в выпуске."));
  const names = { margin_regime: "режим маржи", capex: "уровень capex", world: "мир ставок" };
  const rows = Object.entries(v).sort((a, b) => b[1] - a[1]);
  const max = Math.max(...rows.map((r) => r[1]));
  return card({ title: "Что разводит клетки сетки", span: 6,
    sub: "Доля дисперсии EV по сетке. Это описание сценариев сетки, а не неопределённость печатаемого числа — её показывает полоса." },
  el("div", { class: "hbars" }, rows.map(([k, share]) => el("div", { class: "hbar" },
    el("span", { class: "hbar-name" }, names[k] || k),
    el("span", { class: "hbar-track" }, el("i", { class: "hbar-fill", style: `width:${(100 * share) / max}%;background:var(--axis)` })),
    el("span", { class: "hbar-val" }, fmt.pct(share, 0))))));
}

/* ── сеть, периметр, стратегия ── */

const BASIS_NAMES = { reported: "отчёт", pro_forma: "проформа", estimate: "оценка" };
let NETWORK_PERIOD = null;

// Смесь клеток, по которой выпуск печатает ожидания пути, — та, что даёт точку
// (`mixes.headline`); смесь сетки — запасная.
function pathMix(block) {
  return (block && (block.headline || block.grid)) || null;
}

// «a → b» — факт якоря и путь клетки в одной ячейке.
function arrow(a, b) {
  return `${a} → ${b}`;
}

// Сеть по форматам: факт на дату якоря и ожидаемый путь смеси заголовка (площадь, магазины,
// выручка, плотность, открытия).
function networkCard(d) {
  const n = d.network;
  const mix = n && pathMix(n.expected);
  if (!n || !n.anchor || !mix) return card({ title: "Сеть по форматам" }, empty("Блока сети в выпуске нет."));
  const a = n.anchor;
  const at = a.period;
  const rows = [...(mix.rows || []), ...(mix.lt ? [mix.lt] : [])];
  if (!rows.length) return card({ title: "Сеть по форматам" }, empty("Пути сети в выпуске нет."));
  const tail = String(rows[0].period).slice(4);
  const options = rows.filter((r, i) => i === rows.length - 1 || String(r.period).slice(4) === tail)
    .map((r) => ({ value: r.period, label: r === mix.lt ? `${periodShort(r.period)} · LT` : periodShort(r.period) }));
  if (!NETWORK_PERIOD || !rows.some((r) => r.period === NETWORK_PERIOD)) NETWORK_PERIOD = rows[0].period;
  const units = n.units || {};
  const factPeriods = Object.keys(a.total.revenue || {}).sort();
  const pathRows = mix.rows || [];
  const cats = [...factPeriods, ...pathRows.map((r) => r.period)];
  const mini = (title, fact, path, f) => el("div", { class: "mini" },
    el("span", { class: "tile-label" }, title),
    linesChart([
      { name: "факт", color: "var(--ink)", dots: true, r: 3.5, points: fact },
      { name: "путь модели", color: "var(--model)", dots: true, r: 3, points: path },
    ], { xType: "band", categories: cats, height: 150, left: 50, yFmt: f, tipFmt: f, xFmt: periodShort }));
  const body = el("div");
  const draw = () => {
    const row = rows.find((r) => r.period === NETWORK_PERIOD) || rows[0];
    const seg = (id) => (row.segments || {})[id] || {};
    const basis = (s) => {
      const b = (s.revenue_basis || {})[at];
      const se = (s.revenue_se || {})[at];
      return b ? `${BASIS_NAMES[b] || b}${isNum(se) && se > 0 ? ` ± ${fmt.num(se, 1)}` : ""}` : "";
    };
    const table = dataTable([
      { title: "Формат", value: (s) => el("span", {}, s.name, el("span", { class: "hint" }, basis(s))), cls: "name" },
      { title: `Выручка ${periodShort(at)}`, num: true, value: (s) => fmt.num((s.revenue || {})[at], 1) },
      { title: `Выручка ${periodShort(row.period)}`, num: true, value: (s) => el("strong", {}, fmt.num(seg(s.id).revenue, 1)) },
      { title: "Рост г/г", num: true, value: (s) => isNum(seg(s.id).growth) ? fmt.signedPct(seg(s.id).growth, 1) : "—" },
      { title: "Площадь, тыс. м²", num: true, value: (s) => isNum(s.area_end) ? arrow(fmt.num(s.area_end, 0), fmt.num(seg(s.id).area_end, 0)) : "—" },
      { title: "Магазины", num: true, value: (s) => isNum(s.stores_end) ? arrow(fmt.num(s.stores_end), fmt.num(seg(s.id).stores_est)) : "—" },
      { title: "Плотность", num: true, value: (s) => isNum((s.density || {})[at]) ? arrow(fmt.num(s.density[at], 0), fmt.num(seg(s.id).density, 0)) : "—" },
      { title: `Открытия ${periodShort(row.period)}`, num: true, value: (s) => isNum(seg(s.id).opened_area) && seg(s.id).opened_area > 0
        ? `${fmt.num(seg(s.id).opened_area, 1)} · ${fmt.num(seg(s.id).opened_stores_est)}${THIN}маг.` : "—" },
    ], a.segments);
    const tot = row.total || {};
    body.replaceChildren(table, el("div", { class: "kpis", style: "margin-top:14px" },
      kpi(arrow(fmt.num(a.total.revenue[at], 0), fmt.num(tot.revenue, 0)), `выручка группы, ${units.revenue || "млрд ₽"}`),
      kpi(arrow(fmt.num(a.total.area_end, 0), fmt.num(tot.area_end, 0)), `площадь, ${units.area || "тыс. м²"}`),
      kpi(arrow(fmt.num(a.total.stores_end), fmt.num(tot.stores_est)), "магазинов (путь — оценка)"),
      kpi(`${fmt.num(tot.opened_area, 1)} / ${fmt.num(tot.closed_area, 1)}`, `открыто / закрыто в ${periodShort(row.period)}, тыс. м²`)));
  };
  draw();
  return card({ title: "Сеть по форматам: факт и путь модели",
    sub: `Факт — на ${periodLabel(at)} (${fmt.date(a.facts_date)}); путь — ожидание смеси заголовка на выбранное ${modelUnit(d).one}. Плотность — ${units.density || "выручка на м²"}.` },
  el("div", { class: "minis" },
    mini(`Выручка группы, ${units.revenue || "млрд ₽"}`, factPeriods.map((p) => [p, a.total.revenue[p]]),
      pathRows.map((r) => [r.period, r.total.revenue]), (v) => fmt.num(v, 0)),
    mini(`Площадь, ${units.area || "тыс. м²"}`, [[at, a.total.area_end]],
      pathRows.map((r) => [r.period, r.total.area_end]), (v) => fmt.num(v, 0)),
    mini("Магазинов (путь — оценка)", [[at, a.total.stores_end]],
      pathRows.map((r) => [r.period, r.total.stores_est]), (v) => fmt.num(v, 0))),
  el("div", { class: "pick-row", style: "margin-top:18px" }, el("span", { class: "muted" }, "Путь на:"),
    chooser(options, NETWORK_PERIOD, (v) => { NETWORK_PERIOD = v; draw(); }, "Период пути сети")),
  body,
  el("div", { class: "card-foot" },
    n.notes ? el("p", {}, sentence(upperFirst(`магазины пути — ${n.notes.stores_est}`))) : null,
    n.notes && n.notes.expectation ? el("p", {}, sentence(upperFirst(n.notes.expectation))) : null));
}

// Короткая дата контроля сделки: «2026-06-02» → 02.06.2026, «2021-08» → 08.2021,
// «2026-Q1» → «1 кв. 2026»; полный текст — во второй строке таблицы.
function controlDate(text) {
  const s = String(text || "");
  let m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s);
  if (m) return fmt.date(`${m[1]}-${m[2]}-${m[3]}`);
  m = /^(\d{4})-Q([1-4])/.exec(s);
  if (m) return periodLabel(`${m[1]}Q${m[2]}`);
  m = /^(\d{4})-(\d{2})/.exec(s);
  return m ? `${m[2]}.${m[1]}` : "—";
}

function perimeterCard(d) {
  const p = d.perimeter;
  if (!p || !Array.isArray(p.deals)) return card({ title: "Периметр и сделки" }, empty("Блока периметра в выпуске нет."));
  const deals = p.deals;
  const names = Object.fromEntries(deals.map((x) => [x.id, x.name]));
  const breakName = (item) => {
    const [id, ...rest] = String(item).split(" ");
    const name = names[id] || (id === "domlenta" ? names.obi_domlenta : null);
    return name ? `${name}${rest.length ? " " + rest.join(" ") : ""}` : String(item);
  };
  const contribution = (x) => {
    const c = x.contribution;
    if (!c) return "—";
    return `${fmt.num(c.revenue, 1)} / ${isNum(c.pbt) ? fmt.num(c.pbt, 2) : "—"}`;
  };
  const gw = p.goodwill || {};
  const cash = p.acquisition_cash_1h2026 || {};
  const at = periodLabel(d.network && d.network.anchor ? d.network.anchor.period : "");
  const ok = (p.integration || {}).okey;
  return card({ title: "Периметр: сделки и разрывы истории",
    sub: "Покупки группы по отчётности: дата консолидации, цена, принятый долг, гудвил и вклад с даты покупки (выручка / прибыль до налога, млрд ₽). Будущие сделки модель не содержит." },
  dataTable([
    { title: "Сделка", value: (x) => el("span", {}, x.name,
      x.related_party === true ? el("span", { class: "hint" }, "связанная сторона") : x.format ? el("span", { class: "hint" }, x.format) : null), cls: "name" },
    { title: "Контроль", num: true, value: (x) => controlDate(x.control) },
    { title: "Цена, млрд ₽", num: true, value: (x) => isNum(x.consideration) ? fmt.num(x.consideration, 2) : "—" },
    { title: "Принятый долг", num: true, value: (x) => isNum(x.assumed_debt) ? fmt.num(x.assumed_debt, 1) : "—" },
    { title: "Гудвил", num: true, value: (x) => isNum(x.goodwill) ? fmt.num(x.goodwill, 1) : "—" },
    { title: "Вклад: выручка / прибыль", num: true, value: (x) => contribution(x) },
  ], deals, { detail: (x) => detailsBlock("Подробнее", [
    x.control ? `Контроль: ${x.control}.` : "",
    x.paid ? ` Оплата: ${x.paid}.` : "",
    x.contribution ? ` Вклад — ${x.contribution.period}${isNum(x.contribution.pbt) ? "" : `; прибыль: ${x.contribution.pbt}`}.` : "",
    x.contribution_note ? ` ${x.contribution_note}.` : "",
    x.related_note ? ` ${upperFirst(x.related_note)}.` : "",
    x.pro_forma_note ? ` Проформа: ${x.pro_forma_note}.` : "",
    x.src ? ` Источник: ${x.src}.` : ""].join("").trim()) }),
  el("div", { class: "kpis", style: "margin-top:16px" },
    isNum(gw.opening) ? kpi(arrow(fmt.num(gw.opening, 1), fmt.num(gw.closing, 1)), `гудвил за ${at}, млрд ₽ (покупки ${modelUnit(d).gen})`) : null,
    isNum(cash.total) ? kpi(fmt.bn(cash.total, 2), `деньги на покупки за ${at} (ОДДС)`) : null,
    ok && isNum(ok.assumed_debt) ? kpi(fmt.bn(ok.assumed_debt, 1), `принятый долг: ${ok.name}`) : null),
  el("div", { class: "card-foot" },
    el("p", {}, "Разрывы периметра по периодам модели (история до них несравнима без проформы): ",
      Object.entries(p.breaks_halves || {}).map(([h, items]) => `${periodLabel(h)} — ${items.map(breakName).join(", ")}`).join("; "), "."),
    gw.src ? el("p", {}, `Гудвил и деньги на покупки — ${gw.src}; ${cash.src || ""}.`) : null));
}

// Интеграция покупок: сближение плотности приобретённых гипермаркетов с гипермаркетами группы на
// одной базе площади (выпуск — отношение), вклад и убыток DIY после ребрендинга, интеграционный
// capex и запертые убытки.
function integrationCard(d) {
  const p = d.perimeter;
  const it = p && p.integration;
  if (!it || !it.okey) return card({ title: "Интеграция покупок" }, empty("Блока интеграции в выпуске нет."));
  const ok = it.okey, diy = it.domlenta || {};
  const path = pathMix(it.expected) || [];
  const hyper = ((d.network && d.network.anchor && d.network.anchor.segments) || []).find((s) => s.id === "hyper");
  const hyperName = hyper ? hyper.name : "гипермаркеты группы";
  const byKey = (obj) => Object.entries(obj || {}).sort(([a], [b]) => a.localeCompare(b));
  const key = (k) => (/^\d{4}H\d$/.test(k) ? periodShort(k) : k);
  const anchorPts = Object.entries(ok.density_ratio_anchor || {}).sort();
  const cats = [...anchorPts.map(([k]) => k), ...path.map((r) => r.period)];
  const plot = linesChart([
    { name: "факт", color: "var(--ink)", dots: true, r: 3.5, points: anchorPts },
    { name: "путь модели", color: "var(--model)", dots: true, r: 3, points: path.map((r) => [r.period, r.okey_to_hyper_density]) },
  ], { xType: "band", categories: cats, height: 190, left: 46, yFmt: (v) => fmt.num(v, 2), tipFmt: (v) => fmt.num(v, 3), xFmt: periodShort,
    label: "Плотность приобретённых гипермаркетов к гипермаркетам группы" });
  const fig = withTable(plot, () => dataTable([
    { title: "Период", value: (r) => periodLabel(r.period), cls: "name" },
    { title: "Плотность к гипер.", num: true, value: (r) => fmt.num(r.okey_to_hyper_density, 3) },
    { title: "Выручка DIY, млрд ₽", num: true, value: (r) => fmt.num(r.diy_revenue, 1) },
    { title: "Интеграционный capex", num: true, value: (r) => fmt.num(r.capex_integration, 1) },
    { title: "Запертые убытки", num: true, value: (r) => fmt.num(r.acquired_nol_pool, 1) },
  ], path));
  const press = ok.density_press_2025 || {};
  const nwc = ok.working_capital_at_acquisition || {};
  const nol = it.acquired_nol || {};
  const last = path[path.length - 1] || {};
  return card({ title: "Интеграция покупок", tools: fig.button,
    sub: `${ok.name}: выручка на метр эффективной площади относительно сегмента «${hyperName}» — на одной базе площади.` },
  el("div", { class: "split wide-left" },
    el("div", {}, legend([["key-line key-ink", "факт"], ["key-line key-model", "путь модели (смесь заголовка)"]]), fig.box),
    el("div", { class: "kpis" },
      kpi(arrow(fmt.num(anchorPts.length ? anchorPts[anchorPts.length - 1][1] : null, 2), fmt.num(last.okey_to_hyper_density, 2)),
        `плотность к гипермаркетам: ${periodShort(anchorPts.length ? anchorPts[anchorPts.length - 1][0] : "")} → ${periodShort(last.period)}`),
      isNum(press.okey_2025) ? kpi(`${fmt.num(press.okey_2025, 0)} / ${fmt.num(press.lenta_hyper_2025, 0)}`, `тыс. ₽/м² за 2025: приобретённые против своих (${press.note || "расчёт"})`) : null,
      kpi(fmt.bn(ok.assumed_debt, 1), `принятый долг; аренда ${fmt.bn(ok.assumed_leases, 1)}`),
      kpi(`${fmt.num(ok.revenue_1h2026_full, 1)} / ${fmt.num(ok.pbt_1h2026_full, 1)}`, "выручка / прибыль до налога за всё 1П, млрд ₽"),
      isNum(nwc.simple_nwc) ? kpi(fmt.bn(nwc.simple_nwc, 1), "оборотный капитал при покупке (запасы + дебиторка − кредиторка)") : null,
      kpi(`${fmt.num(ok.stores_end)} · ${fmt.num(ok.area_end, 0)}`, "магазинов · тыс. м²"))),
  el("div", { class: "split", style: "margin-top:18px" },
    el("div", {},
      el("span", { class: "tile-label" }, diy.name || "DIY"),
      el("div", { class: "kpis", style: "margin-top:6px" },
        kpi(fmt.bn(diy.revenue, 2), `выручка ${diy.period || ""}`),
        kpi(fmt.pct(diy.pbt_margin, 1), `прибыль до налога ${fmt.bn(diy.pbt, 2)} — переходный убыток ребрендинга`),
        kpi(arrow(fmt.num(path.length ? path[0].diy_revenue : null, 1), fmt.num(last.diy_revenue, 1)), `выручка DIY за ${modelUnit(d).one}, млрд ₽: ${periodShort(path.length ? path[0].period : "")} → ${periodShort(last.period)}`)),
      diy.density_path_book ? el("p", { class: "muted small", style: "margin-top:8px" },
        `Плотность DIY по книге, тыс. ₽/м²: ${byKey(diy.density_path_book).map(([k, v]) => `${key(k)} ${fmt.num(v)}`).join(" · ")}.`) : null),
    el("div", {},
      el("span", { class: "tile-label" }, "Интеграционный capex и запертые убытки"),
      el("div", { class: "kpis", style: "margin-top:6px" },
        kpi(byKey(it.integration_capex_book).map(([, v]) => fmt.num(v, 1)).join(" · "),
          `интеграционный capex по книге, млрд ₽: ${byKey(it.integration_capex_book).map(([k]) => key(k)).join(" · ")}`),
        isNum(nol.amount) ? kpi(fmt.bn(nol.amount, 1), `убытки приобретённых юрлиц: в зачёт с ${periodLabel(nol.usable_from)}, риск отказа ${fmt.pct(nol.haircut, 0)}`) : null),
      p.notes && p.notes.acquired_nol_pool ? el("p", { class: "muted small", style: "margin-top:8px" }, sentence(upperFirst(clean(p.notes.acquired_nol_pool)))) : null)));
}

// Значение цели стратегии в её единице.
function targetUnitValue(unit, v) {
  if (!isNum(v)) return "—";
  if (unit === "share") return fmt.pct(v, 1);
  if (unit === "times") return fmt.x(v, 2);
  if (unit === "bn") return fmt.bn(v, 0);
  if (unit === "stores_per_year") return `${fmt.num(v)}${NBSP}маг.`;
  return fmt.num(v, 2);
}

function targetGoal(t) {
  const u = (v) => targetUnitValue(t.unit, v);
  const ma = t.includes_ma ? " с M&A" : "";
  if (t.kind === "min") return `≥${NBSP}${u(t.value)}${t.unit === "stores_per_year" ? " в год" : ""}`;
  if (t.kind === "max") return `≤${NBSP}${u(t.value)}${t.includes_ma ? "" : " без M&A"}`;
  if (t.kind === "range") return `${fmt.num(t.low, 1)}–${u(t.high)}${isNum(t.high_with_ma) ? ` (до ${u(t.high_with_ma)} с M&A)` : ""}`;
  return `${u(t.value)}${ma}`;
}

function strategyCard(d) {
  const s = d.strategy;
  if (!s || !Array.isArray(s.targets)) return card({ title: "Стратегия и гайденс" }, empty("Блока стратегии в выпуске нет."));
  const years = s.years || [];
  const cmp = (s.comparisons || []).filter((c) => c.mix === "headline");
  const pick = (id, year) => cmp.find((c) => c.target === id && c.year === year);
  const hist = (id) => (s.history || []).find((h) => h.target === id);
  const histYears = [...new Set((s.history || []).map((h) => h.year))];
  const models = Object.fromEntries((pathMix(s.model) || []).map((m) => [m.year, m]));
  // Отметка выполнения — текстом в строке числа (не плашкой): число и отметка — одна строка.
  const mark = (c) => c ? el("span", { class: cls("met", c.met ? "is-yes" : "is-no") },
    c.met ? "✓" : c.position === "below" ? "ниже" : c.position === "above" ? "выше" : "✗") : null;
  const cell = (t, c, year) => {
    if (!c || !isNum(c.value)) return "—";
    const seg = t.segment && models[year] && models[year].openings && models[year].openings[t.segment];
    const part = seg && seg.model_halves < 2 ? "*" : "";
    return el("span", {}, targetUnitValue(t.unit, c.value) + part, " ", mark(c));
  };
  const g = s.guidance || {};
  const src = s.source || {};
  return card({ title: "Стратегия-2028 и гайденс против пути модели",
    sub: `${src.title || "Стратегия компании"} (${fmt.date(src.published)}); ${s.basis || ""}. Модель — ожидание смеси заголовка; год с отчётным ${modelUnit(d).ins} — отчёт + модель.` },
  dataTable([
    { title: "Цель", value: (t) => el("span", {}, t.title, el("span", { class: "hint" }, `с. ${t.page}`)), cls: "name" },
    { title: "Ориентир", num: true, value: (t) => targetGoal(t) },
    ...histYears.map((y) => ({ title: `${y} факт`, num: true, value: (t) => { const h = hist(t.id); return h && h.year === y ? cell(t, h, y) : "—"; } })),
    ...years.map((y) => ({ title: `${y} модель`, num: true, value: (t) => (t.years || []).includes(y) ? cell(t, pick(t.id, y), y) : "" })),
  ], s.targets, { detail: (t) => detailsBlock("Цитата и оговорка", `«${t.quote}»${t.note ? ` — ${t.note}` : ""}`) }),
  g.fy_margin_min ? el("div", { class: "note-box", style: "margin-top:16px" },
    el("strong", {}, `Гайденс ${g.year}: маржа EBITDA не ниже ${fmt.pct(g.fy_margin_min, 0)}. `),
    `${periodLabel(g.h1.period)} — ${fmt.pct(g.h1.margin, 2)} (${g.h1.basis}); по модели ${periodLabel(g.h2.half)} — ${fmt.pct(g.h2.margin_model, 2)}, `
    + `а для цели нужно ${fmt.pct(g.h2.margin_required, 2)}. Год по модели — ${fmt.pct(g.fy_margin_model, 2)} (${fmt.pp(g.gap_pp / 100, 2)} к цели); `
    + `ниже цели — ${fmt.pct(g.share_of_grid_below, 0)} массы сетки.`,
    g.nd_ebitda_end ? ` ЧД/EBITDA на конец года: ориентир ≤${NBSP}${fmt.x(g.nd_ebitda_end.guidance, 1)}, модель ${fmt.x(g.nd_ebitda_end.model, 2)}.` : "",
    g.gate ? ` Совещательный гейт «${GATE_NAMES[g.gate.key] || g.gate.key}» ${g.gate.explained ? `объяснён до ${fmt.date(g.gate.valid_until)}` : "без объяснения"}.` : "") : null,
  el("div", { class: "card-foot" },
    el("p", {}, `* Открытия года с отчётным ${modelUnit(d).ins} — только ${periodLabel(d.meta.first_period)} модели; ${modelUnit(d).one} отчёта в сравнение не входит.`),
    s.notes && s.notes.revenue ? el("p", {}, sentence(upperFirst(clean(s.notes.revenue)))) : null,
    s.notes && s.notes.openings ? el("p", {}, sentence(upperFirst(clean(s.notes.openings)))) : null,
    g.source ? el("p", {}, sentence(`Гайденс: ${g.source}`)) : null));
}

/* ── экран «Ближайший отчёт» ── */

function screenReport(d) {
  const fact = d.calendar && d.calendar.next_fact;
  const now = d.nowcast || {};
  const q = now.target_quarter || (now.margin && now.margin.period);
  const half = (now.margin && now.margin.half) || (d.next_report_neutral || {}).period;
  const root = el("div", { class: "screen" },
    screenHead("Ближайший отчёт",
      fact ? `${fact.title}: что он даст оценке` : "Что даст ближайший отчёт",
      `Отчётность — по ${reportUnit(d).dat}: ближайший отчёт — ${periodLabel(q)}. Нау-каст — прогноз ещё не отчитавшегося ${reportUnit(d).gen}; в правило обновления модели идёт ${modelUnit(d).one} ${periodLabel(half)}. К цене нау-каст не подключён: экран показывает, что сделает с оценкой факт, и копит честный зачёт против наивных эталонов.`));
  root.append(el("div", { class: "grid" }, countdownCard(d), nowcastCard(d)));
  root.append(el("div", { class: "grid section" }, impliedHalfCard(d)));
  root.append(el("div", { class: "grid section" }, impactCard(d)));
  root.append(el("div", { class: "grid section" }, retroBenchmarksCard(d)));
  root.append(el("div", { class: "grid section" }, interestCard(d), forecastsCard(d)));
  root.append(el("div", { class: "grid section" }, indicatorsCard(d)));
  root.append(el("div", { class: "grid section" }, journalCard(d)));
  root.append(el("div", { class: "grid section" }, eventsFullCard(d)));
  return root;
}

// Сделка разрыва периметра — именем из выпуска (`perimeter.breaks_journal`).
function dealName(d, id) {
  const row = ((d.perimeter && d.perimeter.breaks_journal) || []).find((b) => b.id === id);
  return row ? row.name : id;
}

// Обратный отсчёт до отчёта и статус правила допуска: оба про одно — когда и
// на каком основании прогноз получит право менять оценку.
function countdownCard(d) {
  const fact = d.calendar && d.calendar.next_fact;
  const now = d.nowcast || {};
  const admission = now.admission && now.admission[now.target];
  const head = fact ? [
    el("div", { class: "countdown" },
      el("span", { class: "big" }, fmt.num(fact.days)),
      el("span", { class: "ink-2" }, `${plural(fact.days, ["день", "дня", "дней"])} от даты оценки до ${whenText(fact, true)}`)),
    fact.note ? detailsBlock("Что даст отчёт", ruText(fact.note)) : null,
  ] : [empty("Календаря отчётов в выпуске нет.")];
  const adm = admission ? el("div", { class: "admission" },
    el("div", { class: "admission-top" },
      el("span", { class: "tile-label" }, "Допуск прогноза к цене"),
      badge(admission.title, admission.admitted ? "good" : admission.demoted ? "bad" : "warn")),
    el("div", { class: "progress", role: "img", "aria-label": `${fmt.num(admission.events)} из ${fmt.num(admission.events_needed)} отчётов` },
      Array.from({ length: admission.events_needed }, (_, i) => el("i", { class: i < Math.min(admission.events, admission.events_needed) ? "on" : null }))),
    el("p", { class: "ink-2 small", style: "margin-top:8px" }, sentence(upperFirst(admission.reason))),
    admission.first_countable ? el("p", { class: "ink-2 small" },
      `Первый засчитываемый отчёт — ${periodLabel(admission.first_countable)} (главный эталон не сломан сделкой); решение о допуске — не раньше ${periodLabel(admission.earliest_decision)}.`) : null,
    (admission.broken_ahead || []).length ? detailsBlock(`Отчёты с разрывом периметра · ${admission.broken_ahead.length}`,
      admission.broken_ahead.map((b) => `${periodLabel(b.quarter)} — ${b.deals.map((id) => dealName(d, id)).join(", ")}`).join("; ") + ".") : null,
    (admission.short_horizon_ahead || []).length ? detailsBlock(`Зачёт на фактическом горизонте · ${admission.short_horizon_ahead.length}`,
      admission.short_horizon_ahead.map((q) => `${periodLabel(q.quarter)} — ${fmt.num(q.days)} дн`).join("; ")
      + `: прогноз появляется после факта предыдущего ${reportUnit(d).gen}, в зачёт идёт первый.`) : null,
    isNum(admission.mse_ratio)
      ? el("p", { class: "ink-2 small" }, `Отношение MSE к лучшему эталону: ${fmt.num(admission.mse_ratio, 2)} (${benchTitle(d, admission.best_benchmark)}).`)
      : null,
    detailsBlock("Правило допуска", sentence(upperFirst(admission.rule))),
    scoreboardLine(d)) : el("p", { class: "card-foot" }, "Статуса правила допуска в выпуске нет.");
  return card({ title: "До отчёта", span: 5,
    sub: fact ? `${fact.precision === "day" ? "дата объявлена" : fact.precision === "window" ? "окно по лагам прошлых релизов" : "плановая дата, точность — месяц"}` : "" },
  ...head, adm);
}

// Подписи слагаемых нау-каста словами — машинные имена на экран не идут.
const COMPONENT_TITLES = {
  base: "маржа периода модели",
  quarter_offset: "квартальная поправка книги",
};

function nowcastCard(d) {
  const now = d.nowcast || {};
  const nc = now.margin;
  if (!nc) return card({ title: "Нау-каст маржи", span: 7 }, empty("Нау-каста в выпуске нет."));
  const naive = (now.naive || {})[`${now.target} ${nc.period}`];
  const bench = naive && naive.main && isNum(naive.naive[naive.main]) ? { title: benchTitle(d, naive.main), value: naive.naive[naive.main] } : null;
  const plot = intervalBar(nc, naive, d);
  const comps = Object.entries(nc.components || {});
  return card({ title: `${upperFirst(now.target_title || "маржа")} ${periodLabel(nc.period)}: нау-каст`, span: 7,
    sub: `уравнение ${nc.version}; доля выручки; ${nc.connected_to_price ? "подключён к цене" : "к цене не подключён"}` },
  el("div", { class: "kpis" },
    kpi(fmt.pct(nc.value, 2), `прогноз · ± ${fmt.num(nc.std_error * 100, 2)} п.п.`),
    isNum(nc.expectation) ? kpi(fmt.pct(nc.expectation, 2), `ожидание модели · ± ${fmt.num(nc.expectation_se * 100, 2)} п.п.`) : null,
    isNum(nc.deviation) ? kpi(fmt.pp(nc.deviation, 2), "отклонение от ожидания — всё, что сказали индикаторы") : null,
    bench ? kpi(fmt.pct(bench.value, 2), `главный эталон: ${bench.title}`) : null,
    isNum(nc.revenue) ? kpi(fmt.bn(nc.revenue, 1), `выручка ${periodLabel(nc.period)} по модели`) : null),
  el("div", { style: "margin-top:12px" }, plot),
  legend([["key-b50", "интервал нау-каста (± ошибка)"], ["key-dot key-model", "нау-каст"], ["key-line key-ink", "ожидание модели"],
    ["key-dot key-third", "эталоны"]]),
  comps.length ? el("div", { class: "card-foot" },
    el("p", {}, sentence("Слагаемые: " + comps.map(([k, v]) => `${COMPONENT_TITLES[k] || k} ${k === "base" ? fmt.pct(v, 2) : fmt.pp(v, 2)}`).join(" · "))),
    isNum(nc.half_value) ? el("p", {}, `Маржа ${periodLabel(nc.half)}, которой соответствует прогноз: ${fmt.pct(nc.half_value, 2)} ± ${fmt.num(nc.half_std_error * 100, 2)} п.п.`) : null,
    nc.equation ? detailsBlock("Уравнение словами", nc.equation) : null) : null);
}

// «Какая маржа полугодия следует из факта его первого квартала» (D2): таблица выпуска по строкам
// «что даст отчёт», эталон гайденса и отчёты второго квартала (у 4 кв.
function impliedHalfCard(d) {
  const now = d.nowcast || {};
  const ih = now.implied_half;
  if (!ih || !Array.isArray(ih.table)) return card({ title: "Что следует для периода модели" }, empty(`Правила «${reportUnit(d).one} → ${modelUnit(d).one}» в выпуске нет.`));
  const guide = now.guidance || {};
  const rows = d.next_report_value || [];
  const words = diagWords(d.next_report_neutral);
  const price = (m) => { const r = rows.find((x) => Math.abs(x.margin - m) < 1e-6); return r ? reportRow(d, r).value : null; };
  const near = (r) => ih.fact && Math.abs(r.quarter_fact - ih.fact.value) === Math.min(...ih.table.map((x) => Math.abs(x.quarter_fact - ih.fact.value)));
  const reports = ih.second_quarter_reports || [];
  const gives = { revenue: "выручка", margin: "маржа" };
  return card({ title: `Какая маржа ${periodLabel(ih.half)} следует из факта ${periodLabel(ih.quarter)}`,
    sub: `Факт ${periodLabel(ih.quarter)} → маржа ${periodLabel(ih.half)} двумя прочтениями правила книги: отклонение сохраняется целиком или ${periodLabel(ih.second_quarter)} идёт по ожиданию модели. Цена — строка «что даст отчёт» с той же маржой ${modelUnit(d).gen}.` },
  el("div", { class: "kpis" },
    kpi(fmt.pct(ih.quarter_expectation, 2), `ожидание модели на ${periodLabel(ih.quarter)}`),
    kpi(fmt.pct(ih.second_quarter_expectation, 2), `ожидание модели на ${periodLabel(ih.second_quarter)}`),
    kpi(fmt.pp(ih.quarter_offset, 2), `квартальная поправка книги (${periodLabel(ih.quarter)})`),
    isNum(guide.required_half_margin) ? kpi(fmt.pct(guide.required_half_margin, 2), `нужно в ${periodLabel(guide.required_half)} для гайденса ≥${NBSP}${fmt.pct(guide.fy_margin_min, 0)} за ${guide.year}`) : null,
    isNum(guide.quarter_benchmark) ? kpi(fmt.pct(guide.quarter_benchmark, 2), `эталон гайденса для ${periodLabel(guide.quarter)}`) : null),
  ih.fact ? el("div", { class: "note-box", style: "margin-top:14px" }, el("strong", {}, `Факт ${periodLabel(ih.quarter)} внесён: ${fmt.pct(ih.fact.value, 2)}. `),
    `Для ${periodLabel(ih.half)} это ${fmt.pct(ih.fact.implied_persistent, 2)} при сохранении отклонения и ${fmt.pct(ih.fact.implied_independent, 2)} без него; сюрприз к ожиданию — ${fmt.pp(ih.fact.surprise, 2)}.`) : null,
  el("div", { style: "margin-top:14px" }, dataTable([
    { title: `Факт ${periodLabel(ih.quarter)}`, value: (r) => fmt.pct(r.quarter_fact, 2), cls: "name" },
    { title: `${periodLabel(ih.half)}, отклонение сохраняется`, num: true, value: (r) => el("strong", {}, fmt.pct(r.implied_persistent, 2)) },
    { title: `${periodLabel(ih.half)}, ${periodLabel(ih.second_quarter)} по ожиданию`, num: true, value: (r) => fmt.pct(r.implied_independent, 2) },
    { title: `${upperFirst(words.subj)} при марже ${modelUnit(d).gen}, ₽`, num: true, value: (r) => isNum(price(r.half_margin_row)) ? fmt.num(price(r.half_margin_row)) : "—" },
  ], ih.table, { cls: "compact", rowClass: (r) => (near(r) ? "is-pick" : null) })),
  reports.length ? el("div", { style: "margin-top:16px" },
    el("span", { class: "tile-label" }, `${periodLabel(ih.second_quarter)}: ${reports.length === 1 ? "один отчёт" : `${reports.length} ${plural(reports.length, ["событие", "события", "событий"])} журнала`}`),
    el("ul", { class: "list reports-list" }, reports.map((r) => el("li", {},
      el("span", { class: "t" }, `${upperFirst(r.gives.map((g) => gives[g] || g).join(" и "))}`, el("span", { class: "muted" }, r.title || "по правилу лагов прошлых релизов")),
      el("span", { class: "v" }, whenText(r)))))) : null,
  el("div", { class: "card-foot" }, detailsBlock("Правило книги", ruText(ih.rule))));
}

// Интервал нау-каста и эталоны на одной шкале: прогноз и эталон в разных
// местах экрана глазом не сравнить.
function intervalBar(nc, naive, d) {
  return chart((W) => {
    const H = 86;
    const pts = [nc.value - nc.std_error, nc.value + nc.std_error, nc.expectation];
    const benches = naive ? Object.entries(naive.naive).filter(([, v]) => isNum(v)) : [];
    pts.push(...benches.map(([, v]) => v));
    const lo = Math.min(...pts.filter(isNum)), hi = Math.max(...pts.filter(isNum));
    const pad = (hi - lo) * 0.08 || 0.002;
    const x = scale(lo - pad, hi + pad, 10, W - 10);
    const svg = svgBox(W, H);
    const cy = 44;
    svg.append(sv("rect", { class: "ib-range", x: x(nc.value - nc.std_error), y: cy - 9, width: x(nc.value + nc.std_error) - x(nc.value - nc.std_error),
      height: 18, rx: 6, fill: "var(--model-wash-3)",
      tip: { title: "Интервал нау-каста", rows: [["от", fmt.pct(nc.value - nc.std_error, 2)], ["до", fmt.pct(nc.value + nc.std_error, 2)]] } }));
    if (isNum(nc.expectation)) {
      svg.append(line(x(nc.expectation), cy - 16, x(nc.expectation), cy + 16, { stroke: "var(--ink)", "stroke-width": 2,
        tip: { title: "Ожидание модели", rows: [["маржа", fmt.pct(nc.expectation, 3)]] } }));
    }
    const labels = [];
    for (const [key, v] of benches) {
      const main = naive.main === key;
      svg.append(sv("circle", { class: "ib-mark", cx: x(v), cy, r: main ? 6 : 4.5, fill: "var(--third)", stroke: "var(--surface)", "stroke-width": 2,
        tip: { title: `Эталон: ${benchTitle(d, key)}`, rows: [["маржа", fmt.pct(v, 2)]], note: main ? "главный эталон зачёта" : null } }));
      if (main) labels.push({ x: x(v), text: `эталон ${fmt.pct(v, 2)}` });
    }
    svg.append(sv("circle", { cx: x(nc.value), cy, r: 7, fill: "var(--model)", stroke: "var(--surface)", "stroke-width": 2,
      tip: { title: "Нау-каст", rows: [["маржа", fmt.pct(nc.value, 2)], ["ошибка", `± ${fmt.num(nc.std_error * 100, 2)} п.п.`]] } }));
    labels.push({ x: x(nc.value), text: `нау-каст ${fmt.pct(nc.value, 2)}`, strong: true });
    const items = labels.map((t) => { const w = textWidth(t.text, 12.5, t.strong ? 640 : 520); const x0 = Math.min(Math.max(2, t.x - w / 2), W - w - 2); return { ...t, x0, x1: x0 + w }; });
    stackLabels(items, 10);
    for (const t of items) svg.append(label(t.x0, t.row === 0 ? 16 : H - 6, t.text, { class: t.strong ? "label-strong" : "label" }));
    return svg;
  }, "Интервал нау-каста маржи и эталоны на одной шкале");
}

// Шкала маржи графика «что даст отчёт»: крайние строки таблицы ± 8 % размаха.
function impactSpan(rows) {
  const xs = rows.map((r) => r.margin);
  const span = Math.max(...xs) - Math.min(...xs) || 0.01;
  return [Math.min(...xs) - span * 0.08, Math.max(...xs) + span * 0.08];
}

// Нейтральная маржа выпуска, если она на шкале графика (тогда у неё вертикаль).
function neutralOnScale(d) {
  const n = d.next_report_neutral || {};
  const rows = d.next_report_value || [];
  if (!isNum(n.margin) || !rows.length) return null;
  const [x0, x1] = impactSpan(rows);
  return n.margin >= x0 && n.margin <= x1 ? n : null;
}

// «Что даст отчёт»: факт маржи периода модели → медиана (строки выпуска `next_report_value`),
// вокруг — ось ставок строки; сверху — маржа периода, которой соответствует квартальный нау-каст (±
// её ошибка), и маржа, которой требует гайденс, на той же шкале.
function impactChart(d, compact = false) {
  const words = diagWords(d.next_report_neutral);
  const rows = (d.next_report_value || []).map((r) => ({ ...r, ...reportRow(d, r) }));
  const now = d.nowcast || {};
  const nc = now.margin;
  const half = (d.next_report_neutral || {}).period || (nc && nc.half);
  const hv = nc && isNum(nc.half_value) ? nc.half_value : null;
  const hse = nc && isNum(nc.half_std_error) ? nc.half_std_error : 0;
  const guide = now.guidance || {};
  const bench = isNum(guide.required_half_margin) ? guide.required_half_margin : null;
  const current = words.median && isNum(d.next_report_neutral.central) ? d.next_report_neutral.central : d.fair_value.central;
  const market = d.market.price;
  return chart((W) => {
    const H = compact ? 200 : 300;
    const m = { l: compact ? 42 : 50, r: 14, t: hv !== null ? (compact ? 52 : 62) : 14, b: 30 };
    const [x0, x1] = impactSpan(rows);
    const x = scale(x0, x1, m.l, W - m.r);
    const vals = rows.flatMap((r) => [r.low, r.high]).concat([current, market]).filter(isNum);
    const vpad = (Math.max(...vals) - Math.min(...vals)) * 0.08;
    const y = scale(Math.max(0, Math.min(...vals) - vpad), Math.max(...vals) + vpad, H - m.b, m.t);
    const svg = svgBox(W, H, `${upperFirst(words.full)} в зависимости от маржи отчёта`);
    for (const t of ticks(y.d[0], y.d[1], compact ? 3 : 5)) {
      svg.append(line(m.l, y(t), W - m.r, y(t), { class: "gridline" }),
        text(m.l - 8, y(t) + 4, fmt.num(t), { class: "tick", "text-anchor": "end" }));
    }
    const every = Math.max(1, Math.ceil((textWidth("5,5 %", 12, 400) + 10) / ((W - m.l - m.r) / Math.max(1, rows.length))));
    rows.forEach((r, i) => {
      if ((rows.length - 1 - i) % every === 0) svg.append(text(x(r.margin), H - 8, fmt.num(r.margin * 100, 1) + THIN + "%", { class: "tick", "text-anchor": "middle" }));
    });
    svg.append(line(m.l, H - m.b, W - m.r, H - m.b, { class: "axisline" }));
    const up = rows.map((r) => `${x(r.margin).toFixed(1)},${y(r.high).toFixed(1)}`);
    const down = rows.slice().reverse().map((r) => `${x(r.margin).toFixed(1)},${y(r.low).toFixed(1)}`);
    svg.append(sv("path", { d: `M${up.join(" L")} L${down.join(" L")} Z`, fill: "var(--model-wash-1)" }));
    const refs = [{ v: current, color: "var(--ink)", t: `сейчас ${fmt.rub(current)}` }, { v: market, color: "var(--market)", t: `рынок ${fmt.rub(market)}` }]
      .filter((r) => isNum(r.v));
    for (const ref of refs) svg.append(line(m.l, y(ref.v), W - m.r, y(ref.v), { stroke: ref.color, "stroke-width": 1.2 }));
    const refLabels = refs.map((r) => ({ y: y(r.v), text: r.t }));
    if (refLabels.length === 2 && Math.abs(refLabels[0].y - refLabels[1].y) < 16) {
      const [a, b] = refLabels[0].y > refLabels[1].y ? refLabels : [refLabels[1], refLabels[0]];
      a.y += 8;
      b.y -= 8;
    }
    if (hv !== null && hv >= x0 && hv <= x1) {
      svg.append(line(x(hv), m.t - 10, x(hv), H - m.b, { stroke: "var(--model)", "stroke-width": 1, "stroke-dasharray": "3 3" }));
    }
    const neutral = neutralOnScale(d);
    if (neutral) {
      svg.append(line(x(neutral.margin), m.t, x(neutral.margin), H - m.b, { stroke: "var(--ink-2)", "stroke-width": 1.2, "stroke-dasharray": "1 3",
        tip: { title: "Нейтральная маржа", rows: [["маржа", fmt.pct(neutral.margin, 2)], [words.subj, fmt.rub(neutral.central)],
          ...(isNum(neutral.slope_rub_per_0p1pp) ? [["на 0,1 п.п.", fmt.rub(neutral.slope_rub_per_0p1pp)]] : [])] } }));
    }
    svg.append(sv("path", { d: "M" + rows.map((r) => `${x(r.margin).toFixed(1)},${y(r.value).toFixed(1)}`).join(" L"),
      fill: "none", stroke: "var(--model)", "stroke-width": 2.2, "stroke-linejoin": "round" }));
    const titles = (d.regime_prob || {}).titles || {};
    // Занятые подписями прямоугольники: подпись опорной линии ищет место, где
    // она не наезжает ни на подпись точки, ни на саму точку.
    const taken = [];
    const box = (bx, yBase, w, h = 16) => ({ x0: bx, x1: bx + w, y0: yBase - h + 3, y1: yBase + 5 });
    const hits = (b) => taken.some((t) => b.x0 < t.x1 + 4 && t.x0 < b.x1 + 4 && b.y0 < t.y1 && t.y0 < b.y1);
    rows.forEach((r, i) => {
      const cx = x(r.margin), cy = y(r.value);
      svg.append(sv("circle", { cx, cy, r: 5, fill: "var(--model)", stroke: "var(--surface)", "stroke-width": 2,
        tip: { title: `Маржа ${periodLabel(half)} = ${fmt.pct(r.margin, 1)}`,
          rows: [[words.subj, fmt.rub(r.value)], ["ось ставок", `${fmt.num(r.low)}–${fmt.num(r.high)}${THIN}₽`],
            ...Object.entries(r.probabilities || {}).map(([k, v]) => [titles[k] || k, fmt.pct(v, 1)])] } }));
      taken.push({ x0: cx - 7, x1: cx + 7, y0: cy - 7, y1: cy + 7 });
      const first = i === 0, last = i === rows.length - 1;
      const t = fmt.num(r.value);
      const w = textWidth(t, 13, 640);
      if (first || last || (!compact && (rows.length - 1 - i) % every === 0 && W / rows.length > w + 12)) {
        const lx = cx + (first ? 8 : last ? -8 : 0);
        const bx = last ? lx - w : first ? lx : lx - w / 2;
        taken.push(box(bx, cy - 10, w));
        svg.append(label(lx, cy - 10, t, { "text-anchor": last ? "end" : first ? "start" : "middle", class: "label-strong" }));
      }
    });
    for (const ref of refLabels) {
      const w = textWidth(ref.text, 12.5, 520);
      const spots = [[m.l + 6, ref.y - 5], [W - m.r - w, ref.y - 5], [m.l + 6, ref.y + 15], [W - m.r - w, ref.y + 15]];
      const spot = spots.find(([sx, sy]) => !hits(box(sx, sy, w)));
      if (spot) {
        taken.push(box(spot[0], spot[1], w));
        svg.append(label(spot[0], spot[1], ref.text, { class: "label" }));
      }
    }
    if (neutral) {
      const t = `нейтральная ${fmt.pct(neutral.margin, 2)}`;
      const w = textWidth(t, 12.5, 520);
      const nx = x(neutral.margin);
      const base = H - m.b - 6;
      const spots = [[nx + 5, base], [nx - w - 5, base], [nx + 5, base - 16], [nx - w - 5, base - 16]]
        .filter(([sx]) => sx >= m.l && sx + w <= W - m.r);
      const free = spots.find(([sx, sy]) => !hits(box(sx, sy, w)));
      if (free) {
        taken.push(box(free[0], free[1], w));
        svg.append(label(free[0], free[1], t, { class: "label" }));
      }
    }
    if (hv !== null) {
      const band = m.t - (compact ? 22 : 28);
      const lo = hv - hse, hi = hv + hse;
      const cl = Math.max(x0, lo), ch = Math.min(x1, hi);
      svg.append(sv("rect", { x: x(cl), y: band - 5, width: Math.max(2, x(ch) - x(cl)), height: 10, rx: 5, fill: "var(--model-wash-3)",
        tip: { title: `Нау-каст: маржа ${periodLabel(nc.half)}`, rows: [["от", fmt.pct(lo, 2)], ["до", fmt.pct(hi, 2)]],
          note: lo < x0 || hi > x1 ? "шире шкалы таблицы — обрезан краем" : null } }));
      if (lo < x0) svg.append(sv("path", { d: `M${x(x0) - 7},${band} l7,-5 v10 Z`, fill: "var(--model)" }));
      if (hi > x1) svg.append(sv("path", { d: `M${x(x1) + 7},${band} l-7,-5 v10 Z`, fill: "var(--model)" }));
      if (hv >= x0 && hv <= x1) {
        svg.append(sv("circle", { cx: x(hv), cy: band, r: 5.5, fill: "var(--model)", stroke: "var(--surface)", "stroke-width": 2 }));
      }
      if (isNum(bench) && bench >= x0 && bench <= x1) {
        svg.append(sv("circle", { cx: x(bench), cy: band, r: 4.5, fill: "var(--third)", stroke: "var(--surface)", "stroke-width": 2,
          tip: { title: "Гайденс компании", rows: [[`нужно в ${periodLabel(guide.required_half)}`, fmt.pct(bench, 2)]] } }));
      }
      const t = `нау-каст ${fmt.pct(hv, 2)} ± ${fmt.num(hse * 100, 2)} п.п.`
        + (isNum(bench) && !compact ? ` · гайденс ${fmt.pct(bench, 2)}` : "");
      const w = textWidth(t, 12.5, 520);
      const tx = Math.min(Math.max(m.l, x(Math.min(Math.max(hv, x0), x1)) - w / 2), W - m.r - w);
      svg.append(label(tx, band - 10, t));
    }
    return svg;
  }, `Что даст отчёт: маржа ${periodLabel(half)} и ${words.full}`);
}

function impactCard(d) {
  const rows = d.next_report_value || [];
  if (!rows.length) return card({ title: "Что даст отчёт" }, missing("что даст отчёт"));
  const w = diagWords(d.next_report_neutral);
  const half = (d.next_report_neutral || {}).period || (d.nowcast && d.nowcast.margin && d.nowcast.margin.half);
  const rp = d.regime_prob || { titles: {} };
  const order = ["stress", "floor", "partial", "full"];
  const table = () => dataTable([
    { title: "Маржа отчёта", value: (r) => fmt.pct(r.margin, 1), cls: "name" },
    ...order.map((k) => ({ title: rp.titles[k] || k, num: true, value: (r) => fmt.pct((r.probabilities || {})[k], 1) })),
    { title: `${upperFirst(w.subj)}, ₽`, num: true, value: (r) => el("strong", {}, fmt.num(reportRow(d, r).value)) },
    { title: "Ось ставок, ₽", num: true, value: (r) => `${fmt.num(reportRow(d, r).low)}–${fmt.num(reportRow(d, r).high)}` },
    { title: "P(капитал ≤ 0)", num: true, value: (r) => fmt.pct(r.p_equity_nonpositive, 0) },
  ], d.next_report_value.map((r) => r));
  const fig = withTable(impactChart(d, false), table);
  const rc = rp.reference_class;
  const rcStress = rc && isNum(rc.share && rc.share.stress) && isNum(rp.unconditional && rp.unconditional.stress)
    ? ` В референс-классе (${fmt.num(rc.n)} случаев) новое снижение — ${fmt.pct(rc.share.stress, 0)}`
      + (rc.share_ci95 && rc.share_ci95.stress ? ` (95 %: ${fmt.num(rc.share_ci95.stress[0] * 100, 0)}–${fmt.num(rc.share_ci95.stress[1] * 100, 0)}${THIN}%)` : "")
      + `, в книге стресс — ${fmt.pct(rp.unconditional.stress, 0)}.`
    : "";
  return card({ title: `Что даст отчёт: факт маржи ${periodLabel(half)} → оценка`, tools: fig.button,
    sub: `Факт маржи ${modelUnit(d).gen} сдвигает вероятности режимов по правилу обновления книги, а с ними — ${w.acc}. Заливка — ось ставок строки`
      + (w.median ? "." : "; медиана полосы по каждому исходу не пересчитывается.") },
  legend([["key-line key-model", w.full], ["key-b80", "ось ставок"], ["key-b50", "нау-каст ± ошибка"],
    ["key-dot key-third", "гайденс"], ["key-line key-ink", `${w.subj} сейчас`], ["key-line key-market", "рынок"],
    ...(neutralOnScale(d) ? [["key-line key-dash", "нейтральная маржа"]] : [])]),
  fig.box,
  el("p", { class: "card-foot" }, neutralSentence(d),
    `Разброс правила — ${fmt.num(rp.sigma_pp * 100, 1)} п.п. на наблюдение. К нау-касту правило не подключено: таблица показывает, что сделал бы факт отчёта; допуск прогноза — решение владельца по правилу допуска.${rcStress}`));
}

function scoreboardLine(d) {
  const board = (d.nowcast && d.nowcast.scoreboard) || {};
  const rows = board[d.nowcast.target] || [];
  if (!rows.length) {
    const retro = d.nowcast && d.nowcast.retro;
    return el("p", { class: "card-foot" }, "Табло «прогноз против факта» пусто: фактов после начала журнала ещё не было",
      retro && retro.first_exam ? ` — первая сверка с фактом ${fmt.dateLong(retro.first_exam)}.` : ".");
  }
  return dataTable([
    { title: upperFirst(reportUnit(d).one), value: (r) => periodLabel(r.period), cls: "name" },
    { title: "Прогноз", num: true, value: (r) => targetValue("margin", r.forecast) },
    { title: "Факт", num: true, value: (r) => targetValue("margin", r.actual) },
    { title: "Ошибка", num: true, value: (r) => isNum(r.error) ? fmt.pp(r.error, 2) : "—" },
    { title: "Горизонт, дн", num: true, value: (r) => isNum(r.forecast) && isNum(r.scoring_horizon_days) ? fmt.num(r.scoring_horizon_days) : "—" },
  ], rows, { cls: "compact" });
}

function retroBenchmarksCard(d) {
  const retro = d.nowcast && d.nowcast.retro;
  if (!retro || !retro.margin) return card({ title: "Эталоны на истории" }, missing("ретро-проверка эталонов"));
  const m = retro.margin;
  const split = retro.split_year;
  const periods = m.points.map((p) => p.period);
  const nc = d.nowcast.margin;
  const cats = nc && !periods.includes(nc.period) ? [...periods, nc.period] : periods;
  const mainTitle = benchTitle(d, m.main);
  const series = [
    { name: "факт", color: "var(--ink)", width: 1.8, dots: true, r: 3.5, points: m.points.map((p) => [p.period, p.actual]) },
    { name: `эталон: ${mainTitle}`, color: "var(--third)", line: false, dots: true, r: 4,
      points: m.points.map((p) => [p.period, (p.forecasts || {})[m.main]]) },
  ];
  if (nc) {
    series.push({ name: "нау-каст", color: "var(--model)", line: false, dots: true, r: 5.5, points: [[nc.period, nc.value]],
      errors: [[nc.period, nc.value - nc.std_error, nc.value + nc.std_error]] });
  }
  const plot = linesChart(series, { xType: "band", categories: cats, height: 260, yFmt: (v) => fmt.num(v * 100, 1) + THIN + "%",
    tipFmt: (v) => fmt.pct(v, 2), xFmt: periodShort, label: "Маржа по отчётным периодам: факт, главный эталон и нау-каст" });
  const fig = withTable(plot, () => dataTable([
    { title: upperFirst(reportUnit(d).one), value: (p) => periodLabel(p.period), cls: "name" },
    { title: "Факт", num: true, value: (p) => fmt.pct(p.actual, 2) },
    ...m.benchmarks.map((b) => ({ title: b.title, num: true, value: (p) => fmt.pct((p.forecasts || {})[b.method], 2) })),
  ], m.points));
  const pp = (x) => (x && isNum(x.rmse) ? fmt.num(x.rmse * 100, 2) : "—");
  const scores = dataTable([
    { title: "Эталон", value: (b) => el("span", {}, b.title,
      b.main ? el("span", { class: "hint" }, "главный — по нему зачёт") : null,
      m.best === b.method ? el("span", { class: "hint" }, "лучший на истории") : null), cls: "name" },
    { title: "RMSE", num: true, value: (b) => el("strong", {}, pp(b.all)) },
    { title: "без разрывов", num: true, value: (b) => pp(b.clean) },
    { title: `до ${split}`, num: true, value: (b) => pp(b.before) },
    { title: `с ${split}`, num: true, value: (b) => pp(b.since) },
    { title: "Смещение", num: true, value: (b) => (b.all && isNum(b.all.bias) ? fmt.signed(b.all.bias * 100, 2) : "—") },
  ], m.benchmarks, { rowClass: (b) => (b.main ? "is-pick" : null) });
  const broken = (m.excluded || []).map((x) => periodLabel(x.period));
  return card({ title: "Эталоны на истории: планка для прогноза", tools: fig.button,
    sub: `Ошибки наивных прогнозов маржи вне выборки, ${periodLabel(m.periods[0])}${m.periods[1] !== m.periods[0] ? `–${periodLabel(m.periods[1])}` : ""}, п.п. маржи. У самого уравнения истории нет — это планка, которую ему предстоит взять.` },
  el("div", { class: "split wide-left" },
    el("div", {}, legend([["key-line key-ink", "факт"], ["key-dot key-third", `главный эталон: ${mainTitle}`], ["key-dot key-model", "нау-каст ± ошибка"]]), fig.box),
    el("div", {}, scores,
      el("p", { class: "card-foot" }, sentence(upperFirst(retro.note)),
        broken.length ? ` Периоды на стыке сделок (в «без разрывов» не входят): ${broken.join(", ")}.` : ""))),
  m.note || (retro.source && retro.source.pit_warning)
    ? el("p", { class: "card-foot" }, m.note ? sentence(upperFirst(m.note)) + " " : "", retro.source && retro.source.pit_warning ? sentence(`Ряды: ${retro.source.pit_warning}`) : "") : null);
}

function interestCard(d) {
  const interest = d.nowcast && d.nowcast.interest;
  if (!interest || !isNum(interest.net_interest)) {
    return card({ title: "Чистые проценты", span: 6 }, empty(interest && interest.unavailable ? sentence(upperFirst(interest.unavailable)) : "Канала процентов в выпуске нет."));
  }
  const main = interest.main_benchmark;
  const bench = interest.naive && isNum(interest.naive[main]) ? interest.naive[main] : null;
  const kinds = { bond: "облигации", bank: "банки", other: "прочие займы" };
  const u = reportUnit(d);
  return card({ title: `Чистые проценты ${periodLabel(interest.period)}`, span: 6,
    sub: "посуточное начисление по реестру траншей: плавающие — ключевая + спред, фиксированные — ставка; минус доход на кассу" },
  el("div", { class: "kpis" },
    kpi(fmt.bn(interest.net_interest, 1), `прогноз · заявленная точность ± ${fmt.pct(interest.accuracy, 0)}`),
    bench !== null ? kpi(fmt.bn(bench, 1), `эталон: ${benchTitle(d, main)}`) : null,
    kpi(fmt.pct(interest.floating_share, 1), "доля плавающего долга (реестр)")),
  el("div", { style: "margin-top:16px" }), dataTable([
    { title: "Слагаемое", value: (r) => el("span", { class: r.sub ? "indent" : null }, r.name), cls: "name" },
    { title: "млрд ₽", num: true, value: (r) => r.value },
  ], [
    { name: "Проценты по долгу", value: fmt.num(interest.debt_interest, 2) },
    ...Object.entries(interest.by_kind || {}).map(([k, v]) => ({ name: `в т.ч. ${kinds[k] || k}`, value: fmt.num(v, 2), sub: true })),
    { name: "Доход на кассу (вычитается)", value: fmt.num(interest.cash_income, 2) },
    { name: "Чистые проценты", value: fmt.num(interest.net_interest, 2), total: true },
  ], { cls: "compact", rowClass: (r) => (r.sub ? "is-muted" : r.total ? "is-total" : null) }),
  el("p", { class: "card-foot" },
    `Средний долг ${fmt.bn(interest.average_debt, 1)}, средняя касса ${fmt.bn(interest.average_cash, 1)}, ключевая в среднем ${fmt.pct(interest.average_key_rate, 2)}, `
    + `эффективная ставка ${fmt.pct(interest.effective_rate, 2)} (вменённый спред ${fmt.pp(interest.implied_spread, 2)}). `
    + `Валовой долг на начало ${u.gen} ${fmt.bn(interest.opening_gross_debt, 1)}; погашений в ${u.loc} — ${fmt.num(interest.redemptions_count)}. `,
    interest.redemptions_note ? sentence(interest.redemptions_note) + " " : "",
    isNum(interest.survey_delta) ? `Внешняя траектория ключевой (опрос ЦБ) меняет прогноз на ${fmt.signed(interest.survey_delta, 2)} млрд ₽ — это чувствительность, а не база.` : ""));
}

function forecastsCard(d) {
  const naive = (d.nowcast && d.nowcast.naive) || {};
  const keys = Object.keys(naive);
  if (!keys.length) {
    return card({ title: "Прогнозы против эталонов", span: 6 },
      empty("Журнал прогнозов пуст: прогнозы и эталоны появятся с первым суточным тактом."));
  }
  return card({ title: "Прогнозы против эталонов", span: 6, sub: "до выхода факта: с чем соревнуется каждый прогноз журнала" },
    dataTable([
      { title: "Показатель", value: (k) => el("span", {}, upperFirst(targetName(d, k.split(" ")[0])), el("span", { class: "hint" }, periodLabel(k.split(" ")[1] || ""))), cls: "name" },
      { title: "Прогноз", num: true, value: (k) => el("strong", {}, targetValue(k, naive[k].forecast)) },
      { title: "Главный эталон", num: true, value: (k) => naive[k].main ? targetValue(k, naive[k].naive[naive[k].main]) : "—" },
    ], keys, { detail: (k) => {
      const rest = Object.entries(naive[k].naive).filter(([m]) => m !== naive[k].main);
      return rest.length ? detailsBlock("Прочие эталоны", rest.map(([m, v]) => `${benchTitle(d, m)} — ${targetValue(k, v)}`).join("; ") + ".") : null;
    } }));
}

// Ряды индикаторов: название, единица и группа приходят из выпуска (`title`, `unit`,
// `group_title`); здесь только вид числа.
function indicatorValue(item) {
  const v = item.value;
  if (!isNum(v)) return "—";
  switch (item.unit) {
    case "index": return fmt.signed((v - 1) * 100, 2) + THIN + "%";
    case "share": return fmt.pct(v, 2);
    case "price": return fmt.rub(v);
    case "price_pct_nominal": return `${fmt.num(v * 100, 2)}${THIN}% номинала`;
    case "bn": return fmt.bn(v, 1);
    case "count": return fmt.num(v);
    case "level": return fmt.num(v, 0);
    case "money": return fmt.rub(v);
    default: return fmt.num(v, Math.abs(v) < 10 ? 2 : 0);
  }
}

// Свободный текст выпуска (примечания рядов) — с десятичной запятой.
function ruText(textContent) {
  // Сначала дробная запятая, потом даты ISO в «24.09.2026» — иначе точки даты тоже стали бы
  // запятыми.
  return String(textContent || "")
    .replace(/\b\d{2}\.\d{2}\.\d{4}\b|(\d)\.(\d)/g, (m, a, b) => (a === undefined ? m : `${a},${b}`))
    .replace(/\b(\d{4})-(\d{2})-(\d{2})\b/g, "$3.$2.$1")
    .replace(/(^|[\s(])-(?=\d)/g, `$1${MINUS}`);
}

// Линия ряда в плитке индикатора: история выпуска за год (`indicators[].history`).
function sparkline(item) {
  const pts = (item.history || []).filter((p) => Array.isArray(p) && isNum(p[1]));
  if (pts.length < 2) return null;
  const vals = pts.map((p) => p[1]);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const plot = chart((W) => {
    const H = 40;
    const x = scale(0, pts.length - 1, 3, W - 5);
    const y = hi > lo ? scale(lo, hi, H - 5, 5) : () => H / 2;
    const svg = svgBox(W, H, `История ряда «${item.title}»`);
    svg.append(sv("polyline", { points: pts.map((p, i) => `${x(i).toFixed(1)},${y(p[1]).toFixed(1)}`).join(" "),
      fill: "none", stroke: "var(--model)", "stroke-width": 1.6, "stroke-linejoin": "round", "stroke-linecap": "round" }));
    const last = pts[pts.length - 1];
    svg.append(sv("circle", { cx: x(pts.length - 1), cy: y(last[1]), r: 3, fill: "var(--model)",
      tip: { title: item.title, rows: [[fmt.date(pts[0][0]), indicatorValue({ ...item, value: pts[0][1] })],
        [fmt.date(last[0]), indicatorValue({ ...item, value: last[1] })]] } }));
    return svg;
  }, `История ряда «${item.title}»`);
  return el("div", { class: "spark" }, plot,
    el("span", { class: "muted small" }, `с ${fmt.date(pts[0][0])}: мин ${indicatorValue({ ...item, value: lo })} · макс ${indicatorValue({ ...item, value: hi })}`));
}

function indicatorsCard(d) {
  const items = d.indicators || [];
  if (!items.length) {
    return card({ title: "Опережающие индикаторы" }, empty("Рядов в выпуске нет: состояние сбора пустое."));
  }
  const tiles = items.filter((i) => i.tile);
  const groups = new Map();
  for (const item of items) {
    const g = item.group_title || "Прочее";
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(item);
  }
  return card({ title: "Опережающие индикаторы", sub: `${fmt.num(items.length)} рядов; у плиток — линия ряда за год, в таблицах — последняя точка каждого. Справочные ряды ни в одно уравнение не входят.` },
    el("div", { class: "ind-tiles" }, tiles.map((i) => el("div", { class: "ind-tile" },
      el("span", { class: "tile-label" }, i.title),
      el("span", { class: "ind-value" }, i.missing ? "—" : indicatorValue(i)),
      el("span", { class: "muted small" }, i.missing ? sentence(upperFirst(ruText(i.missing))) : `${fmt.date(i.period)}${i.reference_only ? " · справочно" : ""}`),
      sparkline(i),
      i.note && !i.missing ? el("span", { class: "muted small ind-note" }, ruText(i.note)) : null))),
    el("div", { class: "stack", style: "margin-top:16px" }, [...groups.entries()].map(([g, rows]) =>
      detailsBlock(`${g} · ${rows.length}`, dataTable([
        { title: "Ряд", value: (i) => el("span", {}, i.title, i.reference_only ? el("span", { class: "hint" }, "справочно") : null), cls: "name" },
        { title: "Значение", num: true, value: (i) => indicatorValue(i) },
        { title: "Дата", num: true, value: (i) => i.period ? fmt.date(i.period) : "—" },
        { title: "Точек", num: true, value: (i) => fmt.num(i.points) },
      ], rows, { cls: "compact", detail: (i) => (i.note || i.missing) ? detailsBlock("Примечание", ruText(i.missing || i.note)) : null })))));
}

function journalCard(d) {
  const rows = (d.nowcast && d.nowcast.journal) || [];
  if (!rows.length) return card({ title: "Журнал прогнозов" }, empty("Журнал пуст: записей в выпуске нет."));
  // Уравнение одно на версию: печатается один раз под таблицей.
  const versions = new Map();
  for (const r of rows) if (r.equation && !versions.has(r.version)) versions.set(r.version, r.equation);
  return card({ title: "Журнал прогнозов", sub: "неизменяемые записи: последние по каждому показателю и версии уравнения; факт вносится после отчёта" },
    dataTable([
      { title: "Показатель", value: (r) => el("span", {}, upperFirst(targetName(d, r.target)), r.note ? el("span", { class: "hint" }, ruText(r.note)) : null), cls: "name" },
      { title: "Период", value: (r) => periodLabel(r.period) },
      { title: "Прогноз", num: true, value: (r) => el("strong", {}, targetValue(r.target, r.value)) },
      { title: "±", num: true, value: (r) => isNum(r.std_error) ? (/margin/.test(r.target) ? `${fmt.num(r.std_error * 100, 2)} п.п.` : fmt.num(r.std_error, 2)) : "—" },
      { title: "Версия", value: (r) => el("span", { class: "code" }, r.version) },
      { title: "Записан", num: true, value: (r) => fmt.date(r.made_at) },
    ], rows.slice().reverse()),
    versions.size ? el("div", { class: "card-foot" }, detailsBlock(`Уравнения версий · ${versions.size}`,
      el("dl", { class: "equations" }, [...versions.entries()].map(([v, eq]) => [el("dt", {}, el("span", { class: "code" }, v)), el("dd", {}, eq)])))) : null);
}

function eventsFullCard(d) {
  const node = eventsCard(d, 99);
  node.classList.remove("span-6");
  node.classList.add("span-12");
  return node;
}

/* ── экран «Деньги и долг» ── */

let MONEY_SCENARIO = null;

function screenDebt(d) {
  const S = d.scenarios || [];
  if (!MONEY_SCENARIO || !S.some((s) => s.name === MONEY_SCENARIO)) {
    MONEY_SCENARIO = S.length ? S.slice().sort((a, b) => b.weight - a.weight)[0].name : null;
  }
  const root = el("div", { class: "screen" },
    screenHead("Деньги и долг", "Как бизнес обслуживает долг и когда платит дивиденды",
      `Мост от стоимости бизнеса к капиталу, денежные потоки по годам, дивидендная лестница, сверка баз МСФО 16 и до МСФО 16, стена погашений и чувствительность к ключевой ставке. Модель считает на базе «${d.meta.basis}». Сценарий выбирается кнопками — годовая таблица и мост идут за ним.`));
  const body = el("div");
  const draw = () => {
    const pick = S.find((s) => s.name === MONEY_SCENARIO);
    body.replaceChildren(
      el("div", { class: "grid" }, debtKpis(d, pick)),
      el("div", { class: "grid section" }, bridgeCard(d, pick), evPartsCard(d, pick)),
      el("div", { class: "grid section" }, annualCard(d, pick)));
  };
  const pickRow = S.length ? el("div", { class: "pick-row" },
    el("span", { class: "muted" }, "Сценарная клетка:"),
    chooser(S.map((s) => ({ value: s.name, label: s.title, hint: fmt.pct(s.weight, 0) })), MONEY_SCENARIO,
      (v) => { MONEY_SCENARIO = v; draw(); }, "Сценарий")) : null;
  draw();
  root.append(pickRow, body);
  root.append(el("div", { class: "grid section" }, dividendsCard(d)));
  root.append(el("div", { class: "grid section" }, basesCard(d)));
  root.append(el("div", { class: "grid section" }, wallCard(d), sensitivityCard(d)));
  root.append(el("div", { class: "grid section" }, registerCard(d)));
  return root;
}

// Одно число «чистый долг на дату оценки» — модельное, строка моста выбранного сценария (им
// считается оценка); реестровый — рядом, под своим именем.
function debtKpis(d, pick) {
  const debt = d.debt || {};
  const ndLine = pick && (pick.claims_lines || []).find((line_) => line_.key === "net_debt");
  const closed = d.meta.periods_closed > 0;
  const reg = debt.register || {};
  const u = modelUnit(d);
  return card({ title: "Долг на дату оценки", sub: `${fmt.date(d.meta.valuation_date)} · база ${d.meta.basis}` },
    el("div", { class: "kpis" },
      ndLine ? kpi(fmt.bn(ndLine.value, 1), "чистый долг на дату оценки", { tip: { title: "Строка моста", rows: [["сценарий", pick.title]] } }) : null,
      reg.available ? kpi(fmt.bn(debt.net_debt, 1), "по реестру траншей и кассе на дату") : null,
      kpi(fmt.bn(debt.net_debt_reported, 1), `отчётный на ${fmt.date(d.meta.facts_date)}`),
      reg.available ? kpi(fmt.bn(debt.term_debt, 1), "срочный долг по реестру") : null,
      kpi(fmt.bn(reg.available ? debt.cash : debt.cash_reported, 1), reg.available ? "касса на дату" : `касса на ${fmt.date(d.meta.facts_date)}`),
      kpi(fmt.bn(debt.undrawn_facilities, 1), "неиспользованные линии")),
    el("p", { class: "card-foot" }, closed
      ? `Модельный чистый долг сценария на конец последнего закрытого ${u.gen}, перекатанный потоком прошедшей части текущего, — им считается оценка. Реестровый — номиналы живых траншей минус касса: другая величина под своим именем.`
      : `Модельный — отчётный долг ${fmt.date(d.meta.facts_date)}, перекатанный потоком прошедшей части ${u.gen} (им считается оценка). `
        + (reg.available ? `Реестровый — номиналы живых траншей минус касса отчётной даты за вычетом погашений после неё. Расходятся они на поток ${u.gen} и на разницу учёта кассы.`
          : sentence(`Реестра траншей в выпуске нет: ${reg.reason || "причина не названа"}`))));
}

function bridgeCard(d, pick) {
  if (!pick) return card({ title: "Мост EV → капитал", span: 7 }, empty("Сценариев в выпуске нет."));
  const lines = pick.claims_lines || [];
  // Строка со знаком: положительное значение — требование (вычитается из EV),
  // отрицательное — актив (прибавляется к капиталу).
  const steps = [];
  let run = pick.ev;
  steps.push({ title: "Стоимость бизнеса (EV)", from: 0, to: pick.ev, total: true, value: pick.ev });
  for (const line_ of lines) {
    const next = run - line_.value;
    steps.push({ title: line_.title, from: run, to: next, value: line_.value, sign: line_.value < 0 ? "+" : "−" });
    run = next;
  }
  steps.push({ title: "Капитал до дисконта за управление", from: 0, to: pick.equity_before_governance, total: true, value: pick.equity_before_governance });
  steps.push({ title: `Дисконт за управление ${fmt.pct(d.meta.governance_discount, 2)}`, from: pick.equity_before_governance, to: pick.equity, rate: true });
  steps.push({ title: "Капитал акционеров", from: 0, to: pick.equity, total: true, value: pick.equity });
  const lo = Math.min(0, ...steps.map((s) => Math.min(s.from, s.to)));
  const hi = Math.max(...steps.map((s) => Math.max(s.from, s.to)));
  const dom = [lo, hi];
  const rows = el("div", { class: "wf" }, steps.map((s) => el("div", { class: cls("wf-row", s.total && "is-total") },
    el("span", { class: "wf-name" }, s.title),
    el("span", { class: "wf-bar" }, barTrack([{ from: s.from, to: s.to, cls: s.total ? "is-total" : s.to < s.from ? "is-down" : "is-up",
      tip: { title: s.title, rows: [["млрд ₽", s.total ? fmt.num(s.value, 1) : s.rate ? fmt.pct(d.meta.governance_discount, 2) : `${s.sign} ${fmt.num(Math.abs(s.value), 1)}`]] } }], dom)),
    el("span", { class: "wf-val" }, s.total ? fmt.num(s.value, 1) : s.rate ? `−${fmt.pct(d.meta.governance_discount, 2)}` : `${s.sign}${NBSP}${fmt.num(Math.abs(s.value), 1)}`))));
  return card({ title: `Мост EV → капитал: ${pick.title}`, span: 7, sub: "млрд ₽ на дату оценки; каждая строка — из выпуска, строки складываются в требования" },
    rows,
    el("div", { class: "kpis", style: "margin-top:16px" },
      kpi(fmt.rub(pick.price_published), "цена клетки на акцию: капитал / акции"),
      kpi(fmt.bn(pick.claims, 1), "требования всего")),
    el("p", { class: "card-foot" }, `Аренда не вычитается: база ${d.meta.basis}, обязательство по аренде в долг не входит — сверка баз ниже.`));
}

function evPartsCard(d, pick) {
  if (!pick) return card({ title: "Из чего EV", span: 5 }, empty("Нет в выпуске."));
  return card({ title: "Из чего стоимость бизнеса", span: 5, sub: `${pick.title}: APV, дисконт по ставке активов` },
    el("div", { class: "kpis" },
      kpi(fmt.bn(pick.pv_fcff, 1), `PV свободного потока с ${d.meta.open_period || d.meta.horizon[0]}`),
      kpi(fmt.bn(pick.pv_tax_shield, 1), "PV налогового щита и пула убытков"),
      kpi(fmt.pct(pick.terminal_share, 0), "доля терминала в EV"),
      kpi(fmt.x(pick.exit_multiple, 2), `мультипликатор выхода ${yearOf(d.meta.horizon[1])}`),
      kpi(fmt.pct(pick.r_long, 1), "ставка активов LT, номинал"),
      kpi(fmt.pct(pick.r_real, 1), "реальная ставка в терминале"),
      kpi(fmt.x(pick.max_leverage, 2), "максимум ЧД/EBITDA на пути"),
      kpi(fmt.x(pick.ev_ebitda_ltm, 2), "EV / EBITDA LTM")),
    el("p", { class: "card-foot" }, pick.narrative));
}

function annualCard(d, pick) {
  if (!pick || !pick.annual) return card({ title: "Годовая таблица" }, empty("Нет в выпуске."));
  const A = pick.annual;
  const firstLabel = periodShort(d.meta.first_period || d.meta.horizon[0]);
  const year = (r) => (r.partial ? `${r.year} · ${firstLabel}` : String(r.year));
  const mini = (title, key, f, color) => el("div", { class: "mini" },
    el("span", { class: "tile-label" }, title),
    linesChart([{ name: title, color, dots: true, r: 3, points: A.map((r) => [year(r), r[key]]) }],
      { xType: "band", categories: A.map(year), height: 150, left: 44, yFmt: f, tipFmt: f, xFmt: (k) => k.split(" · ")[0] }));
  return card({ title: `По годам: ${pick.title}`, sub: `млрд ₽; первый год — неполный (${periodLabel(d.meta.first_period || d.meta.horizon[0])}), его рычаг и покрытие считаны на скользящий год` },
    el("div", { class: "minis" },
      mini("Маржа EBITDA", "margin", (v) => fmt.num(v * 100, 1) + THIN + "%", "var(--model)"),
      mini("Свободный поток FCFF, млрд ₽", "fcff", (v) => fmt.num(v, 0), "var(--third)"),
      mini("ЧД / EBITDA", "leverage", (v) => fmt.num(v, 1) + "×", "var(--market)")),
    el("div", { style: "margin-top:18px" }, dataTable([
      { title: "Год", value: (r) => year(r), cls: "name" },
      { title: "Выручка", num: true, value: (r) => fmt.num(r.revenue, 0) },
      { title: "EBITDA", num: true, value: (r) => fmt.num(r.ebitda, 1) },
      { title: "Маржа", num: true, value: (r) => fmt.pct(r.margin, 2) },
      { title: "LFL", num: true, value: (r) => fmt.pct(r.lfl, 1) },
      { title: "Capex", num: true, value: (r) => fmt.num(r.capex, 1) },
      { title: "Capex / выручка", num: true, value: (r) => fmt.pct(r.capex_pct, 2) },
      { title: "Налог", num: true, value: (r) => fmt.num(r.tax, 1) },
      { title: "FCFF", num: true, value: (r) => fmt.num(r.fcff, 1) },
      { title: "Проценты", num: true, value: (r) => fmt.num(r.interest, 1) },
      { title: "Дивиденды", num: true, value: (r) => fmt.num(r.dividends, 1) },
      { title: "Чистый долг", num: true, value: (r) => fmt.num(r.net_debt, 1) },
      { title: "ЧД / EBITDA", num: true, value: (r) => fmt.x(r.leverage, 2) },
      { title: "Покрытие", num: true, value: (r) => r.interest_cover >= 99 ? "—" : fmt.x(r.interest_cover, 2) },
      { title: "Площадь, тыс. м²", num: true, value: (r) => fmt.num(r.area, 0) },
    ], A, { rowClass: (r) => (r.partial ? "is-muted" : null) })));
}

// Дивидендная лестница (положение о дивидендной политике): текущая ступень — по отчётному ЧД/EBITDA
// LTM правилом ядра; год и DPS — ожидание смеси заголовка; сценарные клетки — справочно.
function dividendsCard(d) {
  const dv = d.dividends;
  if (!dv || !Array.isArray(dv.ladder)) return card({ title: "Дивиденды" }, empty("Блока дивидендов в выпуске нет."));
  const cur = dv.current || {};
  const by = pathMix(dv.by_year) || [];
  const firstPay = by.find((r) => isNum(r.dps) && r.dps > 0);
  const sc = dv.scenarios || {};
  const names = Object.fromEntries((d.scenarios || []).map((s) => [s.name, s.title]));
  const scen = Object.keys(sc);
  const plot = columnsChart(by.map((r) => ({ key: r.year, value: r.dps, label: String(r.year), tipTitle: `${r.year}: DPS` })),
    { height: 200, valueName: "₽ на акцию", fmt: (v) => fmt.rub(v), short: (v) => fmt.num(v, 0), label: "Дивиденд на акцию по годам (ожидание)" });
  const fig = withTable(plot, () => dataTable([
    { title: "Год", value: (r) => String(r.year), cls: "name" },
    { title: "Дивиденды, млрд ₽", num: true, value: (r) => fmt.num(r.dividends, 1) },
    { title: "DPS, ₽", num: true, value: (r) => el("strong", {}, fmt.num(r.dps, 0)) },
    ...scen.map((k) => ({ title: `DPS: ${names[k] || k}`, num: true, value: (r) => { const row = (sc[k] || []).find((x) => x.year === r.year); return row ? fmt.num(row.dps, 0) : "—"; } })),
    { title: "ЧД / EBITDA", num: true, value: (r) => fmt.x(r.leverage_end, 2) },
    { title: "Доля клеток с выплатой", num: true, value: (r) => fmt.pct(r.share_paying, 0) },
  ], by));
  return card({ title: "Дивидендная лестница", tools: fig.button,
    sub: `${dv.basis || ""}; выплаты — с ${dv.dividends_from_year} года, целевой рычаг ${fmt.bookValue(dv.leverage_target, "times")}.` },
  el("div", { class: "split" },
    el("div", {},
      el("ol", { class: "ladder" }, dv.ladder.map((r) => el("li", { class: cls(r.rung === cur.rung && "is-current") },
        el("span", { class: "ladder-rung" }, r.rung === cur.rung ? "сейчас" : `ступень ${r.rung + 1}`),
        el("span", {}, r.title)))),
      el("div", { class: "kpis", style: "margin-top:14px" },
        kpi(fmt.x(cur.leverage_reported, 2), `ЧД/EBITDA LTM на ${fmt.date(cur.as_of)} (отчётная EBITDA ${fmt.num(cur.ebitda_ltm_reported, 1)})`),
        kpi(fmt.x(cur.leverage_canon, 2), `на проформенной EBITDA ${fmt.num(cur.ebitda_ltm_canon, 1)}`),
        firstPay ? kpi(`${firstPay.year} · ${fmt.rub(firstPay.dps)}`, "первая выплата по модели: год и DPS") : null)),
    el("div", {}, fig.box)),
  el("div", { class: "card-foot" },
    dv.notes ? el("p", {}, sentence(upperFirst(`DPS — ${dv.notes.dps}`)), " ", sentence(upperFirst(`доля клеток — ${dv.notes.share_paying}`))) : null));
}

// Две базы одной компании: модель — до МСФО 16 (аренда — расход, в долг не входит); МСФО 16 — с
// обязательством по аренде.
function basesCard(d) {
  const b = d.bases;
  if (!b || !b.anchor) return card({ title: "Сверка баз МСФО 16 и до МСФО 16" }, empty("Сверки баз в выпуске нет."));
  const a = b.anchor;
  const debt = b.debt || [];
  const plot = linesChart([
    { name: "ЧД до МСФО 16", color: "var(--model)", dots: true, r: 3.5, points: debt.map((r) => [r.date, r.ias17.net_debt]) },
    { name: "ЧД по МСФО 16", color: "var(--market)", dots: true, r: 3.5, points: debt.map((r) => [r.date, r.ifrs16.net_debt]) },
  ], { xType: "band", categories: debt.map((r) => r.date), height: 210, left: 46, yFmt: (v) => fmt.num(v, 0), tipFmt: (v) => fmt.bn(v, 1),
    xFmt: (k) => (/-12-31$/.test(k) ? yearOf(k) : fmt.date(k).slice(3)), label: "Чистый долг на двух базах" });
  const fig = withTable(plot, () => dataTable([
    { title: "Дата", value: (r) => fmt.date(r.date), cls: "name" },
    { title: "ЧД до МСФО 16", num: true, value: (r) => fmt.num(r.ias17.net_debt, 1) },
    { title: "ЧД/EBITDA", num: true, value: (r) => fmt.x(r.ias17.nd_ebitda, 2) },
    { title: "Аренда", num: true, value: (r) => fmt.num(r.lease_gap, 1) },
    { title: "ЧД по МСФО 16", num: true, value: (r) => fmt.num(r.ifrs16.net_debt, 1) },
    { title: "ЧД/EBITDA", num: true, value: (r) => fmt.x(r.ifrs16.nd_ebitda, 2) },
  ], debt));
  return card({ title: "Сверка баз: МСФО 16 и до МСФО 16", tools: fig.button,
    sub: sentence(upperFirst(clean(b.model_basis))) },
  el("div", { class: "kpis" },
    kpi(fmt.bn(a.net_debt_ias17, 1), `ЧД до МСФО 16 на ${fmt.date(a.date)} — в модели`),
    kpi(fmt.bn(a.lease_liabilities, 1), "обязательства по аренде"),
    kpi(fmt.bn(a.net_debt_ifrs16, 1), "ЧД по МСФО 16"),
    kpi(`${fmt.num(a.ebitda_ltm_ias17_reported, 1)} / ${fmt.num(a.ebitda_ltm_ias17_pro_forma, 1)}`, "EBITDA LTM до МСФО 16: отчётная / проформа"),
    kpi(fmt.num(a.ebitda_ltm_ifrs16, 1), "EBITDA LTM по МСФО 16")),
  el("div", { class: "split wide-left", style: "margin-top:16px" },
    el("div", {}, legend([["key-line key-model", "до МСФО 16"], ["key-line key-market", "МСФО 16"]]), fig.box),
    el("div", {}, dataTable([
      { title: "Год", value: (r) => r.period.replace(/^FY/, ""), cls: "name" },
      { title: "EBITDA до МСФО 16", num: true, value: (r) => fmt.num(r.ias17, 1) },
      { title: "МСФО 16", num: true, value: (r) => fmt.num(r.ifrs16, 1) },
      { title: "Разница", num: true, value: (r) => fmt.num(r.gap, 1) },
    ], b.ebitda || [], { cls: "compact" }))),
  b.source ? el("p", { class: "card-foot" }, `Источник: ${b.source.document}, ${b.source.lines}.`) : null);
}

// Годы стены подряд: год без погашений — пустой столбец, а не пропуск оси.
function wallItems(wall) {
  const years = Object.keys(wall || {}).map(Number).filter(Number.isFinite).sort((a, b) => a - b);
  const out = [];
  for (let y = years[0]; years.length && y <= years[years.length - 1]; y++) {
    out.push({ key: y, value: isNum(wall[String(y)]) ? wall[String(y)] : null, label: String(y), tipTitle: `Погашения ${y}` });
  }
  return out;
}

// Стена рисуется, а не только перечисляется: два соседних года по 250–290
// млрд ₽ видно глазом, в таблице это два числа из шести.
function wallChart(wall, cash) {
  return columnsChart(wallItems(wall), { height: 230, valueName: "млрд ₽", fmt: (v) => fmt.bn(v, 1), short: (v) => fmt.num(v, 0),
    refs: isNum(cash) ? [{ value: cash, text: `касса на дату ${fmt.num(cash, 1)}`, color: "var(--market)" }] : [],
    label: "Погашения срочного долга по годам" });
}

function wallCard(d) {
  const debt = d.debt || {};
  const items = wallItems(debt.wall);
  if (!items.length) {
    const reg = debt.register || {};
    return card({ title: "Стена погашений", span: 7 }, empty(reg.available === false ? sentence(`Реестра траншей в выпуске нет: ${reg.reason || "причина не названа"}`) : "Нет в выпуске."));
  }
  const fig = withTable(wallChart(d.debt.wall, debt.cash), () => dataTable([
    { title: "Год", value: (i) => i.label, cls: "name" },
    { title: "Погашения, млрд ₽", num: true, value: (i) => fmt.num(i.value, 1) },
  ], items.filter((i) => isNum(i.value))));
  return card({ title: "Стена погашений", span: 7, tools: fig.button,
    sub: `срочный долг по реестру на ${fmt.date((debt.register || {}).as_of || d.meta.valuation_date)}, млрд ₽; неиспользованные линии — ${fmt.bn(debt.undrawn_facilities, 1)}` },
  fig.box);
}

function sensitivityCard(d) {
  const debt = d.debt || {};
  const events = debt.events || [];
  return card({ title: "Чувствительность к ключевой ставке", span: 5, sub: "+1 п.п. ключевой — млрд ₽ процентов в год" },
    el("div", { class: "kpis" },
      kpi(fmt.bn(debt.rate_sensitivity_per_pp, 2), "брутто: весь плавающий долг"),
      kpi(fmt.bn(debt.rate_sensitivity_net_register_per_pp, 2), `нетто по реестру (плавающих ${fmt.pct(debt.floating_share_register, 0)})`),
      kpi(fmt.bn(debt.rate_sensitivity_net_per_pp, 2), `нетто по книге (плавающих ${fmt.pct(debt.floating_share_book, 0)})`)),
    el("p", { class: "card-foot" }, `Нетто — за вычетом дохода на кассу: касса дорожает вместе с долгом (доход ${fmt.num(debt.cash_yield_k, 2)} × ключевая).`),
    events.length ? el("div", { style: "margin-top:14px" },
      el("span", { class: "tile-label" }, `Погашения после отчётной даты ${fmt.date(d.meta.facts_date)}`),
      dataTable([
        { title: "Дата", value: (e) => fmt.date(e.date), cls: "name" },
        { title: "Выпуск", value: (e) => e.isin, cls: "txt" },
        { title: "млрд ₽", num: true, value: (e) => fmt.num(e.principal, 1) },
      ], events, { cls: "compact" })) : null);
}

// Корпоративное событие строкой: дата, класс (у сообщения «о существенном влиянии»
// название общее — сделку или дивиденды называет класс, уточнённый по тексту), название.
function eventLine(e) {
  return `${fmt.date(e.date)} — ${e.label ? e.label + ": " : ""}${e.title}`;
}

function registerCard(d) {
  const debt = d.debt || {};
  const tranches = debt.tranches || [];
  const kinds = { bond: "облигации", bank: "банк", other: "займ", refinanced: "рефинансирование" };
  const reg = debt.register || {};
  const recent = debt.recent_events || [];
  return card({ title: "Реестр траншей", sub: reg.available ? `на ${fmt.date(reg.as_of)}; ставка — фиксированная или ключевая + спред` : "реестра в выпуске нет" },
    tranches.length ? dataTable([
      { title: "Транш", value: (t) => t.name, cls: "name" },
      { title: "Вид", value: (t) => kinds[t.kind] || t.kind },
      { title: "Номинал, млрд ₽", num: true, value: (t) => fmt.num(t.principal, 2) },
      { title: "Погашение", num: true, value: (t) => fmt.date(t.maturity) },
      { title: "Ставка", num: true, value: (t) => isNum(t.fixed_rate) ? fmt.pct(t.fixed_rate, 2) : isNum(t.spread) ? `ключевая + ${fmt.num(t.spread * 100, 2)} п.п.` : "—" },
    ], tranches) : empty(sentence(`Реестра траншей в выпуске нет: ${reg.reason || "причина не названа"}`)),
    el("div", { class: "card-foot" },
      el("p", {}, recent.length ? sentence("События за 30 дней: " + recent.map(eventLine).join("; "))
        : "Корпоративных событий за 30 дней в ленте раскрытия нет.")));
}

/* ── экран «Допущения» ── */

let ALL_JUDGEMENTS = false;

function screenBook(d) {
  const root = el("div", { class: "screen" },
    screenHead("Допущения и проверки", `Книга допущений ${d.meta.book_version}`,
      "Каждое суждение — с диапазоном, ценой ошибки и источником. Ниже — проверки правдоподобия с письменными объяснениями и коридорами на истории компании, свежесть живых входов и история маржи и выручки."));
  root.append(el("div", { class: "grid" }, bookMetaCard(d)));
  root.append(el("div", { class: "grid section" }, judgementsCard(d)));
  root.append(el("div", { class: "grid section" }, gatesCard(d)));
  root.append(el("div", { class: "grid section" }, freshnessCard(d), curveCard(d)));
  const control = controlCard(d);
  if (control) root.append(el("div", { class: "grid section" }, control));
  root.append(el("div", { class: "grid section" }, historyCard(d)));
  return root;
}

function bookMetaCard(d) {
  const m = d.meta;
  const live = d.live || {};
  return card({ title: "Выпуск и книга" },
    el("div", { class: "kpis" },
      kpi(m.book_version, "версия книги допущений"),
      kpi(fmt.date(live.book_date || m.valuation_date), "дата книги (кривая, цена)"),
      kpi(fmt.date(m.valuation_date), "дата оценки"),
      kpi(fmt.date(m.facts_date), "отчётные факты"),
      isNum(live.book_age_days) ? kpi(fmt.days(live.book_age_days), "возраст книги") : null,
      kpi(`${periodShort(m.horizon[0])}–${periodShort(m.horizon[1])}`, `горизонт, шаг — ${modelUnit(d).one}`),
      kpi(fmt.date(m.generated_at) + " " + fmt.time(m.generated_at), "выпуск собран"),
      kpi((m.engine_commit || "—").slice(0, 7), "коммит кода")),
    el("p", { class: "card-foot" }, `Контракт ${d.schema}; хэш содержания ${String(m.payload_sha256 || "").slice(0, 16)}…; ${fmt.num((m.bytes || 0) / 1000, 0)} КБ.`));
}

// Значение суждения в книге.
const PATH_NAMES = { cpi: "ИПЦ (путь)", food_cpi: "продовольственный ИПЦ (путь)", "lt.inflation": "инфляция LT" };

// Диапазон суждения: «сдвиг» называется один раз — «сдвиг −0,4 … +0,6 п.п.».
function rangeText(low, high, unit) {
  const a = fmt.bookValue(low, unit), b = fmt.bookValue(high, unit);
  if (a.startsWith("сдвиг ") && b.startsWith("сдвиг ")) {
    return `сдвиг ${a.slice(6).replace(/\s*п\.п\.$/, "")} … ${b.slice(6)}`;
  }
  return `${a} … ${b}`;
}

function bookValueText(d, row) {
  if (isNum(row.value)) return formatByUnit(row.value, row.unit);
  const raw = String(row.value === null || row.value === undefined ? "" : row.value).trim();
  if (!raw) return "—";
  const titles = (d.regime_prob && d.regime_prob.titles) || {};
  const parts = raw.split(/;\s*|,\s+(?=[^,:;]+:\s)/).map((p) => p.trim()).filter(Boolean);
  if (parts.every((p) => /^(bear|base|bull)$/.test(p))) return "все состояния спроса";
  return parts.map((p) => {
    const m = /^([^:]+):\s*([-+]?\d*\.?\d+)$/.exec(p);
    const name = (k) => PATH_NAMES[k] || k.replace(/\.target\.LT$/, "")
      .replace(/^(stress|floor|partial|full)$/, (w) => (titles[w] || w).toLowerCase());
    return m ? `${name(m[1])} ${formatByUnit(Number(m[2]), row.unit)}` : name(p);
  }).join(" · ");
}

function judgementsCard(d) {
  const rows = d.assumptions && d.assumptions.length ? d.assumptions : null;
  if (!rows) return card({ title: "Суждения книги" }, missing("таблица суждений"));
  const center = d.fair_value.central;
  const prices = rows.flatMap((r) => [r.price_low, r.price_high]).filter(isNum);
  const dom = [0, Math.ceil(Math.max(center, ...prices) / 500) * 500];
  const body = el("div");
  const draw = () => {
    const shown = ALL_JUDGEMENTS ? rows : rows.slice(0, 12);
    body.replaceChildren(dataTable([
      { title: "Суждение", value: (r) => {
        const [code, ...rest] = String(r.label).split(" ");
        return el("span", {}, /^A-/.test(code) ? el("span", { class: "code" }, code) : null, " ", /^A-/.test(code) ? rest.join(" ") : r.label);
      }, cls: "name" },
      { title: "В книге", value: (r) => bookValueText(d, r), cls: "txt" },
      { title: "Диапазон", num: true, value: (r) => rangeText(r.low, r.high, r.unit) },
      { title: "Точка на краях, ₽", value: (r) => el("div", { class: "tn-cell" },
        barTrack([
          { from: Math.min(r.price_low, r.price_high), to: Math.min(center, Math.max(r.price_low, r.price_high)), cls: "is-down" },
          { from: Math.max(center, Math.min(r.price_low, r.price_high)), to: Math.max(r.price_low, r.price_high), cls: "is-up" },
        ].filter((s) => s.to > s.from), dom, { center }),
        el("span", { class: "tn-nums" }, `${fmt.num(r.price_low)} / ${fmt.num(r.price_high)}`)) },
      { title: "Цена ошибки", num: true, value: (r) => el("strong", {}, fmt.rub(r.spread)) },
    ], shown, { detail: (r) => r.source ? detailsBlock("Источник", r.source) : null }),
    rows.length > 12 ? el("button", { class: "view-toggle", type: "button", style: "margin-top:12px",
      on: { click: () => { ALL_JUDGEMENTS = !ALL_JUDGEMENTS; draw(); } } }, ALL_JUDGEMENTS ? "Показать главные 12" : `Показать все ${rows.length}`) : null);
  };
  draw();
  return card({ title: "Суждения книги по цене ошибки",
    sub: `Точка при центральных значениях на краях диапазона каждого суждения (по одному, остальные в центре); вертикаль — ${fmt.rub(center)}. Цена ошибки — размах точки на диапазоне.` },
  legend([["key-neg", "ниже точки"], ["key-model", "выше точки"]]), body);
}

function gatesCard(d) {
  const gates = d.gates || [];
  const broken = (d.checks && d.checks.invariants_broken) || 0;
  const corridors = (d.checks && d.checks.corridors) || {};
  const corridorText = (key) => {
    const c = corridors[key];
    if (!c) return null;
    const range = Array.isArray(c.corridor) ? `${fmt.pct(c.corridor[0], 1)}–${fmt.pct(c.corridor[1], 1)}`
      : Object.entries(c.corridor || {}).map(([w, r]) => `${w}: ${fmt.num(r[0], 1)}–${fmt.x(r[1], 1)}`).join("; ");
    const h = c.history || {};
    const annual = h.annual ? Object.entries(h.annual).map(([y, v]) => `${y.replace(/^FY/, "")} ${fmt.pct(v, 1)}`).join(" · ") : "";
    return detailsBlock(`Коридор ${range}: основание и история`, [
      sentence(upperFirst(c.basis || "")),
      annual ? ` История по годам: ${annual}.` : "",
      (h.annual_outside || []).length ? ` Вне коридора: ${h.annual_outside.map((y) => y.replace(/^FY/, "")).join(", ")}.` : "",
      isNum(h.current) ? ` Сейчас на одной базе: ${fmt.x(h.current, 2)} (${fmt.date(h.current_date)}).` : ""].join(""));
  };
  return card({ title: "Проверки правдоподобия",
    sub: "Гейт срабатывает на части клеток сетки; публикация требует письменного объяснения с ожидаемой массой. Совещательные гейты публикацию не блокируют. Инварианты — тождества, их нарушение блокирует выпуск." },
  el("div", { class: "kpis" },
    kpi(fmt.num(broken), "нарушенных инвариантов"),
    kpi(fmt.num(gates.length), "сработавших гейтов"),
    kpi(fmt.num(gates.filter((g) => g.explained && !g.stale).length), "с действующим объяснением")),
  gates.length ? el("div", { class: "gates" }, gates.map((g) => el("article", { class: "gate" },
    el("div", { class: "gate-head" },
      el("strong", {}, GATE_NAMES[g.key] || g.key),
      el("span", { class: "gate-mass" }, g.cells ? `${fmt.num(g.cells)} кл. · ${fmt.pct(g.mass, 1)} вероятности` : ""),
      g.pending ? badge("ждёт текста", "warn") : g.stale ? badge("объяснение просрочено", "bad") : g.explained ? badge("объяснён", "good") : badge("без объяснения", "bad"),
      g.mass_off ? badge(`масса вне коридора (ожидалось ${fmt.pct(g.mass_expected, 1)})`, "warn") : null,
      g.expiring ? badge("срок объяснения истекает", "warn") : null),
    g.valid_until ? el("p", { class: "muted small", style: "margin-top:4px" }, `Объяснение действует по ${fmt.date(g.valid_until)} включительно.`) : null,
    g.message ? el("p", { class: "ink-2", style: "margin-top:6px" }, ruText(g.message)) : null,
    g.explanation ? detailsBlock("Объяснение", ruText(g.explanation)) : null,
    corridorText(g.key)))) : empty("Ни один гейт не сработал."),
  Object.keys(corridors).some((k) => !gates.some((g) => g.key === k))
    ? el("div", { class: "card-foot" }, el("p", {}, "Коридоры несработавших гейтов:"),
      Object.keys(corridors).filter((k) => !gates.some((g) => g.key === k)).map((k) => el("div", {}, el("strong", {}, GATE_NAMES[k] || k), corridorText(k)))) : null);
}

function freshnessCard(d) {
  const live = d.live || {};
  const applied = Object.entries(live.applied || {});
  const names = { price: "цена акции", curve_observed: "кривая ОФЗ", valuation_date: "дата оценки", ofz_in: "ОФЗ-ИН",
    ofz_in_observed: "ОФЗ-ИН (линкеры)" };
  return card({ title: "Свежесть входов", span: 5, sub: `дата оценки ${fmt.date(d.meta.valuation_date)} — от неё, а не от времени сборки, меряется свежесть` },
    applied.length ? el("ul", { class: "list stacked" }, applied.map(([k, v]) => el("li", {}, el("span", { class: "t" }, names[k] || k.replace(/_/g, " ")), el("span", { class: "v" }, ruText(v)))))
      : empty("Живые входы не применены: выпуск собран на книжных входах."),
    live.price_reference ? el("p", { class: "card-foot" }, `Последняя принятая цена ${fmt.rub(live.price_reference)} на ${fmt.date(live.price_reference_date)}.`) : null,
    (live.degraded || []).length ? el("div", { class: "note-box", style: "margin-top:12px" }, el("strong", {}, "Деградация входов:"),
      el("ul", { class: "reasons" }, live.degraded.map((note) => el("li", {}, note)))) : null);
}

function curveCard(d) {
  const live = d.live || {};
  const obs = live.observed_curve && Object.keys(live.observed_curve).length ? live.observed_curve : null;
  const book = d.inputs && d.inputs.curve;
  const shift = live.curve_shift || {};
  if (!obs && !book) return card({ title: "Кривая ОФЗ", span: 7 }, empty("Кривой в выпуске нет."));
  const tenor = (k) => (k === "LT" ? 25 : Number(k));
  const series = [];
  if (book) series.push({ name: "кривая книги (мир M)", color: "var(--market)", dots: true, points: Object.entries(book).map(([t, v]) => [tenor(t), v]).sort((a, b) => a[0] - b[0]) });
  if (obs) series.push({ name: `наблюдаемая ${fmt.date(live.observed_curve_date)}`, color: "var(--ink)", width: 1.6, dots: true, r: 3,
    points: Object.entries(obs).map(([t, v]) => [Number(t), v]).sort((a, b) => a[0] - b[0]) });
  const plot = linesChart(series, { height: 230, xMin: 0, xMax: 26, xMap: Math.sqrt, xTicks: [1, 3, 5, 10, 25], xFmt: (t) => (t === 25 ? "LT" : `${fmt.num(t)} г.`),
    yFmt: (v) => fmt.num(v * 100, 0) + THIN + "%", tipFmt: (v) => fmt.pct(v, 2), label: "Кривая книги и наблюдаемая кривая ОФЗ" });
  const nodes = [...new Set(series.flatMap((s_) => s_.points.map((p) => p[0])))].sort((a, b) => a - b);
  const fig = withTable(plot, () => dataTable([
    { title: "Срок", value: (t) => (t === 25 ? "LT" : `${fmt.num(t, t < 1 ? 2 : 0)} г.`), cls: "name" },
    ...series.map((s_) => ({ title: s_.name, num: true, value: (t) => {
      const p = s_.points.find((q) => q[0] === t);
      return p ? fmt.pct(p[1], 2) : "—";
    } })),
  ], nodes));
  return card({ title: "Кривая ОФЗ: книга и рынок", span: 7, tools: fig.button,
    sub: "В оценку идут миры книги; живая кривая — диагностика. Сдвиг ≥ 50 б.п. на 5–10 годах или книга старше 45 дней — плашка «книгу пора обновлять»." + (obs ? "" : " Наблюдаемая кривая в этом выпуске не принята — причина в свежести входов.") },
  legend([["key-line key-market", "кривая книги (мир M)"], ...(obs ? [["key-line key-ink", "наблюдаемая"]] : [])]),
  fig.box,
  Object.keys(shift).length ? el("div", { class: "kpis", style: "margin-top:12px" }, Object.entries(shift).map(([t, v]) =>
    kpi(`${fmt.signed(v * 10000, 0)} б.п.`, `сдвиг узла ${t} г. к книге`))) : null);
}

function controlCard(d) {
  const cm = d.checks && d.checks.control_model;
  if (!cm || !cm.layers) return null;
  const keys = Object.keys(cm.layers);
  return card({ title: "Сверка с независимой контрольной моделью",
    sub: "Контрольная модель написана заново по тексту книги отдельным исполнителем; точное совпадение агрегата было бы провалом (общий код)." },
  dataTable([
    { title: "Слой", value: (k) => layerTitle(d, k), cls: "name" },
    { title: "V0 ядра", num: true, value: (k) => fmt.num(cm.layers[k].engine_v0, 1) },
    { title: "V0 контрольной", num: true, value: (k) => fmt.num(cm.layers[k].control_v0, 1) },
    { title: "Разрыв", num: true, value: (k) => fmt.signedPct(cm.layers[k].gap, 2) },
    { title: "Требования", num: true, value: (k) => fmt.signedPct(cm.layers[k].claims_gap, 2) },
    { title: "Заголовок", num: true, value: (k) => fmt.signedPct(cm.layers[k].headline_gap, 2) },
  ], keys),
  el("p", { class: "card-foot" }, cm.note || "",
    cm.worst_row ? ` Худшая строка: ${cm.worst_row.row} (${cm.worst_row.cell}) — ${fmt.pct(cm.worst_row.value, 1)}.` : "",
    cm.tolerances ? ` Допуски: ${Object.entries(cm.tolerances).map(([k, v]) => `${k} ${fmt.pct(v, 0)}`).join(", ")}.` : ""));
}

function historyCard(d) {
  const h = d.history;
  if (!h || !h.margin) return card({ title: "История" }, empty("Нет в выпуске."));
  const periods = Object.keys(h.margin).sort();
  const u = modelUnit(d);
  const plot = linesChart([{ name: "маржа EBITDA до МСФО 16", color: "var(--ink)", dots: true, r: 3.5, points: periods.map((p) => [p, h.margin[p]]) }],
    { xType: "band", categories: periods, height: 220, yFmt: (v) => fmt.num(v * 100, 1) + THIN + "%", tipFmt: (v) => fmt.pct(v, 2),
      xFmt: periodShort, label: "Маржа EBITDA по периодам модели" });
  const revenue = columnsChart(periods.map((p) => ({ key: p, value: h.revenue[p], label: periodShort(p), tipTitle: periodLabel(p) })),
    { height: 200, color: "var(--axis)", valueName: "млрд ₽", fmt: (v) => fmt.bn(v, 1), short: () => "", label: "Выручка по периодам модели" });
  const both = el("div", { class: "split" },
    el("div", {}, el("span", { class: "tile-label" }, "Маржа EBITDA"), plot),
    el("div", {}, el("span", { class: "tile-label" }, "Выручка, млрд ₽"), revenue));
  const fig = withTable(both, () => dataTable([
    { title: upperFirst(u.one), value: (p) => periodLabel(p), cls: "name" },
    { title: "Маржа EBITDA", num: true, value: (p) => fmt.pct(h.margin[p], 2) },
    { title: "Выручка, млрд ₽", num: true, value: (p) => fmt.num(h.revenue[p], 1) },
  ], periods));
  const breaks = (d.perimeter && d.perimeter.breaks_halves) || {};
  return card({ title: `История: маржа и выручка по периодам (${u.many})`, sub: `до МСФО 16 · винтаж «${h.vintage}»`, tools: fig.button },
    fig.box,
    el("p", { class: "card-foot" }, h.pit_warning ? sentence(upperFirst(h.pit_warning)) + " " : "",
      Object.keys(breaks).length ? `Разрывы периметра (сравнимость без проформы теряется): ${Object.keys(breaks).map(periodShort).join(", ")} — подробно на экране «Расчёт».` : ""));
}

/* ── пояс плашек ── */

// Предупреждения живут в ОДНОМ месте — в поясе, который `render` рисует перед любым экраном.
function banners(d) {
  const out = [];
  if (d.live && d.live.degraded_flag) {
    const notes = d.live.degraded || [];
    out.push(el("div", { class: "banner banner-degraded", role: "status" },
      el("span", { class: "banner-icon", "aria-hidden": "true" }, "!"),
      el("div", { class: "banner-body" },
        el("strong", {}, "Часть входов не свежая."),
        notes.length
          ? el("ul", { class: "reasons banner-list" }, notes.map((note) => el("li", {}, ruText(note))))
          : el("p", {}, "Причина в выпуске не названа — смотрите журнал такта на сервере."))));
  }
  const ageHours = (Date.now() - Date.parse(d.meta.generated_at)) / 3.6e6;
  if (ageHours > STALE_HOURS) {
    const days = Math.floor(ageHours / 24);
    out.push(plain("banner-stale", `Выпуску ${fmt.days(days)}: собран ${fmt.date(d.meta.generated_at)}, а конвейер обновляет панель каждый будний день. Числа ниже — не сегодняшние.`));
  }
  // Книга устарела — не сбой, а прямое ограничение напечатанного числа:
  // рынок ушёл, а оценка осталась на мирах книги. Причину называет сборка.
  const bookGate = (d.gates || []).find((g) => g.key === "book_update");
  if (bookGate) {
    const reason = bookGate.message || bookGate.explanation || "книга требует обновления: сборка не назвала причину.";
    out.push(plain("banner-book", upperFirst(ruText(reason))));
  }
  // Совещательные тревоги уровня выпуска: смена карточки акции после даты книги
  // («пересмотреть g») и капитал у нуля («нужна смена метода»).
  for (const key of ["security_change", "limited_liability"]) {
    const gate = (d.gates || []).find((g) => g.key === key);
    if (gate) out.push(plain("banner-book", `${GATE_NAMES[key]}: ${ruText(gate.message || gate.explanation || "причина не названа")}`));
  }
  if (d.meta.book_first_period_closed) {
    out.push(plain("banner-book", `Книга устарела: её первый прогнозный период (${periodLabel(d.meta.horizon[0])}) уже закрыт, а миры ставок и факты остались прежними. Оценка на ${fmt.date(d.meta.valuation_date)} считается на них.`));
  }
  // Корпоративные события последних семи дней: сделка, размещение, оферта,
  // дивиденды или листинг меняют долг или долю акционера.
  const week = new Date(Date.now() - 7 * 864e5).toISOString().slice(0, 10);
  const events = ((d.debt || {}).recent_events || []).filter((e) => e.date >= week);
  if (events.length) {
    out.push(plain("banner-event", `Корпоративные события: ${events.map(eventLine).join("; ")}.`));
  }
  return out.length ? [el("div", { class: "belt" }, out)] : [];
}

function plain(kind, message) {
  return el("div", { class: cls("banner", kind), role: "status" },
    el("span", { class: "banner-icon", "aria-hidden": "true" }, "!"),
    el("div", { class: "banner-body" }, message));
}

/* ── экраны и переходы ── */

const SCREENS = {
  overview: screenOverview,
  market: screenMarket,
  model: screenModel,
  report: screenReport,
  debt: screenDebt,
  book: screenBook,
};

function screenFromHash() {
  const name = decodeURIComponent(location.hash.replace(/^#/, ""));
  return Object.prototype.hasOwnProperty.call(SCREENS, name) ? name : "overview";
}

function render(name) {
  const app = $("#app");
  CURRENT = name;
  hideTip();
  for (const tab of document.querySelectorAll(".tab")) {
    const on = tab.dataset.screen === name;
    tab.setAttribute("aria-selected", String(on));
    tab.tabIndex = on ? 0 : -1;
    // На телефоне полоса вкладок едет: открытая вкладка всегда видна.
    if (on && tab.scrollIntoView) tab.scrollIntoView({ block: "nearest", inline: "nearest" });
  }
  let screen;
  try {
    screen = SCREENS[name](DATA);
  } catch (error) {
    // Один сломанный экран не роняет панель: пояс и остальные экраны живут.
    console.error(error);
    screen = el("div", { class: "screen" }, card({ title: "Экран не отрисовался", extra: "broken" },
      el("p", { class: "prose" }, "Ошибка витрины на этом экране; данные выпуска и остальные экраны не затронуты."),
      el("pre", { class: "fatal" }, String(error && error.stack || error).slice(0, 800))));
  }
  app.replaceChildren(...banners(DATA), screen);
  const title = document.querySelector(`.tab[data-screen="${name}"]`);
  document.title = `${title ? title.textContent + " · " : ""}${company(DATA)} — справедливая стоимость`;
}

function go(name, push = true) {
  if (!Object.prototype.hasOwnProperty.call(SCREENS, name)) name = "overview";
  if (push && location.hash !== `#${name}`) history.pushState(null, "", `#${name}`);
  else if (!push && location.hash !== `#${name}`) history.replaceState(null, "", `#${name}`);
  render(name);
  window.scrollTo({ top: 0 });
}

function wireTabs() {
  const tabs = [...document.querySelectorAll(".tab")];
  for (const tab of tabs) {
    tab.addEventListener("click", () => { if (DATA) go(tab.dataset.screen); });
    tab.addEventListener("keydown", (e) => {
      const i = tabs.indexOf(tab);
      const next = e.key === "ArrowRight" ? tabs[(i + 1) % tabs.length]
        : e.key === "ArrowLeft" ? tabs[(i - 1 + tabs.length) % tabs.length]
          : e.key === "Home" ? tabs[0] : e.key === "End" ? tabs[tabs.length - 1] : null;
      if (next) { e.preventDefault(); next.focus(); if (DATA) go(next.dataset.screen); }
    });
  }
  // Смена адреса (ссылки «Подробно →», «назад» и «вперёд» браузера) — hashchange; pushState из
  // вкладок его не зовёт, там экран рисует go().
  window.addEventListener("hashchange", () => {
    if (!DATA || screenFromHash() === CURRENT) return;
    render(screenFromHash());
    window.scrollTo({ top: 0 });
  });
  // «К содержанию» переводит фокус, а не адрес: иначе #app сбросил бы экран.
  const skip = $(".skip");
  if (skip) skip.addEventListener("click", (e) => { e.preventDefault(); $("#app").focus(); });
}

function wireTheme() {
  const button = $("#theme-toggle");
  if (!button) return;
  button.addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    if (window.__theme) window.__theme.remember(next);
    // Цвета графиков — токены CSS, перерисовка не нужна; подписи меряются
    // шрифтом, который от темы не зависит.
  });
}

/* ── шапка и подвал ── */

function releaseChip(d) {
  const chip = $("#release-chip");
  if (!chip) return;
  const ageHours = (Date.now() - Date.parse(d.meta.generated_at)) / 3.6e6;
  const flags = (d.live && d.live.degraded_flag)
    || (d.gates || []).some((g) => ["book_update", "security_change", "limited_liability"].includes(g.key))
    || d.meta.book_first_period_closed;
  chip.dataset.state = ageHours > STALE_HOURS ? "stale" : flags ? "warn" : "ok";
  $(".chip-text", chip).replaceChildren(
    el("span", {}, `Выпуск ${fmt.dateShort(d.meta.generated_at)}, ${fmt.time(d.meta.generated_at)}`),
    el("span", { class: "chip-extra" }, ` · книга ${d.meta.book_version}`));
  chip.title = ageHours > STALE_HOURS ? "Выпуск старше 96 часов" : flags ? "Есть предупреждения — см. плашки" : "Выпуск свежий";
}

// Имя компании в шапке и подвале — из выпуска.
function colophon(d) {
  const node = $("#colophon-release");
  if (node) {
    node.textContent = `Модель 850 · ${company(d)} (${ticker(d)}) · книга допущений ${d.meta.book_version} · оценка на ${fmt.date(d.meta.valuation_date)} · `
      + `факты на ${fmt.date(d.meta.facts_date)} · выпуск ${String(d.meta.payload_sha256 || "").slice(0, 12)} · код ${String(d.meta.engine_commit || "").slice(0, 7)} · контракт ${d.schema}`;
  }
  const brand = $(".brand-name");
  if (brand) brand.textContent = company(d);
}

/* ── загрузка ── */

function fatal(title, detail) {
  $("#app").replaceChildren(el("div", { class: "fatal" },
    el("h1", {}, title),
    el("p", {}, "Прежний выпуск, если он был, остаётся в хранилище; витрина покажет его, как только ответ придёт."),
    detail ? el("pre", {}, detail) : null));
  const chip = $("#release-chip");
  if (chip) { chip.dataset.state = "stale"; $(".chip-text", chip).textContent = "данные недоступны"; }
}

async function boot() {
  wireTips();
  wireTabs();
  wireTheme();
  let response;
  try {
    response = await fetch(API, { headers: { accept: "application/json" }, cache: "no-cache" });
  } catch (error) {
    fatal("Данные недоступны", `Сеть: ${error.message}`);
    return;
  }
  const body = await response.text();
  if (!response.ok) {
    fatal(response.status === 503 ? "Выпуск ещё не опубликован" : "Данные недоступны", `HTTP ${response.status}\n${body.slice(0, 600)}`);
    return;
  }
  try {
    DATA = JSON.parse(body);
  } catch (error) {
    fatal("Ответ не разобрался как JSON", body.slice(0, 400));
    return;
  }
  if (!DATA || !DATA.meta || !DATA.fair_value) {
    fatal("Выпуск без обязательных блоков", "нет meta или fair_value");
    return;
  }
  releaseChip(DATA);
  colophon(DATA);
  const name = screenFromHash();
  if (location.hash.replace(/^#/, "") !== name) history.replaceState(null, "", `#${name}`);
  render(name);
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
else boot();
