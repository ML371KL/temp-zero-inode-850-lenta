# -*- coding: utf-8 -*-
"""Числа, которые видит владелец на экране (перенос 850oa).

Аудит 850oa: «ошибки показателей на экране (ЧД/EBITDA ×2, покрытие ×2,
перцентиль рынка) не ловит ни один тест». Здесь каждое число витрины сверяется
с тем, из чего оно складывается, а текст витрины — с тем, что она печатает число
выпуска и своего не досчитывает. Данные — быстрая сборка на книге; проверки
текста витрины и полного выпуска — `ci_only`.
"""
from __future__ import annotations

import re

import pytest

from model.payload import build_payload
from tests.web_source import APP_CODE as _APP_CODE, function_body as _function

pytestmark = pytest.mark.needs_book


@pytest.fixture(scope="module")
def payload():
    from model.engine import run_release

    return build_payload(run_release(gates=False), with_slow=False)


@pytest.fixture(scope="module")
def full_payload(parallel_band):
    """Полный выпуск (полоса, суждения) — только для тестов `ci_only`; полоса — пулом."""
    from model.engine import run_release

    with parallel_band():
        return build_payload(run_release(gates=False))


def test_bridge_adds_up_exactly(payload):
    """Мост сходится построчно: EV − требования = капитал до дисконта, дисконт — ставкой."""
    discount = payload["meta"]["governance_discount"]
    for scenario in payload["scenarios"]:
        before, after = scenario["equity_before_governance"], scenario["equity"]
        assert scenario["ev"] - scenario["claims"] == pytest.approx(before, abs=0.2), scenario["name"]
        expected = before * (1 - discount) if before > 0 else before
        assert after == pytest.approx(expected, abs=0.2), scenario["name"]


def test_price_follows_from_equity(payload):
    """Цена клетки — капитал на акцию; допуск — шаг округления печати (0,1 млрд и 1 ₽)."""
    shares = payload["meta"]["shares_mln"]
    slack = 0.05 * 1000 / shares + 0.5
    for scenario in payload["scenarios"]:
        assert scenario["price_published"] == pytest.approx(scenario["equity"] * 1000 / shares, abs=slack)


def test_leverage_and_coverage_are_consistent(payload):
    """ЧД/EBITDA и покрытие — из годовых строк, без множителей; неполный год помечен."""
    for scenario in payload["scenarios"]:
        full = [r for r in scenario["annual"] if not r["partial"] and r["ebitda"]]
        assert len(full) >= 9, "почти весь горизонт должен состоять из полных лет"
        for row in full:
            assert row["leverage"] == pytest.approx(row["net_debt"] / row["ebitda"], rel=0.02, abs=0.006)
            if row["interest"] > 0 and row["interest_cover"] < 99:
                assert row["interest_cover"] == pytest.approx(row["ebitda"] / row["interest"], rel=0.05)
        partial = [r for r in scenario["annual"] if r["partial"]]
        assert len(partial) == 1 and partial[0]["year"] == full[0]["year"] - 1


def test_market_percentile_matches_the_grid(payload):
    market = payload["fair_value"]["market"]
    mass = sum(c["probability"] for c in payload["grid"] if c["price_published"] <= market)
    assert payload["fair_value"]["market_percentile"] == pytest.approx(mass, abs=0.01)


def test_probability_of_zero_equity_matches_the_grid(payload):
    mass = sum(c["probability"] for c in payload["grid"] if c["equity"] <= 0)
    assert payload["fair_value"]["p_equity_nonpositive"] == pytest.approx(mass, abs=0.01)


def test_rub_per_ev_percent_is_arithmetic(payload):
    fv = payload["fair_value"]
    assert fv["rub_per_ev_percent"] == pytest.approx(
        fv["ev_market_implied"] / 100.0 * 1000.0 / payload["meta"]["shares_mln"], rel=0.01)
    assert fv["ev_gap"] == pytest.approx(fv["ev_model"] / fv["ev_market_implied"] - 1, abs=0.001)


def test_layer_intrinsic_follows_from_v0_and_claims(payload):
    """Внутренняя стоимость слоя = (V0 − D)·(1 − g) на акцию, без пола."""
    shares = payload["meta"]["shares_mln"]
    discount = payload["meta"]["governance_discount"]
    for name, layer in payload["layers"].items():
        raw = layer["v0"] - layer["claims"]
        expected = (raw * (1 - discount) if raw > 0 else raw) * 1000 / shares
        assert layer["intrinsic"] == pytest.approx(expected, abs=2.0), name


