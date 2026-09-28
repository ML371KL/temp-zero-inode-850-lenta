# -*- coding: utf-8 -*-
"""Синтетическая книга в схеме Ленты (`tests/fixtures/toy_book`) для тестов ядра.

До книги 1.0 общие тесты ядра идут на ней (DESIGN D17), и после — тоже: они
проверяют механику (тождества, закрытые формулы, отказы схемы), а не числа
компании, поэтому им нужна книга, в которой есть ВСЁ, что умеет ядро: сегменты
всех трёх режимов, метод `intrinsic`, строки моста с расчётом, корзины ставок,
лестница дивидендов, приобретённый периметр, квартальный слой.

Ядро диск не читает (`model.core.run_cell` — функция словаря книги), поэтому
тестам хватает словаря; каталог данных (`LENTA_DATA_DIR`) не подменяется.
"""
from __future__ import annotations

import copy
from functools import lru_cache
from pathlib import Path

TOY_DIR = Path(__file__).resolve().parent / "fixtures" / "toy_book"
TOY_BOOK = TOY_DIR / "assumptions.yaml"


@lru_cache(maxsize=1)
def _loaded() -> dict:
    from model.book import load_book

    return load_book(TOY_BOOK)


def toy_book() -> dict:
    """Проверенная синтетическая книга — свежая копия на каждый вызов."""
    return copy.deepcopy(_loaded())
