# -*- coding: utf-8 -*-
"""Периоды слоя индикаторов и отчёты Ленты (`indicators/periods.py`, `calendar.py`).

Модель полугодовая, отчётность квартальная (D2): журнал и нау-каст — на
кварталах, правило A-P2u — на полугодии. У 4 квартала два события: выручка
(операционные, февраль) и маржа (годовой МСФО, март).
"""
from __future__ import annotations

import json
from datetime import date

import pytest

from indicators import calendar, issuer, periods

# Такт (ops/run.sh, TACT_TESTS): слой индикаторов на фикстурах и двойниках —
# быстрый, в сеть не ходит (тесты `network` такт исключает выражением).
pytestmark = pytest.mark.tact


def test_ids_bounds_and_order():
    assert periods.bounds("2026Q3") == (date(2026, 7, 1), date(2026, 9, 30))
    assert periods.bounds("2026Q4") == (date(2026, 10, 1), date(2026, 12, 31))
    assert periods.bounds("2026H2") == (date(2026, 7, 1), date(2026, 12, 31))
    assert periods.bounds("2027H1") == (date(2027, 1, 1), date(2027, 6, 30))
    assert periods.bounds("2026FY") == (date(2026, 1, 1), date(2026, 12, 31))
    assert periods.days_in("2026Q1") == 90 and periods.days_in("2028Q1") == 91
    assert periods.next_period("2026Q4") == "2027Q1"
    assert periods.previous_period("2027Q1") == "2026Q4"
    assert periods.next_period("2026H2") == "2027H1"
    assert periods.shift("2026Q3", -5) == "2025Q2"
    assert periods.same_period_last_year("2026Q3") == "2025Q3"
    assert periods.same_period_last_year("2026H2") == "2025H2"
    assert periods.index("2026Q3") < periods.index("2026Q4") < periods.index("2027Q1")
    for bad in ("2026Q5", "2П2026", "2026H3", "26Q1", "", None):
        assert not periods.is_period(bad)
        with pytest.raises(ValueError):
            periods.parse(bad)


def test_quarters_and_halves_map_onto_each_other():
    assert periods.quarters_of("2026H2") == ("2026Q3", "2026Q4")
    assert periods.quarters_of("2027H1") == ("2027Q1", "2027Q2")
    assert periods.quarters_of("2026FY") == ("2026Q1", "2026Q2", "2026Q3", "2026Q4")
    assert periods.half_of_quarter("2026Q3") == "2026H2"
    assert periods.half_of_quarter("2027Q2") == "2027H1"
    assert periods.quarter_key("2026Q3") == "Q3"
    assert periods.full_year("2026Q3") == periods.full_year("2026H2") == "2026FY"
    assert periods.quarter_of(date(2026, 9, 30)) == "2026Q3"
    assert periods.quarter_of(date(2026, 10, 1)) == "2026Q4"
    assert periods.half_of(date(2027, 1, 2)) == "2027H1"
    assert periods.contains("2026H2", "2026Q4") and not periods.contains("2026H2", "2026Q2")
    with pytest.raises(ValueError):
        periods.half_of_quarter("2026H2")


def test_the_first_forecast_quarter_is_the_first_quarter_of_the_first_model_half():
    """D2: `meta.first_period = 2026H2` — первый прогнозный квартал 2026Q3."""
    assert periods.FIRST_FORECAST_QUARTER == periods.first_quarter("2026H2") == "2026Q3"


def test_one_report_is_one_event_and_the_fourth_quarter_has_two():
    """Тождество отчёта: 2 кв. и 1П — один релиз; 4 кв., 2П и год — два события."""
    p = issuer.series
    assert periods.report_id("2026Q3") == p("q3_2026")
    assert periods.report_id("2027Q1") == p("q1_2027")
    assert periods.report_id("2027Q2") == periods.report_id("2027H1") == p("h1_2027")
    for period in ("2026Q4", "2026H2", "2026FY"):
        assert periods.report_id(period) == p("fy2026"), period
        assert periods.report_id(period, periods.GIVES_REVENUE) == p("q4_2026_ops"), period
    # Выручка 3 кв. приходит тем же релизом, что и маржа.
    assert periods.report_id("2026Q3", periods.GIVES_REVENUE) == p("q3_2026")
    assert periods.report_id("2П2026") == ""


def test_the_report_names_are_the_calendar_ids_of_the_book_draft():
    """Календарь сопоставляется с периодом по ИМЕНИ отчёта — имена обязаны совпасть
    с идентификаторами календаря книги (`lenta-calendar-v1`)."""
    fixture = json.loads((_FIXTURES / "calendar" / "calendar.json").read_text(encoding="utf-8"))
    ids = {e["id"] for e in fixture["events"]}
    for period, gives in (("2026Q3", "margin"), ("2026Q4", "revenue"), ("2026Q4", "margin"),
                          ("2027Q1", "margin"), ("2027Q2", "margin")):
        assert periods.report_id(period, gives) in ids, (period, gives)


def test_the_fallback_lags_follow_the_releases_of_2024_2026():
    """Запасное правило: Q1–Q3 — ≈30 дней (крайний 35), операционные Q4 — ≈38 (43),
    годовые финансовые — ≈85 (90). Календарь сильнее правила."""
    assert periods.fallback_report_window("2026Q3") == (date(2026, 10, 30), date(2026, 11, 4))
    assert periods.fallback_report_window("2027Q2") == periods.fallback_report_window("2027H1")
    ops = periods.fallback_report_window("2026Q4", periods.GIVES_REVENUE)
    fy = periods.fallback_report_window("2026Q4")
    assert ops == (date(2027, 2, 7), date(2027, 2, 12))
    assert fy == (date(2027, 3, 26), date(2027, 3, 31))
    assert periods.fallback_report_window("2026H2") == fy
    assert periods.fallback_report_window("nonsense") is None


def test_the_calendar_gives_the_centre_and_the_end_of_the_window():
    """Окно, а не выдуманная дата: центр — для горизонтов, конец — для сторожа."""
    path = _FIXTURES / "calendar" / "calendar.json"
    assert calendar.report_dates("2026Q3", path=path) == (date(2026, 10, 29), date(2026, 11, 3))
    assert calendar.report_dates("2026Q4", "revenue", path=path) == (
        date(2027, 2, 5), date(2027, 2, 12))
    assert calendar.report_dates("2026H2", path=path) == (date(2027, 3, 26), date(2027, 3, 29))
    # Нет события в календаре — правило лагов.
    assert calendar.report_dates("2027Q3", path=path) == periods.fallback_report_window("2027Q3")
    # Нет файла — тоже правило лагов, а не падение.
    missing = _FIXTURES / "calendar" / "no-such-file.json"
    assert calendar.report_dates("2026Q3", path=missing) == periods.fallback_report_window("2026Q3")

    fact = calendar.next_fact(date(2026, 10, 30), path=path)
    assert fact is not None and fact.id == issuer.series("q3_2026"), (
        "внутри окна отчёт ещё впереди")
    assert calendar.next_fact(date(2026, 11, 4), path=path).id == issuer.series("q4_2026_ops")
    ids = [e.id for e in calendar.upcoming(date(2026, 10, 30), limit=3, path=path)]
    assert ids[0] == issuer.series("q3_2026")


_FIXTURES = __import__("pathlib").Path(__file__).parent / "fixtures"
