# -*- coding: utf-8 -*-
"""Обобщённое ядро (P2) — аналитические тесты на синтетической книге в схеме Ленты.

Ожидание в каждом тесте выведено из книги и строк клетки своей арифметикой, а
не снято с движка: сумма сегментов, формулы трёх режимов выручки, выплата из
строки моста в пути долга (ровно один счёт), внутренняя стоимость и её V* в
замкнутой форме, корзины ставок, дивидендная лестница, запертые убытки, ОК и
capex приобретённого периметра, соглашение ставки терминала, маржа якоря на
проформе, квартальный слой, закрытая схема новых блоков.

Книга — `tests/fixtures/toy_book` (числа выдуманы): тесты проверяют механику и
идут без книги компании.
"""
from __future__ import annotations

import copy
import datetime as dt
import math

import pytest

from model.book import BookError, Cell, half_rate, path_value, periods, previous_period, validate_book
from model.core import run_cell
from tests.toy import toy_book

# Такт (ops/run.sh, TACT_TESTS): закрытые формы и инварианты ядра на синтетической
# книге — быстрые, защищают число до сборки.
pytestmark = pytest.mark.tact

WORLDS_REGIMES = [("H", "partial", "base"), ("N", "full", "high"), ("M", "stress", "low"),
                  ("H", "floor", "base")]


def _cell(A: dict, spec=("H", "partial", "base")) -> Cell:
    return Cell.build(A, *spec)


def _run(A: dict, spec=("H", "partial", "base")):
    return run_cell(A, _cell(A, spec))


# ================================================================== сегменты


@pytest.mark.parametrize("spec", WORLDS_REGIMES, ids="|".join)
def test_group_rows_are_the_sums_of_the_segments(spec):
    """Выручка группы — сумма сегментов; площадь и эффективная площадь группы —
    суммы сегментов сети; у сегмента без площади (`revenue`) площади нет."""
    A = toy_book()
    rows = _run(A, spec).rows
    modes = {sid: seg["mode"] for sid, seg in A["revenue"]["segments"].items()}
    for row in rows:
        assert list(row.segments) == list(modes), "порядок сегментов — порядок книги"
        assert row.revenue == pytest.approx(sum(s.revenue for s in row.segments.values()),
                                            rel=1e-14)
        network = [s for sid, s in row.segments.items() if modes[sid] != "revenue"]
        assert row.area_end == pytest.approx(sum(s.area_end for s in network), rel=1e-14)
        assert row.effective_area_avg == pytest.approx(
            sum(s.effective_area_avg for s in network), rel=1e-14)
        for sid, step in row.segments.items():
            if modes[sid] == "revenue":
                assert step.area_end is None and step.effective_area_avg is None
                assert step.opened == 0.0 and step.closed == 0.0


def _history(A: dict, sid: str, key: str) -> dict:
    return dict(A["facts"]["segments"][sid].get(key) or {})


@pytest.mark.parametrize("spec", WORLDS_REGIMES, ids="|".join)
def test_a_yoy_segment_grows_year_on_year_by_area_ticket_and_its_traffic(spec):
    """yoy: R(p) = R(p−2)·A_eff(p)/A_eff(p−2)·((1 + чек)(1 + трафик) + lfl_offset(p)).

    Чек и трафик — группы (строка клетки), поправка — прибавка к LFL сегмента
    (так её калибрует лист «Сеть» книги 1.0); у приобретённой сети она своя в
    каждом режиме маржи (`by_regime`)."""
    A = toy_book()
    cell = _cell(A, spec)
    rows = run_cell(A, cell).rows
    for sid, seg in A["revenue"]["segments"].items():
        if seg["mode"] != "yoy":
            continue
        revenue, area = _history(A, sid, "revenue"), _history(A, sid, "eff_area_avg_hist")
        offset = seg.get("lfl_offset")
        if isinstance(offset, dict) and "by_regime" in offset:
            offset = offset["by_regime"][cell.margin_regime]
        for row in rows:
            p = row.period
            back = f"{int(p[:4]) - 1}{p[4:]}"
            step = row.segments[sid]
            shift = path_value(offset, p) if offset is not None else 0.0
            want = (revenue[back] * step.effective_area_avg / area[back]
                    * ((1 + row.lfl_ticket) * (1 + row.lfl_traffic) + shift))
            assert step.revenue == pytest.approx(want, rel=1e-12), (sid, p)
            revenue[p], area[p] = step.revenue, step.effective_area_avg


def test_the_regime_offset_moves_only_its_own_segment():
    """`lfl_offset.by_regime` приобретённой сети: при одном спросе и росте сети
    («дно» и «частичный» — оба `base`) остальные сегменты не меняются ни в
    одном знаке, а приобретённая сеть расходится с первого года сходимости."""
    A = toy_book()
    floor, partial = _run(A, ("H", "floor", "base")), _run(A, ("H", "partial", "base"))
    moved = False
    for a, b in zip(floor.rows, partial.rows):
        for sid in A["revenue"]["segments"]:
            if sid == "acq":
                moved |= abs(a.segments[sid].revenue - b.segments[sid].revenue) > 1e-6
            else:
                assert a.segments[sid].revenue == b.segments[sid].revenue, (sid, a.period)
    assert moved, "поправка режима не сдвинула приобретённую сеть — проба пуста"


@pytest.mark.parametrize("spec", WORLDS_REGIMES, ids="|".join)
def test_a_level_segment_is_area_times_density_times_prices_times_the_half_share(spec):
    """level: R(p) = A_eff(p)·плотность(p)/1000·индекс ИПЦ мира(p)·доля полугодия.

    Плотность — тыс. ₽/м² в год в ценах полугодия якоря; индекс ИПЦ мира на
    якоре — единица, дальше × (1 + ИПЦ(p))^0,5 за полугодие (A-R11 книги 1.0)."""
    A = toy_book()
    cell = _cell(A, spec)
    rows = run_cell(A, cell).rows
    cpi = A["worlds"][cell.world]["cpi"]
    for sid, seg in A["revenue"]["segments"].items():
        if seg["mode"] != "level":
            continue
        index = 1.0
        for row in rows:
            p = row.period
            index *= (1 + path_value(cpi, p)) ** 0.5
            share = seg["h1_share"] if p.endswith("H1") else 1.0 - seg["h1_share"]
            step = row.segments[sid]
            want = (step.effective_area_avg * path_value(seg["density_path"], p) / 1000.0
                    * index * share)
            assert step.revenue == pytest.approx(want, rel=1e-12), (sid, p)


def test_a_level_segment_is_proportional_to_its_density():
    A = toy_book()
    B = copy.deepcopy(A)
    B["revenue"]["segments"]["diy"]["density_path"] = {
        k: (2 * v if k != "LT_from" else v)
        for k, v in A["revenue"]["segments"]["diy"]["density_path"].items()}
    for a, b in zip(_run(A).rows, _run(B).rows):
        assert b.segments["diy"].revenue == pytest.approx(2 * a.segments["diy"].revenue, rel=1e-12)


