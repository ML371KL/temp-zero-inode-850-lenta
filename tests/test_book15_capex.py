# -*- coding: utf-8 -*-
"""Правила capex, сети и терминала книги 1.5 — закрытыми формулами из строк.

* `capex.disposal_proceeds_pct` — поступления от выбытия ОС;
* `capex.maintenance_area_share` — физическая доля поддерживающего capex идёт
  за площадью × ценами, а не за выручкой;
* `revenue.segments.<id>.new_space_density` и ротация сети по A-K5 в явном
  периоде и в терминале (по сегментам сети; рост группы в терминале — средний
  рост сегментов с весами выручки последнего года);
* `valuation.terminal.capex_growth_split` — статьи capex, индексируемые ценами,
  отдельным Гордоном с ростом π;
* линейная амортизация явного периода (A-K6; прежде `capex.da_method`);
* «прочий рост» вне вечного роста (прежде `revenue.other_growth_in_terminal`).

Каждое правило проверяется своей формулой на синтетической книге в схеме
Ленты (`tests/fixtures/toy_book`, по сегментам сети); значения ключей и
переносимое перезаякориванием состояние — громко. Сверка с контрольной моделью
850oa и проба инструмента перезаякоривания остались в песочнице переноса:
инструмент ещё не перенесён на сегменты (отчёт P2, открытые вопросы).
"""
from __future__ import annotations

import copy

import pytest

from model.book import (
    BookError,
    Cell,
    half_rate,
    path_value,
    periods,
    previous_period,
    validate_book,
)
from model.checks import check_invariants
from model.core import annuity_ratio, run_cell
from model.grid import build_grid
from tests.toy import toy_book

# Такт (ops/run.sh, TACT_TESTS): закрытые формы и инварианты ядра на синтетической
# книге — быстрые, защищают число до сборки.
pytestmark = pytest.mark.tact


def _with(A: dict, overrides: dict) -> dict:
    """Копия книги с подменёнными ключами (блоки создаются при нужде)."""
    A = copy.deepcopy(A)
    for dotted, value in overrides.items():
        node, keys = A, dotted.split(".")
        for key in keys[:-1]:
            node = node.setdefault(key, {})
        node[keys[-1]] = value
    return A


@pytest.fixture(scope="module")
def cell():
    return Cell.build(toy_book(), "H", "partial", "base")


def _network(A: dict) -> list[str]:
    """Сегменты сети с площадью (`yoy`/`level`) в порядке книги."""
    return [sid for sid, seg in A["revenue"]["segments"].items() if seg["mode"] != "revenue"]


def _space(A: dict, sid: str, cell: Cell) -> dict:
    return A["revenue"]["segments"][sid]["space"][cell.growth]


def _inflation_index(A: dict, cell: Cell) -> list[float]:
    """Индекс цен на конец каждого прогнозного полугодия — из траектории ИПЦ мира."""
    out, index = [], 1.0
    for p in periods(A["meta"]["first_period"], A["meta"]["last_period"]):
        index *= 1 + half_rate(path_value(A["worlds"][cell.world]["cpi"], p))
        out.append(index)
    return out


# ------------------------------------------------------ закрытые формулы


def test_disposal_proceeds_add_an_untaxed_share_of_revenue(cell):
    """FCFF каждого полугодия растёт ровно на pct × выручку, налог не меняется;
    терминал — на Гордона той же доли выручки терминальных полугодий."""
    pct = toy_book()["capex"]["disposal_proceeds_pct"]
    assert pct > 0
    base = run_cell(_with(toy_book(), {"capex.disposal_proceeds_pct": 0.0}), cell)
    moved = run_cell(toy_book(), cell)
    for old, new in zip(base.rows, moved.rows):
        assert new.tax_unlevered == old.tax_unlevered
        assert old.disposal_proceeds == 0.0
        assert new.disposal_proceeds == pytest.approx(pct * old.revenue, rel=1e-12)
        assert new.fcff - old.fcff == pytest.approx(pct * old.revenue, rel=1e-9, abs=1e-12)
    r, g = base.r_long, base.terminal_growth
    halves = [base.rows[-2].revenue * (1 + g) * pct, base.rows[-1].revenue * (1 + g) * pct]
    want = (halves[0] * (1 + r) ** 0.75 + halves[1] * (1 + r) ** 0.25) / (r - g)
    assert moved.terminal_flow_value - base.terminal_flow_value == pytest.approx(want, rel=1e-9)


def test_the_rows_add_up_to_fcff():
    """Поступления от выбытия — опубликованная строка: сверка `fcff_identity`
    (блокирующий инвариант выпуска) собирает FCFF из строк на всех 36 клетках,
    и ни один инвариант не срабатывает."""
    cells = build_grid(toy_book())
    blocking = [f for c in cells for f in check_invariants(c.result, label=c.cell.key)
                if f.blocking]
    assert not blocking, [(f.key, f.label, f.message) for f in blocking[:5]]
    for c in cells:
        for r in c.result.rows:
            assert r.disposal_proceeds > 0
            rebuilt = (r.ebitda - r.tax_unlevered - r.capex - r.nwc_change
                       - r.operating_cash_change + r.lease_adjustment
                       + r.disposal_proceeds)
            assert r.fcff == pytest.approx(rebuilt, rel=1e-12)


