# -*- coding: utf-8 -*-
"""Книга допущений «Ленты» 1.0 в ядре: загрузка, записи миров, связь с фактами и листами.

Книга (`data/assumptions/assumptions.yaml`) собрана из шаблона и записи миров;
тесты проверяют то, что книга обязана держать как записанная:

* грузится ядром без `BookError` (закрытая схема, все правила книги), считается
  быстрым путём выпуска, гейты объяснены, инвариантов нет;
* запись миров — побайтово (sha256 хранимых байтов, `.gitattributes -text`),
  рецепт её воспроизводит, собранная книга = шаблон + запись;
* числа книги = фактам (`data/facts`) и выходам листов (сеть, цели режимов,
  capex) — одно число в одном месте;
* каждая ось полосы, строка чувствительности и обратного DCF исполнима ядром
  на своих концах (E11).
"""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from model.book import BOOK_DIR, load_book, validate_book

ROOT = Path(__file__).resolve().parents[1]
FACTS = ROOT / "data" / "facts"
EVIDENCE = BOOK_DIR / "evidence" / "book-1.0"


@pytest.fixture(scope="module")
def A() -> dict:
    return load_book()


def _facts(name: str) -> dict:
    return json.loads((FACTS / name).read_text(encoding="utf-8"))


def _evidence(rel: str) -> dict:
    return json.loads((EVIDENCE / rel).read_text(encoding="utf-8"))


# ================================================================ книга и миры


def test_the_book_loads_and_is_version_one(A):
    assert A["meta"]["version"] == "1.0"
    assert A["meta"]["company"] == {"name": "Лента", "ticker": "LENT"}
    assert A["valuation"]["headline"]["method"] == "intrinsic"


def test_the_world_record_is_stored_byte_for_byte():
    """D6, решение ведущего F35: `worlds_source.json` — sha256 7296567d… на ХРАНИМЫХ
    байтах (не после нормализации переводов строк), `.csv` — тот же канон записи;
    `.gitattributes` снимает с обоих преобразование текста."""
    data = (BOOK_DIR / "worlds_source.json").read_bytes()
    assert hashlib.sha256(data).hexdigest().startswith("7296567d")
    assert hashlib.sha256((BOOK_DIR / "worlds_source.csv").read_bytes()).hexdigest().startswith("2faae142")
    # Канон записан с переводами строк CRLF — поэтому и нужен `-text`: git с
    # `text=auto eol=lf` превратил бы их в LF и сменил sha256.
    assert b"\r\n" in data
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert "data/assumptions/worlds_source.* -text" in attributes
    done = subprocess.run(["git", "check-attr", "text", "--", "data/assumptions/worlds_source.json",
                           "data/assumptions/worlds_source.csv"], cwd=str(ROOT),
                          capture_output=True, text=True)
    assert done.returncode == 0 and done.stdout.count(": text: unset") == 2, done.stdout


