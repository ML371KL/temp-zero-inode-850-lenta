# -*- coding: utf-8 -*-
"""Правила ядра, которых потребовала книга 1.0 и решения ведущего по ней (этап I1b).

Каждое правило — ключом книги (схема `model/book_schema.py` тем же коммитом),
ожидание — своей арифметикой из книги и строк клетки, а не снято с движка.
Книга — синтетическая (`tests/fixtures/toy_book`): тесты проверяют механику.

* сеть: LFL опта = LFL группы + growth (B14), история эффективной площади
  правилом сети назад (E12), инфраструктура A-K4 по сегментам (D23),
  ошибка базы выручки `revenue_se` и основа выручки (B13), сегмент level без
  базы истории;
"""
from __future__ import annotations

import copy

import pytest

from model.book import BookError, Cell, half_rate, load_book, path_value, validate_book
from model.core import anchor_effective_end, effective_history, run_cell
from tests.toy import toy_book

pytestmark = pytest.mark.tact

SPECS = [("H", "partial", "base"), ("N", "full", "high"), ("M", "stress", "low")]


def _run(A: dict, spec=("H", "partial", "base")):
    return run_cell(A, Cell.build(A, *spec))


# ===================================================================== сеть


@pytest.mark.parametrize("spec", SPECS, ids="|".join)
def test_the_infrastructure_capex_is_summed_over_segments(spec):
    """A-K4 (решение ведущего D23): инфраструктура = Σ_s max(0, открыто_s −
    закрыто_s) × ставка × индекс цен, а не max(0, Σ открыто − Σ закрыто): закрытия
    одного формата не гасят потребность в РЦ другого."""
    A = toy_book()
    A["capex"]["infra_from_year"] = 2026
    cell = Cell.build(A, *spec)
    rows = run_cell(A, cell).rows
    cpi = A["worlds"][cell.world]["cpi"]
    index, seen_gap = 1.0, False
    for row in rows:
        index *= 1 + half_rate(path_value(cpi, row.period))
        net = sum(max(0.0, s.opened - s.closed) for s in row.segments.values())
        group = max(0.0, sum(s.opened - s.closed for s in row.segments.values()))
        seen_gap |= abs(net - group) > 1e-9
        assert row.capex_infra == pytest.approx(net * A["capex"]["infra_capex_per_net_m2"] * index,
                                                rel=1e-12, abs=1e-15), row.period
    if spec[1] == "stress":
        assert seen_gap, "правила по группе и по сегментам совпали всюду — проба пуста"


def _chain_history(facts: dict, spec: dict, maturity: list[float], closed: dict,
                   base: tuple[str, str]) -> dict:
    """Непрерывный индекс эффективной площади вперёд (как `netlib.eff_area_chain`
    листа «Сеть»): уровни шагов правилом сети, затем сдвиг ряда к уровню ядра на
    якоре (физическая минус незрелая); средние — полусуммы концов."""
    cohorts = list(facts["new_area_gross_hist"])
    d, cp = spec["new_space_density"], spec["closed_productivity"]
    dense = facts.get("new_area_dense_cohorts", 0)
    k = len(maturity) - 1
    levels = [0.0]
    for j, p in zip((len(cohorts) - 2, len(cohorts) - 1), base):
        older = cohorts[:j]
        maturing = sum(older[-a] * (maturity[a] - maturity[a - 1]) * (d if a <= dense else 1.0)
                       for a in range(1, min(k, len(older)) + 1))
        levels.append(levels[-1] - closed[p] * cp + cohorts[j] * maturity[0] * d + maturing)
    immature = sum(n * (1 - maturity[min(a, k)] * (d if a < dense else 1.0))
                   for a, n in enumerate(reversed(cohorts)))
    shift = facts["area_end"] - immature - levels[-1]
    levels = [x + shift for x in levels]
    return {base[0]: (levels[0] + levels[1]) / 2, base[1]: (levels[1] + levels[2]) / 2}


def _with_closed_history(A: dict, sid: str = "conv", closed=(4.0, 6.0)) -> dict:
    B = copy.deepcopy(A)
    facts = B["facts"]["segments"][sid]
    facts["new_area_dense_cohorts"] = 2
    facts["closed_area_hist"] = {"2025H2": closed[0], "2026H1": closed[1]}
    derived = _chain_history(facts, B["revenue"]["segments"][sid], B["revenue"]["maturity_curve"],
                             facts["closed_area_hist"], ("2025H2", "2026H1"))
    facts["eff_area_avg_hist"] = {p: round(v, 2) for p, v in derived.items()}
    return B


