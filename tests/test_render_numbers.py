"""Числа результатов книги в документах — подстановкой (`ops/tools/render_numbers.py`).

Документ с метками обязан быть равен своей перерисовке по
`data/assumptions/results.json`: новая книга без перерисовки документов не
пройдёт CI, а метка, указывающая в пустоту, — тоже. Заглушка «⟨ядро⟩» вне
метки — число, которое так и не подставили: тоже провал.

Перенесено из 850oa (тот же инструмент); примеры чисел — условные.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIX = "python ops/tools/render_numbers.py"
PLACEHOLDER = "⟨ядро⟩"
# Документы, которые печатают числа результатов книги метками: книга и журнал
# ТЕКУЩЕЙ версии, обзор, методика, витрина, решения и справочник. Документ,
# потерявший все метки (число вписали руками), выпадает из обхода
# `documents()` молча — этот список ловит именно это.
MARKED_DOCUMENTS = (
    "data/assumptions/ASSUMPTIONS-BOOK.md", "data/assumptions/V1.1-CHANGES.md", "README.md",
    "docs/MODEL.md", "docs/DASHBOARD.md", "docs/DECISIONS.md", "docs/MANUAL.md",
)
# Документы истории: журналы прежних версий книги и датированная история правок.
# Метка в них — число ТЕКУЩЕЙ книги: новая версия молча переписала бы прошлое
# (до 06.10.2026 так были размечены журнал 1.0 и запись истории от 30.09.2026).
# Перед пересчётом таблиц новой версии журнал прежней замораживается
# (`render_numbers.py --freeze`), в датированных записях числа пишутся без меток.
FROZEN_DOCUMENTS = ("data/assumptions/V1.0-CHANGES.md", "docs/CHANGELOG.md")
# Тексты, где доля оси в полосе A-V9 называется «вклад» (доля квадрата ранговой
# корреляции оси с центром), а не «дисперсия» (внешний аудит 30.09.2026, S01).
CONTRIBUTION_DOCUMENTS = (
    "data/assumptions/ASSUMPTIONS-BOOK.md", "data/assumptions/V1.0-CHANGES.md",
    "data/assumptions/V1.1-CHANGES.md", "README.md", "docs/MODEL.md", "docs/MANUAL.md",
    "docs/DASHBOARD.md", "docs/DECISIONS.md",
    "data/assumptions/evidence/book-1.0/worlds-calendar/README.md",
)


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("render_numbers_tool",
                                                  ROOT / "ops" / "tools" / "render_numbers.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.needs_book
@pytest.mark.docs
def test_documents_show_the_numbers_of_the_book_results(tool):
    results = json.loads(tool.RESULTS.read_text(encoding="utf-8"))
    problems, marked = [], set()
    for path, text in tool.documents():
        name = path.relative_to(ROOT).as_posix()
        marked.add(name)
        new, errors = tool.render(text, results)
        problems += [f"{name}: {error}" for error in errors]
        old_lines, new_lines = text.split("\n"), new.split("\n")
        problems += [f"{name}:{number}: {old.strip()[:160]}"
                     for number, (old, fresh) in enumerate(zip(old_lines, new_lines), 1)
                     if old != fresh]
    assert not problems, (f"документы разошлись с results.json — `{FIX}`:\n"
                          + "\n".join(problems[:40]))
    # Сторож от тихой потери разметки: эти документы печатают числа результатов книги.
    assert set(MARKED_DOCUMENTS) <= marked, sorted(set(MARKED_DOCUMENTS) - marked)


@pytest.mark.needs_book
@pytest.mark.docs
def test_no_core_placeholder_is_left_in_the_documents(tool):
    """«⟨ядро⟩» — место числа ядра до первого прогона; после него в документах
    с метками заглушек нет: число стоит меткой, а не словом."""
    left = []
    for path, text in tool.documents():
        for number, line in enumerate(text.split("\n"), 1):
            if PLACEHOLDER in line:
                left.append(f"{path.relative_to(ROOT).as_posix()}:{number}: {line.strip()[:120]}")
    assert not left, "заглушки ядра без метки:\n" + "\n".join(left[:20])


RESULTS = {"headline": {"printed_central": 2750.0}, "cells": {"N|stress": 7.5},
           "gap": -0.0276, "rows": [{"name": "a", "v": 0.00049}, {"name": "b", "v": 2.5}],
           "q": {"0.50": 2744.17}}


def _root(tmp_path: Path, doc: str) -> Path:
    (tmp_path / "data" / "assumptions").mkdir(parents=True)
    (tmp_path / "data" / "assumptions" / "results.json").write_text(json.dumps(RESULTS), encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "X.md").write_bytes(doc.encode("utf-8"))
    return tmp_path


@pytest.mark.docs
def test_a_stale_number_fails_the_check_and_is_rewritten_in_place(tool, tmp_path, capsys):
    doc = ("| медиана | **≈<!--=headline.printed_central r0-->2 700<!--/--> ₽** |\r\n"
           "разрыв <!--=gap sp1-->−2,8<!--/--> %, P50 <!--=q[\"0.50\"] r1-->2 744,2<!--/-->\r\n")
    root = _root(tmp_path, doc)
    assert tool.main(["--check", "--root", str(root)]) == 1
    assert "устарел: docs/X.md" in capsys.readouterr().err
    assert (root / "docs" / "X.md").read_bytes() == doc.encode("utf-8"), "--check ничего не пишет"
    assert tool.main(["--root", str(root)]) == 0
    # Меняется только значение между метками; переводы строк файла — те же.
    assert (root / "docs" / "X.md").read_bytes() == doc.replace(">2 700<", ">2 750<").encode("utf-8")
    assert tool.main(["--check", "--root", str(root)]) == 0


@pytest.mark.docs
@pytest.mark.parametrize("mark, reason", [
    ("<!--=headline.nope r0-->1<!--/-->", "нет ключа «nope»"),
    ("<!--=headline.printed_central x1-->1<!--/-->", "неизвестный формат"),
    ("<!--=rows[name=c].v r1-->1<!--/-->", "строк 0, нужна одна"),
    ("<!--=rows[5].v r1-->1<!--/-->", "нет элемента [5]"),
    ("<!--=rows r1-->1<!--/-->", "ждёт число"),
    ("<!--=headline.printed_central r0-->1 350", "не закрыта"),
    ("<!--=headline.printed_central|r0-->1<!--/-->", "режет ячейку таблицы"),
])
def test_a_broken_mark_is_named_and_fails_the_check(tool, tmp_path, capsys, mark, reason):
    root = _root(tmp_path, f"текст {mark} текст\n")
    assert tool.main(["--check", "--root", str(root)]) == 1
    assert reason in capsys.readouterr().err


@pytest.mark.docs
@pytest.mark.parametrize("line", [
    "<!--=headline.printed_central r0-->1<!--/--> ₽",
    "   <!--=headline.printed_central r0-->1<!--/--> ₽",
    "- <!--=headline.printed_central r0-->1<!--/--> ₽",
    "> 1. <!--=headline.printed_central r0-->1<!--/--> ₽",
])
def test_a_mark_opening_a_line_would_render_as_html_and_fails(tool, tmp_path, capsys, line):
    # CommonMark читает строку, начатую с `<!--`, как HTML-блок: метка видна, абзац рвётся.
    root = _root(tmp_path, f"абзац\n{line}\n")
    assert tool.main(["--check", "--root", str(root)]) == 1
    assert "строка 2: метка в начале строки" in capsys.readouterr().err


@pytest.mark.docs
def test_a_mark_inside_a_line_or_a_table_cell_is_fine(tool):
    doc = ("| <!--=gap sp1-->0<!--/--> % |\n**<!--=gap sp1-->0<!--/--> %**\n"
           "-<!--=gap sp1-->0<!--/--> %\n")
    new, errors = tool.render(doc, RESULTS)
    assert not errors
    assert new == doc.replace(">0<", ">−2,8<")


@pytest.mark.docs
def test_a_mark_in_code_is_an_example_not_a_mark(tool):
    doc = ("Синтаксис: `<!--=ПУТЬ ФОРМАТ-->значение<!--/-->`, число "
           "<!--=headline.printed_central r0-->1<!--/--> ₽\n```\n<!--=нет r0-->2<!--/-->\n```\n")
    new, errors = tool.render(doc, RESULTS)
    assert not errors
    assert new == doc.replace(">1<", ">2 750<")


@pytest.mark.docs
@pytest.mark.parametrize("value, fmt, text", [
    (2744.1737, "r1", "2 744,2"), (2750.0, "r0", "2 750"), (97.85, "r0", "98"),
    (12345678.9, "r0", "12 345 679"), (0.583, "p0", "58"), (0.0003, "p2", "0,03"),
    (-0.0276, "sp1", "−2,8"), (0.00818, "sp1", "+0,8"), (163.8, "s0", "+164"),
    (-26.93, "s0", "−27"), (0.0000526, "sp3", "+0,005"), (-0.00004, "sp1", "0,0"),
    (-0.2, "r0", "0"), (0.69652, "r3", "0,697"),
])
def test_number_formats_reproduce_the_documents_typography(tool, value, fmt, text):
    assert tool.formatted(value, fmt) == text


@pytest.mark.docs
def test_an_empty_value_prints_a_word_only_where_the_format_allows_it(tool):
    # Корень обратного DCF вне отрезка поиска — null в results.json.
    assert tool.formatted(None, "sp3n") == tool.formatted(None, "r2n") == "недостижимо"
    assert tool.formatted(-0.0169, "sp3n") == "−1,690" and tool.formatted(1.62124, "r3n") == "1,621"
    with pytest.raises(ValueError, match="ждёт число"):
        tool.formatted(None, "sp3")
    doc = "корень <!--=root sp2n-->0<!--/-->, число <!--=gap sp2n-->0<!--/-->\n"
    new, errors = tool.render(doc, {"root": None, "gap": -0.0276})
    assert not errors and new == doc.replace(">0<", ">недостижимо<", 1).replace(">0<", ">−2,76<")


@pytest.mark.docs
def test_paths_select_by_name_and_by_raw_key(tool):
    assert tool.resolve(RESULTS, "rows[name=b].v") == 2.5
    assert tool.resolve(RESULTS, 'q["0.50"]') == 2744.17
    assert tool.resolve(RESULTS, "rows[1].v") == 2.5
    assert tool.resolve(RESULTS, r'cells["N\|stress"]') == 7.5
    with pytest.raises(KeyError):
        tool.resolve(RESULTS, "headline[0]")


@pytest.mark.docs
def test_paths_select_by_nested_fields_all_at_once(tool):
    # Клетка сетки — по имени, а не по месту в списке: порядок клеток может смениться.
    grid = {"cells": [{"cell": {"world": w, "capex": c}, "ev": ev}
                      for w, c, ev in [("M", "low", 5.0), ("M", "high", -20.1), ("N", "high", 7.0)]]}
    assert tool.resolve(grid, "cells[cell.world=M&cell.capex=high].ev") == -20.1
    for bad, reason in [("cells[cell.world=M].ev", "строк 2"), ("cells[cell.world=X&cell.capex=high].ev",
                        "строк 0"), ("cells[cell.world=M&capex].ev", "без «=»")]:
        with pytest.raises(KeyError, match=reason):
            tool.resolve(grid, bad)


@pytest.mark.docs
def test_freezing_keeps_the_numbers_and_drops_the_marks(tool, tmp_path, capsys):
    """Журнал прежней версии книги перед пересчётом таблиц новой замораживается:
    метки сняты, числа остались текстом — новая книга их не перепишет. Метки в
    `коде` и в блоке кода — примеры синтаксиса — остаются."""
    doc = ("| медиана | **≈<!--=headline.printed_central r0-->2 700<!--/--> ₽** |\r\n"
           "разрыв <!--=gap sp1-->−2,8<!--/--> %; синтаксис: `<!--=ПУТЬ ФОРМАТ-->число<!--/-->`\r\n"
           "```\n<!--=нет r0-->2<!--/-->\n```\n")
    assert tool.freeze(doc) == (
        "| медиана | **≈2 700 ₽** |\r\n"
        "разрыв −2,8 %; синтаксис: `<!--=ПУТЬ ФОРМАТ-->число<!--/-->`\r\n"
        "```\n<!--=нет r0-->2<!--/-->\n```\n")
    root = _root(tmp_path, doc)
    assert tool.main(["--freeze", str(root / "docs" / "X.md"), "--root", str(root)]) == 0
    assert "меток снято: 2" in capsys.readouterr().out
    frozen = (root / "docs" / "X.md").read_bytes().decode("utf-8")
    assert "2 700" in frozen and "<!--=headline" not in frozen
    assert tool.main(["--check", "--root", str(root)]) == 0, "замороженный документ не устаревает"


@pytest.mark.docs
def test_history_documents_carry_no_live_marks(tool):
    """Журнал прежней версии книги и история правок — без меток: их числа —
    числа своего дня, а не текущей книги."""
    for name in FROZEN_DOCUMENTS:
        text = (ROOT / name).read_text(encoding="utf-8")
        assert tool.freeze(text) == text, f"{name}: метки в документе истории — `{FIX} --freeze {name}`"
    marked = {path.relative_to(ROOT).as_posix() for path, _ in tool.documents()}
    journals = sorted(p.name for p in (ROOT / "data" / "assumptions").glob("V*-CHANGES.md"))
    current = [name for name in journals if f"data/assumptions/{name}" in marked]
    assert current == [journals[-1]], f"метки несёт только журнал текущей версии: {current} из {journals}"


@pytest.mark.docs
def test_the_band_share_of_an_axis_is_called_a_contribution():
    """Доля оси в полосе A-V9 — доля квадрата ранговой корреляции оси с центром:
    это «вклад», а не доля дисперсии (внешний аудит 30.09.2026, S01). «Дисперсия»
    остаётся там, где она дисперсия: правило A-P2u (шум наблюдения маржи) и
    разложение разброса сетки."""
    import re

    near = re.compile(r"вклад|полос|крупнейш|\bос[ьи]\b|contributions|A-V9", re.IGNORECASE)
    # Само правило называет оба слова: «вклад, а не «дисперсия»», «не разложение дисперсии».
    negated = re.compile(r"\bне(?: доля| разложение)?\s+«?$")
    wrong = []
    for name in CONTRIBUTION_DOCUMENTS:
        text = (ROOT / name).read_text(encoding="utf-8")
        for number, line in enumerate(text.split("\n"), 1):
            for hit in re.finditer("дисперси", line):
                if negated.search(line[:hit.start()]):
                    continue
                context = line[max(0, hit.start() - 90):hit.end() + 30]
                if near.search(context):
                    wrong.append(f"{name}:{number}: …{context.strip()}…")
    assert not wrong, "доля оси в полосе названа дисперсией:\n" + "\n".join(wrong)
    book = (ROOT / "data" / "assumptions" / "ASSUMPTIONS-BOOK.md").read_text(encoding="utf-8")
    assert "доля квадрата ранговой корреляции оси с центром" in book, "книга определяет термин «вклад оси»"
