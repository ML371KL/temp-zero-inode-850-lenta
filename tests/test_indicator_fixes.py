# -*- coding: utf-8 -*-
"""Шесть исправлений слоя индикаторов (задание итерации 3, A5).

Каждое из шести — отдельная находка аудита второй итерации, и каждое ловится
здесь своим тестом. Заготовки проверок — аудиторские (`journal_sim.py`,
`test_raw_before_parse.py`, `second_crawl.py`, `weekly_panel_sim.py`,
`store_pit.py`, `prune_sim.py`); здесь они превращены в тесты с ожидаемыми
значениями.
"""
from __future__ import annotations

import inspect
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from indicators import issuer

from indicators.journal import (
    FROZEN_BENCHMARKS,
    MAIN_BENCHMARK,
    Journal,
    JournalError,
    forecast_period,
    report_date,
)
from indicators.store import Point, Store

# Такт (ops/run.sh, TACT_TESTS): слой индикаторов на фикстурах и двойниках —
# быстрый, в сеть не ходит (тесты `network` такт исключает выражением).
pytestmark = pytest.mark.tact

MARGIN = issuer.series("ebitda_margin_pre16")
INTEREST = issuer.series("net_interest")


# ------------------------------------------------- (а) период прогноза


# ------------------------------------------------- (б) эталоны


def test_the_main_benchmark_is_one_per_quantity():
    """Один главный эталон на величину, а не «обогнать все сразу» (аудит 850oa, C2).

    У Ленты квартальная маржа шумная, и главный эталон сезонный: «тот же
    квартал год назад + сдвиг прошлого квартала г/г» (D15).
    """
    assert MAIN_BENCHMARK[MARGIN] == "yoy_plus_shift"
    assert MAIN_BENCHMARK[issuer.series("revenue_pre16")] == "yoy_growth_carried"
    assert MAIN_BENCHMARK[INTEREST] == "previous_period_scaled_by_rates"


def test_the_margin_benchmark_is_frozen_at_the_first_record(tmp_path):
    """Эталон маржи заморожен: переписанный задним числом — это подгонка."""
    journal = Journal(tmp_path / "j.sqlite")
    assert MARGIN in FROZEN_BENCHMARKS
    journal.record_naive(MARGIN, "2026Q3", {"yoy_plus_shift": 0.0712})
    # Тот же эталон тем же числом — не ошибка, просто нечего писать.
    assert journal.record_naive(MARGIN, "2026Q3", {"yoy_plus_shift": 0.0712}) == 0
    with pytest.raises(JournalError, match="заморожен"):
        journal.record_naive(MARGIN, "2026Q3", {"yoy_plus_shift": 0.0690})


def test_the_interest_benchmark_may_still_be_refined(tmp_path):
    """Эталон процентов зависит от средней ставки периода и уточняется."""
    journal = Journal(tmp_path / "j.sqlite")
    assert INTEREST not in FROZEN_BENCHMARKS
    journal.record_naive(INTEREST, "2026Q3", {"previous_period_scaled_by_rates": 3.3})
    assert journal.record_naive(INTEREST, "2026Q3",
                                {"previous_period_scaled_by_rates": 3.2}) == 1


def test_one_report_is_one_event(tmp_path):
    """4 кв., 2П и год приходят ОДНИМ годовым отчётом — одно событие по марже.

    Из периодов одного отчёта в зачёт идёт КВАРТАЛ — его прогнозирует слой.
    """
    journal = Journal(tmp_path / "j.sqlite")
    published = report_date("2026Q4")
    for period, value in (("2026Q4", 0.081), ("2026H2", 0.078), ("2026FY", 0.071)):
        journal.record_naive(MARGIN, period, {"seasonal_naive": 0.080})
        _record(journal, MARGIN, period, 0.0765, published - timedelta(days=95))
        journal.record_actual(MARGIN, period, value, reported_on=published)

    assert journal.events(MARGIN) == ["2026Q4"], (
        "из трёх периодов одного отчёта в зачёт идёт квартал")
    assert len(journal.scoreboard(MARGIN)) == 1
    assert journal.demoted(MARGIN, min_events=4) is False


