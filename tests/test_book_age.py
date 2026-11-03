# -*- coding: utf-8 -*-
"""Возраст книги — от даты её рыночных входов (решение ведущего 03.11.2026).

Плашка `book_update` говорит о свежести рыночных входов книги. Книга 1.2 сняла
миры на 28.10.2026 (`meta.curve_as_of`), а дата оценки и цена книги остались
18.09.2026: возраст от `meta.valuation_date` зажигал бы плашку «книге больше
45 дней» на свежих мирах. Теперь возраст — от `meta.curve_as_of`, если ключ
есть; книга без ключа считает как раньше — от `meta.valuation_date`. Дата цены
и суждений книги остаётся своей: «дата оценки не раньше даты книги» и «смена
карточки акции после даты книги» считаются от `meta.valuation_date`.
"""
from __future__ import annotations

import copy
import datetime as dt
from types import SimpleNamespace

import pytest

from indicators.store import Store
from model.book import book
from model.checks import check_book_is_current, check_security_card
from model.live import apply_live_inputs, book_market_date
from tests.test_ofz_in import _price

pytestmark = [pytest.mark.tact, pytest.mark.needs_book]


def _without_curve_date(A: dict) -> dict:
    B = copy.deepcopy(A)
    B["meta"].pop("curve_as_of", None)
    return B


def test_the_market_date_is_the_curve_date_and_without_the_key_the_valuation_date():
    A = {"meta": {"valuation_date": "2026-09-18", "curve_as_of": "2026-10-28"}}
    assert book_market_date(A) == "2026-10-28"
    assert book_market_date(_without_curve_date(A)) == "2026-09-18"


def test_the_age_of_a_book_with_a_curve_date_counts_from_it(tmp_path):
    A = book()
    store = Store(tmp_path / "state")
    _price(store, "2026-11-03")
    B, report = apply_live_inputs(A, store, today=dt.date(2026, 11, 3))
    assert report.book_date == A["meta"]["curve_as_of"] == "2026-10-28"
    assert report.book_age_days == 6
    assert report.book_valuation_date == A["meta"]["valuation_date"] == "2026-09-18"
    assert B["meta"]["curve_as_of"] == "2026-10-28", "кривые миров — книжные"
    assert B["meta"]["valuation_date"] == "2026-11-03", "дата оценки — дата котировки"
    assert report.as_dict()["book_valuation_date"] == "2026-09-18"


def test_the_age_of_a_book_without_the_key_counts_as_before(tmp_path):
    A = _without_curve_date(book())
    store = Store(tmp_path / "state")
    _price(store, "2026-11-03")
    B, report = apply_live_inputs(A, store, today=dt.date(2026, 11, 3))
    assert report.book_date == report.book_valuation_date == "2026-09-18"
    assert report.book_age_days == 46
    assert B["meta"]["curve_as_of"] == "2026-09-18", "без ключа кривые — на дату книги, как раньше"


def test_the_gate_counts_and_names_the_curve_date():
    A = book()
    young = SimpleNamespace(curve_shift={}, book_age_days=6, book_date="2026-10-28")
    assert check_book_is_current(A, young) == []
    old = SimpleNamespace(curve_shift={}, book_age_days=53, book_date="2026-10-28")
    [finding] = check_book_is_current(A, old)
    assert finding.key == "book_update" and "откалибрована 28.10.2026 — 53 дней назад" in finding.message


def test_the_listing_card_still_counts_from_the_valuation_date():
    """Смена карточки между датой цены книги и датой её кривых — после суждений книги:
    дисконт за управление версия 1.2 не пересматривала, гейт обязан гореть."""
    change = dict(series="moex.security.LENT.listlevel", field="LISTLEVEL", before=3.0,
                  after=2.0, since="2026-10-01", previous_day="2026-09-30")
    live = SimpleNamespace(book_date="2026-10-28", book_valuation_date="2026-09-18",
                           security={"listlevel": 2.0, "changes": [change]})
    assert [f.key for f in check_security_card(live)] == ["security_change"]
    live.book_valuation_date = "2026-10-02"
    assert check_security_card(live) == []
