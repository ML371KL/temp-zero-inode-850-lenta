# -*- coding: utf-8 -*-
"""Витрина: что обязано быть на экране и в каком виде (перенос 850oa, D16).

Шесть экранов («Оценка», «Что в цене», «Расчёт», «Ближайший отчёт», «Деньги и
долг», «Допущения»), графики в настоящих пикселях карточки, у главных графиков —
таблица-двойник, плашки общим поясом, числа по-русски и только из выпуска.
Сверху — правила «Ленты»: имя и тикер только из `meta.company`, единица
периода — из `meta.period_unit` и `nowcast.period_unit`, карточек «Магнита» нет,
новые карточки D16 стоят на своих экранах и читают поля своего блока.

Экран проверяется двумя способами: данные — по выпуску, поведение разметки —
по тексту `app.js`. Снимки headless Chrome с замерами (переносы чисел, наезды
подписей, прокрутка, консоль, CSP) — отчёт этапа P4b-front. Проверки текста
витрины — `ci_only` (суточный такт их не гоняет: витрина выкладывается
отдельно); данные — быстрая сборка на книге (`needs_book`).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from model.payload import (FORBIDDEN_FIELDS, INDICATOR_UNITS, REQUIRED_FIELDS, REQUIRED_TOP_LEVEL,
                           build_payload, indicator_title)
from tests.web_source import APP, APP_CODE, CSS_CODE, function_body as _function_body

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def payload():
    """Быстрая сборка: полосы, обратного DCF и суждений экраны данных не читают."""
    from model.engine import run_release

    return build_payload(run_release(gates=False), with_slow=False)


# ------------------------------------------------------- данные для экранов


@pytest.mark.needs_book
def test_countdown_to_the_next_report_exists(payload):
    """Экран «Ближайший отчёт» без даты отчёта — экран без ближайшего отчёта."""
    fact = payload["calendar"]["next_fact"]
    assert fact is not None and fact["days"] > 0
    assert fact["precision"] in ("day", "month", "window")
    assert fact["earliest"] <= fact["when"] <= fact["latest"]
    assert payload["calendar"]["events"], "календарь ближайших событий обязан быть непустым"


@pytest.mark.needs_book
@pytest.mark.ci_only
def test_assumptions_carry_their_sources(parallel_band):
    """Таблица допущений есть только в полном выпуске: источник, значение, порядок."""
    from model.engine import run_release

    with parallel_band():
        rows = build_payload(run_release(gates=False))["assumptions"]
    assert len(rows) >= 10
    assert all(row["source"] for row in rows), "у каждого допущения обязан быть источник"
    assert all(row["value"] is not None for row in rows), "и значение"
    spreads = [row["spread"] for row in rows]
    assert spreads == sorted(spreads, reverse=True), "порядок — по цене ошибки"


@pytest.mark.needs_book
def test_claims_are_broken_into_lines(payload):
    """Строки моста складываются в `claims` точно: иначе проверить мост нечем."""
    for scenario in payload["scenarios"]:
        lines = scenario["claims_lines"]
        assert len(lines) >= 5, scenario["name"]
        assert sum(line["value"] for line in lines) == pytest.approx(scenario["claims"], abs=0.1)
        assert {"operating_cash", "net_debt"} <= {line["key"] for line in lines}


@pytest.mark.needs_book
def test_rate_sensitivity_is_net_of_cash(payload):
    """Касса дорожает вместе с долгом: нетто меньше брутто — и по реестру, и по книге."""
    debt = payload["debt"]
    if not debt["register"]["available"]:
        pytest.fail("реестр долга есть на книге 1.0 — проверка не должна пустеть")
    assert debt["rate_sensitivity_net_per_pp"] < debt["rate_sensitivity_per_pp"]
    assert debt["rate_sensitivity_net_register_per_pp"] < debt["rate_sensitivity_per_pp"]


@pytest.mark.ci_only
def test_the_sensitivity_card_prints_gross_and_both_nets():
    card_ = _function_body("sensitivityCard")
    for field in ("debt.rate_sensitivity_per_pp", "debt.rate_sensitivity_net_register_per_pp",
                  "debt.rate_sensitivity_net_per_pp"):
        assert field in card_, field


@pytest.mark.needs_book
def test_the_screen_shows_the_interest_channel(payload):
    """Прогноз процентов с эталоном рядом: прогноз без эталона нечем измерить."""
    interest = payload["nowcast"]["interest"]
    assert interest["net_interest"] > 0 and interest["naive"]
    assert interest["debt_interest"] - interest["cash_income"] == pytest.approx(
        interest["net_interest"], abs=0.05)
    assert interest["opening_gross_debt"] > 0 and interest["redemptions_count"] >= 0


@pytest.mark.ci_only
def test_the_interest_card_prints_the_benchmark_and_the_opening_debt():
    card_ = _function_body("interestCard")
    for field in ("interest.naive", "interest.main_benchmark", "interest.opening_gross_debt",
                  "interest.redemptions_note", "interest.accuracy", "interest.unavailable"):
        assert field in card_, field


@pytest.mark.needs_book
def test_indicators_have_human_titles_and_units(payload):
    """Машинный идентификатор и доля единицы на экране — отладка, а не панель."""
    for item in payload["indicators"]:
        assert item["title"] and item["title"] != item["id"], item["id"]
        assert item["unit"] in INDICATOR_UNITS, (item["id"], item["unit"])
        assert item["group_title"], item["id"]
    assert indicator_title("cbr.key_rate") == "Ключевая ставка ЦБ"
    titles = [indicator_title(f"moex.zcyc.{t:g}y") for t in (0.25, 0.5, 0.75, 1, 2, 5, 10)]
    assert len(set(titles)) == len(titles) and "Кривая ОФЗ, 0,25y" in titles


@pytest.mark.ci_only
def test_the_indicators_card_prints_titles_groups_and_units_of_the_release():
    card_ = _function_body("indicatorsCard")
    assert "i.title" in card_ and "indicatorValue(i)" in card_ and "item.group_title" in card_
    assert "i.id" not in card_, "идентификатор ряда на экране"
    assert "items.filter((i) => i.tile)" in card_, "плитки — ряды, помеченные выпуском"
    assert "i.missing" in card_, "плитка без ряда называет причину"
    value = _function_body("indicatorValue")
    for unit in INDICATOR_UNITS:
        assert f'case "{unit}"' in value or unit == "number", unit
    line = next(row for row in value.splitlines() if 'case "index"' in row)
    assert "fmt.pp(" not in line and '"%"' in line, "уровень индекса — проценты с базы"


def test_every_gate_has_a_human_name_on_the_screen():
    """Сработавший гейт печатается по-русски; список гейтов — из model/checks.py."""
    checks = (ROOT / "model" / "checks.py").read_text(encoding="utf-8")
    gates = set(re.findall(r'"([a-z_]+)", Severity\.GATE', checks))
    assert len(gates) >= 12, f"список гейтов не найден: {sorted(gates)}"
    block = APP_CODE[APP_CODE.index("const GATE_NAMES = {"):]
    named = set(re.findall(r"^\s+([a-z_]+):", block[:block.index("};")], flags=re.M))
    assert gates <= named, f"без имени на витрине: {sorted(gates - named)}"
    assert "GATE_NAMES[g.key] || g.key" in _function_body("gatesCard")


@pytest.mark.needs_book
def test_the_tiles_are_marked_by_the_release(payload):
    from model.payload import INDICATOR_TILES

    assert 4 <= len(INDICATOR_TILES) <= 10, "плиток столько, сколько помещается на экран"
    shown = {i["id"]: i for i in payload["indicators"]}
    for key in INDICATOR_TILES:
        if key in shown:
            assert shown[key]["tile"], key


@pytest.mark.needs_book
def test_journal_and_scoreboard_are_keyed_by_the_target(payload):
    """Журнал без дублей, табло — по величине журнала из выпуска."""
    seen = set()
    for row in payload["nowcast"]["journal"]:
        key = (row["target"], row["period"], round(row["value"], 6))
        assert key not in seen, f"дубль в журнале: {key}"
        seen.add(key)
    board = payload["nowcast"]["scoreboard"]
    assert payload["nowcast"]["target"] in board
    for rows in board.values():
        periods = [row["period"] for row in rows]
        assert len(periods) == len(set(periods)), "одна строка на событие"


@pytest.mark.ci_only
def test_the_scoreboard_line_reads_the_board_of_the_release_target():
    assert "board[d.nowcast.target]" in _function_body("scoreboardLine")


# ------------------------------------------------------------ поведение витрины

SCREEN_NAMES = ["book", "debt", "market", "model", "overview", "report"]


@pytest.mark.ci_only
def test_there_are_six_screens_and_the_tabs_name_them():
    screens = re.search(r"const SCREENS = \{(.*?)\};", APP_CODE, re.S).group(1)
    assert sorted(re.findall(r"(\w+):", screens)) == SCREEN_NAMES
    assert sorted(re.findall(r'data-screen="(\w+)"', HTML)) == SCREEN_NAMES


@pytest.mark.ci_only
def test_tabs_report_their_state_to_a_screen_reader():
    render = _function_body("render")
    assert 'setAttribute("aria-selected"' in render and "tabIndex" in render
    assert 'role="tablist"' in HTML and 'role="tab"' in HTML
    tabs = _function_body("wireTabs")
    for key in ('"ArrowRight"', '"ArrowLeft"', '"Home"', '"End"'):
        assert key in tabs, key


@pytest.mark.ci_only
def test_screens_have_direct_links():
    """Адрес экрана — #overview … #book; старых адресов у этой витрины нет."""
    assert "hashchange" in APP_CODE and "screenFromHash" in APP_CODE
    assert "history.replaceState" in APP_CODE and "history.pushState" in APP_CODE
    assert "OLD_HASHES" not in APP_CODE


