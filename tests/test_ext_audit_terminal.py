# -*- coding: utf-8 -*-
"""Терминал: правила ядра по внешнему аудиту 30.09.2026 (находки A04, A05, A06).

Каждое правило — ключом книги (схема `model/book_schema.py` тем же коммитом);
без ключа ядро считает по прежнему правилу бит в бит. Ожидание — своей
арифметикой: прямой суммой по полугодиям терминала от строк клетки и её поля
`terminal_halves`, а не той же замкнутой формулой. Книга — синтетическая
(`tests/fixtures/toy_book`): тесты проверяют механику.

* налоговая D&A — правило когорт явного периода, продолженное навсегда
  (`valuation.terminal.da_convention: cohort_explicit`, A04);
* уровни ОК и операционной кассы на границе — средним по выручке множителем
  двух полугодий (`valuation.terminal.boundary_levels: rolling`, A05);
* нулевой предел налога без рычага — в каждом полугодии, налог и процентный
  щит — точной суммой (`valuation.terminal.tax_floor: exact`, A06).
"""
from __future__ import annotations

import copy
import math

import pytest

from model.book import BookError, Cell, path_value, validate_book
from model.core import (annuity_ratio, positive_part_pv, run_cell, steady_cohort_da,
                        terminal_da_runoff, terminal_tax_pv)
from tests.toy import toy_book

pytestmark = pytest.mark.tact

SPECS = [("H", "partial", "base"), ("N", "full", "high"), ("M", "stress", "low")]


def _run(A: dict, spec=("H", "partial", "base")):
    return run_cell(A, Cell.build(A, *spec))


def _with_terminal(A: dict, **keys) -> dict:
    B = copy.deepcopy(A)
    B["valuation"]["terminal"].update(keys)
    return B


# ================================================== A04: D&A терминала по правилу когорт


def _terminal_path(halves, g, pi, back=40, ahead=80):
    """Capex полугодий терминала, продолженный назад и вперёд годовым ростом:
    список с полугодия −back по ahead − 1 и смещение нулевого полугодия."""
    def capex(i):
        year, half = divmod(i, 2)
        return halves[half][0] * (1 + g) ** year + halves[half][1] * (1 + pi) ** year
    return [capex(i) for i in range(-back, ahead)], back


@pytest.mark.parametrize("life", [10, 12, 12.5, 7.25])
def test_the_steady_cohort_da_is_the_explicit_rule_on_the_terminal_path(life):
    """Стационарная D&A полугодий терминала = правило когорт явного периода
    (1/(2L) в 2L следующих полугодиях, дробный срок — неполной последней долей)
    на capex терминала, продолженном назад, — в любом полугодии; при целом L
    первое полугодие — аннуитет/2, второе — на g₁·g·аннуитет/2 больше."""
    g, pi, halves = 0.06, 0.05, [(38.0, 9.5), (42.0, 10.5)]
    path, zero = _terminal_path(halves, g, pi)
    H = 2 * life
    steady_g = steady_cohort_da(halves[0][0], halves[1][0], g, H)
    steady_pi = steady_cohort_da(halves[0][1], halves[1][1], pi, H)
    for k in range(80):
        n, h = divmod(k, 2)
        direct = sum(path[zero + k - 1 - a] * min(1.0, H - a) for a in range(math.ceil(H))) / H
        assert steady_g[h] * (1 + g) ** n + steady_pi[h] * (1 + pi) ** n == pytest.approx(
            direct, rel=1e-12), k
    if life == int(life):
        for (c1, c2), x, steady in ((tuple(h[0] for h in halves), g, steady_g),
                                    (tuple(h[1] for h in halves), pi, steady_pi)):
            ratio = annuity_ratio(x, life)
            assert steady[0] == pytest.approx((c1 + c2) * ratio / 2, rel=1e-12)
            assert steady[1] == pytest.approx((c1 + c2) * ratio / 2 + c1 * x * ratio / 2, rel=1e-12)


