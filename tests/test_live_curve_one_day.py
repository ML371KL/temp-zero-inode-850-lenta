# -*- coding: utf-8 -*-
"""Наблюдаемая кривая — узлы одного дня (внешний аудит 30.09.2026, T06).

Сборщик пишет каждый узел кривой отдельным рядом, и узел, не пришедший в ответе,
остаётся в состоянии со старой датой. Выпуск брал `latest()` каждого ряда и
датировал кривую самым свежим узлом: минимум узлов и возраст проверялись по
одному узлу, а смесь дней уходила в `live.observed_curve` без плашки — гейт
обновления книги молчал при движении тех сроков, которые не пришли.

Фикстуры — `tests/fixtures/ofz_in/`; в сеть тесты не ходят.
"""
from __future__ import annotations

import copy
import datetime as dt
import json

import pytest

from indicators import sources
from indicators.http import Response
from indicators.store import Store
from model.book import book
from model.live import apply_live_inputs
from tests.test_ofz_in import FIX, FRIDAY, _price

pytestmark = [pytest.mark.tact, pytest.mark.needs_book]
MONDAY = "2026-09-21"


def _collect(monkeypatch, store: Store, *, day: str, drop=(), shift: float = 0.0) -> None:
    """Сбор кривой штатным сборщиком: ответ ISS за `day` без сроков `drop`, все
    доходности сдвинуты на `shift` (в процентных пунктах, как отдаёт биржа)."""
    body = copy.deepcopy(json.loads((FIX / "zcyc_2026-09-18.json").read_text(encoding="utf-8")))
    body["yearyields"]["data"] = [[day, row[1], row[2], row[3] + shift, *row[4:]]
                                  for row in body["yearyields"]["data"] if row[2] not in drop]
    linkers = (FIX / "iss_ofz_in_2026-09-20.json").read_bytes()

    def fetch(url, *, sink=None, name=None, **_):
        reply = Response(url=url, status=200,
                         body=json.dumps(body).encode() if "zcyc" in url else linkers,
                         fetched_at=f"{day}T16:00:00+00:00", headers={})
        if sink is not None and name:
            sink.keep(name, reply)
        return reply

    monkeypatch.setattr(sources, "fetch", fetch)
    assert sources.run_collectors(["moex_curve"], store)["moex_curve"].startswith("ок")


def _live(store: Store):
    _price(store)
    return apply_live_inputs(book(), store, today=dt.date(2026, 9, 21))[1]


def test_a_full_answer_of_the_new_day_is_the_new_curve(monkeypatch, tmp_path):
    """Контроль: полный ответ следующего дня — кривая этого дня, без плашки."""
    store = Store(tmp_path / "state")
    _collect(monkeypatch, store, day=FRIDAY)
    _collect(monkeypatch, store, day=MONDAY, shift=0.2)
    report = _live(store)
    assert report.observed_curve_date == MONDAY and len(report.observed_curve) >= 8
    assert not any("разных дней" in line for line in report.degraded), report.degraded


def test_a_partial_answer_does_not_carry_old_nodes(monkeypatch, tmp_path):
    """Ответ понедельника без сроков 1, 3 и 5 лет: прежде — 11 узлов с датой
    понедельника и пятничными 1/3/5; теперь кривая не принята, плашка называет
    застрявшие сроки."""
    store = Store(tmp_path / "state")
    _collect(monkeypatch, store, day=FRIDAY)
    _collect(monkeypatch, store, day=MONDAY, drop=(1.0, 3.0, 5.0), shift=0.2)
    report = _live(store)
    assert report.observed_curve == {} and report.observed_curve_date == ""
    line = next(line for line in report.degraded if "разных дней" in line)
    assert "1y (2026-09-18)" in line and "5y (2026-09-18)" in line and MONDAY in line


def test_two_fresh_nodes_are_not_a_curve(monkeypatch, tmp_path):
    """Пришли только два коротких срока: девять узлов прошлой недели не выдаются
    за сегодняшнюю кривую — минимум узлов считается по узлам одного дня."""
    store = Store(tmp_path / "state")
    _collect(monkeypatch, store, day=FRIDAY)
    _collect(monkeypatch, store, day=MONDAY,
             drop=(0.75, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0, 15.0, 20.0))
    report = _live(store)
    assert report.observed_curve == {} and report.observed_curve_date == ""
    assert any("разных дней" in line for line in report.degraded), report.degraded
