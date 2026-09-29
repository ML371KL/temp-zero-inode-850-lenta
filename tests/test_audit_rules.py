# -*- coding: utf-8 -*-
"""Правила ядра, которых потребовал аудит книги 1.0 (30.09.2026; решения ведущего
`audit/LEAD-DECISIONS-AUDIT.md`).

Каждое правило — ключом книги (схема `model/book_schema.py` тем же коммитом);
без ключа ядро считает по прежнему правилу бит в бит. Ожидание — своей
арифметикой из книги и строк клетки, а не снято с движка. Книга —
синтетическая (`tests/fixtures/toy_book`): тесты проверяют механику.

* capex: дата удельных цен открытий и инфраструктуры (`capex.unit_price_basis`,
  control-model-03);
* налог: невычитаемая часть D&A якоря (`tax.nondeductible_da_anchor`,
  control-model-02).
"""
from __future__ import annotations

import copy

import pytest

from model.book import BookError, Cell, half_rate, path_value, validate_book
from model.core import run_cell
from tests.toy import toy_book

pytestmark = pytest.mark.tact

SPECS = [("H", "partial", "base"), ("N", "full", "high"), ("M", "stress", "low")]


def _run(A: dict, spec=("H", "partial", "base")):
    return run_cell(A, Cell.build(A, *spec))


# ================================================== capex: дата удельных цен


@pytest.mark.parametrize("spec", SPECS, ids="|".join)
def test_unit_prices_at_the_anchor_end_index_by_half_a_half_less(spec):
    """control-model-03: цены открытий и инфраструктуры названы на конец полугодия
    якоря — индекс цен полугодия p = Π_{q<p}(1 + h_q)·(1 + h_p)^0,5, то есть индекс
    ядра, делённый на (1 + h_p)^0,5; поддерживающий capex, выручка сегмента
    `level` (плотность — в средних ценах якоря) и всё до capex не меняются."""
    A = toy_book()
    A["capex"]["infra_from_year"] = 2026
    B = copy.deepcopy(A)
    B["capex"]["unit_price_basis"] = "anchor_end"
    cell = Cell.build(A, *spec)
    a, b = run_cell(A, cell).rows, run_cell(B, cell).rows
    cpi = A["worlds"][cell.world]["cpi"]
    index = 1.0
    for ra, rb in zip(a, b):
        h = half_rate(path_value(cpi, ra.period))
        index *= 1 + h
        unit = index / (1 + h) ** 0.5
        assert (rb.revenue, rb.ebitda, rb.capex_maintenance) == (ra.revenue, ra.ebitda,
                                                                 ra.capex_maintenance)
        opened = sum(s.opened * A["capex"]["segments"][sid]["growth_capex_per_m2"]
                     for sid, s in ra.segments.items() if s.area_end is not None)
        net = sum(max(0.0, s.opened - s.closed) for s in ra.segments.values())
        assert ra.capex_growth == pytest.approx(opened * index, rel=1e-12, abs=1e-15)
        assert rb.capex_growth == pytest.approx(opened * unit, rel=1e-12, abs=1e-15)
        assert rb.capex_infra == pytest.approx(net * A["capex"]["infra_capex_per_net_m2"] * unit,
                                               rel=1e-12, abs=1e-15)


def test_the_default_unit_price_basis_is_the_anchor_average():
    """Без ключа — средние цены полугодия якоря (правило 850oa) бит в бит; цены
    конца якоря дешевле во всех периодах и в терминале — EV выше."""
    A = toy_book()
    explicit = copy.deepcopy(A)
    explicit["capex"]["unit_price_basis"] = "anchor_average"
    end = copy.deepcopy(A)
    end["capex"]["unit_price_basis"] = "anchor_end"
    assert _run(explicit).ev == _run(A).ev
    assert _run(end).ev > _run(A).ev
    # терминал тоже в ценах конца якоря: EV выше и при нулевых открытиях явного
    # периода, где разница — только замещающие открытия терминала
    for B in (A, end):
        for seg in B["revenue"]["segments"].values():
            if "space" in seg:
                for level in seg["space"].values():
                    level["gross_open"] = 0.0
    assert _run(end).ev > _run(A).ev


def test_an_unknown_unit_price_basis_is_refused():
    A = toy_book()
    A["capex"]["unit_price_basis"] = "mid_2026"
    with pytest.raises(BookError, match="unit_price_basis"):
        validate_book(A)


# ================================================== налог: невычитаемая D&A якоря


@pytest.mark.parametrize("spec", SPECS, ids="|".join)
def test_the_nondeductible_anchor_da_is_added_back_while_the_anchor_base_runs_off(spec):
    """control-model-02: к налоговой базе прибавляется (1 − премия)·невычитаемая
    часть D&A якоря × max(0, 1 − (i + 1)/(2·срок)); учётная D&A, EBITDA и capex не
    меняются; после списания базы якоря прибавки нет."""
    A = toy_book()
    B = copy.deepcopy(A)
    B["tax"]["nondeductible_da_anchor"] = 0.81
    T = A["tax"]
    premium = T.get("capex_tax_premium_share") or 0.0
    half_life = 2 * A["capex"]["asset_life_years"]
    a, b = _run(A, spec).rows, _run(B, spec).rows
    seen = False
    for i, (ra, rb) in enumerate(zip(a, b)):
        assert (rb.ebitda, rb.da, rb.capex) == (ra.ebitda, ra.da, ra.capex)
        base = (ra.ebit + path_value(T["permanent_addback_pct"], ra.period) * ra.revenue
                - premium * (ra.capex - ra.da)
                + (1 - premium) * 0.81 * max(0.0, 1 - (i + 1) / half_life))
        assert rb.tax_unlevered == pytest.approx(max(0.0, T["rate"] * base), rel=1e-12, abs=1e-12)
        seen |= rb.tax_unlevered > ra.tax_unlevered
    assert seen
    assert _run(B, spec).ev <= _run(A, spec).ev


def test_the_nondeductible_share_follows_the_carried_anchor_base():
    """После перезаякоривания база якоря уже списывается `legacy_halves`
    полугодий — невычитаемая часть убывает с того же места (одна функция для
    ядра и инструмента); без ключа — ноль."""
    from model.core import nondeductible_da

    assert nondeductible_da(None, 0, 0, 24) == 0.0
    assert nondeductible_da(0.81, 0, 0, 24) == pytest.approx(0.81 * 23 / 24)
    assert nondeductible_da(0.81, 1, 0, 24) == nondeductible_da(0.81, 0, 1, 24)
    assert nondeductible_da(0.81, 5, 20, 24) == 0.0
    A = toy_book()
    A["tax"]["nondeductible_da_anchor"] = -0.1
    with pytest.raises(BookError, match="nondeductible_da_anchor"):
        validate_book(A)