@pytest.mark.ci_only
def test_a_broken_screen_does_not_take_down_the_panel():
    render = _function_body("render")
    assert "try {" in render and "catch" in render and "Экран не отрисовался" in render
    paint = _function_body("paint")
    assert "try {" in paint and "catch" in paint and "График не отрисовался" in paint


@pytest.mark.ci_only
def test_the_year_table_lets_you_choose_a_scenario():
    debt = _function_body("screenDebt")
    assert "MONEY_SCENARIO" in debt and "chooser(" in debt
    assert "annualCard(d, pick)" in debt and "bridgeCard(d, pick)" in debt
    assert 'class: "chooser"' in APP_CODE and "aria-pressed" in _function_body("chooser")


@pytest.mark.ci_only
def test_the_refinancing_wall_is_drawn_not_only_listed():
    assert "function wallChart" in APP_CODE
    assert "wallChart(d.debt.wall" in _function_body("wallCard")


@pytest.mark.ci_only
def test_forecasts_are_shown_with_their_intervals():
    """Прогноз и эталон — на одной шкале; на графике «что даст отчёт» — маржа
    периода модели, которой соответствует квартальный нау-каст, и гайденс."""
    bar = _function_body("intervalBar")
    assert "ib-range" in bar and "ib-mark" in bar and "nc.std_error" in bar
    assert "intervalBar(nc, naive, d)" in _function_body("nowcastCard")
    impact = _function_body("impactChart")
    assert "nc.half_value" in impact and "nc.half_std_error" in impact
    assert "guide.required_half_margin" in impact