def test_one_event_is_the_report_and_not_a_matching_date(tmp_path):
    """Событие — ОТЧЁТ (`periods.report_id`), а не совпадение `--reported-on`.

    Годовой отчёт вносится и уточняется неделями: 4 кв. можно внести в день
    публикации, а 2П — через неделю. Отчёт от этого не раздваивается; и
    наоборот, два разных отчёта, внесённые одним днём, остаются двумя.
    """
    from indicators.periods import report_id

    assert report_id("2026Q4") == report_id("2026H2") == report_id("2026FY") == \
        issuer.series("fy2026")
    assert report_id("2027Q2") == report_id("2027H1") == issuer.series("h1_2027")
    assert report_id("2027Q1") == issuer.series("q1_2027")
    assert report_id("2026Q4", "revenue") == issuer.series("q4_2026_ops"), (
        "выручка 4 кв. приходит отдельным событием — операционными результатами")
    assert report_id("2П2026") == "", "неразобранный период не выдаёт себя за отчёт"

    journal = Journal(tmp_path / "j.sqlite")
    for period, value, published in (("2026Q4", 0.081, date(2027, 3, 26)),
                                     ("2026H2", 0.078, date(2027, 4, 2))):
        journal.record_naive(MARGIN, period, {"seasonal_naive": 0.080})
        _record(journal, MARGIN, period, 0.0765, date(2026, 12, 20))
        journal.record_actual(MARGIN, period, value, reported_on=published)

    assert journal.events(MARGIN) == ["2026Q4"], (
        "даты внесения разошлись на неделю — отчёт от этого не раздвоился")
    assert len(journal.scoreboard(MARGIN)) == 1

    other = Journal(tmp_path / "k.sqlite")
    one_day = date(2027, 5, 15)
    for period in ("2026Q4", "2027Q1"):
        other.record_naive(MARGIN, period, {"seasonal_naive": 0.070})
        _record(other, MARGIN, period, 0.0765, date(2026, 12, 20))
        other.record_actual(MARGIN, period, 0.075, reported_on=one_day)
    assert other.events(MARGIN) == ["2026Q4", "2027Q1"], (
        "годовой и квартальный отчёты — два события, как бы их ни внесли")


def test_record_actual_refuses_arguments_that_do_not_exist(tmp_path, monkeypatch):
    """`record-actual` проверяет `--target` и `--period` (B4(3), C6).

    Команда принимала любую строку. Пример из `README.md` — `--target margin`
    — проходил с кодом 0 и писал факт под несуществующей величиной; заметить
    это было нечем: запись ложилась в журнал, удалять её запрещают триггеры, а
    с прогнозом она не встречалась никогда, и табло оставалось пустым — то
    есть выглядело как «прогнозов не было».
    """
    from indicators import collect
    from indicators import journal as journal_module

    path = tmp_path / "j.sqlite"
    monkeypatch.setattr(journal_module, "DEFAULT_PATH", path)

    bad = (
        ("margin", "2026Q3"),                       # «короткое» имя величины
        (issuer.series("ebitda_margin"), "2026Q3"),         # почти верное имя
        (MARGIN, "3кв2026"),                        # период по-русски
        (MARGIN, "2026Q5"),                         # пятого квартала не бывает
        (MARGIN, "2016FY"),                         # слой таких не прогнозировал
        (MARGIN, "2026Q2"),                         # до первого прогнозного квартала
    )
    for target, period in bad:
        assert collect.main(["record-actual", "--target", target, "--period", period,
                             "--value", "0.0482"]) == 64, (target, period)
    assert not path.exists(), (
        "отказ обязан случиться ДО журнала: неизменяемую запись не стереть")

    # День публикации по умолчанию — сегодня (будущий `--reported-on`
    # отвергается, см. `test_record_actual_refuses_a_publication_day_that_has_not_come`).
    assert collect.main(["record-actual", "--target", MARGIN, "--period", "2026Q3",
                         "--value", "0.0715"]) == 0
    actual = Journal(path).actual(MARGIN, "2026Q3")
    assert actual is not None and actual.value == pytest.approx(0.0715)


def _record_naive(journal, target, period, method, value, made_on):
    """Эталон «как если бы» его записали в прошлом. См. `_record`."""
    import sqlite3

    journal.record_naive(target, period, {method: value})
    with sqlite3.connect(journal.path) as db:
        db.execute("DROP TRIGGER IF EXISTS naive_no_update")
        db.execute("UPDATE naive SET made_at = ? WHERE method = ? AND value = ?",
                   (made_on.isoformat() + "T00:00:00+00:00", method, value))


