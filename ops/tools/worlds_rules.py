# -*- coding: utf-8 -*-
"""Правила книги «Ленты» для чисел, зависящих от миров ставок (шаги `rule` манифеста
`data/assumptions/worlds_dependents.yaml`, команда `ops/tools/adopt_worlds.py`).

Правило — функция `f(ctx) -> {"file": путь, "values": {ключ: число}, "decimals": {ключ: знаков}}`; `ctx` — контекст
движка (`ctx.root` — корень дерева, `ctx.record` — запись миров, `ctx.yaml(rel)` — YAML дерева). Числа, которые здесь
считаются, в книге и объяснениях не пишутся руками: сторож `tests/test_worlds_dependents.py` (через
`adopt_worlds.py --check`) сверяет их с файлом.

Числа книги, выведенные на мирах, у «Ленты» — только массы гейтов (`gate_explanations.yaml`): масса на дату книги и
коридор дрейфа = развёртка гейтов (`evidence/book-<версия>/gates/gate_sweep.py`). Суждения (веса A-P1m, лист «Сеть»,
дисконт за управление A-V7, пробы тестов) правилами не пересчитываются — `worlds_dependents.yaml`, раздел `judgement`.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

EXPLANATIONS = "data/assumptions/gate_explanations.yaml"
TEMPLATE = "data/assumptions/assumptions_template.yaml"
ND = 4                         # знаков массы в объяснениях (как печатает сводка развёртки)


class RuleStop(Exception):
    """Правило не может дать число без суждения: команда останавливается."""


def sweep_path(root: Path) -> Path:
    version = yaml.safe_load((root / TEMPLATE).read_text(encoding="utf-8"))["meta"]["version"]
    return root / "data" / "assumptions" / "evidence" / f"book-{version}" / "gates" / "out" / "gate_sweep.json"


def gate_masses(ctx) -> dict:
    """Масса гейта на дату книги → `expected_mass`; минимум и максимум развёртки → `expected_mass_range` (у гейтов,
    где он есть). Гейт без объяснения или блокирующая находка на какой-нибудь дате развёртки — стоп: это суждение."""
    path = sweep_path(ctx.root)
    sweep = json.loads(path.read_text(encoding="utf-8"))
    summary = path.with_suffix(".txt").read_text(encoding="utf-8")
    line = next((ln for ln in summary.splitlines() if ln.startswith("Блокирующие находки")), "")
    if not line.endswith("нет ни на одной дате."):
        raise RuleStop(f"развёртка гейтов: {line or 'нет строки блокирующих находок'} — суждение")
    expl = ctx.yaml(EXPLANATIONS)
    at_book = sweep["book_date"]["gates"]
    values, decimals = {}, {}
    for key, g in at_book.items():
        e = expl.get(key)
        if not isinstance(e, dict):
            raise RuleStop(f"гейт {key} сработал на дате книги, а объяснения нет — суждение")
        if isinstance(e.get("expected_mass"), (int, float)):
            values[f"{key}.expected_mass"] = round(g["mass"], ND)
        if isinstance(e.get("expected_mass_range"), list):
            masses = [r["gates"].get(key, {}).get("mass", 0.0) for r in sweep["by_day"]]
            values[f"{key}.expected_mass_range.0"] = round(min(masses), ND)
            values[f"{key}.expected_mass_range.1"] = round(max(masses), ND)
    decimals = {k: ND for k in values}
    return {"file": EXPLANATIONS, "values": values, "decimals": decimals}
