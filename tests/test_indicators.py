"""Слой опережающих индикаторов: хранилище винтажей, журнал, нау-каст, каналы.

Тесты, которым нужна сеть, помечены `network` и пропускаются по умолчанию:
CI не должен зависеть от доступности чужих сайтов. Запуск с сетью —
`pytest -m network`.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from indicators import issuer

from indicators.journal import (
    Journal,
    JournalError,
    current_quarter,
    forecast_period,
    report_date,
    report_deadline,
)
from indicators.nowcast import apply_to_book, margin_nowcast
from indicators.sources import COLLECTORS, DAILY, WEEKLY
from indicators.store import Point, Series, Store

# Такт (ops/run.sh, TACT_TESTS): слой индикаторов на фикстурах и двойниках —
# быстрый, в сеть не ходит (тесты `network` такт исключает выражением).
pytestmark = pytest.mark.tact


@pytest.fixture
def store(tmp_path) -> Store:
    return Store(tmp_path / "indicators")


# ------------------------------------------------------------ хранилище


def test_raw_archive_is_immutable(store):
    """Сырой ответ не перезаписывается: если источник в тот же день ответил
    иначе, рядом ложится вторая копия. Затирание — потеря винтажа."""
    a = store.save_raw("src", "x.json", b"first", url="u", fetched_at="t", sha256="a", day="2026-09-20")
    b = store.save_raw("src", "x.json", b"second", url="u", fetched_at="t", sha256="b", day="2026-09-20")
    assert a != b
    assert a.read_bytes() == b"first"
    assert b.read_bytes() == b"second"


def test_identical_body_is_not_duplicated(store):
    a = store.save_raw("src", "x.json", b"same", url="u", fetched_at="t", sha256="a", day="2026-09-20")
    b = store.save_raw("src", "x.json", b"same", url="u", fetched_at="t2", sha256="a", day="2026-09-20")
    assert a == b


def test_raw_meta_records_provenance(store):
    path = store.save_raw("src", "x.json", b"{}", url="https://example/x",
                          fetched_at="2026-09-20T10:00:00+00:00", sha256="deadbeef")
    meta = json.loads(path.with_suffix(path.suffix + ".meta.json").read_text(encoding="utf-8"))
    assert meta["url"] == "https://example/x"
    assert meta["sha256"] == "deadbeef"
    assert meta["fetched_at"].startswith("2026-09-20")


def test_value_as_of_answers_what_was_known(store):
    """Главный вопрос PIT-слоя. Источник пересмотрел значение задним числом —
    бэктест обязан видеть то, что было известно НА ДАТУ, а не сегодняшнее."""
    store.upsert("s", [
        Point(period="2026-06-30", value=1.0, fetched_at="2026-07-01T00:00:00+00:00"),
        Point(period="2026-06-30", value=1.5, fetched_at="2026-09-01T00:00:00+00:00"),
    ], unit="", cadence="месяц", label="s")
    series = store.load("s")
    assert series.value_as_of("2026-06-30", "2026-08-01") == 1.0
    assert series.value_as_of("2026-06-30", "2026-09-15") == 1.5
    assert series.value_as_of("2026-06-30", "2026-06-15") is None


def test_revision_does_not_erase_the_earlier_version(store):
    store.upsert("s", [Point("2026-06-30", 1.0, "2026-07-01T00:00:00+00:00")])
    store.upsert("s", [Point("2026-06-30", 1.5, "2026-09-01T00:00:00+00:00")])
    series = store.load("s")
    assert len(series.points) == 2


def test_failed_fetch_is_not_missing_data(store):
    """`null ≠ 0` и здесь: «не смогли взять» и «данных нет» — разные статусы."""
    store.upsert("s", [
        Point("2026-06-30", None, "2026-07-01T00:00:00+00:00", status="failed"),
        Point("2026-07-31", None, "2026-08-01T00:00:00+00:00", status="missing"),
        Point("2026-08-31", 2.0, "2026-09-01T00:00:00+00:00"),
    ])
    series = store.load("s")
    assert series.latest().period == "2026-08-31"
    assert series.history() == {"2026-08-31": 2.0}


def test_prune_keeps_the_budget(store):
    """Дни считаются ОТ СЕГОДНЯ, поэтому «свежий» день берётся от текущей даты.

    Раньше здесь стояло 2026-09-20 литералом: 21.10.2026 он вышел бы за
    keep_days=30, «свежий» день удалился бы вместе со старыми, и CI покраснел
    бы сам по себе — без единой правки кода.
    """
    today = date.today()
    fresh = today.isoformat()
    for day in ("2020-01-01", "2020-01-02", fresh):
        store.save_raw("src", "x.bin", day.encode() * 100, url="u", fetched_at="t",
                       sha256=day, day=day)
    removed = store.prune_raw(keep_days=30)
    assert any("2020-01-01" in r for r in removed)
    assert store.raw_days("src") == [fresh]


def test_prune_never_deletes_fresh_days_when_protected_sources_fill_the_ceiling(tmp_path,
                                                                                monkeypatch):
    """Аудит 26.09.2026, п. 5.7 (проба `prune_sim.py`): потолок сравнивался со
    ВСЕМ архивом, а удалять ротация могла только незащищённые дни. Когда
    защищённые источники сами больше потолка, каждый прогон удалял все дни
    ISS/ЦБ/Росстата, включая сегодняшний. Теперь у незащищённых свой бюджет —
    окно `keep_days`: удаляется только то, что старше него."""
    store = Store(tmp_path)
    today = date(2026, 9, 26)
    for age in (0, 1, 9, 31, 400):
        day = (today - timedelta(days=age)).isoformat()
        for source, size in (("issuer_feed", 10_000), ("moex", 50), ("cbr", 10)):
            store.save_raw(source, "x.bin", b"0" * size, url="u", fetched_at="t",
                           sha256=f"{source}{day}", day=day)
    before = store.raw_size_bytes()
    removed = store.prune_raw(protected=frozenset({"issuer_feed"}), keep_days=30, today=today)
    assert sorted(removed) == [f"{s}/{(today - timedelta(days=a)).isoformat()}"
                               for s in ("cbr", "moex") for a in (400, 31)]
    for source in ("moex", "cbr"):
        assert store.raw_days(source)[-1] == today.isoformat()
        assert len(store.raw_days(source)) == 3
    assert len(store.raw_days("issuer_feed")) == 5
    assert store.raw_size_bytes() < before
    # Повторный прогон не удаляет ничего, сколько бы ни весили защищённые
    # источники: потолок — дело `health`, не ротации.
    assert store.prune_raw(protected=frozenset({"issuer_feed"}), keep_days=30, today=today) == []


def test_unchanged_point_is_not_appended_again(tmp_path):
    """Суточный прогон дописывал весь запрошенный год заново.

    Ряд ключевой ставки набрал 7 559 точек на 3 262 календарных дня и рос на
    365 точек в сутки: ключом различения был момент загрузки, а он новый
    каждый раз.
    """
    store = Store(tmp_path)
    day = "2026-09-21"
    for hour in range(3):
        store.upsert("t", [Point(period=day, value=0.14,
                                 fetched_at=f"2026-09-2{1 + hour}T00:00:00+00:00")],
                     channel=1, label="t")
    assert len(store.load("t").points) == 1


def test_a_revision_is_kept_even_if_it_returns_to_the_old_value(tmp_path):
    """A→B→A остаётся тремя точками.

    Источник, исправивший значение и вернувший прежнее, сообщает этим ровно
    столько же, сколько любая другая правка. Сравнение со всеми виденными
    значениями (а не с последним) такую правку потеряло бы.
    """
    store = Store(tmp_path)
    day = "2026-09-21"
    for index, value in enumerate((0.14, 0.15, 0.14)):
        store.upsert("t", [Point(period=day, value=value,
                                 fetched_at=f"2026-09-2{1 + index}T00:00:00+00:00")],
                     channel=1, label="t")
    points = store.load("t").points
    assert [p.value for p in points] == [0.14, 0.15, 0.14]
    assert store.load("t").latest().value == 0.14


def test_compact_removes_only_consecutive_repeats(tmp_path):
    """Уборка наследства прежнего правила не трогает правки источника."""
    store = Store(tmp_path)
    series = store.upsert("t", [
        Point(period="2026-09-01", value=0.14, fetched_at="2026-09-01T00:00:00+00:00"),
    ], channel=1, label="t")
    # Точки с повторами кладём мимо upsert — так их наплодило старое правило.
    series.points += [
        Point(period="2026-09-01", value=0.14, fetched_at="2026-09-02T00:00:00+00:00"),
        Point(period="2026-09-01", value=0.15, fetched_at="2026-09-03T00:00:00+00:00"),
        Point(period="2026-09-01", value=0.15, fetched_at="2026-09-04T00:00:00+00:00"),
        Point(period="2026-09-01", value=0.14, fetched_at="2026-09-05T00:00:00+00:00"),
    ]
    store.save(series)
    assert store.compact("t") == (5, 3)
    assert [p.value for p in store.load("t").points] == [0.14, 0.15, 0.14]


# --------------------------------------------------------------- журнал

# «Сегодня» сценариев журнала: день ДО отчёта за 3 кв. 2026. Запись прогноза
# сверяет крайний день окна отчёта с «сегодня», и без явного дня тесты ниже
# с ноября 2026 падали бы на «окно отчёта закрылось» — то есть проверяли бы
# календарь прогона, а не журнал (аудит 850oa 24.09.2026, G1-exam §1.4).
BEFORE_THE_REPORT = date(2026, 9, 24)
QUARTER = "2026Q3"
PUBLISHED = date(2026, 10, 29)
"""День публикации отчёта за 3 кв. 2026 в сценариях (центр окна календаря)."""


def test_journal_is_append_only(tmp_path):
    journal = Journal(tmp_path / "j.sqlite")
    journal.record(target="t", period=QUARTER, value=0.068, equation="eq",
                   version="v1", inputs={"a": 1}, today=BEFORE_THE_REPORT)
    journal.record(target="t", period=QUARTER, value=0.070, equation="eq",
                   version="v1", inputs={"a": 2}, today=BEFORE_THE_REPORT)
    rows = journal.forecasts("t")
    assert len(rows) == 2
    assert rows[0].value == 0.068 and rows[1].value == 0.070


def test_unchanged_forecast_is_not_written_again(tmp_path):
    """Суточный прогон при неизменившихся входах не порождает записей: дубль не
    добавляет знания, но превращает одно событие табло в пять (аудит 850oa)."""
    journal = Journal(tmp_path / "j.sqlite")
    first = journal.record(target="t", period=QUARTER, value=0.068, std_error=0.01,
                           equation="eq", version="v1", inputs={"day": 1},
                           today=BEFORE_THE_REPORT)
    for day in range(2, 6):
        again = journal.record(target="t", period=QUARTER, value=0.068, std_error=0.01,
                               equation="eq", version="v1", inputs={"day": day},
                               today=BEFORE_THE_REPORT)
        assert again.recorded is False
        assert again.made_at == first.made_at
    assert len(journal.forecasts("t")) == 1

    # Движение, тонущее в собственной ошибке, — тот же прогноз...
    assert journal.record(target="t", period=QUARTER, value=0.068 + 1e-6, std_error=0.01,
                          equation="eq", version="v1", inputs={},
                          today=BEFORE_THE_REPORT).recorded is False
    # ...а движение крупнее — новый.
    assert journal.record(target="t", period=QUARTER, value=0.0695, std_error=0.01,
                          equation="eq", version="v1", inputs={},
                          today=BEFORE_THE_REPORT).recorded is True
    assert len(journal.forecasts("t")) == 2


def test_a_different_equation_keeps_its_own_line(tmp_path):
    """Дедупликация не должна склеивать разные уравнения в одно."""
    journal = Journal(tmp_path / "j.sqlite")
    journal.record(target="t", period=QUARTER, value=0.068, equation="eq-a",
                   version="v1", inputs={}, today=BEFORE_THE_REPORT)
    second = journal.record(target="t", period=QUARTER, value=0.068, equation="eq-b",
                            version="v1", inputs={}, today=BEFORE_THE_REPORT)
    assert second.recorded is True
    assert len(journal.forecasts("t")) == 2


def test_journal_hashes_the_inputs(tmp_path):
    """Версия = версия СПЕЦИФИКАЦИИ уравнения; входы фиксируются хэшем."""
    journal = Journal(tmp_path / "j.sqlite")
    a = journal.record(target="t", period=QUARTER, value=0.068, equation="eq",
                       version="lenta-margin-v1", inputs={"x": 1}, today=BEFORE_THE_REPORT)
    b = journal.record(target="t", period=QUARTER, value=0.069, equation="eq",
                       version="lenta-margin-v1", inputs={"x": 2}, today=BEFORE_THE_REPORT)
    assert a.inputs_sha != b.inputs_sha
    assert a.version == b.version == "lenta-margin-v1"


def test_records_cannot_be_edited_or_deleted(tmp_path):
    """«Переписать нельзя» — запрет базы (триггеры), а не обещание докстроки."""
    import sqlite3

    journal = Journal(tmp_path / "j.sqlite")
    journal.record(target="t", period=QUARTER, value=0.068, equation="eq",
                   version="v1", inputs={}, today=BEFORE_THE_REPORT)
    journal.record_naive("t", QUARTER, {"seasonal_naive": 0.07})
    journal.record_actual("t", QUARTER, 0.067, reported_on=PUBLISHED)

    with sqlite3.connect(journal.path) as db:
        for statement in ("UPDATE forecasts SET value = 0.047",
                          "DELETE FROM forecasts",
                          "UPDATE actuals SET value = 0.048",
                          "DELETE FROM actuals",
                          "UPDATE naive SET value = 0.9",
                          "DELETE FROM naive"):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(statement)
    assert len(journal.forecasts("t")) == 1
    assert journal.actual("t", QUARTER).value == 0.067


def test_forecast_after_the_fact_is_refused(tmp_path):
    """Прогноз, записанный после выхода факта, — не прогноз."""
    journal = Journal(tmp_path / "j.sqlite")
    journal.record_actual("t", QUARTER, 0.067, reported_on=PUBLISHED)
    with pytest.raises(JournalError, match="факт уже внесён"):
        journal.record(target="t", period=QUARTER, value=0.0671, equation="eq",
                       version="v1", inputs={}, today=BEFORE_THE_REPORT)


def test_forecast_after_the_report_window_is_refused(tmp_path):
    """Окно отчёта закрылось, факт не внесли — дальше писать прогнозы нечестно.

    Сторож смотрит на КОНЕЦ окна (`report_deadline`), а не на центр: релиз в
    последний день окна не объявляется пропуском за несколько дней до выхода.
    """
    journal = Journal(tmp_path / "j.sqlite")
    deadline = report_deadline(QUARTER)
    assert report_date(QUARTER) <= deadline
    journal.record(target="t", period=QUARTER, value=0.068, equation="eq",
                   version="v1", inputs={}, today=deadline)
    with pytest.raises(JournalError, match="окно отчёта закрылось"):
        journal.record(target="t", period=QUARTER, value=0.069, equation="eq",
                       version="v1", inputs={}, today=deadline + timedelta(days=1))


def test_the_fourth_quarter_has_two_report_windows(tmp_path):
    """У 4 кв. два события (D2): выручка — операционными (февраль), маржа —
    годовым МСФО (март). Окно выручки закрывается раньше окна маржи."""
    revenue_deadline = report_deadline("2026Q4", "revenue")
    margin_deadline = report_deadline("2026Q4", "margin")
    assert revenue_deadline < margin_deadline
    assert report_deadline("2026Q4", issuer.series("revenue_pre16")) == revenue_deadline
    journal = Journal(tmp_path / "j.sqlite")
    day = revenue_deadline + timedelta(days=1)
    journal.record(target=issuer.series("ebitda_margin_pre16"), period="2026Q4",
                   value=0.075, equation="eq", version="v1", inputs={}, today=day)
    with pytest.raises(JournalError, match="окно отчёта закрылось"):
        journal.record(target=issuer.series("revenue_pre16"), period="2026Q4",
                       value=400.0, equation="eq", version="v1", inputs={}, today=day)


def test_errors_are_measured_at_fixed_horizons(tmp_path):
    """Ошибка считается на 90 / 45 / 15 днях до отчёта, а не «в среднем».

    Прогноз накануне отчёта — не прогноз: к этому дню известны отчёты аналогов.
    Разные горизонты обязаны показывать РАЗНЫЕ числа.
    """
    journal = Journal(tmp_path / "j.sqlite")
    path = {120: 0.064, 60: 0.066, 30: 0.069, 5: 0.0675}
    for days, value in path.items():
        _record_on(journal, PUBLISHED - timedelta(days=days), target="t", period=QUARTER,
                   value=value)
    journal.record_actual("t", QUARTER, 0.067, reported_on=PUBLISHED)

    horizons = journal.horizons("t", QUARTER)
    assert horizons[90]["value"] == pytest.approx(0.064)
    assert horizons[45]["value"] == pytest.approx(0.066)
    assert horizons[15]["value"] == pytest.approx(0.069)
    assert horizons[45]["error"] == pytest.approx(0.066 - 0.067)


def test_horizon_without_a_forecast_is_not_credited(tmp_path):
    """Приписать себе прогноз, которого не делал, нельзя."""
    journal = Journal(tmp_path / "j.sqlite")
    _record_on(journal, PUBLISHED - timedelta(days=10), target="t", period=QUARTER,
               value=0.0669)
    journal.record_naive("t", QUARTER, {"seasonal_naive": 0.07})
    journal.record_actual("t", QUARTER, 0.067, reported_on=PUBLISHED)

    row = journal.scoreboard("t")[0]
    assert journal.horizons("t", QUARTER)[45] is None
    assert row["forecast"] is None and row["beats_benchmark"] is None
    # ...но «последнее слово» всё равно печатается как справка.
    assert row["last_word"]["value"] == pytest.approx(0.0669)


def test_scoreboard_has_one_row_per_event(tmp_path):
    """Семь суточных записей об одном квартале — одно событие, а не семь."""
    journal = Journal(tmp_path / "j.sqlite")
    for days in (100, 80, 60, 50, 46, 20, 3):
        _record_on(journal, PUBLISHED - timedelta(days=days), target="t", period=QUARTER,
                   value=0.0665 + days * 1e-5)
    _record_on(journal, PUBLISHED - timedelta(days=100), target="t", period=QUARTER,
               naive={"seasonal_naive": 0.070})
    journal.record_actual("t", QUARTER, 0.067, reported_on=PUBLISHED)

    board = journal.scoreboard("t")
    assert len(board) == 1, "семь записей об одном отчёте — одно событие"
    assert board[0]["scoring_horizon_days"] == 45
    assert board[0]["beats_benchmark"] is True
    assert journal.demoted("t", min_events=4) is False, "одно событие — не четыре"


def test_demotion_counts_events_not_rows(tmp_path):
    """Уравнение, проигравшее эталону на четырёх ОТЧЁТАХ, — справочное."""
    journal = Journal(tmp_path / "j.sqlite")
    for index, period in enumerate(("2026Q3", "2026Q4", "2027Q1", "2027Q2")):
        published = report_date(period)
        for days in (100, 50, 20):  # три записи на каждое событие
            _record_on(journal, published - timedelta(days=days), target="t",
                       period=period, value=0.10 + index * 1e-4)
        _record_on(journal, published - timedelta(days=100), target="t", period=period,
                   naive={"seasonal_naive": 0.051})
        journal.record_actual("t", period, 0.05, reported_on=published)

    board = journal.scoreboard("t")
    assert len(board) == 4, "четыре отчёта — четыре события, а не двенадцать"
    assert all(r["beats_benchmark"] is False for r in board)
    assert journal.demoted("t", min_events=4) is True


def test_actual_is_recorded_with_the_day_it_was_published(tmp_path):
    """Горизонты меряются от публикации, а не от дня внесения в журнал."""
    journal = Journal(tmp_path / "j.sqlite")
    _record_on(journal, PUBLISHED - timedelta(days=45), target="t", period=QUARTER,
               value=0.066)
    journal.record_actual("t", QUARTER, 0.067, reported_on=PUBLISHED,
                          source="Лента, 3 кв. 2026")
    assert journal.actual("t", QUARTER).reported_on == PUBLISHED
    assert journal.horizons("t", QUARTER)[45]["value"] == pytest.approx(0.066)


def test_release_carries_only_recent_records(tmp_path):
    """Журнал растёт вечно, выпуск — нет."""
    journal = Journal(tmp_path / "j.sqlite")
    for i in range(30):
        journal.record(target="t", period=QUARTER, value=0.04 + i * 1e-3,
                       equation="eq", version="v1", inputs={}, today=BEFORE_THE_REPORT)
    assert len(journal.forecasts("t")) == 30
    assert len(journal.recent(per_target=8)) == 8


def test_journal_v1_database_is_migrated(tmp_path):
    """Старый журнал (схема 850oa v1) не выбрасывается и не ломает прогон."""
    import sqlite3

    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE forecasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, made_at TEXT NOT NULL,
                target TEXT NOT NULL, period TEXT NOT NULL, value REAL NOT NULL,
                std_error REAL, equation TEXT NOT NULL, version TEXT NOT NULL,
                inputs_sha TEXT NOT NULL, inputs TEXT NOT NULL, note TEXT DEFAULT '');
            CREATE TABLE actuals (
                target TEXT NOT NULL, period TEXT NOT NULL, value REAL NOT NULL,
                recorded_at TEXT NOT NULL, source TEXT DEFAULT '',
                PRIMARY KEY (target, period));
            CREATE TABLE naive (
                target TEXT NOT NULL, period TEXT NOT NULL, method TEXT NOT NULL,
                value REAL NOT NULL, made_at TEXT NOT NULL,
                PRIMARY KEY (target, period, method));
        """)
        db.execute("INSERT INTO forecasts (made_at, target, period, value, equation, version,"
                   " inputs_sha, inputs) VALUES ('2026-09-01T00:00:00+00:00','t','2026FY',"
                   "0.048,'eq','v1','abc','{}')")
        db.execute("INSERT INTO actuals VALUES ('t','2026FY',0.047,"
                   "'2027-05-07T00:00:00+00:00','МСФО')")
        db.execute("INSERT INTO naive VALUES ('t','2026FY','seasonal_naive',0.05,"
                   "'2026-09-01T00:00:00+00:00')")

    journal = Journal(path)
    assert len(journal.forecasts("t")) == 1
    actual = journal.actual("t", "2026FY")
    assert actual.value == 0.047
    assert actual.reported_on == date(2027, 5, 7)
    assert journal.naive("t", "2026FY") == {"seasonal_naive": 0.05}
    with pytest.raises(JournalError):
        journal.record(target="t", period="2026FY", value=0.049, equation="eq",
                       version="v1", inputs={})