def test_the_interest_benchmark_is_read_at_the_horizon_of_the_forecast(tmp_path):
    """Эталон процентов на каждом горизонте — тот, что был в ТОТ момент.

    Эталоны маржи и выручки заморожены при первой записи, а эталон процентов —
    ряд во времени (квартал набирает наблюдений ключевой ставки). Сравнение на
    разных множествах информации мерило бы возраст эталона, а не качество.
    """
    journal = Journal(tmp_path / "j.sqlite")
    published = date(2026, 10, 29)
    method = MAIN_BENCHMARK[INTEREST]

    _record_naive(journal, INTEREST, "2026Q3", method, 3.1, date(2026, 7, 15))
    _record_naive(journal, INTEREST, "2026Q3", method, 3.4, date(2026, 10, 10))
    _record(journal, INTEREST, "2026Q3", 3.0, date(2026, 7, 20))
    journal.record_actual(INTEREST, "2026Q3", 3.5, reported_on=published)

    horizons = journal.horizons(INTEREST, "2026Q3")
    assert horizons[90]["benchmark_value"] == pytest.approx(3.1), (
        "за 90 дней до отчёта ряд ключевой ставки знал меньше")
    assert horizons[45]["benchmark_value"] == pytest.approx(3.1)
    assert horizons[15]["benchmark_value"] == pytest.approx(3.4)
    assert horizons[90]["benchmark"] == method

    row = journal.scoreboard(INTEREST)[0]
    assert row["naive_" + method] == pytest.approx(horizons[45]["benchmark_value"])
    assert row["beats_benchmark"] == horizons[45]["beats_benchmark"]


def test_the_scoreboard_scores_against_the_main_benchmark(tmp_path):
    """Зачёт — против главного эталона; остальные остаются справкой."""
    journal = Journal(tmp_path / "j.sqlite")
    published = date(2026, 10, 29)
    made_on = published - timedelta(days=50)
    _record_naive(journal, MARGIN, "2026Q3", "yoy_plus_shift", 0.075, made_on)
    _record_naive(journal, MARGIN, "2026Q3", "seasonal_naive", 0.0695, made_on)
    _record(journal, MARGIN, "2026Q3", 0.0710, made_on)
    journal.record_actual(MARGIN, "2026Q3", 0.070, reported_on=published)

    row = journal.scoreboard(MARGIN)[0]
    assert row["main_benchmark"] == "yoy_plus_shift"
    # Прогноз 7,10 ближе к факту 7,00, чем главный эталон 7,50 → зачёт есть,
    # хотя справочный сезонный (6,95) был ещё точнее.
    assert row["beats_benchmark"] is True
    assert row["naive_seasonal_naive"] == pytest.approx(0.0695)
    # 3 кв. 2026: главный эталон сломан «О'КЕЙ» — на табло есть, в допуск нет.
    assert row["admission_excluded"] is True and "okey" in row["perimeter_breaks"]


# ------------------------------------------------- (в) сырое до разбора


def test_the_raw_response_is_saved_before_it_is_parsed(tmp_path, monkeypatch):
    """Сбой разбора стоит строки ряда, а не дня невосстановимых вакансий.

    Аудит, C3: `save_raw` вызывался ПОСЛЕ разбора, поэтому исключение на
    любой из 64 страниц обхода теряло весь день. У источников без истории
    нет вовсе (`closed_at` всегда null) — потерянный день не догоняется.
    """
    from indicators import http

    store = Store(tmp_path)
    sink = http.RawSink(store, "issuer_feed", day="2026-09-22")
    body = json.dumps({"results": [{"id": "v1", "salary_from": 60000}]}).encode()

    def fake_urlopen(request, timeout=None, context=None):
        class _Response:
            status = 200
            headers = {}

            def read(self):
                return body

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False
        return _Response()

    monkeypatch.setattr(http.urllib.request, "urlopen", fake_urlopen)
    response = http.fetch("https://issuer.example/api/feed?page=1",
                          sink=sink, name="vacancies__loc_506_p1.json", throttle=0.0)
    assert sink.kept == 1
    saved = tmp_path / "raw" / "issuer_feed" / "2026-09-22" / "vacancies__loc_506_p1.json"
    assert saved.exists() and saved.read_bytes() == body

    # И главное: байты на диске УЖЕ есть, хотя разбирать их никто не начинал.
    assert response.json()["results"][0]["id"] == "v1"