@pytest.mark.ci_only
def test_no_machine_jargon_on_the_report_screen():
    report = "".join(_function_body(name) for name in (
        "screenReport", "countdownCard", "nowcastCard", "impliedHalfCard", "intervalBar",
        "impactChart", "impactCard", "scoreboardLine", "retroBenchmarksCard", "interestCard",
        "forecastsCard", "indicatorsCard", "journalCard", "eventsFullCard"))
    for jargon in ("Хэш входов", "A-P2u", "inputs_sha"):
        assert jargon not in report, jargon
    assert "d.nowcast.target_titles" in _function_body("targetName"), "подписи величин — из выпуска"


@pytest.mark.ci_only
def test_numbers_are_written_in_russian():
    for helper in ("num(", "pct(", "pp(", "bn(", "x(", "rub(", "signed("):
        assert helper in APP_CODE[APP_CODE.index("const fmt = {"):], helper
    assert 'new Intl.NumberFormat("ru-RU"' in APP_CODE
    assert "MINUS" in APP_CODE[APP_CODE.index("const fmt = {"):APP_CODE.index("function formatByUnit(")]
    for line in (row for row in APP_CODE.splitlines() if "toFixed(" in row):
        assert re.search(r"\b[xy]\(", line), "toFixed вне координат SVG: " + line.strip()


@pytest.mark.ci_only
def test_ru_text_keeps_dates_and_writes_decimal_commas():
    """`ruText` исполняется настоящим движком JS (node), а не сверяется текстом."""
    node = shutil.which("node")
    assert node, "нужен node: функция витрины проверяется исполнением"
    minus = re.search(r'(?m)^const MINUS = "[^"]*";$', APP)
    begin = APP.index("function ruText(")
    body = APP[begin:APP.index("\n}\n", begin) + 2]
    cases = {
        "26.09.2026": "26.09.2026",
        "сверка 22.09.2026: σ 0.10126, 3 цели": "сверка 22.09.2026: σ 0,10126, 3 цели",
        "с 01.10.2024 по 30.09.2025: -7.5 %": "с 01.10.2024 по 30.09.2025: −7,5 %",
        "кривая 2026-09-18 (-0.3 п.п.)": "кривая 18.09.2026 (−0,3 п.п.)",
    }
    script = (minus.group(0) + "\n" + body + "\nconst cases = " + json.dumps(list(cases))
              + ";\nprocess.stdout.write(JSON.stringify(cases.map(ruText)));\n")
    run = subprocess.run([node, "-e", script], capture_output=True, text=True,
                         encoding="utf-8", timeout=60, check=True)
    assert dict(zip(cases, json.loads(run.stdout))) == cases


@pytest.mark.ci_only
def test_period_labels_come_from_the_period_id_and_the_release_unit():
    """Подписи «полугодие/квартал» — из `meta.period_unit` и `nowcast.period_unit`;
    подпись периода — по идентификатору («2026H2» → «2П 2026», «2026Q3» → «3 кв. 2026»)."""
    node = shutil.which("node")
    assert node
    assert "d.meta && d.meta.period_unit" in _function_body("modelUnit")
    assert "d.nowcast && d.nowcast.period_unit" in _function_body("reportUnit")
    for word in ("полугоди", "квартал"):
        outside = APP_CODE.replace(APP_CODE[APP_CODE.index("const PERIOD_WORDS = {"):
                                            APP_CODE.index("function unitWords(")], "")
        found = [line.strip() for line in outside.splitlines() if word in line.lower()
                 and "BENCH_NAMES" not in line]
        allowed = [line for line in found if re.search(r"квартал(?:а|ов)? (?:год назад|г/г)|последних кварталов|"
                                                       r"квартальная поправка|Квартальная", line)]
        assert set(found) <= set(allowed), f"«{word}» литералом вне словаря единиц: {sorted(set(found) - set(allowed))[:3]}"
    nbsp = re.search(r'(?m)^const NBSP = "[^"]*";$', APP).group(0)
    script = "\n".join([nbsp, _function_body("periodLabel"), _function_body("periodShort"),
                        "process.stdout.write(JSON.stringify([periodLabel('2026H2'), periodLabel('2026Q3'),"
                        " periodLabel('2026FY'), periodShort('2026H2'), periodShort('2027Q4')]));"])
    run = subprocess.run([node, "-e", script], capture_output=True, text=True, encoding="utf-8",
                         timeout=60, check=True)
    nb = chr(0xA0)
    assert json.loads(run.stdout) == [f"2П{nb}2026", f"3{nb}кв.{nb}2026", f"2026{nb}год", "2П’26", "4К’27"]