def _record_on(journal, made, *, target, period, value=None, naive=None):
    """Запись задним числом — только для тестов: подменяет `made_at`.

    Триггеры запрещают UPDATE, поэтому время подставляется в момент вставки.
    """
    import sqlite3

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


# ------------------------------------------------------- период прогноза


def test_the_calendar_quarter_is_not_the_forecast_period(tmp_path):
    """Период закрывает ФАКТ, а не календарь (A5а 850oa, у Ленты — кварталы).

    С 1 октября календарный квартал — четвёртый, но отчёт за третий выходит в
    конце октября: прогноз до него идёт на 3 кв. Сверху — календарный квартал.
    """
    assert current_quarter(date(2026, 9, 21)) == "2026Q3"
    assert current_quarter(date(2026, 10, 1)) == "2026Q4"
    assert current_quarter(date(2027, 1, 2)) == "2027Q1"

    journal = Journal(tmp_path / "j.sqlite")
    assert forecast_period(journal, "t", today=date(2026, 9, 20)) == "2026Q3"
    assert forecast_period(journal, "t", today=date(2026, 10, 15)) == "2026Q3"
    journal.record_actual("t", "2026Q3", 0.07, reported_on=date(2026, 10, 29))
    assert forecast_period(journal, "t", today=date(2026, 10, 30)) == "2026Q4"
    journal.record_actual("t", "2026Q4", 0.08, reported_on=date(2027, 3, 26))
    assert forecast_period(journal, "t", today=date(2026, 12, 1)) == "2026Q4", (
        "за квартал, который ещё не начался, слой не забегает")
    assert forecast_period(journal, "t", today=date(2027, 2, 1)) == "2027Q1"


