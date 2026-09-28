# -*- coding: utf-8 -*-
"""Регрессия книги: `results.json` и `run_output.txt` — выпуск ядра.

Таблицы книги выпускает ядро (`python -B -m model.book_results`), файлы
закоммичены рядом с книгой, и свежий расчёт обязан их воспроизвести:

* быстрые блоки — в каждом прогоне: числа до 1e-12 относительно (пол 1e-10 —
  библиотеки математики Windows и Linux расходятся в последних битах), строки,
  целые, ключи и их порядок — точно;
* полные таблицы (полоса, обратный DCF, заголовок, диагностики медианы) и
  печать строка в строку — `ci_only`: это тысячи сеток;
* поле `environment` (интерпретатор и платформа прогона, DESIGN D17) числом
  не сравнивается: канон — Python 3.12, платформа у автора книги и в CI разная.

Регрессия ловит любую правку числа и ничего не говорит о правильности:
мощность против ошибок методики — у аналитических тестов, мутаций и
независимой контрольной модели. Без книги в `data/` тест не собирается
(маркер `needs_book`).
"""
from __future__ import annotations

import json
import math

import pytest

pytestmark = pytest.mark.needs_book

REL = 1e-12
ABS = 1e-10
SLOW_KEYS = ("reverse_dcf", "uncertainty", "headline", "median_diagnostics")
# Не числа книги, а обстановка прогона: сверяется отдельно (версия Python).
ENVIRONMENT = "environment"
CANON_PYTHON = "3.12"


def differences(ref, new, path: str = "$") -> list[str]:
    """Расхождения двух деревьев JSON: числа — с допуском, остальное — точно."""
    if isinstance(ref, dict) and isinstance(new, dict):
        if list(ref) != list(new):
            return [f"{path}: ключи {list(ref)} против {list(new)}"]
        out: list[str] = []
        for key in ref:
            out += differences(ref[key], new[key], f"{path}.{key}")
        return out
    if isinstance(ref, list) and isinstance(new, list):
        if len(ref) != len(new):
            return [f"{path}: длина {len(ref)} против {len(new)}"]
        out = []
        for i, (a, b) in enumerate(zip(ref, new)):
            out += differences(a, b, f"{path}[{i}]")
        return out
    if (isinstance(ref, float) or isinstance(new, float)) and not isinstance(ref, bool) \
            and isinstance(ref, (int, float)) and isinstance(new, (int, float)):
        if ref == new or (math.isfinite(ref) and math.isfinite(new)
                          and abs(ref - new) <= max(ABS, REL * max(abs(ref), abs(new)))):
            return []
        return [f"{path}: {ref!r} против {new!r}"]
    return [] if (type(ref) is type(new) and ref == new) else [f"{path}: {ref!r} против {new!r}"]


def as_written(results: dict) -> dict:
    """Словарь так, как его пишет `model.book_results.write` (JSON-круг), без
    обстановки прогона."""
    out = json.loads(json.dumps(results, ensure_ascii=False, default=str))
    out.pop(ENVIRONMENT, None)
    return out


@pytest.fixture(scope="module")
def committed() -> dict:
    from model.paths import BOOK_DIR

    return json.loads((BOOK_DIR / "results.json").read_text(encoding="utf-8"))


def test_the_committed_tables_are_from_the_canonical_python(committed):
    """Канон чисел книги — Python 3.12 (DESIGN D1, D17): таблицы, посчитанные
    другим интерпретатором, в репозиторий не кладутся."""
    env = committed[ENVIRONMENT]
    assert env["python"].rsplit(".", 1)[0] == CANON_PYTHON, env
    assert env["implementation"] == "CPython" and env["platform"], env


def test_the_fast_blocks_reproduce_the_committed_tables(committed):
    from model.book import book
    from model.book_results import book_results

    fresh = as_written(book_results(book(), slow=False))
    reference = {k: v for k, v in committed.items() if k not in (*SLOW_KEYS, ENVIRONMENT)}
    found = differences(reference, fresh)
    assert not found, "results.json расходится со свежим расчётом ядра:\n" + "\n".join(found[:20])


@pytest.mark.ci_only
def test_the_full_tables_and_the_print_reproduce(committed, parallel_band):
    from model.book import book
    from model.book_results import book_results, render_run_output
    from model.paths import BOOK_DIR

    A = book()
    with parallel_band():
        fresh = as_written(book_results(A))
    reference = {k: v for k, v in committed.items() if k != ENVIRONMENT}
    found = differences(reference, fresh)
    assert not found, "results.json расходится со свежим расчётом ядра:\n" + "\n".join(found[:20])
    printed = (BOOK_DIR / "run_output.txt").read_text(encoding="utf-8")
    assert render_run_output(fresh, A).splitlines() == printed.splitlines()