def test_the_effective_area_history_is_the_network_rule_run_backwards():
    """E12: с закрытиями истории (`closed_area_hist`) база «год к году» — вывод
    правила сети назад от уровня якоря; тот же ряд даёт непрерывный индекс листа
    «Сеть» вперёд. Записанные числа (два знака) сверяются при загрузке."""
    A = _with_closed_history(toy_book())
    validate_book(A)
    from model.book import check_effective_area_history, segments

    check_effective_area_history(A)
    seg = next(s for s in segments(A) if s.id == "conv")
    maturity = A["revenue"]["maturity_curve"]
    got = effective_history(seg, maturity, anchor_effective_end(seg, maturity), A)
    want = _chain_history(A["facts"]["segments"]["conv"], A["revenue"]["segments"]["conv"],
                          maturity, A["facts"]["segments"]["conv"]["closed_area_hist"],
                          ("2025H2", "2026H1"))
    for p in want:
        assert got[p] == pytest.approx(want[p], rel=1e-12), p
    bad = copy.deepcopy(A)
    bad["facts"]["segments"]["conv"]["eff_area_avg_hist"]["2026H1"] += 0.05
    with pytest.raises(BookError, match="closed_area_hist"):
        check_effective_area_history(bad)


def test_a_density_override_moves_the_history_base_with_closed_areas_only():
    """Подмена d «у дома» (ось полосы) при закрытиях истории двигает и базу «год к
    году»: выручка 2026H2 = R(2025H2)·A_eff(2026H2)/A_eff_d(2025H2)·(1 + LFL), где
    A_eff_d — история правилом сети при НОВОМ d (непрерывный индекс вперёд), а не
    числа книги; без закрытий истории база — числа книги."""
    A = _with_closed_history(toy_book())
    maturity = A["revenue"]["maturity_curve"]
    for d in (0.65, 0.95):
        B = copy.deepcopy(A)
        B["revenue"]["segments"]["conv"]["new_space_density"] = d
        facts = B["facts"]["segments"]["conv"]
        base = _chain_history(facts, B["revenue"]["segments"]["conv"], maturity,
                              facts["closed_area_hist"], ("2025H2", "2026H1"))
        assert abs(base["2025H2"] - facts["eff_area_avg_hist"]["2025H2"]) > 0.1, "проба пуста"
        for label, book_ in (("rule", B), ("stated", copy.deepcopy(B))):
            if label == "stated":
                del book_["facts"]["segments"]["conv"]["closed_area_hist"]
            row = _run(book_).rows[0]
            step = row.segments["conv"]
            hist = base["2025H2"] if label == "rule" else facts["eff_area_avg_hist"]["2025H2"]
            off = path_value(book_["revenue"]["segments"]["conv"]["lfl_offset"], row.period)
            want = (facts["revenue"]["2025H2"] * step.effective_area_avg / hist
                    * ((1 + row.lfl_ticket) * (1 + row.lfl_traffic) + off))
            assert step.revenue == pytest.approx(want, rel=1e-12), (d, label)


@pytest.mark.parametrize("key,value,match", [
    ("closed_area_hist", {"2025H2": 1.0}, "closed_area_hist"),
    ("closed_area_hist", {"2025H2": 1.0, "2026H1": -1.0}, "closed_area_hist"),
])
def test_bad_closed_histories_are_refused(key, value, match):
    A = toy_book()
    A["facts"]["segments"]["conv"][key] = value
    with pytest.raises(BookError, match=match):
        validate_book(A)
    B = toy_book()
    B["facts"]["segments"]["diy"]["closed_area_hist"] = {"2025H2": 1.0, "2026H1": 1.0}
    with pytest.raises(BookError, match="только режиму yoy"):
        validate_book(B)