# -------------------------------------------------------------- нау-каст


def test_nowcast_equals_the_model_expectation_and_the_deviation_is_zero(tmp_path):
    """lenta-margin-v1 = ожидание модели на квартал; без индикаторов отклонение — 0.

    Ожидание считает ядро (`model.quarters.expected_quarter`) на двойнике сетки
    из одной клетки: маржа полугодия 7,0 % + поправка 3 кв. книги. Модальной
    цели у квартального нау-каста нет.
    """
    from types import SimpleNamespace

    from indicators.quarterly import expected_quarter

    A = _toy_book()
    grid = [SimpleNamespace(probability=1.0, result=SimpleNamespace(rows=[
        SimpleNamespace(period="2026H2", revenue=740.0, margin=0.070)]))]
    nowcast = margin_nowcast(Store(tmp_path), A, "2026Q3",
                             expectation=lambda A_, q: expected_quarter(A_, q, grid=grid))
    offset = A["margin"]["quarter_offset_pp"]["Q3"]
    share = A["revenue"]["quarter_share"]
    assert nowcast.value == pytest.approx(0.070 + offset)
    assert nowcast.expectation == pytest.approx(nowcast.value)
    assert nowcast.deviation == pytest.approx(0.0, abs=1e-15)
    assert nowcast.half == "2026H2" and nowcast.half_value == pytest.approx(0.070)
    assert nowcast.revenue == pytest.approx(740.0 * share["Q3"] / (share["Q3"] + share["Q4"]))
    assert nowcast.source == "model.quarters.expected_quarter"
    assert nowcast.version == "lenta-margin-v1"
    assert nowcast.connected_to_price is False
    assert nowcast.modal_target is None and nowcast.modal_regime is None
    with pytest.raises(ValueError):
        margin_nowcast(Store(tmp_path), A, "2026H2")