@pytest.mark.ci_only
def test_charts_are_drawn_in_real_pixels_and_labels_are_readable():
    box = _function_body("svgBox")
    assert "viewBox: `0 0 ${width} ${height}`" in box and "width, height" in box
    assert "ResizeObserver" in APP_CODE and "SIZE.observe(host)" in _function_body("chart")
    for cls in ("text", ".tick", ".label", ".label-strong"):
        rule = re.search(r"\.chart " + re.escape(cls) + r"\s*\{([^}]*)\}", CSS_CODE)
        assert rule, f"нет правила .chart {cls}"
        size = re.search(r"font-size:\s*([\d.]+)px", rule.group(1))
        if size:
            assert float(size.group(1)) >= 12, f".chart {cls}: {size.group(1)} px"
    assert not re.search(r"font-size[\"']?:\s*[\"']?(?:[0-9]|1[01])(?:\.\d+)?px", APP_CODE)
    assert "paint-order" in CSS_CODE and "halo" in _function_body("label")


@pytest.mark.ci_only
def test_every_chart_has_a_table_twin():
    assert APP_CODE.count("withTable(") >= 11
    for name in ("hero", "evLayersCard", "worldsCard", "wallCard", "impactCard", "retroBenchmarksCard",
                 "curveCard", "historyCard", "reverseDcfCard", "integrationCard", "dividendsCard",
                 "basesCard"):
        assert "withTable(" in _function_body(name), f"{name}: у графика нет таблицы"


@pytest.mark.ci_only
def test_freshness_is_measured_from_the_valuation_date():
    fresh = _function_body("freshnessCard")
    assert "d.meta.valuation_date" in fresh and "Свежесть входов" in fresh
    assert "freshnessCard(d)" in _function_body("screenBook")


# ---------------------------------------------- компания, тема, заголовки безопасности


@pytest.mark.ci_only
def test_the_front_carries_no_company_literals():
    """D16: имя и тикер — только из выпуска (`meta.company`). Витрина общая: ни
    «Ленты», ни «Магнита» (имени, тикера, ключей рядов) в ней нет."""
    for pattern in (r"Лент", r"\bLENT\b", r"lenta\.", r"Магнит", r"\bMGNT\b", r"magnit\."):
        assert not re.search(pattern, APP, re.I if pattern.startswith("m") else 0), pattern
    assert "d.meta.company" in _function_body("company") and "d.meta.company" in _function_body("ticker")
    assert "ticker(d)" in _function_body("hero") and "company(d)" in _function_body("colophon")
    assert "company(DATA)" in _function_body("render")


@pytest.mark.ci_only
def test_the_night_theme_keys_are_the_panels_own():
    script = re.search(r"<script>(.*?)</script>", HTML, re.S).group(1)
    assert 'DAY = "lenta-theme", NIGHT = "lenta-theme-tonight", FROM = 20, TO = 7' in script
    assert "window.__theme = { isNight: isNight, resolve: resolve, remember: remember" in script
    assert "window.__theme.remember(next)" in _function_body("wireTheme")


@pytest.mark.ci_only
def test_there_are_no_disclaimers_on_the_screen():
    """Правило владельца (26.09.2026): на витрине нет оговорок и дисклеймеров."""
    for text in (HTML, APP, CSS_CODE):
        for phrase in ("инвестиционн", "disclaimer", "не является рекомендац", "покупать/продавать"):
            assert phrase not in text.lower(), phrase


@pytest.mark.ci_only
def test_the_static_pages_declare_a_csp():
    headers = (ROOT / "web" / "_headers").read_text(encoding="utf-8")
    assert "/*" in headers
    for directive in ("default-src 'self'", "object-src 'none'", "base-uri 'none'",
                      "form-action 'none'", "frame-ancestors 'none'", "font-src 'self'",
                      "script-src 'self'"):
        assert directive in headers, directive
    assert "Referrer-Policy: no-referrer" in headers and "X-Content-Type-Options: nosniff" in headers


@pytest.mark.ci_only
def test_nothing_is_loaded_from_third_party_addresses():
    for text in (HTML, APP, (ROOT / "web" / "styles.css").read_text(encoding="utf-8")):
        assert not re.search(r"(?:src|href)\s*=\s*[\"']https?://", text)
        assert not re.search(r"@import|url\(\s*[\"']?https?://", text)
    assert "fetch(API" in APP_CODE and 'const API = "/api/model"' in APP_CODE
    assert len(re.findall(r"<script", HTML)) == 2, "инлайн-скрипт темы и app.js — и всё"
    assert "api/quote" not in APP