def test_a_revenue_segment_grows_by_the_group_lfl_plus_its_growth():
    """revenue: R(p) = R(p−2)·((1 + чек)(1 + трафик) + growth(p)) — `growth` —
    прибавка к LFL группы (решение ведущего B14); площади у сегмента нет."""
    A = toy_book()
    revenue = _history(A, "wholesale", "revenue")
    growth = A["revenue"]["segments"]["wholesale"]["growth"]
    for row in _run(A).rows:
        p = row.period
        want = revenue[f"{int(p[:4]) - 1}{p[4:]}"] * (
            (1 + row.lfl_ticket) * (1 + row.lfl_traffic) + path_value(growth, p))
        assert row.segments["wholesale"].revenue == pytest.approx(want, rel=1e-12), p
        revenue[p] = row.segments["wholesale"].revenue


def _split(A: dict, sid: str, parts: tuple[str, str]) -> dict:
    """Сегмент, разрезанный на две одинаковые половины (факты пополам)."""
    B = copy.deepcopy(A)
    spec, facts = B["revenue"]["segments"].pop(sid), B["facts"]["segments"].pop(sid)
    capex = B["capex"]["segments"].pop(sid, None)
    half = {k: ({p: v / 2 for p, v in val.items()} if isinstance(val, dict) and k != "revenue_basis"
                else [v / 2 for v in val] if isinstance(val, list)
                else val / 2 if isinstance(val, (int, float)) else val)
            for k, val in facts.items()}
    for name in parts:
        B["revenue"]["segments"][name] = copy.deepcopy(spec)
        B["facts"]["segments"][name] = copy.deepcopy(half)
        if capex is not None:
            B["capex"]["segments"][name] = copy.deepcopy(capex)
    return B


@pytest.mark.parametrize("sid", ["hyper", "diy", "wholesale"])
def test_segments_add_up_a_split_segment_changes_nothing(sid):
    """Сегмент, разрезанный на две одинаковые половины, даёт ту же группу: выручку,
    площадь, capex, терминальный рост, EV и требования. Сеть — сумма сегментов,
    а не среднее их долей."""
    A = toy_book()
    B = _split(A, sid, (f"{sid}_a", f"{sid}_b"))
    validate_book(B)
    for spec in WORLDS_REGIMES:
        a, b = _run(A, spec), _run(B, spec)
        for ra, rb in zip(a.rows, b.rows):
            assert rb.revenue == pytest.approx(ra.revenue, rel=1e-12), ra.period
            assert rb.capex == pytest.approx(ra.capex, rel=1e-12), ra.period
        assert b.terminal_growth == pytest.approx(a.terminal_growth, rel=1e-12)
        assert b.ev == pytest.approx(a.ev, rel=1e-12)
        assert b.claims == pytest.approx(a.claims, rel=1e-12)


def test_a_lost_segment_is_refused():
    """Мутация «потеря сегмента»: сегмент выпал из правил выручки, а факты остались
    (или наоборот) — отказ книги, а не выручка без сегмента."""
    A = toy_book()
    B = copy.deepcopy(A)
    del B["revenue"]["segments"]["conv"]
    with pytest.raises(BookError, match="не совпадают"):
        validate_book(B)
    C = copy.deepcopy(A)
    del C["facts"]["segments"]["conv"]
    with pytest.raises(BookError, match="не совпадают"):
        validate_book(C)
    D = copy.deepcopy(A)
    D["facts"]["segments"]["conv"]["revenue"]["2026H1"] += 1.0
    with pytest.raises(BookError, match="выручка сегментов"):
        validate_book(D)


@pytest.mark.parametrize("where,key,value,match", [
    ("revenue.segments.hyper", "lfl_offsett", 0.0, "lfl_offsett"),
    ("revenue.segments.hyper", "mode", "stock", "mode"),
    ("revenue.segments.diy", "lfl_offset", {"LT": 0.0}, "не читает"),
    ("revenue.segments.wholesale", "space", {}, "не читает"),
    ("revenue.segments.hyper.space.mid", "grossopen", {"LT": 0.0}, "grossopen"),
    ("facts.segments.hyper", "area", 1.0, "area"),
    ("facts.segments.hyper.revenue_basis", "2026H1", "guess", "revenue_basis"),
    ("capex.segments.hyper", "growth_capex", 0.05, "growth_capex"),
    ("capex.segments", "wholesale", {"growth_capex_per_m2": 0.05}, "wholesale"),
    ("revenue.segments.acq.lfl_offset.by_regime", "stres", {"LT": 0.0}, "stres"),
])
def test_the_closed_schema_refuses_unknown_segment_keys(where, key, value, match):
    A = toy_book()
    node = A
    for part in where.split("."):
        node = node[part]
    node[key] = value
    with pytest.raises(BookError, match=match):
        validate_book(A)


def test_a_yoy_segment_needs_a_positive_area_base():
    A = toy_book()
    A["facts"]["segments"]["acq"]["eff_area_avg_hist"]["2025H2"] = 0.0
    with pytest.raises(BookError, match="положительная"):
        validate_book(A)


# ======================================================= выплата из строки моста


def _ruler(day: dt.date) -> float:
    """Положение дня на полугодовой линейке: номер полугодия + доля по календарю
    (своей арифметикой; день — на своё НАЧАЛО)."""
    first = day.month <= 6
    start = dt.date(day.year, 1, 1) if first else dt.date(day.year, 7, 1)
    end = dt.date(day.year, 6, 30) if first else dt.date(day.year, 12, 31)
    return day.year * 2 + (0 if first else 1) + (day - start).days / ((end - start).days + 1)


def _period_end_next_day(period: str) -> dt.date:
    year, half = int(period[:4]), int(period[5])
    return dt.date(year, 7, 1) if half == 1 else dt.date(year + 1, 1, 1)


def _line(row: dict, day: dt.date) -> float:
    """Строка моста на день `day` со знаком — своей арифметикой."""
    amount = row["amount"]
    if row.get("accrete_rate_half") is not None:
        amount *= (1 + row["accrete_rate_half"]) ** (
            _ruler(day) - _ruler(dt.date.fromisoformat(row["as_of"])))
    if row["kind"] == "asset":
        return -amount * (1 - row.get("haircut", 0.0))
    return amount


def _payments(A: dict) -> dict[str, float]:
    """Выплаты пути долга из строк моста с `settle_period` — своей арифметикой:
    `settle_amount` или сумма, наращенная до конца полугодия расчёта."""
    out: dict[str, float] = {}
    for row in A["bridge"]["items"]:
        p = row.get("settle_period")
        if p is None:
            continue
        if row.get("settle_amount") is not None:
            value = row["settle_amount"]
            value = -value * (1 - row.get("haircut", 0.0)) if row["kind"] == "asset" else value
        else:
            value = _line(row, _period_end_next_day(p))
        out[p] = out.get(p, 0.0) + value
    return out