def test_the_quarter_error_is_wider_than_the_half_year_error():
    """σ квартала = σ полугодия A-P2u · √2, если книга не задала своего ключа."""
    from indicators.quarterly import quarter_se

    A = _toy_book()
    sigma = A["joint"]["regime_update"]["sigma_pp"]
    assert quarter_se(A) == pytest.approx(sigma * 2 ** 0.5)
    A["margin"]["quarter_sigma_pp"] = 0.009
    assert quarter_se(A) == pytest.approx(0.009)


def test_nowcast_enters_valuation_only_through_the_rule_and_only_as_a_half(monkeypatch):
    """Путь (в): в правило A-P2u подаётся ПОЛУГОДИЕ квартала со своей ошибкой (D2).

    Книга на месте не правится; прогноз ДОПОЛНЯЕТ внесённые наблюдения.
    Правило ядра (`model.grid.with_observation`) подменено двойником: проверяется
    ЧТО подаётся, а не арифметика A-P2u.
    """
    import copy

    import model.grid as grid
    from indicators.nowcast import MarginNowcast

    def fake_with_observation(book_, period, observation):
        trial = copy.deepcopy(book_)
        trial["joint"]["regime_update"]["observations"][period] = observation
        return trial

    monkeypatch.setattr(grid, "with_observation", fake_with_observation)
    A = {"joint": {"regime_update": {"observations": {"2026H1": 0.0606}}}}
    forecast = MarginNowcast("2026Q3", 0.069, 0.0099, "eq", "v", {}, {},
                             expectation=0.069, half="2026H2", half_value=0.0695,
                             half_std_error=0.007)
    trial = apply_to_book(A, forecast)
    assert trial["joint"]["regime_update"]["observations"] == {
        "2026H1": 0.0606, "2026H2": {"value": 0.0695, "se": 0.007}}
    assert A["joint"]["regime_update"]["observations"] == {"2026H1": 0.0606}