def test_straight_line_da_runs_the_anchor_base_off_and_spreads_each_vintage(cell):
    """D&A полугодия i = база якоря × (1 − (i+1)/2L)⁺ + сумма последних 2L когорт / 2L."""
    A = toy_book()
    rows = run_cell(A, cell).rows
    half_life = 2 * A["capex"]["asset_life_years"]
    da0, vintages = A["facts"]["anchor"]["da_pre16"], [A["facts"]["anchor"]["capex"]]
    for i, row in enumerate(rows):
        want = (da0 * max(0.0, 1 - (i + 1) / half_life)
                + sum(vintages[-int(half_life):]) / half_life)
        assert row.da == pytest.approx(want, rel=1e-12), row.period
        vintages.append(row.capex)
    assert len(rows) > half_life, "база якоря должна успеть списаться целиком"


def test_the_physical_share_of_maintenance_follows_area_times_prices(cell):
    """При s = 1 поддерживающий capex / (выручка × доля) = x_t / x_0,
    x = площадь середины полугодия × индекс цен / годовая выручка."""
    A = _with(toy_book(), {"capex.maintenance_area_share": 1.0})
    rows = run_cell(A, cell).rows
    F = A["facts"]
    index = _inflation_index(A, cell)
    anchor_revenue = F["revenue"][previous_period(rows[0].period)]
    area_prev = sum(F["segments"][sid]["area_end"] for sid in _network(A))
    revenue_prev, x0 = anchor_revenue, None
    for row, idx in zip(rows, index):
        x = (area_prev + row.area_end) / 2.0 * idx / (row.revenue + revenue_prev)
        x0 = x if x0 is None else x0
        pct = path_value(A["capex"]["maintenance_pct"][cell.capex], row.period)
        assert row.capex_maintenance / (row.revenue * pct) == pytest.approx(x / x0, rel=1e-9)
        area_prev, revenue_prev = row.area_end, row.revenue


def test_new_space_density_touches_only_the_forecast_cohorts(cell):
    """В первом полугодии созревают только исторические когорты, поэтому средняя
    эффективная площадь каждого сегмента сети меняется ровно на его открытия ×
    зрелость₀ × Δd / 2, а группы — на сумму этого по сегментам."""
    B, d = toy_book(), 1.0
    A = _with(B, {f"revenue.segments.{sid}.new_space_density": d for sid in _network(B)})
    base, moved = run_cell(B, cell).rows[0], run_cell(A, cell).rows[0]
    R = A["revenue"]
    want_total = 0.0
    for sid in _network(A):
        opened = (A["facts"]["segments"][sid]["area_end"]
                  * path_value(_space(A, sid, cell)["gross_open"], base.period) / 2.0)
        d_book = B["revenue"]["segments"][sid]["new_space_density"]
        want = opened * R["maturity_curve"][0] * (d - d_book) / 2.0
        got = moved.segments[sid].effective_area_avg - base.segments[sid].effective_area_avg
        assert got == pytest.approx(want, rel=1e-9, abs=1e-12), sid
        want_total += want
    assert moved.effective_area_avg - base.effective_area_avg == pytest.approx(want_total, rel=1e-9)


def test_terminal_growth_with_rotation_uplift_and_without_other_growth(cell):
    """Сегмент yoy: g_s = ((1 + чек)(1 + трафик) + lfl_offset)(1 + (d·зрелость −
    продуктивность закрытых)·закрытия) − 1; уровнем — ИПЦ мира × ротация;
    выручкой (опт) — LFL группы + growth. Группа: Σ w_s·g_s, w_s — доля
    сегмента в выручке последнего года (решение ведущего по вопросу P2 8)."""
    from model.book import lfl_offset_spec, segments

    A = toy_book()
    result = run_cell(A, cell)
    last, prev, R = result.rows[-1], result.rows[-2], A["revenue"]
    year = last.revenue + prev.revenue
    want = 0.0
    for seg in segments(A):
        lfl = (1 + last.lfl_ticket) * (1 + last.lfl_traffic) - 1
        if seg.mode == "yoy":
            offset = lfl_offset_spec(seg, cell.margin_regime)
            g = lfl + (path_value(offset, last.period) if offset is not None else 0.0)
        elif seg.mode == "level":
            g = path_value(A["worlds"][cell.world]["cpi"], last.period)
        else:
            g = lfl + path_value(seg.spec["growth"], last.period)
        if seg.network:
            close = path_value(seg.spec["space"][cell.growth]["close"], last.period)
            g = (1 + g) * (1 + (seg.new_space_density * R["maturity_curve"][-1]
                                - seg.closed_productivity) * close) - 1
        want += (last.segments[seg.id].revenue + prev.segments[seg.id].revenue) / year * g
    assert result.terminal_growth == pytest.approx(want, rel=1e-12)


