# -*- coding: utf-8 -*-
"""Журнал прогнозов: зачёт квартала после позднего факта (внешний аудит, T02).

Слой переходит на квартал только после факта предыдущего. Маржа 4 кв. выходит
годовым отчётом (≈26.03) — позже момента зачёта 1 кв. (45 дней до его отчёта,
≈15.03), поэтому зачётного прогноза у 1 кв. не было никогда: четверть событий
молча выпадала из счёта допуска, а календарь допуска этого не знал и печатал
срок решения на три квартала раньше настоящего. Решение владельца 30.09.2026
(вариант B2): у такого квартала в зачёт идёт первый прогноз после факта
предыдущего, на фактическом горизонте; эталоны прежние — модель и эталоны видят
факт предыдущего квартала на одну дату.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from indicators import calendar, collect, issuer, periods, quarterly, retro
from indicators import journal as journal_module
from indicators.journal import (MODEL_EXPECTATION, SCORE_AFTER_PREVIOUS_FACT, SCORE_AT_HORIZON,
                                SCORING_HORIZON_DAYS, Journal, admission_rule_text,
                                expected_scoring, forecast_period, naive_margin, report_date)
from indicators.nowcast import MARGIN_VERSION
from indicators.store import Point, Store
from tests.test_quarterly_layer import ANCHOR, core_grid, toy_book

pytestmark = pytest.mark.tact

MARGIN = issuer.series("ebitda_margin_pre16")
REVENUE = issuer.series("revenue_pre16")


def _record_on(journal: Journal, made: date, *, target: str, period: str, value=None,
               naive=None) -> None:
    """Запись «как если бы» её сделали в день `made` (правка запрещена триггерами,
    поэтому время подставляется при вставке)."""
    stamp = made.isoformat() + "T12:00:00+00:00"
    with sqlite3.connect(journal.path) as db:
        if value is not None:
            db.execute(
                "INSERT INTO forecasts (made_at, target, period, value, std_error, equation,"
                " version, inputs_sha, inputs, note) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (stamp, target, period, value, None, "eq", "v1", "sha", "{}", ""))
        for method, item in (naive or {}).items():
            db.execute("INSERT INTO naive (made_at, target, period, method, value)"
                       " VALUES (?,?,?,?,?)", (stamp, target, period, method, item))


# ================================================== T02: зачёт после позднего факта


def test_by_the_calendar_only_the_first_quarter_follows_a_late_report():
    """Годовой отчёт (маржа 4 кв.) — ≈26.03, отчёт 1 кв. — ≈29.04: до отчёта 1 кв. от
    факта 4 кв. ≈34 дня — меньше 45. У остальных кварталов предыдущий отчёт выходит
    за ≈90 дней. Выручка 4 кв. приходит операционными результатами в феврале —
    выручку 1 кв. правило не трогает."""
    for year in (2027, 2028, 2029, 2030):
        rule, days = expected_scoring(f"{year}Q1", MARGIN)
        assert rule == SCORE_AFTER_PREVIOUS_FACT and 25 <= days < SCORING_HORIZON_DAYS, (year, days)
        gap = report_date(f"{year}Q1") - report_date(f"{year - 1}Q4")
        assert days == gap.days
        for quarter in (f"{year}Q2", f"{year}Q3", f"{year}Q4"):
            assert expected_scoring(quarter, MARGIN) == (SCORE_AT_HORIZON, SCORING_HORIZON_DAYS)
        assert expected_scoring(f"{year}Q1", REVENUE) == (SCORE_AT_HORIZON, SCORING_HORIZON_DAYS)
    assert expected_scoring("2027Q1", MARGIN)[1] == 34


def test_the_first_forecast_after_a_late_previous_fact_is_scored_at_its_real_horizon(tmp_path):
    """Факт 4 кв. вышел 26.03 — позже момента зачёта 1 кв. (15.03). В зачёт идёт
    первый прогноз 1 кв. после него (26.03, горизонт 34 дня), эталоны — того же
    дня; более поздние прогнозы — только «последнее слово»."""
    journal = Journal(tmp_path / "j.sqlite")
    fy, q1 = date(2027, 3, 26), date(2027, 4, 29)
    _record_on(journal, date(2027, 1, 10), target="t", period="2026Q4", value=0.080,
               naive={"seasonal_naive": 0.078})
    journal.record_actual("t", "2026Q4", 0.081, reported_on=fy)
    _record_on(journal, fy, target="t", period="2027Q1", value=0.0600,
               naive={"seasonal_naive": 0.0570, MODEL_EXPECTATION: 0.0600})
    _record_on(journal, fy + timedelta(days=20), target="t", period="2027Q1", value=0.0640,
               naive={MODEL_EXPECTATION: 0.0640})
    journal.record_actual("t", "2027Q1", 0.0620, reported_on=q1)

    assert journal.horizons("t", "2027Q1")[SCORING_HORIZON_DAYS] is None
    scored = journal.scored("t", "2027Q1")
    assert scored["rule"] == SCORE_AFTER_PREVIOUS_FACT and scored["horizon_days"] == 34
    assert scored["value"] == pytest.approx(0.0600) and scored["moment"] == "2027-03-26"
    assert (scored["previous_period"], scored["previous_reported_on"]) == ("2026Q4", "2027-03-26")

    row = next(r for r in journal.scoreboard("t") if r["period"] == "2027Q1")
    assert row["forecast"] == pytest.approx(0.0600) and row["scoring_horizon_days"] == 34
    assert row["scoring_rule"] == SCORE_AFTER_PREVIOUS_FACT
    assert "фактическом горизонте 34 дн" in row["note"] and "2026Q4" in row["note"]
    assert row["naive_" + MODEL_EXPECTATION] == pytest.approx(0.0600), "эталон — того же дня"
    assert row["naive_seasonal_naive"] == pytest.approx(0.0570)
    assert row["last_word"]["value"] == pytest.approx(0.0640)
    # Обычный квартал по-прежнему идёт на 45 днях.
    other = next(r for r in journal.scoreboard("t") if r["period"] == "2026Q4")
    assert other["scoring_rule"] == SCORE_AT_HORIZON and other["scoring_horizon_days"] == 45


def test_a_late_first_forecast_is_not_scored_when_the_previous_fact_was_out_in_time(tmp_path):
    """Правило — только для кварталов, чей предыдущий факт ВЫШЕЛ позже момента
    зачёта. Если предыдущий отчёт вышел вовремя, а прогноз появился поздно (факт
    внесли с опозданием, слой включили позже), зачёта нет — и причина названа.
    Прогноз, записанный в день публикации факта или позже, в зачёт не идёт."""
    journal = Journal(tmp_path / "j.sqlite")
    q2, q3 = date(2027, 7, 30), date(2027, 10, 29)
    journal.record_actual("t", "2027Q2", 0.066, reported_on=q2)      # вышел за 91 день
    _record_on(journal, q3 - timedelta(days=20), target="t", period="2027Q3", value=0.070)
    journal.record_actual("t", "2027Q3", 0.071, reported_on=q3)
    assert journal.scored("t", "2027Q3") is None
    row = next(r for r in journal.scoreboard("t") if r["period"] == "2027Q3")
    assert row["forecast"] is None and row["scoring_rule"] is None
    assert row["note"].startswith("прогноза за 45 дней до отчёта не было: первый записан "
                                  "2027-10-09, факт за 2027Q2 (опубликован 2027-07-30)")
    # Первый прогноз — в день публикации факта: не прогноз.
    late = Journal(tmp_path / "late.sqlite")
    late.record_actual("t", "2026Q4", 0.08, reported_on=date(2027, 3, 26))
    _record_on(late, date(2027, 4, 29), target="t", period="2027Q1", value=0.062)
    late.record_actual("t", "2027Q1", 0.062, reported_on=date(2027, 4, 29))
    assert late.scored("t", "2027Q1") is None


def test_the_admission_rule_names_the_scoring_of_the_first_quarter():
    text = admission_rule_text(target=MARGIN)
    assert "в зачёт идёт прогноз за 45 дней до отчёта" in text
    assert "первый прогноз после факта предыдущего, на фактическом горизонте" in text


# ------------------------------------------------- такт нау-каста по дням


class _Clock(datetime):
    """Часы журнала: `made_at` и `recorded_at` — день симуляции."""

    day = date(2027, 1, 1)

    @classmethod
    def now(cls, tz=None):
        return datetime(cls.day.year, cls.day.month, cls.day.day, 14, 25, tzinfo=tz or timezone.utc)


def _true_margin(quarter: str) -> float:
    number = periods.parse(quarter).number
    return 0.066 + {1: -0.008, 2: 0.006, 3: -0.001, 4: 0.004}[number]


def test_the_daily_tact_scores_the_first_quarter_after_the_annual_report(tmp_path, monkeypatch):
    """Штатный `cmd_nowcast` каждый будний день с 01.02 по 05.05.2028; годовой отчёт
    и отчёт 1 кв. вносятся в центральные даты календаря, датабук (ряды хранилища) —
    в тот же день. Первый прогноз 1 кв. появляется в день годового отчёта, идёт в
    зачёт на фактическом горизонте и несёт главный эталон того же дня."""
    monkeypatch.setattr(journal_module, "datetime", _Clock)
    monkeypatch.setattr(collect, "anchor_facts", lambda: ANCHOR)
    # История эталонов — ряды датабука, какими они были на день симуляции.
    monkeypatch.setattr(collect, "quarterly_facts", lambda store: retro.quarterly_history(
        store=store, as_of=_Clock.day.isoformat()))
    store = Store(tmp_path / "state")
    journal = Journal(tmp_path / "journal.sqlite")
    halves = {f"{year}H{h}": ((690.0, 0.062) if h == 1 else (740.0, 0.070))
              for year in range(2026, 2030) for h in (1, 2)}
    expectation = (lambda A, q: quarterly.expected_quarter(A, q, grid=core_grid(rows=halves)))

    def publish(quarter: str, day: date) -> None:
        """Отчёт с маржой вышел: квартал — в датабук (ряды хранилища), факт — в журнал."""
        stamp = day.isoformat() + "T08:00:00+00:00"
        store.upsert(retro.DATABOOK_SERIES["revenue"], [Point(quarter, 300.0, stamp)])
        store.upsert(retro.DATABOOK_SERIES["ebitda"],
                     [Point(quarter, 300.0 * _true_margin(quarter), stamp)])
        _Clock.day = day
        journal.record_actual(MARGIN, quarter, _true_margin(quarter), reported_on=day)

    def publish_revenue(quarter: str, day: date) -> None:
        """Выручка квартала: у 4 кв. — операционными результатами, раньше маржи."""
        _Clock.day = day
        journal.record_actual(REVENUE, quarter, 300.0, reported_on=day)

    quarter = "2024Q1"                               # история датабука и журнала до 3 кв. 2027
    while periods.index(quarter) <= periods.index("2027Q3"):
        publish(quarter, report_date(quarter))
        publish_revenue(quarter, calendar.report_dates(quarter, "revenue")[0])
        quarter = periods.next_period(quarter)
    reports = {report_date(q): q for q in ("2027Q4", "2028Q1")}
    revenues = {calendar.report_dates(q, "revenue")[0]: q for q in ("2027Q4", "2028Q1")}
    assert sorted(reports) == [date(2028, 3, 25), date(2028, 4, 30)]
    assert min(revenues) < date(2028, 2, 15), "выручка 4 кв. — в феврале"

    day, first_seen = date(2028, 2, 1), {}
    while day <= date(2028, 5, 5):
        if day in reports:
            publish(reports[day], day)
        if day in revenues:
            publish_revenue(revenues[day], day)
        _Clock.day = day
        assert collect.cmd_nowcast(store, today=day, A=toy_book(), journal=journal,
                                   expectation=expectation) == 0
        first_seen.setdefault(forecast_period(journal, MARGIN, today=day), day)
        day += timedelta(days=1)

    assert first_seen["2028Q1"] == date(2028, 3, 25), "слой переходит на 1 кв. в день факта 4 кв."
    rows = {r["period"]: r for r in journal.scoreboard(MARGIN)}
    q1, q4 = rows["2028Q1"], rows["2027Q4"]
    assert q4["scoring_rule"] == SCORE_AT_HORIZON and q4["scoring_horizon_days"] == 45, (
        "4 кв.: прогноз за 45 дней до годового отчёта был — обычный зачёт")
    assert q1["scoring_rule"] == SCORE_AFTER_PREVIOUS_FACT
    assert q1["scoring_horizon_days"] == (date(2028, 4, 30) - date(2028, 3, 25)).days == 36
    assert q1["made_at"].startswith("2028-03-25") and q1["version"] == MARGIN_VERSION
    history = {q: _true_margin(q) for q in ("2027Q1", "2026Q4", "2027Q4", "2027Q3", "2027Q2")}
    want = naive_margin(history, "2028Q1")
    assert q1["main_benchmark"] == "yoy_plus_shift"
    assert q1["naive_yoy_plus_shift"] == pytest.approx(want["yoy_plus_shift"])
    assert q1["beats_benchmark"] is not None
    status = journal.admission(MARGIN, version=MARGIN_VERSION)
    assert status.events == 2, "4 кв. 2027 и 1 кв. 2028 — оба с зачётным прогнозом"
