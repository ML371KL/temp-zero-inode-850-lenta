# -*- coding: utf-8 -*-
"""Книга 1.1 против 1.0: ключи версии 1.0 воспроизводят её числа, прогулка
`evidence/book-1.1/walk/` начинается в книге 1.0 и кончается в таблицах книги.

Версия 1.1 суждений не меняет: она включает четыре правила расчёта по внешнему
аудиту 30.09.2026 ключами книги (`valuation.uncertainty.axis_merge`,
`valuation.terminal.da_convention`, `.boundary_levels`, `.tax_floor`). Обещание
версии — «книга 1.0 воспроизводится бит в бит ключами по умолчанию» — здесь
проверяется расчётом, а не словами журнала: та же книга с ключами версии 1.0
даёт точку и медиану таблиц книги 1.0 (тег `book-1.0`).

С книги 1.2 текущая книга стоит на мирах 28.10.2026: миры книг 1.0 и 1.1
(запись 18.09.2026) подставляются из листа `evidence/book-1.2/walk/worlds-1.1.yaml`
(`tests/test_book12_walk.py` проверяет, что так собирается книга 1.1 целиком),
а конец прогулки сверяется с таблицами книги 1.1 — числами ниже.
"""
from __future__ import annotations

import copy
import json

import pytest
import yaml

from model.book import book, validate_book
from model.paths import BOOK_DIR
from model.uncertainty import evaluate, uncertainty

pytestmark = pytest.mark.needs_book

WALK = BOOK_DIR / "evidence" / "book-1.1" / "walk" / "out" / "walk.json"
WORLDS_1_1 = BOOK_DIR / "evidence" / "book-1.2" / "walk" / "worlds-1.1.yaml"
# Допуск регрессии книги: библиотеки математики Windows и Linux расходятся в
# последних битах (`tests/test_book_regression.py`).
REL = 1e-12
# `results.json` книги 1.0 (тег `book-1.0`): точка по оси ставок и медиана полосы.
BOOK_1_0_POINT = (3172.193861874779, 3270.502314484846, 3368.810767094913)
BOOK_1_0_MEDIAN = 3130.6253318118447
# `results.json` книги 1.1 (тег `book-1.1`): точка, медиана и полосы — конец прогулки 1.0 → 1.1.
BOOK_1_1_POINT = (3175.3891026072533, 3271.489863631813, 3367.590624656373)
BOOK_1_1_MEDIAN = 3125.3684366894713
BOOK_1_1_BAND = (2604.4651445088234, 3669.985059963579)
BOOK_1_1_INNER = (2858.8750239777837, 3413.0550913381207)


def with_the_worlds_of_1_1(A: dict) -> dict:
    """Книга на мирах книг 1.0 и 1.1 (запись 18.09.2026): блок `worlds` листа,
    дата кривых — дата оценки книги (ключа `meta.curve_as_of` в 1.0 и 1.1 нет)."""
    B = copy.deepcopy(A)
    B["worlds"] = yaml.safe_load(WORLDS_1_1.read_text(encoding="utf-8"))["worlds"]
    B["meta"].pop("curve_as_of", None)
    validate_book(B)
    return B


def with_the_rules_of_1_0(A: dict) -> dict:
    """Книга с ключами версии 1.0: три ключа сняты (умолчания ядра), D&A терминала —
    `cohort_runoff`, как стояло в книге 1.0."""
    B = copy.deepcopy(A)
    B["valuation"]["uncertainty"].pop("axis_merge")
    terminal = B["valuation"]["terminal"]
    terminal.pop("boundary_levels")
    terminal.pop("tax_floor")
    terminal["da_convention"] = "cohort_runoff"
    validate_book(B)
    return B


@pytest.fixture(scope="module")
def walk() -> dict:
    return json.loads(WALK.read_text(encoding="utf-8"))


def test_the_keys_of_1_0_reproduce_the_point_of_book_1_0(walk):
    fv = evaluate(with_the_rules_of_1_0(with_the_worlds_of_1_1(book())))[3]
    assert (fv.low, fv.central, fv.high) == pytest.approx(BOOK_1_0_POINT, rel=REL)
    start = walk["rows"][0]
    assert (start["low"], start["central"], start["high"]) == pytest.approx(BOOK_1_0_POINT, rel=REL), (
        "прогулка начинается в книге 1.0")
    assert start["median"] == pytest.approx(BOOK_1_0_MEDIAN, rel=REL)


@pytest.mark.ci_only
def test_the_keys_of_1_0_reproduce_the_median_of_book_1_0():
    """Полоса на 2 000 прогонов — десятки секунд: в CI, не в такте сервера."""
    band = uncertainty(with_the_rules_of_1_0(with_the_worlds_of_1_1(book())))
    assert band["central"]["0.50"] == pytest.approx(BOOK_1_0_MEDIAN, rel=REL)


def test_the_walk_ends_in_the_tables_of_book_1_1(walk):
    """Последний шаг прогулки — таблицы книги 1.1 (`results.json` тега `book-1.1`),
    обе проверки прогулки — «ДА»; шаги — четыре ключа и номер версии."""
    assert walk["consistent"] and walk["same_book"]
    last = walk["rows"][-1]
    assert (last["low"], last["central"], last["high"]) == pytest.approx(BOOK_1_1_POINT, rel=REL)
    assert last["median"] == pytest.approx(BOOK_1_1_MEDIAN, rel=REL)
    assert (last["p10"], last["p90"]) == pytest.approx(BOOK_1_1_BAND, rel=REL)
    assert (last["p25"], last["p75"]) == pytest.approx(BOOK_1_1_INNER, rel=REL)
    assert len(walk["rows"]) == 6, "старт, номер версии и четыре правила"


def test_the_walk_splits_the_effect_by_the_four_rules(walk):
    """Раскладка журнала версии: номер версии чисел не меняет, сложение осей не
    меняет точку и расширяет полосу, три правила терминала сдвигают точку на
    +4,52 / −2,48 / −1,05 ₽."""
    rows = walk["rows"]
    step = [{k: b[k] - a[k] for k in ("central", "median", "p10", "p90")} for a, b in zip(rows, rows[1:])]
    version, merge, cohort, boundary, floor = step
    assert version == {"central": 0.0, "median": 0.0, "p10": 0.0, "p90": 0.0}
    assert merge["central"] == 0.0, "оси полосы точку не трогают"
    assert merge["p10"] < -40 and merge["p90"] > 10 and -7 < merge["median"] < -5
    assert cohort["central"] == pytest.approx(4.52, abs=0.005)
    assert boundary["central"] == pytest.approx(-2.48, abs=0.005)
    assert floor["central"] == pytest.approx(-1.05, abs=0.005)
    assert rows[-1]["central"] - rows[0]["central"] == pytest.approx(0.99, abs=0.005)
    assert rows[-1]["median"] - rows[0]["median"] == pytest.approx(-5.26, abs=0.005)