def test_the_revenue_standard_error_follows_the_revenue_basis():
    """`revenue_se` (решение ведущего B13): те же полугодия, что у выручки; у отчёта
    ошибки нет, у расчёта и проформы — ≥ 0; у полугодия без выручки — null."""
    A = toy_book()
    A["facts"]["segments"]["acq"]["revenue_se"] = {"2025H1": 3.0, "2025H2": 3.3, "2026H1": 0.5}
    A["facts"]["segments"]["hyper"]["revenue_se"] = {"2025H1": 0.0, "2025H2": 0.0, "2026H1": 0.0}
    validate_book(A)
    assert _run(A).ev == _run(toy_book()).ev, "ошибка базы не двигает расчёт"
    for sid, se, match in [
            ("hyper", {"2025H1": 0.1, "2025H2": 0.0, "2026H1": 0.0}, "reported"),
            ("hyper", {"2025H1": 0.0, "2026H1": 0.0}, "те же полугодия"),
            ("acq", {"2025H1": -1.0, "2025H2": 0.0, "2026H1": 0.0}, "≥ 0"),
            ("acq", {"2025H1": "1", "2025H2": 0.0, "2026H1": 0.0}, "≥ 0")]:
        B = toy_book()
        B["facts"]["segments"][sid]["revenue_se"] = se
        with pytest.raises(BookError, match=match):
            validate_book(B)


def test_a_level_segment_needs_no_history_base_and_null_is_not_zero():
    """Режим level выручку истории не читает: база «Дом Ленты» до покупки не
    раскрыта — null (не ноль), основа и ошибка тоже null; выручка группы этих
    полугодий — сумма остальных сегментов. У режима yoy пропуск базы — отказ."""
    A = toy_book()
    diy = A["facts"]["segments"]["diy"]
    for p in ("2025H1", "2025H2"):
        A["facts"]["revenue"][p] -= diy["revenue"][p]
        diy["revenue"][p] = None
        diy["revenue_basis"][p] = None
    diy["revenue_se"] = {"2025H1": None, "2025H2": None, "2026H1": 0.2}
    A["facts"]["anchor"]["revenue_ltm"] = A["facts"]["revenue"]["2025H2"] + A["facts"]["revenue"]["2026H1"]
    validate_book(A)
    first = _run(A).rows[0].segments["diy"]
    assert first.growth is None and first.revenue > 0
    B = copy.deepcopy(A)
    B["facts"]["segments"]["diy"]["revenue_se"]["2025H1"] = 0.0
    with pytest.raises(BookError, match="без выручки"):
        validate_book(B)
    C = copy.deepcopy(A)
    C["facts"]["segments"]["diy"]["revenue_basis"]["2026H1"] = None
    with pytest.raises(BookError, match="null при выручке"):
        validate_book(C)
    D = toy_book()
    D["facts"]["segments"]["hyper"]["revenue"]["2025H2"] = None
    with pytest.raises(BookError, match="нет выручки"):
        validate_book(D)


def test_the_loader_checks_the_written_history(tmp_path):
    """`load_book` сверяет записанную историю эффективной площади с правилом сети
    у книги как она записана (подмены — нет: ось d законно двигает вывод)."""
    import yaml

    A = _with_closed_history(toy_book())
    A["facts"]["segments"]["conv"]["eff_area_avg_hist"]["2025H2"] += 1.0
    path = tmp_path / "assumptions.yaml"
    path.write_text(yaml.safe_dump(A, allow_unicode=True), encoding="utf-8")
    with pytest.raises(BookError, match="eff_area_avg_hist"):
        load_book(path)


# ===================================================================== ОК


def _weight_by_hand(shares: dict, day: str) -> float:
    import datetime as dt

    d = dt.date.fromisoformat(day)
    h1 = d.month <= 6
    a, b = ("Q1", "Q2") if h1 else ("Q3", "Q4")
    q1 = dt.date(d.year, 1 if h1 else 7, 1)
    q2 = dt.date(d.year, 4 if h1 else 10, 1)
    end = dt.date(d.year, 7, 1) if h1 else dt.date(d.year + 1, 1, 1)
    if d < q2:
        return shares[a] * (d - q1).days / (q2 - q1).days
    return shares[a] + shares[b] * (d - q2).days / (end - q2).days


@pytest.mark.parametrize("day", ["2026-07-01", "2026-09-18", "2026-09-30", "2026-10-01",
                                 "2026-12-31", "2027-02-15", "2027-05-20"])
