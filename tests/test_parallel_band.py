# -*- coding: utf-8 -*-
"""Пул процессов полосы и кэш медленных блоков на диске — те же числа.

Пул (`model.uncertainty.parallel_rows`) считает прогоны полосы в рабочих
процессах кусками по порядку точек: строки прогонов и медианы обязаны совпасть
с последовательным расчётом побайтно. Набор по умолчанию считает
последовательно (`tests/conftest.py`); здесь пул включён явно. Кэш на диске
(`model.payload.slow_cache`) работает только с `LENTA_SLOW_CACHE`.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from concurrent.futures.process import BrokenProcessPool

import pytest

from model import payload, uncertainty
from model.book import book

DRAWS = 64
WORKERS = 3
AUTO = max(1, min((os.cpu_count() or 1) - 1, uncertainty.MAX_WORKERS))


def _today() -> str:
    """«Сегодня» процесса, в котором вызвано (рабочий пула — через pickle по имени)."""
    return dt.date.today().isoformat()


@pytest.mark.needs_book
def test_the_pool_gives_the_same_draws_and_medians_bit_for_bit(monkeypatch, capsys):
    A = book()
    monkeypatch.setenv("LENTA_WORKERS", "1")
    rows, medians = uncertainty.band_rows(A, DRAWS), uncertainty.band_medians(A, DRAWS)
    monkeypatch.setenv("LENTA_WORKERS", str(WORKERS))
    pooled, pooled_medians = uncertainty.band_rows(A, DRAWS), uncertainty.band_medians(A, DRAWS)
    assert uncertainty._POOL["workers"] == WORKERS and not uncertainty._POOL["failed"]
    assert "недоступен" not in capsys.readouterr().err
    # json печатает repr числа: равные строки — равные биты (и −0,0 ≠ 0,0).
    assert pooled == rows and json.dumps(pooled) == json.dumps(rows)
    assert pooled_medians == medians and json.dumps(pooled_medians) == json.dumps(medians)
    # Рабочий видит ту же дату: при FAKE_TODAY — через sitecustomize в PYTHONPATH.
    assert uncertainty._executor(WORKERS).submit(_today).result() == _today()


@pytest.mark.needs_book
def test_a_broken_pool_falls_back_to_the_same_rows(monkeypatch, capsys):
    class Broken:
        def map(self, *args):
            raise BrokenProcessPool("проба")

    A, n = book(), uncertainty.PARALLEL_MIN_DRAWS
    monkeypatch.setenv("LENTA_WORKERS", "1")
    rows = uncertainty.band_rows(A, n)
    monkeypatch.setitem(uncertainty._POOL, "failed", False)
    monkeypatch.setattr(uncertainty, "_executor", lambda k: Broken())
    monkeypatch.setattr(uncertainty, "close_pool", lambda: None)
    monkeypatch.setenv("LENTA_WORKERS", str(WORKERS))
    assert json.dumps(uncertainty.band_rows(A, n)) == json.dumps(rows)
    assert "параллельный расчёт недоступен: BrokenProcessPool: проба, считаю последовательно" \
        in capsys.readouterr().err
    assert uncertainty._POOL["failed"], "сломанный пул не зовётся снова до конца процесса"


@pytest.mark.parametrize("raw, want", [("1", 1), ("3", 3), (" auto ", AUTO), ("", AUTO)])
def test_the_number_of_workers(monkeypatch, raw, want):
    monkeypatch.setenv("LENTA_WORKERS", raw)
    assert uncertainty.workers() == want


@pytest.mark.parametrize("raw", ["0", "-2", "два", "1.5"])
def test_a_bad_number_of_workers_is_refused(monkeypatch, raw):
    monkeypatch.setenv("LENTA_WORKERS", raw)
    with pytest.raises(ValueError, match="LENTA_WORKERS"):
        uncertainty.workers()


def test_the_suite_is_sequential_unless_a_test_asks(parallel_band):
    assert os.environ["LENTA_WORKERS"] == "1" and "LENTA_SLOW_CACHE" not in os.environ
    with parallel_band():
        assert uncertainty.workers() >= 1
    assert os.environ["LENTA_WORKERS"] == "1"


@pytest.mark.needs_book
def test_the_disk_cache_reads_what_it_wrote_and_misses_on_other_code(tmp_path, monkeypatch, capsys):
    calls = []

    def compute():
        calls.append(1)
        return dict(uncertainty=dict(draws=3, central={"0.50": 1350.25}), rows=[1.5, -0.0], note="полоса")

    key, folder = payload.book_digest(book()), tmp_path / "cache"
    assert payload.slow_cache(key, compute) == compute() and not folder.exists(), "без переменной — мимо диска"
    calls.clear()
    monkeypatch.setenv("LENTA_SLOW_CACHE", str(folder))
    first = payload.slow_cache(key, compute)
    again = payload.slow_cache(key, compute)
    assert calls == [1] and again == first and again is not first
    assert "медленные блоки — из кэша" in capsys.readouterr().err, "чтение с диска видно в журнале"
    assert json.dumps(again) == json.dumps(first)
    assert [p.suffix for p in folder.iterdir()] == [".pickle"], "временный файл остался"
    monkeypatch.setattr(payload, "code_digest", lambda: "другой код")
    payload.slow_cache(key, compute)
    assert calls == [1, 1], "другой код — промах"
    payload.slow_cache("другая книга", compute)
    assert calls == [1, 1, 1], "другая книга — промах"
