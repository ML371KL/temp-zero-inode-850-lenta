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
  control-model-02);
* терминал: выручка от эффективной площади на выходе явного периода
  (`valuation.terminal.revenue_base: exit_area`, discount-terminal-01),
  доамортизация когорт capex явного периода в налоге терминала
  (`valuation.terminal.da_convention: cohort_runoff`, capex-06);
* capex: физическая часть поддерживающего capex по форматам и когортам
  (`capex.physical`, capex-04);
* печать: LFL = (1 + чек)(1 + трафик) − 1, как в выручке (control-model-06).

Перезаякоривание на ожидаемом пути с этими ключами = перекат бит в бит —
`tests/test_reanchor.py::test_the_audit_rules_make_reanchoring_equal_rolling`.
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


# ================================================== терминал: выручка от площади на выходе


def _still_network(A: dict, years=("2035", "2036")) -> dict:
    """Книга, у которой в последние годы горизонта сеть стоит: ни открытий, ни
    закрытий — младших когорт и ротации на выходе нет."""
    B = copy.deepcopy(A)
    for seg in B["revenue"]["segments"].values():
        for level in (seg.get("space") or {}).values():
            for key in ("gross_open", "close"):
                spec = level[key]
                spec = dict(spec) if isinstance(spec, dict) else {"LT": spec}
                for y in years:
                    spec[y] = 0.0
                spec["LT"] = 0.0
                spec.pop("LT_from", None)
                level[key] = spec
    return B


@pytest.mark.parametrize("spec", SPECS, ids="|".join)
def test_the_exit_area_base_equals_the_last_year_when_the_network_stands_still(spec):
    """discount-terminal-01: без открытий и закрытий на выходе младших когорт нет,
    эффективная площадь на выходе = средней последнего полугодия, ротации нет —
    множители 1, терминал тот же, что у правила «последний год × (1 + g)»."""
    A = _still_network(toy_book())
    B = copy.deepcopy(A)
    B["valuation"]["terminal"]["revenue_base"] = "exit_area"
    a, b = _run(A, spec), _run(B, spec)
    assert b.ev == pytest.approx(a.ev, rel=1e-12)


def test_the_exit_area_base_pays_the_last_openings_back_and_defaults_to_the_last_year():
    """Растущая сеть: открытия последнего года оплачены в явном периоде, их
    дозревание входит в выручку терминала — EV выше; без ключа — правило 850oa
    бит в бит; явный период не меняется."""
    A = toy_book()
    explicit = copy.deepcopy(A)
    explicit["valuation"]["terminal"]["revenue_base"] = "last_year"
    B = copy.deepcopy(A)
    B["valuation"]["terminal"]["revenue_base"] = "exit_area"
    spec = ("N", "full", "high")
    a, b = _run(A, spec), _run(B, spec)
    assert _run(explicit, spec).ev == a.ev
    assert [r.fcff for r in b.rows] == [r.fcff for r in a.rows]
    assert b.terminal_flow_value > a.terminal_flow_value
    assert b.ev > a.ev
    bad = copy.deepcopy(A)
    bad["valuation"]["terminal"]["revenue_base"] = "exit"
    with pytest.raises(BookError, match="revenue_base"):
        validate_book(bad)


