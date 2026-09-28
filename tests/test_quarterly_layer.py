# -*- coding: utf-8 -*-
"""Квартальный слой Ленты: мост к полугодовой модели, гайденс, периметр, ретро, такт нау-каста.

Книга 1.0 ещё не в `data/` (D17): ожидание квартала считает настоящее ядро
(`model.quarters.expected_quarter`) на двойнике сетки или на синтетической книге
(`tests/toy.py`); прочее — на синтетических данных. Ключи книги — значения
черновика книги 1.0 в форме, которую принимает ядро (см. `toy_book`); факты
гайденса — `anchor.json` черновика.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from indicators import collect, issuer, perimeter, periods, quarterly, retro
from indicators.journal import GUIDANCE, MODEL_EXPECTATION, Journal, report_deadline
from indicators.store import Point, Store

# Такт (ops/run.sh, TACT_TESTS): слой индикаторов на фикстурах и двойниках —
# быстрый, в сеть не ходит (тесты `network` такт исключает выражением).
pytestmark = pytest.mark.tact

MARGIN = issuer.series("ebitda_margin_pre16")
REVENUE = issuer.series("revenue_pre16")


def toy_book() -> dict:
    """Ключи квартального слоя книги 1.0 в форме ядра.

    Ядро (`model.book.quarter_rules`) принимает доли кварталов ВНУТРИ полугодия
    (пары дают 1, DESIGN D2) и поправки, которые с весами выручки дают ноль до
    1e-12. Доли — книги 1.0; поправки 1 и 3 кв. — книги, 2 и 4 кв. выведены из
    тождества Σ доля·поправка = 0 (так их записывает книга 1.0: черновик давал
    Σ ≈ −1·10⁻⁶ из-за округления последнего знака).
    """
    share = {"Q1": 0.484, "Q2": 0.516, "Q3": 0.457, "Q4": 0.543}
    offset = {"Q1": -0.00754, "Q3": -0.00062}
    offset["Q2"] = -offset["Q1"] * share["Q1"] / share["Q2"]
    offset["Q4"] = -offset["Q3"] * share["Q3"] / share["Q4"]
    return {
        "margin": {"quarter_offset_pp": {q: offset[q] for q in ("Q1", "Q2", "Q3", "Q4")}},
        "revenue": {"quarter_share": share},
        "joint": {"regime_update": {"sigma_pp": 0.007, "observations": {}}},
    }


def core_grid(*, rows: dict | None = None, cells: list | None = None) -> list:
    """Двойник сетки `build_grid` для ядра: клетки `(вероятность, {полугодие:
    (выручка, маржа)})` или одна клетка `rows`; ядро читает у сетки те же поля —
    `probability`, `result.rows[]` с `period`, `revenue`, `margin`."""
    cells = cells or [(1.0, rows)]
    return [SimpleNamespace(probability=p, result=SimpleNamespace(rows=[
        SimpleNamespace(period=period, revenue=revenue, margin=margin)
        for period, (revenue, margin) in half_rows.items()])) for p, half_rows in cells]


ANCHOR = {"guidance_2026": {"ebitda_margin_min": 0.07, "src": "PR_OKEY_2026_06_02"},
          "halves_for_book": {"revenue": {"reported": {"2026H1": 648.488059}},
                              "ebitda_pre16": {"reported": {"2026H1": 39.279464}}}}


# ------------------------------------------------------------ мост квартала


def test_the_quarter_share_is_normalised_within_the_half():
    """Доли внутри полугодия (правило ядра и книги 1.0) и те же доли, записанные в
    году, дают одно и то же: s_Q / (s_Qa + s_Qb) — нормировка слоя от масштаба
    не зависит."""
    within = {"Q1": 0.484, "Q2": 0.516, "Q3": 0.457, "Q4": 0.543}
    annual = {k: v / 2 for k, v in within.items()}
    for quarter in ("2026Q1", "2026Q2", "2026Q3", "2026Q4"):
        assert quarterly.share_within_half(within, quarter) == pytest.approx(
            quarterly.share_within_half(annual, quarter))
    assert quarterly.share_within_half(within, "2026Q3") == pytest.approx(0.457)


def test_the_layer_takes_the_quarter_from_the_core():
    """Ожидание квартала слоя — ответ настоящего `model.quarters.expected_quarter`
    (своей арифметики квартала у слоя нет): Σ p · (квартал клетки), выручка
    R_H·s_Q/(s_Qa+s_Qb), маржа m_H + o_Q; полугодие восстанавливается обратно."""
    from model.quarters import expected_quarter as core

    A = toy_book()
    grid = core_grid(cells=[(0.6, {"2026H2": (740.0, 0.071)}),
                            (0.4, {"2026H2": (735.0, 0.066)})])
    half_margin = 0.6 * 0.071 + 0.4 * 0.066
    half_revenue = 0.6 * 740.0 + 0.4 * 735.0
    got = quarterly.expected_quarter(A, "2026Q3", grid=grid)
    direct = core(grid, A, "2026Q3")
    assert got.source == "model.quarters.expected_quarter"
    assert (got.period, got.half) == ("2026Q3", "2026H2")
    assert got.margin == direct["margin"] and got.revenue == direct["revenue"]
    assert got.margin == pytest.approx(half_margin - 0.00062, abs=1e-15)
    assert got.revenue == pytest.approx(half_revenue * 0.457, rel=1e-14)
    assert got.half_margin == pytest.approx(half_margin, abs=1e-15)
    assert got.half_revenue == pytest.approx(half_revenue, rel=1e-14)
    assert got.offset_pp == -0.00062 and got.share == pytest.approx(0.457, rel=1e-14)
    assert got.margin_se == pytest.approx(0.007 * 2 ** 0.5)
    with pytest.raises(ValueError, match="квартал"):
        quarterly.expected_quarter(A, "2026H2", grid=grid)
    with pytest.raises(KeyError):
        quarterly.expected_quarter(A, "2027Q1", grid=grid)


def test_the_core_refuses_quarter_keys_it_does_not_accept():
    """Доли в году (пары не дают 1) и поправки черновика книги с округлённым
    последним знаком (Σ доля·поправка ≈ −1·10⁻⁶) ядро отказывает — квартал не
    считается молча на неверных ключах."""
    from model.book import BookError

    A = toy_book()
    grid = core_grid(rows={"2026H2": (740.0, 0.07)})
    annual = dict(A, revenue={"quarter_share": {"Q1": 0.242, "Q2": 0.258,
                                                "Q3": 0.2285, "Q4": 0.2715}})
    with pytest.raises(BookError, match="сумм"):
        quarterly.expected_quarter(annual, "2026Q3", grid=grid)
    rounded = dict(A, margin={"quarter_offset_pp": {"Q1": -0.00754, "Q2": 0.00707,
                                                    "Q3": -0.00062, "Q4": 0.00052}})
    with pytest.raises(BookError, match="весами выручки"):
        quarterly.expected_quarter(rounded, "2026Q3", grid=grid)


def test_the_expected_quarter_of_the_synthetic_book_rests_on_the_grid_of_the_core():
    """Без двойников: синтетическая книга (`tests/toy.py`) → сетка `build_grid` на
    чистой книге → ядро. Маржа полугодия — ожидание модели нау-каста
    (`model_expectation`), кварталы полугодия с весами выручки дают его ровно."""
    from indicators.nowcast import model_expectation
    from model.grid import build_grid
    from model.quarters import expected_quarter as core
    from tests.toy import toy_book as synthetic_book

    A = synthetic_book()
    q3, q4 = (quarterly.expected_quarter(A, "2026Q3"),
              quarterly.expected_quarter(A, "2026Q4"))
    direct = core(build_grid(A), A, "2026Q3")
    assert q3.margin == pytest.approx(direct["margin"], abs=1e-15)
    assert q3.revenue == pytest.approx(direct["revenue"], rel=1e-14)
    half = model_expectation(A, "2026H2")[0]
    assert q3.half_margin == pytest.approx(half, abs=1e-12)
    assert q4.half_margin == pytest.approx(half, abs=1e-12)
    assert q3.half_revenue == pytest.approx(q4.half_revenue, rel=1e-12)
    weighted = (q3.revenue * q3.margin + q4.revenue * q4.margin) / (q3.revenue + q4.revenue)
    assert weighted == pytest.approx(half, abs=1e-12)


def test_the_quarter_margins_average_back_to_the_half_with_revenue_weights():
    """Σ доля · поправка = 0 внутри полугодия: маржа двух кварталов, взвешенная
    выручкой, равна марже полугодия, выручки кварталов складываются в полугодие."""
    A = toy_book()
    grid = core_grid(rows={"2026H2": (740.0, 0.0715)})
    q3 = quarterly.expected_quarter(A, "2026Q3", grid=grid)
    q4 = quarterly.expected_quarter(A, "2026Q4", grid=grid)
    weighted = (q3.revenue * q3.margin + q4.revenue * q4.margin) / (q3.revenue + q4.revenue)
    assert weighted == pytest.approx(0.0715, abs=1e-12)
    assert q3.revenue + q4.revenue == pytest.approx(740.0, rel=1e-14)


# ------------------------------------------------------------------ гайденс


def test_the_guidance_requires_a_second_half_margin_of_about_7_8_percent():
    """D2: «≥7 % за 2026» при 1П 6,06 % требует ≈7,7–7,9 % во 2П (вес 2П 51–55 %)."""
    guidance = quarterly.load_guidance(ANCHOR, 2026)
    assert guidance == dict(year=2026, fy_margin=0.07, h1_revenue=648.488059,
                            h1_ebitda=39.279464, source="PR_OKEY_2026_06_02")
    for h2_revenue, low, high in ((700.0, 0.0775, 0.0790), (800.0, 0.0765, 0.0780)):
        half = quarterly.guidance_half_margin(fy_margin=0.07, h1_revenue=648.488059,
                                              h1_ebitda=39.279464, h2_revenue=h2_revenue)
        assert low < half < high, (h2_revenue, half)
        year = (39.279464 + half * h2_revenue) / (648.488059 + h2_revenue)
        assert year == pytest.approx(0.07), "маржа 2П возвращает годовую цель ровно"


def test_the_guidance_benchmark_says_nothing_about_other_periods():
    kwargs = dict(guidance_year=2026, fy_margin=0.07, h1_revenue=648.488059,
                  h1_ebitda=39.279464, h2_revenue=740.0,
                  quarter_offset_pp=toy_book()["margin"]["quarter_offset_pp"])
    half = quarterly.guidance_benchmark("2026H2", **kwargs)
    assert quarterly.guidance_benchmark("2026Q3", **kwargs) == pytest.approx(half - 0.00062)
    assert quarterly.guidance_benchmark("2026Q4", **kwargs) == pytest.approx(
        half + kwargs["quarter_offset_pp"]["Q4"])
    assert quarterly.guidance_benchmark("2026FY", **kwargs) == 0.07
    for period in ("2026Q1", "2026Q2", "2026H1", "2027Q3", "2027FY"):
        assert quarterly.guidance_benchmark(period, **kwargs) is None, period
    assert quarterly.load_guidance({}, 2026) is None, "нет фактов — нет эталона"


def test_the_second_half_margin_implied_by_the_third_quarter_fact():
    """Экран «Ближайший отчёт»: какая маржа 2П следует из факта 3 кв. (D2)."""
    A = toy_book()
    implied = quarterly.implied_half_margin(
        0.0730, quarter="2026Q3", quarter_offset_pp=A["margin"]["quarter_offset_pp"],
        quarter_share=A["revenue"]["quarter_share"], other_quarter_expectation=0.0760,
        quarter_expectation=0.0709)
    assert implied["half"] == "2026H2"
    assert implied["implied_persistent"] == pytest.approx(0.0730 + 0.00062)
    assert implied["implied_independent"] == pytest.approx(0.457 * 0.0730 + 0.543 * 0.0760)
    assert implied["surprise"] == pytest.approx(0.0021)
    with pytest.raises(ValueError):
        quarterly.implied_half_margin(0.07, quarter="2026Q4",
                                      quarter_offset_pp=A["margin"]["quarter_offset_pp"],
                                      quarter_share=A["revenue"]["quarter_share"])


# ------------------------------------------------------------------ периметр


def test_the_perimeter_breaks_are_the_six_deals_of_the_design():
    ids = [b.id for b in perimeter.breaks()]
    assert ids == ["monetka", "ulybka", "molniya", "remi", "domlenta", "okey"]
    quarters = {b.id: b.quarter for b in perimeter.breaks()}
    assert quarters == {"monetka": "2023Q4", "ulybka": "2024Q4", "molniya": "2025Q2",
                        "remi": "2025Q4", "domlenta": "2026Q1", "okey": "2026Q2"}


def test_okey_breaks_the_main_benchmark_from_2026q2_to_2027q3():
    """«О'КЕЙ» (02.06.2026) нет в базе прошлого года: главный эталон сломан с 2 кв.
    2026 по 3 кв. 2027 (сдвиг прошлого квартала сравнивает 2027Q2 с 2026Q2)."""
    broken = [p for p in _quarters("2026Q1", "2028Q1")
              if "okey" in [b.id for b in perimeter.broken_by(p, "yoy_plus_shift")]]
    assert broken == ["2026Q2", "2026Q3", "2026Q4", "2027Q1", "2027Q2", "2027Q3"]
    seasonal = [p for p in _quarters("2026Q1", "2028Q1")
                if "okey" in [b.id for b in perimeter.broken_by(p, "seasonal_naive")]]
    assert seasonal == ["2026Q2", "2026Q3", "2026Q4", "2027Q1", "2027Q2"]
    # Эталоны на текущем периметре разрывом не ломаются.
    assert perimeter.broken_by("2026Q3", MODEL_EXPECTATION) == []
    assert perimeter.broken_by("2026Q3", GUIDANCE) == []
    # Консолидация с первого дня квартала периметры не различает.
    assert [b.id for b in perimeter.level_broken_by("2026Q1")] == []
    assert [b.id for b in perimeter.level_broken_by("2026Q2")] == ["okey"]


def test_the_perimeter_list_can_be_extended_by_data(tmp_path):
    """Следующая сделка («Мария-Ра» и т. п.) вносится фактами, а не кодом."""
    (tmp_path / perimeter.BREAKS_FILE).write_text(json.dumps({
        "schema": perimeter.SCHEMA,
        "breaks": [{"id": "maria", "name": "«Мария-Ра»", "control": "2027-04-01",
                    "basis": "пример"}]}), encoding="utf-8")
    items = perimeter.breaks(tmp_path)
    assert [b.id for b in items] == ["maria"]
    assert [b.id for b in perimeter.broken_by("2027Q3", "seasonal_naive", items=items)] == [
        "maria"]
    (tmp_path / perimeter.BREAKS_FILE).write_text('{"schema": "other", "breaks": []}',
                                                  encoding="utf-8")
    with pytest.raises(ValueError, match="схема"):
        perimeter.breaks(tmp_path)


def _quarters(start: str, stop: str) -> list[str]:
    out, period = [], start
    while periods.index(period) <= periods.index(stop):
        out.append(period)
        period = periods.next_period(period)
    return out


# ------------------------------------------------------------------ ретро


def _facts_dir(tmp_path: Path) -> Path:
    """Синтетическая квартальная история в схеме фактов книги (`accounting_base.json`)."""
    quarters = {}
    for n, quarter in enumerate(_quarters("2021Q1", "2026Q2")):
        season = {1: -0.008, 2: 0.006, 3: -0.001, 4: 0.003}[periods.parse(quarter).number]
        revenue = 100.0 * (1.03 ** n) * (1.6 if periods.index(quarter) >= periods.index(
            "2023Q4") else 1.0)
        margin = 0.065 + season + 0.0005 * (n % 3)
        quarters[quarter] = {"ias17": {"revenue": {"v": revenue},
                                       "ebitda": {"v": revenue * margin},
                                       "net_interest": {"v": -2.0 - 0.05 * n}}}
    directory = tmp_path / "facts"
    directory.mkdir(parents=True)
    (directory / retro.ACCOUNTING_FILE).write_text(json.dumps({
        "source_doc": {"id": "DATABOOK_Q22026", "sha256": "0" * 64},
        "quarters": quarters}), encoding="utf-8")
    return directory


def test_the_retro_check_lists_the_perimeter_breaks_and_scores_the_rest(tmp_path):
    block = retro.retro_block(store=None, facts_dir=_facts_dir(tmp_path))
    margin = block["margin"]
    assert margin["main"] == "yoy_plus_shift"
    assert margin["periods"] == ["2022Q2", "2026Q2"]
    excluded = {e["period"] for e in margin["excluded"]}
    assert {"2023Q4", "2024Q1", "2026Q2"} <= excluded, "Монетка и «О'КЕЙ» — отдельным списком"
    main = next(r for r in margin["benchmarks"] if r["method"] == "yoy_plus_shift")
    assert main["clean"]["n"] == main["all"]["n"] - len(margin["excluded"])
    revenue = block["revenue"]
    assert {e["period"] for e in revenue["excluded"]} >= {"2023Q4", "2024Q3"}
    assert [b["id"] for b in block["perimeter_breaks"]][-1] == "okey"
    assert block["source"]["kind"] == "facts" and "один винтаж" in block["source"]["pit_warning"]
    assert "первый зачёт — отчёт за 3 кв. 2026" in block["note"]
    table = retro.markdown_table(block)
    assert "тот же квартал год назад + сдвиг прошлого квартала г/г (главный)" in table
    assert block["interest"]["available"] is False


def test_the_store_databook_series_are_stronger_than_the_book_facts(tmp_path):
    store = Store(tmp_path / "state")
    for key, series_id in retro.DATABOOK_SERIES.items():
        values = {"revenue": 300.0, "ebitda": 21.0, "net_interest": 3.0}[key]
        store.upsert(series_id, [Point(period=q, value=values * (1 + 0.01 * i),
                                       fetched_at="2026-08-04T08:00:00+00:00")
                                 for i, q in enumerate(_quarters("2025Q1", "2026Q2"))])
    history = retro.quarterly_history(store=store, facts_dir=_facts_dir(tmp_path),
                                      as_of="2026-09-28")
    assert history["source"]["kind"] == "store"
    assert sorted(history["revenue"]) == _quarters("2025Q1", "2026Q2")
    assert history["margin"]["2025Q1"] == pytest.approx(0.07)
    # Точечно во времени: до получения датабука ряд был пуст.
    early = retro.quarterly_history(store=store, facts_dir=_facts_dir(tmp_path / "x"),
                                    as_of="2026-08-01")
    assert early["revenue"] == {}


# ------------------------------------------------------------- такт нау-каста


@pytest.fixture
def tact(tmp_path, monkeypatch):
    """Такт нау-каста на двойниках: книга, журнал, хранилище, ожидание ядра."""
    store = Store(tmp_path / "state")
    journal = Journal(tmp_path / "journal.sqlite")
    monkeypatch.setattr(collect, "anchor_facts", lambda: ANCHOR)
    halves = {f"{year}H{h}": ((690.0, 0.0620) if h == 1 else (740.0, 0.0700))
              for year in range(2026, 2029) for h in (1, 2)}
    expectation = (lambda A, q: quarterly.expected_quarter(A, q, grid=core_grid(rows=halves)))
    return store, journal, expectation


def test_the_nowcast_tact_records_the_quarter_its_half_and_the_revenue(tact):
    store, journal, expectation = tact
    today = date(2026, 9, 28)
    assert collect.cmd_nowcast(store, today=today, A=toy_book(), journal=journal,
                               expectation=expectation) == 0
    quarter = journal.latest_forecast(MARGIN, "2026Q3")
    assert quarter.value == pytest.approx(0.0700 - 0.00062)
    assert quarter.std_error == pytest.approx(0.007 * 2 ** 0.5)
    assert quarter.version == "lenta-margin-v1"
    half = journal.latest_forecast(MARGIN, "2026H2")
    assert half.value == pytest.approx(0.0700) and half.std_error == pytest.approx(0.007)
    revenue = journal.latest_forecast(REVENUE, "2026Q3")
    assert revenue.value == pytest.approx(740.0 * 0.457)

    naive = journal.naive(MARGIN, "2026Q3")
    assert naive[MODEL_EXPECTATION] == pytest.approx(quarter.value)
    guidance_half = (0.07 * (648.488059 + 740.0) - 39.279464) / 740.0
    assert naive[GUIDANCE] == pytest.approx(guidance_half - 0.00062)
    assert journal.naive(MARGIN, "2026H2")[GUIDANCE] == pytest.approx(guidance_half)

    # Повтор такта того же дня не плодит записей.
    assert collect.cmd_nowcast(store, today=today, A=toy_book(), journal=journal,
                               expectation=expectation) == 0
    assert len(journal.forecasts(MARGIN)) == 2
    # Отчёт о прогоне — «ок» для плашки выпуска.
    assert collect.read_collector_report(store) == {} or "nowcast" not in (
        collect.read_collector_report(store).get("sources") or {})


def test_a_frozen_benchmark_is_not_recomputed_when_the_databook_rewrites_history(tact):
    """В день отчёта датабук уже новый, а факт ещё не внесён: пересчитанный эталон
    разошёлся бы с замороженным. Первая запись — навсегда, тревоги нет."""
    store, journal, expectation = tact
    for i, q in enumerate(_quarters("2025Q2", "2026Q2")):
        store.upsert(retro.DATABOOK_SERIES["revenue"],
                     [Point(q, 300.0 + i, "2026-08-04T08:00:00+00:00")])
        store.upsert(retro.DATABOOK_SERIES["ebitda"],
                     [Point(q, 21.0 + 0.1 * i, "2026-08-04T08:00:00+00:00")])
    assert collect.cmd_nowcast(store, today=date(2026, 9, 28), A=toy_book(), journal=journal,
                               expectation=expectation) == 0
    frozen = journal.naive(MARGIN, "2026Q3")["yoy_plus_shift"]
    store.upsert(retro.DATABOOK_SERIES["ebitda"],
                 [Point("2025Q3", 30.0, "2026-10-29T08:00:00+00:00")])
    assert collect.cmd_nowcast(store, today=date(2026, 10, 29), A=toy_book(), journal=journal,
                               expectation=expectation) == 0
    assert journal.naive(MARGIN, "2026Q3")["yoy_plus_shift"] == frozen


def test_a_missed_fact_degrades_the_nowcast_into_a_flag(tact):
    """Окно отчёта закрылось, факт не внесён: код тревоги и строка для плашки."""
    store, journal, expectation = tact
    late = report_deadline("2026Q3") + timedelta(days=1)
    assert collect.cmd_nowcast(store, today=late, A=toy_book(), journal=journal,
                               expectation=expectation) == collect.ALARM_EXIT
    saved = collect.read_collector_report(store)["sources"]["nowcast"]
    assert "дисциплина журнала" in saved["status"]
    journal.record_actual(MARGIN, "2026Q3", 0.0715, reported_on=late)
    journal.record_actual(REVENUE, "2026Q3", 345.0, reported_on=late)
    assert collect.cmd_nowcast(store, today=late, A=toy_book(), journal=journal,
                               expectation=expectation) == 0
    assert journal.latest_forecast(MARGIN, "2026Q4") is not None
    assert "nowcast" not in collect.read_collector_report(store)["sources"]


def test_the_interest_benchmark_works_on_quarters(tmp_path):
    """«Прошлый квартал × отношение средних ставок» по квартальным фактам и ряду ключевой."""
    store = Store(tmp_path / "state")
    for i, q in enumerate(_quarters("2025Q1", "2026Q2")):
        store.upsert(retro.DATABOOK_SERIES["revenue"], [Point(q, 300.0, "2026-08-04T08:00")])
        store.upsert(retro.DATABOOK_SERIES["ebitda"], [Point(q, 21.0, "2026-08-04T08:00")])
        store.upsert(retro.DATABOOK_SERIES["net_interest"],
                     [Point(q, 3.0 + 0.1 * i, "2026-08-04T08:00")])
    day, points = date(2025, 7, 1), []
    while day <= date(2026, 9, 27):
        points.append(Point(day.isoformat(), 0.21 if day < date(2026, 4, 1) else 0.18,
                            "2026-09-28T08:00"))
        day += timedelta(days=1)
    store.upsert("cbr.key_rate", points)
    benchmark = collect._interest_benchmark(store, "2026Q2")
    q1 = 3.0 + 0.1 * 4
    assert benchmark == {"previous_period_scaled_by_rates": pytest.approx(q1 * 0.18 / 0.21)}
    half = collect._interest_benchmark(store, "2026H1")
    assert half["previous_period_scaled_by_rates"] == pytest.approx(
        (3.2 + 3.3) * ((0.21 * 90 + 0.18 * 91) / 181) / 0.21), "полугодие — сумма его кварталов"