def test_an_answer_that_parses_into_nothing_is_an_error(tmp_path):
    """«ок: рядов 0» — самый вредный вид зелёного (у 850oa — мусор от Чек Индекса)."""
    from indicators import sources
    from indicators.sources import Collected, run_collectors

    store = Store(tmp_path)
    registry = dict(sources.COLLECTORS)
    registry["garbage_feed"] = (lambda: Collected("garbage_feed", 3, {}, []), "еженедельно", 3)
    registry["quiet_feed"] = (lambda: Collected("quiet_feed", 7, {}, []), "ежедневно", 7)
    original, quiet = sources.COLLECTORS, sources.MAY_PARSE_NOTHING
    sources.COLLECTORS, sources.MAY_PARSE_NOTHING = registry, frozenset({"quiet_feed"})
    try:
        report = run_collectors(["garbage_feed", "quiet_feed"], store)
    finally:
        sources.COLLECTORS, sources.MAY_PARSE_NOTHING = original, quiet

    assert report["garbage_feed"].startswith("ОШИБКА")
    # А молчащая лента раскрытия — норма: у эмитента просто не было событий.
    assert report["quiet_feed"].startswith("ок")


# ------------------------------------------------- (г) один обход в сутки


def test_a_back_dated_import_of_a_known_value_is_kept(tmp_path):
    """Архив, импортированный ПОСЛЕ живого сбора, обязан догрузиться.

    Аудит, C6: точка, равная последней известной, отбрасывалась — поэтому
    архив 850cl за 03–18.09.2026 фактически не догрузился, хотя импорт
    прошёл. Значение, датированное раньше, говорит не «ничего нового», а
    «это было известно РАНЬШЕ», и на этом стоит весь `value_as_of`.
    """
    store = Store(tmp_path)
    point = lambda value, fetched: Point(  # noqa: E731
        period="2026-08-24", value=value, fetched_at=fetched)

    store.upsert("t", [point(-0.04, "2026-09-21T08:00:00+00:00")])   # живой сборщик
    store.upsert("t", [point(-0.04, "2026-09-03T06:00:00+00:00")])   # архив 850cl
    series = store.load("t")
    assert len(series.points) == 2
    assert series.value_as_of("2026-08-24", "2026-09-10") == pytest.approx(-0.04)
    assert series.value_as_of("2026-08-24", "2026-09-02") is None, (
        "до 03.09 архив об этом не знал")


def test_repeated_identical_values_still_collapse(tmp_path):
    """A→A→A остаётся одной точкой: суточный прогон не плодит винтажи."""
    store = Store(tmp_path)
    for day in ("02", "03", "04"):
        store.upsert("t", [Point(period="2026-09-01", value=1.0,
                                 fetched_at=f"2026-09-{day}T00:00:00+00:00")])
    assert len(store.load("t").points) == 1

    # А A→B→A — три: исправленное и возвращённое значение тоже сообщение.
    store.upsert("t", [Point(period="2026-09-01", value=2.0,
                             fetched_at="2026-09-05T00:00:00+00:00")])
    store.upsert("t", [Point(period="2026-09-01", value=1.0,
                             fetched_at="2026-09-06T00:00:00+00:00")])
    assert len(store.load("t").points) == 3


def test_float_noise_is_not_a_new_vintage(tmp_path):
    """Пересчёт индекса меняет последний бит мантиссы — это не наблюдение."""
    store = Store(tmp_path)
    store.upsert("t", [Point(period="2026-09-05", value=1.010756960077949,
                             fetched_at="2026-09-21T00:00:00+00:00")])
    store.upsert("t", [Point(period="2026-09-05", value=1.0107569600779492,
                             fetched_at="2026-09-28T00:00:00+00:00")])
    assert len(store.load("t").points) == 1


