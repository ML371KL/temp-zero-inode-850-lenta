"""Сведение листов: цели режимов маржи (A-C1) на пути долей приобретённого периметра из листа «Сеть».

Лист «Маржа» (margin/regimes.py) считает цель режима смесью периметров
  m_r(t) = (1 − w_A(t))·m_L,r(t) + w_A(t)·m_A,r(t),   m_A,r(t) = m_A0 + f_r(t)·(m_L,r(t) − m_A0),
на временном пути w_A. Сведение (книга 1.0, решение ведущего C17) — с ЯВНЫМ слагаемым «Дом Ленты»:
  m_r(t) = (1 − w_A(t))·m_L,r(t) + w_OR(t)·[m_OR0 + f_r(t)·(m_L,r(t) − m_OR0)] + w_D(t)·m_D,r(t),
  * w_OR — доля «О'КЕЙ» + «Реми», w_D — доля «Дом Ленты» в выручке центральной клетки листа «Сеть»
    (network/checks_out.json → base_cell, H × base × mid × partial): 2026H2 — полугодие, годы — сумма полугодий,
    LT — 2036; w_A = w_OR + w_D;
  * m_OR0 — маржа «О'КЕЙ» + «Реми» 1П2026 на проформе без сезонности (margin/anchor_out.json → acquired.parts):
    маржа группы на выручку DIY не распространяется;
  * m_D,r — «Дом Лента» после ребрендинга: «дно» — безубыточность EBITDA (0); «стресс» — ниже на тот же шаг,
    что унаследованный периметр в стрессе (−LEGACY_STRESS_DROP по форме перехода G); «частичная» и «полная» —
    сходимость от безубыточности к марже унаследованного периметра с той же долей f_r(t);
  * убыток «Дом Ленты» 1П2026 (−28,9 % маржи EBITDA) — ПЕРЕХОДНЫЙ: в целях его нет, ключ "2026H1" всех режимов =
    проформа якоря без сезонности − w_D0·m_D0 (DIY в цели якоря — безубыточность), и наблюдение якоря (проформа
    5,74 %) даёт отклонение −w_D0·m_D0 ≈ −0,32 п.п., одинаковое во всех режимах (вероятности не сдвигает), которое
    затухает с ρ правилом AR(1) ядра — «затухающее отклонение» решения ведущего;
  * m_L,r(t), f_r(t), G(t), вероятности — листа «Маржа» (regimes_out.json), без изменений;
  * цели округляются до 4 знаков, как во фрагменте листа;
  * правило A-P2u (формулы update_demo.py листа «Маржа», без изменений; наблюдение якоря с ошибкой se, дальше
    отклонение якоря затухает с ρ): вероятности и E[m_LT] после факта 2П2026, нейтральная маржа, ожидание модели
    2П2026 с отклонением якоря, мартингал E[m_LT] после отчёта;
  * ожидание на 3 и 4 кв. (margin.quarter_offset_pp), проверка гайденса ≥7 % за 2026 на выручке 2П центральной
    клетки.
Первичку не читает: только выходы двух листов. Пути — от места скрипта (каталог листов — родитель; LENTA_EVIDENCE
переопределяет). Запуск: python -B regime_targets.py → печать и regime_targets_out.json рядом.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
EV = Path(os.environ.get("LENTA_EVIDENCE", str(HERE.parent)))
REG = ("stress", "floor", "partial", "full")
OKEY_REMI = ("okey", "remi")
DIY = "diy"
QOFF = {"Q3": -0.00062, "Q4": 0.00052180478821363}   # margin.quarter_offset_pp книги 1.0
REP_1H26 = (648.488059, 39.279464)                   # отчётные выручка и EBITDA IAS 17 1П2026 (датабук PL)
PF_1H26 = 705.248956                                  # проформа 1П2026 (facts.revenue.2026H1)


def load(rel: str) -> dict:
    return json.loads((EV / rel).read_text(encoding="utf-8"))


def shares(segs: dict) -> tuple[dict, dict]:
    def part(names, periods):
        return sum(segs[s][p] for s in names for p in periods) / sum(segs[s][p] for s in segs for p in periods)
    keys = {"2026H2": ["2026H2"], **{str(y): [f"{y}H1", f"{y}H2"] for y in range(2027, 2031)},
            "LT": ["2036H1", "2036H2"]}
    return ({k: part(OKEY_REMI, ps) for k, ps in keys.items()}, {k: part((DIY,), ps) for k, ps in keys.items()})


def demo(tg, prior, s, rho, sigma, obs0, se0=0.001, cap=0.10, values=(0.050, 0.055, 0.060, 0.065, 0.070, 0.075, 0.080)):
    def season(p):
        return s if p.endswith("H1") else -s

    def step(value, se, p, state, first):
        out, new = {}, {}
        for r in REG:
            dev = value - tg[r][p] - season(p)
            pm, pv = (0.0, sigma ** 2) if first else (rho * state[r][0], sigma ** 2 * (1 - rho ** 2) + rho ** 2 * state[r][1])
            err = dev - pm
            out[r] = math.exp(-0.5 * err * err / (pv + se * se))
            if se:
                g = pv / (pv + se * se)
                new[r] = (pm + g * err, (1 - g) * pv)
            else:
                new[r] = (dev, 0.0)
        return out, new

    def update(pri, lik, lim):
        z = sum(pri[r] * lik[r] for r in REG)
        post = {r: pri[r] * lik[r] / z for r in REG}
        if not lim:
            return post
        adj = {r: min(max(post[r], pri[r] - lim), pri[r] + lim) for r in REG}
        z = sum(adj.values())
        return {r: adj[r] / z for r in REG}

    def elt(p):
        return sum(p[r] * tg[r]["LT"] for r in REG)

    lik0, st0 = step(obs0, se0, "2026H1", None, True)
    p1 = update(prior, lik0, cap)
    base = elt(p1)
    rows = []
    for v in values:
        lik, _ = step(v, 0.0, "2026H2", st0, False)
        p2 = update(p1, lik, cap)
        rows.append({"obs": v, "probs": {r: round(p2[r], 4) for r in REG}, "e_lt": round(elt(p2), 6)})
    lo, hi = 0.03, 0.10
    for _ in range(80):
        mid = (lo + hi) / 2
        lik, _ = step(mid, 0.0, "2026H2", st0, False)
        if elt(update(p1, lik, cap)) > base:
            hi = mid
        else:
            lo = mid
    # ожидание модели 2П2026: цель − сезонность + затухшее отклонение якоря (ρ·dev_якоря)
    e2h = sum(p1[r] * (tg[r]["2026H2"] - s + rho * st0[r][0]) for r in REG)

    def drift(lim):
        tot = 0.0
        for r in REG:
            mu = tg[r]["2026H2"] - s + rho * st0[r][0]
            sd = math.sqrt(sigma ** 2 * (1 - rho ** 2) + rho ** 2 * st0[r][1])
            nq, acc = 241, 0.0
            for i in range(nq):
                z = -6 + 12 * i / (nq - 1)
                w = (1 if i in (0, nq - 1) else (4 if i % 2 else 2)) * (12 / (nq - 1)) / 3
                lik, _ = step(mu + z * sd, 0.0, "2026H2", st0, False)
                acc += w * math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi) * elt(update(p1, lik, lim))
            tot += p1[r] * acc
        return tot - base

    return dict(e_lt=round(base, 6), rows=rows, neutral_2026H2=round((lo + hi) / 2, 6), expected_2026H2=round(e2h, 6),
                anchor_deviation={r: round(st0[r][0], 6) for r in REG},
                by_regime_2026H2={r: round(tg[r]["2026H2"] - s + rho * st0[r][0], 6) for r in REG},
                martingale_drift_bp={"no_cap": round(drift(None) * 1e4, 4), "cap": round(drift(cap) * 1e4, 4)})


def main() -> dict:
    m = load("margin/regimes_out.json")
    ar1 = load("margin/ar1_out.json")["choice"]
    anchor = load("margin/anchor_out.json")
    segs = load("network/checks_out.json")["base_cell"]["segments"]
    w_or, w_d = shares(segs)
    s = m["seasonal_h1_pp"]
    parts = anchor["acquired"]["parts"]
    fx = anchor["facts"]
    rev_or = fx["okey_rev_pf"] + fx["remi_rev_1h26"]
    m_or0 = (parts["okey_1h"] + parts["remi"]) / rev_or - s          # без сезонности, как m_L0 и m_A0 листа
    m_d0 = parts["diy"] / fx["diy_rev"] - s
    w_d0 = fx["diy_rev"] / PF_1H26
    stress_drop = m["legacy_stress_drop"]
    anchor_key = round(m["anchor_level"] - w_d0 * m_d0, 4)
    targets, diy_paths = {}, {}
    for r in REG:
        t, dp = {"2026H1": anchor_key}, {}
        for k, p in m["parts"][r].items():
            mL, f, g = p["m_legacy"], p["f"], m["G"][k]
            m_or = m_or0 + f * (mL - m_or0)
            m_diy = {"stress": -stress_drop * g, "floor": 0.0}.get(r, f * mL)
            dp[k] = m_diy
            t[k] = round((1 - w_or[k] - w_d[k]) * mL + w_or[k] * m_or + w_d[k] * m_diy, 4)
        targets[r], diy_paths[r] = t, dp
    prior = m["probs"]
    d = demo(targets, prior, s, ar1["rho"], ar1["sigma_pp"], anchor["margin_pro_forma"])
    rev2h = sum(v["2026H2"] for v in segs.values())
    need = (0.07 * (REP_1H26[0] + rev2h) - REP_1H26[1]) / rev2h
    fy = (REP_1H26[1] + d["expected_2026H2"] * rev2h) / (REP_1H26[0] + rev2h)
    best = max(d["by_regime_2026H2"].values())
    e_path = {k: round(sum(prior[r] * targets[r][k] for r in REG), 6) for k in targets["stress"]}
    return dict(w_okey_remi={k: round(v, 4) for k, v in w_or.items()}, w_diy={k: round(v, 4) for k, v in w_d.items()},
                m_okey_remi0=round(m_or0, 6), m_diy0=round(m_d0, 6), w_diy0=round(w_d0, 6), anchor_key=anchor_key,
                diy_margin_by_regime={r: {k: round(v, 6) for k, v in dp.items()} for r, dp in diy_paths.items()},
                targets=targets, expected_target=e_path, update_rule=d,
                quarter_expect_2026={q: round(d["expected_2026H2"] + off, 6) for q, off in QOFF.items()},
                guidance=dict(rev_2h_center=round(rev2h, 3), need_2h=round(need, 6), fy_model=round(fy, 6),
                              best_regime_2h=round(best, 6),
                              regimes_reaching=sum(1 for v in d["by_regime_2026H2"].values() if v >= need)))


if __name__ == "__main__":
    res = main()
    print("w «О'КЕЙ» + «Реми»:", res["w_okey_remi"], "; w «Дом Лента»:", res["w_diy"])
    print(f"m_OR0 {res['m_okey_remi0'] * 100:.3f} %; m_D0 {res['m_diy0'] * 100:.2f} % (w_D0 {res['w_diy0']:.5f}); "
          f"ключ \"2026H1\" {res['anchor_key']}")
    for r in REG:
        print(f"  {r:8s} {res['targets'][r]}")
    print("ожидаемая цель по периодам:", res["expected_target"])
    d = res["update_rule"]
    print(f"E[m_LT] {d['e_lt'] * 100:.3f} %; ожидание 2П2026 {d['expected_2026H2'] * 100:.3f} %; "
          f"нейтральная {d['neutral_2026H2'] * 100:.3f} %; мартингал {d['martingale_drift_bp']} б.п.; "
          f"отклонение якоря {d['anchor_deviation']}; по режимам {d['by_regime_2026H2']}")
    for row in d["rows"]:
        print(f"   факт {row['obs'] * 100:.1f} %: " + " / ".join(f"{row['probs'][r] * 100:.1f}" for r in REG)
              + f"; E[m_LT] {row['e_lt'] * 100:.3f} %")
    print("ожидание на квартал 2026:", res["quarter_expect_2026"], "; гайденс:", res["guidance"])
    (HERE / "regime_targets_out.json").write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n",
                                                  encoding="utf-8")
