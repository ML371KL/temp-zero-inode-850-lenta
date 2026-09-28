# -*- coding: utf-8 -*-
"""Шесть данных, которых не хватало витрине (решение владельца 25.09.2026).

Исполнитель редизайна перечислил, чего нет в выпуске: нейтральной маржи и
наклона «что даст отчёт», референс-класса эпизодов маржи, сроков объяснений
гейтов, P(ниже рынка) и EV центра при λ, отличном от книги, истории рядов
для плиток и единиц у суждений с ключами у вклада в полосу. Теперь всё это
печатает выпуск, а витрина только показывает.

Здесь проверяется две вещи: числа выпуска — те же, что в таблицах книги
(`data/assumptions/results.json`), и витрина берёт их из выпуска, а не считает
сама. Проверки полного выпуска и текста витрины — `ci_only`: тождества
полного выпуска сверяет и сама сборка (`validate`). Шаг ползунка против
сетки λ модели — в такте.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

from model.engine import run_release
from model.payload import (INDICATOR_HISTORY_DAYS, JUDGEMENT_UNITS, LAMBDA_STEPS,
                           build_payload, judgement_unit, lambda_grid,
                           next_report_neutral_block, tile_history)
from tests.web_source import APP_CODE, function_body as _function_body

ROOT = Path(__file__).resolve().parents[1]


def _results() -> dict:
    """Таблицы книги (`results.json` рядом с книгой) — лениво: без книги модуль собирается."""
    from model.paths import BOOK_DIR

    return json.loads((BOOK_DIR / "results.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def payload():
    return build_payload(run_release(gates=False), with_slow=False)


@pytest.fixture(scope="module")
def full_payload(parallel_band):
    # Медленные блоки кэшируются по содержанию книги (`payload._SLOW_BLOCKS`):
    # в одном прогоне CI полоса считается один раз на все модули; пулом.
    with parallel_band():
        return build_payload(run_release(gates=False), with_slow=True)


# ------------------------------------------ 1. нейтральная маржа и наклон


@pytest.mark.needs_book
@pytest.mark.ci_only
def test_the_neutral_margin_and_the_slope_are_the_books(full_payload):
    """Нейтральная маржа и наклон выпуска — числа таблиц книги.

    Таблицы: `next_report_neutral` (бисекция на [3 %; 7 %]) и
    `next_report_slope_rub_per_0p1pp.headline` (крайние строки таблицы «что
    даст отчёт»). На книге 1.4 — 4,71 % и ≈148 ₽ на 0,1 п.п. Книга 1.5
    (`diagnostics: median`) решает для печатаемой МЕДИАНЫ — таблица
    `median_diagnostics` (4,754 % и ≈76 ₽ на 0,1 п.п.).
    """
    payload = full_payload
    got = payload["next_report_neutral"]
    median = _results().get("median_diagnostics")
    rows = payload["next_report_value"]
    if median:
        ref, ref_rows = median["next_report_neutral"], median["next_report_value"]
        assert got["target"] == "median"
        slope = ((ref_rows[-1]["central"] - ref_rows[0]["central"])
                 / ((ref_rows[-1]["margin"] - ref_rows[0]["margin"]) * 1000))
        value, now = "median", payload["fair_value"]["headline"]["median"]
    else:
        ref = _results()["next_report_neutral"]
        slope = _results()["next_report_slope_rub_per_0p1pp"]["headline"]
        value, now = "central", payload["fair_value"]["central"]
    assert got["period"] == ref["period"]
    assert got["margin"] == pytest.approx(ref["margin"], abs=5e-6)
    assert got["central"] == pytest.approx(ref["central"], abs=0.05)
    assert got["slope_rub_per_0p1pp"] == pytest.approx(slope, abs=0.05)
    assert got["slope_between"] == [rows[0]["margin"], rows[-1]["margin"]]
    # Нейтральная — там, где линия пересекает «сейчас»: между строками
    # таблицы, по разные стороны от числа выпуска.
    below = [r for r in rows if r["margin"] < got["margin"]]
    above = [r for r in rows if r["margin"] > got["margin"]]
    assert below and above
    assert below[-1][value] <= now <= above[0][value]


@pytest.mark.needs_book
def test_a_failed_bisection_is_a_note_and_not_a_lost_release():
    """Отказ бисекции — поле `error` вместо чисел; наклон и выпуск остаются."""
    release = run_release(gates=False)
    block = next_report_neutral_block(release, {"error": "ValueError: нет корня"})
    assert block["error"] == "ValueError: нет корня" and "margin" not in block
    assert block["slope_rub_per_0p1pp"] and block["period"] == release.next_report[0]["period"]


@pytest.mark.needs_book
def test_a_fast_build_has_an_empty_block():
    """Быстрая сборка (без полосы) нейтральную маржу не считает — и так и пишет."""
    fast = build_payload(run_release(gates=False), with_slow=False)
    assert fast["next_report_neutral"] == {}


# ------------------------------------------ 4. P(ниже рынка) и EV при λ


@pytest.mark.tact
def test_the_sliders_steps_are_the_lambda_grid():
    """Шаг ползунка витрины = 1 / LAMBDA_STEPS модели: константа сетки λ и
    разметка сверяются без выпуска, в такте."""
    step = re.search(r'id: "lambda", type: "range", min: "0", max: "1", step: "([0-9.]+)"', APP_CODE)
    assert step, "ползунок λ не найден"
    assert float(step.group(1)) == pytest.approx(1 / LAMBDA_STEPS)
    # λ книги вне сетки добавляется отдельной строкой, а не теряется.
    assert 0.37 in lambda_grid(0.37) and len(lambda_grid(0.37)) == LAMBDA_STEPS + 2


@pytest.mark.needs_book
@pytest.mark.ci_only
def test_the_lambda_tables_sit_on_the_sliders_steps(full_payload):
    """Сетка λ выпуска — положения ползунка (`LAMBDA_STEPS`)."""
    payload = full_payload
    head = payload["fair_value"]["headline"]
    first = payload["fair_value"]["ev_first_line"]
    want = [round(i / LAMBDA_STEPS, 4) for i in range(LAMBDA_STEPS + 1)]
    assert [round(r["lambda"], 4) for r in head["by_lambda"]] == want
    assert [round(r["lambda"], 4) for r in first["by_lambda"]] == want


@pytest.mark.needs_book
@pytest.mark.ci_only
def test_at_the_books_lambda_the_tables_are_the_release(full_payload):
    """При λ книги строки таблиц — числа заголовка и первой строки, во всех знаках."""
    payload = full_payload
    head = payload["fair_value"]["headline"]
    first = payload["fair_value"]["ev_first_line"]
    row = [r for r in head["by_lambda"] if r["release"]]
    assert len(row) == 1 and row[0]["lambda"] == pytest.approx(head["own_macro_confidence"])
    assert (row[0]["p_below_market"], row[0]["market_percentile"], row[0]["mean"]) == (
        head["p_below_market"], head["market_percentile"], head["mean"])
    ev = [r for r in first["by_lambda"] if r["release"]]
    assert len(ev) == 1
    assert (ev[0]["v0"], ev[0]["v_star"], ev[0]["gap"], ev[0]["rub_per_1pct_ev"]) == (
        first["v0"], first["v_star"], first["gap"], first["rub_per_1pct_ev"])


@pytest.mark.needs_book
@pytest.mark.ci_only
def test_off_the_books_lambda_the_rows_follow_the_books_rule(full_payload):
    """Прочие строки — правило книги на прогонах выпуска, а EV — расчёт ядра.

    P(ниже рынка), перцентиль и среднее при λ пересчитываются здесь из тех же
    прогонов выпуска (низ + λ·(верх − низ), до рубля) — и обязаны совпасть
    точно: таблица воспроизводима из самого выпуска. EV при λ = 0 и λ = 1
    обязаны совпасть с V0 и V* слоёв: это ДРУГОЙ расчёт ядра (обращение одного
    слоя, `solve_v0`), а не та же бисекция центра.
    """
    payload = full_payload
    head = payload["fair_value"]["headline"]
    lows, highs, market = head["low_draws"], head["high_draws"], head["market"]
    n = len(lows)
    for row in head["by_lambda"]:
        if row["release"]:
            continue
        lam = row["lambda"]
        centres = [lo + lam * (hi - lo) for lo, hi in zip(lows, highs)]
        assert row["p_below_market"] == round(sum(c < market for c in centres) / n, 4), lam
        assert row["market_percentile"] == round(sum(c <= market for c in centres) / n, 4), lam
        assert row["mean"] == pytest.approx(sum(centres) / n, abs=0.05), lam
    first = payload["fair_value"]["ev_first_line"]
    low_row, high_row = first["by_lambda"][0], first["by_lambda"][-1]
    for row, name in ((low_row, "macro_neutral"), (high_row, "analytical")):
        layer = first["layers"][name]
        assert row["v0"] == pytest.approx(layer["v0"], abs=0.051), name
        assert row["v_star"] == pytest.approx(layer["v_star"], abs=0.11), name
        assert row["gap"] == pytest.approx(layer["gap"], abs=2e-4), name


# ------------------------------------------ 2. референс-класс эпизодов


# ------------------------------------------ 3. сроки объяснений гейтов


@pytest.mark.needs_book
def test_the_gates_carry_their_deadlines():
    """Срок объяснения каждого сработавшего гейта — в выпуске, как в файле объяснений."""
    release = run_release(gates=True)
    gates = build_payload(release, with_slow=False)["gates"]
    assert gates, "на книге 1.4 гейты срабатывают — проверять нечего"
    spec = yaml.safe_load((ROOT / "data" / "assumptions" / "gate_explanations.yaml")
                          .read_text(encoding="utf-8")) or {}
    spec = spec.get("explanations", spec)
    for g in gates:
        assert isinstance(g["expiring"], bool), g["key"]
        until = (spec.get(g["key"]) or {}).get("valid_until")
        if until is None:
            assert g["valid_until"] is None, g["key"]
        else:
            assert g["valid_until"] == str(until), g["key"]


# ------------------------------------------ 5. история плиток


@pytest.mark.needs_book
def test_tiles_carry_a_year_of_history_ending_at_the_tile(payload):
    """Линия плитки кончается там же, где её цифра; у прочих рядов истории нет.

    Плитка, ряда которой ещё нет, — строка с причиной (`missing`) и пустой
    историей: пустое место на экране называет причину (P4b)."""
    tiles = [i for i in payload["indicators"] if i["tile"] and "missing" not in i]
    assert tiles, "в фикстурном состоянии нет плиток — проверять нечего"
    for t in (i for i in payload["indicators"] if i["tile"] and "missing" in i):
        assert t["history"] == [] and t["value"] is None and t["missing"], t["id"]
    for t in tiles:
        hist = t["history"]
        assert hist, t["id"]
        periods = [p for p, _ in hist]
        assert periods == sorted(periods), t["id"]
        if t["value"] is not None:
            assert hist[-1] == [t["period"], t["value"]], t["id"]
    assert all("history" not in i for i in payload["indicators"] if not i["tile"])


@pytest.mark.tact
def test_the_tile_history_is_one_year_of_the_latest_vintages():
    """Год до последней точки; из двух версий периода — последняя полученная."""
    from indicators.store import Point, Series

    start = date(2025, 1, 1)
    points = [Point(period=(start + timedelta(days=i)).isoformat(), value=float(i),
                    fetched_at="2026-09-01T00:00:00+00:00") for i in range(700)]
    # Пересмотр последнего периода: новая версия получена позже — она и в линии.
    points.append(Point(period=points[-1].period, value=-1.0, fetched_at="2026-09-02T00:00:00+00:00"))
    series = Series(id="x", unit="", cadence="daily", label="x", channel=1, points=points)
    hist = tile_history(series)
    last = start + timedelta(days=699)
    assert hist[-1] == [last.isoformat(), -1.0]
    assert date.fromisoformat(hist[0][0]) == last - timedelta(days=INDICATOR_HISTORY_DAYS)
    assert len(hist) == INDICATOR_HISTORY_DAYS + 1


# ------------------------------------------ 6. единицы суждений и ключи вклада


@pytest.mark.needs_book
@pytest.mark.ci_only
def test_every_judgement_carries_its_unit(full_payload):
    """Единица — в выпуске, по одному правилу; витрина её больше не угадывает."""
    payload = full_payload
    for row in payload["judgements"] + payload["assumptions"]:
        assert row["unit"] in JUDGEMENT_UNITS and row["unit"] == judgement_unit(row["key"]), row["key"]
    for axis in payload["fair_value"]["judgement_spread"]["axes"]:
        for end in (axis["low"], axis["high"]):
            assert end["unit"] == judgement_unit(end["key"])


@pytest.mark.tact
def test_the_unit_of_a_judgement_follows_its_key():
    assert judgement_unit("valuation.beta_u") == "plain"
    assert judgement_unit("valuation.erp") == "pct"
    assert judgement_unit("capex.segments.hyper.growth_capex_per_m2") == "bn_per_m2"
    assert judgement_unit("financing.leverage_target") == "times"
    assert judgement_unit("bridge.items[put].amount") == "bn"
    assert judgement_unit("что-то.новое") == "plain"


@pytest.mark.needs_book
@pytest.mark.ci_only
def test_every_contribution_names_its_paths_and_its_judgement(full_payload):
    """Вклад в полосу связан со строкой таблицы суждений ключом выпуска."""
    payload = full_payload
    keys = {j["key"] for j in payload["judgements"]}
    contributions = payload["fair_value"]["headline"]["contributions"]
    for c in contributions:
        assert c["paths"], c["axis"]
        assert c["judgement_key"] is None or c["judgement_key"] in keys, c
    linked = {c["paths"][0]: c["judgement_key"] for c in contributions}
    assert linked["capex.maintenance_pct.low"] == "capex.maintenance_pct.base"
    assert linked["valuation.erp"] == "valuation.erp"
    # Ось A-C0 пересекается и со строками целей одного режима (A-C1): связь —
    # со строкой того же набора путей, как бы ни легла сортировка таблицы.
    margin_axis = next(c for c in contributions if c["paths"][0] == "margin.regimes.stress.target.LT")
    row = next(j for j in payload["judgements"] if j["key"] == margin_axis["judgement_key"])
    assert set(row["paths"]) == set(margin_axis["paths"])


@pytest.mark.tact
def test_the_contribution_link_does_not_follow_the_table_order():
    """Порядок строк таблицы суждений (цена ошибки) плывёт — связь от него не зависит."""
    from model.payload import link_contributions

    axis = {"axis": "маржа LT", "paths": ["a", "b", "c", "d"]}
    rows = [{"key": "b", "paths": ["b"]},             # дороже, но цель одного режима
            {"key": "a", "paths": ["a", "b", "c", "d"]},
            {"key": "x", "paths": ["x"]}]
    for order in (rows, rows[::-1]):
        contributions = [dict(axis)]
        link_contributions(contributions, order)
        assert contributions[0]["judgement_key"] == "a"
    partial = [dict(axis, paths=["b", "q"])]
    link_contributions(partial, rows)
    assert partial[0]["judgement_key"] == "b"
    nothing = [dict(axis, paths=["z"])]
    link_contributions(nothing, rows)
    assert nothing[0]["judgement_key"] is None


@pytest.mark.needs_book
def test_the_reference_only_blocks_cannot_stop_the_release():
    """Испорченный справочный блок книги и нечисловая точка ряда — не повод не выйти."""
    import copy

    from indicators.store import Point, Series
    from model.book import book
    from model.payload import _soft, reference_class_block

    A = copy.deepcopy(book())
    A["reference_class_margin"]["n"] = 0
    broken = _soft(reference_class_block, A, {"stress": 1.0})
    assert "error" in broken and "ZeroDivisionError" in broken["error"]
    points = [Point(period="2026-09-01", value=1.0, fetched_at="2026-09-01T00:00:00+00:00"),
              Point(period="2026-09-02", value="битое", fetched_at="2026-09-02T00:00:00+00:00"),
              Point(period="2026-09-03", value=2.0, fetched_at="2026-09-03T00:00:00+00:00")]
    series = Series(id="x", unit="", cadence="daily", label="x", channel=1, points=points)
    assert tile_history(series) == [["2026-09-01", 1.0], ["2026-09-03", 2.0]]


# ------------------------------------------ витрина: показывает, а не считает


@pytest.mark.ci_only
def test_the_front_takes_all_six_from_the_release():
    """Каждое из шести — чтением поля выпуска в своём месте экрана."""
    assert "UNIT_BY_KEY" not in APP_CODE and "formatByKey" not in APP_CODE
    for call in re.findall(r"fmt\.bookValue\(([^)]*)\)", APP_CODE):
        assert ".key" not in call, f"единица по ключу вместо единицы выпуска: {call}"
    assert "atLambda(head.by_lambda" in _function_body("hero")
    assert "atLambda(first.by_lambda" in _function_body("heroTiles")
    assert "neutralOnScale(d)" in _function_body("impactChart")
    assert "d.next_report_neutral" in _function_body("neutralOnScale")
    # Строка легенды — ровно при нарисованной вертикали: то же условие.
    assert "neutralOnScale(d)" in _function_body("impactCard")
    assert "neutralSentence(d)" in _function_body("impactCard")
    assert "neutralSentence(d)" in _function_body("reportTeaser")
    assert "d.next_report_neutral" in _function_body("neutralSentence")
    assert "referenceClass(rp, order)" in _function_body("regimesCard")
    assert "rp.reference_class" in _function_body("referenceClass")
    assert "rp.reference_class" in _function_body("impactCard")
    assert "g.valid_until" in _function_body("gatesCard") and "g.expiring" in _function_body("gatesCard")
    assert "sparkline(i)" in _function_body("indicatorsCard")
    assert "item.history" in _function_body("sparkline")
    assert "r.judgement_key" in _function_body("bandDrivers")


@pytest.mark.ci_only
def test_the_front_still_computes_nothing_of_its_own():
    """Новые места витрины ничего не досчитывают: ни суммы, ни доли по прогонам."""
    for name in ("atLambda", "neutralSentence", "neutralOnScale", "referenceClass", "sparkline"):
        body = _function_body(name)
        assert ".reduce(" not in body and "low_draws" not in body, name
    # Наклон и нейтральная — числами выпуска, не разностью строк таблицы.
    impact = _function_body("impactChart") + _function_body("neutralSentence")
    assert "slope_rub_per_0p1pp" in impact
    assert not re.search(r"rows\[rows\.length - 1\]\.central\s*-\s*rows\[0\]\.central", impact)
