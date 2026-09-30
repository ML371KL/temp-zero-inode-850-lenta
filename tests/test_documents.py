"""Документы говорят то же, что код: правило допуска и ретро-проверка эталонов.

У каждого правила один дом — код; документ его цитирует. Цитата, разошедшаяся
с кодом, хуже её отсутствия: владелец решает о допуске нау-каста по тексту
справочника, а планку уравнения видит в таблице `docs/INDICATORS.md`.
Числа результатов книги в документах сверяет `tests/test_render_numbers.py`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from indicators.journal import MARGIN_TARGET, admission_rule_text

ROOT = Path(__file__).resolve().parents[1]
INDICATORS_DOC = "docs/INDICATORS.md"
MANUAL_DOC = "docs/MANUAL.md"
RETRO_BLOCK = re.compile(r"<!-- retro:begin -->\n(.*?)\n<!-- retro:end -->", re.S)


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


@pytest.mark.docs
@pytest.mark.parametrize("name", [INDICATORS_DOC, MANUAL_DOC])
def test_the_admission_rule_in_the_documents_is_the_rule_of_the_journal(name):
    """Правило допуска — строкой `journal.admission_rule_text`, слово в слово."""
    rule = admission_rule_text(target=MARGIN_TARGET)
    assert rule in _read(name), f"{name}: правило допуска разошлось с журналом:\n{rule}"


@pytest.mark.docs
@pytest.mark.needs_book
def test_the_retro_table_in_the_indicators_document_is_the_output_of_the_module():
    """Таблица ретро-проверки — вывод `indicators.retro.markdown_table` на фактах
    репозитория (без хранилища: эталон процентов считается только на сервере)."""
    from indicators.retro import markdown_table, retro_block
    from model.paths import FACTS_DIR

    found = RETRO_BLOCK.search(_read(INDICATORS_DOC))
    assert found, f"{INDICATORS_DOC}: нет блока <!-- retro:begin --> … <!-- retro:end -->"
    table = markdown_table(retro_block(store=None, facts_dir=FACTS_DIR))
    assert found.group(1) == table, (
        f"{INDICATORS_DOC}: таблица ретро-проверки устарела — вставить вывод "
        "`python -m indicators.retro`:\n" + table)


@pytest.mark.docs
def test_the_documents_named_by_the_readme_exist():
    """Документы, на которые ссылаются обзор и справочник, лежат в репозитории."""
    named = set()
    for name in ("README.md", MANUAL_DOC):
        named |= set(re.findall(r"`((?:docs|ops|data/assumptions|data/facts)/[\w./-]+\.md)`",
                                _read(name)))
    missing = sorted(n for n in named if not (ROOT / n).is_file())
    assert named and not missing, missing