def _toy_book() -> dict:
    """Ключи книги, которые читает квартальный слой, в форме ядра: доли книги 1.0
    внутри полугодия (пары дают 1), поправки 2 и 4 кв. — из тождества
    Σ доля·поправка = 0 (`tests/test_quarterly_layer.py::toy_book`)."""
    from tests.test_quarterly_layer import toy_book

    return toy_book()


@pytest.mark.needs_book
def test_the_expected_quarter_rests_on_the_half_year_expectation_of_the_core():
    """На книге 1.0: ожидание квартала = ожидание полугодия ядра + поправка квартала,
    и взвешенная выручкой маржа двух кварталов полугодия равна марже полугодия
    (доли — внутри полугодия; тождество Σ доля·поправка = 0 ядро держит до
    1e-12). Тот же тест без книги — на
    синтетической книге, `tests/test_quarterly_layer.py`."""
    from indicators.nowcast import model_expectation
    from indicators.quarterly import expected_quarter
    from model.book import book

    A = book()
    q3, q4 = expected_quarter(A, "2026Q3"), expected_quarter(A, "2026Q4")
    half = model_expectation(A, "2026H2")[0]
    assert q3.half_margin == pytest.approx(half, abs=1e-12)
    weighted = (q3.revenue * q3.margin + q4.revenue * q4.margin) / (q3.revenue + q4.revenue)
    assert weighted == pytest.approx(half, abs=1e-12)


# ------------------------------- канал 1: сверка на фактических процентах


# ------------------------------------------------------- реестр каналов


def test_every_collector_declares_its_channel():
    for name, (_, cadence, channel) in COLLECTORS.items():
        assert channel in (1, 2, 3, 7), name
        assert cadence in ("ежедневно", "еженедельно"), name


def test_daily_and_weekly_do_not_overlap():
    assert not set(DAILY) & set(WEEKLY)
    # Каждый сборщик реестра стоит в расписании: выпавший из него молча
    # перестал бы собирать, и этого никто бы не заметил.
    assert set(DAILY) | set(WEEKLY) == set(COLLECTORS)


# ------------------------------------------------------------ с сетью


@pytest.mark.network
def test_live_collection_of_daily_channels(tmp_path):
    from indicators.sources import run_collectors

    store = Store(tmp_path / "live")
    report = run_collectors(DAILY, store)
    failed = {k: v for k, v in report.items() if v.startswith("ОШИБКА")}
    assert not failed, failed
    assert len(store.all_series()) > 20


# ------------------------------------------------- предохранители РП0 (итерация 2)


def test_save_raw_is_idempotent_across_runs(store):
    """Повторный прогон не должен добавлять НИ ОДНОГО файла.

    Два разных ответа на одно имя за день — законная ситуация (источник
    переписал файл между запросами), и второй ложится копией с номером. Но при
    повторе прогона хранилище обязано узнать оба тела среди уже лежащих. Иначе
    каждый перезапуск добавляет копию, и архив растёт от самого факта
    перезапуска — поймано на переносе архива 850cl.
    """
    def run():
        store.save_raw("s", "x.json", b"first", url="u", fetched_at="t1", sha256="a", day="2026-09-05")
        store.save_raw("s", "x.json", b"second", url="u", fetched_at="t2", sha256="b", day="2026-09-05")

    run()
    files = sorted(p.name for p in (store.raw / "s" / "2026-09-05").iterdir())
    run()
    run()
    assert sorted(p.name for p in (store.raw / "s" / "2026-09-05").iterdir()) == files
    assert len([f for f in files if not f.endswith(".meta.json")]) == 2


def test_large_raw_bodies_are_stored_compressed(store):
    """Крупные ответы ложатся сжатыми, и это видно по мете и по имени.

    Вакансии дают ~10 МБ в сутки без сжатия при бюджете состояния в 1 ГБ и
    запрете ротации для этого источника: без сжатия бюджет кончился бы за два
    месяца. `sha256` при этом остаётся суммой ИСХОДНОГО ответа — по ней ответ
    опознаётся, а не файл на диске.
    """
    body = b'{"results": []}' + b" " * 40_000
    path = store.save_raw("s", "big.json", body, url="u", fetched_at="t",
                          sha256="deadbeef", day="2026-09-21")
    assert path.name.endswith(".json.gz")
    assert path.stat().st_size < len(body) / 5
    assert store.read_raw(path) == body

    meta = json.loads(path.with_suffix(path.suffix + ".meta.json").read_text(encoding="utf-8"))
    assert meta["stored_encoding"] == "gzip"
    assert meta["sha256"] == "deadbeef"

    small = store.save_raw("s", "small.json", b"{}", url="u", fetched_at="t",
                           sha256="x", day="2026-09-21")
    assert small.name == "small.json", "мелкое сжимать незачем"


# --------------------------- A4(1): интерфейс тревоги между сбором и конвейером


