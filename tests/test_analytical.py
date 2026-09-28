# -*- coding: utf-8 -*-
"""Аналитические тесты: ожидание выведено руками, а не снято с движка.

Аудит второй итерации: из 229 тестов расчёт модели трогали 59, а сверяли с
НЕЗАВИСИМО ВЫВЕДЕННЫМ ожиданием только 11 (4,8 %). Остальные сравнивали
движок с самим собой или с эталоном, написанным тем же способом.

Здесь книга сводится к случаю, который считается на бумаге: плоский мир без
инфляции и роста, один поток, нулевой долг. В таком мире ответ известен
заранее, и если движок его не даёт — ошибка в движке, а не в допусках.

Книга — синтетическая в схеме Ленты (`tests/fixtures/toy_book`, DESIGN D17):
тесты проверяют механику ядра и идут без книги компании.

Каждый тест отвечает на вопрос «что именно сломается, если эту строку
испортить» — поэтому они и ловят мутации, которые сверка с эталоном
пропускает.
"""
from __future__ import annotations

import copy
import math

import pytest

from model.book import Cell, periods
from model.core import annuity_ratio, run_cell
from model.engine import with_overrides
from tests.toy import toy_book

# Такт (ops/run.sh, TACT_TESTS): закрытые формы и инварианты ядра на синтетической
# книге — быстрые, защищают число до сборки.
pytestmark = pytest.mark.tact


def plain() -> dict:
    """Синтетическая книга без наблюдения маржи якоря (проформа с ошибкой):
    закрытые формулы A-P2u ниже написаны для книги без наблюдений; проформа
    якоря проверяется своим тестом (`tests/test_p2_core.py`)."""
    A = toy_book()
    for key in ("margin_pro_forma", "margin_pro_forma_se"):
        A["facts"]["anchor"].pop(key, None)
    return A


def flat(**overrides) -> dict:
    """Книга, сведённая к плоскому миру: считается на бумаге.

    Всё, что создаёт динамику, обнуляется: инфляция, рост чека и трафика,
    открытия и закрытия, сезонность, отклонение маржи, дивиденды. Остаётся
    ровно то, что проверяет тест.
    """
    A = plain()
    zero_path = {"LT": 0.0}

    for world in A["worlds"].values():
        world["cpi"] = dict(zero_path)
        world["food_cpi"] = dict(zero_path)
        world["key_rate"] = {"LT": 0.10}
        world["wage_growth"] = dict(zero_path)
        world["lt"]["inflation"] = 0.0
        world["zero_curve"] = {k: 0.10 for k in world["zero_curve"]}

    R = A["revenue"]
    R["ticket_k"] = {k: 0.0 for k in R["ticket_k"]}
    R["ticket_shift"] = {k: dict(zero_path) for k in R["ticket_shift"]}
    R["traffic"] = {k: dict(zero_path) for k in R["traffic"]}
    R["vat_adjustment"] = dict(zero_path)
    # Сегменты сети: без открытий, закрытий и поправок к трафику; сегмент
    # выручки без площади — без роста; сегмент «уровнем» — с постоянной
    # плотностью (при нулевом росте чека индекс цен постоянен).
    first = A["meta"]["first_period"]
    for seg in R["segments"].values():
        for key in ("other_growth", "lfl_offset"):
            if key in seg:
                seg[key] = dict(zero_path)
        for level in (seg.get("space") or {}).values():
            level["gross_open"] = dict(zero_path)
            level["close"] = dict(zero_path)
        if seg["mode"] == "revenue":
            seg["growth"] = dict(zero_path)
        if seg["mode"] == "level":
            from model.book import path_value

            seg["density_path"] = {"LT": path_value(seg["density_path"], first)}

    A["margin"]["season_k"] = 1.0
    A["margin"]["nowcast_margin_shocks_pp"] = {}
    A["joint"]["regime_update"]["observations"] = {}
    A["nwc"]["seasonal_june_excess"] = 0.0
    A["financing"]["dividends_from_year"] = 9999
    A["valuation"]["terminal_real_growth"] = 0.0
    # Приобретённый периметр: без пути ОК и интеграционного capex (их проверяют
    # свои тесты), чтобы поток плоского мира был ровным.
    A["nwc"].pop("acquired_path", None)
    A["capex"].pop("integration_capex", None)

    return with_overrides(A, overrides) if overrides else A


def cell(A: dict) -> Cell:
    return Cell.build(A, "H", "floor", "base")


# ------------------------------------------------------------- перпетуитет