def _nwc_weight(A: dict, day: dt.date, period: str) -> float:
    """Доля изменения ОК полугодия к дню `day` — своей арифметикой: кварталы по
    календарю, внутри квартала по дням (день — на своё начало)."""
    s = A["nwc"]["quarter_share"]
    year, half = int(period[:4]), int(period[5])
    a, b = ("Q1", "Q2") if half == 1 else ("Q3", "Q4")
    q1 = dt.date(year, 1 if half == 1 else 7, 1)
    q2 = dt.date(year, 4 if half == 1 else 10, 1)
    q_end = dt.date(year, 7, 1) if half == 1 else dt.date(year + 1, 1, 1)
    if day < q2:
        return s[a] * (day - q1).days / (q2 - q1).days
    return s[a] + s[b] * (day - q2).days / (q_end - q2).days


def _exactly_once_problems(A: dict, day: str, spec=("H", "partial", "base")) -> list[str]:
    """Одна запись — один счёт: строка с расчётом сидит в требованиях моста до
    закрытия своего полугодия и в якоре чистого долга — после, но не там и там.

    Проверка — два тождества своей арифметикой: путь долга (выплата — в своём
    полугодии) и требования на дату оценки (якорь − прошедший поток + касса₀ +
    строки, не рассчитанные к закрытым полугодиям)."""
    from model.core import time_position

    trial = copy.deepcopy(A)
    trial["meta"]["valuation_date"] = day
    result = _run(trial, spec)
    closed, elapsed = time_position(trial)
    P = periods(trial["meta"]["first_period"], trial["meta"]["last_period"])
    problems = []
    payments = _payments(trial)
    debt = trial["facts"]["anchor"]["net_debt"]
    for row in result.rows:
        debt = debt - (row.fcff + row.tax_shield - row.net_interest
                       - payments.get(row.period, 0.0)) + row.dividends
        if abs(row.net_debt - debt) > 1e-9 * max(1.0, abs(debt)):
            problems.append(f"{day}: долг {row.period} {row.net_debt:.6f} против "
                            f"{debt:.6f} с выплатой {payments.get(row.period, 0.0):.4f}")
            break
    anchor_debt = (trial["facts"]["anchor"]["net_debt"] if closed == 0
                   else result.rows[closed - 1].net_debt)
    current = result.rows[closed]
    revenue = trial["facts"]["revenue"]
    anchor = previous_period(P[0])
    cash_0 = trial["financing"]["operating_cash_pct"] * (revenue[previous_period(anchor)]
                                                         + revenue[anchor])
    lines = sum(_line(row, dt.date.fromisoformat(day)) for row in trial["bridge"]["items"]
                if row.get("settle_period") is None or P.index(row["settle_period"]) >= closed)
    # Перекат чистого долга: поток без ΔОК — по дням, ΔОК — квартальными весами
    # ОК (`nwc.quarter_share`, решение ведущего A9).
    w = _nwc_weight(trial, dt.date.fromisoformat(day), current.period)
    want = (anchor_debt - (elapsed * (current.fcff + current.nwc_change + current.tax_shield
                                      - current.net_interest) - w * current.nwc_change)
            + cash_0 + lines)
    if abs(result.claims_par - want) > 1e-9 * max(1.0, abs(want)):
        problems.append(f"{day}: требования {result.claims_par:.6f} против {want:.6f}")
    return problems


# Даты по обе стороны от полугодий расчёта строк книги (2П2026 и 1П2027).
SETTLEMENT_DAYS = ["2026-09-18", "2026-12-31", "2027-01-01", "2027-05-15", "2027-06-30",
                   "2027-07-01", "2028-03-01"]


@pytest.mark.parametrize("day", SETTLEMENT_DAYS)
def test_a_settled_claim_is_counted_exactly_once(day):
    A = toy_book()
    settled = [r for r in A["bridge"]["items"] if r.get("settle_period")]
    assert {r["settle_period"] for r in settled} == {"2026H2", "2027H1"}, "книга сменила расчёты"
    assert any(r.get("settle_amount") is None for r in settled), "нет строки с выведенной суммой"
    assert not _exactly_once_problems(A, day)


@pytest.mark.parametrize("mutant", ["выплата потеряна", "выплата дважды",
                                    "строка остаётся после расчёта"])
def test_the_double_and_zero_count_mutants_are_caught(monkeypatch, mutant):
    """Мутанты «ни разу» и «дважды» ловятся тем же тождеством: пропавшая или
    удвоенная выплата ломает путь долга, строка сверх расчёта — требования."""
    from model import core

    if mutant == "выплата потеряна":
        monkeypatch.setattr(core, "settlement_payments", lambda A: {})
    elif mutant == "выплата дважды":
        real = core.settlement_payments
        monkeypatch.setattr(core, "settlement_payments",
                            lambda A: {p: 2 * v for p, v in real(A).items()})
    else:
        real_value = core.bridge_item_value
        monkeypatch.setattr(core, "bridge_item_value",
                            lambda A, item, closed, P: real_value(A, item, 0, P))
    A = toy_book()
    assert any(_exactly_once_problems(A, day) for day in SETTLEMENT_DAYS), mutant


def test_the_settlement_amount_is_the_line_accreted_to_the_period_end():
    """Без `settle_amount` выплата — строка, наращенная по `accrete_rate_half` до
    конца полугодия расчёта: на этой дате требование моста и выплата совпадают."""
    from model.book import bridge_items
    from model.core import settlement_amount, settlement_payments

    A = toy_book()
    item = next(i for i in bridge_items(A) if i.id == "put")
    row = next(r for r in A["bridge"]["items"] if r["id"] == "put")
    assert settlement_amount(A, item) == pytest.approx(
        _line(row, _period_end_next_day(row["settle_period"])), rel=1e-12)
    assert settlement_payments(A) == pytest.approx(_payments(A), rel=1e-12)
    assert "scheduled_payments" not in A["financing"]


# ========================================================= внутренняя стоимость


def _layers(A: dict):
    from model.grid import build_grid, fair_value, layers

    cells = build_grid(A)
    layer_map = layers(A, cells)
    return cells, layer_map, fair_value(A, cells, layer_map)