def test_cells_over_the_credit_limit_are_flagged_by_the_limit(payload):
    """Флаг «долг выше лимита линий» — ровно у клеток, чей путь валового долга выше лимита."""
    from model.book import book
    from model.financing import credit_limit

    limit = credit_limit(book())
    for c in payload["grid"]:
        assert c["over_credit_limit"] == (c["max_gross_debt"] > limit + 0.5) or abs(c["max_gross_debt"] - limit) <= 0.5


def test_the_printed_bridge_adds_up(payload):
    """Сумма НАПЕЧАТАННЫХ строк моста — НАПЕЧАТАННЫЙ итог; активы — строки со знаком «+»."""
    for scenario in payload["scenarios"]:
        lines = scenario.get("claims_lines") or []
        printed = sum(line["value"] for line in lines)
        assert printed == pytest.approx(scenario["claims"], abs=0.15), scenario["name"]
        assert scenario["ev"] - printed == pytest.approx(scenario["equity_before_governance"], abs=0.2)
    first = {line["key"]: line["value"] for line in payload["scenarios"][0]["claims_lines"]}
    assert first["loans_issued"] < 0, "займы выданные требования УМЕНЬШАЮТ"


@pytest.mark.ci_only
def test_the_bridge_card_prints_the_sign_of_each_line():
    bridge = _function("bridgeCard")
    assert 'line_.value < 0 ? "+" : "−"' in bridge and "const next = run - line_.value;" in bridge
    assert "pick.equity_before_governance" in bridge and "pick.equity" in bridge
    assert "price_structural" not in bridge and "pick.price_published" in bridge


def test_interest_interval_comes_from_the_declared_accuracy(payload):
    from indicators.collect import INTEREST_ACCURACY

    assert payload["nowcast"]["interest"]["accuracy"] == pytest.approx(INTEREST_ACCURACY)


@pytest.mark.ci_only
def test_the_interest_interval_is_not_a_literal_on_the_screen():
    assert "interest.accuracy" in _APP_CODE
    assert re.search(r"net_interest \* 0?\.\d+", _APP_CODE) is None


@pytest.mark.ci_only
def test_the_lead_rounds_by_the_same_step_as_the_release(full_payload):
    fv = full_payload["fair_value"]
    head = fv["headline"]
    step = head["print_step"]
    for exact, printed in (("low", "printed_low"), ("central", "printed_central"), ("high", "printed_high")):
        assert round(fv[exact] / step) * step == pytest.approx(fv[printed])
    assert round(head["median"] / step) * step == pytest.approx(head["printed_median"])
    for band, printed in (("band80", "printed_band80"), ("band50", "printed_band50")):
        assert [round(v / step) * step for v in head[band]] == pytest.approx(head[printed])
    assert not re.search(r"/ ?50\)? ?\* ?50", _APP_CODE) and "PRINT_STEP" not in _APP_CODE
    at = _function("headlineAt")
    assert "head.print_step" in at and "head.printed_median" in at and "head.printed_band80" in at
    point = _function("printedPoint")
    assert "fv.headline.printed_point" in point and "fv.printed_central" in point and "print_step" in point
    hero = _function("hero")
    assert "hl.printed_median" in hero and "fmt.rub(hl.median)" in hero
    assert "fmt.rub(point)" in _function("ledeHtml")


def test_the_business_value_line_is_the_engine_arithmetic(payload):
    """EV модели и EV в цене — на знаменателе ядра (EBITDA LTM якоря, канон проформы)."""
    from model.book import book

    fv = payload["fair_value"]
    cmp = fv["ev_comparison"]
    assert (cmp["ev_model"], cmp["ev_market_implied"], cmp["gap"]) == (fv["ev_model"], fv["ev_market_implied"], fv["ev_gap"])
    assert payload["meta"]["periods_closed"] == 0
    checked = 0
    for ev, multiple in [(c["ev"], c["ev_ebitda"]) for c in payload["grid"]]:
        if multiple <= 0.005:
            continue
        # Допуск — округление печати: EV до 0,1, мультипликатор до 0,01×, EBITDA до 0,01.
        assert (ev - 0.05) / (multiple + 0.005) - 0.006 <= cmp["ebitda_ltm"] <= (ev + 0.05) / (multiple - 0.005) + 0.006
        checked += 1
    assert checked >= 36
    assert cmp["ebitda_ltm"] == pytest.approx(book()["facts"]["anchor"]["ebitda_ltm"], abs=0.01)
    assert cmp["ev_ebitda_model"] == pytest.approx(cmp["ev_model"] / cmp["ebitda_ltm"], abs=0.006)
    assert (cmp["ev_ebitda_model"] < cmp["ev_ebitda_market"]) == (cmp["gap"] < 0)