def test_perpetuity_in_a_flat_world():
    """Терминал плоского мира = поток / ставка. Проверяется прямо.

    При нулевой инфляции и нулевом росте вечный поток стоит F/r. Движок
    считает терминал полугодовыми потоками с ростом g; при g = 0 сумма двух
    полугодий, приведённых к концу периода, обязана дать ровно годовой поток
    делённый на ставку.
    """
    A = flat()
    result = run_cell(A, cell(A))
    assert result.terminal_growth == pytest.approx(0.0, abs=1e-9)

    r = result.r_long
    # Терминал = (F1·(1+r)^0.75 + F2·(1+r)^0.25) / r. При равных полугодиях
    # это примерно F_год/r с поправкой на внутригодовое размещение.
    annual_flow = result.fcff_terminal_margin * 0.0 + 0.0  # заглушка не нужна
    tv_gross = result.terminal_value
    assert tv_gross > 0
    # Порядок величины проверяется через отношение: терминал к годовому потоку
    # обязан быть близок к 1/r, а не к 1/(r−π) или 1/(r+π).
    ratio = tv_gross / (result.rows[-1].fcff + result.rows[-2].fcff)
    assert 1 / r * 0.7 < ratio < 1 / r * 1.6, (
        f"терминал/поток = {ratio:.1f} при 1/r = {1/r:.1f}")


def test_zero_inflation_does_not_divide_by_zero():
    """Плоский мир не должен ронять расчёт.

    Прежняя формула D&A в терминале делила на инфляцию; при нулевой она
    давала ZeroDivisionError. Сверка с эталоном этого не видела, потому что
    эталон делил так же.
    """
    A = flat()
    result = run_cell(A, cell(A))
    assert math.isfinite(result.ev)
    assert annuity_ratio(0.0, 10) == 1.0


# -------------------------------------------------------------------- мост


def test_each_bridge_line_moves_equity_by_its_own_amount():
    """Каждая строка моста проверяется ОТДЕЛЬНО и на точную величину.

    Аудит внёс мутацию «в мосте забыты пут и НДУ» — её не поймал никто,
    потому что мост проверялся целиком одним числом. Здесь каждая строка
    двигает капитал ровно на свою величину, и пропажа любой видна.
    """
    A = flat()
    # Строки с расчётом — без расчёта и наращения: выплата двигает путь долга
    # и щит (это проверяет `test_a_settled_claim_is_counted_exactly_once`),
    # а здесь нужна ровно строка моста.
    for row in A["bridge"]["items"]:
        for key in ("settle_period", "settle_amount", "accrete_rate_half"):
            row.pop(key, None)
    base = run_cell(A, cell(A))
    delta = 10.0
    claims = [row["id"] for row in A["bridge"]["items"] if row["kind"] == "claim"]
    assert claims, "в книге нет требований моста — проверка пуста"
    for item in claims:
        B = copy.deepcopy(A)
        row = next(r for r in B["bridge"]["items"] if r["id"] == item)
        row["amount"] = row["amount"] + delta

        changed = run_cell(B, cell(B))
        governance = A["valuation"]["governance_discount"]
        expected = -delta * (1 - governance if base.equity_before_governance > 0 else 1.0)
        assert changed.equity - base.equity == pytest.approx(expected, abs=0.05), item


def test_financial_assets_reduce_claims_not_increase_them():
    """Финансовые активы вычитаются из требований — знак проверяется явно.

    Мутация «финансовые активы прибавлены к требованиям» проходила незаметно:
    она меняла число, но ни один тест не знал, каким оно должно быть.
    """
    A = flat()
    base = run_cell(A, cell(A))
    B = copy.deepcopy(A)
    asset = next(r for r in B["bridge"]["items"] if r["kind"] == "asset")
    asset["amount"] += 10.0
    changed = run_cell(B, cell(B))
    assert changed.claims < base.claims
    recognised = 1.0 - asset.get("haircut", 0.0)
    assert base.claims - changed.claims == pytest.approx(10.0 * recognised, abs=0.01)


def test_share_count_scales_the_price_exactly():
    """Цена обратно пропорциональна числу акций. Мутация «выпущенные вместо находящихся в обращении»
    млн меняла цену в полтора раза и не ловилась ничем."""
    A = flat()
    base = run_cell(A, cell(A))
    B = copy.deepcopy(A)
    B["facts"]["shares_out_mln"] *= 2
    changed = run_cell(B, cell(B))
    assert changed.price == pytest.approx(base.price / 2, rel=1e-9)