def test_the_intrinsic_headline_is_max_v0_minus_d_after_governance():
    """Заголовок слоя = max(V0 − D, 0)·(1 − g)·1000/акции — одна функция капитала."""
    A = toy_book()
    _, layer_map, fv = _layers(A)
    g, shares = A["valuation"]["governance_discount"], A["facts"]["shares_out_mln"]
    for name, layer in layer_map.items():
        want = max(layer.v0 - layer.claims, 0.0) * (1 - g) * 1000.0 / shares
        assert layer.headline == pytest.approx(want, rel=1e-14), name
        assert layer.mapping == ("intrinsic", layer.claims)
    lam = A["joint"]["own_macro_confidence"]
    low, high = layer_map["macro_neutral"].headline, layer_map["analytical"].headline
    assert fv.method == "intrinsic"
    assert fv.central == pytest.approx(low + lam * (high - low), rel=1e-14)


def test_the_intrinsic_v_star_has_a_closed_form():
    """V* внутренней стоимости — E/(1 − g) + D (E — капитализация по рынку): решение
    той же численной процедурой, что у 850oa (бисекция `solve_v0`), совпадает с
    замкнутой формой до шага бисекции."""
    from model.grid import solve_v0

    A = toy_book()
    _, layer_map, fv = _layers(A)
    g, shares, price = (A["valuation"]["governance_discount"], A["facts"]["shares_out_mln"],
                        A["market"]["price"])
    for name in ("analytical", "macro_neutral"):
        layer = layer_map[name]
        closed_form = price * shares / 1000.0 / (1 - g) + layer.claims
        assert solve_v0(price, layer.mapping, g, shares) == pytest.approx(closed_form, rel=1e-12)
        assert fv.ev_comparison[name].v_star == pytest.approx(closed_form, rel=1e-12)
    for target in (1.0, 500.0, 5000.0):
        layer = layer_map["analytical"]
        assert solve_v0(target, layer.mapping, g, shares) == pytest.approx(
            target * shares / 1000.0 / (1 - g) + layer.claims, rel=1e-12)


def test_the_intrinsic_headline_floors_at_zero_equity():
    """Ограниченная ответственность в отображении — только пол: V0 < D — ноль, а не
    отрицательная цена; вспомогательная оценка клетки — тем же отображением."""
    from model.mapping import layer_mapping, mapped_equity

    A = toy_book()
    mapping = layer_mapping(A, 100.0)
    assert mapped_equity(mapping, 80.0) == 0.0
    assert mapped_equity(mapping, 130.0) == pytest.approx(30.0, rel=1e-15)
    B = copy.deepcopy(A)
    B["facts"]["anchor"]["net_debt"] = 5000.0
    result = _run(B)
    assert result.equity_before_governance < 0
    assert result.price_floor == 0.0


def test_intrinsic_needs_no_structural_keys_and_refuses_them():
    """У `intrinsic` нет σ, страйка, калибровки и затухания — ключ, который никто не
    читает, выглядел бы действующим суждением: отказ."""
    A = toy_book()
    assert not {"strike_premium", "sigma_ev", "credit_share_c", "strike_decay",
                "calibration_price", "recalibration_flag"} & set(A["valuation"]["headline"])
    for key, value in (("sigma_ev", 0.1), ("strike_premium", 0.02), ("strike_decay", {})):
        B = copy.deepcopy(A)
        B["valuation"]["headline"][key] = value
        with pytest.raises(BookError, match=key):
            validate_book(B)


def test_the_limited_liability_gate_fires_on_the_share_of_draws():
    """Гейт `limited_liability`: доля прогонов полосы с V0 слоя ниже v0_to_d_min·D
    выше `max_share` — совещательная находка уровня выпуска."""
    from model.checks import ADVISORY_GATES, check_limited_liability
    from model.uncertainty import limited_liability_share

    A = toy_book()
    rows = [dict(v0_own=300.0, v0_market=290.0, d=100.0, d_market=100.0),
            dict(v0_own=110.0, v0_market=150.0, d=100.0, d_market=100.0),
            dict(v0_own=200.0, v0_market=105.0, d=100.0, d_market=100.0),
            dict(v0_own=500.0, v0_market=400.0, d=100.0, d_market=100.0)]
    info = limited_liability_share(A, rows)
    assert (info["hits"], info["draws"], info["share"]) == (2, 4, 0.5)
    assert info["fired"] and "limited_liability" in ADVISORY_GATES
    finding = check_limited_liability(A, {"limited_liability": info})
    assert [f.key for f in finding] == ["limited_liability"] and not finding[0].blocking
    quiet = dict(info, fired=False)
    assert check_limited_liability(A, {"limited_liability": quiet}) == []
    B = copy.deepcopy(A)
    del B["valuation"]["headline"]["limited_liability"]
    assert limited_liability_share(B, rows) is None


def test_the_band_carries_the_limited_liability_share():
    """Полоса считает долю по своим прогонам; при высоком пороге книги гейт горит."""
    from model.checks import check_limited_liability
    from model.uncertainty import uncertainty

    A = toy_book()
    A["valuation"]["headline"]["limited_liability"] = {"v0_to_d_min": 50.0, "max_share": 0.5}
    band = uncertainty(A, draws=6)
    assert band["limited_liability"]["share"] == 1.0
    assert band["layer_draws"][0].keys() == {"own", "market", "governance"}
    assert check_limited_liability(A, band)


def test_the_reverse_dcf_prices_the_market_with_intrinsic():
    """Обратный DCF точки при `intrinsic`: найденное значение суждения даёт
    рыночную цену тем же расчётом центра."""
    from model.uncertainty import evaluate, reverse_dcf

    A = toy_book()
    A["reverse_dcf"] = [A["reverse_dcf"][1]]           # β_u
    row = reverse_dcf(A)[0]
    assert row["value"] is not None
    *_, fv = evaluate(A, {"valuation.beta_u": row["value"]})
    assert fv.central == pytest.approx(A["market"]["price"], rel=1e-6)


def test_the_jump_guard_thresholds_are_the_books():
    from model.book import jump_guard

    A = toy_book()
    assert jump_guard(A) == {"median_pct": 0.09, "v0_pct": 0.10}
    del A["valuation"]["headline"]["jump_guard"]
    with pytest.raises(BookError, match="jump_guard"):
        validate_book(A)


# ============================================================ корзины ставок


def test_the_debt_rate_is_the_basket_mix_until_each_basket_ends():
    """Ставка долга = Σ доля·(своя ставка живой корзины: договорная или ключевая +
    спред) + доля вне живых корзин × ставка нового долга (решение ведущего A10)."""
    from model.book import rate_baskets
    from model.core import basket_debt_rate

    A = toy_book()
    baskets, new_debt, key = rate_baskets(A), 0.2, 0.15
    raw = A["financing"]["rate_baskets"]
    for p in periods("2026H2", "2030H2"):
        want, live = 0.0, 0.0
        for b in raw:
            ends = int(b["until"][:4]) * 2 + int(b["until"][5])
            if int(p[:4]) * 2 + int(p[5]) <= ends:
                live += b["share"]
                want += b["share"] * (b["rate"]["fixed"] if "fixed" in b["rate"]
                                      else key + b["rate"]["key_plus"])
        want += (1 - live) * new_debt
        assert basket_debt_rate(baskets, p, key, new_debt) == pytest.approx(want, rel=1e-14), p
    assert basket_debt_rate((), "2027H1", key, new_debt) == new_debt