# ---------------------------------------------- заголовок и ползунок λ


@pytest.mark.ci_only
def test_the_slider_moves_the_whole_headline():
    hero = _function_body("hero")
    assert 'slider.addEventListener("input"' in hero
    update = hero[hero.index("const update = () => {"):hero.index('slider.addEventListener("input"')]
    for part in ("headlineAt(head, lam)", 'id: "fv-headline"', 'id: "kpi-central"', 'id: "fv-ev"',
                 "heroTiles(d, lam", "hl.printed_band80", "hl.printed_band50"):
        assert part in update, part
    handler = hero[hero.index('slider.addEventListener("input"'):]
    assert "update();" in handler and "repaint(plot)" in handler
    tiles = _function_body("heroTiles")
    assert "printedPoint(d, lam)" in tiles and "ledeHtml(d, lam)" in tiles and 'id: "fv-lede"' in tiles


@pytest.mark.ci_only
def test_the_slider_rebuilds_the_median_from_the_release_draws():
    q = _function_body("quantile7")
    assert "(n - 1) * q" in q and "Math.floor(h)" in q and "Math.min(lo + 1, n - 1)" in q
    centres = _function_body("centresAt")
    assert "head.low_draws" in centres and "lo + lam * (head.high_draws[i] - lo)" in centres
    at = _function_body("headlineAt")
    assert "head.own_macro_confidence" in at and "head.printed_median" in at and "head.print_step" in at
    assert "roundHalfEven(" in at and all(q_ in at for q_ in ("q(0.5)", "q(0.1)", "q(0.9)", "q(0.25)", "q(0.75)"))
    even = _function_body("roundHalfEven")
    assert "=== 0.5" in even and "2 * Math.round(x / 2)" in even
    point = _function_body("pointAt")
    assert "view.low + lam * (view.high - view.low)" in point and "fv.central" in point


@pytest.mark.ci_only
def test_the_front_computes_nothing_but_the_books_lambda_rule():
    """Единственный пересчёт — ползунок λ; всё прочее печатается из выпуска."""
    assert APP_CODE.count("low_draws") == APP_CODE.count("head.low_draws")
    assert set(re.findall(r"function (\w+)\([^)]*\) \{[^}]*head\.low_draws", APP_CODE)) <= {"centresAt", "hero"}
    reducers = [m.start() for m in re.finditer(r"\.reduce\(", APP_CODE)]
    dist, dist_end = APP_CODE.index("function distributionChart("), APP_CODE.index("function rangeRowChart(")
    assert all(dist < pos < dist_end for pos in reducers), "сумма вне графика распределения"
    for forbidden in ("pick.ev - pick.pv_fcff", "reported - model", "reg.net_debt - reported",
                      "c.probability, 0)", "<= market).length", "net_debt_ifrs16 - ", "lease_liabilities +"):
        assert forbidden not in APP_CODE, forbidden
    hero = _function_body("hero")
    assert "head.p_below_market" in hero and "head.market_percentile" in hero
    # λ-шаг ползунка — сетка выпуска; итог g — число выпуска, а не сумма витрины.
    assert "g.sum_signed" in _function_body("governanceCard")


@pytest.mark.ci_only
def test_the_value_screen_speaks_the_book():
    overview = "".join(_function_body(name) for name in (
        "hero", "heroTiles", "ledeHtml", "pointVsMedianText", "pricedTeaser", "bandDrivers",
        "reportTeaser", "methodNote"))
    for text in ("медиана по суждениям книги", "полоса 80 %", "полоса 50 %",
                 "Точка при центральных значениях всех суждений", "рыночные ставки как есть",
                 "свой макро-взгляд", "перцентиле распределения центра", "P(ниже рынка)",
                 "Что определяет полосу", "Что заложено в цену", "рыночного V*",
                 "Вклад взгляда на инфляцию и ставки", "внутренняя стоимость"):
        assert text in overview, text
    assert "rows.slice(0, 6)" in _function_body("bandDrivers")
    tiles = _function_body("heroTiles")
    assert "const ev = at || first;" in tiles and "ev.rub_per_1pct_ev" in tiles
    assert "atLambda(first.by_lambda, lam)" in tiles
    assert "rub_per_ev_percent" not in APP_CODE
    assert "Аналоги на одной базе" in _function_body("peersCard")


@pytest.mark.ci_only
def test_the_lede_names_the_books_reason_only_for_the_books_direction():
    lede = _function_body("pointVsMedianText")
    assert "headlineAt(head, lam).median" in lede
    above, below = lede[lede.index("point > medianAt"):].split('"Точка не выше медианы.', 1)
    assert "Точка выше медианы" in above and "асимметричны" in above and "асимметричны" not in below
    assert "d.judgements" in lede and "low_label" in lede and "high_label" in lede
    assert "c.rank_corr" in lede, "сторону, снижающую цену, называет знак вклада из выпуска"
    assert "В выпуске нет прогонов по суждениям" in _function_body("heroWithoutBand")