def test_the_worlds_recipe_reproduces_the_record():
    """F34: рецепт и входы — без чисел оценки книги-источника, но функционально те
    же: `worlds_recipe.py --check` воспроизводит запись (63 строки)."""
    done = subprocess.run([sys.executable, "-B", "worlds_recipe.py", "--check"], cwd=str(BOOK_DIR),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert done.returncode == 0, done.stdout[-800:] + done.stderr[-800:]
    assert "воспроизведено: 63" in done.stdout


def test_the_built_book_is_the_template_plus_the_world_record(tmp_path):
    """`assumptions.yaml` и `.json` — выход сборщика на шаблоне и записи миров."""
    for name in ("build_assumptions.py", "assumptions_template.yaml", "worlds_source.json"):
        (tmp_path / name).write_bytes((BOOK_DIR / name).read_bytes())
    done = subprocess.run([sys.executable, "-B", "build_assumptions.py"], cwd=str(tmp_path),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert done.returncode == 0, done.stderr[-800:]
    for name in ("assumptions.yaml", "assumptions.json"):
        assert (tmp_path / name).read_text(encoding="utf-8") == (BOOK_DIR / name).read_text(encoding="utf-8"), name


def test_the_world_weights_and_their_axis_stay_inside_the_record_ranges(A):
    """Решение ведущего F36: веса миров книги = записи; концы строки чувствительности
    весов миров — внутри `probability_ranges` записи (25–40 / 40–50 / 15–25)."""
    record = json.loads((BOOK_DIR / "worlds_source.json").read_text(encoding="utf-8"))
    assert A["joint"]["world_prob"] == record["probabilities"]
    ranges = record["probability_ranges"]
    [row] = [s for s in A["sensitivities"] if s.get("path") == "joint.world_prob"]
    for end in ("low", "high"):
        for world, value in row[end].items():
            lo, hi = ranges[world]
            assert lo - 1e-12 <= value <= hi + 1e-12, (end, world, value)
        assert sum(row[end].values()) == pytest.approx(1.0)


# ============================================================== квартальный слой


def test_the_quarter_offsets_net_to_zero_within_each_half(A):
    """Решение ведущего 21: доли кварталов — внутри полугодия, поправки маржи с
    весами выручки обнуляются точно (ядро проверяет до 1e-12)."""
    share, offset = A["revenue"]["quarter_share"], A["margin"]["quarter_offset_pp"]
    for a, b in (("Q1", "Q2"), ("Q3", "Q4")):
        assert share[a] + share[b] == pytest.approx(1.0, abs=1e-12)
        assert abs(share[a] * offset[a] + share[b] * offset[b]) < 1e-15
    assert (offset["Q1"], offset["Q3"]) == (-0.00754, -0.00062)
    nwc = A["nwc"]["quarter_share"]
    assert nwc == {"Q1": 1.18, "Q2": -0.18, "Q3": -0.123, "Q4": 1.123}


# ======================================================= книга ↔ факты и листы


def test_the_anchor_facts_are_the_fact_base(A):
    """Одно число — одно место (решение ведущего A12): EBITDA LTM, выручка LTM, D&A
    якоря — канон проформы фактов; отчётная LTM — рядом; акции, линии, долг."""
    anchor = _facts("anchor.json")
    F = A["facts"]
    assert F["anchor"]["ebitda_ltm"] == anchor["ebitda_ltm"]["pro_forma"] == 88.235
    assert F["anchor"]["ebitda_ltm_reported"] == anchor["ebitda_ltm"]["reported"]
    assert F["anchor"]["revenue_ltm"] == pytest.approx(anchor["revenue_ltm"]["pro_forma"], abs=1e-9)
    assert F["anchor"]["da_pre16"] == anchor["da_pre16"]["pro_forma"]
    assert F["anchor"]["margin_pro_forma"] == anchor["margin_pro_forma"]
    assert F["revenue"]["2026H1"] == anchor["revenue"]["pro_forma"]
    assert F["reported"]["revenue"]["2026H1"] == anchor["revenue"]["reported"]
    assert F["guidance"]["ebitda_margin_min"] == anchor["guidance_2026"]["ebitda_margin_min"]
    assert F["shares_out_mln"] == _facts("shares.json")["outstanding"]["shares_out_mln"]
    debt = _facts("debt.json")
    assert F["undrawn_credit_lines"] == debt["undrawn_credit_lines"]["v"]
    assert F["anchor"]["net_debt"] == pytest.approx(debt["net_debt_pre16"]["v"], abs=1e-9)
    assert F["anchor"]["cash"] == pytest.approx(debt["cash"]["v"], abs=1e-9)


def test_the_bridge_counts_each_claim_once_and_pairs_the_indemnification_asset(A):
    """Решения ведущего A1, A2, A6: ограниченных средств нет — компенсирующий актив
    в ОК с парой (в мосте haircut 1); НДУ без «Реми» + доля «Реми» = строке НДУ
    баланса; каждое требование — одна строка, выплата выводится из неё.
    Долгосрочная часть денежного LTIP 2,000523 — не строка моста, а возобновляемый
    «поплавок» в ОК (решение ведущего по аудиту 30.09.2026, п. 22): ОК якоря =
    ОК баланса без неё минус она, выплаты 2028H1 в мосте нет."""
    from model.book import bridge_items
    from model.core import settlement_payments

    balance = _facts("bridge_balance.json")
    items = {i.id: i for i in bridge_items(A)}
    assert len(items) == len(A["bridge"]["items"])
    assert balance["restricted_cash"]["amount_2026h1"] == 0.0
    asset = items["remi_indemnification_asset"]
    assert asset.kind == "asset" and asset.haircut == 1.0
    assert asset.amount == pytest.approx(balance["indemnification_asset"]["amount"], abs=1e-9)
    nci = balance["nci"]
    assert items["nci_ex_remi"].amount + nci["remi_track"]["implied_2026h1"] == pytest.approx(
        nci["balance_2026h1"], abs=1e-9)
    payments = settlement_payments(A)
    assert payments["2027H1"] == pytest.approx(5.704364, abs=1e-12)            # пут «Реми»
    assert payments["2026H2"] == pytest.approx(4.070211 - 3.33989, abs=1e-12)  # «ОБИ» − займы выданные
    assert "2028H1" not in payments and "ltip_long_term" not in items           # LTIP — в ОК
    assert A["nwc"]["anchor_level"] == pytest.approx(-10.9559 - 2.000523, abs=5e-5)


def test_the_rate_baskets_cover_the_anchor_debt(A):
    """Решение ведущего A10: корзины — доли всего основного долга 141,923; на якоре
    покрывают его целиком; фиксированная часть = 0,7393 реестра."""
    baskets = A["financing"]["rate_baskets"]
    assert sum(b["share"] for b in baskets) == pytest.approx(1.0, abs=1e-6)
    fixed = sum(b["share"] for b in baskets if "fixed" in b["rate"])
    assert fixed == pytest.approx(_facts("debt.json")["shares_of_principal"]["fixed_incl_bonds"], abs=1e-3)
    register = _facts("debt_register.json")
    assert sum(t["principal"] for t in register["tranches"]) == pytest.approx(
        _facts("debt.json")["reported"]["principal"], abs=1e-6)


def test_the_central_cell_revenue_is_the_network_sheet(A):
    """E1: центральная клетка книги (H × частичная × base × mid) даёт выручку
    центральной клетки листа «Сеть» (`revsim.py`): 2П2026 777,2; 2027 1 675,8; 2028
    1 881,6 (плотности супер 0,95 и дрогери 0,67 — аудит 30.09.2026, п. 17) — те же правила сегментов (LFL + поправка, опт — LFL + прибавка, DIY — ИПЦ
    мира, эффективная площадь с закрытиями истории)."""
    from model.book import named_cells
    from model.core import run_cell

    sheet = _evidence("network/checks_out.json")["base_cell"]["total"]
    rows = run_cell(A, named_cells(A)["base"]).rows
    assert len(rows) == 21
    for row in rows:
        # Лист печатает выручку с тремя знаками, книга пишет историю площади и
        # когорты с двумя: расхождение ≤ 0,013 млрд на 2036H2 (7·10⁻⁶).
        assert row.revenue == pytest.approx(sheet[row.period], rel=2e-5), row.period
    assert rows[0].revenue == pytest.approx(777.2, abs=0.05)
    assert rows[1].revenue + rows[2].revenue == pytest.approx(1675.8, abs=0.1)
    assert rows[3].revenue + rows[4].revenue == pytest.approx(1881.6, abs=0.1)


def test_the_regime_targets_and_capex_keys_are_the_assembly_tools(A):
    """Цели режимов (решение ведущего C17) и ключи поддерживающего capex — выходы
    инструментов сведения листов (`evidence/book-1.0/assembly/`)."""
    targets = _evidence("assembly/regime_targets_out.json")["targets"]
    for regime, spec in A["margin"]["regimes"].items():
        for key, value in targets[regime].items():
            assert spec["target"][key] == pytest.approx(value, abs=1e-12), (regime, key)
    capex = _evidence("assembly/rescale_capex_out.json")
    for level, path in A["capex"]["maintenance_pct"].items():
        for key, value in capex["maintenance_pct"][level].items():
            assert path[key] == pytest.approx(value, abs=1.5e-4), (level, key)


def test_the_anchor_margin_is_one_and_its_transitional_deviation_is_neutral(A):
    """Маржа якоря — одна (проформа 5,74 %, se 0,10 п.п., в observations не
    дублируется); ключ "2026H1" целей равен у всех режимов, поэтому наблюдение
    вероятностей не сдвигает, а переходный убыток «Дом Ленты» — отклонение."""
    from model.core import margin_season
    from model.grid import regime_table

    anchor = A["facts"]["anchor"]
    assert (anchor["margin_pro_forma"], anchor["margin_pro_forma_se"]) == (0.0574, 0.0010)
    assert not A["joint"]["regime_update"]["observations"]
    keys = {spec["target"]["2026H1"] for spec in A["margin"]["regimes"].values()}
    assert len(keys) == 1
    deviation = anchor["margin_pro_forma"] - keys.pop() - margin_season(A, "2026H1")
    assert -0.0035 < deviation < -0.0030
    for world, row in regime_table(A).items():
        assert row == pytest.approx(A["joint"]["regime_given_world"][world], abs=1e-12)


# ========================================================= исполнимость осей


def _ends(A: dict):
    from model.uncertainty import axis_overrides

    for group in ("sensitivities", "axes"):
        rows = A["sensitivities"] if group == "sensitivities" else A["valuation"]["uncertainty"]["axes"]
        for row in rows:
            for side in (-1.0, 1.0):
                yield f"{group}:{row['name']}:{side:+.0f}", axis_overrides(A, row, side)


def test_every_axis_and_sensitivity_end_is_executable(A):
    """E11: у каждой оси полосы и строки чувствительности оба конца — подмены,
    которые ядро принимает (книга после подмены валидна) и считает (клетка)."""
    from model.book import Cell
    from model.core import run_cell
    from model.engine import with_overrides

    cell = None
    for name, overrides in _ends(A):
        trial = with_overrides(A, overrides)
        validate_book(trial)
        cell = cell or Cell.build(A, "H", "partial", "base")
        assert run_cell(trial, cell).ev == run_cell(trial, cell).ev, name


def test_every_reverse_dcf_row_moves_the_book(A):
    """Строки обратного DCF исполнимы: концы отрезка поиска — валидные подмены."""
    from model.engine import with_overrides
    from model.uncertainty import axis_value

    for row in A["reverse_dcf"]:
        for v in row["search"]:
            validate_book(with_overrides(A, {p: axis_value(row["kind"], v) for p in row["paths"]}))


# ============================================================ выпуск на книге


def test_the_release_runs_on_the_book_with_explained_gates(A):
    """Быстрый путь: выпуск на книге — сетка, слои, гейты; инвариантов нет, у
    каждого сработавшего гейта есть объяснение; `guidance_gap` — совещательный,
    срабатывает на всей сетке (гайденс не даёт ни один режим, решение C20)."""
    import datetime as dt

    from model.checks import load_gate_explanations, summarize_gates
    from model.engine import run_release

    release = run_release(copy.deepcopy(A), gates=True)
    assert not release.blocking, [f.key for f in release.blocking]
    explained = load_gate_explanations(today=dt.date(2026, 10, 1))
    mass = {c.cell.key: c.probability for c in release.cells}
    summary = {g.key: g for g in summarize_gates(release.findings, mass | {"выпуск целиком": 1.0},
                                                 explained, today=dt.date(2026, 10, 1))}
    for key, gate in summary.items():
        assert gate.explained, key
    assert summary["guidance_gap"].advisory
    assert summary["guidance_gap"].mass == pytest.approx(1.0, abs=1e-9)


def test_the_fast_path_prints_the_release_and_writes_nothing(A, tmp_path, monkeypatch):
    """`python -B -m model.book_results --fast` — проба «книга считается ядром»:
    печатает слои, точку, сценарии и гейты, инвариантов нет, файлов не пишет."""
    from model import book_results

    text = book_results.fast_summary(copy.deepcopy(A))
    assert "книга 1.0" in text and "точка: низ" in text
    assert "инварианты: чисто" in text
    monkeypatch.chdir(tmp_path)
    assert book_results.main(["--fast", "--out", str(tmp_path)]) == 0
    assert list(tmp_path.iterdir()) == []


def test_the_peers_block_reads_the_fact_file_and_reproduces_the_sheet(A):
    """Витрина читает аналоги из `data/facts/peers.json` (схема листа фактов —
    список строк): по цене legal close 18.09.2026 её EV/EBITDA совпадает с
    мультипликатором листа, ряд цены — `moex.price.<тикер>` сборщика котировок."""
    from types import SimpleNamespace

    from model.payload import peers_block

    sheet = _facts("peers.json")
    close = {p["ticker"]: p["price_legal_close"] for p in sheet["peers"]}
    asked = []

    class Store:
        def load(self, series_id):
            asked.append(series_id)
            ticker = series_id.removeprefix("moex.price.")
            point = SimpleNamespace(value=close[ticker], period="2026-09-18")
            return SimpleNamespace(latest=lambda: point)

    block = peers_block(copy.deepcopy(A), store=Store())
    rows = {r["key"]: r for r in block["rows"]}
    assert sorted(asked) == sorted(f"moex.price.{t}" for t in close)
    for p in sheet["peers"]:
        row = rows[p["ticker"]]
        assert row["ev"] == pytest.approx(p["ev"], abs=0.1), p["ticker"]
        assert row["ev_ebitda"] == pytest.approx(p["ev_ebitda"], abs=0.006), p["ticker"]
        assert row["source"] == p["src"]
    assert block["as_of"] == A["meta"]["facts_date"]


def test_the_history_block_reads_the_accounting_base():
    """История витрины — из `accounting_base.json` листа фактов: маржа и выручка
    IAS 17 по полугодиям с 2018H1, площадь на конец 1П и года; последняя точка —
    отчётное 1П2026 (не проформа)."""
    from model.payload import _history_block

    base = _facts("accounting_base.json")
    pl = base["halves"]["2026H1"]["ias17"]["pl"]
    h = _history_block()
    assert min(h["margin"]) == "2018H1" and max(h["margin"]) == "2026H1"
    assert list(h["margin"]) == list(h["revenue"]) == sorted(h["margin"])
    assert h["margin"]["2026H1"] == pytest.approx(pl["ebitda"]["v"] / pl["revenue"]["v"], abs=5e-6)
    assert h["revenue"]["2026H1"] == pytest.approx(pl["revenue"]["v"], abs=0.05)
    assert h["area"]["2026H1"] == pytest.approx(base["operating"]["area_sqm"]["2026H1"]["total"]["v"] / 1000, abs=0.05)
    assert h["vintage"] and "винтаж" in h["pit_warning"]


# ================================================== решения ведущего F1 на книге


def test_the_scenario_weights_are_the_grid_weights(A):
    """Веса именованных сценариев книги (`weights`) = выведенным из сетки ядром
    (`model.grid.weights_from_grid`: клетка → ближайший по EV сценарий) на дату
    книги, до записи в 4 знака (решение ведущего F1: предварительные 0,69 /
    0,20 / 0,11 расходились с сеткой 0,59 / 0,21 / 0,20)."""
    from model.engine import run_release

    grid = run_release(copy.deepcopy(A), gates=False).named_weights
    assert set(grid) == set(A["weights"])
    for name, weight in A["weights"].items():
        assert weight == pytest.approx(grid[name], abs=5e-5), (name, weight, grid[name])
    assert sum(A["weights"].values()) == pytest.approx(1.0, abs=1e-9)


def test_the_anchor_fcfe_is_the_cash_flow_of_the_half(A):
    """`facts.anchor.fcfe_ytd` (годовое правило лестницы) — FCFE 1П2026 в
    определении ядра из отчёта о движении денег IAS 17 листа фактов: чистый ОДП
    (после процентов и налога) + capex (ОС, НМА, права аренды) + продажа ОС;
    покупки бизнеса и займы выданные — не FCFE (M&A и строки моста)."""
    cf = _facts("accounting_base.json")["halves"][A["facts"]["anchor"]["period"]]["ias17"]["cf"]
    want = sum(cf[k]["v"] for k in ("net_ocf", "capex_ppe", "capex_intangibles",
                                    "capex_leasehold_rights", "proceeds_ppe",
                                    "proceeds_disposals_lr"))
    assert A["facts"]["anchor"]["fcfe_ytd"] == pytest.approx(want, abs=1e-9)
    assert cf["acquisitions_net"]["v"] < 0 and cf["loans_given_net"]["v"] < 0


def test_the_annual_ladder_neither_pays_the_seasonal_inflow_nor_ratchets(A):
    """Книга 1.0 платит лестницу по году (`dividend_timing: annual_next_h1`):
    во 2П выплат нет (сезонный приток ОК 2П не уходит акционерам полугодием),
    рычаг конца года не ползёт: за 2029–2036 гг. в каждой клетке сетки он не
    выше цели L, и средние первых и последних четырёх лет расходятся не больше
    чем на 0,15×. «Пила» внутри этих границ — правило ступеней, а не храповик:
    при L 1,47 (аудит 30.09.2026, п. 14) конец года стоит у границы ступени
    1,0×; ниже неё выплата без потолка поднимает долг к L, выше — потолок
    100 % FCF, и рычаг снижается ростом EBITDA (амплитуда до ≈0,4×, тренд
    −0,13…+0,11×). Контроль — правило полугодия на той же книге: рычаг конца
    года базового сценария растёт, уходит выше L и к 2036 г. выше годового на
    0,5× и больше (прогон при L 1,47: 1,49 → 1,85× против 1,04×)."""
    from model.book import all_cells
    from model.core import run_cell
    from model.engine import run_release

    assert A["financing"]["dividend_timing"] == "annual_next_h1"
    last = int(A["meta"]["last_period"][:4])
    for cell in all_cells(A):
        result = run_cell(A, cell)
        assert all(r.dividends == 0.0 for r in result.rows if r.half == 2), cell.key
        ends = {a["year"]: a["leverage"] for a in result.annual()}
        span = [ends[y] for y in range(last - 7, last + 1)]
        assert max(span) <= A["financing"]["leverage_target"], (cell.key, span)
        assert abs(sum(span[4:]) - sum(span[:4])) / 4 <= 0.15, (cell.key, span)
    halves = copy.deepcopy(A)
    del halves["financing"]["dividend_timing"]
    del halves["facts"]["anchor"]["fcfe_ytd"]           # правило полугодия его не читает
    base_annual = {a["year"]: a["leverage"] for a in run_release(copy.deepcopy(A), gates=False)
                   .named["base"].annual()}
    base_halves = {a["year"]: a["leverage"] for a in run_release(halves, gates=False)
                   .named["base"].annual()}
    assert base_halves[last] - base_annual[last] >= 0.5, (base_halves[last], base_annual[last])
    assert base_halves[last] > base_halves[last - 7] + 0.3
    assert base_halves[last] > A["financing"]["leverage_target"]
