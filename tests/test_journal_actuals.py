# -*- coding: utf-8 -*-
"""Журнал прогнозов: день публикации факта можно исправить (внешний аудит, T12).

`record_actual` молча возвращал прежнюю строку, если число совпало: исправить
день публикации при том же числе было нельзя, а от него меряются горизонты
зачёта (90 / 45 / 15 дней); строки журнала не удаляются. Обратный случай — факт
внесён без дня (днём внесения, на несколько дней позже публикации) — сдвигал
момент зачёта вперёд, в пользу более позднего прогноза, и тоже не исправлялся.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from indicators import collect, issuer, periods
from indicators import journal as journal_module
from indicators.journal import Journal

pytestmark = pytest.mark.tact

MARGIN = issuer.series("ebitda_margin_pre16")


def _actual_rows(journal: Journal) -> list[tuple]:
    with sqlite3.connect(journal.path) as db:
        return db.execute("SELECT value, reported_on FROM actuals ORDER BY id").fetchall()


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


# ================================================== T12: день публикации факта


def test_the_publication_day_of_a_fact_can_be_corrected_with_the_same_value(tmp_path):
    journal = Journal(tmp_path / "j.sqlite")
    journal.record_actual(MARGIN, "2026Q3", 0.07, reported_on=date(2026, 10, 29))
    fixed = journal.record_actual(MARGIN, "2026Q3", 0.07, reported_on=date(2026, 10, 30))
    assert fixed.reported_on == date(2026, 10, 30)
    assert journal.actual(MARGIN, "2026Q3").reported_on == date(2026, 10, 30), (
        "день поправлен тем же числом — горизонты зачёта меряются от нового дня")
    # Повтор без дня и повтор с тем же днём — не новые записи.
    assert journal.record_actual(MARGIN, "2026Q3", 0.07).reported_on == date(2026, 10, 30)
    journal.record_actual(MARGIN, "2026Q3", 0.07, reported_on=date(2026, 10, 30))
    assert _actual_rows(journal) == [(0.07, "2026-10-29"), (0.07, "2026-10-30")]
    # Правка числа без дня не сдвигает день публикации на день внесения.
    journal.record_actual(MARGIN, "2026Q3", 0.071)
    assert journal.actual(MARGIN, "2026Q3").reported_on == date(2026, 10, 30)
    assert len(_actual_rows(journal)) == 3


def test_a_corrected_publication_day_moves_the_scored_forecast_and_the_order_of_events(tmp_path):
    """Факт внесли днём внесения (03.11) вместо дня публикации (29.10): момент
    зачёта уезжает на пять дней вперёд, и в зачёт попадает более поздний прогноз.
    Исправление тем же числом возвращает и прогноз, и порядок событий."""
    journal = Journal(tmp_path / "j.sqlite")
    published = date(2026, 10, 29)
    _record_on(journal, published - timedelta(days=70), target="t", period="2026Q3", value=0.060)
    _record_on(journal, published - timedelta(days=43), target="t", period="2026Q3", value=0.069)
    journal.record_actual("t", "2026Q3", 0.070, reported_on=date(2026, 11, 3))
    assert journal.scoreboard("t")[0]["forecast"] == pytest.approx(0.069), "неверный день"
    journal.record_actual("t", "2026Q3", 0.070, reported_on=published)
    row = journal.scoreboard("t")[0]
    assert row["forecast"] == pytest.approx(0.060) and row["reported_on"] == "2026-10-29"

    # Порядок событий — по ПОСЛЕДНЕЙ строке периода, а не по самой ранней дате.
    journal.record_actual("t", "2026Q4", 0.08, reported_on=date(2026, 3, 26))   # опечатка в годе
    assert journal.events("t") == ["2026Q4", "2026Q3"]
    journal.record_actual("t", "2026Q4", 0.08, reported_on=date(2027, 3, 26))
    assert journal.events("t") == ["2026Q3", "2026Q4"]


def test_the_command_without_a_day_keeps_the_recorded_publication_day(tmp_path, monkeypatch,
                                                                    capsys):
    """`record-actual` без `--reported-on`: первая запись — сегодня, повтор и правка
    числа день не трогают; исправление дня тем же числом печатает новый день."""
    path = tmp_path / "journal.sqlite"
    monkeypatch.setattr(journal_module, "DEFAULT_PATH", path)
    quarter = periods.FIRST_FORECAST_QUARTER
    today = datetime.now(timezone.utc).date()
    earlier = (today - timedelta(days=3)).isoformat()
    base = ["record-actual", "--target", MARGIN, "--period", quarter]
    assert collect.main(base + ["--value", "0.0715", "--reported-on", earlier]) == 0
    assert collect.main(base + ["--value", "0.0715"]) == 0          # повтор без дня
    assert collect.main(base + ["--value", "0.0716"]) == 0          # правка числа без дня
    printed = [line for line in capsys.readouterr().out.splitlines() if line.startswith("факт ")]
    assert len(printed) == 3 and all(f"(опубликован {earlier})" in line for line in printed)
    journal = Journal(path)
    assert journal.actual(MARGIN, quarter).reported_on.isoformat() == earlier
    assert [r[1] for r in _actual_rows(journal)] == [earlier, earlier]
    fixed = (today - timedelta(days=2)).isoformat()
    assert collect.main(base + ["--value", "0.0716", "--reported-on", fixed]) == 0
    assert f"(опубликован {fixed})" in capsys.readouterr().out
    assert journal.actual(MARGIN, quarter).reported_on.isoformat() == fixed