def test_the_nwc_weight_is_quarterly_then_daily(day):
    """A9: доля изменения ОК полугодия к дате оценки — квартальные веса
    `nwc.quarter_share`, внутри квартала по дням (18.09.2026 при весе 3 кв.
    −0,123 — ≈ −0,105 против 0,429 по дням: ЧД на дату оценки выше линейного)."""
    from model.core import nwc_quarter_weight, time_position

    A = toy_book()
    A["meta"]["valuation_date"] = day
    closed, _ = time_position(A)
    assert nwc_quarter_weight(A, closed) == pytest.approx(
        _weight_by_hand(A["nwc"]["quarter_share"], day), rel=1e-14, abs=1e-15)


def test_day_proportional_weights_reproduce_the_linear_roll_forward():
    """Веса кварталов, равные их долям дней в полугодии, — тот же линейный перекат
    (ЧД на дату оценки и EV); значит, ключ меняет только распределение ΔОК во
    времени и поток полугодия считается ровно один раз: часть — в ЧД, остаток —
    в EV, тем же весом."""
    A = toy_book()
    linear = copy.deepcopy(A)
    del linear["nwc"]["quarter_share"]
    days = copy.deepcopy(A)
    days["nwc"]["quarter_share"] = {"Q1": 90 / 181, "Q2": 91 / 181, "Q3": 0.5, "Q4": 0.5}
    for day in ("2026-09-18", "2026-11-20", "2027-03-03"):
        for B in (linear, days):
            B["meta"]["valuation_date"] = day
        a, b = _run(linear), _run(days)
        assert b.claims_par == pytest.approx(a.claims_par, rel=1e-12), day
        assert b.ev == pytest.approx(a.ev, rel=1e-12), day
    moved = _run(A)
    assert abs(moved.claims_par - _run(linear).claims_par) > 0.1, "проба пуста"


def test_the_quarter_weights_move_value_between_debt_and_ev_only_by_discounting():
    """Перенос ΔОК между ЧД и EV тем же весом: EV − требования с квартальными
    весами и линейно расходятся только на дисконт переносимой части (доли
    процента), а ЧД на дату оценки — на (w − доля дней)·ΔОК."""
    from model.core import nwc_quarter_weight, time_position

    A = toy_book()
    linear = copy.deepcopy(A)
    del linear["nwc"]["quarter_share"]
    a, b = _run(linear), _run(A)
    closed, elapsed = time_position(A)
    w = nwc_quarter_weight(A, closed)
    change = b.rows[closed].nwc_change
    assert b.claims_par - a.claims_par == pytest.approx((w - elapsed) * change, rel=1e-9)
    moved = (w - elapsed) * change
    assert abs((b.ev - b.claims) - (a.ev - a.claims)) < 0.05 * abs(moved)


def test_the_anchor_level_keeps_the_starting_nwc_under_a_june_override():
    """D27 (урок 850oa № 19): с `nwc.anchor_level` ОК якоря — баланс при любой
    подмене июньского излишка, и ось излишка не рождает ложного высвобождения в
    первом полугодии; с `nwc_pct_start` подмена излишка сдвигает старт."""
    from model.engine import with_overrides

    A = toy_book()
    N = A["nwc"]
    ltm = A["facts"]["revenue"]["2025H2"] + A["facts"]["revenue"]["2026H1"]
    level = N["nwc_pct_start"] * ltm + N["seasonal_june_excess"]
    B = copy.deepcopy(A)
    del B["nwc"]["nwc_pct_start"]
    B["nwc"]["anchor_level"] = level
    validate_book(B)
    assert _run(B).ev == pytest.approx(_run(A).ev, rel=1e-12)
    bump = {"nwc.seasonal_june_excess": N["seasonal_june_excess"] + 3.0}
    first_a = _run(with_overrides(A, bump)).rows[0].nwc_change - _run(A).rows[0].nwc_change
    first_b = _run(with_overrides(B, bump)).rows[0].nwc_change - _run(B).rows[0].nwc_change
    assert first_a == pytest.approx(-3.0, rel=1e-12)
    assert first_b == pytest.approx(0.0, abs=1e-12)
    for broken, match in ((copy.deepcopy(A), "ровно одним"), (copy.deepcopy(B), "ровно одним")):
        if "anchor_level" in broken["nwc"]:
            del broken["nwc"]["anchor_level"]
        else:
            broken["nwc"]["anchor_level"] = level
        with pytest.raises(BookError, match=match):
            validate_book(broken)


