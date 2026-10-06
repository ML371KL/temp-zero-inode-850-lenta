# -*- coding: utf-8 -*-
"""Календарь допуска выпуска против планировщика нау-каста — посуточная симуляция.

Внешний аудит 30.09.2026, T02: календарь допуска (`payload._admission_calendar`)
печатал «решение не раньше 2029Q3», а планировщик не давал зачётного прогноза
1 кв., и восьмое засчитанное событие приходилось на 2030Q2. Календарь и журнал
считали по разным правилам, и сверить их было нечем. Здесь они сверяются
напрямую: планировщик гоняется по дням до 2030 г. на настоящем журнале.

Тест долгий (≈20 с) — без метки такта: идёт в CI и в полном прогоне.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from indicators import issuer, periods
from indicators import journal as journal_module
from indicators.journal import (MODEL_EXPECTATION, SCORE_AFTER_PREVIOUS_FACT, SCORE_AT_HORIZON,
                                Journal, forecast_period, naive_margin, report_date)
from indicators.nowcast import MARGIN_VERSION

MARGIN = issuer.series("ebitda_margin_pre16")


class _Clock(datetime):
    """Часы журнала: `made_at` и `recorded_at` — день симуляции."""

    day = date(2027, 1, 1)

    @classmethod
    def now(cls, tz=None):
        return datetime(cls.day.year, cls.day.month, cls.day.day, 14, 25, tzinfo=tz or timezone.utc)


def _true_margin(quarter: str) -> float:
    number = periods.parse(quarter).number
    return 0.066 + {1: -0.008, 2: 0.006, 3: -0.001, 4: 0.004}[number]


@pytest.mark.needs_book
def test_the_admission_calendar_is_what_the_planner_will_really_score(tmp_path, monkeypatch):
    """Календарь допуска выпуска против посуточной симуляции планировщика до 2030 г.
    на настоящем журнале (`forecast_period`, записи прогноза и эталонов, факты — в
    центральные даты календаря): первые восемь засчитанных событий табло — те же
    кварталы, что в календаре, и восьмое — квартал «решение не раньше». Кварталы
    позднего факта идут в зачёт на тех горизонтах, которые календарь называет."""
    from model.payload import _admission_calendar

    monkeypatch.setattr(journal_module, "datetime", _Clock)
    journal = Journal(tmp_path / "journal.sqlite")
    start = date(2026, 9, 30)
    known = {q: _true_margin(q) for q in ("2025Q1", "2025Q2", "2025Q3", "2025Q4", "2026Q1",
                                           "2026Q2")}
    schedule, quarter = {}, periods.FIRST_FORECAST_QUARTER
    while periods.index(quarter) <= periods.index("2030Q1"):
        schedule[report_date(quarter)] = quarter
        quarter = periods.next_period(quarter)

    day = start
    while day <= date(2030, 1, 31):
        _Clock.day = day
        if day in schedule:
            reported = schedule[day]
            journal.record_actual(MARGIN, reported, _true_margin(reported), reported_on=day)
            known[reported] = _true_margin(reported)
        if day.weekday() < 5:                        # суточный такт — по будням
            period = forecast_period(journal, MARGIN, today=day)
            journal.record(target=MARGIN, period=period, value=_true_margin(period) + 0.001,
                           std_error=0.01, equation="sim", version=MARGIN_VERSION,
                           inputs={"q": period}, today=day)
            held = journal.naive(MARGIN, period)
            journal.record_naive(MARGIN, period, {
                **{m: v for m, v in naive_margin(known, period).items() if m not in held},
                MODEL_EXPECTATION: _true_margin(period)})
        day += timedelta(days=1)

    board = journal.scoreboard(MARGIN)
    counted = [r for r in board if r["forecast"] is not None and not r["admission_excluded"]]
    plan = _admission_calendar(periods.FIRST_FORECAST_QUARTER, 8)
    assert [r["period"] for r in counted[:8]] == plan["countable"]
    assert plan["first_countable"] == "2027Q4" and plan["earliest_decision"] == "2029Q3"
    assert plan["earliest_decision"] == counted[7]["period"]
    assert all("yoy_plus_shift" in {k[len("naive_"):] for k in r if k.startswith("naive_")}
               for r in counted[:8]), "главный эталон есть у каждого засчитанного события"
    short = {item["quarter"]: item["days"] for item in plan["short_horizon_ahead"]}
    assert set(short) == {"2028Q1", "2029Q1"}
    for row in counted[:8]:
        if row["period"] in short:
            assert row["scoring_rule"] == SCORE_AFTER_PREVIOUS_FACT
            # Факт внесён в день отчёта; первый будний такт — в тот же день или после выходных.
            assert 0 <= short[row["period"]] - row["scoring_horizon_days"] <= 2, row["period"]
        else:
            assert row["scoring_rule"] == SCORE_AT_HORIZON and row["scoring_horizon_days"] == 45
    assert journal.admission(MARGIN, version=MARGIN_VERSION).events >= 8