@pytest.mark.parametrize("spec", WORLDS_REGIMES, ids="|".join)
def test_the_rows_pay_the_baskets_and_the_new_debt_mix(spec):
    """Ставка строки: корзины + новый долг вне живых корзин — `fixed_share` по
    кривой мира на 3 года + спред, остальное — ключевая + спред клетки."""
    from model.book import interp_curve

    A = toy_book()
    cell = Cell.build(A, *spec)
    W, F = A["worlds"][cell.world], A["financing"]
    for row in run_cell(A, cell).rows:
        p = row.period
        key = path_value(W["key_rate"], p)
        fs = path_value(F["fixed_share"], p)
        new = (fs * (interp_curve(W["zero_curve"], 3.0) + F["spread_fixed"][cell.credit])
               + (1 - fs) * (key + F["spread_float"][cell.credit]))
        live = [b for b in F["rate_baskets"]
                if int(p[:4]) * 2 + int(p[5]) <= int(b["until"][:4]) * 2 + int(b["until"][5])]
        want = (1 - sum(b["share"] for b in live)) * new + sum(
            b["share"] * (b["rate"]["fixed"] if "fixed" in b["rate"] else key + b["rate"]["key_plus"])
            for b in live)
        assert row.debt_rate == pytest.approx(want, rel=1e-14), p


def test_a_basket_is_fair_at_its_own_rate_and_a_split_basket_changes_nothing():
    """Справедливая ставка корзины — её собственная (A10): при справедливых спредах
    нового долга, равных базовым, избытка процентов нет, какой бы ни была ставка
    корзины (премия старых купонов — строка моста). Корзина, разрезанная на две
    одинаковые, не меняет ни одной строки."""
    A = toy_book()
    B = copy.deepcopy(A)
    B["financing"]["rate_baskets"][0]["rate"] = {"fixed": 0.25}
    B["financing"]["rate_baskets"][2]["rate"] = {"key_plus": 0.06}
    for book_ in (A, B):
        assert _run(book_).debt_cost_addon == 0.0
    C = copy.deepcopy(A)
    first = C["financing"]["rate_baskets"].pop(0)
    C["financing"]["rate_baskets"][:0] = [dict(first, name="a", share=first["share"] / 2),
                                          dict(first, name="b", share=first["share"] / 2)]
    a, c = _run(A), _run(C)
    assert [r.debt_rate for r in c.rows] == pytest.approx([r.debt_rate for r in a.rows], rel=1e-14)
    assert c.ev == pytest.approx(a.ev, rel=1e-13)


# ======================================================== лестница дивидендов


def _ladder_dividend(ladder, leverage, fcfe, room):
    """Правило лестницы своей арифметикой (докстрока `model.book.dividend_ladder`)."""
    for number, rung in enumerate(ladder):
        if rung["max_leverage"] is None or leverage < rung["max_leverage"]:
            positive = max(fcfe, 0.0)
            cap = math.inf if rung["payout_max"] is None else rung["payout_max"] * positive
            return min(max(room, positive), cap), number
    return 0.0, None


def test_the_ladder_rule_by_rung():
    """λ < 1,0 — вся FCFE и больше, до целевого рычага; 1,0–1,5 — не больше FCFE;
    выше — не больше половины FCFE. Без лестницы — правило 850oa (запас до цели)."""
    from model.book import dividend_ladder
    from model.core import dividend_rule

    A = toy_book()
    ladder = dividend_ladder(A)
    year = A["financing"]["dividends_from_year"]
    ebitda = 100.0
    # (ЧД до дивидендов, ЧД прошлого полугодия) → λ, FCFE, запас до цели L·EBITDA = 100
    for pre, prev, want, rung in [(60.0, 80.0, 40.0, 0),      # λ 0,6: запас 40 > FCFE 20
                                  (90.0, 150.0, 60.0, 0),     # λ 0,9: FCFE 60 > запас 10
                                  (120.0, 140.0, 20.0, 1),    # λ 1,2: FCFE 20, запаса нет
                                  (120.0, 110.0, 0.0, 1),     # λ 1,2: FCFE < 0
                                  (180.0, 200.0, 10.0, 2)]:   # λ 1,8: половина FCFE
        got = dividend_rule(A, year, prev, pre, ebitda, ladder)
        assert got == (pytest.approx(want), rung), (pre, prev)
    assert dividend_rule(A, year - 1, 80.0, 60.0, ebitda, ladder) == (0.0, None)
    assert dividend_rule(A, year, 80.0, 60.0, ebitda, None) == (pytest.approx(40.0), None)
    assert dividend_rule(A, year, 80.0, 120.0, ebitda, None) == (0.0, None)


@pytest.mark.parametrize("spec", WORLDS_REGIMES, ids="|".join)
def test_the_rows_pay_the_ladder(spec):
    """Дивиденды каждой строки — правило лестницы на её же величинах: FCFE = ЧД(p−1)
    − ЧД до дивидендов, λ = ЧД до дивидендов / EBITDA LTM, запас до L·EBITDA LTM."""
    A = toy_book()
    ladder = A["financing"]["dividend_ladder"]
    L, start = A["financing"]["leverage_target"], A["financing"]["dividends_from_year"]
    rows = _run(A, spec).rows
    prev_debt, prev_ebitda = A["facts"]["anchor"]["net_debt"], A["facts"]["ebitda_pre16"]["2026H1"]
    for row in rows:
        pre = row.net_debt - row.dividends
        ltm = row.ebitda + prev_ebitda
        if row.year < start:
            assert row.dividends == 0.0 and row.dividend_rung is None
        else:
            want, rung = _ladder_dividend(ladder, pre / ltm, prev_debt - pre, max(L * ltm - pre, 0.0))
            assert row.dividends == pytest.approx(want, rel=1e-12, abs=1e-12), row.period
            assert row.dividend_rung == rung, row.period
        assert row.fcfe == pytest.approx(prev_debt - pre, rel=1e-12, abs=1e-12)
        prev_debt, prev_ebitda = row.net_debt, row.ebitda