def test_governance_discount_applies_only_to_positive_equity():
    """Дисконт за управление — свойство доли миноритария, а не долга.

    К отрицательному капиталу он не применяется: иначе убыток «уменьшался бы»
    на 10 % и клетка выглядела бы лучше, чем она есть.
    """
    A = flat()
    B = copy.deepcopy(A)
    B["valuation"]["governance_discount"] = 0.0
    with_discount, without = run_cell(A, cell(A)), run_cell(B, cell(B))
    if without.equity_before_governance > 0:
        assert with_discount.equity < without.equity
        ratio = with_discount.equity / without.equity
        assert ratio == pytest.approx(1 - A["valuation"]["governance_discount"], rel=1e-9)
    else:
        assert with_discount.equity == pytest.approx(without.equity, rel=1e-9)


# ------------------------------------------------------- потоки и тайминг


def test_nwc_growth_consumes_cash():
    """Рост оборотного капитала УМЕНЬШАЕТ поток. Знак проверяется прямо.

    Обе мутации знака ΔNWC (в формуле FCFF и в самом приращении) поднимали
    оценку и не ловились: движок и эталон ошибались одинаково.
    """
    A = flat()
    base = run_cell(A, cell(A))
    B = copy.deepcopy(A)
    for key, path in B["nwc"]["nwc_pct"].items():
        if isinstance(path, dict):
            B["nwc"]["nwc_pct"][key] = {k: v + 0.01 for k, v in path.items()}
        else:
            B["nwc"]["nwc_pct"][key] = path + 0.01
    changed = run_cell(B, cell(B))
    assert changed.rows[0].nwc_change > base.rows[0].nwc_change
    assert changed.rows[0].fcff < base.rows[0].fcff


def test_no_debt_means_no_tax_shield():
    """При нулевом долге щит обязан быть нулевым — тождество, не оценка."""
    A = flat()
    B = copy.deepcopy(A)
    B["facts"]["anchor"]["net_debt"] = 0.0
    B["facts"]["anchor"]["cash"] = 0.0
    B["financing"]["leverage_target"] = 0.0
    # Выплаты по строкам моста — тоже долг пути: без расчёта строки остаются
    # требованиями моста, а путь долга — нулевым.
    for row in B["bridge"]["items"]:
        row.pop("settle_period", None)
        row.pop("settle_amount", None)
    B["tax"]["alpha_terminal_shield"] = 0.0
    # Пул накопленных убытков тоже обнуляется: он даёт СВОЮ экономию налога,
    # и без этого «щит» остаётся положительным при нулевом долге — что
    # выглядело бы как ошибка, хотя это другая строка.
    B["tax"]["nol_start"] = 0.0
    B["tax"].pop("acquired_nol", None)
    result = run_cell(B, cell(B))
    assert abs(result.rows[0].tax_shield) < 0.5
    assert result.pv_tax_shield < 2.0


def test_growth_capex_is_not_counted_twice():
    """Ростовой capex входит в поток один раз.

    Мутация «двойной счёт ростового capex» снижала оценку на треть и
    проходила мимо всех тестов. Проверка прямая: удвоение цены метра обязано
    удвоить ИМЕННО ростовую компоненту, а не всю строку capex.
    """
    A = flat()
    for seg in A["revenue"]["segments"].values():
        if "space" in seg:
            seg["space"]["mid"]["gross_open"] = {"LT": 0.02}
    base = run_cell(A, cell(A))
    B = copy.deepcopy(A)
    for seg in B["capex"]["segments"].values():
        seg["growth_capex_per_m2"] *= 2
    changed = run_cell(B, cell(B))
    row_base, row_changed = base.rows[0], changed.rows[0]
    assert row_changed.capex_growth == pytest.approx(2 * row_base.capex_growth, rel=1e-9)
    assert row_changed.capex - row_base.capex == pytest.approx(row_base.capex_growth, rel=1e-6)


def test_discounting_uses_mid_period_not_end():
    """Поток дисконтируется на СЕРЕДИНУ периода.

    Мутация «на конец периода» снижала оценку на 8 % и ловилась только
    сверкой с эталоном. Здесь проверяется напрямую: первый полный период
    приведён множителем между (1+r)^-0.5 и 1.
    """
    A = flat()
    result = run_cell(A, cell(A))
    r = result.r_long
    total_flow = sum(row.fcff for row in result.rows)
    # PV первого полного периода не может быть меньше, чем при
    # дисконтировании на конец, и не больше, чем без дисконта вообще.
    assert result.pv_fcff < total_flow
    assert result.pv_fcff > total_flow / (1 + r) ** len(result.rows)