def test_the_irrecoverable_sources_are_real_collectors():
    """Множества дисциплины реестра — имена настоящих сборщиков.

    `IRRECOVERABLE` — интерфейс между слоем сбора и юнитами: по нему юнит
    решает, завершаться ли кодом 1, то есть дойдёт ли тревога до владельца
    через мост `dash-alert`. Опечатка в имени сделала бы список тихо пустым
    (у 850oa отказ источника так остался бы строкой в journalctl, как утром
    22.09.2026). То же для критических, раз-в-сутки и вправе-молчать.
    """
    from indicators.sources import (COLLECTORS, CRITICAL, IRRECOVERABLE, MAY_PARSE_NOTHING,
                                    ONCE_A_DAY, ONCE_A_DAY_REASONS)
    from indicators.store import PROTECTED_SOURCES

    for group in (IRRECOVERABLE, CRITICAL, MAY_PARSE_NOTHING, ONCE_A_DAY, PROTECTED_SOURCES):
        for name in group:
            assert name in COLLECTORS, name
    assert CRITICAL == {"moex_quote", "moex_curve"}
    # У каждого «раз в сутки» — своя названная причина (у Ленты это не
    # невосстановимость, а вежливость и бюджет запросов).
    assert set(ONCE_A_DAY_REASONS) == ONCE_A_DAY
    assert all(len(reason) > 20 for reason in ONCE_A_DAY_REASONS.values())
    # Невосполнимое не ротируется: пропущенный день там не докачать.
    assert IRRECOVERABLE <= PROTECTED_SOURCES
    assert "lenta_databook" in IRRECOVERABLE and "lenta_databook" in PROTECTED_SOURCES
    assert "trudvsem_vacancies" in PROTECTED_SOURCES and "trudvsem_vacancies" in MAY_PARSE_NOTHING


def test_a_failed_collector_says_so_with_the_word_the_unit_looks_for(tmp_path,
                                                                     monkeypatch):
    """Отказ сборщика начинается со слова «ОШИБКА:» — по нему юнит и узнаёт.

    Это вся договорённость между `run_collectors` и юнитом `collect`: юнит
    смотрит отчёт, и невосстановимый источник со строкой «ОШИБКА:» обязан
    дать код возврата 1. Перепиши здесь слово — и мост `dash-alert` не
    сработает ни разу, а тесты юнита останутся зелёными: они проверяют свой
    код, а не наш.

    Проверяются три исхода, которые юнит обязан различать: отказ, «ответ
    получен, но разобрать нечего» и штатный пропуск уже собранного дня.
    Пропуск — НЕ отказ: иначе владельцу приходила бы тревога каждый вечер.
    """
    from indicators import sources
    from indicators.sources import run_collectors

    store = Store(tmp_path)

    def falls(*, store=None):
        raise sources.FetchError("https://issuer.example/api/feed", 503, "service unavailable")

    def parses_nothing(*, store=None):
        return sources.Collected("issuer_feed", 2, {}, [])

    registry = dict(sources.COLLECTORS)
    registry["issuer_feed"] = (falls, "ежедневно", 2)
    monkeypatch.setattr(sources, "COLLECTORS", registry)
    monkeypatch.setattr(sources, "ONCE_A_DAY", frozenset({"issuer_feed"}))
    report = run_collectors(["issuer_feed"], store)
    assert report["issuer_feed"].startswith("ОШИБКА:"), report
    assert report["issuer_feed"].startswith("ОШИБКА: FetchError:"), (
        "в строке назван вид отказа: " + report["issuer_feed"])
    assert "service unavailable" in report["issuer_feed"]

    registry["issuer_feed"] = (parses_nothing, "ежедневно", 2)
    report = run_collectors(["issuer_feed"], store)
    assert report["issuer_feed"].startswith("ОШИБКА:"), report

    # Пропуск собранного дня — не отказ.
    sources.mark_collected_today(store, "issuer_feed", status="ок")
    report = run_collectors(["issuer_feed"], store)
    assert report["issuer_feed"].startswith("пропущен"), report
    assert not report["issuer_feed"].startswith("ОШИБКА:")


def test_a_series_without_a_single_observation_is_an_error(tmp_path, monkeypatch):
    """«ок: рядов 1, точек 0» — самый вредный вид зелёного (аудит итерации 3, C9).

    Проба аудитора `crawl_sim.py`, сценарий 3: ЦБ отвечает мусором, разбор
    отдаёт `{"cbr.key_rate": []}`, отчёт печатает «ок», ставка молча остаётся
    прошлой — и никто об этом не узнаёт, потому что и ряд, и его имя на месте.
    Ряд без точек значит «ответ пришёл, а наблюдений из него не вышло», и это
    отказ сборщика: молчать вправе только те, кто назван в `MAY_PARSE_NOTHING`
    (лента раскрытия в спокойный день).
    """
    from indicators import sources
    from indicators.sources import run_collectors

    store = Store(tmp_path)

    def empty(*, store=None):
        return sources.Collected("cbr", 1, {"cbr.key_rate": []}, [])

    registry = dict(sources.COLLECTORS)
    registry["cbr_key_rate"] = (empty, "ежедневно", 1)
    registry["quiet_feed"] = (
        lambda *, store=None: sources.Collected("quiet_feed", 7, {}, []),
        "ежедневно", 7)
    monkeypatch.setattr(sources, "COLLECTORS", registry)
    monkeypatch.setattr(sources, "MAY_PARSE_NOTHING", frozenset({"quiet_feed"}))

    report = run_collectors(["cbr_key_rate", "quiet_feed"], store)
    assert report["cbr_key_rate"].startswith("ОШИБКА:"), report
    assert "ни одного наблюдения" in report["cbr_key_rate"]
    # Лента в спокойный день молчит законно — тревоги быть не должно.
    assert report["quiet_feed"].startswith("ок"), report


# ---------- S1.7 (б): журнал объёма состояния и прирост МБ/сутки


def test_health_writes_the_size_log_and_prints_the_growth(tmp_path, capsys):
    """«Перемерить прирост после 29.09» обязан делать конвейер, а не человек.

    `health` дописывает в состояние строку «дата;байты» и, когда замеров
    набралось на неделю, печатает прирост МБ/сутки. Проверяется то, что
    увидит человек: строка вывода и файл на диске.
    """
    from datetime import date, timedelta

    from indicators import collect, sources
    from indicators.store import Point, Store

    store = Store(tmp_path)
    # Журнал датируется по UTC (S1.3): с местной датой тест падал бы на
    # ноутбуке с полуночи до трёх ночи по Москве.
    today = date.fromisoformat(sources.UTC_TODAY())
    stamp = f"{today.isoformat()}T03:10:00+00:00"
    for series_id in ("cbr.key_rate", issuer.PRICE_SERIES, "moex.zcyc.10y"):
        store.upsert(series_id, [Point(today.isoformat(), 1.0, stamp)])

    # Восемь дней истории по +2,5 МБ в сутки; сегодняшний замер допишет сам
    # `health`, и он же — 442 МБ (боевое состояние на 22.09.2026).
    log = store.root / collect.STATE_SIZE_LOG
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("".join(
        f"{(today - timedelta(days=age)).isoformat()};{442_000_000 - age * 2_500_000}\n"
        for age in range(8, 0, -1)), encoding="utf-8")
    store.raw_size_bytes = lambda: 442_000_000

    assert collect.cmd_health(store) == 0
    printed = capsys.readouterr().out

    assert "прирост состояния" in printed
    assert "2.50 МБ/сутки" in printed, printed
    assert "за 7 дн" in printed, printed

    rows = [line.split(";") for line in log.read_text(encoding="utf-8").splitlines()]
    assert rows[-1] == [today.isoformat(), "442000000"], "сегодняшний замер дописан"
    assert len(rows) == 9

    # Повторный запуск в тот же день не плодит строк: иначе «прирост за семь
    # дней» считался бы по числу запусков, а не по дням.
    collect.cmd_health(store)
    assert len(log.read_text(encoding="utf-8").splitlines()) == 9