@pytest.mark.parametrize("spec", WORLDS_REGIMES, ids="|".join)
def test_the_reported_basis_measures_the_ladder_on_reported_net_debt(spec):
    """`financing.dividend_net_debt_basis: reported` (находка I1d): λ и запас —
    по отчётному ЧД = модельный − прирост операционной кассы с якоря
    (`operating_cash_growth` строки); FCFE правила тот же. Явное `model` — бит в
    бит как без ключа."""
    A = toy_book()
    B = copy.deepcopy(A)
    B["financing"]["dividend_net_debt_basis"] = "model"
    assert [r.net_debt for r in _run(B, spec).rows] == [r.net_debt for r in _run(A, spec).rows]
    A["financing"]["dividend_net_debt_basis"] = "reported"
    ladder = A["financing"]["dividend_ladder"]
    L, start = A["financing"]["leverage_target"], A["financing"]["dividends_from_year"]
    rows = _run(A, spec).rows
    prev_debt, prev_ebitda = A["facts"]["anchor"]["net_debt"], A["facts"]["ebitda_pre16"]["2026H1"]
    paid = 0
    for row in rows:
        pre = row.net_debt - row.dividends
        ltm = row.ebitda + prev_ebitda
        g = row.operating_cash_growth
        if row.year >= start:
            want, rung = _ladder_dividend(ladder, (pre - g) / ltm, prev_debt - pre,
                                          max(L * ltm - (pre - g), 0.0))
            assert row.dividends == pytest.approx(want, rel=1e-12, abs=1e-12), row.period
            assert row.dividend_rung == rung, row.period
            paid += row.dividends > 0
        prev_debt, prev_ebitda = row.net_debt, row.ebitda
    assert paid, "проба пуста: ни одной выплаты"
    assert any(r.operating_cash_growth for r in rows), "проба пуста: касса не растёт"


def _annual_book(basis: str = "reported") -> dict:
    A = toy_book()
    A["financing"]["dividend_timing"] = "annual_next_h1"
    A["financing"]["dividend_net_debt_basis"] = basis
    return A


@pytest.mark.parametrize("basis", ["model", "reported"])
@pytest.mark.parametrize("spec", WORLDS_REGIMES, ids="|".join)
def test_the_annual_ladder_pays_the_fiscal_year_in_the_next_first_half(spec, basis):
    """`financing.dividend_timing: annual_next_h1` своей арифметикой: по итогам
    года Y — FCFE_Y = FCFE 1П + FCFE 2П, ступень и запас по ЧД 31.12 (отчётному
    при `reported`) и EBITDA LTM 2П, D_Y = верх ступени; платится в 1П года
    Y + 1 ≥ `dividends_from_year`, во 2П — ноль; ступень — у строки выплаты."""
    A = _annual_book(basis)
    ladder = A["financing"]["dividend_ladder"]
    L, start = A["financing"]["leverage_target"], A["financing"]["dividends_from_year"]
    rows = _run(A, spec).rows
    prev_debt, prev_ebitda = A["facts"]["anchor"]["net_debt"], A["facts"]["ebitda_pre16"]["2026H1"]
    owed, year_fcfe, paid = (0.0, None), 0.0, 0
    for row in rows:
        pre = row.net_debt - row.dividends
        assert row.fcfe == pytest.approx(prev_debt - pre, rel=1e-12, abs=1e-12)
        ltm = row.ebitda + prev_ebitda
        if row.half == 1:
            assert (row.dividends, row.dividend_rung) == (pytest.approx(owed[0], rel=1e-12, abs=1e-12),
                                                          owed[1]), row.period
            paid += row.dividends > 0
            owed, year_fcfe = (0.0, None), row.fcfe
        else:
            assert row.dividends == 0.0 and row.dividend_rung is None, row.period
            year_fcfe += row.fcfe
            if row.year + 1 >= start:
                debt = row.net_debt - (row.operating_cash_growth if basis == "reported" else 0.0)
                owed = _ladder_dividend(ladder, debt / ltm, year_fcfe, max(L * ltm - debt, 0.0))
        prev_debt, prev_ebitda = row.net_debt, row.ebitda
    assert paid, "проба пуста: ни одной годовой выплаты"
    assert all(r.dividends == 0.0 for r in rows if r.year < start)


def test_the_annual_ladder_nets_the_seasonal_working_capital_within_the_year():
    """Суть годового правила: отток ОК 1П и приток 2П одного года гасятся
    внутри года. Правило полугодия платит положительный FCFE 2П целиком и
    оставляет отрицательный FCFE 1П в долге; годовое — только чистый FCFE года.
    Проба на одной ступени (1,0–1,5×, потолок 100 % FCF, запаса нет)."""
    from model.book import dividend_ladder
    from model.core import dividend_rule, ladder_payout

    A = toy_book()
    ladder, FN = dividend_ladder(A), A["financing"]
    year = FN["dividends_from_year"]
    ebitda, debt = 100.0, 120.0                       # λ 1,2: ступень «≤ 100 % FCF»
    h1 = dividend_rule(A, year, debt, debt + 30.0, ebitda, ladder)         # FCFE 1П −30
    h2 = dividend_rule(A, year, debt + 30.0, debt - 10.0, ebitda, ladder)  # FCFE 2П +40
    assert (h1[0], h2[0]) == (0.0, pytest.approx(40.0))                     # полугодие: 40 из 10
    annual = ladder_payout(FN, -30.0 + 40.0, debt - 10.0, ebitda, ladder)
    assert annual == (pytest.approx(10.0), 1)                              # год: чистые 10


def test_the_ladder_is_exercised_on_several_rungs():
    """Сторож невакуумности: в клетках сетки встречаются хотя бы две ступени."""
    from model.book import all_cells

    A = toy_book()
    seen = {row.dividend_rung for c in all_cells(A) for row in run_cell(A, c).rows
            if row.dividend_rung is not None}
    assert len(seen) >= 2, seen


# ============================================= запертые убытки, ОК, capex покупок


@pytest.mark.parametrize("spec", WORLDS_REGIMES, ids="|".join)
def test_the_acquired_losses_are_frozen_until_they_are_usable(spec):
    """Пул `tax.acquired_nol` (правило `frozen_until_usable_from`, решение D26):
    до `usable_from` убыток запертых юрлиц `locked_addback` прибавляется к базе
    пула группы B = α·база − проценты и копится в их пуле; в `usable_from` пул
    группы += (1 − haircut)·их пул; база = EBIT + прибавка·выручка − премия·(capex
    − D&A). Пул группы зачитывается с пределом `nol_limit`."""
    A = toy_book()
    T, rows = A["tax"], _run(A, spec).rows
    locked = T["acquired_nol"]
    rule = locked["discount_rule"]
    pool, nol = locked["amount"], T["nol_start"]
    usable = int(locked["usable_from"][:4]) * 2 + int(locked["usable_from"][5])
    premium = T["capex_tax_premium_share"]
    merged_at = None
    for row in rows:
        index = int(row.period[:4]) * 2 + int(row.period[5])
        base = (row.ebit + path_value(T["permanent_addback_pct"], row.period) * row.revenue
                - premium * (row.capex - row.da))
        B = T["alpha"] * base - row.net_interest
        if index < usable:
            add = rule["locked_addback"].get(row.period, 0.0)
            B, pool = B + add, pool + add
        elif merged_at is None:
            nol, pool, merged_at = nol + (1 - rule["haircut"]) * pool, 0.0, row.period
        limit = T["nol_limit"] if row.year < T["nol_full_from_year"] else 1.0
        nol = nol - B if B < 0 else nol - min(nol, limit * B)
        assert row.tax_loss_pool == pytest.approx(nol, rel=1e-12, abs=1e-12), row.period
        assert row.tax_loss_pool_acquired == pytest.approx(pool, rel=1e-12, abs=1e-12), row.period
    assert merged_at == locked["usable_from"], "пул не перешёл в пул группы — проба пуста"