def test_a_non_finite_value_is_refused_at_intake(tmp_path, monkeypatch):
    """NaN и Infinity от источника не попадают в ряд: конечные точки дня
    сохранены, отказ назван рядом и периодом и доходит до `live.degraded`."""
    from indicators import sources
    from indicators.collect import degradation_notes, read_collector_report, write_collector_report
    from indicators.http import Response

    # Сборщик-заглушка: источник отдал NaN и бесконечности вперемешку с
    # конечной точкой (у 850oa так отвечал Чек Индекс).
    def feed(*, store=None):
        points = [sources.Point(period=day, value=value, fetched_at="2026-09-28T06:00:00+00:00")
                  for day, value in (("2026-09-07", float("nan")), ("2026-09-14", float("nan")),
                                     ("2026-09-21", float("inf")), ("2026-09-28", 0.03))]
        return sources.Collected("feed", 3, {"feed.receipts_yoy": points}, [])

    registry = dict(sources.COLLECTORS)
    registry["feed"] = (feed, "еженедельно", 3)
    monkeypatch.setattr(sources, "COLLECTORS", registry)
    store = Store(tmp_path)
    report = sources.run_collectors(["feed"], store)

    status = report["feed"]
    assert status.startswith("ОШИБКА: неконечные значения отброшены (3)"), status
    assert "feed.receipts_yoy 2026-09-07: nan" in status
    series = store.load("feed.receipts_yoy")
    assert [(p.period, p.value) for p in series.points] == [("2026-09-28", 0.03)]
    json.loads((tmp_path / "series" / "feed.receipts_yoy.json").read_text(encoding="utf-8"),
               parse_constant=lambda token: pytest.fail(f"{token} в архиве"))

    write_collector_report(store, report)
    notes = degradation_notes(read_collector_report(store), today=date.today())
    assert any("feed" in note and "2026-09-14" in note for note in notes), notes


def test_the_series_file_is_strict_json(tmp_path):
    """Страховка за приёмом: ряд с NaN не записывается вовсе, а не пишется
    токеном, который потом уйдёт в выпуск."""
    from indicators.store import Series

    store = Store(tmp_path)
    with pytest.raises(ValueError):
        store.save(Series(id="t", unit="", cadence="", label="", channel=0, points=[
            Point(period="2026-09-01", value=float("nan"), fetched_at="2026-09-02")]))


def _record(journal, target, period, value, made_on):
    """Запись прогноза «как если бы» она была сделана в прошлом.

    Журнал неизменяем и ставит `made_at` сам; для теста горизонтов нужна
    запись в прошлом, поэтому дата правится прямым UPDATE в обход триггера —
    ровно так же, как это делает аудиторская заготовка.

    «Сегодня» записи — тот же прошлый день (`today=made_on`). Без этого
    сторож журнала сверял плановую дату отчёта с НАСТОЯЩИМ днём прогона, и с
    01.05.2027 каждый тест с этой заготовкой падал бы на «плановая дата отчёта
    прошла» — то есть проверял бы календарь, а не журнал (аудит 24.09.2026,
    G1-exam §1.4).
    """
    import sqlite3

    forecast = journal.record(target=target, period=period, value=value,
                              std_error=0.01, equation="e", version="v", inputs={},
                              today=made_on)
    with sqlite3.connect(journal.path) as db:
        db.execute("DROP TRIGGER IF EXISTS forecasts_no_update")
        db.execute("UPDATE forecasts SET made_at = ? WHERE made_at = ?",
                   (made_on.isoformat() + "T00:00:00+00:00", forecast.made_at))
    return forecast


# ------------------------------------------------- (е) потолок состояния


# ---------- S1.4: сырое на диск ДО разбора у пяти названных сборщиков