def test_the_explicit_runoff_vanishes_on_the_history_of_the_terminal_halves():
    """`cohort_explicit`: фактические когорты — ровно полугодия терминала,
    продолженные назад, — поправки нет ни в одном полугодии."""
    g, pi, halves = 0.06, 0.05, [(38.0, 9.5), (42.0, 10.5)]
    path, zero = _terminal_path(halves, g, pi, back=30, ahead=0)
    annual = (sum(h[0] for h in halves), sum(h[1] for h in halves))
    out = terminal_da_runoff(path[:zero], 0.0, 0, 21, 24.0, *annual, g, pi, None, halves=halves)
    assert out == [] or max(abs(x) for x in out) < 1e-9
    heavy = terminal_da_runoff([1.2 * v for v in path[:zero]], 0.0, 0, 21, 24.0, *annual, g, pi,
                               None, halves=halves)
    assert all(x > 0 for x in heavy) and heavy[0] > heavy[-1] and len(heavy) <= 24


def test_the_old_runoff_history_is_not_the_history_of_the_annuity():
    """`cohort_runoff` вычитает полугодовые когорты годового capex — на этой
    истории поправка нулевая, но её D&A не равна аннуитету/2 (отношение
    (1 + √(1 + g))/2): сумма «аннуитет + поправка» не есть правило явного периода.
    Это и чинит `cohort_explicit`; прежнее правило остаётся как было."""
    L2, g, pi, cg, cp = 24.0, 0.06, 0.05, 80.0, 20.0
    cohorts = [cg / 2 * (1 + g) ** (-(j + 1) / 2) + cp / 2 * (1 + pi) ** (-(j + 1) / 2)
               for j in reversed(range(30))]
    out = terminal_da_runoff(cohorts, 0.0, 0, 21, L2, cg, cp, g, pi, None)
    assert len(out) <= L2 and max(abs(x) for x in out) < 1e-9
    da_of_that_history = sum(cohorts[-24:]) / 24
    annuity_half = (cg * annuity_ratio(g, 12) + cp * annuity_ratio(pi, 12)) / 2
    assert da_of_that_history == pytest.approx(35.836475, abs=1e-6)
    assert annuity_half == pytest.approx(35.332189, abs=1e-6)


def _terminal_direct(A: dict, res, years: int = 3000) -> tuple[float, float]:
    """Стоимость потока и щита терминала ПРЯМОЙ СУММОЙ по полугодиям: правило
    когорт явного периода на фактических когортах и capex терминала, налог
    τ·max(0, база) каждого полугодия, щит — налог без рычага минус налог с
    вычетом процентов. Всё — на конец явного периода, в серединах полугодий."""
    C, TX, M = A["capex"], A["tax"], A["margin"]
    rows, th = res.rows, res.terminal_halves
    last = rows[-1].period
    tau, premium = TX["rate"], TX.get("capex_tax_premium_share") or 0.0
    addback = path_value(TX["permanent_addback_pct"], last)
    nondeductible = TX.get("nondeductible_da_anchor") or 0.0
    H = 2 * C["asset_life_years"]
    g, r = res.terminal_growth, res.r_long
    pi = min(A["worlds"][res.cell.world]["lt"]["inflation"], r - 1e-4)
    anchor = A["facts"]["anchor"]
    cohorts = [anchor["capex"]] + [row.capex for row in rows]
    explicit = len(rows)
    flow_pv = shield_pv = 0.0
    for k in range(2 * years):
        n, h = divmod(k, 2)
        gg, pp = (1 + g) ** n, (1 + pi) ** n
        rev, ebitda = th[h]["rev"] * gg, th[h]["ebitda"] * gg
        capex = (th[h]["capex"] - th[h]["capex_pi"]) * gg + th[h]["capex_pi"] * pp
        base_share = max(0.0, 1 - (explicit + k + 1) / H)
        linear = (anchor["da_pre16"] - nondeductible) * base_share + sum(
            cohorts[-1 - a] * min(1.0, H - a) for a in range(math.ceil(H))
            if a < len(cohorts)) / H
        base = ebitda + addback * rev - premium * capex - (1 - premium) * linear
        tax = tau * max(0.0, base)
        flow = (ebitda - tax - capex - (th[h]["d_nwc"] + th[h]["d_opc"]) * gg
                + (M["cash_lease_adj_pct"] + C["disposal_proceeds_pct"]) * rev)
        discount = (1 + r) ** -(0.25 + 0.5 * k)
        flow_pv += flow * discount
        shield_pv += (tax - tau * max(0.0, base - th[h]["interest"] * gg)) * discount
        cohorts.append(capex)
    return flow_pv, shield_pv