def test_the_acquired_losses_are_worth_nothing_when_never_usable():
    """haircut 1 без прибавок — ровно как без пула; прибавки без присоединения
    (usable_from вне горизонта) — налог выше, чем без пула: убытки запертых
    юрлиц консолидированную базу не уменьшают; haircut 0 стоит больше haircut 1."""
    A = toy_book()
    base = copy.deepcopy(A)
    del base["tax"]["acquired_nol"]
    dead = copy.deepcopy(A)
    dead["tax"]["acquired_nol"]["discount_rule"].update(haircut=1.0, locked_addback={})
    assert _run(dead).ev == _run(base).ev
    never = copy.deepcopy(A)
    never["tax"]["acquired_nol"]["usable_from"] = "2040H1"
    assert _run(never).ev < _run(base).ev
    full = copy.deepcopy(A)
    full["tax"]["acquired_nol"]["discount_rule"]["haircut"] = 0.0
    one = copy.deepcopy(A)
    one["tax"]["acquired_nol"]["discount_rule"]["haircut"] = 1.0
    assert _run(full).ev > _run(A).ev > _run(one).ev


def test_the_acquired_nwc_path_adds_its_level_and_its_changes():
    """`nwc.acquired_path` — доля годовой выручки поверх `nwc_pct` (A-W3 книги 1.0):
    уровень пути × годовая выручка — в ОК группы, его изменение — в потоке; на
    якоре пути нет (старт — баланс, в нём этот ОК уже сидит)."""
    A = toy_book()
    base = copy.deepcopy(A)
    del base["nwc"]["acquired_path"]
    path = A["nwc"]["acquired_path"]
    previous, revenue_prev = 0.0, A["facts"]["revenue"]["2026H1"]
    for a, b in zip(_run(A).rows, _run(base).rows):
        assert a.revenue == b.revenue
        level = path_value(path, a.period) * (a.revenue + revenue_prev)
        assert a.nwc_change - b.nwc_change == pytest.approx(level - previous, abs=1e-9), a.period
        previous, revenue_prev = level, a.revenue


def test_the_integration_capex_is_its_own_line_in_the_explicit_period():
    A = toy_book()
    base = copy.deepcopy(A)
    del base["capex"]["integration_capex"]
    path = A["capex"]["integration_capex"]
    for a, b in zip(_run(A).rows, _run(base).rows):
        assert a.capex_integration == pytest.approx(path_value(path, a.period), rel=1e-15)
        assert b.capex_integration == 0.0
        assert a.capex - b.capex == pytest.approx(a.capex_integration, abs=1e-12), a.period


# ============================================================ терминал, якорь


def test_the_terminal_half_rate_convention():
    """`simple` — r/2, `compound` — (1 + r)^0,5 − 1: щит терминала меняется ровно в
    отношении полугодовых ставок, поток терминала — нет."""
    A = toy_book()
    simple = copy.deepcopy(A)
    simple["valuation"]["terminal"]["half_rate_convention"] = "simple"
    spec = ("H", "partial", "base")
    c, s = _run(A, spec), _run(simple, spec)
    credit = A["joint"]["by_world"]["H"]["credit"]
    r = A["worlds"]["H"]["zero_curve"]["LT"] + A["financing"]["spread_fixed"][credit]
    assert c.terminal_flow_value == s.terminal_flow_value
    assert c.terminal_shield_value / s.terminal_shield_value == pytest.approx(
        half_rate(r) / (r / 2.0), rel=1e-12)


def test_the_pro_forma_anchor_margin_is_an_observation_with_its_error():
    """Маржа якоря на проформе (`facts.anchor.margin_pro_forma`, se > 0) — наблюдение
    полугодия якоря: отклонение на якоре — шаг Калмана от N(0, σ²) с весом
    σ²/(σ² + se²); дальше затухает ρ, как у факта. Правдоподобие режимов берёт то
    же наблюдение первым."""
    from model.core import margin_observations, margin_season
    from model.grid import regime_likelihoods

    A = toy_book()
    anchor = A["facts"]["anchor"]
    m, se = anchor["margin_pro_forma"], anchor["margin_pro_forma_se"]
    assert margin_observations(A) == {anchor["period"]: (m, se)}
    rho = A["margin"]["deviation_persistence"]
    sigma2 = A["joint"]["regime_update"]["sigma_pp"] ** 2
    weight = sigma2 / (sigma2 + se * se)
    for regime, spec in A["margin"]["regimes"].items():
        target = spec["target"]
        dev = m - path_value(target, anchor["period"]) - margin_season(A, anchor["period"])
        rows = run_cell(A, Cell.build(A, "M", regime, "base")).rows
        for k, row in enumerate(rows[:4], start=1):
            want = (path_value(target, row.period) + margin_season(A, row.period)
                    + rho ** k * weight * dev)
            assert row.margin == pytest.approx(want, abs=1e-12), (regime, row.period)
    [first] = regime_likelihoods(A)
    for regime, spec in A["margin"]["regimes"].items():
        dev = m - path_value(spec["target"], anchor["period"]) - margin_season(A, anchor["period"])
        assert first[regime] == pytest.approx(math.exp(-0.5 * dev * dev / (sigma2 + se * se)),
                                              rel=1e-12)


def test_the_pro_forma_needs_its_error_and_is_not_written_twice():
    A = toy_book()
    B = copy.deepcopy(A)
    del B["facts"]["anchor"]["margin_pro_forma_se"]
    with pytest.raises(BookError, match="парой"):
        validate_book(B)
    C = copy.deepcopy(A)
    C["joint"]["regime_update"]["observations"] = {"2026H1": 0.06}
    with pytest.raises(BookError, match="дважды"):
        validate_book(C)


def test_the_season_free_window_is_the_books():
    """`margin.season_free_halves` полугодий перед `season_from_period` — без
    сезонной поправки; раньше и позже — ± `seasonal_h1_pp`."""
    from model.core import margin_season

    A = toy_book()
    s = A["margin"]["seasonal_h1_pp"]
    assert [margin_season(A, p) for p in ("2025H2", "2026H1", "2026H2", "2027H1", "2027H2")] == [
        -s, 0.0, 0.0, s, -s]
    A["margin"]["season_free_halves"] = 1
    assert [margin_season(A, p) for p in ("2026H1", "2026H2")] == [s, 0.0]