def test_health_prints_no_growth_until_there_is_a_week_of_measurements(tmp_path, capsys):
    """Пока замеров меньше недели, прирост не печатается вовсе.

    Две точки дают «прирост», который скачет от одного крупного ответа, —
    и по нему нельзя решать, хватит ли потолка.
    """
    from datetime import date, timedelta

    from indicators import collect
    from indicators.store import Store

    store = Store(tmp_path)
    today = date.today()
    rows = [(today - timedelta(days=age), 100_000_000) for age in range(3, 0, -1)]
    assert collect.state_growth_line(rows) == ""

    store.raw_size_bytes = lambda: 100_000_000
    collect.cmd_health(store)
    assert "прирост состояния" not in capsys.readouterr().out


def test_a_gap_in_the_log_does_not_turn_two_points_into_a_weekly_growth(tmp_path):
    """Порог «неделя замеров» считается ПО ОКНУ, а не по длине журнала.

    Находка скептика. После простоя такта дольше недели старые строки в
    журнале остаются: порог «хотя бы семь строк» проходил, а в окне
    оказывалось две точки — и `health` печатал «прирост состояния
    500 МБ/сутки за 1 дн (замеров 2)». По этой строке принимается решение,
    на сколько лет хватит потолка.
    """
    from datetime import date, timedelta

    from indicators import collect

    today = date.today()
    # Шесть замеров год назад плюс два свежих: строк восемь, в окне — две.
    rows = [(today - timedelta(days=365 + age), 100_000_000) for age in range(6, 0, -1)]
    rows += [(today - timedelta(days=1), 100_000_000), (today, 600_000_000)]
    assert collect.state_growth_line(rows) == "", (
        "две точки за сутки — не недельный прирост, как бы длинен ни был журнал")

    # А восемь подряд идущих суток — печатаются, как и прежде.
    daily = [(today - timedelta(days=age), 442_000_000 - age * 2_500_000)
             for age in range(7, -1, -1)]
    line = collect.state_growth_line(daily)
    assert "2.50 МБ/сутки" in line and "замеров 8" in line, line


def test_the_size_log_is_dated_by_utc_even_just_after_local_midnight(tmp_path, capsys,
                                                                      monkeypatch):
    """S1.3, случай 1: 01:30 по Москве 23.09 — это ещё 22.09 по UTC.

    Прежний `date.today()` брал местную дату: на ноутбуке ручной `health`
    после полуночи писал завтрашний день, и следующий такт в 03:10 UTC
    ложился в «прошлые» сутки. Часы подменены целиком: UTC-момент — у
    `sources.datetime`, местная дата — у `collect.date`, так что тест видит
    ровно то расхождение, которое бывает на машине с часовым поясом не UTC.
    """
    from datetime import date, datetime, timedelta, timezone

    from indicators import collect, sources
    from indicators.store import Point, Store

    instant = datetime(2026, 9, 22, 22, 30, tzinfo=timezone.utc)
    moscow = timezone(timedelta(hours=3))

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant.astimezone(moscow)

    class LocalDate(date):
        @classmethod
        def today(cls):
            return instant.astimezone(moscow).date()     # 23.09 по Москве

    monkeypatch.setattr(sources, "datetime", Clock)
    monkeypatch.setattr(collect, "date", LocalDate)

    store = Store(tmp_path)
    for series_id in ("cbr.key_rate", issuer.PRICE_SERIES, "moex.zcyc.10y"):
        store.upsert(series_id, [Point("2026-09-22", 1.0, "2026-09-22T16:40:00+00:00")])
    store.raw_size_bytes = lambda: 442_000_000
    assert collect.cmd_health(store) == 0
    assert "возраст   0 дн" in capsys.readouterr().out, "возраст рядов — тоже по UTC"
    log = (store.root / collect.STATE_SIZE_LOG).read_text(encoding="utf-8")
    assert log == "2026-09-22;442000000\n", log


def test_the_weekly_growth_needs_eight_measurements_over_seven_days():
    """S1.3, случай 2: семь замеров — это шесть суток, а не неделя.

    Аудит закрытия, A3: при семи замерах подряд `health` печатал «прирост
    состояния … за 6 дн (замеров 7)» — недельной строкой по шести суткам.
    """
    from datetime import date, timedelta

    from indicators import collect

    last = date(2026, 9, 22)
    seven = [(last - timedelta(days=6 - i), 400_000_000 + i * 2_500_000) for i in range(7)]
    assert collect.state_growth_line(seven) == ""
    eight = [(last - timedelta(days=7 - i), 400_000_000 + i * 2_500_000) for i in range(8)]
    line = collect.state_growth_line(eight)
    assert "2.50 МБ/сутки" in line and "за 7 дн" in line and "замеров 8" in line, line
    assert "2026-09-15 → 2026-09-22" in line, line
    # Восемь строк, но с дырой внутри недели: в окне семь замеров — строки нет.
    holed = [row for row in eight if row[0] != date(2026, 9, 18)]
    holed.insert(0, (date(2026, 9, 14), 397_500_000))
    assert len(holed) == 8 and collect.state_growth_line(holed) == ""