@pytest.mark.parametrize("spec", SPECS, ids="|".join)
def test_the_cohort_explicit_terminal_is_the_explicit_rule_continued_forever(spec):
    """A04 + A06 вместе: стоимость терминала с `cohort_explicit` и `exact` — прямая
    сумма потоков, в которой D&A каждого полугодия — правило когорт явного периода
    на фактических когортах и capex терминала, а налог — τ·max(0, база)."""
    A = _with_terminal(toy_book(), da_convention="cohort_explicit", tax_floor="exact")
    res = _run(A, spec)
    flow, shield = _terminal_direct(A, res)
    assert res.terminal_flow_value == pytest.approx(flow, rel=1e-9)
    assert res.terminal_shield_value == pytest.approx(shield, rel=1e-9)


def test_the_cohort_explicit_rule_adds_the_second_half_cohort_and_changes_only_the_terminal():
    """`cohort_explicit` против `cohort_runoff`: явный период тот же; налоговая D&A
    второго полугодия несёт когорту первого — стоимость потока терминала выше;
    незнакомое слово — отказ; без ключа — аннуитет бит в бит."""
    A = toy_book()
    for spec in SPECS:
        old = _run(_with_terminal(A, da_convention="cohort_runoff"), spec)
        new = _run(_with_terminal(A, da_convention="cohort_explicit"), spec)
        assert [r.fcff for r in new.rows] == [r.fcff for r in old.rows]
        assert new.terminal_shield_value == old.terminal_shield_value
        assert new.terminal_flow_value > old.terminal_flow_value
        premium, life = A["tax"]["capex_tax_premium_share"], A["capex"]["asset_life_years"]
        g = new.terminal_growth
        pi = min(A["worlds"][spec[0]]["lt"]["inflation"], new.r_long - 1e-4)

        def linear(h):                  # линейная D&A полугодия из налоговой
            return (h["tax_da"] - premium * h["capex"]) / (1 - premium)

        first, second = new.terminal_halves
        topup = ((first["capex"] - first["capex_pi"]) * g * annuity_ratio(g, life)
                 + first["capex_pi"] * pi * annuity_ratio(pi, life)) / 2
        assert linear(second) - linear(first) == pytest.approx(topup, rel=1e-9)
        assert linear(old.terminal_halves[1]) == pytest.approx(linear(old.terminal_halves[0]),
                                                               rel=1e-12)
        assert linear(first) == pytest.approx(linear(old.terminal_halves[0]), rel=1e-12)
        assert _run(_with_terminal(A, da_convention="annuity"), spec).ev == _run(A, spec).ev
    with pytest.raises(BookError, match="da_convention"):
        validate_book(_with_terminal(A, da_convention="explicit"))


# ================================================== A05: уровни ОК и кассы на границе терминала


def _exit_area_book() -> dict:
    A = _with_terminal(toy_book(), revenue_base="exit_area")
    A["nwc"]["seasonal_june_excess"] = 0.0
    return A


