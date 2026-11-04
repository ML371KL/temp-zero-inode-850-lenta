# -*- coding: utf-8 -*-
"""A-V7: неподвижная точка дисконта за управление g на печатаемой медиане книги — лист пересчёта листа «Оценка».

Каналы (а), (б) и потеря размещения (г) — доли капитала МОДЕЛИ (V0 − D) печатаемой медианы (решение ведущего по
аудиту 30.09.2026, п. 3): капитал E = V·N/(1 − g), поэтому g зависит от медианы V, а V — от g. Лист пересчитывает
только это — каналы на медиане книги и центр g (медиана Монте-Карло суммы независимых каналов, п. 23) — и повторяет
пересчёт с новым g, пока g в 5 знаках не перестанет меняться. Остальное — лист «Оценка» как есть:
  * входы каналов — выписки чисел `governance.py` (потоки, PV сделок, потери, шкала ликвидности, размещение 2022 года)
    и его вывода `out/governance_out.json`; первичку (МСФО, ISS) лист не читает — она там хэшами (`sources`);
  * Монте-Карло тот же: 100 000 прогонов, зерно листа 20260918, правило полос A-V9 (треугольно по половинам), каналы в
    порядке листа (а)–(е); скошенность (ж) = центр − сумма центров каналов.
На входах листа «Оценка» (медиана 3 130,6 ₽, g 0,06415) пересчёт даёт его центр бит в бит (P50 0,06414762573681532).

    python -B data/assumptions/evidence/book-1.0/valuation/governance_fixed_point.py [--median V] [--g G] [--write] [--check]

Без --median — печатаемая медиана таблиц книги (`results.json` → `headline.central`), без --g — g книги. С --write пишет
`out/governance_fixed_point.json` (каналы и итог в виде вывода листа «Оценка»): это вход правила `governance` команды
приёма миров (`ops/tools/worlds_rules.py`), которое пишет g и каналы в книгу при смене версии миров.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
OUT = HERE / "out" / "governance_fixed_point.json"
SHEET = HERE / "out" / "governance_out.json"
# выписки чисел листа «Оценка» (governance.py) — те же константы, что у листа
SHARES_OUT = 115_074_675          # акций в обращении
PRICE_BOOK = 1619.5               # цена книги, ₽
N_NEW = 18.399265                 # млн акций, размещение 2022 («Утконос»)
PLACE_PREMIUM = 1.127             # цена размещения: премия прецедента к рынку 12,7 %
P_PLACEMENT, P_CONTROL, LOSS_CONTROL = 0.20, 0.10, 0.02   # P(размещение за 10 лет); P(продажа контроля без оферты) × потеря
SEED, DRAWS, ND = 20260918, 100_000, 5
G_LOW, G_HIGH = 0.0, 0.14


def tri_from_halves(u: float, low: float, mode: float, high: float) -> float:
    """Правило A-V9 850oa (common.py листа): s ~ треугольное на [−1; 1] с модой 0, значение линейно по половинам."""
    s = math.sqrt(2 * u) - 1 if u < 0.5 else 1 - math.sqrt(2 * (1 - u))
    return mode + s * (mode - low) if s < 0 else mode + s * (high - mode)


def quantile(xs: list[float], q: float) -> float:
    s = sorted(xs)
    pos = q * (len(s) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def channels(sheet: dict, v_median: float, g: float) -> list[tuple]:
    """Каналы (id, знак, (низ, центр, верх)) на медиане V и g: (а), (б), (г) — доли капитала модели E = V·N/(1 − g)."""
    e_model = v_median * SHARES_OUT / 1e9 / (1 - g)
    pv = sheet["leakage"]["pv_bn"]
    deals, loss = sheet["deals"]["forward_pv_deals"], sheet["deals"]["loss_rate"]
    q_new = N_NEW / (SHARES_OUT / 1e6 + N_NEW)
    loss_g = q_new * (1 - PLACE_PREMIUM * PRICE_BOOK / v_median)
    g_ch = P_PLACEMENT * loss_g + P_CONTROL * LOSS_CONTROL
    fixed = {c["id"]: c for c in sheet["channels"]}
    return [("а", +1, tuple(round(x / e_model, ND) for x in pv)),
            ("б", +1, tuple(round(deals[i] * loss[i] / e_model, ND) for i in range(3))),
            ("в", fixed["в"]["sign"], (fixed["в"]["low"], fixed["в"]["value"], fixed["в"]["high"])),
            ("г", +1, (0.0, round(g_ch, ND), 0.020)),
            ("д", fixed["д"]["sign"], (fixed["д"]["low"], fixed["д"]["value"], fixed["д"]["high"])),
            ("е", fixed["е"]["sign"], (fixed["е"]["low"], fixed["е"]["value"], fixed["е"]["high"]))]


def centre(ch: list[tuple]) -> dict:
    rng = random.Random(SEED)
    mc = [sum(s * tri_from_halves(rng.random(), c[0], c[1], c[2]) for _, s, c in ch) for _ in range(DRAWS)]
    q = {k: quantile(mc, k) for k in (0.025, 0.05, 0.10, 0.50, 0.90, 0.95, 0.975)}
    g = round(q[0.50], ND)
    return {"g": g, "skew": round(g - sum(s * round(c[1], ND) for _, s, c in ch), ND), "mc_quantiles": q}


def fixed_point(v_median: float, g0: float, sheet: dict | None = None, max_iter: int = 8) -> dict:
    """g → каналы на (V, g) → центр Монте-Карло → g, пока g в 5 знаках не повторится."""
    sheet = sheet or json.loads(SHEET.read_text(encoding="utf-8"))
    g, steps = round(float(g0), ND), []
    for _ in range(max_iter):
        ch = channels(sheet, v_median, g)
        c = centre(ch)
        steps.append({"g_in": g, "g_out": c["g"], "p50": c["mc_quantiles"][0.50]})
        if c["g"] == g:
            break
        g = c["g"]
    else:
        raise RuntimeError(f"g не сошёлся за {max_iter} шагов: {steps}")
    return {"v_median": v_median, "g": c["g"], "skew": c["skew"], "steps": steps,
            "channels": [{"id": i, "sign": s, "low": t[0], "value": t[1], "high": t[2]} for i, s, t in ch]
            + [{"id": "ж", "sign": 1, "low": c["skew"], "value": c["skew"], "high": c["skew"]}],
            "result": {"governance_discount": c["g"], "skew": c["skew"], "range": [G_LOW, G_HIGH],
                       "mc_quantiles": {str(k): v for k, v in c["mc_quantiles"].items()},
                       "sum_center": round(sum(s * t[1] for _, s, t in ch), ND),
                       "comonotonic": [round(sum((t[0] if s > 0 else -t[2]) for _, s, t in ch), ND),
                                       round(sum((t[2] if s > 0 else -t[0]) for _, s, t in ch), ND)],
                       "model_frame": {"v_median": v_median, "g_fixed": c["g"],
                                       "equity_model_bn": round(v_median * SHARES_OUT / 1e9 / (1 - c["g"]), 3),
                                       "on_850oa_scope": round(sum(t[1] for i, _, t in ch if i in ("а", "в", "г", "е")), ND)}}}


def main(argv: list[str] | None = None) -> int:
    import yaml
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--median", type=float)
    ap.add_argument("--g", type=float)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--check", action="store_true", help="код 1, если g книги — не неподвижная точка на медиане таблиц")
    a = ap.parse_args(argv)
    book = ROOT / "data" / "assumptions"
    v = a.median if a.median is not None else json.loads((book / "results.json").read_text(encoding="utf-8"))["headline"]["central"]
    g0 = a.g if a.g is not None else yaml.safe_load((book / "assumptions_template.yaml").read_text(encoding="utf-8"))["valuation"]["governance_discount"]
    fp = fixed_point(v, g0)
    print(f"медиана {v:.4f} ₽, g книги {g0}: " + " → ".join(f"{s['g_out']:.5f} (P50 {s['p50']:.8f})" for s in fp["steps"]))
    print("каналы: " + ", ".join(f"({c['id']}) {c['value']:.5f}" for c in fp["channels"]))
    print(f"ИТОГ: g = {fp['g']:.5f} (скошенность (ж) {fp['skew']:+.5f}; на охвате 850oa {fp['result']['model_frame']['on_850oa_scope']:.5f})")
    if a.write:
        OUT.write_text(json.dumps(fp, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
        print(f"записано: {OUT.relative_to(ROOT).as_posix()}")
    if a.check and (fp["g"] != round(float(g0), ND) or len(fp["steps"]) != 1):
        print(f"ОТКАЗ: g книги {g0} — не неподвижная точка на медиане таблиц {v:.4f} ₽ (лист даёт {fp['g']:.5f})")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