def test_the_exit_area_factors_follow_the_formula():
    """Множитель полугодия k терминала = Σ_s R_s·A_ss,s/A_eff,ср,s(k)·(1 + τ_k·rot_s)/(1 + rot_s) / R,
    A_ss = A_eff(конец) + дозревание двух младших когорт − стационарная незрелость
    замещения; состояние сети на выходе восстановлено по строкам клетки (конец
    полугодия = 2·среднее − начало)."""
    from model.book import segments
    from model.core import _SegmentState, anchor_effective_end, terminal_revenue_factors

    A = toy_book()
    cell = Cell.build(A, "N", "full", "high")
    rows = run_cell(A, cell).rows
    m = A["revenue"]["maturity_curve"]
    states = [_SegmentState(seg, A, cell, m) for seg in segments(A)]
    network = [s for s in states if s.seg.network]
    expected = {}
    for s in network:
        end = anchor_effective_end(s.seg, m)
        for row in rows:
            step = row.segments[s.seg.id]
            end = 2 * step.effective_area_avg - end
            s.cohorts.append(step.opened)
            s.effective_hist[row.period] = step.effective_area_avg
        s.effective_end, s.area_end = end, rows[-1].segments[s.seg.id].area_end
        d, cp = s.seg.new_space_density, s.seg.closed_productivity
        close = path_value(s.space["close"], rows[-1].period)
        young = s.cohorts[-1] * (m[2] - m[0]) * d + s.cohorts[-2] * (m[2] - m[1]) * d
        steady = s.area_end * close / 2 * d * ((m[2] - m[0]) + (m[2] - m[1]))
        exit_area = end + young - steady
        rot = (d * m[2] - cp) * close
        expected[s.seg.id] = (exit_area / s.effective_hist[rows[-2].period] * (1 + rot / 4) / (1 + rot),
                              exit_area / s.effective_hist[rows[-1].period] * (1 + 3 * rot / 4) / (1 + rot))
    want = [sum(st.revenue * expected.get(sid, (1.0, 1.0))[k] for sid, st in row.segments.items())
            / row.revenue for k, row in enumerate(rows[-2:])]
    got = terminal_revenue_factors(network, m, [r.period for r in rows], rows[-2], rows[-1])
    assert got == pytest.approx(tuple(want), rel=1e-12)
    assert got[0] > 1.0 and got[1] > 1.0


# ================================================== терминал: доамортизация когорт явного периода


def test_the_runoff_vanishes_when_the_cohorts_are_the_annuity_history():
    """capex-06: если фактические когорты — ровно та история, которую предполагает
    аннуитет (capex растёт темпом терминала), поправки нет ни в одном полугодии;
    ряд конечен (не длиннее 2L полугодий)."""
    from model.core import terminal_da_runoff

    L2, g, pi, cg, cp = 24.0, 0.06, 0.05, 80.0, 20.0
    cohorts = [cg / 2 * (1 + g) ** (-(j + 1) / 2) + cp / 2 * (1 + pi) ** (-(j + 1) / 2)
               for j in reversed(range(30))]
    out = terminal_da_runoff(cohorts, 0.0, 0, 21, L2, cg, cp, g, pi, None)
    assert len(out) <= L2
    assert max(abs(x) for x in out) < 1e-9


def test_heavier_cohorts_and_the_anchor_base_are_amortised_after_the_horizon():
    """Когорты тяжелее аннуитетной истории — поправка положительна и гаснет к 2L;
    база якоря даёт вклад, пока не списана, её невычитаемая часть — нет."""
    from model.core import terminal_da_runoff

    L2, g, pi, cg, cp = 24.0, 0.06, 0.05, 80.0, 20.0
    hist = [cg / 2 * (1 + g) ** (-(j + 1) / 2) + cp / 2 * (1 + pi) ** (-(j + 1) / 2)
            for j in reversed(range(30))]
    heavy = [1.2 * v for v in hist]
    out = terminal_da_runoff(heavy, 0.0, 0, 21, L2, cg, cp, g, pi, None)
    assert all(x > 0 for x in out) and out[0] > out[-1] and len(out) <= L2
    with_base = terminal_da_runoff(hist, 12.0, 0, 21, L2, cg, cp, g, pi, None)
    assert with_base[0] == pytest.approx(12.0 * (1 - 22 / 24), abs=1e-9)
    assert with_base[1] == pytest.approx(12.0 * (1 - 23 / 24), abs=1e-9)
    nd = terminal_da_runoff(hist, 12.0, 0, 21, L2, cg, cp, g, pi, 0.8)
    assert nd[0] == pytest.approx(11.2 * (1 - 22 / 24), abs=1e-9)


