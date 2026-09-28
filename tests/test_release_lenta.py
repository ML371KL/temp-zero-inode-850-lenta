# -*- coding: utf-8 -*-
"""Выпуск «Ленты» (контракт `lenta-v1`, этап P4b): блоки D16, квартальный
нау-каст, гигиена выпуска, снятые поля структурного пути 850oa.

Числа блоков — пересчёт книги, фактов и строк ядра; здесь проверяется, что они
действительно оттуда (тем же правилом, что у ядра и фактов), что блоки согласованы
между собой и с заголовком, и что сборка отказывает, когда контракт нарушен.
Быстрая сборка (`with_slow=False`) — полный выпуск проверяет сама сборка той же
`validate` (метка `ci_only` у полных тестов в `test_payload.py`).
"""
from __future__ import annotations

import copy
import json
import math
import sys
from pathlib import Path

import pytest

from model.engine import run_release
from model.payload import (FORBIDDEN_FIELDS, INDICATOR_GROUPS, INDICATOR_TILES, INDICATOR_UNITS,
                           NETWORK_YEARS, REQUIRED_FIELDS, REQUIRED_TOP_LEVEL, SCHEMA,
                           build_payload, hygiene_problems, indicator_group, indicator_title,
                           indicator_unit, is_tile, validate)

ROOT = Path(__file__).resolve().parents[1]
FACTS = ROOT / "data" / "facts"


@pytest.fixture(scope="module")
def release():
    return run_release()


@pytest.fixture(scope="module")
def payload(release):
    return build_payload(release, with_slow=False)


def _facts(name: str) -> dict:
    return json.loads((FACTS / name).read_text(encoding="utf-8"))


# ------------------------------------------------------------------ контракт


@pytest.mark.needs_book
def test_the_contract_is_lenta_v1_with_every_block_and_field(release, payload):
    assert payload["schema"] == SCHEMA == "lenta-v1"
    for block in ("network", "perimeter", "strategy", "dividends", "governance", "bases", "mixes"):
        assert block in REQUIRED_TOP_LEVEL and block in payload, block
    assert validate(payload, release=release) == []
    # Каждый объявленный путь полей — в выпуске (иначе список полей для витрины врёт).
    for dotted, fields in REQUIRED_FIELDS.items():
        node = payload
        for part in dotted.split("."):
            node = node[part]
        assert set(fields) <= set(node), (dotted, sorted(set(fields) - set(node)))


@pytest.mark.needs_book
def test_a_missing_field_or_a_returned_structural_field_stops_the_build(payload):
    """Удалённое поле ломает контракт; вернувшееся поле структурного пути 850oa —
    тоже (витрина снова нарисовала бы σ, страйк или «старый метод»)."""
    for dotted, fields in (("network", "expected"), ("nowcast.margin", "expectation_se"),
                           ("dividends.current", "rung"), ("meta", "period_unit")):
        broken = copy.deepcopy(payload)
        node = broken
        for part in dotted.split("."):
            node = node[part]
        node.pop(fields)
        assert any(dotted in p and fields in p for p in validate(broken)), (dotted, fields)
    for where, field, put in (
            ("fair_value", "sigma_live", lambda p: p["fair_value"].update(sigma_live={})),
            ("layers.*", "old_method", lambda p: p["layers"]["analytical"].update(old_method=1.0)),
            ("scenarios[]", "price_structural",
             lambda p: p["scenarios"][0].update(price_structural=1.0)),
            ("market", "peers", lambda p: p["market"].update(peers={"x5": 2.8})),
            ("debt", "registry_check", lambda p: p["debt"].update(registry_check={})),
            ("nowcast.margin", "modal_target",
             lambda p: p["nowcast"]["margin"].update(modal_target=None))):
        broken = copy.deepcopy(payload)
        put(broken)
        assert any(where in p and field in p for p in validate(broken)), (where, field)
    assert set(FORBIDDEN_FIELDS["layers.*"]) >= {"structural", "strike", "credit_put"}


