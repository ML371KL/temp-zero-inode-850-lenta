"""Квартальный слой ожидания модели: квартал из полугодовых строк ядра.

Ядро считает полугодиями; отчётность и нау-каст — поквартальные. Ожидание
модели на квартал выводится из строк полугодия правилом книги, без второго
расчёта.

ИНТЕРФЕЙС ДЛЯ НАУ-КАСТА (этап P3)::

    expected_quarter(result, book, quarter_id) -> dict
        {"quarter": "2026Q3", "period": "2026H2",
         "revenue": млрд ₽, "margin": доля, "ebitda": млрд ₽}

* `result` — либо результат одной клетки (`model.core.CellResult`, из
  `run_cell`), либо сетка: список `model.grid.GridCell` (`build_grid`) или
  объект с полем `cells` (`model.engine.Release`) — тогда ожидание взвешено
  вероятностями клеток;
* `book` — книга, по которой посчитан `result` (`revenue.quarter_share`,
  `margin.quarter_offset_pp`; без них — `BookError`);
* `quarter_id` — «ГГГГQn» (`2026Q3`); его полугодие обязано быть строкой
  `result` (иначе `KeyError`: квартал вне горизонта ядра).

ПРАВИЛО (книга; `model.book.quarter_rules` проверяет ключи):

* полугодие квартала: Q1, Q2 → H1; Q3, Q4 → H2 того же года;
* выручка: R_Q = R_полугодия × s_Q / (s_Qa + s_Qb), s — `revenue.quarter_share`
  (доли кварталов внутри своего полугодия: s_Qa + s_Qb = 1, так их проверяет
  книга; деление оставлено — оно не меняет долей), Qa и Qb — кварталы полугодия;
* маржа: m_Q = m_полугодия + o_Q, o — `margin.quarter_offset_pp`; поправки
  внутри полугодия обнуляются с весами выручки (s_Qa·o_Qa + s_Qb·o_Qb = 0),
  поэтому EBITDA двух кварталов в сумме — ровно EBITDA полугодия (assert);
* EBITDA_Q = R_Q × m_Q;
* сетка: выручка и EBITDA — Σ p·(величина клетки), маржа — Σ p·m_Q (как
  ожидание маржи 850oa: средняя маржа ядра по сетке,
  `indicators.nowcast.model_expectation`).
"""

from __future__ import annotations

import re

from model.book import BookError, quarter_rules

QUARTER_ID = re.compile(r"^(\d{4})Q([1-4])$")
# Допуск тождества «поправки полугодия с весами выручки дают ноль».
OFFSET_TOL = 1e-12


def quarter_period(quarter_id: str) -> str:
    """«2026Q3» → «2026H2» — полугодие ядра, в котором лежит квартал."""
    match = QUARTER_ID.match(str(quarter_id))
    if not match:
        raise ValueError(f"квартал {quarter_id!r}: ожидается «ГГГГQn»")
    year, q = match.group(1), int(match.group(2))
    return f"{year}H{1 if q <= 2 else 2}"


def half_quarters(quarter_id: str) -> tuple[str, str]:
    """Кварталы полугодия квартала: («Q1», «Q2») или («Q3», «Q4»)."""
    q = int(quarter_id[-1])
    return ("Q1", "Q2") if q <= 2 else ("Q3", "Q4")


def _rules(book: dict) -> dict:
    rules = quarter_rules(book)
    if rules is None:
        raise BookError("квартальный слой: в книге нет revenue.quarter_share и "
                        "margin.quarter_offset_pp")
    return rules


def quarter_of_row(row, book: dict, quarter_id: str) -> dict:
    """Квартал из одной полугодовой строки ядра (`model.core.StepRow`)."""
    rules = _rules(book)
    period = quarter_period(quarter_id)
    if row.period != period:
        raise ValueError(f"квартал {quarter_id}: строка {row.period}, а нужна {period}")
    q = f"Q{quarter_id[-1]}"
    pair = half_quarters(quarter_id)
    share, offset = rules["share"], rules["offset"]
    weighted = sum(share[k] * offset[k] for k in pair)
    assert abs(weighted) <= OFFSET_TOL, (
        f"поправки маржи {pair} с весами выручки дают {weighted!r}, а не 0")
    revenue = row.revenue * share[q] / (share[pair[0]] + share[pair[1]])
    margin = row.margin + offset[q]
    return dict(quarter=quarter_id, period=period, revenue=revenue, margin=margin,
                ebitda=revenue * margin)


def _row_of(result, period: str):
    for row in result.rows:
        if row.period == period:
            return row
    raise KeyError(f"полугодие {period} вне строк ядра "
                   f"({result.rows[0].period}…{result.rows[-1].period})")


def expected_quarter(result, book: dict, quarter_id: str) -> dict:
    """Ожидание модели на квартал — клетки или сетки (см. шапку модуля)."""
    period = quarter_period(quarter_id)
    cells = getattr(result, "cells", result)
    if hasattr(cells, "rows"):                      # одна клетка
        return quarter_of_row(_row_of(cells, period), book, quarter_id)
    cells = list(cells)
    total = sum(c.probability for c in cells)
    if not cells or total <= 0:
        raise ValueError("квартальный слой: пустая сетка")
    parts = [(c.probability / total, quarter_of_row(_row_of(c.result, period), book, quarter_id))
             for c in cells]
    return dict(quarter=quarter_id, period=period,
                revenue=sum(p * q["revenue"] for p, q in parts),
                margin=sum(p * q["margin"] for p, q in parts),
                ebitda=sum(p * q["ebitda"] for p, q in parts))
