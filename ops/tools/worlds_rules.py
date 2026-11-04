# -*- coding: utf-8 -*-
"""Правила книги «Ленты» для чисел, зависящих от миров ставок (шаги `rule` манифеста
`data/assumptions/worlds_dependents.yaml`, команда `ops/tools/adopt_worlds.py`).

Правило — функция `f(ctx) -> {"file": путь, "values": {ключ: число}, "decimals": {ключ: знаков}}`; `ctx` — контекст
движка (`ctx.root` — корень дерева, `ctx.record` — запись миров, `ctx.yaml(rel)` — YAML дерева). Числа, которые здесь
считаются, в книге не пишутся руками: сторож `tests/test_worlds_dependents.py` (через `adopt_worlds.py --check`)
сверяет их с файлом.

  governance   дисконт за управление g (A-V7) и каналы (а), (б), (г), (ж) — неподвижная точка листа пересчёта
               `evidence/book-1.0/valuation/governance_fixed_point.py` на печатаемой медиане таблиц книги; пишется
               всегда, без порога, при смене версии миров (шаг манифеста `when: change`).

Числа объяснений гейтов (массы на дату книги, коридоры дрейфа, составы клеток) — не правило, а выход
`ops/tools/gate_numbers.py` и шаблон `gate_explanations.tpl` (шаг `gate-texts` команды).
"""
from __future__ import annotations

import json

TEMPLATE = "data/assumptions/assumptions_template.yaml"
FIXED_POINT = "data/assumptions/evidence/book-1.0/valuation/out/governance_fixed_point.json"
ND = 5                          # знаков g и каналов (как у листа «Оценка»)
CHANNELS = ("а", "б", "г", "ж")  # каналы, которые зависят от капитала модели при медиане (остальные — константы листа)


class RuleStop(Exception):
    """Правило не может дать число без суждения: команда останавливается."""


def governance(ctx) -> dict:
    """g книги = центр листа пересчёта на медиане таблиц (`out/governance_fixed_point.json`, его пишет шаг листа перед
    правилом); каналы шаблона (а), (б), (г), (ж) — его же. Канал листа без строки шаблона или строка без канала — стоп."""
    fp = json.loads((ctx.root / FIXED_POINT).read_text(encoding="utf-8"))
    tpl = ctx.yaml(TEMPLATE)
    comps = tpl["valuation"]["governance_components"]
    by_id = {c["id"]: c for c in fp["channels"]}
    values = {"valuation.governance_discount": float(fp["g"])}
    for i, comp in enumerate(comps):
        cid = str(comp["name"])[1]                       # «(а) …» → «а»
        if cid not in by_id:
            raise RuleStop(f"канал {comp['name'][:20]!r} шаблона не в листе пересчёта")
        if cid in CHANNELS:
            values[("valuation", "governance_components", str(i), "value")] = float(by_id[cid]["value"])
        elif abs(float(comp["value"]) - float(by_id[cid]["value"])) > 1e-12:
            raise RuleStop(f"канал ({cid}) шаблона {comp['value']} ≠ листу {by_id[cid]['value']} — суждение листа «Оценка»")
    total = sum(float(c["sign"]) * (values.get(("valuation", "governance_components", str(i), "value"), float(c["value"])))
                for i, c in enumerate(comps))
    if abs(total - float(fp["g"])) > 1e-9:
        raise RuleStop(f"Σ каналов {total:.6f} ≠ g листа {fp['g']} (центр — медиана Монте-Карло, а не сумма: скошенность (ж))")
    return {"file": TEMPLATE, "values": values, "decimals": {k: ND for k in values}}
