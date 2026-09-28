# -*- coding: utf-8 -*-
"""В коде модели нет литералов года: годы правил — ключи книги.

У 850oa в ядре жили два литерала года (`year == 2026` в сезонности маржи,
`year <= 2027` у ставки действующего фиксированного долга). Тест на литералы
чисел книги (`test_no_literals.py`) целые меньше 10 000 не проверяет, поэтому
они прошли — а после перезаякоривания на другой год правила молча перестали
бы соответствовать книге. Теперь это ключи `margin.season_from_period` и
`financing.rate_baskets[].until`, а здесь — сторож: ни одного целого
2000…2100 в `model/*.py` вне комментариев и строк (разбор AST: комментарии и
строки, включая докстроки, целыми константами не бывают).

Книга тесту не нужна.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Такт (ops/run.sh, TACT_TESTS): сканер литералов годов в model/ — быстрый.
pytestmark = pytest.mark.tact

ROOT = Path(__file__).resolve().parents[1]
YEARS = range(2000, 2101)


def year_literals(source: str) -> list[tuple[int, int]]:
    """(строка, значение) каждого целого-года в исходном тексте."""
    out = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and type(node.value) is int and node.value in YEARS:
            out.append((node.lineno, node.value))
    return out


def test_the_model_has_no_year_literals():
    found = {}
    for path in sorted((ROOT / "model").glob("*.py")):
        hits = year_literals(path.read_text(encoding="utf-8"))
        if hits:
            found[path.name] = hits
    assert not found, (
        f"литералы года в model/*.py: {found} — год правила обязан быть ключом книги "
        "(как margin.season_from_period, financing.rate_baskets[].until)")


def test_the_scanner_sees_a_year_but_not_comments_or_strings():
    """Мощность сторожа: целое-год ловится, тот же год в комментарии, строке и
    докстроке — нет, как и логическое значение и дробное число."""
    source = (
        '"""Докстрока про 2026 год."""\n'
        "x = 1 if year <= 2027 else 2   # комментарий 2028\n"
        "label = '2029'\n"
        "flag, share = True, 2030.5\n"
    )
    assert year_literals(source) == [(2, 2027)]