def _level_share(A: dict, res) -> tuple[float, float]:
    """Доли ОК (с приобретённым периметром) и операционной кассы в годовой выручке
    терминала."""
    last = res.rows[-1].period
    nwc = path_value(A["nwc"]["nwc_pct"][res.cell.nwc], last)
    if A["nwc"].get("acquired_path") is not None:
        nwc += path_value(A["nwc"]["acquired_path"], last)
    return nwc, A["financing"]["operating_cash_pct"]


@pytest.mark.parametrize("spec", [("N", "full", "high"), ("H", "partial", "base")], ids="|".join)
def test_with_rolling_boundary_levels_the_first_terminal_year_is_stationary(spec):
    """A05: уровень конца первого терминального года = доля × годовая выручка
    терминала; у стационарной пары открывающий уровень — он же, делённый на
    (1 + g), и изменение за год — g/(1 + g) от уровня конца года. Множитель второго
    полугодия оставляет в изменении первого полугодия разовый остаток."""
    A = _exit_area_book()
    rolling = _run(_with_terminal(A, boundary_levels="rolling"), spec)
    second = _run(A, spec)
    g = rolling.terminal_growth
    nwc_share, cash_share = _level_share(A, rolling)
    th = rolling.terminal_halves
    year_revenue = th[0]["rev"] + th[1]["rev"]
    factors = [th[k]["rev"] / (rolling.rows[-2 + k].revenue * (1 + g)) for k in (0, 1)]
    assert abs(factors[0] - factors[1]) > 1e-4           # иначе тест ничего не ловит
    for share, key in ((nwc_share, "d_nwc"), (cash_share, "d_opc")):
        want = share * year_revenue * g / (1 + g)
        assert th[0][key] + th[1][key] == pytest.approx(want, rel=1e-11)
        residue = share * rolling.rows[-2].revenue * (factors[0] - factors[1])
        old = second.terminal_halves
        assert old[0][key] + old[1][key] == pytest.approx(want + residue, rel=1e-11)
        assert old[1][key] == th[1][key]
    assert [r.fcff for r in rolling.rows] == [r.fcff for r in second.rows]


@pytest.mark.parametrize("spec", [("N", "full", "high"), ("H", "partial", "base")], ids="|".join)
def test_the_rolling_boundary_does_not_capitalise_a_one_off_shift(spec):
    """Стоимость ОК и операционной кассы в терминале = −(прямая сумма изменений
    уровней правила терминала на 600 лет от стационарного открывающего уровня):
    разность стоимости потока терминала книги и той же книги без ОК и кассы."""
    A = _with_terminal(_exit_area_book(), boundary_levels="rolling")
    Z = copy.deepcopy(A)
    for key in Z["nwc"]["nwc_pct"]:
        Z["nwc"]["nwc_pct"][key] = 0.0
    Z["nwc"].pop("acquired_path", None)
    Z["financing"]["operating_cash_pct"] = 0.0
    a, z = _run(A, spec), _run(Z, spec)
    g, r = a.terminal_growth, a.r_long
    share = sum(_level_share(A, a))
    r1, r2 = (h["rev"] for h in a.terminal_halves)
    level, back, pv = share * (r1 + r2) / (1 + g), r2 / (1 + g), 0.0
    for n in range(600):
        for h, rev0 in enumerate((r1, r2)):
            rev = rev0 * (1 + g) ** n
            new = share * (rev + back)
            back = rev
            pv += (new - level) * (1 + r) ** (-0.25 - n - 0.5 * h)
            level = new
    assert a.terminal_flow_value - z.terminal_flow_value == pytest.approx(-pv, rel=1e-9, abs=1e-9)
    old = _run(_exit_area_book(), spec)
    assert abs(old.terminal_flow_value - a.terminal_flow_value) > 1e-3


def test_the_boundary_levels_default_to_the_second_half_and_need_the_exit_area_base():
    """Без ключа — множитель второго полугодия бит в бит; при базе `last_year`
    множители — единицы, и `rolling` ничего не меняет; незнакомое слово — отказ."""
    A = _exit_area_book()
    for spec in SPECS:
        assert _run(_with_terminal(A, boundary_levels="second_half"), spec).ev == _run(A, spec).ev
        plain = toy_book()
        assert _run(_with_terminal(plain, boundary_levels="rolling"), spec).ev == _run(plain, spec).ev
    with pytest.raises(BookError, match="boundary_levels"):
        validate_book(_with_terminal(A, boundary_levels="average"))


