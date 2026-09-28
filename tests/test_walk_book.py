# -*- coding: utf-8 -*-
"""Прогулка версий книги (`ops/tools/walk_book.py`) на книге 1.5.

Две подмены — ключ и возврат к значению конечной книги: последний шаг обязан
совпасть с прямым расчётом бит в бит, книга после шагов — с конечной книгой;
прогулка без возврата — код 1.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml

from model.book import BOOK_YAML
from model.engine import central_price, with_overrides

# Инструмент оператора (прогулка версий книги): такт его не вызывает. Гоняется
# в CI.
pytestmark = pytest.mark.ci_only

ROOT = Path(__file__).resolve().parents[1]
STEPS = [{"name": "β_u = 0,9", "set": {"valuation.beta_u": 0.9}},
         {"name": "β_u — книги", "from_end": ["valuation.beta_u"]}]


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("walk_book_tool", ROOT / "ops" / "tools" / "walk_book.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _steps(tmp_path: Path, steps: list[dict]) -> Path:
    path = tmp_path / "steps.yaml"
    path.write_text(yaml.safe_dump(steps, allow_unicode=True), encoding="utf-8")
    return path


@pytest.mark.needs_book
def test_a_substitution_and_its_return_walk_back_to_the_book(tool, tmp_path, capsys):
    book_path = str(BOOK_YAML)
    code = tool.main(["--start", book_path, "--end", book_path, "--steps", str(_steps(tmp_path, STEPS)),
                      "--median", "--draws", "4", "--json", str(tmp_path / "walk.json")])
    out = capsys.readouterr().out
    assert code == tool.OK, out
    assert "последний шаг = прямой расчёт конечной книги: ДА" in out
    assert "книга после шагов = конечная книга целиком: ДА" in out

    res = json.loads((tmp_path / "walk.json").read_text(encoding="utf-8"))
    start, probe, back = res["rows"]
    A = tool.load_book(BOOK_YAML)
    assert probe["central"] == central_price(with_overrides(A, {"valuation.beta_u": 0.9}))
    assert probe["central"] < start["central"]
    assert back == dict(start, step="β_u — книги")
    assert res["direct"] == {k: v for k, v in start.items() if k != "step"}


@pytest.mark.needs_book
def test_a_walk_that_misses_a_step_is_not_consistent(tool, tmp_path, capsys):
    book_path = str(BOOK_YAML)
    code = tool.main(["--start", book_path, "--end", book_path, "--steps", str(_steps(tmp_path, STEPS[:1]))])
    out = capsys.readouterr().out
    assert code == tool.NOT_CONSISTENT
    assert "последний шаг = прямой расчёт конечной книги: НЕТ" in out
    assert "книга после шагов = конечная книга целиком: НЕТ" in out


@pytest.mark.parametrize("steps", [[], [{"name": "пусто"}], [{"name": "x", "set": {"valuation.beta_u": 0.9},
                                                              "paths": ["valuation.beta_u"]}],
                                   [{"name": "опечатка", "set": {"valuation.headline.strike_markup_k": 0.03}}]])
def test_bad_steps_are_refused(tool, tmp_path, steps, capsys):
    book_path = str(BOOK_YAML)
    code = tool.main(["--start", book_path, "--end", book_path, "--steps", str(_steps(tmp_path, steps))])
    assert code == tool.BAD_INPUT
    assert "НЕГОДНЫЕ ВХОДЫ" in capsys.readouterr().err
