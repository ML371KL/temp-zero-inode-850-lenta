# -*- coding: utf-8 -*-
"""Независимая проверка листа «Миры и календарь» (VERIFY.md): инварианты фрагмента, календаря, текста раздела и гигиены.

Не пересчитывает листы — читает их выходы (worlds_check_out.json, calendar.json) и сверяет:
  F. фрагмент fragments/worlds-calendar.yaml: пути ключей есть в списке ключей книги (study/magnit_book_keys.txt, env
     LENTA_BOOK_KEYS; без файла шаг пропускается), веса = запись, пути осей существуют в собранном блоке worlds;
  C. календарь: поля загрузчика indicators/calendar.py, порядок, «ближайший факт эмитента» на пяти датах (как FAKE_TODAY),
     отчёт для периодов журнала (2026H2/2026FY → lenta.fy2026, 2027H1 → lenta.h1_2027, кварталы), нет прошедших событий;
  S. текст раздела sections/02-worlds-calendar.md: таблица A-M1…A-M3 и числа A-M4/A-P1m = выходу worlds_check.py;
  G. гигиена: в скриптах, выходах, фрагменте и календаре нет путей C:\\Users и имени книги-источника.
Код 0 — всё сошлось.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
BOOK_DRAFT = HERE.parents[2]
H = BOOK_DRAFT.parent
FRAGMENT = BOOK_DRAFT / "fragments" / "worlds-calendar.yaml"
CALENDAR = Path(os.environ.get("LENTA_CALENDAR_OUT", BOOK_DRAFT / "calendar.json"))
SECTION = BOOK_DRAFT / "sections" / "02-worlds-calendar.md"
KEYS = Path(os.environ.get("LENTA_BOOK_KEYS", H / "study" / "magnit_book_keys.txt"))
WORLDS_OUT = HERE / "worlds_check_out.json"

fails: list[str] = []
n = 0


def ok(cond: bool, what: str) -> None:
    global n
    n += 1
    if not cond:
        fails.append(what)


def fmt(x: float, d: int) -> str:
    s = f"{x:.{d}f}".replace(".", ",")
    return s


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    W = json.loads(WORLDS_OUT.read_text(encoding="utf-8"))
    ok(W["ok"], "worlds_check.py: не все проверки прошли")

    # --- F. фрагмент
    frag = yaml.safe_load(FRAGMENT.read_text(encoding="utf-8"))
    ok("worlds" in frag and frag["worlds"] is None, "фрагмент: блок worlds — только маркер #{{WORLDS}} (значение null)")
    ok("#{{WORLDS}}" in FRAGMENT.read_text(encoding="utf-8"), "фрагмент: нет маркера #{{WORLDS}}")
    rec_probs = {"N": 0.35, "H": 0.45, "M": 0.2}
    ok(frag["joint"]["world_prob"] == rec_probs, "фрагмент: world_prob ≠ запись")
    ok(frag["joint"]["world_prob_market_implied"] == {"N": 0.1, "H": 0.25, "M": 0.65}, "фрагмент: world_prob_market_implied")
    ok(frag["joint"]["macro_neutral_world"] == "M" and frag["joint"]["own_macro_confidence"] == 0.5, "фрагмент: M / λ")
    rng = W["probability_ranges_record"]
    for ax in frag["valuation"]["uncertainty"]["axes"]:
        if ax.get("path") == "joint.world_prob":
            for side in ("low", "high"):
                ok(abs(sum(ax[side].values()) - 1) < 1e-12, f"ось весов: {side} не суммируется в 1")
                for w, v in ax[side].items():
                    ok(rng[w][0] - 1e-12 <= v <= rng[w][1] + 1e-12, f"ось весов: {side}.{w} = {v} вне диапазона записи {rng[w]}")
    world_fields = {"cpi", "food_cpi", "lt"}
    for row in frag["valuation"]["uncertainty"]["axes"] + frag["sensitivities"] + frag["reverse_dcf"]:
        for p in row.get("paths", [row.get("path")]):
            if p and p.startswith("worlds."):
                parts = p.split(".")
                ok(parts[1] in ("N", "H", "M") and parts[2] in world_fields, f"путь {p} не из блока worlds")
    if KEYS.exists():
        keys = set()
        for line in KEYS.read_text(encoding="utf-8").splitlines():
            k = line.split(" = ")[0].rstrip("/")
            keys.add(k)
        for k in ("joint.world_prob", "joint.world_prob_market_implied", "joint.macro_neutral_world", "joint.own_macro_confidence",
                  "valuation.roll_along_forwards", "valuation.market_curve_years", "valuation.uncertainty.axes",
                  "sensitivities", "reverse_dcf", "worlds"):
            ok(k in keys, f"ключ {k} не найден в списке ключей книги")

    # --- C. календарь
    cal = json.loads(CALENDAR.read_text(encoding="utf-8"))
    ev = cal["events"]
    as_of = dt.date.fromisoformat(cal["as_of"])
    for e in ev:
        for k in ("id", "when", "precision", "title", "gives", "prior_for", "note"):
            ok(k in e, f"{e.get('id')}: нет поля {k}")
        ok(dt.date.fromisoformat(e["when"]) >= as_of, f"{e['id']}: событие в прошлом относительно as_of")
    ok([e["when"] for e in ev] == sorted(e["when"] for e in ev), "календарь не упорядочен")

    def next_fact(today: dt.date):
        for e in sorted(ev, key=lambda x: x["when"]):
            if dt.date.fromisoformat(e["when"]) >= today and e["id"].startswith("lenta.") and e["prior_for"] is None \
                    and "revenue" in e["gives"]:
                return e["id"]
        return None
    for today, want in (("2026-09-28", "lenta.q3_2026"), ("2026-11-04", "lenta.q4_2026_ops"), ("2027-02-13", "lenta.fy2026"),
                        ("2027-03-30", "lenta.q1_2027"), ("2027-05-04", "lenta.h1_2027")):
        got = next_fact(dt.date.fromisoformat(today))
        ok(got == want, f"ближайший факт на {today}: {got}, ожидалось {want}")
    ids = {e["id"] for e in ev}
    for period, rid in (("2026H2", "lenta.fy2026"), ("2026FY", "lenta.fy2026"), ("2027H1", "lenta.h1_2027"),
                        ("2026Q3", "lenta.q3_2026"), ("2026Q4 выручка", "lenta.q4_2026_ops"), ("2026Q4 маржа", "lenta.fy2026"),
                        ("2027Q1", "lenta.q1_2027"), ("2027Q2", "lenta.h1_2027")):
        ok(rid in ids, f"период {period}: нет события {rid}")
    q3 = next(e for e in ev if e["id"] == "lenta.q3_2026")
    ok(q3["window"] == {"from": "2026-10-26", "to": "2026-11-03"} and q3["when"] in ("2026-10-29", "2026-10-30"), "3 кв. 2026 не по D2")
    puts = {e["id"]: e["when"] for e in ev if e["id"].startswith("okey.bond_")}
    ok(puts == {"okey.bond_b1p8_put": "2027-02-09", "okey.bond_b1p6_put": "2027-03-17"}, f"пут-оферты: {puts}")
    for w in cal.get("watch", []):
        ok("when" not in w, f"watch {w['id']}: у списка наблюдения не должно быть даты")

    # --- S. текст раздела
    txt = SECTION.read_text(encoding="utf-8")
    t = W["annual_table"]
    # числа, которые зависят только от миров, в тексте должны совпадать с выходом листа
    mi = W["market_implied_10y"]
    for label, val in (("fair_10y_by_world.N p2", mi["fair_10y_by_world"]["N"]), ("fair_10y_by_world.H p2", mi["fair_10y_by_world"]["H"]),
                       ("fair_10y_by_world.M p2", mi["fair_10y_by_world"]["M"]), ("market_10y p2", mi["market_10y"]),
                       ("market_implied.linear p2", mi["market_implied"]["linear"]),
                       ("market_implied.discount_factor_mix p2", mi["market_implied"]["discount_factor_mix"]),
                       ("analytical.linear p2", mi["analytical"]["linear"])):
        m = re.search(r"<!--=checks\.market_implied_10y\." + re.escape(label) + r"-->(.*?)<!--/-->", txt)
        ok(m is not None and m.group(1) == fmt(val * 100, 2), f"A-P1m {label}: в тексте {m.group(1) if m else None}, лист {fmt(val*100, 2)}")
    fair = W["fair_zero_curve_today"]
    for w in ("N", "H", "M"):
        want = " / ".join(fmt(fair[w][k], 2) for k in ("1", "3", "5", "10"))
        ok(want in txt, f"A-M4: кривая {w} {want} не найдена в тексте")
    r055 = W["terminal_real_r_u_pct"]["0.550"]
    ok(f"{fmt(r055['N'], 2)} % (N), {fmt(r055['H'], 2)} % (H), {fmt(r055['M'], 2)} % (M)" in txt, "A-M4: r_u при β_u 0,55")
    # таблица A-M1…A-M3: каждое годовое число листа (округлённое, как в тексте) присутствует в строке своего мира
    from decimal import Decimal, ROUND_HALF_UP

    def r(x, d, strip):
        v = Decimal(repr(x)).quantize(Decimal(1).scaleb(-d), rounding=ROUND_HALF_UP)   # «половины вверх», как в тексте
        s = f"{v:.{d}f}"
        if strip:
            s = s.rstrip("0").rstrip(".")
        return s.replace(".", ",")
    lines = {w: [l for l in txt.splitlines() if l.startswith(f"| {w}:") or l.startswith(f"| **{w} ")] for w in ("N", "H", "M")}
    cols = ("2П2026", "2027", "2028", "2029", "2030")
    for w in ("N", "H", "M"):
        joined = " ".join(lines[w])
        for c in cols:
            for f, d in (("key", 2), ("cpi_avg", 2), ("food_avg", 2), ("wage", 1), ("ofz_10y", 1)):
                val = t[w][f][c]
                cand = {r(val, 1, False), r(val, 1, True), r(val, 2, True)}
                ok(any(re.search(r"(?<![\d,])" + re.escape(s) + r"(?![\d,])", joined) for s in cand),
                   f"таблица A-M: {w} {f} {c} = {val} не найдено в строках мира")

    # --- G. гигиена
    bad = re.compile(r"C:\\Users|Магнит|MGNT|magnit", re.I)
    for p in [FRAGMENT, CALENDAR, HERE / "worlds_check.py", HERE / "build_calendar.py", HERE / "verify_worlds_calendar.py",
              HERE / "worlds_check_out.txt", HERE / "worlds_check_out.json", HERE / "calendar_out.txt", HERE / "calendar_out.json",
              HERE / "inputs" / "calendar_inputs.yaml"]:
        text = p.read_text(encoding="utf-8")
        hits = [m.group(0) for m in bad.finditer(text) if not (p.name == "verify_worlds_calendar.py")]
        ok(not hits, f"гигиена {p.name}: {sorted(set(hits))}")

    print(f"VERIFY «миры и календарь»: проверок {n}, провалов {len(fails)}")
    for f in fails:
        print("  НЕТ:", f)
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