@pytest.mark.parametrize("name,source", [
    ("cbr_key_rate", "cbr"),
    ("moex_curve", "moex"),
    ("moex_quote", "moex"),
    ("moex_security", "moex"),
    ("bonds", "moex"),
    ("lenta_disclosure", "lenta_disclosure"),
    ("lenta_databook", "lenta_databook"),
])
def test_a_page_is_on_disk_when_parsing_throws(tmp_path, monkeypatch, name, source):
    """Исключение в разборе не должно стоить скачанной страницы.

    Заявление четвёртой итерации «`RawSink` у всех сборщиков» было неверным:
    приёмник был у одного (`issuer_feed`), остальные сохраняли сырое из
    `result.raw` ПОСЛЕ возврата сборщика — то есть при исключении не
    сохраняли ничего (аудит, A4; проба `rawsink_sim.py`).

    Сеть подменяется на уровне `urllib.request.urlopen`: сохранение до
    разбора живёт внутри настоящего `http.fetch`, и проба, подменяющая сам
    `fetch`, проверяла бы собственную заглушку.
    """
    import urllib.request

    from indicators import http, sources

    class Reply:
        status, headers = 200, {}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"<html>maintenance</html>"

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda request, timeout=None, context=None: Reply())
    real_fetch = http.fetch
    monkeypatch.setattr(sources, "fetch",
                        lambda url, **kw: real_fetch(url, **{**kw, "throttle": 0.0}))

    store = Store(tmp_path)
    report = sources.run_collectors([name], store)
    assert report[name].startswith("ОШИБКА"), report

    saved = [path.name for path in (store.raw / source).rglob("*")
             if path.is_file() and not path.name.endswith(".meta.json")]
    assert saved, f"{name}: страница скачана, разбор упал, на диске ничего нет"
    for path in (store.raw / source).rglob("*"):
        if path.is_file() and not path.name.endswith(".meta.json"):
            assert Store.read_raw(path) == b"<html>maintenance</html>"


def test_the_first_good_page_survives_a_broken_second_one(tmp_path, monkeypatch):
    """У кривой ОФЗ терялась и УДАЧНАЯ первая страница.

    `collect_moex_curve` берёт две страницы: ZCYC и линкеры. Пока сырое
    сохранялось после возврата сборщика, HTML на второй странице уносил с
    собой первую — ту, на которой стоит весь мир ставок.
    """
    import urllib.request

    from indicators import http, sources

    good = json.dumps({"yearyields": {"columns": ["period", "tradedate", "value"],
                                      "data": [[10, "2026-09-22", 15.0]]}}).encode()

    class Reply:
        status, headers = 200, {}

        def __init__(self, url):
            self.body = good if "zcyc" in url else b"<html>maintenance</html>"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return self.body

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda request, timeout=None, context=None: Reply(request.full_url))
    real_fetch = http.fetch
    monkeypatch.setattr(sources, "fetch",
                        lambda url, **kw: real_fetch(url, **{**kw, "throttle": 0.0}))

    store = Store(tmp_path)
    report = sources.run_collectors(["moex_curve"], store)
    assert report["moex_curve"].startswith("ОШИБКА"), report

    saved = sorted(path.name for path in (store.raw / "moex").rglob("*")
                   if path.is_file() and not path.name.endswith(".meta.json"))
    assert saved == ["ofz_in.json", "zcyc.json"]
    zcyc = next(path for path in (store.raw / "moex").rglob("zcyc.json"))
    assert Store.read_raw(zcyc) == good, "удачная страница кривой на месте"


# ---------- S1.6: проба тревоги отличима от отказа и снимается тактом


def test_the_probe_line_starts_with_the_word_probe(tmp_path, monkeypatch):
    """Плашку читает человек, и первым словом он должен видеть «ПРОБА».

    `--simulate-failure` объявляет источник отказавшим, НЕ обращаясь к нему:
    данные за день на месте. Поле `simulated` в отчёте было, а
    `degradation_notes` его не читал — на витрине проба выглядела как
    настоящий невосполнимый пропуск (аудит четвёртой итерации, A6).
    Слово стоит первым, потому что на телефоне строка обрезается.
    """
    from indicators import collect
    from indicators.store import Store

    store = Store(tmp_path)
    monkeypatch.setattr(collect, "IRRECOVERABLE", frozenset({"issuer_feed"}))
    collect.write_collector_report(store, {"issuer_feed": collect.SIMULATED_STATUS},
                                   simulated=("issuer_feed",))
    line = collect.degradation_notes(collect.read_collector_report(store))[0]
    assert line.startswith("ПРОБА"), line
    assert "обращения к источнику не было" in line
    assert "невосполним" not in line, (
        "проба ничего не теряет — слово «невосполним» здесь было бы ложью")

    # А настоящий отказ того же источника читается по-прежнему.
    collect.write_collector_report(store, {"issuer_feed": "ОШИБКА: HTTP 503"})
    real = collect.degradation_notes(collect.read_collector_report(store))[0]
    assert not real.startswith("ПРОБА")
    assert "невосполним" in real and "503" in real