def test_the_runoff_changes_only_the_terminal_and_defaults_to_the_annuity():
    """Без ключа — аннуитет (850oa) бит в бит; с `cohort_runoff` явный период тот же,
    меняется только стоимость потока терминала."""
    A = toy_book()
    explicit = copy.deepcopy(A)
    explicit["valuation"]["terminal"]["da_convention"] = "annuity"
    B = copy.deepcopy(A)
    B["valuation"]["terminal"]["da_convention"] = "cohort_runoff"
    for spec in SPECS:
        a, b = _run(A, spec), _run(B, spec)
        assert _run(explicit, spec).ev == a.ev
        assert [r.fcff for r in b.rows] == [r.fcff for r in a.rows]
        assert b.terminal_shield_value == a.terminal_shield_value
        assert b.terminal_flow_value != a.terminal_flow_value
    bad = copy.deepcopy(A)
    bad["valuation"]["terminal"]["da_convention"] = "runoff"
    with pytest.raises(BookError, match="da_convention"):
        validate_book(bad)


# ================================================== capex: физическая часть по форматам и когортам


PHYSICAL = {"steady_per_m2": {"hyper": 3.8, "conv": 6.8, "acq": 3.8, "diy": 1.9},
            "young_per_m2": {"hyper": 1.9, "conv": 2.7, "acq": 1.9, "diy": 1.1},
            "reconstruction_cycle_years": {
                "low": {"hyper": 16, "conv": 12.5, "acq": 16, "diy": 20},
                "base": {"hyper": 13, "conv": 3.0, "acq": 13, "diy": 15},
                "high": {"hyper": 10, "conv": 8, "acq": 10, "diy": 12}}}


def physical_book(**overrides) -> dict:
    A = toy_book()
    A["capex"]["physical"] = copy.deepcopy(PHYSICAL)
    A["capex"]["physical"].update(overrides)
    return A


def test_the_physical_weights_are_normalised_on_the_reference_network():
    """Веса — статьи м² формата к их среднему по эталонной сети (площади якоря):
    у эталонной сети Σ вес·площадь = площадь; эталонная сеть по умолчанию —
    площади сегментов на якоре."""
    from model.book import physical_rule

    A = physical_book()
    rule = physical_rule(A)
    ref = {sid: A["facts"]["segments"][sid]["area_end"] for sid in PHYSICAL["steady_per_m2"]}
    assert rule.reference_area == ref
    assert sum(rule.steady[s] * a for s, a in ref.items()) == pytest.approx(sum(ref.values()))
    assert rule.young["conv"] / rule.steady["conv"] == pytest.approx(2.7 / 6.8)
    assert rule.cycle_halves["base"]["conv"] == 6.0


@pytest.mark.parametrize("bad,match", [
    ({"young_per_m2": {"hyper": 9.0, "conv": 2.7, "acq": 1.9, "diy": 1.1}}, "young_per_m2.hyper"),
    ({"steady_per_m2": {"hyper": 3.8, "conv": 6.8, "acq": 3.8}}, "steady_per_m2"),
    ({"reconstruction_cycle_years": {"base": PHYSICAL["reconstruction_cycle_years"]["base"]}},
     "reconstruction_cycle_years"),
    ({"extra": 1}, "extra"),
])
def test_bad_physical_blocks_are_refused(bad, match):
    with pytest.raises(BookError, match=match):
        validate_book(physical_book(**bad))


def test_new_area_is_young_until_its_reconstruction_cycle_and_closures_shrink_the_reference():
    """Запас новой площади сверх эталонной — когортами: молодая площадь весит
    y_f, с возраста цикла — w_f; убыль снимается с младших когорт; сегмент,
    ушедший ниже эталона, теряет эталонную часть по весу w_f; середина
    полугодия — полусумма концов."""
    from model.book import physical_rule
    from model.core import PhysicalArea

    A = physical_book()
    rule = physical_rule(A)
    ref = dict(rule.reference_area)
    w, y = rule.steady, rule.young
    area = PhysicalArea(rule, "base", ref, "2026H2")          # цикл conv — 6 полугодий
    base_index = 2 * 2026 + 1                                 # индекс 2026H2
    path = []
    conv = ref["conv"]
    for k in range(10):
        conv += 10.0 if k < 8 else -15.0                      # рост 8 полугодий, затем убыль
        areas = dict(ref, conv=conv, hyper=ref["hyper"] - 5.0 * (k + 1))
        path.append((areas, area.step(areas, base_index + k)))
    # конец полугодия k: эталонная часть hyper уменьшается, conv — на эталоне
    ends = [(sum(w[s] * min(a, ref[s]) for s, a in areas.items())) for areas, _ in path]
    assert path[0][1][0] == pytest.approx((sum(w[s] * ref[s] for s in ref) + ends[0]) / 2)
    # когорта conv полугодия 0 молода до возраста 6 полугодий, дальше зрелая
    new_end_5 = 10.0 * 6 * y["conv"]                          # на конец k = 5 все 6 когорт молоды
    new_end_6 = 10.0 * w["conv"] + 10.0 * 6 * y["conv"]       # на конец k = 6 первая когорта созрела
    assert path[6][1][1] == pytest.approx((new_end_5 + new_end_6) / 2)
    # убыль по 15 в полугодия 8 и 9 снимается с младших когорт: запас 80 → 65 → 50,
    # остаются пять старших когорт по 10
    assert [amount for _, amount in area.cohorts["conv"]] == pytest.approx([10.0] * 5)
    assert area.steady_area(path[-1][0]) == pytest.approx(
        sum(w[s] * a for s, a in path[-1][0].items()))