@pytest.mark.needs_book
def test_meta_names_the_company_and_both_period_units(payload, release):
    meta = payload["meta"]
    assert meta["company"] == release.book["meta"]["company"]
    assert meta["period_unit"] == release.book["meta"]["period_unit"] == "half"
    assert payload["nowcast"]["period_unit"] == "quarter"


# ------------------------------------------------------------------ гигиена


# Утёкшие строки собираются из частей во время теста: сам файл теста проходит
# гигиену дерева (`tests/test_public_hygiene.py`), и адресов в нём нет.
AT, DOT, BS = "@", ".", chr(92)
LEAKS = [
    ("пишите на ir-desk" + AT + "company" + DOT + "ru", "почта"),
    ("звонить +7 (812) 555-12-34", "телефон"),
    ("звонить 8 (800) 200-00-00", "телефон"),
    ("хост " + DOT.join(["46", "62", "1", "9"]) + " ответил 503", "IPv4"),
    ("файл C:" + BS + "Us" + "ers" + BS + "someone" + BS + "x.json", "путь"),
    ("файл /ho" + "me/dash/state/x.json", "путь"),
    ("каталог /var/lib/lenta-850/release", "путь"),
]


@pytest.mark.tact
@pytest.mark.parametrize("text,rule", LEAKS)
def test_the_release_refuses_emails_phones_addresses_and_paths(text, rule):
    problems = hygiene_problems({"live": {"degraded": [text]}})
    assert problems and rule in problems[0] and "$.live.degraded[0]" in problems[0], problems
    assert text not in problems[0], "строка отказа не повторяет утёкший текст"


@pytest.mark.tact
def test_the_hygiene_rules_leave_ordinary_release_text_alone():
    ordinary = {"sources": ["https://www.finmarket.ru/database/analytics/6635586",
                            "https://smart-lab.ru/blog/news/1262227.php"],
                "note": "мир M: 2,5–4,5×; выручка 1 394,8 млрд ₽ (2025H2 + 2026H1); версия 1.0.2",
                "loop": "127.0.0.1", "doc": "198.51.100.7", "mail": "someone" + AT + "example.com",
                "ratio": "0.6329", "isin": "RU000A10CCN3"}
    assert hygiene_problems(ordinary) == []


