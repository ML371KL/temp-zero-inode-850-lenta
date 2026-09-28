"""Фрагмент машинной книги 1.0 по теме facts: book-draft/fragments/facts.yaml.

Читает только facts/*.json (собраны build_facts.py). Пишет YAML с комментарием у каждого
ключа: основание и диапазон. Пути ключей — DESIGN §4 (meta, market, facts).
    python make_fragment.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
BOOK = HERE.parents[2]
FACTS = Path(os.environ.get("LENTA_FACTS_OUT", BOOK / "facts"))
OUT = BOOK / "fragments" / "facts.yaml"


def J(n):
    return json.loads((FACTS / n).read_text(encoding="utf-8"))


def r(x, n=6):
    return None if x is None else round(float(x), n)


def segments(acc, ino):
    Q, ops = acc["quarters"], acc["operating"]
    H = acc["halves"]

    def qs(key, q1, q2):
        a, b = Q[q1]["ias17"].get(key), Q[q2]["ias17"].get(key)
        return (a["v"] or 0) + (b["v"] or 0)

    def half(key, per):
        y = per[:4]
        return qs(key, f"{y}Q1", f"{y}Q2") if per.endswith("H1") else qs(key, f"{y}Q3", f"{y}Q4")

    okey = next(d for d in ino["deals"] if d["id"] == "okey")
    remi = next(d for d in ino["deals"] if d["id"] == "remi")
    okey_june = okey["contribution"]["revenue"]
    hyper25 = [half("seg_hyper_db", "2025H1"), half("seg_hyper_db", "2025H2")]
    h1_share_hyper = hyper25[0] / sum(hyper25)
    grp25 = [H["2025H1"]["ias17"]["pl"]["revenue"]["v"], H["2025H2"]["ias17"]["pl"]["revenue"]["v"]]
    h1_share_grp = grp25[0] / sum(grp25)
    area = lambda k, q="2026Q2": ops["area_sqm"][q][k]["v"] / 1000.0  # тыс. м²
    stores = lambda k, q="2026Q2": int(ops["stores"][q][k]["v"])

    def avg3(k, qa, qb, qc, adj=0.0):
        return r(sum(area(k, q) for q in (qa, qb, qc)) / 3 - adj, 3)

    okey_area = okey_area_k = 478.0
    seg = {}
    seg["hyper"] = {
        "name": "Гипермаркеты «Ленты» (без «О’КЕЙ»)",
        "revenue": {"2025H1": r(hyper25[0]), "2025H2": r(hyper25[1]), "2026H1": r(half("seg_hyper_db", "2026H1") - okey_june)},
        "revenue_basis": {"2025H1": "reported", "2025H2": "reported", "2026H1": "estimate"},
        "area_end": r(area("hyper_db") - okey_area_k, 3), "stores_end": stores("hyper_db") - 75,
        "eff_area_avg_hist": {"2025H2": avg3("hyper_db", "2025Q2", "2025Q3", "2025Q4"),
                              "2026H1": r((area("hyper_db", "2025Q4") + area("hyper_db", "2026Q1") + (area("hyper_db") - okey_area_k)) / 3, 3)},
        "_c": {"revenue": "датабук Financials quarterly «Hypermarkets» (сумма кварталов); 2026H1 — минус июнь «О’КЕЙ» 9,359828 (прим. 5 с. 18): вывод, что июнь «О’КЕЙ» сидит в строке гипермаркетов (г/г 2 кв. +9,2 % без него против +9,7 % в 1 кв.; с ним +16,0 %)",
               "area": "датабук Operating Results 2кв2026 1 850,007 тыс. м² − 478 «О’КЕЙ» (прим. 5); 346 − 75 магазинов; расчёт (DB-8: невязка 8 тыс. м² и 1 магазин)",
               "eff": "простое среднее трёх квартальных точек; лист network заменяет правилом ядра (урок 850oa Д8)"}}
    seg["okey"] = {
        "name": "Гипермаркеты «О’КЕЙ» (с 02.06.2026)",
        "revenue": {"2025H1": r(142.0 * h1_share_hyper), "2025H2": r(142.0 * (1 - h1_share_hyper)), "2026H1": okey["pro_forma"]["revenue_1H2026_full"]},
        "revenue_basis": {"2025H1": "estimate", "2025H2": "estimate", "2026H1": "pro_forma"},
        "area_end": okey_area, "stores_end": 75,
        "eff_area_avg_hist": {"2025H2": okey_area, "2026H1": okey_area},
        "_c": {"revenue": "2025: 142 (пресс-релиз 02.06.2026) × доля полугодия гипермаркетов «Ленты» 2025 (1П 0,4688 / 2П 0,5312) — расчёт, se ≈1,5; 2026H1 — вклад за всё полугодие 66,120725 (прим. 5 с. 18), в отчёте только июнь 9,359828",
               "area": "478 тыс. м², 75 гипермаркетов (прим. 5 с. 16)", "eff": "площадь постоянна до сценариев close; лист network"}}
    seg["super"] = {
        "name": "Супермаркеты «Ленты»",
        "revenue": {p: r(half("seg_super", p)) for p in ("2025H1", "2025H2", "2026H1")},
        "revenue_basis": {"2025H1": "reported", "2025H2": "reported", "2026H1": "reported"},
        "area_end": r(area("super"), 3), "stores_end": stores("super"),
        "eff_area_avg_hist": {"2025H2": avg3("super", "2025Q2", "2025Q3", "2025Q4"), "2026H1": avg3("super", "2025Q4", "2026Q1", "2026Q2")},
        "_c": {"revenue": "датабук Financials quarterly «Supermarkets»", "area": "Operating Results 2кв2026", "eff": "простое среднее; лист network"}}
    conv = {p: half("seg_convenience", p) + half("seg_other_formats", p) for p in ("2025H1", "2025H2", "2026H1")}
    seg["conv"] = {
        "name": "Магазины у дома («Монетка», «Мини Лента») + «Вингараж» (прочие форматы)",
        "revenue": {p: r(v_) for p, v_ in conv.items()},
        "revenue_basis": {"2025H1": "reported", "2025H2": "reported", "2026H1": "reported"},
        "area_end": r(area("convenience") + area("other_formats"), 3), "stores_end": stores("convenience") + stores("other_formats"),
        "eff_area_avg_hist": {"2025H2": r(avg3("convenience", "2025Q2", "2025Q3", "2025Q4") + avg3("other_formats", "2025Q2", "2025Q3", "2025Q4"), 3),
                              "2026H1": r(avg3("convenience", "2025Q4", "2026Q1", "2026Q2") + avg3("other_formats", "2025Q4", "2026Q1", "2026Q2"), 3)},
        "_c": {"revenue": "датабук «Convenience stores» + «Other formats» («Вингараж»); 2025H1 без «Молнии» до 24.06.2025 (≈10,2 по прим. 8 МСФО 2025: 19,796 − 9,644) — нужно только для истории", "area": "Operating Results 2кв2026", "eff": "простое среднее; лист network"}}
    seg["droge"] = {
        "name": "«Улыбка радуги»",
        "revenue": {p: r(half("seg_droge", p)) for p in ("2025H1", "2025H2", "2026H1")},
        "revenue_basis": {"2025H1": "reported", "2025H2": "reported", "2026H1": "reported"},
        "area_end": r(area("droge"), 3), "stores_end": stores("droge"),
        "eff_area_avg_hist": {"2025H2": avg3("droge", "2025Q2", "2025Q3", "2025Q4"), "2026H1": avg3("droge", "2025Q4", "2026Q1", "2026Q2")},
        "_c": {"revenue": "датабук «Drogerie»", "area": "Operating Results 2кв2026", "eff": "простое среднее; лист network"}}
    remi_pf = remi["pro_forma"]["network_revenue_2025"]
    seg["remi"] = {
        "name": "«Реми» (Дальний Восток, 67 %; вне LFL до 12.2026)",
        "revenue": {"2025H1": r(remi_pf * h1_share_grp), "2025H2": r(remi_pf * (1 - h1_share_grp)), "2026H1": r(half("seg_remi", "2026H1"))},
        "revenue_basis": {"2025H1": "estimate", "2025H2": "estimate", "2026H1": "reported"},
        "area_end": r(area("remi"), 3), "stores_end": stores("remi"),
        "eff_area_avg_hist": {"2025H2": r(area("remi", "2025Q4"), 3), "2026H1": r(area("remi"), 3)},
        "_c": {"revenue": "2025: выручка сети 55,488 (прим. 8 МСФО 2025 с. 49) × доля полугодия группы 2025 (0,4657/0,5343) — расчёт, se ≈1,0; в отчёте 2П2025 только декабрь 5,170; 2026H1 — датабук «Remi»",
               "area": "Operating Results 2кв2026", "eff": "площадь не менялась с 4кв2025"}}
    seg["diy"] = {
        "name": "«Дом Лента» (бывш. «ОБИ Россия»; режим level)",
        "revenue": {"2025H1": None, "2025H2": None, "2026H1": r(half("seg_diy", "2026H1"))},
        "revenue_basis": {"2025H1": "estimate", "2025H2": "estimate", "2026H1": "reported"},
        "area_end": r(area("diy"), 3), "stores_end": stores("diy"),
        "eff_area_avg_hist": {"2025H2": None, "2026H1": r(area("diy"), 3)},
        "_c": {"revenue": "2026H1 — датабук «Dom Lenta» (консолидация поэтапно в 1 кв.: 2,059 + 5,901); базы 2025 в первичке нет — режим level её не требует (null ≠ 0)",
               "area": "Operating Results 2кв2026 (прим. 5: 25 магазинов, 263 тыс. м² на дату сделки)", "eff": "2025H2 нет в периметре"}}
    seg["wholesale"] = {
        "name": "Опт и прочая выручка без площади",
        "revenue": {p: r(half("wholesale", p)) for p in ("2025H1", "2025H2", "2026H1")},
        "revenue_basis": {"2025H1": "reported", "2025H2": "reported", "2026H1": "reported"},
        "area_end": None, "stores_end": None, "eff_area_avg_hist": {"2025H2": None, "2026H1": None},
        "_c": {"revenue": "датабук «Wholesale»; МСФО 6М2026 прим. 25: оптовая торговля 4,796135", "area": "площади нет", "eff": "—"}}
    return seg


def main():
    acc, anc, sh, peers, debt, ino = J("accounting_base.json"), J("anchor.json"), J("shares.json"), J("peers.json"), J("debt.json"), J("inorganic.json")
    H = acc["halves"]
    rep_rev = {p: r(H[p]["ias17"]["pl"]["revenue"]["v"]) for p in ("2025H1", "2025H2", "2026H1")}
    rep_eb = {p: r(H[p]["ias17"]["pl"]["ebitda"]["v"]) for p in ("2025H1", "2025H2", "2026H1")}
    seg = segments(acc, ino)
    ex_diy = lambda p: r(sum((s["revenue"][p] or 0) for k, s in seg.items() if k != "diy"))
    pf_rev = {"2025H1": ex_diy("2025H1"), "2025H2": ex_diy("2025H2"), "2026H1": anc["revenue"]["pro_forma"]}
    cand_da = r(anc["da_pre16"]["reported"] + anc["da_pre16"]["pro_forma_candidate"]["okey_add_jan_may"]["center"])
    cand_rev_ltm = anc["revenue_ltm"]["pro_forma_candidate"]["v"]
    L = []
    w = L.append
    w("# Фрагмент машинной книги 1.0 «Ленты»: тема facts (раздел 00).")
    w("# Сгенерирован evidence/book-1.0/facts/make_fragment.py из facts/*.json (build_facts.py) — руками не править.")
    w("# Единицы: млрд ₽; площадь — тыс. м²; доли — в долях единицы. null ≠ 0: ключ ждёт другой лист.")
    w("meta:")
    w('  version: "1.0"')
    w("  company: {name: \"Лента\", ticker: \"LENT\"}          # МКПАО «Лента», ISIN RU000A102S15 (ISS)")
    w("  period_unit: half                                # D2")
    w('  valuation_date: "2026-09-18"                     # D7: дата записи миров 850oa')
    w('  facts_date: "2026-06-30"                         # МСФО 6М2026 (обзор), датабук 2 кв. 2026')
    w('  bridge_as_of: "2026-06-30"                       # все строки моста — на отчётную дату (bridge_balance.json)')
    w('  first_period: "2026H2"')
    w('  last_period: "2036H2"')
    w("market:")
    mp = peers["market_price_book"]
    w(f"  price: {mp['price']}                              # LEGALCLOSEPRICE LENT 18.09.2026, ISS (research/_work05/LENT_TQBR.json sha256 {mp['sha256'][:12]}…); close 1 625,5")
    w("  price_convention: legalclose                     # D7; в дни без сделок legal close не брать (урок Д12)")
    w('  price_date: "2026-09-18"                         # первый день вне IMOEX')
    w("  peers:                                           # живые на одной базе IAS 17: EV = капитализация (legal close 18.09) + ЧД 30.06 + дивиденды после баланса; EBITDA LTM 30.06")
    for p_ in peers["peers"]:
        w(f"    - {{name: \"{p_['name']}\", ev_ebitda: {p_['ev_ebitda']}, basis: \"IAS 17\", as_of: \"2026-09-18\"}}   # EV {p_['ev']} / EBITDA {p_['ebitda_ltm']}; ЧД/EBITDA {p_['nd_ebitda']}; маржа {p_['ebitda_margin']}")
    lt = peers["lent"]
    w(f"    - {{name: \"Лента (отчётная LTM)\", ev_ebitda: {lt['ev_ebitda_reported']}, basis: \"IAS 17\", as_of: \"2026-09-18\"}}   # EV {lt['ev']} / 84,433; завышен: долг «О’КЕЙ» целиком, EBITDA — месяц")
    w("    - {name: \"Лента (проформа LTM)\", ev_ebitda: null, basis: \"IAS 17\", as_of: \"2026-09-18\"}   # ждёт EBITDA LTM на проформе (лист margin); при 84,4 ± 3 → 3,47–3,73×")
    st = peers["sellside_targets"]
    w("  sellside_targets:                                # последняя действующая цель каждого дома, с датой (урок Д10); вторичные источники — класс B")
    for t in st["latest_by_house"]:
        w(f"    - {{house: \"{t['house']}\", target: {t['target']}, date: \"{t['date']}\", rating: \"{t['rating']}\", source: \"{t['source']}\"}}")
    w(f"  sellside_targets_median: {st['latest_median']}              # n = {st['latest_n']}, диапазон {st['latest_range'][0]}–{st['latest_range'][1]}; после отчёта 2 кв. (6 домов) медиана 2 375")
    w("facts:")
    w(f"  shares_out_mln: {sh['outstanding']['shares_out_mln']}             # A: 115 985 197 выпущено − 910 522 квазиказначейских = 115 074 675 (МСФО 6М2026 прим. 18–19 с. 29–30); воспроизводит EPS 0,092; ДР-счёт 4,88 млн остаётся в знаменателе (D8)")
    w("  revenue:")
    w(f"    reported: {{2025H1: {rep_rev['2025H1']}, 2025H2: {rep_rev['2025H2']}, 2026H1: {rep_rev['2026H1']}}}   # A: датабук PL IAS 17 (2П = FY − 1П); 1П2026 = МСФО с. 7")
    w(f"    pro_forma: {{2025H1: {pf_rev['2025H1']}, 2025H2: {pf_rev['2025H2']}, 2026H1: {pf_rev['2026H1']}}}   # 2026H1 = 648,488059 + 66,120725 − 9,359828 (прим. 5 с. 18; A/расчёт); 2025 — Σ сегментов на периметре 30.06.2026 без diy (расчёт, se ≈2): отношение 2П/1П {round(pf_rev['2025H2'] / pf_rev['2025H1'], 3)} против 1,147 в 2025")
    w("  ebitda_pre16:")
    w(f"    reported: {{2025H1: {rep_eb['2025H1']}, 2025H2: {rep_eb['2025H2']}, 2026H1: {rep_eb['2026H1']}}}   # A: датабук PL IAS 17 = OPBI + D&A (2025H2 содержит +0,039 строки сверки FY2025)")
    w("    pro_forma: {2025H1: null, 2025H2: null, 2026H1: null}   # лист margin (мост «прибыль до налога МСФО 16 → EBITDA IAS 17», D3); кандидат 2026H1: 39,279 + EBITDA «О’КЕЙ» янв–май 0 ± 1")
    w("  anchor:")
    w('    period: "2026H1"')
    w(f"    da_pre16: {cand_da}                             # расчёт: 15,794885 (датабук PL AE17, A) + «О’КЕЙ» янв–май ≈1,8 [1,4–2,2] (ОС+ИН 31,05 × темп D&A группы 7,2 %/полугодие × 5/6); лист margin может уточнить")
    w(f"    capex: {anc['capex']['v']}                          # A: денежный IAS 17, CF AE54+AE56+AE57 (МСФО 16: 22,243864); 3,47 % отчётной выручки; «О’КЕЙ» янв–май не добавлен (capex сети до сделки не раскрыт)")
    w(f"    net_debt: {anc['net_debt']['v']}                     # A: 100,030371 + 42,553313 − 25,172516 (МСФО с. 6) = лист Debt 117 410; ≈1 млрд = 7,8 ₽/акция")
    w(f"    cash: {anc['cash']['v']}                         # A: МСФО с. 6, прим. 16 (депозиты 19,46, счета 3,29, в пути 1,31, касса 1,11)")
    w(f"    ebitda_ltm: {anc['ebitda_ltm']['reported']}                   # ВРЕМЕННО отчётная (A: 83,684 − 38,531 + 39,279); на проформе — лист margin [81,4–87,4]; ЧД/EBITDA 1,39")
    w(f"    revenue_ltm: {cand_rev_ltm}                 # расчёт на проформе: 1 238,217 отчётная + «О’КЕЙ» 2П2025 75,43 [73–78] + янв–май 2026 56,76 + «Реми» июль–ноябрь 2025 22,90 [21–25]; без «ОБИ» до консолидации (нет в первичке, 9–20 — суждение)")
    w("    margin_pro_forma: null                          # лист margin; кандидат 0,0557 [0,0543–0,0571] = 39,279 / 705,249 при EBITDA «О’КЕЙ» 0 ± 1; 0,1 п.п. ≈10–45 ₽/акция")
    w("    margin_pro_forma_se: null                       # лист margin; кандидат ≈0,0008–0,0014 (полуширина моста ±1 млрд / 705)")
    w(f"  undrawn_credit_lines: {debt['undrawn_credit_lines']['v']}                # A: МСФО 6М2026 прим. 20 с. 31 и прим. 2.1 с. 12, на 30.06.2026 (та же дата, что долг: лимит 426,447)")
    w("  segments:                                        # D3: сегменты по строкам датабука, периметр 30.06.2026; revenue_basis: reported | pro_forma | estimate")
    for k, s in seg.items():
        c = s["_c"]
        w(f"    {k}:                                         # {s['name']}")
        rv = s["revenue"]
        w(f"      revenue: {{2025H1: {json.dumps(rv['2025H1'])}, 2025H2: {json.dumps(rv['2025H2'])}, 2026H1: {json.dumps(rv['2026H1'])}}}   # {c['revenue']}".replace("null", "null"))
        rb = s["revenue_basis"]
        w(f"      revenue_basis: {{2025H1: {rb['2025H1']}, 2025H2: {rb['2025H2']}, 2026H1: {rb['2026H1']}}}")
        w(f"      area_end: {json.dumps(s['area_end'])}                  # {c['area']}")
        w(f"      stores_end: {json.dumps(s['stores_end'])}")
        e = s["eff_area_avg_hist"]
        w(f"      eff_area_avg_hist: {{2025H2: {json.dumps(e['2025H2'])}, 2026H1: {json.dumps(e['2026H1'])}}}   # {c['eff']}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    txt = "\n".join(L) + "\n"
    yaml.safe_load(txt)   # фрагмент обязан читаться
    OUT.write_text(txt, encoding="utf-8", newline="\n")
    # сверка: сумма сегментов 2026H1 (проформа) = проформа группы
    s26 = sum((s["revenue"]["2026H1"] or 0) for s in seg.values())
    print(f"facts.yaml: {OUT}  (Σ сегментов 2026H1 = {s26:.3f}; проформа группы {anc['revenue']['pro_forma']:.3f})")


if __name__ == "__main__":
    main()
