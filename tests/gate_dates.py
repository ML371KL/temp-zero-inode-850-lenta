# -*- coding: utf-8 -*-
"""Даты объяснений гейтов в тестах МЕХАНИЗМА — фиксированные, а не «сегодня».

Тесты гейтов бывают двух родов, и путать их нельзя.

* **Тесты данных** спрашивают о настоящем файле на настоящий день: «не истёк
  ли срок объяснения», «у каждого ли срабатывающего гейта есть действующий
  текст». Им падать после срока ПОЛОЖЕНО — это и есть будильник, и сборка в
  тот же день встанет по той же причине (`test_explanations_are_not_stale`,
  `test_every_firing_gate_has_a_live_explanation`).
* **Тесты механизма** спрашивают, что делает код: блокирует ли гейт без
  текста, даёт ли расхождение массы код 3, пишет ли сборка выпуск. Их ответ не
  должен зависеть от того, истёк ли срок в файле на день прогона. До
  24.09.2026 зависел: с 01.02.2027 (срок `creditor_loss`) шесть таких тестов
  падали вместе с тестами данных и называли не ту причину (аудит 24.09.2026,
  G1-exam §1.4).

Отсюда два инструмента: «тихий день» — последний день, когда все объяснения
файла действуют и ни одно не истекает в пределах предупреждения, — и копия
репозитория с отодвинутыми сроками для настоящей сборки в подпроцессе.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import re
import shutil
from pathlib import Path

import pytest
import yaml

import model.checks as checks

ROOT = Path(__file__).resolve().parents[1]
EXPLANATIONS = ROOT / "data" / "assumptions" / "gate_explanations.yaml"

# Подлинная функция — до любых подмен: вложенные подмены не должны читать
# «на день» чужой подмены.
_LOAD = checks.load_gate_explanations

# Срок в копиях репозитория: заведомо дальше любой даты прогона «в будущем».
FAR_UNTIL = "2099-12-31"


def quiet_day(path: Path = EXPLANATIONS) -> dt.date:
    """Последний день, когда все объяснения файла действуют и ни одно не
    истекает в пределах `EXPLANATION_WARN_DAYS` — то есть сборка на книжных
    входах обязана давать код 0."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    untils = [checks._parse_until(key, spec.get("valid_until")) for key, spec in raw.items()]
    first = min(until for until in untils if until is not None)
    return first - dt.timedelta(days=checks.EXPLANATION_WARN_DAYS + 1)


@contextlib.contextmanager
def explanations_as_of(day: dt.date, *modules):
    """Объяснения читаются «на день `day`» — в `model.checks` и в `modules`.

    `modules` — модули, взявшие функцию импортом по имени (например,
    `ops/build_release.py`): подмена атрибута `model.checks` их не касается.
    """
    def pinned(path=None, *, today=None):
        return _LOAD(path, today=today or day)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(checks, "load_gate_explanations", pinned)
        for module in modules:
            patch.setattr(module, "load_gate_explanations", pinned)
        yield day


def move_deadlines(path: Path, until: str = FAR_UNTIL, *, only: str | None = None) -> None:
    """Переписывает `valid_until` в КОПИИ файла объяснений, не трогая остального.

    Правка строкой, а не через `yaml.safe_dump`: копия остаётся тем же файлом с
    теми же текстами, меняется только срок. Оригинал в `data/assumptions`
    этой функцией не правится никогда — она зовётся только на копиях.
    """
    path = Path(path)
    assert ROOT / "data" not in path.parents, "правится только копия, не книга"
    text = path.read_text(encoding="utf-8")
    if only is None:
        text, count = re.subn(r"(?m)^(\s+valid_until:\s*)\S+", r"\g<1>" + until, text)
        assert count, "в файле объяснений не нашлось ни одного срока"
    else:
        block = re.search(rf"(?ms)^{re.escape(only)}:\n(.*?)(?=^\S|\Z)", text)
        assert block, f"в файле объяснений нет записи {only}"
        body, count = re.subn(r"(?m)^(\s+valid_until:\s*)\S+", r"\g<1>" + until, block.group(1))
        assert count == 1, f"у записи {only} не один срок"
        text = text[:block.start(1)] + body + text[block.end(1):]
    path.write_text(text, encoding="utf-8", newline="\n")


def repo_copy(target: Path, *, until: str | None = FAR_UNTIL) -> Path:
    """Копия репозитория, достаточная для ЗАПУСКА СБОРКИ (не для тестов).

    `until` — срок, которым переписываются все объяснения копии; `None`
    оставляет сроки как в книге. Настоящая сборка идёт подпроцессом, и
    подменить в нём «сегодня» нечем, кроме самих сроков.
    """
    for name in ("model", "data", "ops", "indicators"):
        # Инструменты и доказательные листы книги сборка не читает.
        shutil.copytree(ROOT / name, target / name,
                        ignore=shutil.ignore_patterns("__pycache__", "tools", "evidence"))
    (target / "var").mkdir(exist_ok=True)
    if until is not None:
        move_deadlines(target / "data" / "assumptions" / "gate_explanations.yaml", until)
    return target