@pytest.mark.needs_book
def test_a_leaked_path_in_the_release_is_exit_code_one(release, payload, monkeypatch, tmp_path, capsys):
    """Путь сервера в выпуске — код 1 и ни одного файла: на витрине остаётся прежний выпуск."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("build_release_hygiene",
                                                  ROOT / "ops" / "build_release.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    broken = copy.deepcopy(payload)
    broken["live"]["degraded"].append("кэш C:" + BS + "Us" + "ers" + BS + "someone" + BS + "slow.pickle")
    monkeypatch.setattr(build, "run_release", lambda live: release)
    monkeypatch.setattr(build, "build_payload", lambda _release, with_slow: broken)
    monkeypatch.setattr(build, "OUT", tmp_path / "release")
    monkeypatch.setattr(sys, "argv", ["build_release.py", "--fast"])
    assert build.main() == 1
    assert not (tmp_path / "release").exists()
    assert "гигиена выпуска" in capsys.readouterr().err


# ------------------------------------------------------------------ смеси клеток


@pytest.mark.needs_book
def test_the_headline_mix_is_the_lambda_blend_of_the_layers(release, payload):
    """V0 смеси заголовка — V0 точки (λ-смесь V0 слоёв); смесь сетки — слой «свой взгляд»."""
    mixes = payload["mixes"]
    lam = release.fair_value.own_macro_confidence
    own, neutral = release.layers["analytical"].v0, release.layers["macro_neutral"].v0
    assert mixes["headline"]["lambda"] == lam
    assert mixes["headline"]["v0"] == pytest.approx(lam * own + (1 - lam) * neutral, abs=0.006)
    assert mixes["grid"]["v0"] == pytest.approx(own, abs=0.006)
    assert mixes["headline"]["v0"] == pytest.approx(release.fair_value.center_ev.v0, abs=0.006)
    broken = copy.deepcopy(payload)
    broken["mixes"]["headline"]["v0"] += 1.0
    assert any("смесь заголовка" in p for p in validate(broken))


# ------------------------------------------------------------------ сеть


@pytest.mark.needs_book
def test_the_network_block_is_the_facts_at_the_anchor_and_the_core_ahead(release, payload):
    A = release.book
    net = payload["network"]
    first_year = int(A["meta"]["first_period"][:4])
    assert net["periods"][0] == A["meta"]["first_period"]
    assert net["periods"][-1] == f"{first_year + NETWORK_YEARS}H2"
    assert net["lt_period"] == A["meta"]["last_period"]
    anchor = {s["id"]: s for s in net["anchor"]["segments"]}
    assert list(anchor) == list(A["facts"]["segments"])
    for sid, facts in A["facts"]["segments"].items():
        assert anchor[sid]["revenue"] == facts["revenue"], sid
        assert anchor[sid]["area_end"] == facts.get("area_end"), sid
        assert anchor[sid]["stores_end"] == facts.get("stores_end"), sid
    # Ожидание сетки — Σ p · (величина клетки) строкой ядра.
    period, sid = net["periods"][1], "conv"
    want = sum(c.probability * next(r for r in c.result.rows if r.period == period).segments[sid].revenue
               for c in release.cells) / sum(c.probability for c in release.cells)
    row = next(r for r in net["expected"]["grid"]["rows"] if r["period"] == period)
    assert row["segments"][sid]["revenue"] == pytest.approx(want, abs=0.006)
    # Сегменты складываются в группу; магазины — площадь на среднюю площадь магазина якоря.
    for name, block in net["expected"].items():
        for r in block["rows"] + [block["lt"]]:
            revenue = math.fsum(s["revenue"] for s in r["segments"].values())
            assert revenue == pytest.approx(r["total"]["revenue"], abs=0.05), (name, r["period"])
            conv = r["segments"]["conv"]
            size = anchor["conv"]["area_end"] / anchor["conv"]["stores_end"]
            assert conv["stores_est"] == pytest.approx(conv["area_end"] / size, abs=1.0)
            assert r["segments"]["wholesale"]["area_end"] is None
    broken = copy.deepcopy(payload)
    broken["network"]["expected"]["grid"]["rows"][0]["total"]["revenue"] += 5.0
    assert any("сумма сегментов" in p for p in validate(broken))


# ------------------------------------------------------------------ периметр


@pytest.mark.needs_book
def test_the_perimeter_block_reads_the_deals_and_measures_okey_on_one_area_base(payload, release):
    facts = _facts("inorganic.json")
    per = payload["perimeter"]
    assert [d["id"] for d in per["deals"]] == [d["id"] for d in facts["deals"]]
    okey = next(d for d in per["deals"] if d["id"] == "okey")
    assert okey["assumed_debt"] == facts["deals"][-1]["assumed_debt"]["total"]
    assert okey["control"] == "2026-06-02" and okey["goodwill"] == pytest.approx(13.694519)
    seg = release.book["facts"]["segments"]
    ratio = per["integration"]["okey"]["density_ratio_anchor"]
    for p, value in ratio.items():
        want = ((seg["okey"]["revenue"][p] / seg["okey"]["eff_area_avg_hist"][p])
                / (seg["hyper"]["revenue"][p] / seg["hyper"]["eff_area_avg_hist"][p]))
        assert value == pytest.approx(want, abs=1e-4), p
    diy = per["integration"]["domlenta"]
    assert diy["pbt"] < 0 < diy["revenue"] and diy["pbt_margin"] == pytest.approx(
        diy["pbt"] / diy["revenue"], abs=1e-4)
    rows = per["integration"]["expected"]["grid"]
    assert rows[0]["okey_to_hyper_density"] is not None and rows[0]["capex_integration"] >= 0


# ------------------------------------------------------------------ стратегия и гайденс


@pytest.mark.needs_book
def test_the_strategy_block_holds_the_targets_the_model_path_and_the_history(payload, release):
    facts = _facts("strategy.json")
    st = payload["strategy"]
    assert st["targets"] == facts["targets"] and st["source"]["sha256"] == facts["source"]["sha256"]
    ids = {t["id"] for t in facts["targets"]}
    assert {"revenue_2028", "margin_2028", "margin_2025_2027", "nd_ebitda", "capex_pct"} <= ids
    # История — отчётные годы окна цели (IAS 17): маржа 2025 = EBITDA / выручка FY2025.
    base = _facts("accounting_base.json")
    pl = base["fy"]["FY2025"]["ias17"]["pl"]
    margin = next(h for h in st["history"] if h["target"] == "margin_2025_2027" and h["year"] == 2025)
    assert margin["value"] == pytest.approx(pl["ebitda"]["v"] / pl["revenue"]["v"], abs=1e-5)
    # Год с отчётным 1П — отчёт + ожидание сетки по 2П (правило гейта guidance_gap).
    year = int(release.book["facts"]["guidance"]["period"])
    row = next(r for r in st["model"]["grid"] if r["year"] == year)
    assert row["halves"] == ["reported", "model"]
    g = st["guidance"]
    assert g["fy_margin_model"] == pytest.approx(row["margin"], abs=1e-5)
    assert g["share_of_grid_below"] == pytest.approx(1.0)
    gate = next(x for x in payload["gates"] if x["key"] == "guidance_gap")
    assert g["gate"]["mass"] == gate["mass"] and g["gate"]["advisory"] is True
    for c in st["comparisons"]:
        assert c["year"] in next(t for t in facts["targets"] if t["id"] == c["target"])["years"]
    nd = [c for c in st["comparisons"] if c["target"] == "nd_ebitda"]
    assert nd and all(c["position"] in {"below", "inside", "above"} for c in nd)


# ------------------------------------------------------------------ дивиденды


@pytest.mark.needs_book
def test_the_dividends_block_is_the_ladder_and_the_expected_payout(payload, release):
    A = release.book
    div = payload["dividends"]
    assert [(r["max_leverage"], r["payout_max"]) for r in div["ladder"]] == [
        (r["max_leverage"], r["payout_max"]) for r in A["financing"]["dividend_ladder"]]
    anchor = A["facts"]["anchor"]
    lev = anchor["net_debt"] / anchor["ebitda_ltm_reported"]
    assert div["current"]["leverage_reported"] == pytest.approx(lev, abs=1e-4)
    want = next(i for i, r in enumerate(A["financing"]["dividend_ladder"])
                if r["max_leverage"] is None or lev < r["max_leverage"])
    assert div["current"]["rung"] == want
    shares = A["facts"]["shares_out_mln"]
    grid = {r["year"]: r for r in div["by_year"]["grid"]}
    first = A["financing"]["dividends_from_year"]
    assert all(r["dps"] == 0 for y, r in grid.items() if y < first)
    total = sum(c.probability for c in release.cells)
    year = first + 1
    expected = sum(c.probability * next(a["dividends"] for a in c.result.annual() if a["year"] == year)
                   for c in release.cells) / total
    assert grid[year]["dividends"] == pytest.approx(expected, abs=0.006)
    assert grid[year]["dps"] == pytest.approx(expected * 1000 / shares, abs=0.06)
    for name, result in release.named.items():
        assert [r["year"] for r in div["scenarios"][name]] == [a["year"] for a in result.annual()]


# ------------------------------------------------------------------ дисконт за управление


@pytest.mark.needs_book
def test_the_governance_block_decomposes_g_and_prints_the_850oa_scope_line(payload, release):
    from model.book import governance_components, governance_on_850oa_scope

    gov = payload["governance"]
    parts = governance_components(release.book)
    assert [c["name"] for c in gov["components"]] == [p["name"] for p in parts]
    assert all(c["signed"] == c["sign"] * c["value"] for c in gov["components"])
    assert gov["sum_signed"] == pytest.approx(gov["discount"], abs=1e-9)
    assert gov["on_850oa_scope"] == pytest.approx(governance_on_850oa_scope(release.book))
    assert gov["out_of_850oa_scope"] == [p["name"] for p in parts if not p["in_850oa_scope"]]


# ------------------------------------------------------------------ базы отчётности


@pytest.mark.needs_book
def test_the_bases_bridge_is_the_lease_between_ifrs16_and_ias17(payload, release):
    bases = payload["bases"]
    for row in bases["debt"]:
        assert row["lease_gap"] == pytest.approx(row["ifrs16"]["lease_liabilities"], abs=1e-3), row["date"]
    anchor = bases["anchor"]
    assert anchor["net_debt_ias17"] == release.book["facts"]["anchor"]["net_debt"]
    assert anchor["net_debt_ifrs16"] - anchor["net_debt_ias17"] == pytest.approx(
        anchor["lease_liabilities"], abs=2e-3)
    base = _facts("accounting_base.json")
    ltm = (base["fy"]["FY2025"]["ifrs16"]["pl"]["ebitda"]["v"]
           - base["halves"]["2025H1"]["ifrs16"]["pl"]["ebitda"]["v"]
           + base["halves"]["2026H1"]["ifrs16"]["pl"]["ebitda"]["v"])
    assert anchor["ebitda_ltm_ifrs16"] == pytest.approx(ltm, abs=1e-5)
    broken = copy.deepcopy(payload)
    broken["bases"]["debt"][-1]["lease_gap"] += 1.0
    assert any("базы" in p for p in validate(broken))


# ------------------------------------------------------------------ квартальный нау-каст


@pytest.mark.needs_book
def test_the_nowcast_is_quarterly_with_the_half_the_guidance_and_the_admission_calendar(payload, release):
    from indicators import perimeter, periods, quarterly
    from indicators.journal import MAIN_BENCHMARK

    now = payload["nowcast"]
    margin = now["margin"]
    assert now["period_unit"] == "quarter" and periods.is_quarter(now["target_quarter"])
    assert margin["period"] == now["target_quarter"]
    assert margin["half"] == periods.half_of_quarter(margin["period"])
    assert margin["expectation_se"] == margin["std_error"] > 0
    A = release.book
    # «Какая маржа 2П следует из факта 3 кв.»: строка таблицы на каждой строке
    # «что даст отчёт»; при полной персистентности маржа полугодия — маржа строки.
    implied = now["implied_half"]
    offset = A["margin"]["quarter_offset_pp"][periods.quarter_key(implied["quarter"])]
    assert [r["half_margin_row"] for r in implied["table"]] == [
        round(r["margin"], 4) for r in release.next_report]
    for r in implied["table"]:
        assert r["implied_persistent"] == pytest.approx(r["quarter_fact"] - offset, abs=2e-5)
    # Эталон гайденса: маржа 2П, которой требует годовая цель, и эталон квартала.
    g = now["guidance"]
    st = payload["strategy"]["guidance"]
    need = quarterly.guidance_half_margin(fy_margin=st["fy_margin_min"], h1_revenue=st["h1"]["revenue"],
                                          h1_ebitda=st["h1"]["ebitda"],
                                          h2_revenue=st["h2"]["revenue_model"])
    assert g["required_half_margin"] == pytest.approx(need, abs=2e-5)
    assert g["quarter_benchmark"] == pytest.approx(g["required_half_margin"] + offset, abs=2e-5)
    # Календарь допуска: первый засчитываемый квартал — первый без разрыва
    # главного эталона; все квартала до него — в списке сломанных.
    adm = next(iter(now["admission"].values()))
    main = MAIN_BENCHMARK[now["target"]]
    q, ahead = now["target_quarter"], []
    while perimeter.broken_by(q, main):
        ahead.append(q)
        q = periods.shift(q, 1)
    assert adm["first_countable"] == q
    assert [b["quarter"] for b in adm["broken_ahead"]][:len(ahead)] == ahead
    assert adm["earliest_decision"] >= adm["first_countable"]
    # Второй квартал полугодия раскрывают отчёты журнала: у 4 кв. — два события.
    reports = implied["second_quarter_reports"]
    assert [r["id"] for r in reports] == list(dict.fromkeys(
        periods.report_id(implied["second_quarter"], g_) for g_ in ("revenue", "margin")))
    # Подпись есть у каждой величины журнала и табло: витрина ключей эмитента не держит.
    titled = set(now["target_titles"])
    assert {row["target"] for row in now["journal"]} <= titled
    assert set(now["scoreboard"]) <= titled and now["target"] in titled
    # Окно даты у событий календаря: экран печатает окно, а не выдуманный день.
    cal = payload["calendar"]
    for event in [cal["next_fact"], *cal["events"]]:
        assert event["earliest"] <= event["when"] <= event["latest"], event["id"]


@pytest.mark.tact
def test_the_fourth_quarter_is_two_reports_and_the_others_one(tmp_path):
    """4 кв.: выручка — операционными результатами, маржа — годовым МСФО (D2).

    Дата — из календаря книги по имени отчёта (`periods.report_id`), без
    события — по правилу лагов с пометкой `rule`; отчёт 2 кв. закрывает и
    выручку, и маржу одним релизом."""
    from datetime import date

    from indicators import calendar, periods
    from model.payload import quarter_reports

    today = date(2026, 9, 28)
    q4 = quarter_reports("2026Q4", today)
    assert [r["gives"] for r in q4] == [["revenue"], ["margin"]]
    assert [r["id"] for r in q4] == [periods.report_id("2026Q4", "revenue"),
                                     periods.report_id("2026Q4", "margin")]
    by_id = {e.id: e for e in calendar.load()}
    for r in q4:
        event = by_id[r["id"]]
        assert r["when"] == event.when.isoformat() and r["latest"] == event.latest.isoformat()
        assert r["days"] == (event.when - today).days and r["title"] == event.title
        assert r["earliest"] <= r["when"] <= r["latest"]
    assert q4[0]["when"] < q4[1]["when"], "выручка раньше маржи"
    q2 = quarter_reports("2027Q2", today)
    assert len(q2) == 1 and q2[0]["gives"] == ["revenue", "margin"]
    # Отчёта нет в календаре — правило лагов, а не пропуск.
    far = quarter_reports("2031Q4", today)
    assert [r["precision"] for r in far] == ["rule", "rule"]
    assert [r["when"] for r in far] == [periods.fallback_report_window("2031Q4", g)[0].isoformat()
                                        for g in ("revenue", "margin")]


@pytest.mark.tact
def test_the_leverage_axis_of_the_reverse_dcf_is_printed_in_times():
    """Ось «целевой рычаг» — «1,0×», а не «100 %»: единица оси — правило суждений."""
    from model.payload import reverse_dcf_block

    rows = [dict(name=n, paths=[p], kind=k, book_value=b, value=None, range=[0.0, 2.0],
                 inside_range=False)
            for n, p, k, b in (("рычаг", "financing.leverage_target", "value", 1.0),
                               ("β", "valuation.beta_u", "value", 0.52),
                               ("ERP", "valuation.erp", "value", 0.0557),
                               ("маржа", "margin.regimes.stress.target.LT", "shift", 0.0),
                               ("темп", "revenue.segments.conv.space.mid.gross_open", "scale", 1.0))]
    A = {"market": {"price": 1.0}, "meta": {"valuation_date": "2026-09-28"},
         "valuation": {"uncertainty": {"median_draws": 200}}}
    block = reverse_dcf_block(A, rows)
    assert [r["unit"] for r in block["rows"]] == ["times", "number", "pct", "pp", "mult"]


@pytest.mark.needs_book
def test_the_release_goes_without_the_debt_register(tmp_path, monkeypatch):
    """Без реестра долга выпуск выходит: поля реестра — null с причиной, канал
    процентов — `unavailable`; контракт цел (P4b: «интерес без реестра»)."""
    import model.financing as financing
    from model.payload import REGISTER_FIELDS

    monkeypatch.setattr(financing, "REGISTER_FILE", "no_such_register.json")
    release = run_release(gates=False)
    fast = build_payload(release, with_slow=False)
    debt = fast["debt"]
    assert debt["register"]["available"] is False and debt["register"]["reason"]
    assert all(debt[k] is None for k in REGISTER_FIELDS)
    assert debt["net_debt_reported"] == release.book["facts"]["anchor"]["net_debt"]
    assert fast["nowcast"]["interest"].get("unavailable")
    assert validate(fast) == []


# ------------------------------------------------------------------ индикаторы


def _fixture_store(tmp_path, monkeypatch):
    """Все сборщики «Ленты» на фикстурах (как `tests/test_sources_lenta.py`)."""
    from indicators import sources
    from indicators.store import Store
    from tests import test_sources_lenta as T

    fix = ROOT / "tests" / "fixtures"
    routes = [
        (r"boards/TQBR/securities\.json", T.fixture("moex_quote", "quotes.json")),
        (r"securities/LENT\.json", T.fixture("moex_security", "security_LENT.json")),
        (r"q=7814148471", T.fixture("bonds", "bonds_search_7814148471.json")),
        (r"q=7826087713", T.fixture("bonds", "bonds_search_7826087713.json")),
        (r"boards/TQCB/securities\.json", T.fixture("bonds", "bonds_tqcb.json")),
        (r"DailyInfo\.asmx", T.fixture("cbr_key_rate", "key_rate.xml")),
        (r"zcyc", (fix / "ofz_in" / "zcyc_2026-09-18.json").read_bytes()),
        (r"boards/TQOB", (fix / "ofz_in" / "iss_ofz_in_2026-09-20.json").read_bytes()),
        (r"/publications/$", T.fixture("lenta_databook", "publications.html")),
        (T.DATABOOK_LINK, T._databook_xlsx(T.EXTRACT)),
    ] + T._disclosure_web(T._feed_page()).routes + T._trudvsem_web().routes
    monkeypatch.setattr(sources, "fetch", T.FakeWeb(routes))
    monkeypatch.setattr(sources, "UTC_TODAY", lambda: "2026-09-28")
    store = Store(tmp_path)
    report = sources.run_collectors(list(sources.DAILY), store)
    assert all(v.startswith("ок") for v in report.values()), report
    return store


@pytest.mark.tact
def test_every_lenta_series_has_a_human_title_a_unit_and_a_group(tmp_path, monkeypatch):
    """Каждый ряд сборщиков «Ленты» на фикстурах — с человеческим названием (не
    машинным идентификатором), единицей из списка выпуска и группой экрана."""
    from model.payload import _indicators_block

    store = _fixture_store(tmp_path, monkeypatch)
    rows = _indicators_block(store)
    ids = {s.id for s in store.all_series()}
    assert ids <= {r["id"] for r in rows}
    for r in rows:
        assert r["unit"] in INDICATOR_UNITS, r["id"]
        assert r["group"] in INDICATOR_GROUPS and r["group_title"], r["id"]
        assert r["title"] != r["id"] and "·" not in r["title"], (r["id"], r["title"])
    assert indicator_unit("lenta.bond.RU000A10CCN3.price") == "price_pct_nominal"
    assert indicator_unit("lenta.bond.RU000A10CCN3.yield") == "share"
    assert indicator_unit("lenta.salary.vacancy_chain_low") == "index"
    assert indicator_unit("lenta.salary.vacancy_chain_matched") == "count"
    assert indicator_group("moex.security.LENT.listlevel") == "disclosure"
    assert indicator_title("moex.zcyc.0.5y") == "Кривая ОФЗ, 0,5y"


@pytest.mark.tact
def test_the_tiles_are_the_lenta_set_and_a_missing_one_says_why(tmp_path, monkeypatch):
    """Плитки — котировка LENT, ключевая, кривая, облигации «О'КЕЙ», сигнал датабука,
    события раскрытия, уровень листинга, зарплатный индекс; ряда нет — строка с
    причиной, а не пустое место."""
    from indicators.store import Store
    from model.payload import _indicators_block

    assert {"moex.price.LENT", "cbr.key_rate", "moex.zcyc.10y", "lenta.databook.new_version",
            "lenta.disclosure.events", "moex.security.LENT.listlevel",
            "lenta.salary.vacancy_chain_low"} <= set(INDICATOR_TILES)
    assert is_tile("lenta.bond.RU000A10CCN3.yield") and not is_tile("lenta.bond.RU000A10CCN3.price")
    empty = _indicators_block(Store(tmp_path / "empty"))
    assert {r["id"] for r in empty} == set(INDICATOR_TILES) | {"lenta.bond.*.yield"}
    assert all(r["tile"] and r["missing"] and r["history"] == [] for r in empty)
    full = _indicators_block(_fixture_store(tmp_path / "full", monkeypatch))
    tiles = [r for r in full if r["tile"]]
    bonds = [r for r in tiles if r["id"].startswith("lenta.bond.")]
    assert len(bonds) == 4 and all(r["reference_only"] for r in bonds)
    assert not next(r for r in tiles if r["id"] == "moex.price.LENT")["reference_only"]
    salary = [r for r in tiles if r["id"].startswith("lenta.salary.")]
    assert salary and all("missing" in r for r in salary), "индекс — со второго дня обхода"
    order = [("missing" in r) for r in tiles]
    assert order == sorted(order), "плитки с рядом — перед пустыми"


# ------------------------------------------------------------------ гейты «Ленты»


@pytest.mark.tact
def test_a_security_card_change_after_the_book_is_an_advisory_gate():
    from types import SimpleNamespace

    from model.checks import ADVISORY_GATES, check_security_card

    change = dict(series="moex.security.LENT.listlevel", field="LISTLEVEL", before=3.0,
                  after=2.0, since="2026-10-01", previous_day="2026-09-30")
    live = SimpleNamespace(book_date="2026-09-18", security={"listlevel": 2.0, "changes": [change]})
    found = check_security_card(live)
    assert [f.key for f in found] == ["security_change"] and "security_change" in ADVISORY_GATES
    assert "уровень листинга 3 → 2" in found[0].message and not found[0].blocking
    live.book_date = "2026-10-02"
    assert check_security_card(live) == [], "смена до даты книги уже в книге"
    assert check_security_card(None) == []


@pytest.mark.needs_book
def test_the_corridors_cover_the_lenta_history_of_their_window(payload):
    """Коридоры гейтов — данные (E20); их основание и история компании — в выпуске,
    и годовая история окна основания лежит внутри коридора."""
    from model.checks import CORRIDOR_BASIS, CORRIDOR_GATES, corridor_history, gate_corridors

    corridors = payload["checks"]["corridors"]
    assert set(corridors) == set(CORRIDOR_GATES) == set(CORRIDOR_BASIS)
    history = corridor_history()
    for key, spec in corridors.items():
        want = gate_corridors()[key]
        assert spec["corridor"] == (dict(want) if isinstance(want, dict) else list(want))
        assert spec["basis"] == CORRIDOR_BASIS[key]
        if key in history:
            assert history[key]["window_outside"] == [], (key, history[key])
            assert spec["history"]["annual"], key
    assert corridors["ev_ebitda"]["history"]["current"] is not None