def test_bad_nwc_quarter_shares_are_refused():
    A = toy_book()
    A["nwc"]["quarter_share"]["Q4"] = 1.0
    with pytest.raises(BookError, match="сумма пары"):
        validate_book(A)


# ==================================================================== налог


@pytest.mark.parametrize("spec", SPECS, ids="|".join)
def test_the_tax_premium_lowers_the_base_by_premium_times_capex_minus_da(spec):
    """D25: налоговая D&A = премия·capex + (1 − премия)·линейная; налог без рычага =
    max(0, τ·(EBIT + прибавка·выручка − премия·(capex − D&A))). Учётная D&A,
    EBITDA и capex не меняются."""
    A = toy_book()
    zero = copy.deepcopy(A)
    del zero["tax"]["capex_tax_premium_share"]
    T = A["tax"]
    a, z = _run(A, spec).rows, _run(zero, spec).rows
    for ra, rz in zip(a, z):
        assert (ra.ebitda, ra.da, ra.capex) == (rz.ebitda, rz.da, rz.capex)
        base = (ra.ebit + path_value(T["permanent_addback_pct"], ra.period) * ra.revenue
                - T["capex_tax_premium_share"] * (ra.capex - ra.da))
        assert ra.tax_unlevered == pytest.approx(max(0.0, T["rate"] * base), rel=1e-12, abs=1e-12)


def test_the_tax_premium_is_worth_more_with_growth_and_nothing_at_zero():
    """Премия 0 — ровно без ключа; стоимость растёт с премией (капитал растущей
    сети: capex > D&A и в явном периоде, и в терминале — стационарная разница)."""
    A = toy_book()
    none = copy.deepcopy(A)
    del none["tax"]["capex_tax_premium_share"]
    zero = copy.deepcopy(A)
    zero["tax"]["capex_tax_premium_share"] = 0.0
    assert _run(zero).ev == _run(none).ev
    values = []
    for share in (0.0, 0.1, 0.2, 0.3):
        B = copy.deepcopy(A)
        B["tax"]["capex_tax_premium_share"] = share
        values.append(_run(B).ev)
    assert values == sorted(values) and values[-1] > values[0]


def test_the_terminal_tax_takes_the_stationary_premium():
    """В терминале налоговая D&A полугодия = премия·capex_h + (1 − премия)·D&A/2:
    поток терминала с премией выше ровно на τ·премия·(capex − D&A) пары полугодий
    (при положительной базе), капитализированный тем же Гордоном."""
    A = toy_book()
    spec = ("H", "partial", "base")
    a = _run(A, spec)
    zero = copy.deepcopy(A)
    zero["tax"]["capex_tax_premium_share"] = 0.0
    z = _run(zero, spec)
    assert a.terminal_flow_value > z.terminal_flow_value
    assert a.terminal_shield_value == z.terminal_shield_value


# ========================================================== оси и строки книги


def test_a_scale_override_multiplies_numbers_and_trajectories_but_not_lt_from():
    """`{"__scale__": f}`: число × f, траектория — каждое число × f, `LT_from` —
    как есть; путь обязан существовать (опечатка — отказ, а не пустая строка)."""
    from model.engine import with_overrides

    A = toy_book()
    B = with_overrides(A, {"capex.segments.conv.growth_capex_per_m2": {"__scale__": 1.1},
                           "revenue.segments.diy.density_path": {"__scale__": 0.5}})
    assert B["capex"]["segments"]["conv"]["growth_capex_per_m2"] == pytest.approx(
        A["capex"]["segments"]["conv"]["growth_capex_per_m2"] * 1.1, rel=1e-15)
    for k, v in A["revenue"]["segments"]["diy"]["density_path"].items():
        got = B["revenue"]["segments"]["diy"]["density_path"][k]
        assert got == (v if k == "LT_from" else pytest.approx(v * 0.5, rel=1e-15)), k
    with pytest.raises(BookError, match="нет"):
        with_overrides(A, {"capex.segments.conv.growth_capex": {"__scale__": 1.1}})