def test_a_broken_line_of_the_size_log_is_skipped_with_a_warning_and_kept(tmp_path, capsys):
    """S1.3, случай 3: битая строка пропускается с предупреждением и ОСТАЁТСЯ в файле.

    Прежде она молча исчезала при первой же перезаписи журнала (аудит
    закрытия, A3). Журнал прежнего формата (строки `дата;байты` по UTC, как
    их писал боевой сервер) дописывается одной строкой, прежние строки не
    трогаются; предупреждение — не «НЕ В ПОРЯДКЕ», код `health` остаётся 0.
    """
    from datetime import date

    from indicators import collect, sources
    from indicators.store import Point, Store

    store = Store(tmp_path)
    log = store.root / collect.STATE_SIZE_LOG
    log.parent.mkdir(parents=True, exist_ok=True)
    old = ("2026-09-15;400000000\nмусор без точки с запятой\n2026-09-16;abc\n"
           "2026-09-17;402500000\n")
    log.write_text(old, encoding="utf-8")

    rows = collect.append_state_size(store, 405_000_000, today=date(2026, 9, 18))
    assert rows == [(date(2026, 9, 15), 400_000_000), (date(2026, 9, 17), 402_500_000),
                    (date(2026, 9, 18), 405_000_000)]
    printed = capsys.readouterr().out
    assert "строка 2 не разобрана" in printed and "строка 3 не разобрана" in printed, printed
    assert log.read_text(encoding="utf-8") == old + "2026-09-18;405000000\n"

    # Повтор в тот же день заменяет только сегодняшнюю строку.
    collect.append_state_size(store, 406_000_000, today=date(2026, 9, 18))
    assert log.read_text(encoding="utf-8") == old + "2026-09-18;406000000\n"

    # И `health` с битой строкой в журнале — в порядке.
    today = sources.UTC_TODAY()
    for series_id in ("cbr.key_rate", issuer.PRICE_SERIES, "moex.zcyc.10y"):
        store.upsert(series_id, [Point(today, 1.0, f"{today}T03:10:00+00:00")])
    store.raw_size_bytes = lambda: 407_000_000
    capsys.readouterr()
    assert collect.cmd_health(store) == 0
    printed = capsys.readouterr().out
    assert "предупреждение" in printed and "НЕ В ПОРЯДКЕ" not in printed, printed
    assert "мусор без точки с запятой" in log.read_text(encoding="utf-8")

    # Байты не в UTF-8 — тоже битая строка, а не падение `health`: прежде
    # `UnicodeDecodeError` давал тревогу каждое утро, пока файл не поправят
    # руками. Строка пропускается и возвращается в файл побайтово.
    raw = b"2026-09-15;400000000\n2026-09-16;40\xff\xfe0000\n"
    log.write_bytes(raw)
    assert collect.cmd_health(store) == 0
    printed = capsys.readouterr().out
    assert "строка 2 не разобрана" in printed and "НЕ В ПОРЯДКЕ" not in printed, printed
    assert log.read_bytes() == raw + f"{today};407000000\n".encode(), log.read_bytes()


def test_the_size_and_the_ceiling_are_printed_in_the_same_units(tmp_path, capsys):
    """Делегировано из S2.5: в одной строке были МиБ и десятичные ГБ.

    Из-за этого в отчёты уехало «442 МБ — 21,2 % потолка»: 442 МиБ при
    потолке 2 десятичных ГБ. 442 МБ десятичных — это 22,1 %.
    """
    from indicators import collect
    from indicators.store import STATE_CEILING_BYTES, Store

    store = Store(tmp_path)
    store.raw_size_bytes = lambda: 442_000_000
    collect.cmd_health(store)
    printed = capsys.readouterr().out

    assert "442.0 МБ" in printed, printed
    assert "22.1 % потолка 2 ГБ" in printed, printed
    assert STATE_CEILING_BYTES == 2_000_000_000, "потолок — решение владельца, не трогать"


def test_every_line_of_a_tact_prints_the_archive_in_the_same_units(tmp_path, capsys,
                                                                   monkeypatch):
    """ТРИ строки одного такта — один архив, одни единицы.

    Находка скептика: единицы свели только в строке `health`, которую назвал
    бриф, а `cmd_collect` (строка каждого такта) и `cmd_status` (зовётся из
    `run.sh` дважды) остались в МиБ под подписью «МБ». В одном журнале такта
    соседствовали «сырой архив: 421.5 МБ» и «сырой архив 442.0 МБ» про один и
    тот же архив — человеку хуже, чем до правки, где врали одинаково все три.

    Проверяется печать ВСЕХ трёх команд, а не только `health`.
    """
    from indicators import collect, sources
    from indicators.store import Store

    store = Store(tmp_path)
    store.raw_size_bytes = lambda: 442_000_000

    registry = dict(sources.COLLECTORS)
    registry["cbr_key_rate"] = (
        lambda *, store=None: sources.Collected("cbr", 1, {}, []), "ежедневно", 1)
    monkeypatch.setattr(sources, "COLLECTORS", registry)

    collect.cmd_collect(["cbr_key_rate"], store, retry=False, pause=0.0)
    collect.cmd_status(store)
    collect.cmd_health(store)
    lines = [line for line in capsys.readouterr().out.splitlines()
             if "сырой архив" in line]

    assert len(lines) == 3, lines
    for line in lines:
        assert "442.0 МБ" in line, line
        assert "421.5" not in line, line


def test_health_prints_the_age_of_the_irreplaceable_series(tmp_path, capsys, monkeypatch):
    """Аудит 850oa 26.09.2026, п. 5.6: возраст последней точки невосполнимых
    рядов — в `health`; старше предела — «не в порядке», ряда ещё нет —
    строка без тревоги. Ряды — подставные (у Ленты в `IRREPLACEABLE_SERIES`
    стоит день публикации датабука)."""
    from indicators import collect, issuer, sources
    from indicators.store import Point, Store

    monkeypatch.setattr(sources, "UTC_TODAY", lambda: "2026-09-26")
    monkeypatch.setattr(collect, "IRREPLACEABLE_SERIES",
                        {"issuer.feed.level": 2, "issuer.grid.low": 3, "issuer.grid.high": 3})
    store = Store(tmp_path)
    for series_id in ("cbr.key_rate", issuer.PRICE_SERIES, "moex.zcyc.10y"):
        store.upsert(series_id, [Point("2026-09-26", 1.0, "2026-09-26T03:10:00+00:00")])
    store.raw_size_bytes = lambda: 1_000_000
    assert collect.cmd_health(store) == 0
    printed = capsys.readouterr().out
    assert "issuer.feed.level" in printed and "ряда ещё нет" in printed

    store.upsert("issuer.feed.level", [Point("2026-09-25", 9300.0, "2026-09-25T03:10:00+00:00")])
    store.upsert("issuer.grid.low", [Point("2026-09-20", 1.01, "2026-09-20T03:10:00+00:00")])
    store.upsert("issuer.grid.high", [Point("2026-09-25", 1.01, "2026-09-25T03:10:00+00:00")])
    assert collect.cmd_health(store) == 1
    printed = capsys.readouterr().out
    assert "  issuer.feed.level               2026-09-25  возраст   1 дн  ок" in printed, printed
    assert "НЕ В ПОРЯДКЕ: issuer.grid.low: возраст 6 дн при пределе 3 — невосполнимый ряд" in printed