# ===================================================== управление, рынок, схема


def test_the_governance_components_sum_to_the_discount():
    from model.book import check_governance_sum, governance_components

    A = toy_book()
    parts = governance_components(A)
    assert abs(sum(p["sign"] * p["value"] for p in parts)
               - A["valuation"]["governance_discount"]) <= 1e-9
    B = copy.deepcopy(A)
    B["valuation"]["governance_components"][0]["value"] += 0.01
    with pytest.raises(BookError, match="governance"):
        check_governance_sum(B)
    C = copy.deepcopy(A)
    C["valuation"]["governance_components"][0]["sign"] = 2
    with pytest.raises(BookError, match="sign"):
        validate_book(C)


def test_the_load_refuses_components_that_do_not_sum(tmp_path):
    import yaml

    from model.book import load_book
    from tests.toy import TOY_BOOK

    A = yaml.safe_load(TOY_BOOK.read_text(encoding="utf-8"))
    A["valuation"]["governance_discount"] = 0.2
    path = tmp_path / "assumptions.yaml"
    path.write_text(yaml.safe_dump(A, allow_unicode=True), encoding="utf-8")
    with pytest.raises(BookError, match="governance_components"):
        load_book(path)


def test_the_price_convention_is_informational():
    A = toy_book()
    base = _run(A).price
    A["market"]["price_convention"], A["market"]["price_date"] = "vwap", "2026-09-17"
    validate_book(A)
    assert _run(A).price == base


@pytest.mark.parametrize("path,value,match", [
    ("meta.period_unit", "quarter", "period_unit"),
    ("meta.company", {"name": "x"}, "company"),
    ("nwc.acquired_path", {"2026H2": 1.0}, "acquired_path"),
    ("capex.integration_capex", {"2026H2": 1.0}, "integration_capex"),
    ("capex.integration_capex", {"LT": "1.0"}, "integration_capex"),
    ("tax.acquired_nol", {"amount": 1.0, "usable_from": "2027"}, "acquired_nol"),
    ("tax.acquired_nol", {"amount": 1.0, "usable_from": "2027H2",
                          "discount_rule": {"profit_share": 0.1}}, "profit_share"),
    ("tax.acquired_nol", {"amount": 1.0, "usable_from": "2027H2",
                          "discount_rule": {"method": "frozen", "haircut": 0.2,
                                            "locked_addback": {}}}, "method"),
    ("tax.acquired_nol", {"amount": 1.0, "usable_from": "2027H2",
                          "discount_rule": {"method": "frozen_until_usable_from", "haircut": 1.2,
                                            "locked_addback": {}}}, "haircut"),
    ("tax.acquired_nol", {"amount": 1.0, "usable_from": "2027H2",
                          "discount_rule": {"method": "frozen_until_usable_from", "haircut": 0.2,
                                            "locked_addback": {"2025H2": 1.0}}}, "locked_addback"),
    ("tax.capex_tax_premium_share", 1.5, "capex_tax_premium_share"),
    ("tax.acquired_nol", {"amount": 1.0, "usable_from": "2027",
                          "discount_rule": {"method": "frozen_until_usable_from", "haircut": 0.1,
                                            "locked_addback": {}}}, "usable_from"),
    ("valuation.headline.limited_liability", {"v0_to_d_min": 0.0, "max_share": 0.1}, "v0_to_d_min"),
    ("market.peers", [{"key": "a", "name": "A", "ev_ebitda": 4.0, "ticker": "A"}], "ticker"),
    ("margin.quarter_sigma_pp", -0.01, "quarter_sigma_pp"),
])
def test_bad_new_keys_are_refused(path, value, match):
    from model.engine import with_overrides

    A = with_overrides(toy_book(), {path: value}, create=True)
    with pytest.raises(BookError, match=match):
        validate_book(A)


# ============================================================ квартальный слой


def test_the_quarters_split_the_half_exactly():
    """Выручка квартала = выручка полугодия × доля квартала в полугодии; маржа =
    маржа полугодия + поправка; два квартала дают ровно выручку и EBITDA
    полугодия (поправки внутри полугодия обнуляются с весами выручки)."""
    from model.quarters import expected_quarter

    A = toy_book()
    result = _run(A)
    share, offset = A["revenue"]["quarter_share"], A["margin"]["quarter_offset_pp"]
    for row in result.rows[:6]:
        year, half = row.period[:4], int(row.period[5])
        pair = ("Q1", "Q2") if half == 1 else ("Q3", "Q4")
        got = [expected_quarter(result, A, f"{year}{q}") for q in pair]
        for q, g in zip(pair, got):
            assert g["period"] == row.period
            assert g["revenue"] == pytest.approx(
                row.revenue * share[q] / (share[pair[0]] + share[pair[1]]), rel=1e-14)
            assert g["margin"] == pytest.approx(row.margin + offset[q], rel=1e-14)
            assert g["ebitda"] == pytest.approx(g["revenue"] * g["margin"], rel=1e-14)
        assert sum(g["revenue"] for g in got) == pytest.approx(row.revenue, rel=1e-13)
        assert sum(g["ebitda"] for g in got) == pytest.approx(row.ebitda, rel=1e-12)


def test_the_grid_expectation_is_probability_weighted():
    """Ожидание сетки — Σ p·(величина клетки); маржа — Σ p·m_Q, как ожидание
    модели 850oa (средняя маржа ядра по сетке)."""
    from model.grid import build_grid
    from model.quarters import expected_quarter

    A = toy_book()
    cells = build_grid(A)
    total = sum(c.probability for c in cells)
    got = expected_quarter(cells, A, "2026Q3")
    per_cell = [(c.probability / total, expected_quarter(c.result, A, "2026Q3")) for c in cells]
    for key in ("revenue", "margin", "ebitda"):
        assert got[key] == pytest.approx(sum(p * q[key] for p, q in per_cell), rel=1e-13)
    assert got["quarter"] == "2026Q3" and got["period"] == "2026H2"


def test_the_quarter_layer_refuses_what_it_cannot_answer():
    from model.quarters import expected_quarter

    A = toy_book()
    result = _run(A)
    with pytest.raises(ValueError, match="ГГГГQn"):
        expected_quarter(result, A, "2026-Q3")
    with pytest.raises(KeyError):
        expected_quarter(result, A, "2040Q1")
    B = copy.deepcopy(A)
    B["margin"]["quarter_offset_pp"]["Q2"] = 0.01
    with pytest.raises(BookError, match="весами выручки"):
        validate_book(B)
    C = copy.deepcopy(A)
    del C["margin"]["quarter_offset_pp"]
    with pytest.raises(BookError, match="парой"):
        validate_book(C)
    D = copy.deepcopy(A)
    D["revenue"]["quarter_share"]["Q4"] = 0.5
    with pytest.raises(BookError, match="сумм"):
        validate_book(D)