@pytest.mark.ci_only
def test_the_priced_screen_prints_the_books_reverse_dcf():
    card_ = _function_body("reverseDcfCard")
    for field in ("block.rows", "r.book_value", "r.value", "r.range", "r.inside_range"):
        assert field in card_, field
    value = _function_body("reverseValue")
    assert "row.unit" in value and '"mult"' in value and '"times"' in value
    assert '"недостижимо"' in card_ and "в диапазоне" in card_ and "вне диапазона" in card_
    market = _function_body("screenMarket")
    assert "reverseDcfCard(d)" in market and "spreadCard(d)" in market


@pytest.mark.ci_only
def test_the_mapping_is_the_intrinsic_value_and_the_magnit_cards_are_gone():
    """D4, D16: цена — внутренняя стоимость; карточек и полей структурного пути
    850oa (σ, страйк, кредитный пут, «старый метод»), зарплатной сетки, сверки
    реестра облигаций и событий «Магнита» на витрине нет."""
    mapping = _function_body("mappingCard")
    assert "fv.method" in mapping and '"limited_liability"' in mapping
    for gone in ("sigmaCard", "strikeForm", "STRIKE_EFFECTIVE", "sigma_live", "sigma_ev", "strike",
                 "credit_put", "old_method", "price_structural", "registry_check", "modal_target",
                 "wage", "тонкий слой", "Самбери", "Тандер", "РЕПО", "repo|treasury", "book_constants",
                 "x5_ev_ebitda", "INDICATOR_GROUPS"):
        assert gone not in APP_CODE, gone
    assert not re.search(r"market\.peers\b(?!_)", APP_CODE), "константы аналогов книги"


@pytest.mark.ci_only
def test_the_distribution_of_the_draws_is_the_hero_chart():
    dist = _function_body("distributionChart")
    for part in ("centresAt(head, lam)", "headlineAt(head, LAMBDA)", "hl.band80", "hl.band50",
                 "hl.median", "market", "pointAt(d, LAMBDA)", "ticker(d)"):
        assert part in dist, part
    assert "P10" in _function_body("hero") and "P90" in _function_body("hero")
    scen = _function_body("scenariosCard")
    assert "Сценарные клетки" in scen and "не заголовок" in scen


@pytest.mark.ci_only
def test_a_stale_release_warns_on_screen():
    assert "STALE_HOURS = 96" in APP
    belt = _function_body("banners")
    assert "banner-stale" in belt and "d.meta.generated_at" in belt
    assert "app.replaceChildren(...banners(DATA), screen)" in _function_body("render")
    assert ".prepend(" not in APP_CODE
    assert "STALE_HOURS" in _function_body("releaseChip")


# ---------------------------------------------- «Ближайший отчёт»


def test_the_report_screen_shows_the_main_benchmark(tmp_path):
    """Эталон на экране — ГЛАВНЫЙ (`MAIN_BENCHMARK`), а не первый ключ словаря
    (аудит 850oa, C2). Журнал здесь свой: проверяется правило выбора."""
    from indicators.collect import MARGIN_TARGET
    from indicators.journal import MAIN_BENCHMARK, Journal
    from model.payload import _naive_block

    journal = Journal(tmp_path / "j.sqlite")
    journal.record(target=MARGIN_TARGET, period="2026Q3", value=0.061, std_error=0.01,
                   equation="ожидание модели", version="v1", inputs={}, today=date(2026, 9, 28))
    journal.record_naive(MARGIN_TARGET, "2026Q3", {
        "seasonal_naive": 0.0733, "last_period": 0.0669, "mean_of_year": 0.0683,
        "yoy_plus_shift": 0.0564})
    block = _naive_block(journal)[f"{MARGIN_TARGET} 2026Q3"]
    assert block["main"] == MAIN_BENCHMARK[MARGIN_TARGET] == "yoy_plus_shift"
    assert block["naive"][block["main"]] == pytest.approx(0.0564)
    assert next(iter(block["naive"])) != block["main"]


@pytest.mark.ci_only
def test_the_report_screen_prints_the_main_benchmark():
    assert "bench.naive[bench.main]" in _function_body("mainBenchmark")
    assert "naive.naive[naive.main]" in _function_body("nowcastCard")
    assert "interest.main_benchmark" in _function_body("interestCard")


def test_the_report_screen_has_no_machine_identifiers():
    """Уравнение нау-каста переведено в источнике: латиницы в нём нет."""
    from indicators.nowcast import margin_nowcast
    from indicators.store import Store
    from model.book import book

    equation = margin_nowcast(Store(), book(), period="2026Q3").equation
    assert "ожидание модели" in equation, equation
    assert not any("a" <= ch.lower() <= "z" for ch in equation), equation


@pytest.mark.needs_book
def test_the_components_of_the_nowcast_are_titled_in_words(payload):
    titles = re.search(r"const COMPONENT_TITLES = \{(.*?)\};", APP_CODE, re.S).group(1)
    for name in payload["nowcast"]["margin"]["components"]:
        assert re.search(rf"\b{name}:", titles), f"слагаемое {name} без подписи"
    assert "COMPONENT_TITLES[k]" in _function_body("nowcastCard")