def test_the_price_indexed_capex_is_capitalised_at_inflation(cell):
    """Замещающие открытия терминала — отдельным Гордоном с ростом π.

    Замещающие открытия полугодия h = площадь × закрытия/2 × цена м² × индекс
    цен. Цена м² больше на Δ: часть потока с ростом g не меняется, а часть с
    ростом π меняется на f_π = τ·(премия·Δcapex_π + (1 − премия)·ΔD&A_π/2) −
    Δcapex_π (ΔD&A_π — аннуитет π от Δcapex_π) и уходит в Гордон с ростом π: Δтерминала = Г_π(f_π). Приведи
    ядро эти статьи с ростом g или их D&A аннуитетом g — разность была бы другой.
    """
    A = toy_book()
    network = _network(A)
    price = {sid: A["capex"]["segments"][sid]["growth_capex_per_m2"] for sid in network}
    B = _with(A, {f"capex.segments.{sid}.growth_capex_per_m2": price[sid] * 1.1
                  for sid in network})
    base, moved = run_cell(A, cell), run_cell(B, cell)
    r, pi = base.r_long, A["worlds"][cell.world]["lt"]["inflation"]
    assert pi < r
    last = base.rows[-1]
    # Замещение по сегментам: площадь × закрытия/2 × цена м² своего формата.
    replaced = sum(last.segments[sid].area_end
                   * path_value(_space(A, sid, cell)["close"], last.period) / 2.0 * price[sid]
                   for sid in network)
    index = _inflation_index(A, cell)[-1]
    d_capex = []
    for _ in range(2):
        index *= 1 + half_rate(pi)
        d_capex.append(replaced * 0.1 * index)
    tau = A["tax"]["rate"]
    d_da = sum(d_capex) * annuity_ratio(pi, A["capex"]["asset_life_years"])
    # Налоговая D&A статьи: премия·capex полугодия + (1 − премия)·аннуитет π
    # (`tax.capex_tax_premium_share`, решение ведущего D25; без ключа — 0).
    prem = A["tax"].get("capex_tax_premium_share", 0.0)
    f_pi = [tau * (prem * c + (1 - prem) * d_da / 2.0) - c for c in d_capex]
    want = (f_pi[0] * (1 + r) ** 0.75 + f_pi[1] * (1 + r) ** 0.25) / (r - pi)
    assert moved.terminal_growth == base.terminal_growth
    assert moved.terminal_flow_value - base.terminal_flow_value == pytest.approx(want, rel=1e-9)
    assert want < 0, "дороже замещение — дешевле терминал"


# ------------------------------------------------------ проверка значений

@pytest.mark.parametrize("dotted,value", [
    ("capex.disposal_proceeds_pct", "0.0004"),
    ("capex.disposal_proceeds_pct", -0.0004),
    ("capex.maintenance_area_share", 1.5),
    ("capex.maintenance_area_share", True),
    ("revenue.segments.<seg>.new_space_density", 0.0),
])
def test_a_bad_value_is_refused_loudly(dotted, value):
    """Строка "false" непуста и потому истинна; 1 — не флаг; опечатка метода —
    не другой метод. Всё это отказ, а не молчаливое прочтение — и у книги, и у
    клетки."""
    dotted = dotted.replace("<seg>", _network(toy_book())[0])
    A = _with(toy_book(), {dotted: value})
    with pytest.raises(BookError, match=dotted.rsplit(".", 1)[-1]):
        validate_book(A)
    with pytest.raises(BookError):
        run_cell(A, Cell.build(A, "H", "partial", "base"))


# ------------------------------------------------- состояние, переносимое перезаякориванием
#
# Три правила помнят прошлое: когорты, дозревающие до d, база x₀ физической
# доли capex и база с когортами линейной D&A. Перезаякоривание переносит это
# состояние в книгу-кандидата (`facts.segments.<id>.new_area_dense_cohorts`,
# `capex.maintenance_area_base`, `facts.da_straight_line`) вместе с уровнем
# индекса эффективной площади сегмента (`facts.segments.<id>.eff_area_end`);
# испорченное значение — отказ книги.


@pytest.mark.parametrize("dotted,value", [
    ("facts.segments.<seg>.new_area_dense_cohorts", 9),
    ("facts.segments.<seg>.new_area_dense_cohorts", 1.0),
    ("capex.maintenance_area_base", -1.0),
    ("facts.da_straight_line", {"legacy": 40.0, "legacy_halves": 1}),
    ("facts.da_straight_line", {"legacy": 40.0, "legacy_halves": "1", "vintages": []}),
    ("facts.segments.<seg>.eff_area_end", "11000"),
])
def test_a_bad_carried_state_is_refused_loudly(dotted, value):
    dotted = dotted.replace("<seg>", _network(toy_book())[0])
    with pytest.raises(BookError, match=dotted.rsplit(".", 1)[-1]):
        validate_book(_with(toy_book(), {dotted: value}))