# ================================================== A06: нулевой предел налога терминала


def _positive_part_direct(a, b, x, y, r, n0, terms=4000) -> float:
    return math.fsum(max(0.0, a * (1 + x) ** n - b * (1 + y) ** n) * (1 + r) ** -n
                     for n in range(n0, n0 + terms))


@pytest.mark.parametrize("a,b,x,y,n0", [
    (50.0, 20.0, 0.02, 0.04, 0),        # база положительна и уходит в минус (π > g)
    (50.0, 20.0, 0.02, 0.04, 30),       # хвост с года после пересечения — пусто или остаток
    (20.0, 50.0, 0.06, 0.04, 0),        # отрицательна и становится положительной (g > π)
    (20.0, 50.0, 0.06, 0.04, 9),
    (50.0, 20.0, 0.06, 0.04, 0),        # положительна всегда
    (20.0, 50.0, 0.02, 0.04, 0),        # отрицательна всегда
    (-5.0, 20.0, 0.02, 0.04, 0),        # a ≤ 0: налога нет
    (50.0, 0.0, 0.03, 0.05, 0),         # b = 0: простой Гордон
    (50.0, -20.0, 0.03, 0.05, 2),       # b < 0: положительна всегда
    (-50.0, -80.0, 0.02, 0.05, 0),      # оба отрицательны: положительна, пока −a·ρ^n < −b … до пересечения нет
    (-50.0, -80.0, 0.05, 0.02, 0),      # оба отрицательны, знак меняется в другую сторону
    (30.0, 30.0, 0.04, 0.04, 0),        # a = b, x = y: ноль
    (40.0, 30.0, 0.04, 0.04, 0),        # x = y: постоянный знак
    (30.0, 30.0, 0.03, 0.05, 0),        # a = b: пересечение в n = 0
])
def test_the_positive_part_pv_is_the_direct_sum(a, b, x, y, n0):
    want = _positive_part_direct(a, b, x, y, 0.13, n0)
    assert positive_part_pv(a, b, x, y, 0.13, n0) == pytest.approx(want, rel=1e-12, abs=1e-12)


def test_the_terminal_tax_pv_is_the_direct_sum_over_the_halves():
    """Σ max(0, база) по полугодиям терминала: первые полугодия — с конечной
    поправкой (чётное и нечётное число), хвост — замкнуто; поправка может сама
    увести базу в минус в начале ряда."""
    x, y, g, pi, r = [60.0, 75.0], [22.0, 24.0], 0.025, 0.045, 0.125
    for deltas in ([], [3.0], [70.0, -4.0, 5.0], [1.0] * 24, [9.0, 8.0, 7.0, 6.0, 5.0] * 5):
        direct = 0.0
        for k in range(2 * 4000):
            n, h = divmod(k, 2)
            base = x[h] * (1 + g) ** n - y[h] * (1 + pi) ** n - (deltas[k] if k < len(deltas) else 0.0)
            direct += max(0.0, base) * (1 + r) ** -(0.25 + 0.5 * k)
        assert terminal_tax_pv(x, y, g, pi, r, deltas) == pytest.approx(direct, rel=1e-12), deltas


def _high_inflation_book(**terminal) -> dict:
    """Книга, где долгосрочная инфляция мира выше роста сети: база налога
    терминала через годы уходит в минус (у «Ленты» — режим «стресс»)."""
    A = _with_terminal(toy_book(), **terminal)
    for world, inflation in (("N", 0.085), ("H", 0.105), ("M", 0.10)):
        A["worlds"][world]["lt"]["inflation"] = inflation
    return A