@pytest.mark.ci_only
def test_the_judgement_spread_is_a_selection_from_the_judgements(full_payload):
    """Строка разброса — минимум и максимум таблицы суждений по ключам осей выпуска."""
    from model.payload import HEADLINE_JUDGEMENT_AXES

    rows = {j["key"]: j for j in full_payload["judgements"]}
    spread = full_payload["fair_value"]["judgement_spread"]
    assert spread["missing"] == []
    assert [a["axis"] for a in spread["axes"]] == [axis for axis, _, _ in HEADLINE_JUDGEMENT_AXES]
    everything = []
    for axis, (name, title, keys) in zip(spread["axes"], HEADLINE_JUDGEMENT_AXES):
        assert axis["title"] == title and axis["keys"] == list(keys)
        prices = [rows[k][f"price_{side}"] for k in keys for side in ("low", "high")]
        assert axis["low"]["price"] == min(prices) and axis["high"]["price"] == max(prices), name
        for end in (axis["low"], axis["high"]):
            row = rows[end["key"]]
            assert end["price"] == row[f"price_{end['bound']}"] and end["value"] == row[f"{end['bound']}_label"]
        everything += prices
    assert spread["low"] == min(everything) and spread["high"] == max(everything)


def test_the_judgement_spread_names_what_it_could_not_find():
    """Суждение, выпавшее из таблицы, не выпадает из разброса молча."""
    from model.payload import HEADLINE_JUDGEMENT_AXES, judgement_spread

    keys = [k for _, _, ks in HEADLINE_JUDGEMENT_AXES for k in ks]
    rows = [dict(key=k, label=k, low_label=f"{k}-lo", high_label=f"{k}-hi",
                 price_low=1000 - i, price_high=2000 + i) for i, k in enumerate(keys[1:], 1)]
    spread = judgement_spread(rows)
    assert spread["missing"] == [keys[0]]
    assert judgement_spread([]) is None


@pytest.mark.ci_only
def test_the_value_screen_prints_both_blocks_and_survives_their_absence(full_payload):
    """Медленные блоки читаются в одном месте, и это место начинает с выхода без них."""
    assert "ev_first_line" in full_payload["fair_value"] and "peers_same_base" in full_payload["market"]
    hero = _function("hero")
    assert "if (!head || !Array.isArray(head.low_draws) || !head.low_draws.length) return heroWithoutBand(d);" in hero
    assert _APP_CODE.count("judgement_spread") == 1
    spread = _function("spreadCard")
    first = spread[spread.index("{") + 1:].strip().splitlines()
    assert first[0].strip() == "const spread = d.fair_value.judgement_spread;"
    assert first[1].strip().startswith("if (!spread")
    assert "fmt.num(a.low.price)" in spread and "fmt.num(a.high.price)" in spread
    for axis in full_payload["fair_value"]["judgement_spread"]["axes"]:
        for end in (axis["low"], axis["high"]):
            literal = str(int(end["price"]))
            assert not re.search(rf"(?<![\d.]){literal}(?!\d)", _APP_CODE), f"число выпуска литералом: {literal}"


@pytest.mark.ci_only
def test_the_multiples_card_shows_the_live_peers_and_the_subject_row():
    """Аналоги — только живые на одной базе и строка эмитента (`subject_key`);
    констант книги на экране нет (D16)."""
    peers = _function("peersCard")
    assert "block.rows" in peers and "r.ev_ebitda" in peers and "block.subject_key" in peers
    assert "line_.ev_ebitda" in peers and "line_.ev_ebitda_v_star" in peers and "r.missing" in peers
    assert "bc." not in peers and "peersCard(d)" in _function("screenMarket")


def test_one_net_debt_on_the_valuation_date_and_the_register_beside_it(payload):
    from model.book import book

    debt = payload["debt"]
    assert debt["register"]["available"]
    assert debt["net_debt"] == pytest.approx(debt["term_debt"] - debt["cash"], abs=0.11)
    assert debt["net_debt_reported"] == book()["facts"]["anchor"]["net_debt"]
    for scenario in payload["scenarios"]:
        lines = [line for line in scenario["claims_lines"] if line["key"] == "net_debt"]
        assert len(lines) == 1 and abs(lines[0]["value"] - debt["net_debt"]) > 1.0, scenario["name"]