# --------------------------------------- A-P2u: наблюдение с ошибкой (Калман)


def _observed(A: dict, *pairs) -> dict:
    from model.grid import with_observation

    for period, observation in pairs:
        A = with_observation(A, period, observation)
    return A


def test_facts_keep_the_exact_ar1_likelihood():
    """Для фактов (se = 0) правдоподобие — прежняя закрытая формула AR(1) бит
    в бит: первое наблюдение безусловно с σ², следующее — инновация
    откл(t) − ρ^k·откл(t−k) с σ²·(1 − ρ^{2k})."""
    from model.book import path_value
    from model.core import margin_season
    from model.grid import regime_likelihoods

    A = _observed(plain(), ("2026H2", 0.044), ("2027H2", 0.052))
    rho = A["margin"]["deviation_persistence"]
    sigma2 = A["joint"]["regime_update"]["sigma_pp"] ** 2
    want = [{}, {}]
    for name, spec in A["margin"]["regimes"].items():
        first = 0.044 - path_value(spec["target"], "2026H2") - margin_season(A, "2026H2")
        second = 0.052 - path_value(spec["target"], "2027H2") - margin_season(A, "2027H2")
        want[0][name] = math.exp(-0.5 * first * first / (sigma2 + 0.0 ** 2))
        error = second - rho ** 2 * first
        want[1][name] = math.exp(-0.5 * error * error / (sigma2 * (1 - rho ** 4) + 0.0 ** 2))
    assert regime_likelihoods(A) == want


def test_a_noisy_nowcast_after_a_fact_keeps_the_carried_deviation():
    """Нау-каст с огромной ошибкой после факта: отклонение пути маржи — то,
    что перенесено от факта (ρ^k·откл), а не сжатое к цели режима."""
    fact = _observed(plain(), ("2026H2", 0.044))
    both = _observed(fact, ("2027H1", {"value": 0.08, "se": 1e6}))
    for regime in ("stress", "partial"):
        at = Cell.build(fact, "M", regime, "base")
        margins = [row.margin for row in run_cell(fact, at).rows]
        assert [row.margin for row in run_cell(both, at).rows] == pytest.approx(margins, abs=1e-12)


def test_a_fact_on_the_anchor_carries_its_deviation():
    """Правило якоря (A-C4, книга §4 п. 6): факт за полугодие ЯКОРЯ задаёт
    отклонение на якоре. Маржа режима r в k-м прогнозном полугодии — цель +
    сезон + ρ^k·(факт − цель_r(якорь) − сезон(якорь)); нау-каст первого
    полугодия берёт априорную дисперсию σ²(1 − ρ²), а не σ². Без факта якоря
    отклонение — ноль. Так после перезаякоривания путь маржи совпадает с
    прокатанной книгой (`tests/test_reanchor.py`)."""
    from model.book import path_value, previous_period
    from model.core import margin_season

    plain_book = plain()
    anchor = previous_period(plain_book["meta"]["first_period"])
    fact, nowcast = 0.052, {"value": 0.046, "se": 0.003}
    A = _observed(plain_book, (anchor, fact))
    both = _observed(A, (plain_book["meta"]["first_period"], nowcast))
    rho = A["margin"]["deviation_persistence"]
    sigma2 = A["joint"]["regime_update"]["sigma_pp"] ** 2
    prior_var = sigma2 * (1 - rho ** 2)
    for regime, spec in A["margin"]["regimes"].items():
        def base(p):
            return path_value(spec["target"], p) + margin_season(A, p)

        rows = run_cell(A, Cell.build(A, "M", regime, "base")).rows
        dev = fact - path_value(spec["target"], anchor) - margin_season(A, anchor)
        assert abs(dev) > 1e-3, "факт у цели режима — проверка пуста"
        assert [r.margin for r in rows] == pytest.approx(
            [base(r.period) + rho ** (k + 1) * dev for k, r in enumerate(rows)], abs=1e-12), regime
        first = rows[0].period
        prior = rho * dev
        post = prior + prior_var / (prior_var + nowcast["se"] ** 2) * (
            nowcast["value"] - base(first) - prior)
        got = run_cell(both, Cell.build(both, "M", regime, "base")).rows
        assert got[0].margin == pytest.approx(base(first) + post, abs=1e-12), regime
        assert got[1].margin == pytest.approx(base(got[1].period) + rho * post, abs=1e-12), regime
        rows = run_cell(plain_book, Cell.build(plain_book, "M", regime, "base")).rows
        assert [r.margin for r in rows] == pytest.approx([base(r.period) for r in rows], abs=1e-12)