@pytest.mark.parametrize("spec", [("N", "stress", "high"), ("H", "stress", "base"),
                                  ("M", "partial", "base")], ids="|".join)
@pytest.mark.parametrize("convention", ["annuity", "cohort_runoff", "cohort_explicit"])
def test_the_exact_tax_floor_is_the_direct_sum_and_cuts_the_linear_tail(spec, convention):
    """π заметно выше g: налог без рычага — τ·max(0, база) в каждом полугодии, щит
    ограничен налогом. Стоимость потока и щита терминала — прямая сумма (для
    `cohort_explicit` — по когортам, для прочих правил D&A — по стационарной D&A
    клетки с её поправкой) и ниже линейной капитализации."""
    A = _high_inflation_book(da_convention=convention, tax_floor="exact")
    res = _run(A, spec)
    old = _run(_with_terminal(A, tax_floor="first_year"), spec)
    g = res.terminal_growth
    pi = min(A["worlds"][spec[0]]["lt"]["inflation"], res.r_long - 1e-4)
    assert pi - g > 0.02, "инфляция не выше роста — тест ничего не проверяет"
    if convention == "cohort_explicit":
        flow, shield = _terminal_direct(A, res)
    else:
        flow, shield = _terminal_direct_steady(A, res, convention)
    assert res.terminal_flow_value == pytest.approx(flow, rel=1e-9)
    assert res.terminal_shield_value == pytest.approx(shield, rel=1e-9)
    assert res.terminal_flow_value < old.terminal_flow_value - 1e-3
    assert res.terminal_shield_value < old.terminal_shield_value - 1e-3
    assert [r.fcff for r in res.rows] == [r.fcff for r in old.rows]


def _terminal_direct_steady(A: dict, res, convention: str, years: int = 3000) -> tuple[float, float]:
    """Прямая сумма терминала для правил D&A без когорт в стационаре: налоговая
    D&A полугодия — стационарная клетки (`tax_da`: её часть по статьям с π растёт
    с π, остальная — с g) плюс поправка доамортизации (`cohort_runoff`)."""
    C, TX, M = A["capex"], A["tax"], A["margin"]
    rows, th = res.rows, res.terminal_halves
    last = rows[-1].period
    tau, premium = TX["rate"], TX.get("capex_tax_premium_share") or 0.0
    addback = path_value(TX["permanent_addback_pct"], last)
    g, r = res.terminal_growth, res.r_long
    pi = min(A["worlds"][res.cell.world]["lt"]["inflation"], r - 1e-4)
    runoff: list[float] = []
    if convention == "cohort_runoff":
        anchor = A["facts"]["anchor"]
        capex_pi = th[0]["capex_pi"] + th[1]["capex_pi"]
        runoff = terminal_da_runoff(
            [anchor["capex"]] + [row.capex for row in rows], anchor["da_pre16"], 0, len(rows),
            2 * C["asset_life_years"], th[0]["capex"] + th[1]["capex"] - capex_pi, capex_pi,
            g, pi, TX.get("nondeductible_da_anchor"))
    flow_pv = shield_pv = 0.0
    for k in range(2 * years):
        n, h = divmod(k, 2)
        gg, pp = (1 + g) ** n, (1 + pi) ** n
        rev, ebitda = th[h]["rev"] * gg, th[h]["ebitda"] * gg
        capex = (th[h]["capex"] - th[h]["capex_pi"]) * gg + th[h]["capex_pi"] * pp
        tax_da = ((th[h]["tax_da"] - th[h]["tax_da_pi"]) * gg + th[h]["tax_da_pi"] * pp
                  + (1 - premium) * (runoff[k] if k < len(runoff) else 0.0))
        base = ebitda + addback * rev - tax_da
        tax = tau * max(0.0, base)
        flow = (ebitda - tax - capex - (th[h]["d_nwc"] + th[h]["d_opc"]) * gg
                + (M["cash_lease_adj_pct"] + C["disposal_proceeds_pct"]) * rev)
        discount = (1 + r) ** -(0.25 + 0.5 * k)
        flow_pv += flow * discount
        shield_pv += (tax - tau * max(0.0, base - th[h]["interest"] * gg)) * discount
    return flow_pv, shield_pv