def test_with_uniform_weights_and_a_flat_key_the_physical_part_keeps_the_old_shape():
    """Равные веса (y = w = 1), ровный ключ A-K1: эталонная часть + новая = вся
    площадь, и физическая часть идёт за невзвешенной площадью, как в правиле
    850oa; отличается только база x₀ (эталонная сеть якоря против площади
    середины первого полугодия) — постоянным множителем во всех полугодиях."""
    A = toy_book()
    for level in A["capex"]["maintenance_pct"]:
        A["capex"]["maintenance_pct"][level] = 0.02
    B = copy.deepcopy(A)
    ids = ["hyper", "conv", "acq", "diy"]
    B["capex"]["physical"] = {"steady_per_m2": {s: 1.0 for s in ids},
                              "young_per_m2": {s: 1.0 for s in ids},
                              "reconstruction_cycle_years": {lv: {s: 10 for s in ids}
                                                             for lv in ("low", "base", "high")}}
    s = A["capex"]["maintenance_area_share"]
    cell = Cell.build(A, "N", "full", "high")
    a, b = run_cell(A, cell).rows, run_cell(B, cell).rows
    ratios = [(rb.capex_maintenance - rb.revenue * 0.02 * (1 - s))
              / (ra.capex_maintenance - ra.revenue * 0.02 * (1 - s)) for ra, rb in zip(a, b)]
    ref = sum(A["facts"]["segments"][sid]["area_end"] for sid in ids)
    first_mid = sum(seg.area_end - (seg.opened - seg.closed) / 2 for seg in a[0].segments.values()
                    if seg.area_end is not None)
    assert ratios == pytest.approx([first_mid / ref] * len(ratios), rel=1e-12)


def test_growth_costs_less_physical_capex_by_cohort_and_the_default_is_the_old_rule():
    """Растущий «у дома»: новая площадь без реконструкций до цикла — физический
    capex явного периода ниже, чем у правила 850oa; без блока — правило 850oa бит
    в бит (прочие тесты capex на синтетической книге)."""
    A = toy_book()
    B = physical_book()
    B["capex"]["physical"]["reconstruction_cycle_years"]["high"]["conv"] = 30
    cell = Cell.build(A, "N", "full", "high")
    a, b = run_cell(A, cell).rows, run_cell(B, cell).rows
    assert b[-1].capex_maintenance < a[-1].capex_maintenance
    assert [r.revenue for r in b] == [r.revenue for r in a]


# ================================================== печать: LFL, который входит в выручку


def test_the_printed_lfl_is_the_lfl_of_the_revenue():
    """control-model-06: печатается LFL = (1 + чек)(1 + трафик) − 1 — тот, по
    которому считается выручка (строки клетки выпуска книги и годовая таблица)."""
    from model.book_results import _row

    result = _run(toy_book(), ("N", "full", "high"))
    for row in result.rows:
        want = (1 + row.lfl_ticket) * (1 + row.lfl_traffic) - 1
        assert row.lfl == pytest.approx(want, abs=1e-15)
        assert _row(row)["lfl"] == row.lfl
    for year in result.annual():
        halves = [r for r in result.rows if r.year == year["year"]]
        assert year["lfl"] == pytest.approx(sum(r.lfl for r in halves) / len(halves), abs=1e-15)