def test_scale_and_choice_axes_take_the_book_at_the_centre_and_the_ends_at_the_edges():
    """E11: ось `scale` — ×(1 + |s|·(конец − 1)), в центре ×1; ось `choice` — конец
    со стороны s, в центре — значение книги; ось-словарь вероятностей режимов —
    поэлементно с нормировкой (схема допускает концы по режимам)."""
    from model.uncertainty import axis_overrides

    A = toy_book()
    scale = {"name": "capex открытия", "kind": "scale", "low": 0.86, "high": 1.14,
             "paths": ["capex.segments.conv.growth_capex_per_m2"]}
    assert axis_overrides(A, scale, 0.0) == {scale["paths"][0]: {"__scale__": 1.0}}
    assert axis_overrides(A, scale, -1.0)[scale["paths"][0]]["__scale__"] == pytest.approx(0.86)
    assert axis_overrides(A, scale, 0.5)[scale["paths"][0]]["__scale__"] == pytest.approx(1.07)
    choice = {"name": "срок присоединения", "kind": "choice", "low": "2031H1", "high": "2027H1",
              "path": "tax.acquired_nol.usable_from"}
    assert axis_overrides(A, choice, 0.0) == {choice["path"]: A["tax"]["acquired_nol"]["usable_from"]}
    assert axis_overrides(A, choice, -0.3) == {choice["path"]: "2031H1"}
    assert axis_overrides(A, choice, 1.0) == {choice["path"]: "2027H1"}
    regimes = {"name": "вероятности режимов", "paths": ["joint.regime_given_world.H"],
               "low": {"stress": 0.4, "floor": 0.3, "partial": 0.2, "full": 0.1},
               "high": {"stress": 0.1, "floor": 0.3, "partial": 0.4, "full": 0.2}}
    mid = axis_overrides(A, regimes, 0.5)["joint.regime_given_world.H"]
    assert sum(mid.values()) == pytest.approx(1.0) and mid["full"] > 0.1
    B = copy.deepcopy(A)
    B["valuation"]["uncertainty"]["axes"] += [scale, choice, regimes]
    B["sensitivities"] += [scale, choice, regimes]
    B["reverse_dcf"].append({"name": "capex открытия", "paths": scale["paths"], "kind": "scale",
                             "search": [0.0, 5.0], "range": [0.86, 1.14]})
    validate_book(B)
    C = copy.deepcopy(B)
    C["reverse_dcf"][-1]["kind"] = "choice"
    with pytest.raises(BookError, match="kind"):
        validate_book(C)


def test_the_reverse_dcf_solves_a_scale_axis():
    """Обратный DCF по оси-множителю: корень — множитель, при котором центр =
    рынку; значение книги у множителя — 1."""
    from model.uncertainty import evaluate, reverse_dcf

    A = toy_book()
    A["reverse_dcf"] = [{"name": "Поддерживающий capex (множитель)",
                         "paths": ["capex.maintenance_pct.base", "capex.maintenance_pct.low",
                                   "capex.maintenance_pct.high"],
                         "kind": "scale", "search": [0.2, 3.0], "range": [0.8, 1.2]}]
    [row] = reverse_dcf(A)
    assert row["book_value"] == 1.0
    if row["value"] is not None:
        from model.engine import with_overrides

        trial = with_overrides(A, {p: {"__scale__": row["value"]} for p in row["paths"]})
        assert evaluate(trial)[3].central == pytest.approx(A["market"]["price"], abs=1.0)


# =============================================== рынок, факты якоря, управление


def test_one_ebitda_ltm_canon_with_the_reported_beside_it():
    """A12: `facts.anchor.ebitda_ltm` — проформа, сумма `facts.ebitda_pre16` двух
    полугодий до якоря; отчётная — `ebitda_ltm_reported`, сумма отчётных."""
    A = toy_book()
    validate_book(A)
    for key, delta in (("ebitda_ltm", 0.5), ("ebitda_ltm_reported", -0.5)):
        B = copy.deepcopy(A)
        B["facts"]["anchor"][key] += delta
        with pytest.raises(BookError, match=key):
            validate_book(B)


