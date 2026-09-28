# -*- coding: utf-8 -*-
"""Собирает assumptions.yaml: макро-миры берутся из worlds_source.json (полугодовая сетка макролиста),
остальные блоки — из шаблона assumptions_template.yaml. С версии 1.2 здесь же пишется assumptions.json —
копия того же объекта для потребителей без YAML. Запуск: python -B build_assumptions.py"""
import json
from pathlib import Path
import yaml

HERE = Path(__file__).resolve().parent
src = json.loads((HERE / "worlds_source.json").read_text(encoding="utf-8"))

LT = {  # установившиеся значения и «справедливая» сегодняшняя бескупонная кривая мира (для дисконтирования)
    "N": dict(name="Нормализация", infl=0.040, curve_lt=0.090),
    "H": dict(name="Высокие ставки надолго", infl=0.055, curve_lt=0.127),
    "M": dict(name="Рыночный как есть", infl=0.090, curve_lt=0.167),
}
TARIFF = {"2026H2": 0.099, "2027": 0.11, "2028": 0.08, "2029": 0.06}  # коммунальные/сетевые тарифы: Минэк (вторичные), далее ИПЦ мира + 1,5 п.п.
# 1.4: рецепт миров пишет установившуюся инфляцию миров в блок lt_inflation (мир M — по форвардным BEI); при его наличии берётся
# он, таблица LT выше остаётся для книг до 1.4 (там блока нет)
for _w, _pi in (src.get("lt_inflation") or {}).items():
    LT[_w]["infl"] = round(float(_pi) / 100.0, 5)
# 1.5 (аудит 26.09.2026, п. 5.2): у мира M инфляция в долгую — рыночная (блок m_inflation, форвардные BEI), и уровень
# кривой в терминале берётся в той же сборке правилом рецепта (worlds_recipe.curve_lt_rule: ОФЗ 10 лет последнего
# периода сетки, округление до 0,1). Иначе пересборка сдвигала бы π_ss при неизменной ставке терминала из таблицы
# выше: ложный сдвиг кандидата (эффект на заголовок — книга-источник (850oa)). На записи 1.4 правило даёт те же 16,7 %. У N/H уровень —
# суждение записи (правило даёт на 0,1 ниже), таблица остаётся.
if (src.get("m_inflation") or {}).get("source") == "forward_bei":
    LT["M"]["curve_lt"] = round(round([r for r in src["rows"] if r["world"] == "M"][-1]["ofz_10y"], 1) / 100.0, 5)


def series(world, field, scale=0.01):
    rows = [r for r in src["rows"] if r["world"] == world]
    return "{" + ", ".join(f'"{r["period"]}": {round(r[field] * scale, 5)}' for r in rows) + "}"


def world_block(w):
    fair = src["fair_zero_curve_today"][w]
    curve = ", ".join(f"{k}: {round(v / 100, 5)}" for k, v in sorted(fair.items(), key=lambda kv: float(kv[0])))
    tariff = dict(TARIFF)
    tariff["LT"] = round(LT[w]["infl"] + 0.015, 4)
    tariff["LT_from"] = 2031
    t = "{" + ", ".join(f'"{k}": {v}' if not str(k).startswith("LT") else f"{k}: {v}" for k, v in tariff.items()) + "}"
    return (f"  {w}:\n"
            f"    name: \"{LT[w]['name']}\"\n"
            f"    key_rate: {series(w, 'key_avg')}\n"
            f"    cpi: {series(w, 'cpi_yoy_avg')}\n"
            f"    food_cpi: {series(w, 'food_cpi_yoy_avg')}\n"
            f"    wage_growth: {series(w, 'nominal_wage_yoy')}\n"
            f"    ofz_10y_path: {series(w, 'ofz_10y')}\n"
            f"    tariff_growth: {t}\n"
            f"    zero_curve: {{{curve}, LT: {LT[w]['curve_lt']}}}\n"
            f"    lt: {{inflation: {LT[w]['infl']}}}\n")


template = (HERE / "assumptions_template.yaml").read_text(encoding="utf-8")
out = template.replace("#{{WORLDS}}", "".join(world_block(w) for w in ("N", "H", "M")).rstrip("\n"))
(HERE / "assumptions.yaml").write_text(out, encoding="utf-8")
print("assumptions.yaml written:", len(out), "chars")
book = yaml.safe_load(out)                     # заодно проверка, что собранный файл разбирается
js = json.dumps(book, ensure_ascii=False, indent=1)
(HERE / "assumptions.json").write_text(js, encoding="utf-8")
print("assumptions.json written:", len(js), "chars; version", book["meta"]["version"])