@pytest.mark.needs_book
def test_the_report_screen_shows_expectation_admission_and_retro(payload):
    block = payload["nowcast"]
    for key in ("expectation", "expectation_se", "deviation", "half", "half_value"):
        assert key in block["margin"], key
    status = block["admission"][block["target"]]
    for key in ("rule", "reason", "title", "version", "first_countable", "earliest_decision"):
        assert status.get(key), key
    retro = block["retro"]
    assert retro["note"] and retro["split_year"]
    for row in retro["margin"]["benchmarks"]:
        assert {"title", "main", "all", "clean", "before", "since"} <= set(row)


@pytest.mark.ci_only
def test_the_report_screen_reads_expectation_admission_retro_and_the_half():
    for field, where in (("nc.expectation", "nowcastCard"), ("nc.deviation", "nowcastCard"),
                         ("now.admission", "countdownCard"), ("admission.rule", "countdownCard"),
                         ("admission.reason", "countdownCard"), ("admission.first_countable", "countdownCard"),
                         ("admission.broken_ahead", "countdownCard"), ("d.nowcast.retro", "retroBenchmarksCard"),
                         ("retro.split_year", "retroBenchmarksCard"), ("ih.table", "impliedHalfCard"),
                         ("ih.second_quarter_reports", "impliedHalfCard"), ("guide.required_half_margin", "impliedHalfCard"),
                         ("guide.quarter_benchmark", "impliedHalfCard"), ("r.half_margin_row", "impliedHalfCard")):
        assert field in _function_body(where), f"{where} обязана читать {field}"
    assert "impliedHalfCard(d)" in _function_body("screenReport")
    assert not re.search(r"\d[.,]\d\d", _function_body("retroBenchmarksCard")), "дробное число литералом"


# ---------------------------------------------- карточки «Ленты» (D16)


@pytest.mark.ci_only
def test_the_lenta_cards_stand_on_their_screens():
    model = _function_body("screenModel")
    for card_ in ("networkCard(d)", "perimeterCard(d)", "integrationCard(d)", "strategyCard(d)",
                  "governanceCard(d)", "mappingCard(d)"):
        assert card_ in model, card_
    debt = _function_body("screenDebt")
    assert "dividendsCard(d)" in debt and "basesCard(d)" in debt
    reads = {
        "networkCard": ("n.anchor", "n.expected", "n.units", "stores_est", "opened_area", "density"),
        "perimeterCard": ("p.deals", "p.breaks_halves", "p.goodwill", "x.assumed_debt", "x.contribution"),
        "integrationCard": ("density_ratio_anchor", "okey_to_hyper_density", "density_press_2025",
                            "diy.pbt_margin", "it.acquired_nol", "it.integration_capex_book"),
        "strategyCard": ("s.targets", "s.comparisons", "s.history", "s.guidance", "margin_required"),
        "dividendsCard": ("dv.ladder", "cur.rung", "cur.leverage_reported", "r.dps", "dv.dividends_from_year"),
        "basesCard": ("a.net_debt_ias17", "a.lease_liabilities", "a.net_debt_ifrs16", "r.lease_gap", "b.ebitda"),
        "governanceCard": ("g.components", "c.signed", "g.on_850oa_scope", "g.on_850oa_scope_title",
                           "c.in_850oa_scope", "g.price_sensitivity"),
    }
    for name, fields in reads.items():
        body = _function_body(name)
        for field in fields:
            assert field in body, f"{name} обязана читать {field}"


@pytest.mark.needs_book
def test_the_front_reads_only_declared_fields(payload):
    """Нет карточек без полей в выпуске (D16): каждое `d.<блок>.<поле>` витрины —
    объявленное поле контракта (`REQUIRED_FIELDS`) или поле быстрого выпуска, а
    поля, снятые с контракта (`FORBIDDEN_FIELDS`), витрина не читает."""
    used = set(re.findall(r"\bd\.([a-z_]+)\.([a-z_0-9]+)\b", APP_CODE))
    assert used, "регулярка ничего не нашла — поменялось соглашение `d` = выпуск"
    for block, field in sorted(used):
        assert block in REQUIRED_TOP_LEVEL, block
        declared = REQUIRED_FIELDS.get(block)
        node = payload.get(block)
        if declared is not None:
            assert field in declared, f"d.{block}.{field}: поле не объявлено в REQUIRED_FIELDS"
        elif isinstance(node, dict) and node:
            assert field in node, f"d.{block}.{field}: такого поля в выпуске нет"
    forbidden = {f for fields in FORBIDDEN_FIELDS.values() for f in fields} - {"mean", "explained", "peers"}
    for field in sorted(forbidden):
        assert not re.search(rf"\.{field}\b", APP_CODE), f"витрина читает снятое поле {field}"


# ---------------------------------------------- таблицы на телефоне и плашки