def test_the_sellside_summary_is_a_function_of_the_targets():
    """E32: `market.sellside_targets` — список последних целей домов,
    `market.sellside_summary` — агрегаты этого списка (n, медиана, среднее,
    минимум, максимум); расхождение — отказ; ключи — только парой."""
    A = toy_book()
    validate_book(A)
    B = copy.deepcopy(A)
    B["market"]["sellside_summary"]["median"] = 1500.0
    with pytest.raises(BookError, match="median"):
        validate_book(B)
    C = copy.deepcopy(A)
    del C["market"]["sellside_summary"]
    with pytest.raises(BookError, match="парой"):
        validate_book(C)
    D = copy.deepcopy(A)
    D["market"]["sellside_targets"][0]["price"] = 1.0
    with pytest.raises(BookError, match="price"):
        validate_book(D)


def test_the_peers_are_addressed_by_a_unique_key():
    A = toy_book()
    A["market"]["peers"][1]["key"] = A["market"]["peers"][0]["key"]
    with pytest.raises(BookError, match="уникальный"):
        validate_book(A)
    B = toy_book()
    del B["market"]["peers"][0]["key"]
    with pytest.raises(BookError, match="key"):
        validate_book(B)


def test_the_governance_discount_on_the_850oa_scope_leaves_out_flagged_channels():
    """E31: «g на охвате 850oa» — Σ sign·value каналов с `in_850oa_scope` (по
    умолчанию true); ядро число не читает — EV от флага не зависит."""
    from model.book import governance_on_850oa_scope

    A = toy_book()
    parts = A["valuation"]["governance_components"]
    want = sum(p["sign"] * p["value"] for p in parts if p.get("in_850oa_scope", True))
    assert governance_on_850oa_scope(A) == pytest.approx(want, abs=1e-15)
    assert want < A["valuation"]["governance_discount"]
    B = copy.deepcopy(A)
    for p in B["valuation"]["governance_components"]:
        p.pop("in_850oa_scope", None)
    assert governance_on_850oa_scope(B) == pytest.approx(A["valuation"]["governance_discount"])
    assert _run(B).ev == _run(A).ev
    B["valuation"]["governance_components"][0]["in_850oa_scope"] = "нет"
    with pytest.raises(BookError, match="in_850oa_scope"):
        validate_book(B)


# ==================================================================== гейты

CORRIDORS = {"margin_range": (0.045, 0.080), "capex_range": (0.015, 0.055),
             "ev_ebitda": {"N": (3.5, 6.0), "H": (2.5, 4.5), "M": (2.5, 4.5)}}


@pytest.mark.parametrize("spec", SPECS, ids="|".join)
def test_the_guidance_gate_compares_the_year_margin_of_the_cell(spec):
    """C20: маржа года гайденса в клетке = (EBITDA 1П отчётная + EBITDA 2П клетки) /
    (выручка 1П отчётная + выручка 2П клетки); ниже `facts.guidance` — находка
    `guidance_gap` клетки; гейт совещательный."""
    from model.checks import ADVISORY_GATES, check_guidance

    A = toy_book()
    result = _run(A, spec)
    row = next(r for r in result.rows if r.period == "2026H2")
    rep = A["facts"]["reported"]
    margin = (rep["ebitda_pre16"]["2026H1"] + row.ebitda) / (rep["revenue"]["2026H1"] + row.revenue)
    floor = A["facts"]["guidance"]["ebitda_margin_min"]
    found = check_guidance(A, result, label="x")
    assert bool(found) == (margin < floor)
    for bar, fires in ((margin + 0.001, True), (margin - 0.001, False)):
        B = copy.deepcopy(A)
        B["facts"]["guidance"]["ebitda_margin_min"] = bar
        got = check_guidance(B, result, label="x")
        assert bool(got) == fires, bar
        if got:
            assert got[0].key == "guidance_gap" and got[0].observed == pytest.approx(margin, rel=1e-12)
    assert "guidance_gap" in ADVISORY_GATES


def test_the_guidance_gate_is_silent_when_the_year_is_reported_or_absent():
    from model.checks import check_guidance

    A = toy_book()
    result = _run(A)
    B = copy.deepcopy(A)
    B["facts"]["guidance"]["period"] = "2025"
    assert check_guidance(B, result) == []
    del B["facts"]["guidance"]
    assert check_guidance(B, result) == []
    C = copy.deepcopy(A)
    C["facts"]["guidance"]["period"] = 2026
    with pytest.raises(BookError, match="guidance"):
        validate_book(C)