def test_a_probe_record_is_cleared_by_the_next_tact_whatever_it_returns(tmp_path):
    """Запись пробы живёт ровно один такт — чем бы следующий ни кончился.

    У вакансий следующий такт суток отвечает «пропущен: обход за эту дату уже
    собран», а «пропущен» отказа не снимает (и правильно — обход мог упасть).
    Поэтому плашка о НЕСУЩЕСТВУЮЩЕМ пропуске висела на витрине до завтра
    (проба аудитора `alarm_codes_sim.py`: «следующий такт того же дня … отчёт:
    {'issuer_feed': (…, True, True, …)}»).
    """
    from indicators import collect
    from indicators.store import Store

    outcomes = {"skip": "пропущен: обход за эту дату уже собран",
                "ok": "ок: рядов 4, точек 4",
                "fail": "ОШИБКА: HTTP 503"}
    for label, next_status in outcomes.items():
        store = Store(tmp_path / label)
        collect.write_collector_report(store, {"issuer_feed": collect.SIMULATED_STATUS},
                                       simulated=("issuer_feed",))
        assert collect.read_collector_report(store)["sources"]["issuer_feed"]["simulated"]

        collect.write_collector_report(store, {"issuer_feed": next_status})
        saved = collect.read_collector_report(store)["sources"].get("issuer_feed")
        if next_status.startswith("ОШИБКА"):
            # Настоящий отказ встаёт на место пробы — и уже НЕ помечен пробой.
            assert saved["simulated"] is False, next_status
            assert "503" in saved["status"]
        else:
            assert saved is None, f"запись пробы обязана сняться: {next_status}"
        assert not any(note.startswith("ПРОБА") for note in
                       collect.degradation_notes(collect.read_collector_report(store)))

    # Повторная проба тем же тактом запись, разумеется, сохраняет.
    store = Store(tmp_path / "again")
    collect.write_collector_report(store, {"issuer_feed": collect.SIMULATED_STATUS},
                                   simulated=("issuer_feed",))
    collect.write_collector_report(store, {"issuer_feed": collect.SIMULATED_STATUS},
                                   simulated=("issuer_feed",))
    assert collect.read_collector_report(store)["sources"]["issuer_feed"]["simulated"]


# ---------- S1.7 (а): record-actual проверяет период, величину и день выхода


def _record_actual(argv):
    from indicators import collect

    return collect.main(["record-actual"] + argv)


@pytest.fixture
def journal_in(tmp_path, monkeypatch):
    """Журнал во временном файле: запись факта неснимаема, и это к лучшему."""
    from indicators import journal as journal_module

    path = tmp_path / "journal.sqlite"
    monkeypatch.setattr(journal_module, "DEFAULT_PATH", path)
    return path


def test_record_actual_refuses_a_period_that_has_not_started(journal_in):
    """Факт за период, который ещё не начался, — опечатка, и стереть её нечем.

    Граница сверху — календарный квартал (та же, что у `forecast_period`);
    периоды считаются от сегодняшнего квартала, а не литералами, — иначе тест
    устаревал бы вместе с календарём.
    """
    from indicators import periods

    now = periods.quarter_of(date.today())
    upcoming = periods.next_period(now)             # ещё не начался
    typo = f"{int(now[:4]) + 4}Q1"                  # опечатка в цифре года
    for period in (typo, upcoming, f"{int(now[:4]) + 1}FY"):
        assert _record_actual(["--target", issuer.series("net_interest"), "--period", period,
                               "--value", "3.3"]) == 64, period
    assert not journal_in.exists(), "отказ обязан случиться ДО журнала"

    # Текущий квартал — годится (факт за него вносят после выхода отчёта).
    assert _record_actual(["--target", issuer.series("net_interest"),
                           "--period", now, "--value", "3.3"]) == 0


