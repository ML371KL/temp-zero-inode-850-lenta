# -*- coding: utf-8 -*-
"""Реальные доходности ОФЗ-ИН: дата торгов в сборщике и наблюдение в выпуске.

ЗАЧЕМ. Книга 1.4 строит инфляцию мира M по форвардным BEI: номинальные
форварды кривой ОФЗ против реальных форвардов ОФЗ-ИН 52002–52005, и рецепт
миров отказывается смешивать даты (`worlds_recipe.py`, защита по дате). А
сборщик датировал доходности днём СЪЁМА (`UTC_TODAY()`): съём в воскресенье
20.09 записал «2026-09-20» пятничный LAST 18.09 (проверяющий листа worlds,
VERIFY §3.3), ранний такт сбора (06:10 МСК) — вчерашние сделки сегодняшним
числом. С такой датой пересборка миров после заседания ЦБ 23.10.2026 не
сходилась бы с кривой никогда или сходилась бы с чужим днём.

Здесь проверяется: (1) сборщик датирует точку днём сделки из ответа ISS
(`marketdata_yields.TRADEMOMENT`), а без даты сделки точку не пишет;
(2) выпуск кладёт в `live.observed_ofz_in` доходности ровно на дату принятой
кривой — по каждому выпуску отдельно, с причиной для недостающего, и не
поднимает плашку деградации (в оценку они не входят).

Фикстуры — `tests/fixtures/ofz_in/` (README там же). В сеть тесты не ходят:
`sources.fetch` подменён ответами из фикстур.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from indicators import issuer
import yaml

from indicators import sources
from indicators.http import Response
from indicators.store import Point, Store
from model.book import book
from model.live import OFZ_IN_PREFIX, apply_live_inputs

# Такт (ops/run.sh, TACT_TESTS): слой индикаторов на фикстурах и двойниках —
# быстрый, в сеть не ходит (тесты `network` такт исключает выражением).
pytestmark = pytest.mark.tact

ROOT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).parent / "fixtures" / "ofz_in"
# Съём в воскресенье вечером: так лежит в состоянии точка «2026-09-20».
SUNDAY = "2026-09-20T19:15:22+00:00"
FRIDAY = "2026-09-18"
# Записи книги 1.4 (`worlds_inputs.yaml`, `ofz_in.bonds`) — они же в фикстуре.
LAST_18_09 = {"SU52002RMFS1": 0.0878, "SU52003RMFS9": 0.072,
              "SU52004RMFS7": 0.0699, "SU52005RMFS4": 0.0631}


def iss(monkeypatch, *, ofz_in: bytes | None = None, fetched_at: str = SUNDAY) -> list[str]:
    """`sources.fetch` отвечает фикстурами ISS; возвращает список запрошенных адресов."""
    pages = {"zcyc": (FIX / "zcyc_2026-09-18.json").read_bytes(),
             "TQOB": ofz_in if ofz_in is not None else (FIX / "iss_ofz_in_2026-09-20.json").read_bytes()}
    asked: list[str] = []

    def fetch(url, *, sink=None, name=None, **_):
        asked.append(url)
        reply = Response(url=url, status=200, body=pages["zcyc" if "zcyc" in url else "TQOB"],
                         fetched_at=fetched_at, headers={})
        if sink is not None and name:
            sink.keep(name, reply)
        return reply

    monkeypatch.setattr(sources, "fetch", fetch)
    return asked


def collected(monkeypatch, tmp_path, **kw) -> Store:
    store = Store(tmp_path / "state")
    iss(monkeypatch, **kw)
    report = sources.run_collectors(["moex_curve"], store)
    assert report["moex_curve"].startswith("ок"), report
    return store


def _price(store: Store, day: str = "2026-09-21") -> None:
    store.upsert(issuer.PRICE_SERIES, [Point(period=day, value=float(book()["market"]["price"]), fetched_at=day + "T16:00:00+00:00")],
                 unit="RUB", cadence="ежедневно", label=issuer.TICKER, channel=1)


# ------------------------------------------------ (1) сборщик: дата торгов


def test_the_collector_dates_linkers_by_the_trade_not_by_the_fetch(monkeypatch, tmp_path):
    """Воскресный съём пятничных сделок пишет пятницу — ту же дату, что у кривой."""
    store = Store(tmp_path / "state")
    asked = iss(monkeypatch)
    assert sources.run_collectors(["moex_curve"], store)["moex_curve"].startswith("ок")
    linkers = next(url for url in asked if "TQOB" in url)
    assert "marketdata_yields.columns=SECID,TRADEMOMENT" in linkers
    for secid, value in LAST_18_09.items():
        points = store.load(OFZ_IN_PREFIX + secid).points
        assert [p.period for p in points] == [FRIDAY], secid
        assert points[0].value == pytest.approx(value, abs=1e-12)
        assert points[0].fetched_at == SUNDAY
        assert points[0].note.startswith(f"сделка {FRIDAY} 17:")
    curve_days = {p.period for s in store.all_series() if s.id.startswith("moex.zcyc.") for p in s.points}
    assert curve_days == {FRIDAY}, "кривая и линкеры одного дня"


def test_a_linker_without_a_trade_date_is_not_written_at_all():
    """Без даты сделки точки нет: дата съёма на её месте и была ошибкой.

    Ответ старого вида (без блока `marketdata_yields`), пустой момент сделки,
    мусор вместо даты и нулевая доходность («сделки не было») — всё мимо ряда,
    с причиной по выпуску.
    """
    payload = {"marketdata": {"columns": ["SECID", "YIELD"],
                              "data": [["SU52002RMFS1", 8.78], ["SU52003RMFS9", 7.2],
                                       ["SU52004RMFS7", 0], ["SU52005RMFS4", None]]},
               "marketdata_yields": {"columns": ["SECID", "TRADEMOMENT"],
                                     "data": [["SU52002RMFS1", None], ["SU52003RMFS9", "18:05"],
                                              ["SU52004RMFS7", "2026-09-18 17:21:10"],
                                              ["SU52005RMFS4", "2026-09-18 17:21:29"]]}}
    series, skipped = sources.ofz_in_series(payload, fetched_at=SUNDAY, sha256="x")
    assert series == {}
    assert skipped == ["SU52002RMFS1: нет даты сделки (TRADEMOMENT пуст)",
                       "SU52003RMFS9: нет даты сделки (18:05)",
                       "SU52004RMFS7: нет доходности сделки",
                       "SU52005RMFS4: нет доходности сделки"]
    old = {"marketdata": payload["marketdata"]}
    assert sources.ofz_in_series(old, fetched_at=SUNDAY, sha256="x")[0] == {}


@pytest.mark.needs_book
def test_the_collector_covers_every_linker_of_the_worlds_recipe():
    """Рецепту 1.4 нужны ВСЕ выпуски входов: без одного π_ss мира M другое.

    Без 52005 π_ss 9,1 вместо 9,8 (лист worlds, раздел 3.6) — выпуск, который
    аудитор добавит во входы, обязан собираться.
    """
    inputs = yaml.safe_load((ROOT / "data" / "assumptions" / "worlds_inputs.yaml").read_text(encoding="utf-8"))
    recipe = {str(b["secid"]) for b in inputs["ofz_in"]["bonds"]}
    assert recipe <= {secid[2:7] for secid in sources.OFZ_IN_SECIDS}
    assert all(secid.startswith("SU52") and secid[7:11] == "RMFS" for secid in sources.OFZ_IN_SECIDS)


# ------------------------------------------ (2) выпуск: дата кривой, поштучно


@pytest.mark.needs_book
def test_the_release_carries_the_linkers_of_the_curve_date(monkeypatch, tmp_path):
    """Кривая и линкеры одного дня — в выпуске; плашки деградации нет."""
    store = collected(monkeypatch, tmp_path)
    _price(store)
    _, report = apply_live_inputs(book(), store, today=dt.date(2026, 9, 21))
    assert report.observed_curve_date == FRIDAY
    assert report.observed_ofz_in == pytest.approx(LAST_18_09, abs=1e-12)
    assert report.observed_ofz_in_date == FRIDAY
    assert report.observed_ofz_in_missing == {}
    assert not report.degraded, report.degraded
    assert report.applied["ofz_in_observed"].startswith(f"4 из 4 выпусков ОФЗ-ИН на {FRIDAY}")
    live = json.loads(json.dumps(report.as_dict()))
    assert live["observed_ofz_in"] == report.observed_ofz_in
    assert live["observed_ofz_in_date"] == live["observed_curve_date"] == FRIDAY


@pytest.mark.needs_book
def test_each_linker_degrades_on_its_own(monkeypatch, tmp_path):
    """Один выпуск без сделки в день кривой, другой в процентах — остальные в выпуске.

    Ближайший общий день не подбирается: доходность четверга при кривой
    пятницы — смешанный мир (VERIFY листа worlds, §3.1). Выпуск помечает
    причину по выпуску, в `live.degraded` ничего: оценку это не трогает.
    """
    body = json.loads((FIX / "iss_ofz_in_2026-09-20.json").read_text(encoding="utf-8"))
    moments = body["marketdata_yields"]["data"]
    moments[0][1] = "2026-09-17 18:39:59"                     # 52002 в пятницу не торговался
    body["marketdata"]["data"][3][1] = 631.0                  # 52005: доходность ×100
    store = collected(monkeypatch, tmp_path, ofz_in=json.dumps(body).encode())
    _price(store)
    _, report = apply_live_inputs(book(), store, today=dt.date(2026, 9, 21))
    assert set(report.observed_ofz_in) == {"SU52003RMFS9", "SU52004RMFS7"}
    assert report.observed_ofz_in_date == FRIDAY
    assert report.observed_ofz_in_missing == {
        "SU52002RMFS1": f"нет сделки на дату кривой {FRIDAY} (последняя точка — 2026-09-17)",
        "SU52005RMFS4": "доходность 6.31 вне коридора 0.5%–20% — единицы или мусор"}
    assert not report.degraded
    assert report.applied["ofz_in_observed"].startswith("2 из 4")


@pytest.mark.needs_book
def test_a_point_dated_by_the_fetch_day_does_not_match_the_curve(tmp_path):
    """Точка прежнего сборщика («2026-09-20» за пятницу) с кривой 18.09 не сходится.

    Так лежит состояние на сервере до этой правки: старые точки выпуск не
    подхватывает, пересчитывать их не нужно. Из двух версий одного дня
    берётся полученная последней (утренняя внутридневная против вечерней).
    """
    store = Store(tmp_path)
    for tenor, value in {0.25: 0.125, 0.5: 0.128, 0.75: 0.132, 1: 0.136, 2: 0.148,
                         3: 0.155, 5: 0.161, 10: 0.165}.items():
        store.upsert(f"moex.zcyc.{tenor:g}y", [Point(period=FRIDAY, value=value, fetched_at=SUNDAY)])
    store.upsert(OFZ_IN_PREFIX + "SU52005RMFS4", [Point(period="2026-09-20", value=0.0631, fetched_at=SUNDAY)])
    store.upsert(OFZ_IN_PREFIX + "SU52004RMFS7", [
        Point(period=FRIDAY, value=0.0712, fetched_at="2026-09-18T08:30:55+00:00"),
        Point(period=FRIDAY, value=0.0699, fetched_at="2026-09-18T16:45:00+00:00")])
    _, report = apply_live_inputs(book(), store, today=dt.date(2026, 9, 21))
    assert report.observed_ofz_in == {"SU52004RMFS7": 0.0699}
    assert report.observed_ofz_in_missing["SU52005RMFS4"] == (
        f"нет сделки на дату кривой {FRIDAY} (последняя точка — 2026-09-20)")
    assert report.observed_ofz_in_missing["SU52002RMFS1"] == "ряд не собран"


@pytest.mark.needs_book
def test_without_an_accepted_curve_there_is_no_linker_date(monkeypatch, tmp_path):
    """Кривая не принята — дату доходностей сверить не с чем: блок пуст с причиной."""
    store = Store(tmp_path / "state")
    iss(monkeypatch)
    sources.run_collectors(["moex_curve"], store)
    for series in store.all_series():
        if series.id.startswith("moex.zcyc."):
            store.path_for(series.id).unlink()
    _price(store)
    _, report = apply_live_inputs(book(), store, today=dt.date(2026, 9, 21))
    assert report.observed_curve == {} and report.observed_ofz_in == {}
    assert report.observed_ofz_in_date == ""
    assert set(report.observed_ofz_in_missing) == set(sources.OFZ_IN_SECIDS)
    assert all("кривая дня не принята" in why for why in report.observed_ofz_in_missing.values())
    assert "ofz_in_observed" not in report.applied