@pytest.mark.ci_only
def test_the_debt_screen_labels_one_net_debt_on_the_valuation_date():
    body = _function("debtKpis")
    for label in ('"чистый долг на дату оценки"', '"по реестру траншей и кассе на дату"'):
        assert _APP_CODE.count(label) == 1 and label in body, label
    assert re.search(r'kpi\(fmt\.bn\(ndLine\.value, 1\), "чистый долг на дату оценки"', body)
    assert re.search(r'kpi\(fmt\.bn\(debt\.net_debt, 1\), "по реестру траншей и кассе на дату"', body)
    assert 'find((line_) => line_.key === "net_debt")' in body and "debtKpis(d, pick)" in _function("screenDebt")
    assert _APP_CODE.count("debt.net_debt,") == 1
    assert not re.search(r"net_debt\w*\s*-\s*\w*\.?net_debt|ndLine\.value\s*-", _APP_CODE)
    assert "Расходятся они на поток" in body and "reg.reason" in body, "без реестра — причина словами"


def test_after_a_closed_half_the_bridge_says_what_the_engine_counts(payload):
    import copy
    import inspect
    from datetime import timedelta

    from model.book import book, periods
    from model.core import period_bounds, time_position
    from model.payload import open_period

    meta = payload["meta"]
    assert meta["open_period"] == meta["horizon"][0] and meta["periods_closed"] == 0
    A = copy.deepcopy(book())
    P = periods(A["meta"]["first_period"], A["meta"]["last_period"])
    A["meta"]["valuation_date"] = (period_bounds(P[1])[0] + timedelta(days=59)).isoformat()
    assert time_position(A)[0] == 1
    assert open_period(A) == P[1] != A["meta"]["first_period"]
    assert "open_period=open_period(A)" in inspect.getsource(build_payload)


@pytest.mark.ci_only
def test_after_a_closed_half_the_screen_prints_the_open_period():
    body = _function("debtKpis")
    assert "d.meta.periods_closed > 0" in body and "Модельный чистый долг сценария на конец последнего закрытого" in body
    parts = _function("evPartsCard")
    assert "PV свободного потока с ${d.meta.open_period || d.meta.horizon[0]}" in parts
    assert "yearOf(d.meta.horizon[1])" in parts, "год мультипликатора выхода — из горизонта выпуска"


def test_the_ev_rows_add_up_to_the_ev(payload):
    """EV = поток + щит с пулом убытков + терминал (доля выпуска)."""
    for s in payload["scenarios"]:
        residual = s["ev"] - s["pv_fcff"] - s["pv_tax_shield"]
        assert residual == pytest.approx(s["terminal_share"] * s["ev"], abs=0.3), s["name"]


@pytest.mark.ci_only
def test_the_ev_card_prints_the_parts_of_the_release():
    parts = _function("evPartsCard")
    for field in ("pick.pv_fcff", "pick.pv_tax_shield", "pick.terminal_share"):
        assert field in parts, field
    assert "налогового щита и пула убытков" in parts and "pick.ev - pick.pv_fcff" not in _APP_CODE


def test_the_dividend_rung_and_the_bases_the_screen_prints_are_the_release_arithmetic(payload):
    """Числа карточек «Ленты» на экране «Деньги и долг» — тождества выпуска:
    ступень лестницы — по отчётному ЧД/EBITDA; МСФО 16 − до МСФО 16 = аренда."""
    dv = payload["dividends"]
    cur = dv["current"]
    assert cur["leverage_reported"] == pytest.approx(cur["net_debt"] / cur["ebitda_ltm_reported"], abs=1e-3)
    rung = next(r for r in dv["ladder"] if r["max_leverage"] is None or cur["leverage_reported"] < r["max_leverage"])
    assert cur["rung"] == rung["rung"]
    b = payload["bases"]["anchor"]
    assert b["net_debt_ifrs16"] - b["net_debt_ias17"] == pytest.approx(b["lease_liabilities"], abs=0.01)
    for row in payload["bases"]["debt"]:
        assert row["ifrs16"]["net_debt"] - row["ias17"]["net_debt"] == pytest.approx(row["lease_gap"], abs=0.01)
    g = payload["governance"]
    assert sum(c["signed"] for c in g["components"]) == pytest.approx(g["sum_signed"], abs=1e-9)
    assert g["sum_signed"] == pytest.approx(g["discount"], abs=1e-9)