@pytest.mark.parametrize("target,value,ok", [
    (issuer.series("ebitda_margin_pre16"), 0.0715, True),
    (issuer.series("ebitda_margin_pre16"), 7.15, False),      # проценты вместо доли
    (issuer.series("ebitda_margin_pre16"), -0.01, False),
    (issuer.series("net_interest"), 3.3, True),
    (issuer.series("net_interest"), 3300.0, False),           # миллионы вместо миллиардов
    (issuer.series("revenue_pre16"), 341.6, True),
    (issuer.series("revenue_pre16"), 0.34, False),            # триллионы вместо миллиардов
    (issuer.series("revenue_pre16"), 341600.0, False),        # миллионы
])
def test_record_actual_checks_the_size_of_the_value(journal_in, target, value, ok):
    """Значение проверяется ПО ВЕЛИЧИНЕ: 7,15 для доли — это чужие единицы."""
    from indicators import periods

    code = _record_actual(["--target", target, "--period", periods.quarter_of(date.today()),
                           "--value", str(value)])
    assert code == (0 if ok else 64), (target, value)


def test_record_actual_refuses_a_publication_day_that_has_not_come(journal_in):
    """`--reported-on 2099-01-01` принимался (A7): от него меряются горизонты.

    День публикации в будущем тихо обнуляет зачёт «за сколько дней до отчёта
    что говорили». Запас ровно в сутки — конвейер живёт в UTC, а раскрытие
    выходит по Москве.
    """
    from datetime import datetime, timedelta, timezone

    from indicators import periods

    today = datetime.now(timezone.utc).date()
    base = ["--target", issuer.series("net_interest"), "--period", periods.quarter_of(today),
            "--value", "3.3", "--reported-on"]

    assert _record_actual(base + ["2099-01-01"]) == 64
    assert _record_actual(base + [(today + timedelta(days=2)).isoformat()]) == 64
    assert _record_actual(base + ["29.10.2026"]) == 64, "не дата в формате ГГГГ-ММ-ДД"
    assert not journal_in.exists(), "отказ обязан случиться ДО журнала"

    assert _record_actual(base + [(today - timedelta(days=3)).isoformat()]) == 0

    # Отказ называет причину — день ПУБЛИКАЦИИ, — на любой дате прогона.
    import io
    from contextlib import redirect_stderr

    from indicators import collect

    err = io.StringIO()
    with redirect_stderr(err):
        code = _record_actual(base + [(today + timedelta(days=2)).isoformat()])
    assert code == 64
    assert "день ПУБЛИКАЦИИ" in err.getvalue()

    # Пример из шапки `collect.py` (`--reported-on 2026-10-29`) — день выхода
    # отчёта за 3 кв. 2026: ДО него команда отказывает, С НЕГО пример
    # становится обычной командой внесения факта.
    readme_day = date(2026, 10, 29)
    example = ["record-actual", "--target", issuer.series("ebitda_margin_pre16"),
               "--period", "2026Q3", "--value", "0.0715",
               "--reported-on", readme_day.isoformat(), "--source", "Лента, 3 кв. 2026"]
    err = io.StringIO()
    with redirect_stderr(err):
        code = collect.main(example)
    if readme_day > today + timedelta(days=1):
        assert code == 64
        assert "день ПУБЛИКАЦИИ" in err.getvalue()
    else:
        assert code == 0, err.getvalue()


def test_half_and_year_margin_facts_are_accepted_because_the_layer_writes_them(journal_in):
    """Факт по марже за полугодие и год принимается — и это правило, а не дыра.

    Такт нау-каста пишет по марже ДВА прогноза: за квартал и за его полугодие
    (правило A-P2u, D2), и полугодовой закрывается только фактом за `ГГГГHn`;
    год — сверка с гайденсом компании. Запрет отрезал бы единственный способ
    их закрыть. Период — тот, чей первый квартал уже начался.
    """
    from indicators import collect, periods
    from indicators.journal import Journal

    now = periods.quarter_of(date.today())
    half = periods.half_of_quarter(now)
    year = periods.full_year(now)
    assert _record_actual(["--target", collect.MARGIN_TARGET,
                           "--period", half, "--value", "0.0775"]) == 0
    assert _record_actual(["--target", collect.MARGIN_TARGET,
                           "--period", year, "--value", "0.0712"]) == 0
    assert Journal().actual(collect.MARGIN_TARGET, half).value == 0.0775

    source = inspect.getsource(collect.cmd_nowcast)
    assert "target=MARGIN_TARGET, period=half" in source, (
        "прогноз маржи полугодия исчез из нау-каста — тогда и правило другое")
