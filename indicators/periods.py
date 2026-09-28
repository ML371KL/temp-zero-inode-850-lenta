"""Периоды слоя индикаторов: квартал, полугодие, год — и отчёты, которые их раскрывают.

Модель считает полугодиями (`meta.period_unit: half`, D2), а Лента отчитывается
поквартально: нау-каст и журнал прогнозов живут на кварталах, правило A-P2u и
таблица «что даст отчёт» — на полугодии. У 850oa полугодие было прошито в
журнал (`current_half`, `next_half`, `period_index`, хвосты `H1/H2/FY`,
`half_bounds` в канале процентов); здесь одно определение периода на весь слой.

Идентификаторы:

* квартал — `2026Q3`, полугодие — `2026H2`, год — `2026FY`;
* `index` сравним только ВНУТРИ вида (кварталы между собой, полугодия между
  собой): «2026Q3 < 2026H2» — вопрос без ответа, и функция его не решает;
* границы включительные (`bounds`), по календарю.

**Отчёты.** Периоду соответствует ОТЧЁТ — событие раскрытия, а не дата
(`report_id`). Одно раскрытие закрывает несколько периодов, и журнал считает
его одним событием:

* 1 кв. — `<префикс>.q1_ГГГГ`, 3 кв. — `<префикс>.q3_ГГГГ` (операционные и
  финансовые результаты одним релизом через ≈26–34 дня);
* 2 кв. и 1П — `<префикс>.h1_ГГГГ` (релиз 2 кв. вместе с МСФО за 6 мес.);
* 4 кв., 2П и год — ДВА события (D2): выручка приходит операционными
  результатами (`<префикс>.q4_ГГГГ_ops`, ≈36–40 дней), маржа — финансовыми за
  12 мес. (`<префикс>.fyГГГГ`, ≈81–85 дней). Какое из двух — решает величина
  (`gives`): выручка или всё остальное.

Имена событий совпадают с идентификаторами календаря (`data/calendar.json`,
черновик книги: `lenta.q3_2026`, `lenta.q4_2026_ops`, `lenta.fy2026`,
`lenta.q1_2027`, `lenta.h1_2027`), и календарь сопоставляется с периодом по
этому имени, а не по дате.

Даты выхода — в `indicators.calendar` (календарь — источник правды); здесь —
только запасное правило лагов на периоды, которых в календаре нет.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from indicators import issuer

PERIOD_RE = re.compile(r"^(\d{4})(?:Q([1-4])|H([12])|(FY))$")

QUARTER, HALF, YEAR = "Q", "H", "FY"

# Самый ранний квартал, который слой прогнозирует: первый квартал первого
# прогнозного полугодия модели (`meta.first_period = 2026H2`, D2). Раньше него
# факты уже в истории, и прогнозировать их задним числом незачем.
FIRST_FORECAST_QUARTER = "2026Q3"

# Что даёт отчёт: выручку или всё остальное (маржа, проценты). От этого
# зависит, какое из двух событий 4 квартала закрывает период (D2).
GIVES_REVENUE, GIVES_MARGIN = "revenue", "margin"


@dataclass(frozen=True, order=True)
class Period:
    year: int
    kind: str
    number: int
    """Номер квартала (1–4) или полугодия (1–2); у года — 0."""

    def __str__(self) -> str:
        if self.kind == YEAR:
            return f"{self.year}FY"
        return f"{self.year}{self.kind}{self.number}"

    @property
    def start(self) -> date:
        if self.kind == YEAR:
            return date(self.year, 1, 1)
        months = 3 if self.kind == QUARTER else 6
        return date(self.year, (self.number - 1) * months + 1, 1)

    @property
    def end(self) -> date:
        if self.kind == YEAR:
            return date(self.year, 12, 31)
        months = 3 if self.kind == QUARTER else 6
        last_month = self.number * months
        if last_month == 12:
            return date(self.year, 12, 31)
        return date(self.year, last_month + 1, 1) - timedelta(days=1)


def parse(period: str) -> Period:
    """«2026Q3» → Period(2026, "Q", 3). Неразобранная строка — ValueError."""
    match = PERIOD_RE.match(str(period or ""))
    if not match:
        raise ValueError(f"не период: {period!r} (ожидается ГГГГQn, ГГГГHn или ГГГГFY)")
    year = int(match.group(1))
    if match.group(2):
        return Period(year, QUARTER, int(match.group(2)))
    if match.group(3):
        return Period(year, HALF, int(match.group(3)))
    return Period(year, YEAR, 0)


def is_period(period: str) -> bool:
    return bool(PERIOD_RE.match(str(period or "")))


def kind(period: str) -> str:
    return parse(period).kind


def is_quarter(period: str) -> bool:
    return is_period(period) and parse(period).kind == QUARTER


def is_half(period: str) -> bool:
    return is_period(period) and parse(period).kind == HALF


def bounds(period: str) -> tuple[date, date]:
    """Границы периода, обе включительно: «2026Q3» → (01.07.2026, 30.09.2026)."""
    p = parse(period)
    return p.start, p.end


def days_in(period: str) -> int:
    start, end = bounds(period)
    return (end - start).days + 1


def index(period: str) -> int:
    """Порядковый номер ВНУТРИ вида: кварталы 4·год + (n−1), полугодия 2·год + (n−1)."""
    p = parse(period)
    if p.kind == QUARTER:
        return 4 * p.year + p.number - 1
    if p.kind == HALF:
        return 2 * p.year + p.number - 1
    return p.year


def _from_index(kind_: str, value: int) -> str:
    if kind_ == QUARTER:
        return f"{value // 4}Q{value % 4 + 1}"
    if kind_ == HALF:
        return f"{value // 2}H{value % 2 + 1}"
    return f"{value}FY"


def shift(period: str, steps: int) -> str:
    """Период того же вида на `steps` шагов вперёд (назад — отрицательным)."""
    return _from_index(kind(period), index(period) + steps)


def next_period(period: str) -> str:
    return shift(period, 1)


def previous_period(period: str) -> str:
    return shift(period, -1)


def same_period_last_year(period: str) -> str:
    """Тот же квартал (полугодие) годом раньше: «2026Q3» → «2025Q3»."""
    p = parse(period)
    return str(Period(p.year - 1, p.kind, p.number))


def quarter_of(day: date) -> str:
    """Квартал КАЛЕНДАРЯ, в котором лежит день."""
    return f"{day.year}Q{(day.month - 1) // 3 + 1}"


def half_of(day: date) -> str:
    """Полугодие КАЛЕНДАРЯ, в котором лежит день."""
    return f"{day.year}H{1 if day.month <= 6 else 2}"


def quarters_of(period: str) -> tuple[str, ...]:
    """Кварталы периода по порядку: «2026H2» → («2026Q3», «2026Q4»)."""
    p = parse(period)
    if p.kind == QUARTER:
        return (str(p),)
    if p.kind == HALF:
        first = 2 * p.number - 1
        return (f"{p.year}Q{first}", f"{p.year}Q{first + 1}")
    return tuple(f"{p.year}Q{n}" for n in range(1, 5))


def half_of_quarter(quarter: str) -> str:
    """Полугодие, в которое входит квартал: «2026Q3» → «2026H2»."""
    p = parse(quarter)
    if p.kind != QUARTER:
        raise ValueError(f"{quarter}: ожидается квартал")
    return f"{p.year}H{1 if p.number <= 2 else 2}"


def quarter_key(quarter: str) -> str:
    """Ключ квартала в книге (`margin.quarter_offset_pp`, `revenue.quarter_share`): «Q3»."""
    p = parse(quarter)
    if p.kind != QUARTER:
        raise ValueError(f"{quarter}: ожидается квартал")
    return f"Q{p.number}"


def full_year(period: str) -> str:
    """Год периода: «2026Q3» и «2026H2» → «2026FY»."""
    return f"{parse(period).year}FY"


def first_quarter(period: str) -> str:
    return quarters_of(period)[0]


def last_quarter(period: str) -> str:
    return quarters_of(period)[-1]


def contains(outer: str, inner: str) -> bool:
    """Лежит ли период `inner` целиком внутри `outer` (по датам)."""
    a, b = bounds(outer)
    c, d = bounds(inner)
    return a <= c and d <= b


# ---------------------------------------------------------------- отчёты


def report_id(period: str, gives: str = GIVES_MARGIN) -> str:
    """Какой ОТЧЁТ раскрывает период. Пустая строка — период не разобран.

    Это тождество отчёта, а не догадка о дате: два периода одного раскрытия
    (2 кв. и 1П; 4 кв., 2П и год) — одно событие журнала, как бы их ни
    внесли. У 4 квартала событий два, и выбирает их `gives`: выручка приходит
    операционными результатами в феврале, маржа — годовым МСФО в марте (D2).
    """
    if not is_period(period):
        return ""
    p = parse(period)
    year = p.year
    closes_year = p.kind == YEAR or (p.kind == HALF and p.number == 2) or (
        p.kind == QUARTER and p.number == 4)
    if closes_year:
        name = f"q4_{year}_ops" if gives == GIVES_REVENUE else f"fy{year}"
    elif (p.kind == HALF and p.number == 1) or (p.kind == QUARTER and p.number == 2):
        name = f"h1_{year}"
    else:
        name = f"q{p.number}_{year}"
    return issuer.series(name)


# Запасное правило лагов: (центральная оценка, последний правдоподобный день)
# в днях после конца отчётного периода. Основание — даты существенных фактов о
# публикации релизов 2024–2026 (research/06 §4): Q1–Q3 — 26–34 дня, операционные
# 4 кв. — 36–40, годовые финансовые — 81–85. Календарь книги сильнее правила.
REPORT_LAG_DAYS = {
    "quarter": (30, 35),
    "q4_ops": (38, 43),
    "fy": (85, 90),
}


def fallback_report_window(period: str, gives: str = GIVES_MARGIN) -> tuple[date, date] | None:
    """(ожидаемая дата, крайняя дата) выхода отчёта по правилу лагов.

    Правило — запас на периоды, которых нет в календаре; дату объявляет
    компания, и правится она в `data/calendar.json`, а не здесь.
    """
    rid = report_id(period, gives)
    if not rid:
        return None
    year = parse(period).year
    name = rid[len(issuer.series("")):]
    if name.endswith("_ops") or name.startswith("fy"):
        closing = date(year, 12, 31)
        rule = "q4_ops" if name.endswith("_ops") else "fy"
    else:
        # q1 → 31.03, h1 → 30.06, q3 → 30.09: конец квартала, которым отчёт
        # закрывается (у h1 — второй квартал).
        closing = {"q1": date(year, 3, 31), "h1": date(year, 6, 30),
                   "q3": date(year, 9, 30)}[name.split("_")[0]]
        rule = "quarter"
    central, latest = REPORT_LAG_DAYS[rule]
    return closing + timedelta(days=central), closing + timedelta(days=latest)
