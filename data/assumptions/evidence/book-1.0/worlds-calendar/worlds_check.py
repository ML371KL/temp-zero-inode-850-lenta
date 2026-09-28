# -*- coding: utf-8 -*-
"""Лист «Миры ставок» книги 1.0 «Ленты»: проверка дословной копии записи миров и расчёты раздела 2.

Что делает (D6: миры N/H/M — дословно из общей записи 18.09.2026):
  1. sha256 шести файлов записи в каталоге миров (по умолчанию <book-draft>/worlds; env LENTA_WORLDS_DIR)
     против закреплённых значений (коммит-источник ca449343, ветка v5); worlds_source.json обязан начинаться с 7296567d;
  2. переводы строк: запись собрана на Windows (CRLF) — что станет с sha256 при нормализации в LF;
  3. `worlds_recipe.py --check` и `--candidate` во временной копии (нужны только worlds_inputs.yaml, worlds_recipe.py,
     worlds_source.json рядом): «воспроизведено: 63 строк»; кандидат json/csv против канона (байты и смысл);
  4. вставка миров сборщиком: временный шаблон = блок worlds с маркером #{{WORLDS}} + блок joint фрагмента темы
     (<book-draft>/fragments/worlds-calendar.yaml) -> build_assumptions.py -> assumptions.json; проверка полей мира
     (схема ядра), lt.inflation и zero_curve.LT; по желанию — равенство блока worlds и четырёх ключей joint с собранной
     книгой-источником (env SOURCE_BOOK_YAML — путь к её assumptions.yaml; без него шаг пропускается);
  5. таблицы раздела 2: годовые ключевая/ИПЦ/продовольствие/зарплаты/ОФЗ 10 лет по мирам; справедливые кривые;
     контроль A-P1m (10-летняя доходность смесей весов против рыночной); реальная r_u в терминале по сетке β_u;
     перенос цены ошибки осей миров в ₽ «Ленты» через % EV (входы — inputs/transfer.yaml).
Вывод: worlds_check_out.json, worlds_check_out.txt рядом со скриптом. Код 0 — все проверки прошли, 1 — нет.
Запуск: python -B worlds_check.py   (Python 3.12, PyYAML; первичка «Ленты» здесь не нужна)
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
BOOK_DRAFT = HERE.parents[2]
WORLDS_DIR = Path(os.environ.get("LENTA_WORLDS_DIR", BOOK_DRAFT / "worlds"))
FRAGMENT = Path(os.environ.get("LENTA_WORLDS_FRAGMENT", BOOK_DRAFT / "fragments" / "worlds-calendar.yaml"))
TRANSFER = HERE / "inputs" / "transfer.yaml"

# sha256 файлов записи на коммите-источнике ca4493432e4b (ветка v5, data/assumptions/), байты как в git (CRLF)
EXPECTED_SHA = {
    "worlds_source.json": "7296567d5200b5599efd0989de6ed8c8461d56c5c5dda244ab7d622303969fec",
    "worlds_source.csv": "2faae142d447a309daaf43eeea0a928db94ae2dc90ff1e0b21771703cd4a8dfc",
    "worlds_inputs.yaml": "1560bdbdb73374b5ecb77197f7705f95e0e9d00fc7b1878a2ac8c7304a8b778f",
    "worlds_recipe.py": "3e286c768e7f2ad94faec40930641fc2c54533cf4b20741fe5cfff8590ec7013",
    "WORLDS-RECIPE.md": "0b1824c22a681ba503c742e2d5270b76ab6fc0430561ece59c944f5cb12253f1",
    "build_assumptions.py": "f9d4dc6d25a8ecbd65ac1e3a7f6cc98e18ad2a9adabdef8dfb938ad53913420b",
}
CANON_PREFIX = "7296567d"
# поля мира, которые читает закрытая схема ядра (model/book_schema.py::_WORLD)
WORLD_FIELDS = {"cpi", "food_cpi", "key_rate", "lt", "name", "ofz_10y_path", "tariff_growth", "wage_growth", "zero_curve"}
JOINT_WORLD_KEYS = ("world_prob", "world_prob_market_implied", "macro_neutral_world", "own_macro_confidence")
MARKET_TENOR = 10
ERP = 0.0557                       # A-V2 ERP — общий с книгой-источником (D14); β_u — лист «Оценка», здесь сетка
BETA_GRID = (0.50, 0.52, 0.55, 0.58, 0.601, 0.65, 0.70)

results: dict = {"checks": [], "ok": True}


def check(name: str, ok: bool, detail: str = "") -> None:
    results["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
    if not ok:
        results["ok"] = False


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def step_hashes() -> None:
    out = {}
    for name, want in EXPECTED_SHA.items():
        p = WORLDS_DIR / name
        got = sha(p) if p.exists() else None
        raw = p.read_bytes() if p.exists() else b""
        crlf = raw.count(b"\r\n")
        lf_only = raw.count(b"\n") - crlf
        lf_sha = hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest() if raw else None
        out[name] = {"sha256": got, "expected": want, "match": got == want, "crlf_lines": crlf, "bare_lf_lines": lf_only,
                     "sha256_if_normalized_to_lf": lf_sha}
        check(f"sha256 {name}", got == want, f"{got}")
    check("worlds_source.json начинается с 7296567d", (out["worlds_source.json"]["sha256"] or "").startswith(CANON_PREFIX))
    results["hashes"] = out


def run(cmd: list[str], cwd: Path) -> tuple[int, str]:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", env=env)
    return p.returncode, (p.stdout + p.stderr).strip()


def step_recipe(tmp: Path) -> None:
    d = tmp / "recipe"
    d.mkdir()
    for f in ("worlds_inputs.yaml", "worlds_recipe.py", "worlds_source.json"):
        shutil.copy2(WORLDS_DIR / f, d / f)
    code, text = run([sys.executable, "-B", "worlds_recipe.py", "--check"], d)
    check("worlds_recipe.py --check: код 0 и «воспроизведено: 63 строк»", code == 0 and "воспроизведено: 63 строк" in text, text)
    code2, text2 = run([sys.executable, "-B", "worlds_recipe.py", "--candidate", "cand"], d)
    cj, cc = d / "cand" / "worlds_source.json", d / "cand" / "worlds_source.csv"
    canon_j, canon_c = (WORLDS_DIR / "worlds_source.json").read_bytes(), (WORLDS_DIR / "worlds_source.csv").read_bytes()
    ok_run = code2 == 0 and cj.exists() and cc.exists()
    same_json_bytes = ok_run and cj.read_bytes() == canon_j
    same_json_text = ok_run and cj.read_bytes().replace(b"\r\n", b"\n") == canon_j.replace(b"\r\n", b"\n")
    same_csv_bytes = ok_run and cc.read_bytes() == canon_c
    check("кандидат рецепта = канон по тексту json (без учёта CRLF/LF)", same_json_text, text2)
    check("кандидат рецепта = канон csv побайтно", same_csv_bytes)
    results["recipe"] = {"check_output": text, "candidate_output": text2, "python": sys.version.split()[0],
                         "platform": sys.platform, "candidate_json_bytes_equal": same_json_bytes,
                         "candidate_json_text_equal": same_json_text, "candidate_csv_bytes_equal": same_csv_bytes,
                         "note": "json кандидата пишется Path.write_text: на Windows CRLF (байты = канон), на Linux LF "
                                 "(смысл = канон, sha256 другой); csv пишется csv-модулем с \\r\\n на любой платформе"}


def fragment_joint() -> dict:
    frag = yaml.safe_load(FRAGMENT.read_text(encoding="utf-8"))
    return {k: frag["joint"][k] for k in JOINT_WORLD_KEYS}


def step_build(tmp: Path) -> dict:
    d = tmp / "build"
    d.mkdir()
    for f in ("build_assumptions.py", "worlds_source.json"):
        shutil.copy2(WORLDS_DIR / f, d / f)
    joint = fragment_joint()
    stub = ("meta:\n  version: \"1.0\"\n"
            "worlds:\n#{{WORLDS}}\n"
            "joint:\n" + "".join(f"  {k}: {json.dumps(v, ensure_ascii=False)}\n" for k, v in joint.items()))
    (d / "assumptions_template.yaml").write_text(stub, encoding="utf-8")
    code, text = run([sys.executable, "-B", "build_assumptions.py"], d)
    check("build_assumptions.py на шаблоне-заглушке: код 0", code == 0, text)
    # сравнения — по YAML-объекту (как его читает ядро); в assumptions.json целые ключи кривой ("1", "3"…) становятся строками
    book = yaml.safe_load((d / "assumptions.yaml").read_text(encoding="utf-8"))
    book_json = json.loads((d / "assumptions.json").read_text(encoding="utf-8"))
    check("assumptions.json = assumptions.yaml (с точностью до строковых ключей JSON)",
          json.loads(json.dumps(book, ensure_ascii=False)) == book_json)
    W = book["worlds"]
    check("миры N, H, M и только они", sorted(W) == ["H", "M", "N"], str(sorted(W)))
    for w in W:
        check(f"поля мира {w} = схема ядра", set(W[w]) == WORLD_FIELDS, str(sorted(set(W[w]) ^ WORLD_FIELDS)))
    lt = {w: W[w]["lt"]["inflation"] for w in W}
    zlt = {w: W[w]["zero_curve"]["LT"] for w in W}
    check("lt.inflation N/H/M = 0,040 / 0,055 / 0,098", lt == {"N": 0.04, "H": 0.055, "M": 0.098}, str(lt))
    check("zero_curve.LT N/H/M = 0,090 / 0,127 / 0,167", zlt == {"N": 0.09, "H": 0.127, "M": 0.167}, str(zlt))
    check("joint после сборки = фрагмент", {k: book["joint"][k] for k in JOINT_WORLD_KEYS} == joint)
    src = json.loads((WORLDS_DIR / "worlds_source.json").read_text(encoding="utf-8"))
    check("веса фрагмента = веса записи worlds_source.json", joint["world_prob"] == src["probabilities"],
          f"{joint['world_prob']} против {src['probabilities']}")
    ext = os.environ.get("SOURCE_BOOK_YAML")
    cmp = {"skipped": ext is None}
    if ext:
        ref = yaml.safe_load(Path(ext).read_text(encoding="utf-8"))
        cmp["worlds_equal"] = ref["worlds"] == W
        cmp["joint_equal"] = {k: ref["joint"][k] for k in JOINT_WORLD_KEYS} == joint
        check("блок worlds = блок собранной книги-источника", cmp["worlds_equal"])
        check("четыре ключа joint = книга-источник", cmp["joint_equal"])
    results["build"] = {"output": text, "external_compare": cmp, "lt_inflation": lt, "zero_curve_LT": zlt}
    return book


def path_value(path: dict, period: str) -> float:
    return float(path[period])


def step_tables(book: dict) -> None:
    src = json.loads((WORLDS_DIR / "worlds_source.json").read_text(encoding="utf-8"))
    rows = src["rows"]
    cols = {"2П2026": ["2026H2"], "2027": ["2027H1", "2027H2"], "2028": ["2028H1", "2028H2"], "2029": ["2029H1", "2029H2"],
            "2030": ["2030H1", "2030H2"], "2031–33": [f"{y}H{h}" for y in (2031, 2032, 2033) for h in (1, 2)],
            "2034–36": [f"{y}H{h}" for y in (2034, 2035, 2036) for h in (1, 2)],
            "2031–36": [f"{y}H{h}" for y in range(2031, 2037) for h in (1, 2)]}
    fields = {"key": "key_avg", "cpi_avg": "cpi_yoy_avg", "food_avg": "food_cpi_yoy_avg", "wage": "nominal_wage_yoy",
              "ofz_10y": "ofz_10y", "cpi_eop": "cpi_yoy_eop"}
    table = {}
    for w in ("N", "H", "M"):
        by_p = {r["period"]: r for r in rows if r["world"] == w}
        table[w] = {f: {c: round(sum(by_p[p][src_f] for p in ps) / len(ps), 3) for c, ps in cols.items()}
                    for f, src_f in fields.items()}
    results["annual_table"] = table

    fair = src["fair_zero_curve_today"]
    J = book["joint"]
    node = {w: fair[w][str(MARKET_TENOR)] / 100.0 for w in fair}
    market = node[J["macro_neutral_world"]]
    implied = {"market_10y": market, "fair_10y_by_world": node}
    for name, weights in (("analytical", J["world_prob"]), ("market_implied", J["world_prob_market_implied"])):
        linear = sum(weights[w] * node[w] for w in weights)
        mix = sum(weights[w] * (1 + node[w]) ** -MARKET_TENOR for w in weights) ** (-1 / MARKET_TENOR) - 1
        implied[name] = {"weights": weights, "linear": linear, "discount_factor_mix": mix, "gap_to_market_linear": linear - market}
    # какой вес M нужен, чтобы линейная смесь дала рыночную 10-летку при N:H как в аналитическом наборе
    nh = J["world_prob"]["N"] / (J["world_prob"]["N"] + J["world_prob"]["H"])
    base_nh = nh * node["N"] + (1 - nh) * node["H"]
    w_m_needed = (market - base_nh) / (node["M"] - base_nh)
    implied["m_weight_needed_linear"] = w_m_needed
    results["market_implied_10y"] = implied
    check("A-P1m: вменённые веса 10/25/65 дают ≈15,04 % (линейно)", abs(implied["market_implied"]["linear"] - 0.15038) < 5e-4,
          f"{implied['market_implied']['linear']:.5f}")
    check("набор миров односторонний: рыночная 10-летка достигается только при весе M = 100 %", w_m_needed >= 0.999,
          f"нужный вес M {w_m_needed:.4f}")

    W = book["worlds"]
    r_u = {}
    for b in BETA_GRID:
        prem = b * ERP
        r_u[f"{b:.3f}"] = {"premium_pp": round(prem * 100, 3),
                           **{w: round(((1 + W[w]["zero_curve"]["LT"] + prem) / (1 + W[w]["lt"]["inflation"]) - 1) * 100, 3)
                              for w in ("N", "H", "M")}}
    results["terminal_real_r_u_pct"] = r_u
    results["fair_zero_curve_today"] = fair
    results["m_inflation"] = {k: v for k, v in src["m_inflation"].items() if not isinstance(v, (list, dict))}
    results["probability_ranges_record"] = src["probability_ranges"]


def step_transfer() -> None:
    t = yaml.safe_load(TRANSFER.read_text(encoding="utf-8"))
    lenta_rub_per_pct_ev = t["lenta"]["rub_per_1pct_ev"]
    lo_hi = t["lenta"]["rub_per_1pct_ev_range"]
    src_rub = t["source_book"]["rub_per_1pct_ev_point"]
    out = []
    for row in t["rows"]:
        pct_lo = row["source_rub_low"] / src_rub
        pct_hi = row["source_rub_high"] / src_rub
        out.append({"name": row["name"], "paths": row["paths"], "pct_ev_low": round(pct_lo, 3), "pct_ev_high": round(pct_hi, 3),
                    "lenta_rub_low": round(pct_lo * lenta_rub_per_pct_ev), "lenta_rub_high": round(pct_hi * lenta_rub_per_pct_ev),
                    "lenta_rub_low_range": sorted([round(pct_lo * x) for x in lo_hi]),
                    "lenta_rub_high_range": sorted([round(pct_hi * x) for x in lo_hi]),
                    # неопределённость переноса ±50 % (inputs/transfer.yaml → method)
                    "lenta_rub_low_x05_x15": sorted([round(pct_lo * lenta_rub_per_pct_ev * f) for f in (0.5, 1.5)]),
                    "lenta_rub_high_x05_x15": sorted([round(pct_hi * lenta_rub_per_pct_ev * f) for f in (0.5, 1.5)]),
                    "basis": row["basis"]})
    results["transfer_cost_of_error"] = {"method": t["method"], "rows": out,
                                          "lenta_rub_per_1pct_ev": lenta_rub_per_pct_ev}


def write_txt() -> str:
    L = ["ЛИСТ «МИРЫ СТАВОК» — книга 1.0 «Ленты» (worlds_check.py)", ""]
    L.append("1. Проверки")
    for c in results["checks"]:
        L.append(f"  [{'да ' if c['ok'] else 'НЕТ'}] {c['name']}" + (f" — {c['detail'][:160]}" if c["detail"] and not c["ok"] else ""))
    L.append("")
    L.append("2. Файлы записи: sha256, переводы строк")
    for n, h in results["hashes"].items():
        L.append(f"  {n}: {h['sha256'][:16]}… CRLF-строк {h['crlf_lines']}, голых LF {h['bare_lf_lines']}; "
                 f"sha256 после нормализации в LF {h['sha256_if_normalized_to_lf'][:16]}…")
    L.append("")
    L.append(f"3. Рецепт: {results['recipe']['check_output']}")
    L.append(f"   кандидат: {results['recipe']['candidate_output']}; json байт в байт {results['recipe']['candidate_json_bytes_equal']}, "
             f"по тексту {results['recipe']['candidate_json_text_equal']}; csv байт в байт {results['recipe']['candidate_csv_bytes_equal']} "
             f"(Python {results['recipe']['python']}, {results['recipe']['platform']})")
    L.append("")
    L.append(f"4. Сборка: {results['build']['output']}")
    L.append(f"   lt.inflation {results['build']['lt_inflation']}; zero_curve.LT {results['build']['zero_curve_LT']}; "
             f"сверка с книгой-источником: {results['build']['external_compare']}")
    L.append("")
    L.append("5. Годовые значения миров (среднее полугодий, % годовых)")
    hdr = ["2П2026", "2027", "2028", "2029", "2030", "2031–33", "2034–36", "2031–36"]
    L.append("   мир поле       " + "  ".join(f"{h:>8}" for h in hdr))
    for w, t in results["annual_table"].items():
        for f in ("key", "cpi_avg", "food_avg", "wage", "ofz_10y", "cpi_eop"):
            L.append(f"   {w}   {f:<9}" + "  ".join(f"{t[f][h]:>8.3f}" for h in hdr))
    L.append("")
    mi = results["market_implied_10y"]
    L.append("6. Контроль A-P1m: 10-летняя бескупонная доходность")
    L.append(f"   справедливая по мирам {{N {mi['fair_10y_by_world']['N']*100:.2f}, H {mi['fair_10y_by_world']['H']*100:.2f}, "
             f"M {mi['fair_10y_by_world']['M']*100:.2f}}} %, рыночная {mi['market_10y']*100:.2f} %")
    for name in ("analytical", "market_implied"):
        x = mi[name]
        L.append(f"   {name}: веса {x['weights']} -> линейно {x['linear']*100:.2f} %, смесь дисконт-факторов "
                 f"{x['discount_factor_mix']*100:.2f} %, разрыв с рынком {x['gap_to_market_linear']*100:+.2f} п.п.")
    L.append(f"   вес M, нужный для рыночной 10-летки при N:H аналитического набора: {mi['m_weight_needed_linear']*100:.1f} %")
    L.append("")
    L.append(f"7. Реальная r_u в терминале, % = (1 + LT кривой + β_u·ERP)/(1 + π_LT) − 1; ERP {ERP*100:.2f} %")
    for b, r in results["terminal_real_r_u_pct"].items():
        L.append(f"   β_u {b}: премия {r['premium_pp']:.2f} п.п.; N {r['N']:.2f} · H {r['H']:.2f} · M {r['M']:.2f}")
    L.append("")
    L.append("8. Перенос цены ошибки осей миров в ₽ «Ленты» (через % EV; до ядра)")
    for r in results["transfer_cost_of_error"]["rows"]:
        L.append(f"   {r['name']}: {r['pct_ev_low']:+.2f} … {r['pct_ev_high']:+.2f} % EV -> {r['lenta_rub_low']:+d} … "
                 f"{r['lenta_rub_high']:+d} ₽ (перенос ±50 %: {r['lenta_rub_low_x05_x15']} … {r['lenta_rub_high_x05_x15']})")
    L.append("")
    L.append("ИТОГ: " + ("все проверки прошли" if results["ok"] else "ЕСТЬ ПРОВАЛЫ"))
    return "\n".join(L) + "\n"


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    results["inputs"] = {"worlds_dir": WORLDS_DIR.name, "fragment": FRAGMENT.name, "transfer": TRANSFER.name}
    step_hashes()
    with tempfile.TemporaryDirectory(prefix="lenta-worlds-") as t:
        tmp = Path(t)
        step_recipe(tmp)
        book = step_build(tmp)
    step_tables(book)
    step_transfer()
    (HERE / "worlds_check_out.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    txt = write_txt()
    (HERE / "worlds_check_out.txt").write_text(txt, encoding="utf-8", newline="\n")
    print(txt)
    return 0 if results["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
