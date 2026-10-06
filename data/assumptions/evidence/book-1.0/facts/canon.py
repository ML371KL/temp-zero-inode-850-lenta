# -*- coding: utf-8 -*-
"""Канон проформы якоря в фактах (решение ведущего A12): одна EBITDA LTM, отчётная — рядом.

Лист фактов (`build_facts.py`) писал в `anchor.json` отчётные величины и «кандидатов» проформы до листов «Маржа»,
«Сеть» и capex. Этот шаг сводит канон из выходов листов (первичку не читает):
  * `ebitda_pre16.pro_forma` и `halves_for_book.ebitda_pre16.pro_forma` — лист «Маржа» (`margin/ltm_out.json`,
    мост «прибыль до налога МСФО 16 → EBITDA IAS 17»; округление до 0,001 — как в книге);
  * `ebitda_ltm.pro_forma` = проформа 2025H2 + 2026H1 (88,235) — ЕДИНСТВЕННЫЙ канон EBITDA LTM книги
    (`facts.anchor.ebitda_ltm`); отчётная 84,433077 остаётся полем `reported` (`facts.anchor.ebitda_ltm_reported`);
  * `revenue_ltm.pro_forma` = Σ сегментов книги 2025H2 + проформа 2026H1 (1 394,815228; лист «Сеть», сведение R1);
  * `halves_for_book.revenue.pro_forma` 2025 — Σ сегментов без diy (правило D3);
  * `da_pre16.pro_forma` = 16,496 — лист capex (`capex/capex_out.json → da_anchor`: «О'КЕЙ» январь–май по классам
    ОС 0,701; оценка листа фактов 17,595 по темпу группы завышена ≈втрое);
  * `margin_pro_forma` и se Монте-Карло моста `margin_pro_forma_se_mc` — лист «Маржа» (`margin/anchor_out.json`);
    кандидаты листа фактов (`pro_forma_candidate`) снимаются — их заменил канон.
Шаг переписывает `anchor.json` ПОСЛЕ листа фактов, поэтому здесь же обновляется его sha256 в
`sources.json → derived_files` (внешний аудит 30.09.2026, D01: хэш оставался от сборки до канона).
Запуск: python -B canon.py [каталог фактов] (по умолчанию ../../../../facts от места скрипта — data/facts репозитория).
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EV = HERE.parent
FACTS = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parents[3] / "facts"
SEG_2025H2 = 689.566272          # facts.revenue.2025H2 книги = Σ сегментов без diy (лист «Сеть», сведение R1)


def load(rel: str) -> dict:
    return json.loads((EV / rel).read_text(encoding="utf-8"))


def main() -> dict:
    path = FACTS / "anchor.json"
    A = json.loads(path.read_text(encoding="utf-8"))
    ltm = load("margin/ltm_out.json")
    anchor = load("margin/anchor_out.json")
    capex = load("capex/capex_out.json")
    halves = {p: round(v["ebitda"], 3) for p, v in ltm["halves_pro_forma"].items()}
    A["ebitda_pre16"]["pro_forma"] = halves["2026H1"]
    A["ebitda_pre16"]["pro_forma_src"] = "лист «Маржа»: margin/ltm_out.json → halves_pro_forma.2026H1 (мост anchor_bridge.py)"
    A["ebitda_pre16"].pop("pro_forma_pending", None)
    A["halves_for_book"]["ebitda_pre16"]["pro_forma"] = halves
    A["ebitda_ltm"]["pro_forma"] = round(halves["2025H2"] + halves["2026H1"], 3)
    A["ebitda_ltm"]["canon"] = "pro_forma"
    A["ebitda_ltm"]["canon_note"] = ("решение ведущего A12: один канон EBITDA LTM — проформа 2П2025 + 1П2026 "
                                     "(facts.anchor.ebitda_ltm); отчётная — reported (facts.anchor.ebitda_ltm_reported)")
    A["ebitda_ltm"].pop("pro_forma_pending", None)
    A["revenue_ltm"]["pro_forma"] = round(SEG_2025H2 + A["revenue"]["pro_forma"], 6)
    A["revenue_ltm"]["pro_forma_src"] = ("Σ сегментов книги 2025H2 (без diy) + проформа 1П2026; по итогам PL — "
                                         f"{round(ltm['revenue_ltm_pro_forma'], 3)} (Δ — «Реми» декабрь, DB-7)")
    A["revenue_ltm"].pop("pro_forma_pending", None)
    A["halves_for_book"]["revenue"]["pro_forma"]["2025H2"] = SEG_2025H2
    A["halves_for_book"]["revenue"]["pro_forma"]["2025H1"] = round(ltm["halves_pro_forma"]["2025H1"]["revenue"], 6)
    A["da_pre16"]["pro_forma"] = capex["da_anchor"]["pro_forma"]
    A["da_pre16"]["pro_forma_src"] = ("лист capex: capex/capex_out.json → da_anchor (15,795 + «О'КЕЙ» январь–май "
                                      "0,701 по классам ОС; решение ведущего A12)")
    A["da_pre16"].pop("pro_forma_pending", None)
    A["margin_pro_forma"] = anchor["margin_pro_forma"]
    # se Монте-Карло моста; книга берёт se 0,0010 — с неопределённостью базы проформы (A-C7)
    A["margin_pro_forma_se_mc"] = anchor["margin_pro_forma_se"]
    A.pop("margin_pro_forma_se", None)
    for key in ("ebitda_pre16", "ebitda_ltm", "revenue_ltm", "da_pre16"):
        if A[key].pop("pro_forma_candidate", None) is not None:
            A[key]["pro_forma_candidate_note"] = "кандидат листа фактов до листов «Маржа», «Сеть», capex заменён каноном (canon.py)"
    path.write_text(json.dumps(A, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    record_hash(path)
    return dict(ebitda_ltm=A["ebitda_ltm"]["pro_forma"], revenue_ltm=A["revenue_ltm"]["pro_forma"],
                da_pre16=A["da_pre16"]["pro_forma"], margin=A["margin_pro_forma"])


def record_hash(path: Path) -> None:
    """sha256 переписанного файла фактов — в `sources.json → derived_files` рядом с ним
    (тем же видом, каким файл пишет лист фактов: отступ 1, перевод строки в конце)."""
    sources = path.parent / "sources.json"
    if not sources.exists():
        return
    S = json.loads(sources.read_text(encoding="utf-8"))
    S.setdefault("derived_files", {})[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    sources.write_text(json.dumps(S, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    print(main())
