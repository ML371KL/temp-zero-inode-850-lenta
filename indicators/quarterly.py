"""Квартал поверх полугодовой модели: ожидание, гайденс, «маржа 2П из факта 3 кв.».

Модель считает полугодиями, нау-каст и журнал — кварталами (D2). Мост —
два ключа книги:

* `margin.quarter_offset_pp: {Q1..Q4}` — поправка к марже полугодия для
  квартала; внутри полугодия поправки обнуляются с весами выручки
  (Σ доля · поправка = 0), поэтому средняя маржа кварталов, взвешенная
  выручкой, равна марже полугодия;
* `revenue.quarter_share: {Q1..Q4}` — доля квартала внутри своего полугодия
  (Q1 + Q2 = 1 и Q3 + Q4 = 1, так её проверяет ядро). Нормировка
  s_Q / (s_Qa + s_Qb) (правило ядра, `model/quarters.py`) при таких долях
  ничего не меняет и оставлена как защита формулы.

**Ожидание модели на квартал** = маржа полугодия ядра + поправка квартала;
выручка = выручка полугодия ядра × нормированная доля квартала. Его строит
ядро — `model.quarters.expected_quarter(result, book, quarter_id)` (ответ —
словарь `quarter, period, revenue, margin, ebitda`; `result` — сетка
`build_grid`); своей арифметики квартала у слоя нет. `expected_quarter(A,
квартал)` здесь — один вход для всех потребителей слоя: строит сетку на
чистой книге, зовёт ядро и дописывает к ответу то, что нужно журналу и
витрине (полугодие, σ квартала, поправку и долю). Ключи книги проверяет
ядро (`model.book.quarter_rules`): доли — внутри полугодия, пара даёт 1,
поправки полугодия с весами выручки — ноль.

Сетка строится на книге БЕЗ наблюдений и шоков маржи за сам период и позже
(`nowcast._without_own_forecasts`): иначе нау-каст, поданный в книгу,
возвращался бы в неё базой.

**Ошибка квартала.** σ правила A-P2u (`joint.regime_update.sigma_pp`) — это
стационарный разброс ПОЛУГОДОВОЙ маржи. Квартал шумнее: если квартальные
отклонения внутри полугодия независимы и равны по дисперсии, σ квартала =
σ полугодия · √2. Это верхняя оценка (отклонения персистентны); на истории
Ленты отношение RMSE лучших эталонов квартал/полугодие ≈1,17 (study/04 §8.2).
Ключ книги `margin.quarter_sigma_pp`, если книга его заведёт, сильнее правила.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from indicators import periods
from model.quarters import expected_quarter as core_expected_quarter


@dataclass(frozen=True)
class QuarterExpectation:
    period: str
    half: str
    margin: float
    """Ожидание маржи EBITDA квартала (доля выручки, до МСФО 16)."""
    margin_se: float
    revenue: float | None
    """Ожидание выручки квартала, млрд ₽ (None — ядро выручку не дало)."""
    half_margin: float
    half_revenue: float | None
    offset_pp: float
    share: float
    """Доля квартала ВНУТРИ полугодия (нормированная)."""
    source: str
    """Кто посчитал — всегда ядро, `model.quarters.expected_quarter`."""

    def as_dict(self) -> dict:
        return asdict(self)


QUARTER_SIGMA_KEY = "quarter_sigma_pp"
SOURCE = "model.quarters.expected_quarter"


def quarter_se(A: dict) -> float:
    """σ маржи квартала: ключ книги, иначе σ полугодия A-P2u · √2 (см. шапку)."""
    explicit = (A.get("margin") or {}).get(QUARTER_SIGMA_KEY)
    if explicit is not None:
        return float(explicit)
    return float(A["joint"]["regime_update"]["sigma_pp"]) * math.sqrt(2.0)


def share_within_half(shares: dict, quarter: str) -> float:
    """Доля квартала внутри его полугодия: s_Q / (s_Qa + s_Qb)."""
    first, second = periods.quarters_of(periods.half_of_quarter(quarter))
    pair = float(shares[periods.quarter_key(first)]) + float(shares[periods.quarter_key(second)])
    return float(shares[periods.quarter_key(quarter)]) / pair


def offset_and_share(A: dict, quarter: str) -> tuple[float, float]:
    """Поправка маржи и доля выручки квартала внутри полугодия — из книги."""
    return (float(A["margin"]["quarter_offset_pp"][periods.quarter_key(quarter)]),
            share_within_half(A["revenue"]["quarter_share"], quarter))


def _clean_book(A: dict, half: str) -> dict:
    """Книга без наблюдений и шоков маржи за полугодие квартала и позже."""
    from indicators.nowcast import _without_own_forecasts

    return _without_own_forecasts(A, half)


def _from_core(result, A: dict, quarter: str) -> QuarterExpectation:
    """Ответ `model.quarters.expected_quarter` (словарь ядра) → `QuarterExpectation`.

    Ядро отдаёт `revenue`, `margin`, `ebitda` квартала; маржа и выручка
    полугодия восстанавливаются обратно по тем же ключам книги (m_H = m_Q −
    o_Q; R_H = R_Q / доля внутри полугодия), ошибка — `quarter_se`.
    """
    half = periods.half_of_quarter(quarter)
    if result["period"] != half or result["quarter"] != quarter:
        raise ValueError(f"ядро ответило за {result['quarter']}/{result['period']}, "
                         f"а спрошен {quarter}/{half}")
    offset, share = offset_and_share(A, quarter)
    margin, revenue = float(result["margin"]), float(result["revenue"])
    return QuarterExpectation(
        period=quarter, half=half, margin=margin, margin_se=quarter_se(A),
        revenue=revenue, half_margin=margin - offset, half_revenue=revenue / share,
        offset_pp=offset, share=share, source=SOURCE)



def expected_quarter(A: dict, quarter: str, *, grid=None) -> QuarterExpectation:
    """Ожидание модели на квартал — один вход для нау-каста, журнала и выпуска.

    Считает ядро (`model.quarters.expected_quarter`) на сетке `build_grid`,
    построенной на книге без собственных прогнозов за полугодие квартала и
    позже. `grid` — уже посчитанная сетка (такт не считает её дважды; тесты
    подают двойника с полями `probability`, `result.rows[].period/revenue/
    margin`); она обязана быть посчитана на той же книге.
    """
    if not periods.is_quarter(quarter):
        raise ValueError(f"{quarter}: ожидается квартал (ГГГГQn)")
    if grid is None:
        from model.grid import build_grid

        grid = build_grid(_clean_book(A, periods.half_of_quarter(quarter)))
    return _from_core(core_expected_quarter(grid, A, quarter), A, quarter)


# ------------------------------------------------------------------ гайденс


def guidance_half_margin(*, fy_margin: float, h1_revenue: float, h1_ebitda: float,
                         h2_revenue: float) -> float:
    """Маржа 2П, которую требует годовая цель компании: (g·(R₁ + R₂) − E₁) / R₂.

    «Не менее 7 % EBITDA за 2026» при 1П 6,06 % (39,28 / 648,5) и выручке 2П
    ≈740 млрд требует ≈7,8 % во 2П (D2: 7,7–7,9 % в зависимости от веса 2П).
    Гайденс — на отчётном периметре, поэтому и 1П — отчётное, не проформа.
    """
    return (fy_margin * (h1_revenue + h2_revenue) - h1_ebitda) / h2_revenue


def guidance_benchmark(period: str, *, guidance_year: int, fy_margin: float,
                       h1_revenue: float, h1_ebitda: float, h2_revenue: float,
                       quarter_offset_pp: dict | None = None) -> float | None:
    """Эталон «гайденс» для периода: None, если гайденс о нём ничего не говорит.

    Год гайденса — `fy_margin`; его 2П — маржа 2П из `guidance_half_margin`;
    кварталы 2П — маржа 2П + поправка квартала книги (без ключей — None:
    раскладывать полугодие на кварталы нечем).
    """
    if not periods.is_period(period) or periods.parse(period).year != guidance_year:
        return None
    p = periods.parse(period)
    if p.kind == periods.YEAR:
        return fy_margin
    half = guidance_half_margin(fy_margin=fy_margin, h1_revenue=h1_revenue,
                                h1_ebitda=h1_ebitda, h2_revenue=h2_revenue)
    if p.kind == periods.HALF:
        return half if p.number == 2 else None
    if p.number <= 2 or not quarter_offset_pp:
        return None
    return half + float(quarter_offset_pp[periods.quarter_key(period)])


def load_guidance(facts: dict, year: int) -> dict | None:
    """Гайденс года из фактов якоря (`anchor.json` черновика книги):
    `guidance_<год>.ebitda_margin_min` и отчётные 1П (выручка, EBITDA до МСФО 16).

    Факты — словарь файла `anchor.json`; нет ключа — None (эталона нет, и это
    честнее, чем число из кода).
    """
    block = facts.get(f"guidance_{year}") or {}
    margin = block.get("ebitda_margin_min")
    halves = facts.get("halves_for_book") or {}
    h1 = f"{year}H1"
    revenue = ((halves.get("revenue") or {}).get("reported") or {}).get(h1)
    ebitda = ((halves.get("ebitda_pre16") or {}).get("reported") or {}).get(h1)
    if margin is None or revenue is None or ebitda is None:
        return None
    return dict(year=year, fy_margin=float(margin), h1_revenue=float(revenue),
                h1_ebitda=float(ebitda), source=block.get("src", ""))


# ------------------------------------------------ «маржа 2П из факта 3 кв.»


def implied_half_margin(quarter_fact: float, *, quarter: str, quarter_offset_pp: dict,
                        quarter_share: dict, other_quarter_expectation: float | None = None,
                        quarter_expectation: float | None = None) -> dict:
    """Какая маржа полугодия следует из факта его ПЕРВОГО квартала (экран «Ближайший отчёт»).

    Факт квартала q₁ полугодия (3 кв. → 2П, 1 кв. → 1П) и ключи книги:

    * **при полной персистентности** отклонение первого квартала от его
      ожидания переносится на второй: маржа полугодия = m₁ − поправка(q₁).
      Это та маржа полугодия, при которой ожидание квартала q₁ по модели
      равнялось бы факту (Σ доля · поправка = 0 внутри полугодия);
    * **без персистентности** второй квартал — своё ожидание модели:
      маржа полугодия = s₁·m₁ + s₂·E[m₂] (нужен `other_quarter_expectation`).

    Возвращает обе величины (вторая — None без ожидания второго квартала) и
    правило текстом. Чувствительность цены к марже 2П считает выпуск (таблица
    «что даст отчёт» на полугодии 2026H2, D2).
    """
    p = periods.parse(quarter)
    if p.kind != periods.QUARTER or p.number not in (1, 3):
        raise ValueError(f"{quarter}: следствие для полугодия считается от его первого квартала")
    first, second = periods.quarters_of(periods.half_of_quarter(quarter))
    k1, k2 = periods.quarter_key(first), periods.quarter_key(second)
    o1 = float(quarter_offset_pp[k1])
    s1, s2 = share_within_half(quarter_share, first), share_within_half(quarter_share, second)
    persistent = quarter_fact - o1
    independent = (None if other_quarter_expectation is None
                   else s1 * quarter_fact + s2 * other_quarter_expectation)
    return dict(
        quarter=quarter, half=periods.half_of_quarter(quarter), quarter_fact=quarter_fact,
        implied_persistent=persistent, implied_independent=independent,
        quarter_expectation=quarter_expectation,
        surprise=(None if quarter_expectation is None else quarter_fact - quarter_expectation),
        rule=("полная персистентность: m(полугодие) = m(факт квартала) − поправка квартала "
              "книги; без персистентности: m(полугодие) = доля₁·m(факт) + доля₂·E[m второго "
              "квартала] (margin.quarter_offset_pp, revenue.quarter_share)"))