@pytest.mark.parametrize("spec", SPECS, ids="|".join)
@pytest.mark.parametrize("convention", ["annuity", "cohort_runoff", "cohort_explicit"])
def test_where_the_base_stays_above_the_interest_the_exact_floor_changes_nothing(spec, convention):
    """Рост не ниже инфляции цен capex и база больше процентов во всех полугодиях:
    точная сумма — та же линейная капитализация (поток и щит, rel 1e-12)."""
    A = _with_terminal(toy_book(), da_convention=convention)
    for world in A["worlds"].values():
        world["lt"]["inflation"] = 0.02
    res = _run(_with_terminal(A, tax_floor="exact"), spec)
    old = _run(A, spec)
    assert min(A["worlds"][spec[0]]["lt"]["inflation"], res.r_long) <= res.terminal_growth
    addback = path_value(A["tax"]["permanent_addback_pct"], res.rows[-1].period)
    assert all(h["ebitda"] - h["tax_da"] + addback * h["rev"] - h["interest"] > 0
               for h in res.terminal_halves)
    assert res.terminal_flow_value == pytest.approx(old.terminal_flow_value, rel=1e-12)
    assert res.terminal_shield_value == pytest.approx(old.terminal_shield_value, rel=1e-12)


def test_the_tax_floor_defaults_to_the_first_year():
    """Без ключа — предел только в первом терминальном году (850oa) бит в бит, в том
    числе там, где точная сумма дала бы другое число; незнакомое слово — отказ."""
    A = _high_inflation_book()
    for spec in SPECS + [("N", "stress", "high")]:
        assert _run(_with_terminal(A, tax_floor="first_year"), spec).ev == _run(A, spec).ev
    with pytest.raises(BookError, match="tax_floor"):
        validate_book(_with_terminal(A, tax_floor="always"))


# ================================================== книга «Ленты»: терминал — прямая сумма


@pytest.mark.needs_book
@pytest.mark.parametrize("spec", [(world, regime, capex) for world in ("N", "H", "M")
                                  for regime in ("stress", "floor", "partial", "full")
                                  for capex in ("low", "base", "high")], ids="|".join)
def test_on_the_book_the_terminal_is_the_direct_sum_over_the_halves(spec):
    """Книга «Ленты» с тремя правилами, все 36 клеток сетки: стоимость потока и щита
    терминала клетки — прямая сумма по полугодиям (когорты, налог τ·max(0, база),
    щит не больше налога); в «стрессе» (π выше g на 1,7–1,8 п.п.) она ниже линейной
    капитализации, в остальных клетках правила совпадают."""
    from model.book import book

    A = _with_terminal(book(), da_convention="cohort_explicit", boundary_levels="rolling",
                       tax_floor="exact")
    res = _run(A, spec)
    flow, shield = _terminal_direct(A, res, years=2500)
    assert res.terminal_flow_value == pytest.approx(flow, rel=1e-9)
    assert res.terminal_shield_value == pytest.approx(shield, rel=1e-9)
    linear = _run(_with_terminal(A, tax_floor="first_year"), spec)
    if spec[1] == "stress":
        assert res.terminal_flow_value < linear.terminal_flow_value - 1e-3
        assert res.terminal_shield_value < linear.terminal_shield_value - 1e-3
    else:
        # В «дне» и «частичной сходимости» π выше g на 0,01–0,2 п.п.: база меняет знак
        # через сотни лет, и разница правил тонет в дисконте.
        assert res.terminal_flow_value == pytest.approx(linear.terminal_flow_value, rel=1e-10)
        assert res.terminal_shield_value == pytest.approx(linear.terminal_shield_value, rel=1e-10)
