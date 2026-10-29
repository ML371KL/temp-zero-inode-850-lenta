# -*- coding: utf-8 -*-
"""Книга 1.2 против 1.1: миры книги 1.1 дают её числа, прогулка
`evidence/book-1.2/walk/` начинается в книге 1.1 и кончается в таблицах книги.

Версия 1.2 меняет только миры ставок: общая запись 28.10.2026 после решения
Банка России 23.10.2026 (`worlds_source.json`, sha256 1e905cb7…) и дата её
кривых `meta.curve_as_of`; суждения, факты, цена и мост — книги 1.1. Обещание
версии — «один источник сдвига» — проверяется расчётом: та же книга с блоком
миров книги 1.1 (лист `worlds-1.1.yaml`) и без даты кривых — книга 1.1 целиком
(точка и медиана её таблиц), а прогулка раскладывает сдвиг на мир M и миры N и H.
"""
from __future__ import annotations

import copy
import json

import pytest
import yaml

from model.book import book, validate_book
from model.paths import BOOK_DIR
from model.uncertainty import evaluate, uncertainty
from tests.test_book11_walk import (BOOK_1_1_BAND, BOOK_1_1_INNER, BOOK_1_1_MEDIAN, BOOK_1_1_POINT, REL,
                                    with_the_worlds_of_1_1)

pytestmark = pytest.mark.needs_book

WALK = BOOK_DIR / "evidence" / "book-1.2" / "walk" / "out" / "walk.json"
RECORD = BOOK_DIR / "worlds_source.json"


@pytest.fixture(scope="module")
def walk() -> dict:
    return json.loads(WALK.read_text(encoding="utf-8"))


def test_the_book_is_1_1_but_for_the_worlds_and_their_curve_date():
    """Книга 1.2 с мирами 1.1, без `meta.curve_as_of` и с номером 1.1 даёт точку
    таблиц книги 1.1 бит в бит: других различий у версий нет (лист прогулки
    сверяет и словарь книги целиком — «книга после шагов = конечная книга»)."""
    A = book()
    assert A["meta"]["version"] == "1.2"
    B = with_the_worlds_of_1_1(A)
    B["meta"]["version"] = "1.1"
    validate_book(B)
    fv = evaluate(B)[3]
    assert (fv.low, fv.central, fv.high) == pytest.approx(BOOK_1_1_POINT, rel=REL)


@pytest.mark.ci_only
def test_the_worlds_of_1_1_give_the_median_of_book_1_1():
    """Полоса на 2 000 прогонов — десятки секунд: в CI, не в такте сервера."""
    band = uncertainty(with_the_worlds_of_1_1(book()))
    assert band["central"]["0.50"] == pytest.approx(BOOK_1_1_MEDIAN, rel=REL)


def test_the_curve_date_and_the_terminal_level_come_from_the_record():
    """Дата кривых книги — дата снимка записи (иначе ядро катило бы кривые окна
    по форвардам от 18.09); уровень кривой в терминале всех миров — блок
    `curve_lt` записи (без него N/H остались бы на 9,0 / 12,7)."""
    A = book()
    record = json.loads(RECORD.read_text(encoding="utf-8"))
    assert A["meta"]["curve_as_of"] == record["m_inflation"]["curve_date"] == record["asof"] == "2026-10-28"
    assert A["meta"]["valuation_date"] == "2026-09-18", "цена и дата оценки книги — версии 1.1"
    for world in ("N", "H", "M"):
        assert A["worlds"][world]["zero_curve"]["LT"] == pytest.approx(record["curve_lt"][world] / 100.0, abs=1e-12)
        assert A["worlds"][world]["lt"]["inflation"] == pytest.approx(record["lt_inflation"][world] / 100.0, abs=1e-12)
    assert record["curve_lt"] == {"N": 8.9, "H": 12.6, "M": 16.7}


def test_the_walk_starts_in_book_1_1_and_ends_in_the_tables_of_the_book(walk):
    """Старт прогулки — книга 1.1 (таблицы тега `book-1.1`), последний шаг —
    таблицы книги 1.2 (`results.json`); обе проверки прогулки — «ДА»."""
    results = json.loads((BOOK_DIR / "results.json").read_text(encoding="utf-8"))
    assert results["book_version"] == "1.2"
    assert walk["consistent"] and walk["same_book"]
    start, last = walk["rows"][0], walk["rows"][-1]
    assert (start["low"], start["central"], start["high"]) == pytest.approx(BOOK_1_1_POINT, rel=REL)
    assert start["median"] == pytest.approx(BOOK_1_1_MEDIAN, rel=REL)
    assert (start["p10"], start["p90"]) == pytest.approx(BOOK_1_1_BAND, rel=REL)
    assert (start["p25"], start["p75"]) == pytest.approx(BOOK_1_1_INNER, rel=REL)
    fair, headline = results["fair_value"], results["headline"]
    assert (last["low"], last["central"], last["high"]) == pytest.approx(
        (fair["low"], fair["central"], fair["high"]), rel=REL)
    assert last["median"] == pytest.approx(headline["central"], rel=REL)
    assert (last["p10"], last["p90"]) == pytest.approx(tuple(headline["band"]), rel=REL)
    assert (last["p25"], last["p75"]) == pytest.approx(tuple(headline["inner"]), rel=REL)
    assert len(walk["rows"]) == 4, "старт, номер версии, мир M, миры N и H"


def test_the_walk_splits_the_worlds_step_into_m_and_n_h(walk):
    """Номер версии чисел не меняет; низ оси ставок — слой «рыночные ставки как
    есть» (мир M целиком) — двигает только шаг мира M, а шаг миров N и H
    сдвигает только верх («свой макро-взгляд»)."""
    start, version, world_m, worlds_nh = walk["rows"]
    for key in ("low", "central", "high", "median", "p10", "p90"):
        assert version[key] == start[key], key
    assert world_m["low"] != start["low"]
    assert worlds_nh["low"] == pytest.approx(world_m["low"], rel=REL), "миры N и H низ не трогают"
    assert worlds_nh["high"] != world_m["high"]