@pytest.mark.ci_only
def test_phone_tables_keep_the_numbers_on_screen():
    assert "overflow-wrap: anywhere" not in CSS_CODE and "word-break: break-all" not in CSS_CODE
    assert re.search(r"td\.num\s*\{[^}]*white-space:\s*nowrap", CSS_CODE)
    assert re.search(r"thead th\s*\{[^}]*overflow-wrap:\s*normal", CSS_CODE)
    assert re.search(r"\.scroll\s*\{[^}]*overflow-x:\s*auto", CSS_CODE)
    assert "min-width: max-content" not in CSS_CODE
    phone = CSS_CODE[CSS_CODE.index("@media (max-width: 480px)"):]
    assert "position: sticky" in phone and "max-width: 42vw" in phone
    assert not re.search(r"(?<![\w-])(?:th|td)(?![\w-])[^{}]*\{[^}]*display:\s*none", CSS_CODE)
    assert "tr.detail" in CSS_CODE
    assert APP_CODE.count('el("table"') == 2, "таблицы данных строит только dataTable (и теплокарта)"
    assert 'class: "scroll"' in _function_body("dataTable")


@pytest.mark.ci_only
def test_free_text_goes_to_a_second_row_not_a_column():
    table = _function_body("dataTable")
    assert "opts.detail" in table and "colspan: columns.length" in table and '"detail"' in table
    assert 'detail: (r) => r.source ? detailsBlock("Источник", r.source)' in _function_body("judgementsCard")
    journal = _function_body("journalCard")
    assert '"Уравнение"' not in journal and "Уравнения версий" in journal
    assert 'el("details"' in APP_CODE and 'el("summary"' in APP_CODE


@pytest.mark.ci_only
def test_degraded_inputs_and_release_alarms_are_one_belt_on_every_screen():
    """Пояс плашек рисуется в `render()` перед ЛЮБЫМ экраном; условия — только от
    выпуска; тревоги уровня выпуска (карточка акции, капитал у нуля) — там же."""
    belt = _function_body("banners")
    assert "degraded_flag" in belt and "banner-degraded" in belt and "d.live.degraded" in belt
    warning = "Часть входов не свежая"
    assert warning in belt and APP_CODE.count(warning) == 1
    for screen in ("screenOverview", "screenMarket", "screenModel", "screenReport", "screenDebt", "screenBook"):
        assert warning not in _function_body(screen), screen
    for key in ('"book_update"', '"security_change"', '"limited_liability"'):
        assert key in belt, key
    assert re.search(r"replaceChildren\(\s*\.\.\.banners\(", _function_body("render"))
    branch = belt[belt.index("if (d.live"):belt.index("const ageHours")]
    assert re.match(r"if \(d\.live && d\.live\.degraded_flag\) \{", branch.strip())
    for forbidden in ("location.", "hash", "SCREENS", "CURRENT", "screen"):
        assert forbidden not in belt, forbidden
    assert "bookGate.message || bookGate.explanation" in belt


@pytest.mark.ci_only
def test_a_long_source_address_wraps_wherever_the_reasons_are_printed():
    assert re.search(r"\.belt\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)", CSS_CODE)
    classes = []
    for m in re.finditer(r'el\("ul",\s*\{([^}]*)\}', APP_CODE):
        tail = APP_CODE[m.end():m.end() + 200]
        if "degraded" in tail or "notes.map" in tail:
            cls = re.search(r'class:\s*"([^"]*)"', m.group(1))
            classes.append(cls.group(1) if cls else "")
    assert len(classes) == 2, classes
    for cls in classes:
        assert any(re.search(rf"\.{re.escape(n)}\b[^{{}}]*\{{[^}}]*overflow-wrap:\s*break-word", CSS_CODE)
                   for n in cls.split()), cls


# ---------------------------------------------- «Что в цене»


@pytest.mark.needs_book
def test_sellside_targets_carry_their_date_and_the_book_caveat(payload):
    from model.book import book

    summary = book()["market"]["sellside_summary"]
    shown = payload["market"]["sellside"]
    assert shown["as_of"] == summary["as_of"] and shown["note"] == summary["note"] and shown["note"]
    assert len(shown["houses"]) == shown["n"] and all(h["date"] and h["house"] for h in shown["houses"])


@pytest.mark.ci_only
def test_the_sellside_card_prints_each_house_with_its_date():
    body = _function_body("sellsideCard")
    for field in ("s.as_of", "s.note", "s.median", "s.min", "s.max", "s.n", "s.houses", "h.date", "h.house"):
        assert field in body, field
    assert "fmt.date(s.as_of)" in body and "sellsideCard(d.market.sellside, d)" in _function_body("screenMarket")
    for literal in ("2400", "2 400", "1473", "2800", "2 800"):
        assert literal not in APP_CODE, literal


@pytest.mark.needs_book
def test_the_next_report_card_has_no_numbers_of_its_own(payload):
    rows = payload["next_report_value"]
    assert len(rows) >= 5 and [r["margin"] for r in rows] == sorted(r["margin"] for r in rows)
    for fn in ("impactChart", "impactCard"):
        assert "d.next_report_value" in _function_body(fn)
    for row in rows:
        for key in ("low", "central", "high"):
            literal = str(int(round(row[key])))
            assert not re.search(rf"(?<![\d.]){literal}(?!\d)", APP_CODE), f"число таблицы литералом: {literal}"
    assert re.search(r"Разброс правила — \$\{fmt\.num\(rp\.sigma_pp \* 100, 1\)\} п\.п\.",
                     _function_body("impactCard"))
