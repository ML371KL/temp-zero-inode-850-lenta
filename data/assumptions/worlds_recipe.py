# -*- coding: utf-8 -*-
"""Рецепт макро-миров — книга-источник (850oa) — исполняемая спецификация к WORLDS-RECIPE.md.

КОПИЯ-КАНДИДАТ ДЛЯ КНИГИ 1.4 (доказательный лист evidence/book-1.4/worlds, 24.09.2026). Отличия от канона 1.3.1:
  * переключатель источника инфляции мира M: `m_inflation.source` во входах = judgement | forward_bei;
    judgement — ровно канон 1.3.1 (--check воспроизводит worlds_source.json 1.3.1 бит в бит);
    forward_bei — инфляция M = рыночные форвардные BEI на дату кривой (RealCurve + forward_bei_path ниже);
  * при forward_bei меняются только ряды мира M, зависящие от инфляции (cpi_eop, cpi_avg, food, wage,
    real_key_fwd12m, real_wage), заметка про cpi M и добавляется блок `lt_inflation` (pi_ss миров для build_assumptions);
  * новые ключи запуска: --m-inflation, --liquidity-adj, --candidate DIR (пишет json+csv), --check-nh.

Ярус 3 (механика): build_worlds(inputs, curve) -> объект worlds_source.json. Обязан воспроизводить канон книги
из worlds_inputs.yaml бит в бит после округления (проверка: --check).
Ярус 2 (правила якорей): candidate_anchors(inputs, curve) -> якоря-кандидаты по опубликованным таблицам
(прогноз ЦБ, ОНДКП, макроопрос, форварды). Печатаются рядом с якорями записи; в книгу попадают решением аудитора.

Запуск (из каталога книги):
  python -B worlds_recipe.py --check                                   # воспроизведение worlds_source.json
  python -B worlds_recipe.py --release <адрес выпуска>/api/model --out candidate-2026-10-23
  python -B worlds_recipe.py --curve zcyc.json --curve-date 2026-10-23 --out DIR
Без numpy: только стандартная библиотека и PyYAML.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
import urllib.request
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
TENORS = (1, 3, 5, 10)


# ----------------------------------------------------------------------------- сетка периодов
def parse_period(p: str) -> tuple[int, int]:
    return int(p[:4]), int(p[5])


def periods_of(first: str, n: int) -> list[str]:
    y, h = parse_period(first)
    out = []
    for _ in range(n):
        out.append(f"{y}H{h}")
        y, h = (y, 2) if h == 1 else (y + 1, 1)
    return out


def mid_date(p: str) -> dt.date:
    """Середина полугодия: 1 апреля для H1, 1 октября для H2."""
    y, h = parse_period(p)
    return dt.date(y, 4, 1) if h == 1 else dt.date(y, 10, 1)


def path(anchors: dict, steady: float, periods: list[str]) -> list[float]:
    """Якоря по периодам, после последнего якоря — установившееся значение."""
    return [float(anchors.get(p, steady)) for p in periods]


# ----------------------------------------------------------------------------- кривая
class Curve:
    """Бескупонная кривая ОФЗ (годовое начисление, % годовых) с линейной интерполяцией в непрерывной ставке."""

    def __init__(self, nodes: dict, date: str):
        pts = sorted((float(k), float(v)) for k, v in nodes.items())
        self.T = [t for t, _ in pts]
        self.Y = [v for _, v in pts]
        self.G = [math.log(1 + v / 100) for v in self.Y]
        self.nodes = {t: v for t, v in pts}
        self.date = date

    def g(self, t: float) -> float:
        T, G = self.T, self.G
        if t <= T[0]:
            return G[0]
        if t >= T[-1]:
            return G[-1]
        for i in range(1, len(T)):
            if t <= T[i]:
                w = (t - T[i - 1]) / (T[i] - T[i - 1])
                return G[i - 1] + w * (G[i] - G[i - 1])
        return G[-1]

    def spot(self, t: float) -> float:
        return math.exp(self.g(t)) - 1

    def fwd(self, a: float, b: float) -> float:
        """Форвардная ставка между a и b годами (годовое начисление); при a <= 0 — спот до b."""
        if a <= 0:
            return math.exp(self.g(b)) - 1
        return math.exp((self.g(b) * b - self.g(a) * a) / (b - a)) - 1

    def node(self, t: float) -> float:
        """Узел кривой в процентах; если узла нет — интерполированный спот."""
        return self.nodes[t] if t in self.nodes else self.spot(t) * 100

    @staticmethod
    def from_release(payload: dict) -> "Curve":
        live = payload["live"]
        nodes = {k: v * 100 for k, v in live["observed_curve"].items()}
        if not nodes:
            raise SystemExit("в выпуске нет наблюдаемой кривой (live.observed_curve пуст — кривая отвергнута)")
        return Curve(nodes, live["observed_curve_date"])

    @staticmethod
    def from_zcyc(payload: dict, date: str) -> "Curve":
        block = payload["yearyields"]
        cols = block["columns"]
        ip, iv = cols.index("period"), cols.index("value")
        return Curve({row[ip]: row[iv] for row in block["data"]}, date)


def real(nom: float, infl: float) -> float:
    return ((1 + nom / 100) / (1 + infl / 100) - 1) * 100


# ----------------------------------------------------------------------------- книга 1.4: инфляция мира M по форвардным BEI
def add_months(d: dt.date, months: int) -> dt.date:
    y, m = divmod(d.month - 1 + months, 12)
    y, m = d.year + y, m + 1
    days = [31, 29 if (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return dt.date(y, m, min(d.day, days))


def half_bounds(p: str) -> tuple[dt.date, dt.date]:
    """Календарные границы полугодия: [1 января, 1 июля) или [1 июля, 1 января)."""
    y, h = parse_period(p)
    return (dt.date(y, 1, 1), dt.date(y, 7, 1)) if h == 1 else (dt.date(y, 7, 1), dt.date(y + 1, 1, 1))


def ofz_in_cashflows(bond: dict, settle: dt.date, coupon_pct: float, period_days: int) -> list[tuple[dt.date, float]]:
    """Реальные потоки ОФЗ-ИН в % текущего индексированного номинала: купон coupon_pct·period_days/365
    каждые period_days дней назад от погашения (график MOEX bondization) + 100 в погашение."""
    mat = dt.date.fromisoformat(bond["maturity"])
    c = coupon_pct * period_days / 365
    dates, d = [], mat
    while d > settle:
        dates.append(d)
        d -= dt.timedelta(days=period_days)
    dates.sort()
    return [(x, c + (100.0 if x == mat else 0.0)) for x in dates]


def dirty_from_yield(cfs: list[tuple[dt.date, float]], y_pct: float, settle: dt.date) -> float:
    """Грязная цена по доходности MOEX (эффективная годовая, дни/365 от даты расчётов T+1)."""
    return sum(cf / (1 + y_pct / 100) ** ((d - settle).days / 365) for d, cf in cfs)


class RealCurve(Curve):
    """Реальная бескупонная кривая ОФЗ-ИН. Узлы — даты погашения выпусков, непрерывная ставка линейна между
    узлами, до первого и после последнего узла постоянна — та же конвенция, что у номинальной Curve рецепта.
    Время — годы (дни/365,25) от даты расчётов. Узлы подбираются последовательно так, чтобы каждый выпуск
    переоценивался в точности по своей доходности (бутстрэп с купонами)."""

    def __init__(self, T: list[float], G: list[float], origin: dt.date, tail_fwd: float | None = None,
                 interp: str = "linear_zero"):
        self.T, self.G, self.origin = list(T), list(G), origin
        self.Y = [100 * (math.exp(g) - 1) for g in G]
        self.nodes = {t: y for t, y in zip(self.T, self.Y)}
        self.date = origin.isoformat()
        self.tail_fwd = tail_fwd          # None — за последним узлом постоянная непрерывная СТАВКА (как Curve рецепта);
                                          # число — постоянный непрерывный ФОРВАРД за последним узлом (вариант оси)
        self.interp = interp              # linear_zero — линейно по непрерывной ставке (как Curve рецепта);
                                          # flat_forward — постоянный форвард между узлами (вариант оси)

    def g(self, t: float) -> float:
        if self.tail_fwd is not None and self.T and t > self.T[-1]:
            return (self.G[-1] * self.T[-1] + self.tail_fwd * (t - self.T[-1])) / t
        if self.interp == "flat_forward" and self.T[0] < t < self.T[-1]:
            for i in range(1, len(self.T)):
                if t <= self.T[i]:
                    a, b = self.G[i - 1] * self.T[i - 1], self.G[i] * self.T[i]
                    return (a + (b - a) * (t - self.T[i - 1]) / (self.T[i] - self.T[i - 1])) / t
        return super().g(t)

    def with_flat_forward_tail(self) -> "RealCurve":
        """Вариант оси: за последним узлом — средний непрерывный форвард между первым и последним узлом."""
        f = (self.G[-1] * self.T[-1] - self.G[0] * self.T[0]) / (self.T[-1] - self.T[0])
        return RealCurve(self.T, self.G, self.origin, tail_fwd=f, interp=self.interp)

    def t_of(self, d: dt.date) -> float:
        return (d - self.origin).days / 365.25

    @staticmethod
    def bootstrap(bonds: list[dict], settle: dt.date, coupon_pct: float, period_days: int,
                  yield_key: str = "real_yield", interp: str = "linear_zero") -> "RealCurve":
        bonds = sorted(bonds, key=lambda b: b["maturity"])
        T, G = [], []
        for b in bonds:
            cfs = ofz_in_cashflows(b, settle, coupon_pct, period_days)
            price = dirty_from_yield(cfs, float(b[yield_key]), settle)
            tm = (dt.date.fromisoformat(b["maturity"]) - settle).days / 365.25
            lo, hi = -0.5, 0.5
            for _ in range(200):                       # бисекция по непрерывной ставке нового узла
                g = (lo + hi) / 2
                c = RealCurve(T + [tm], G + [g], settle, interp=interp)
                pv = sum(cf * math.exp(-c.g(c.t_of(d)) * c.t_of(d)) for d, cf in cfs)
                if pv > price:
                    lo = g
                else:
                    hi = g
            T.append(tm)
            G.append((lo + hi) / 2)
        return RealCurve(T, G, settle, interp=interp)


def bei_window(nom: Curve, rc: RealCurve, curve_date: dt.date, a: dt.date, b: dt.date, lag_months: int) -> float:
    """Форвардная BEI (% годовых, годовое начисление) за календарное окно инфляции [a, b].
    Поток ОФЗ-ИН в дату T индексирован ИПЦ месяца T − лаг, поэтому инфляция за [a, b] — это отношение
    форвардов номинальной и реальной кривых за окно потоков [a + лаг, b + лаг] (Фишер, мультипликативно)."""
    A, B = add_months(a, lag_months), add_months(b, lag_months)
    ta_n, tb_n = max(0.0, (A - curve_date).days / 365.25), (B - curve_date).days / 365.25
    ta_r, tb_r = max(0.0, rc.t_of(A)), rc.t_of(B)
    fn = nom.fwd(ta_n, tb_n) if ta_n > 0 else nom.spot(tb_n)
    fr = rc.fwd(ta_r, tb_r) if ta_r > 0 else rc.spot(tb_r)
    return ((1 + fn) / (1 + fr) - 1) * 100


def forward_bei_path(inp: dict, curve: Curve | None = None, liquidity_adj_pp: float | None = None,
                     bonds: list[dict] | None = None, pi_h_override: list[float] | None = None) -> dict:
    """Инфляция мира M по рыночным форвардным BEI на дату кривой (решение владельца к книге 1.4).

    1. Реальная кривая — бутстрэп ОФЗ-ИН (RealCurve); номинальная — Curve рецепта (те же узлы zcyc, что у мира M).
    2. pi_h[j] — форвардная BEI за календарное полугодие j (с лагом индексации) + поправка на ликвидность.
    3. Сглаживание — центрированное среднее трёх полугодий, как у ключевой мира M (форварды на полугодовой сетке зубчатые).
    4. ИПЦ на конец периода i >= 1: год к году из двух сглаженных полугодий (i−1, i); средний — среднее соседних концов
       (правило M рецепта); текущий период (окно года к году почти целиком в прошлом) — запись книги.
    5. pi_ss — среднее несглаженных pi_h за последние шесть периодов сетки, округление 0,1 (как установившаяся ключевая M)."""
    MI = inp["m_inflation"]
    OI = inp["ofz_in"]
    curve = curve or Curve(inp["curve"]["nodes"], inp["curve"]["date"])
    cdate = dt.date.fromisoformat(curve.date)
    settle = dt.date.fromisoformat(OI["settlement_date"])
    rc = RealCurve.bootstrap(bonds or OI["bonds"], settle, float(OI["coupon_pct"]), int(OI["coupon_period_days"]),
                             interp=MI.get("real_interpolation", "linear_zero"))
    if MI.get("real_extrapolation", "flat") == "flat_forward":
        rc = rc.with_flat_forward_tail()
    lag = int(MI["index_lag_months"])
    adj = float(MI.get("liquidity_adj_pp", 0.0) if liquidity_adj_pp is None else liquidity_adj_pp)
    cstep = float(inp["anchor_rules"]["cpi_round_pp"])
    periods = periods_of(inp["grid"]["first_period"], int(inp["grid"]["n_periods"]))
    n = len(periods)
    if pi_h_override is None:
        pi_h = [bei_window(curve, rc, cdate, *half_bounds(p), lag) + adj for p in periods]
    else:                                                  # готовый путь (напр. среднее по окну дат) — дальше та же обработка
        pi_h = [float(x) + adj for x in pi_h_override]
    if MI.get("smoothing", "centered3") == "centered3":
        sm = [sum(pi_h[max(0, i - 1):i + 2]) / len(pi_h[max(0, i - 1):i + 2]) for i in range(n)]
    else:
        sm = list(pi_h)
    first = periods.index(MI.get("first_market_period", periods[1]))
    rec = inp["worlds"]["M"]
    eop_rec = path(rec["anchors"]["cpi_eop"], rec["steady"]["cpi_eop"], periods)
    avg_rec = path(rec["anchors"]["cpi_avg"], rec["steady"]["cpi_avg"], periods)
    eop_raw = list(eop_rec)
    for i in range(first, n):
        eop_raw[i] = (math.sqrt((1 + sm[i - 1] / 100) * (1 + sm[i] / 100)) - 1) * 100
    eop = [eop_rec[i] if i < first else rnd(eop_raw[i], cstep) for i in range(n)]
    avg = [avg_rec[i] if i < first else rnd((eop_raw[i - 1] + eop_raw[i]) / 2, cstep) for i in range(n)]
    k = int(MI.get("lt_window_periods", 6))
    pi_ss = rnd(sum(pi_h[-k:]) / k, cstep)
    return {"periods": periods, "pi_h": pi_h, "pi_h_smoothed": sm, "eop_unrounded": eop_raw,
            "cpi_eop": eop, "cpi_avg": avg, "pi_ss": pi_ss, "pi_ss_unrounded": sum(pi_h[-k:]) / k,
            "first_market_period": periods[first], "liquidity_adj_pp": adj, "lag_months": lag,
            "real_curve": {"settle": settle.isoformat(), "T": rc.T, "zero_real_pct": rc.Y}}


def m_anchors_forward_bei(inp: dict, bei: dict) -> tuple[dict, dict]:
    """Якоря мира M при forward_bei: меняется только инфляция. Спред продовольствия к ИПЦ и реальные зарплаты
    берутся из записи 1.3.1 по периодам (с 2028 это ровно правило рецепта: +0,5 п.п. и ≈1,4 %), поэтому
    food = cpi_avg + (food − cpi_avg)_записи, wage = (1 + cpi_avg)(1 + реальная зарплата записи) − 1."""
    cstep = float(inp["anchor_rules"]["cpi_round_pp"])
    rec = inp["worlds"]["M"]
    periods = bei["periods"]
    A, S = rec["anchors"], rec["steady"]
    avg_rec = path(A["cpi_avg"], S["cpi_avg"], periods)
    food_rec = path(A["food_avg"], S["food_avg"], periods)
    wage_rec = path(A["wage"], S["wage"], periods)
    anchors = {"key": dict(A["key"]), "cpi_eop": {}, "cpi_avg": {}, "food_avg": {}, "wage": {}}
    for i, p in enumerate(periods):
        a = bei["cpi_avg"][i]
        anchors["cpi_eop"][p] = bei["cpi_eop"][i]
        anchors["cpi_avg"][p] = a
        if a == avg_rec[i]:                              # инфляция периода не изменилась — ряды записи как есть
            anchors["food_avg"][p], anchors["wage"][p] = food_rec[i], wage_rec[i]
            continue
        anchors["food_avg"][p] = rnd(a + (food_rec[i] - avg_rec[i]), cstep)
        rw = (1 + wage_rec[i] / 100) / (1 + avg_rec[i] / 100) - 1
        anchors["wage"][p] = rnd(((1 + a / 100) * (1 + rw) - 1) * 100, cstep)
    ss = bei["pi_ss"]
    rw_ss = (1 + float(S["wage"]) / 100) / (1 + float(S["cpi_avg"]) / 100) - 1
    steady = {"key": S["key"], "cpi_eop": ss, "cpi_avg": ss,
              "food_avg": rnd(ss + (float(S["food_avg"]) - float(S["cpi_avg"])), cstep),
              "wage": rnd(((1 + ss / 100) * (1 + rw_ss) - 1) * 100, cstep)}
    return anchors, steady


def with_m_inflation(inp: dict, curve: Curve | None = None, liquidity_adj_pp: float | None = None) -> tuple[dict, dict | None]:
    """Входы с якорями мира M по выбранному источнику инфляции. judgement — входы как есть (канон)."""
    MI = inp.get("m_inflation") or {}
    if MI.get("source", "judgement") != "forward_bei":
        return inp, None
    import copy
    out = copy.deepcopy(inp)
    bei = forward_bei_path(inp, curve, liquidity_adj_pp)
    anchors, steady = m_anchors_forward_bei(inp, bei)
    out["worlds"]["M"]["anchors"] = anchors
    out["worlds"]["M"]["steady"] = steady
    out["worlds"]["M"]["pi_ss"] = bei["pi_ss"]
    out["worlds"]["M"]["basis"] = MI.get("basis_forward_bei", out["worlds"]["M"]["basis"])
    note = MI.get("note_forward_bei")
    if note:
        out["notes"] = [(note.format(pi_ss=bei["pi_ss"], adj=bei["liquidity_adj_pp"]) if x.startswith("cpi for M") else x)
                        for x in out["notes"]]
    return out, bei


# ----------------------------------------------------------------------------- ярус 3: механика
def exp_short(key: list[float], i: int, tenor: float, spread: float) -> float:
    """Среднее (ключевая + спред) на горизонте tenor лет от периода i при полном предвидении пути; за сеткой — последнее значение."""
    n = len(key)
    k = int(round(tenor * 2))
    vals = [(key[j] if j < n else key[-1]) + spread for j in range(i, i + k)]
    return sum(vals) / k


def first_period_mid(inp: dict, curve: Curve, periods: list[str]) -> float:
    mid0 = inp["grid"].get("first_period_mid_years")
    if mid0 is None:
        mid0 = max(0.0, (mid_date(periods[0]) - dt.date.fromisoformat(curve.date)).days / 365.25)
    return float(mid0)


def build_worlds(inp: dict, curve: Curve | None = None, mid0: float | None = None,
                 liquidity_adj_pp: float | None = None) -> dict:
    if (inp.get("m_inflation") or {}).get("source") == "forward_bei":
        c0 = curve or Curve(inp["curve"]["nodes"], inp["curve"]["date"])
        if inp["ofz_in"].get("date") != c0.date:
            raise SystemExit(f"forward_bei: реальные доходности ОФЗ-ИН ({inp['ofz_in'].get('date')}) должны быть на дату кривой ({c0.date})")
    inp, bei = with_m_inflation(inp, curve, liquidity_adj_pp)
    P = inp["parameters"]
    spread = float(P["spread_short_vs_key_pp"])
    W = {int(k): float(v) for k, v in P["tp_shape"].items()}
    blend = float(P["first_period_blend_observed"])
    rd = int(P["round_decimals"])
    periods = periods_of(inp["grid"]["first_period"], int(inp["grid"]["n_periods"]))
    n = len(periods)
    curve = curve or Curve(inp["curve"]["nodes"], inp["curve"]["date"])
    mid0 = first_period_mid(inp, curve, periods) if mid0 is None else mid0
    mid = [mid0 + 0.5 * i for i in range(n)]

    rows, fair = [], {}
    for name, w in inp["worlds"].items():
        A, S = w["anchors"], w["steady"]
        key = path(A["key"], S["key"], periods)
        cpi_eop = path(A["cpi_eop"], S["cpi_eop"], periods)
        cpi_avg = path(A["cpi_avg"], S["cpi_avg"], periods)
        food = path(A["food_avg"], S["food_avg"], periods)
        wage = path(A["wage"], S["wage"], periods)
        market = w.get("tp10_ss_pp") is None            # мир без срочной премии = рыночные форварды как есть
        y = {t: [0.0] * n for t in TENORS}
        if market:
            for t in TENORS:
                for i in range(n):
                    y[t][i] = 100 * curve.fwd(mid[i], mid[i] + t)
            fair[name] = {t: curve.node(float(t)) for t in TENORS}
        else:
            tp_ss, lam = float(w["tp10_ss_pp"]), math.log(2) / float(w["tp_halflife_years"])
            tp0 = curve.node(10.0) - exp_short(key, 0, 10, spread)   # премия 10 лет, вменённая сегодняшней кривой при пути мира
            for i in range(n):
                tp10 = tp_ss + (tp0 - tp_ss) * math.exp(-lam * mid[i])
                for t in TENORS:
                    y[t][i] = exp_short(key, i, t, spread) + W[t] * tp10
            fair[name] = {t: exp_short(key, 0, t, spread) + W[t] * tp_ss for t in TENORS}
        for t in TENORS:                                   # первый период: наблюдаемая кривая (M) или смесь (N/H)
            obs = curve.node(float(t))
            y[t][0] = obs if market else blend * obs + (1 - blend) * y[t][0]
        for i, p in enumerate(periods):
            rows.append({
                "world": name, "period": p, "key_avg": round(key[i], rd),
                "cpi_yoy_eop": cpi_eop[i], "cpi_yoy_avg": cpi_avg[i], "food_cpi_yoy_avg": food[i], "nominal_wage_yoy": wage[i],
                "ofz_1y": round(y[1][i], rd), "ofz_3y": round(y[3][i], rd), "ofz_5y": round(y[5][i], rd), "ofz_10y": round(y[10][i], rd),
                "real_key_fwd12m": round(real(key[i], cpi_eop[min(i + 2, n - 1)]), rd),
                "real_wage": round(real(wage[i], cpi_avg[i]), rd),
            })
    out = {
        "asof": inp["asof"], "units": inp["units"],
        "probabilities": dict(inp["probabilities"]), "probability_ranges": {k: list(v) for k, v in inp["probability_ranges"].items()},
        "rows": rows,
        "fair_zero_curve_today": {k: {str(t): round(v, rd) for t, v in d.items()} for k, d in fair.items()},
        "notes": list(inp["notes"]),
    }
    if bei is not None:                                    # книга 1.4: pi_ss миров для build_assumptions (lt.inflation, тарифы LT)
        out["lt_inflation"] = {k: float(w["pi_ss"]) for k, w in inp["worlds"].items()}
        out["m_inflation"] = {"source": "forward_bei", "curve_date": inp["curve"]["date"] if curve is None else curve.date,
                              "ofz_in_date": inp["ofz_in"]["date"], "index_lag_months": bei["lag_months"],
                              "liquidity_adj_pp": bei["liquidity_adj_pp"], "pi_ss": bei["pi_ss"],
                              "pi_ss_unrounded": round(bei["pi_ss_unrounded"], 4),
                              "first_market_period": bei["first_market_period"],
                              "pi_half_year_fwd": {p: round(v, 4) for p, v in zip(bei["periods"], bei["pi_h"])},
                              "pi_half_year_fwd_smoothed": {p: round(v, 4) for p, v in zip(bei["periods"], bei["pi_h_smoothed"])}}
    return out


def curve_lt_rule(ws: dict, rd: int = 1) -> dict:
    """Правило 1.4a для уровня кривой в терминале: ОФЗ 10 лет последнего периода сетки, округление до 0,1."""
    out = {}
    for r in ws["rows"]:
        out[r["world"]] = round(r["ofz_10y"], rd)
    return out


# ----------------------------------------------------------------------------- ярус 2: правила якорей
def mid_of(v) -> float:
    return (float(v[0]) + float(v[1])) / 2 if isinstance(v, (list, tuple)) else float(v)


def rnd(x: float, step: float) -> float:
    return round(round(x / step) * step, 6)


def extend_to_steady(annual: dict[int, float], steady: float, converge_year: int) -> dict[int, float]:
    """После последнего года таблицы — линейно к установившемуся значению к converge_year."""
    out = dict(annual)
    last = max(out)
    if converge_year > last:
        for y in range(last + 1, converge_year + 1):
            out[y] = out[last] + (steady - out[last]) * (y - last) / (converge_year - last)
    return out


def split_halves(annual: dict[int, float], steady: float, step: float, first_full_year: int, converge_year: int,
                 prev_first: float | None = None) -> dict[str, float]:
    """Годовые средние -> полугодовые по правилу наклона: H1 = A + s/4, H2 = A − s/4, s = (A[y−1] − A[y+1])/2;
    для первого полного года «предыдущее» значение — текущее полугодие (prev_first), чтобы спуск шёл от сегодняшнего уровня."""
    ext = extend_to_steady(annual, steady, converge_year)
    out = {}
    for y in range(first_full_year, converge_year + 1):
        a = ext[y]
        prev = ext.get(y - 1, prev_first if (y == first_full_year and prev_first is not None) else a)
        nxt = ext.get(y + 1, steady)
        s = (prev - nxt) / 2
        out[f"{y}H1"], out[f"{y}H2"] = rnd(a + s / 4, step), rnd(a - s / 4, step)
    return out


def candidate_anchors(inp: dict, curve: Curve | None = None) -> tuple[dict, list[str]]:
    """Якоря-кандидаты по правилам яруса 2. Возвращает ({мир: {поле: {период: значение}}}, замечания)."""
    R, notes = inp["anchor_rules"], []
    periods = periods_of(inp["grid"]["first_period"], int(inp["grid"]["n_periods"]))
    curve = curve or Curve(inp["curve"]["nodes"], inp["curve"]["date"])
    mid0 = first_period_mid(inp, curve, periods)
    mid = [mid0 + 0.5 * i for i in range(len(periods))]
    cur_year, cur_half = parse_period(periods[0])
    first_full = cur_year + 1
    conv = int(R["converge_year"])
    kstep, cstep = float(R["key_round_pp"]), float(R["cpi_round_pp"])
    cbr, ondkp, survey = inp["cbr_forecast"], inp["ondkp"], inp["survey"]
    out = {}

    def food_wage(world: str, cpi_avg: dict[str, float]) -> tuple[dict, dict]:
        fs, rw = float(R["food_spread_lt_pp"][world]), float(R["real_wage_lt_pct"][world])
        food = {p: rnd(v + fs, cstep) for p, v in cpi_avg.items()}
        wage = {p: rnd(((1 + v / 100) * (1 + rw / 100) - 1) * 100, cstep) for p, v in cpi_avg.items()}
        return food, wage

    # ---- N: середина диапазона ЦБ, смешанная с медианой макроопроса
    wN, sN = inp["worlds"]["N"], inp["worlds"]["N"]["steady"]
    w_s = float(survey.get("weight_in_N", 0))
    annual = {}
    for ys, v in cbr["key_avg"].items():
        y = int(ys)
        if y == cur_year:
            continue
        m = mid_of(v)
        sv = survey.get("key_avg") or {}
        annual[y] = (1 - w_s) * m + w_s * float(sv[ys]) if ys in sv else m
    if not (survey.get("key_avg") or {}):
        notes.append("макроопрос не заполнен — N = середина диапазона ЦБ без смешивания")
    cur = inp.get("current_half_actual")
    if cur and cur.get("avg") is not None:                 # средняя факт с начала полугодия × дни + сноска × оставшиеся дни
        h_start = dt.date(cur_year, 7 if cur_half == 2 else 1, 1)
        h_end = dt.date(cur_year, 12, 31) if cur_half == 2 else dt.date(cur_year, 6, 30)
        to = dt.date.fromisoformat(cur["to"])
        d1, d2 = (to - h_start).days + 1, (h_end - to).days
        cur_val = rnd((float(cur["avg"]) * d1 + mid_of(cbr["rest_of_year"]["range"]) * d2) / (d1 + d2), 0.05)
    else:
        cur_val = rnd(mid_of(cbr["rest_of_year"]["range"]), 0.05)
        notes.append("текущее полугодие N: середина сноски ЦБ без взвешивания фактом (current_half_actual не задан)")
    keyN = split_halves(annual, float(sN["key"]), kstep, first_full, conv, prev_first=cur_val)
    keyN[periods[0]] = cur_val
    eopN, avgN = {}, {}
    prev_eop = None
    for ys, v in sorted(cbr["cpi_eop"].items()):
        y, m = int(ys), mid_of(v)
        if y >= cur_year:
            if y > cur_year and prev_eop is not None:
                eopN[f"{y}H1"] = rnd((prev_eop + m) / 2, cstep)
            eopN[f"{y}H2"] = rnd(m, cstep)
        prev_eop = m
    avg_annual = {int(ys): mid_of(v) for ys, v in cbr["cpi_avg"].items() if int(ys) > cur_year}
    avg_cur = rnd(mid_of(cbr["cpi_avg"][str(cur_year)]), cstep)
    avgN = split_halves(avg_annual, float(sN["cpi_avg"]), cstep, first_full, conv, prev_first=avg_cur)
    avgN[periods[0]] = avg_cur
    foodN, wageN = food_wage("N", avgN)
    out["N"] = {"key": keyN, "cpi_eop": eopN, "cpi_avg": avgN, "food_avg": foodN, "wage": wageN}

    # ---- H: проинфляционный сценарий ОНДКП + сдвиг со второго года, затем линейно к установившемуся
    wH, sH = inp["worlds"]["H"], inp["worlds"]["H"]["steady"]
    pro = ondkp["proinflation"]["key_avg"]
    years = sorted(int(y) for y in pro)
    shift, off = float(R["h_shift_pp"]), int(R["h_shift_from_year_offset"])
    annualH = {y: mid_of(pro[str(y)]) + (shift if y >= years[0] + off else 0.0) for y in years}
    keyH = split_halves(annualH, float(sH["key"]), kstep, first_full, conv, prev_first=cur_val)
    keyH[periods[0]] = cur_val
    pro_cpi = ondkp["proinflation"].get("cpi_eop") or {}
    hi = cbr["cpi_eop"][str(cur_year)]
    e0 = float(hi[1]) if isinstance(hi, (list, tuple)) else float(hi)   # текущий год: верхняя граница диапазона ЦБ
    eopH = {periods[0]: rnd(e0, cstep)}
    if pro_cpi:
        prev = e0
        for ys, v in sorted(pro_cpi.items()):
            y, m = int(ys), mid_of(v)
            eopH[f"{y}H1"], eopH[f"{y}H2"] = rnd((prev + m) / 2, cstep), rnd(m, cstep)
            prev = m
    else:
        prev = e0
        for y in range(first_full, conv + 1):
            e = e0 + (float(sH["cpi_eop"]) - e0) * (y - cur_year) / (conv - cur_year)
            eopH[f"{y}H1"], eopH[f"{y}H2"] = rnd((prev + e) / 2, cstep), rnd(e, cstep)
            prev = e
        notes.append("ОНДКП без инфляции проинфляционного сценария — H: от верхней границы диапазона ЦБ текущего года линейно к установившемуся значению")
    avgH = {p: rnd(v + (float(sH["cpi_avg"]) - float(sN["cpi_avg"])), cstep) for p, v in avgN.items()}
    foodH, wageH = food_wage("H", avgH)
    out["H"] = {"key": keyH, "cpi_eop": eopH, "cpi_avg": avgH, "food_avg": foodH, "wage": wageH}

    # ---- M: 6-месячный форвард + надбавка; инфляция линейно к pi_ss
    wM, sM = inp["worlds"]["M"], inp["worlds"]["M"]["steady"]
    o = inp["parameters"]["m_key_offset_pp"]
    o0, o1 = float(o["start"]), float(o["steady"])
    k_conv = max(1, periods.index(f"{conv}H2") if f"{conv}H2" in periods else len(periods) - 1)
    keyM = {}
    f6 = [100 * curve.fwd(mid[i], mid[i] + 0.5) for i in range(len(periods))]
    k_now = inp.get("current_key_rate")
    for i, p in enumerate(periods[:k_conv + 1]):
        if i == 0 and k_now is not None:                   # текущее полугодие: действующая ключевая ставка
            keyM[p] = float(k_now)
            continue
        window = f6[max(0, i - 1):i + 2]                   # центрированное среднее трёх полугодий: форварды на полугодовой сетке зубчатые
        keyM[p] = rnd(sum(window) / len(window) + o0 + (o1 - o0) * min(1.0, i / k_conv), 0.1)
    keyM["steady"] = rnd(sum(f6[-6:]) / 6 + o1, 0.1)
    eop0 = mid_of(cbr["cpi_eop"][str(cur_year)])
    halves = int(R["m_cpi_converge_halves"])
    eopM, avgM = {}, {}
    prev = mid_of(cbr["cpi_avg"][str(cur_year)])
    if (inp.get("m_inflation") or {}).get("source") == "forward_bei":   # книга 1.4: с первого рыночного периода — форвардные BEI
        bei = forward_bei_path(inp, curve)
        first = bei["periods"].index(bei["first_market_period"])
        for i, p in enumerate(periods):
            if i < first:
                eopM[p] = rnd(eop0, cstep)
                avgM[p] = rnd((prev + eop0) / 2, cstep)
            else:
                eopM[p], avgM[p] = bei["cpi_eop"][i], bei["cpi_avg"][i]
        eopM["steady"] = bei["pi_ss"]
        notes.append(f"M: инфляция по рыночным форвардным BEI на {curve.date} (pi_ss {bei['pi_ss']}); текущий период — середина диапазона ЦБ")
        halves = -1
    for i, p in enumerate(periods[:halves + 1]):
        e = eop0 + (float(sM["cpi_eop"]) - eop0) * min(1.0, i / halves)
        eopM[p] = rnd(e, cstep)
        avgM[p] = rnd((prev + e) / 2, cstep)
        prev = e
    foodM, wageM = food_wage("M", avgM)
    out["M"] = {"key": keyM, "cpi_eop": eopM, "cpi_avg": avgM, "food_avg": foodM, "wage": wageM}
    return out, notes


# ----------------------------------------------------------------------------- отчёт и запуск
def compare(a: dict, b: dict) -> list[str]:
    """Различия двух объектов worlds_source: список строк; пусто — объекты равны."""
    diffs = []
    if a["rows"] != b["rows"]:
        for x, y in zip(a["rows"], b["rows"]):
            for k in x:
                if x[k] != y.get(k):
                    diffs.append(f"{x['world']} {x['period']} {k}: {x[k]} -> {y.get(k)}")
    for k in ("fair_zero_curve_today", "probabilities", "probability_ranges", "notes", "asof", "units", "lt_inflation", "m_inflation"):
        if a.get(k) != b.get(k):
            diffs.append(f"{k}: {a.get(k)} -> {b.get(k)}")
    return diffs


def candidate_report(inp: dict, record: dict, cand: dict, curve: Curve, anchors: dict, notes: list[str],
                     model: dict | None, model_record: dict | None) -> str:
    L = [f"# Кандидат миров на кривой {curve.date} (входы записи: книга {inp['version']}, кривая {inp['curve']['date']})", ""]
    rec_curve = Curve(inp["curve"]["nodes"], inp["curve"]["date"])
    L += ["## Кривая: узлы 1/3/5/10 лет, %", "", "| срок | запись | кандидат | Δ, б.п. |", "|---|---:|---:|---:|"]
    for t in TENORS:
        L.append(f"| {t} | {rec_curve.node(float(t)):.2f} | {curve.node(float(t)):.2f} | {100 * (curve.node(float(t)) - rec_curve.node(float(t))):+.0f} |")
    L += ["", "## Механика: что сдвинулось в worlds_source (ОФЗ миров, реальные ставки)", ""]
    diffs = compare(record, cand)
    fields = ("ofz_1y", "ofz_3y", "ofz_5y", "ofz_10y", "real_key_fwd12m")
    L += ["| мир | период | " + " | ".join(fields) + " |", "|---|---|" + "---:|" * len(fields)]
    rec_rows = {(r["world"], r["period"]): r for r in record["rows"]}
    shown = 0
    for r in cand["rows"]:
        o = rec_rows[(r["world"], r["period"])]
        if any(abs(r[f] - o[f]) >= 0.005 for f in fields):
            shown += 1
            if shown <= 24:
                L.append(f"| {r['world']} | {r['period']} | " + " | ".join(f"{o[f]:.2f}→{r[f]:.2f}" for f in fields) + " |")
    L.append(f"\nСтрок с изменениями: {shown} из {len(cand['rows'])} (показаны первые 24); всего различий по полям: {len(diffs)}.")
    L += ["", "## «Справедливые» кривые сегодня (A-M4), %", "", "| мир | 1 | 3 | 5 | 10 | LT запись | LT по правилу |", "|---|---:|---:|---:|---:|---:|---:|"]
    lt_rule = curve_lt_rule(cand)
    for wname in cand["fair_zero_curve_today"]:
        f, g = cand["fair_zero_curve_today"][wname], record["fair_zero_curve_today"][wname]
        L.append(f"| {wname} | " + " | ".join(f"{g[str(t)]:.2f}→{f[str(t)]:.2f}" for t in TENORS)
                 + f" | {inp['worlds'][wname]['curve_lt']} | {lt_rule[wname]} |")
    L += ["", "## Якоря: запись против правил яруса 2 (в книгу попадают решением аудитора)", ""]
    for wname, fields2 in anchors.items():
        for fname in ("key", "cpi_eop"):
            rec = inp["worlds"][wname]["anchors"][fname]
            can = fields2[fname]
            ps = [p for p in periods_of(inp["grid"]["first_period"], int(inp["grid"]["n_periods"])) if p in rec or p in can]
            L.append(f"**{wname} · {fname}** (установившееся: запись {inp['worlds'][wname]['steady'][fname]}"
                     + (f", правило {can['steady']}" if "steady" in can else "") + ")")
            L.append("")
            L.append("| период | " + " | ".join(ps) + " |")
            L.append("|---|" + "---:|" * len(ps))
            L.append("| запись | " + " | ".join(f"{rec[p]}" if p in rec else "—" for p in ps) + " |")
            L.append("| правило | " + " | ".join(f"{can[p]}" if p in can else "—" for p in ps) + " |")
            L.append("")
    if notes:
        L += ["Замечания правил:", ""] + [f"- {x}" for x in notes] + [""]
    if model is not None:
        fv, fr = model["fair_value"], (model_record or {}).get("fair_value", {})
        L += ["## Ядро на кандидате (входы книги: только механика, якоря записи)", "",
              f"Структурная оценка: {fr.get('low', float('nan')):.0f} / {fr.get('central', float('nan')):.0f} / {fr.get('high', float('nan')):.0f} → "
              f"**{fv['low']:.0f} / {fv['central']:.0f} / {fv['high']:.0f} ₽**; V0 аналитический "
              f"{(model_record or {}).get('fair_value', {}).get('ev_comparison', {}).get('analytical', {}).get('v0', float('nan')):.1f} → "
              f"{fv.get('ev_comparison', {}).get('analytical', {}).get('v0', float('nan')):.1f} млрд ₽.", ""]
    L += ["Кандидат не публикуется: в книгу — только новой версией с подписью аудитора и подтверждением владельца (WORLDS-RECIPE.md, раздел 6)."]
    return "\n".join(L) + "\n"


def load_curve(args, inp: dict) -> Curve:
    if args.release:
        src = args.release
        raw = urllib.request.urlopen(src, timeout=60).read() if src.startswith("http") else Path(src).read_bytes()
        return Curve.from_release(json.loads(raw))
    if args.curve:
        payload = json.loads(Path(args.curve).read_text(encoding="utf-8"))
        return Curve.from_zcyc(payload, args.curve_date or dt.date.today().isoformat())
    return Curve(inp["curve"]["nodes"], inp["curve"]["date"])


def write_candidate(cand: dict, out: Path) -> None:
    """worlds_source.json (как канон: json indent=1, текстовый режим) + worlds_source.csv (как evidence/agent-macro/worlds.py)."""
    import csv
    out.mkdir(parents=True, exist_ok=True)
    (out / "worlds_source.json").write_text(json.dumps(cand, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(out / "worlds_source.csv", "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=list(cand["rows"][0].keys()))
        wr.writeheader()
        wr.writerows(cand["rows"])


def check_nh(record: dict, cand: dict) -> tuple[list[str], list[str]]:
    """Сверка кандидата с каноном: (различия вне мира M — должны быть пусты; различия мира M по полям)."""
    bad, m_diffs = [], []
    for x, y in zip(record["rows"], cand["rows"]):
        assert (x["world"], x["period"]) == (y["world"], y["period"])
        for k in x:
            if x[k] != y.get(k):
                (m_diffs if x["world"] == "M" else bad).append(f"{x['world']} {x['period']} {k}: {x[k]} -> {y.get(k)}")
    if len(record["rows"]) != len(cand["rows"]):
        bad.append("число строк различается")
    for k in ("fair_zero_curve_today", "probabilities", "probability_ranges", "asof", "units"):
        if record.get(k) != cand.get(k):
            bad.append(f"{k}: {record.get(k)} -> {cand.get(k)}")
    rn, cn = record["notes"], cand["notes"]
    for i, (a, b) in enumerate(zip(rn, cn)):
        if a != b:
            (m_diffs if a.startswith("cpi for M") else bad).append(f"notes[{i}]: {a} -> {b}")
    return bad, m_diffs


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inputs", default=str(HERE / "worlds_inputs.yaml"))
    ap.add_argument("--check", default=None, nargs="?", const="",
                    help="воспроизвести канон и сравнить (по умолчанию canon-1.3.1/worlds_source.json); при forward_bei — "
                         "N и H обязаны совпасть бит в бит, печатаются изменения мира M")
    ap.add_argument("--m-inflation", choices=("judgement", "forward_bei"), help="источник инфляции мира M (перекрывает входы)")
    ap.add_argument("--liquidity-adj", type=float, help="поправка на премию за ликвидность ОФЗ-ИН, п.п. к BEI (ось)")
    ap.add_argument("--candidate", help="каталог: записать worlds_source.json/.csv кандидата на кривой записи")
    ap.add_argument("--release", help="URL или файл выпуска (/api/model): кривая из live.observed_curve")
    ap.add_argument("--curve", help="снимок MOEX ISS zcyc.json")
    ap.add_argument("--curve-date", help="дата снимка для --curve (ГГГГ-ММ-ДД)")
    ap.add_argument("--out", help="каталог кандидата (worlds_source.json + CANDIDATE.md)")
    args = ap.parse_args(argv)
    inp = yaml.safe_load(Path(args.inputs).read_text(encoding="utf-8"))
    if args.m_inflation:
        inp.setdefault("m_inflation", {})["source"] = args.m_inflation
    if args.liquidity_adj is not None:
        inp.setdefault("m_inflation", {})["liquidity_adj_pp"] = args.liquidity_adj
    source = (inp.get("m_inflation") or {}).get("source", "judgement")
    canon_dir = Path(args.inputs).resolve().parent
    rec_path = canon_dir / "worlds_source.json"
    if not rec_path.exists():
        rec_path = canon_dir / "canon-1.3.1" / "worlds_source.json"

    if args.check is not None or not (args.release or args.curve or args.candidate):
        rp = Path(args.check) if args.check else rec_path
        record = json.loads(rp.read_text(encoding="utf-8"))
        rebuilt = build_worlds(inp)
        # переходная сверка «N и H бит в бит, M печатается» — только против записи БЕЗ блока m_inflation (канон 1.3.1);
        # запись 1.4 уже содержит мир M по форвардным BEI и сверяется целиком, строго (все 63 строки, lt_inflation, m_inflation)
        if source == "forward_bei" and "m_inflation" not in record:
            bad, m_diffs = check_nh(record, rebuilt)
            if bad:
                print(f"НЕ воспроизведено вне мира M: {len(bad)} различий"); print("\n".join(bad[:40])); return 1
            n_rows = len({d.split(' ')[1] for d in m_diffs if d.startswith('M ')})
            print(f"N и H воспроизведены бит в бит ({sum(r['world'] != 'M' for r in rebuilt['rows'])} строк), кривые, веса, прочие заметки — "
                  f"совпадают с {rp.name}; мир M: {len(m_diffs)} изменений в {n_rows} периодах (pi_ss {rebuilt['lt_inflation']['M']})")
            for d in m_diffs:
                print("  " + d)
        else:
            diffs = compare(record, rebuilt)
            if diffs:
                print(f"НЕ воспроизведено: {len(diffs)} различий"); print("\n".join(diffs[:40])); return 1
            print(f"воспроизведено: {len(rebuilt['rows'])} строк, кривые {sorted(rebuilt['fair_zero_curve_today'])} — совпадают с {rp.name}")
        if not (args.release or args.curve or args.candidate):
            return 0

    if args.candidate:
        cand = build_worlds(inp)
        write_candidate(cand, Path(args.candidate))
        print(f"кандидат ({source}, поправка ликвидности {(inp.get('m_inflation') or {}).get('liquidity_adj_pp', 0.0)} п.п.) записан: {args.candidate}")
        if not (args.release or args.curve):
            return 0

    curve = load_curve(args, inp)
    record = json.loads(rec_path.read_text(encoding="utf-8"))
    cand = build_worlds(inp, curve, mid0=None if args.release or args.curve else None)
    cand["asof"] = curve.date
    anchors, notes = candidate_anchors(inp, curve)
    out = Path(args.out or (canon_dir / f"candidate-{curve.date}"))
    out.mkdir(parents=True, exist_ok=True)
    (out / "worlds_source.json").write_text(json.dumps(cand, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "anchors_by_rule.json").write_text(json.dumps({"anchors": anchors, "notes": notes}, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "CANDIDATE.md").write_text(candidate_report(inp, record, cand, curve, anchors, notes, None, None), encoding="utf-8")
    print(f"кандидат записан: {out} (кривая {curve.date}, {len(compare(record, cand))} различий с записью)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