def test_the_gate_corridors_are_data_not_code(tmp_path):
    """E20: коридоры `margin_range`, `capex_range`, `ev_ebitda` — поле `corridor`
    записей файла объяснений; без поля — ошибка сборки, а не молчание."""
    import yaml

    from model.checks import check_gates, gate_corridors

    good = {"margin_range": {"explanation": "x", "valid_until": "2027-06-30", "corridor": [0.045, 0.08]},
            "capex_range": {"explanation": "x", "valid_until": "2027-06-30", "corridor": [0.015, 0.055]},
            "ev_ebitda": {"explanation": "x", "valid_until": "2027-06-30",
                          "corridor": {"N": [3.5, 6.0], "H": [2.5, 4.5], "M": [2.5, 4.5]}}}
    path = tmp_path / "gate_explanations.yaml"
    path.write_text(yaml.safe_dump(good, allow_unicode=True), encoding="utf-8")
    assert gate_corridors(path) == CORRIDORS
    for key, broken in (("margin_range", None), ("ev_ebitda", [2.5, 4.5]), ("capex_range", [0.05, 0.01])):
        bad = copy.deepcopy(good)
        if broken is None:
            del bad[key]["corridor"]
        else:
            bad[key]["corridor"] = broken
        path.write_text(yaml.safe_dump(bad, allow_unicode=True), encoding="utf-8")
        with pytest.raises(ValueError, match=key):
            gate_corridors(path)
    A = toy_book()
    result = _run(A, ("H", "stress", "low"))
    tight = dict(CORRIDORS, margin_range=(0.07, 0.08))
    keys = {f.key for f in check_gates(A, result, corridors=tight, credit_limit=1e9)}
    assert "margin_range" in keys
    loose = dict(CORRIDORS, margin_range=(0.0, 1.0))
    keys = {f.key for f in check_gates(A, result, corridors=loose, credit_limit=1e9)}
    assert "margin_range" not in keys


# ============================================ лимит линий: гейт идёт за триггером


def test_the_credit_lines_gate_and_the_distress_cost_follow_the_book_trigger():
    """`valuation.distress.credit_limit_trigger` (решение ведущего F1) решает
    ОДНО суждение — ограничивает ли номинальный лимит «долг + линии» путь
    долга: при true выход за лимит — и гейт `credit_lines`, и издержки
    неустойчивости; при false — ни то ни другое (путь против лимита —
    справка `checks.credit_lines`). Порог ЧД/EBITDA действует при любом."""
    from model.checks import check_gates, gate_corridors
    from model.financing import credit_limit

    A = toy_book()
    assert A["valuation"]["distress"]["credit_limit_trigger"] is True
    spec = ("H", "full", "base")                      # путь валового долга выше якоря
    result = _run(A, spec)
    below = 0.5 * result.max_gross_debt               # лимит ниже пути клетки
    corridors = gate_corridors()
    on = {f.key for f in check_gates(A, result, corridors=corridors, credit_limit=below)}
    assert "credit_lines" in on
    off_book = copy.deepcopy(A)
    off_book["valuation"]["distress"]["credit_limit_trigger"] = False
    off = {f.key for f in check_gates(off_book, result, corridors=corridors, credit_limit=below)}
    assert "credit_lines" not in off and on - {"credit_lines"} == off

    # Издержки: подменённые линии выводят путь за лимит — при триггере EV
    # теряет долю cost_pct_ev, без триггера — нет (рычаг пути ниже порога).
    tight = copy.deepcopy(A)
    tight["facts"]["undrawn_credit_lines"] = 0.0
    assert result.max_gross_debt > credit_limit(tight)
    assert result.max_leverage < A["valuation"]["distress"]["net_leverage_trigger"]
    hit = _run(tight, spec)
    assert hit.distress_cost == pytest.approx(
        A["valuation"]["distress"]["cost_pct_ev"] * (hit.ev + hit.distress_cost), rel=1e-12)
    tight["valuation"]["distress"]["credit_limit_trigger"] = False
    free = _run(tight, spec)
    assert free.distress_cost == 0.0 and free.ev == pytest.approx(result.ev, rel=1e-12)
