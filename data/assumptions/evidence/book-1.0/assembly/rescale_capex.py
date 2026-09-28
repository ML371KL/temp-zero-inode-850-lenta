"""Сведение листов: знаменатель поддерживающего capex (A-K1) к выручке центральной клетки листа «Сеть».

Лист capex (evidence/book-1.0/capex) делит рубли поддерживающего capex на неизменной сети на прокси выручки
2026E на проформе 705,25 × (1 + 1,12) = 1 495,13 и нормирует пару 2027 по доле 1П 0,4656 (прокси) —
и сам просит ведущего пересчитать по сетке (README листа, «Открытые вопросы»). Здесь:
  * знаменатель = проформа 1П2026 (facts) + выручка 2026H2 центральной клетки листа «Сеть»
    (network/checks_out.json → base_cell, клетка H × base × mid × partial);
  * доля 1П 2027 = 2027H1 / 2027 той же клетки;
  * ключи = рубли пути уровня (capex_out.json → path.<уровень>.<год>.total_bn) / знаменатель;
    2026H2 = уровень 2026 × m2; 2027H1/2027H2 = уровень 2027 × m1·k / m2·k, k = 1 / (m1·s1 + m2·(1 − s1));
    далее — годовой уровень; LT = 2036 (стационар).
Первичку не читает: только выходы двух листов. Пути — от места скрипта (каталог листов — родитель; LENTA_EVIDENCE
переопределяет). Запуск: python -B rescale_capex.py → печать и rescale_capex_out.json рядом.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
EV = Path(os.environ.get("LENTA_EVIDENCE", str(HERE.parent)))

PF_1H26 = 648.488059 + 66.120725 - 9.359828   # facts.revenue.pro_forma.2026H1 (МСФО 6М2026 с. 7 и прим. 5)


def main() -> dict:
    cap = json.loads((EV / "capex" / "capex_out.json").read_text(encoding="utf-8"))
    net = json.loads((EV / "network" / "checks_out.json").read_text(encoding="utf-8"))
    segs = net["base_cell"]["segments"]
    h2_26 = sum(s["2026H2"] for s in segs.values())
    h1_27 = sum(s["2027H1"] for s in segs.values())
    h2_27 = sum(s["2027H2"] for s in segs.values())
    rev26 = PF_1H26 + h2_26
    s1 = h1_27 / (h1_27 + h2_27)
    season = cap["seasonality"]
    m1, m2 = season["m1"], season["m2"]
    kq = 1.0 / (m1 * s1 + m2 * (1.0 - s1))
    old_rev = cap["bottom_up_inputs"]["rev_2026e_pf"]
    out = {"inputs": dict(rev_2026e_old=old_rev, s1_2027_old=season["s1_2027_proxy"], rev_2026h2_center=round(h2_26, 3),
                          rev_2026e_new=round(rev26, 3), s1_2027_new=round(s1, 5), m1=m1, m2=m2, kq=round(kq, 5),
                          m1_norm=round(m1 * kq, 4), m2_norm=round(m2 * kq, 4), factor=round(old_rev / rev26, 5)),
           "maintenance_pct": {}}
    for lv, path in cap["path"].items():
        lvl = {int(t): row["total_bn"] / rev26 for t, row in path.items()}
        k = {"2026H2": round(lvl[2026] * m2, 4), "2027H1": round(lvl[2027] * m1 * kq, 4),
             "2027H2": round(lvl[2027] * m2 * kq, 4)}
        for t in range(2027, 2037):
            k[str(t)] = round(lvl[t], 4)
        k["LT"] = round(lvl[2036], 4)
        out["maintenance_pct"][lv] = k
    return out


if __name__ == "__main__":
    res = main()
    i = res["inputs"]
    print(f"знаменатель 2026E: {i['rev_2026e_old']} (прокси листа) → {i['rev_2026e_new']} (проформа 1П2026 + 2026H2 центральной клетки "
          f"{i['rev_2026h2_center']}); множитель {i['factor']}")
    print(f"доля 1П 2027: {i['s1_2027_old']} → {i['s1_2027_new']}; m1·k / m2·k = {i['m1_norm']} / {i['m2_norm']}")
    for lv, k in res["maintenance_pct"].items():
        print(f"  {lv}: " + ", ".join(f"{p} {v}" for p, v in k.items()))
    (HERE / "rescale_capex_out.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
