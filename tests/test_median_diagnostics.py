"""Диагностики для печатаемой медианы (`valuation.headline.diagnostics: median`).

Книга печатает медиану распределения центра по суждениям (A-V9), и «что в
цене», «EV против V*» и нейтральная маржа решаются для неё же, а не для точки
при центральных значениях.

Здесь — определяющие свойства на малом числе прогонов (полоса на 16,
пересчёт медианы на 8): в найденном значении медиана равна рынку, при
нейтральной марже — печатаемой, параметр не трогает клеток. Числа книги 1.5
на полных 2 000 / 200 прогонах — в `results.json` (`median_diagnostics`,
регрессия `test_book_results.py`).
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from model.book import BookError, book, median_draws, validate_book
from tests.toy import toy_book

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "data" / "assumptions" / "results.json"
BAND_DRAWS, MEDIAN_DRAWS = 16, 8


def median_book(draws: int = BAND_DRAWS, n: int = MEDIAN_DRAWS) -> dict:
    A = copy.deepcopy(book())
    A["valuation"]["uncertainty"]["draws"] = draws
    A["valuation"]["uncertainty"]["median_draws"] = n
    return A


@pytest.fixture(scope="module")
def pair():
    """Полоса, обратный DCF и «что даст отчёт» для медианы на малых прогонах.

    Старт поиска — точечный обратный DCF книги из `results.json`, чтобы здесь
    считалась именно медиана. Якорь — общий, его пересчёты видны в `evaluations`.
    """
    from model.uncertainty import (MedianAnchor, next_report_median, reverse_dcf_median,
                                   uncertainty)

    A = median_book()
    point_rows = json.loads(RESULTS.read_text(encoding="utf-8"))["reverse_dcf"]
    band = uncertainty(A)
    anchor = MedianAnchor(A, band)
    mine = dict(band=band, reverse=reverse_dcf_median(A, band, point_rows, anchor),
                report=next_report_median(A, band, anchor=anchor), anchor=anchor,
                point_rows=point_rows)
    return A, mine


# ------------------------------------------------------------------ ключи книги


@pytest.mark.tact
def test_the_book_diagnoses_the_printed_median():
    """Диагностики — для печатаемой медианы: действующее правило; ключ
    `valuation.headline.diagnostics` (схема книги Ленты 1.0) допускает только
    `median`, без ключа — то же правило."""
    A = toy_book()
    assert A["valuation"]["headline"].get("diagnostics", "median") == "median"
    assert median_draws(A) > 0


@pytest.mark.tact
def test_a_diagnostics_value_other_than_the_median_is_refused():
    A = toy_book()
    del A["valuation"]["headline"]["diagnostics"]
    validate_book(A)
    A["valuation"]["headline"]["diagnostics"] = "point"
    with pytest.raises(BookError, match="diagnostics"):
        validate_book(A)


@pytest.mark.parametrize("value", [True, 1, 0, 200.0, "200"])
@pytest.mark.tact
def test_a_bad_median_draws_is_refused(value):
    A = toy_book()
    A["valuation"]["uncertainty"]["median_draws"] = value
    with pytest.raises(BookError, match="median_draws"):
        validate_book(A)


@pytest.mark.needs_book
def test_median_diagnostics_need_the_band():
    A = copy.deepcopy(book())
    del A["valuation"]["uncertainty"]
    with pytest.raises(BookError, match="valuation.uncertainty"):
        validate_book(A)
    validate_book(median_book())


@pytest.mark.needs_book
def test_the_band_carries_the_mapping_of_each_draw():
    """Полоса несёт отображение каждого прогона: «EV против V*» медианы
    считается по ним без новых сеток."""
    from model.uncertainty import uncertainty

    assert len(uncertainty(median_book(), draws=4)["layer_draws"]) == 4


# --------------------------------------------------- определяющие свойства медианы


# Рынок, до которого медиане малой полосы надо дойти (рынок самой книги может
# оказаться уже в допуске поиска): так поиск корня действительно работает.
SHIFTED_MARKET = 1700.0


@pytest.fixture(scope="module")
def shifted():
    from model.uncertainty import reverse_dcf_median, uncertainty

    A = median_book()
    A["market"]["price"] = SHIFTED_MARKET
    point_rows = json.loads(RESULTS.read_text(encoding="utf-8"))["reverse_dcf"]
    band = uncertainty(A)
    return A, band, point_rows, reverse_dcf_median(A, band, point_rows)


@pytest.mark.needs_book
def test_the_median_reverse_dcf_prices_the_market(shifted):
    """В решении поиска медиана полосы — рынок (до допуска поиска); строка несёт
    точечное решение (старт поиска) и значение книги. Строка, уточнённая шагом
    секущей на полной полосе (`median_refine`), несёт решение поиска в
    `search_value`, а её значение — шаг секущей от него."""
    from model.book import reverse_bounds
    from model.engine import with_overrides
    from model.uncertainty import MEDIAN_TOLERANCE_RUB, MedianAnchor, follow_center, secant_step, value_axis

    A, band, point_rows, rows = shifted
    assert [r["name"] for r in rows] == [ax["name"] for ax in A["reverse_dcf"]]
    anchor = MedianAnchor(A, band)
    found = 0
    for row, point, ax in zip(rows, point_rows, A["reverse_dcf"]):
        assert row["point_value"] == point["value"] and row["book_value"] == point["book_value"]
        assert row["inside_range"] == (row["value"] is not None
                                       and row["range"][0] <= row["value"] <= row["range"][1])
        if row["value"] is None:
            continue
        found += row["evaluations"] > 0
        value = row["value"]
        if row.get("search_gap_full") is not None:
            b = 0.0 if ax.get("kind") == "shift" else row["book_value"]
            assert value == pytest.approx(secant_step(row["search_value"], row["search_gap_full"], b,
                                                      anchor.full["central"] - SHIFTED_MARKET),
                                          rel=1e-12, abs=1e-15), row["name"]
            value = row["search_value"]
        overrides = {p: ({"__shift__": value} if ax.get("kind") == "shift" else value) for p in ax["paths"]}
        B = with_overrides(A, overrides)
        if reverse_bounds(A) == "follow_center" and ax.get("kind", "value") == "value":
            j = value_axis(A, ax["paths"][0])
            if j is not None:
                follow_center(B, j, value)
        median = anchor.at(B)["central"]
        assert abs(median - SHIFTED_MARKET) <= MEDIAN_TOLERANCE_RUB, (row["name"], value, median)
    assert found >= 5, "поиск корня почти не работал: проба ничего не проверяет"


@pytest.mark.needs_book
def test_the_median_at_the_market_needs_no_search(pair):
    """Медиана полосы уже у рынка (в допуске) — значение каждой оси = книге, без
    пересчётов. Рынок пробы — сама медиана малой полосы: у цены книги она стоит
    только случайно (на книге 1.5.1 стояла, на 1.6 — нет)."""
    from model.uncertainty import MEDIAN_TOLERANCE_RUB, MedianAnchor, reverse_dcf_median

    A, mine = copy.deepcopy(pair[0]), pair[1]
    A["market"]["price"] = mine["band"]["central"]["0.50"]
    rows = reverse_dcf_median(A, mine["band"], mine["point_rows"], MedianAnchor(A, mine["band"]))
    assert abs(mine["band"]["central"]["0.50"] - A["market"]["price"]) <= MEDIAN_TOLERANCE_RUB
    for row in rows:
        assert row["evaluations"] == 0, row["name"]
        assert row["value"] == (0.0 if row["kind"] == "shift" else row["book_value"]), row["name"]


@pytest.mark.needs_book
def test_the_median_next_report_and_neutral_margin(pair):
    """Строки — на значениях книги, медиана растёт с маржой; при нейтральной марже
    медиана — печатаемая (до допуска поиска)."""
    from model.grid import with_observation
    from model.uncertainty import MEDIAN_TOLERANCE_RUB, MedianAnchor

    A, mine = pair
    rows, neutral = mine["report"]
    assert [r["margin"] for r in rows] == book()["joint"]["regime_update"]["demo_values"]
    assert [r["central"] for r in rows] == sorted(r["central"] for r in rows), "медиана растёт с маржой"
    assert neutral["central"] == mine["band"]["central"]["0.50"]
    assert neutral["evaluations"] == mine["anchor"].evaluations
    at = MedianAnchor(A, mine["band"]).at(with_observation(A, neutral["period"], neutral["margin"]))
    assert abs(at["central"] - neutral["central"]) <= MEDIAN_TOLERANCE_RUB


@pytest.mark.needs_book
def test_the_median_book_prices_the_same_cells_and_layers():
    """Параметр — только о диагностиках: 36 клеток, слои и точка те же, бит в бит."""
    from model.grid import build_grid, fair_value, layers

    A, B = median_book(), book()
    cells, base = build_grid(A), build_grid(B)
    lm, lb = layers(A, cells), layers(B, base)
    for c, b in zip(cells, base):
        assert c.cell.key == b.cell.key
        assert (c.result.ev, c.result.claims, c.probability) == (b.result.ev, b.result.claims, b.probability)
    for name in ("macro_neutral", "market_implied", "analytical"):
        assert (lm[name].v0, lm[name].claims, lm[name].headline) == (
            lb[name].v0, lb[name].claims, lb[name].headline), name
    assert fair_value(A, cells, lm).central == fair_value(B, base, lb).central


# ------------------------------------------------------------- свойства ядра


@pytest.mark.needs_book
def test_the_median_mapping_reproduces_the_band_median(pair):
    """При x = 1 медиана центров отображения — медиана полосы (та же функция)."""
    from model.uncertainty import center_ev_median

    A, mine = pair
    band = copy.deepcopy(mine["band"])
    band["market"] = band["central"]["0.50"]
    ce = center_ev_median(A, band)
    assert ce.v_star == pytest.approx(ce.v0, rel=1e-9), "x* = 1: рынок и есть медиана"
    assert ce.v0 == pytest.approx(mine["band"]["v0_lambda"]["0.50"], rel=1e-12)


@pytest.mark.needs_book
def test_the_median_anchor_equals_the_printed_median_at_the_book(pair):
    """Сдвиг на общих числах: при значении книги — ровно печатаемая медиана."""
    from model.uncertainty import MedianAnchor, uncertainty

    A, mine = pair
    anchor = MedianAnchor(A, mine["band"])
    assert anchor.at(copy.deepcopy(A))["central"] == mine["band"]["central"]["0.50"]
    same = MedianAnchor(A, uncertainty(A, draws=MEDIAN_DRAWS))
    assert same.base["central"] == same.full["central"], "полоса той же длины — те же прогоны"


@pytest.mark.tact
def test_the_solver_finds_a_root_or_says_it_is_out_of_reach():
    from model.uncertainty import MEDIAN_TOLERANCE_RUB, solve_outward

    calls = []

    def g(x):
        calls.append(x)
        return 1000.0 * x ** 3 + 300.0 * x - 150.0

    root, used = solve_outward(g, 0.0, g(0.0), 0.1, 2.0)
    assert used == len(calls) - 1, "значение в старте отрезка не пересчитывается"
    assert abs(g(root)) <= MEDIAN_TOLERANCE_RUB
    assert solve_outward(lambda x: -100.0 - x, 0.0, -100.0, 0.5, 1.0) == (None, 2)
    assert solve_outward(g, 0.0, 0.0, 1.0, 2.0) == (0.0, 0)


# ------------------------------------------------------------------ выпуск


@pytest.fixture(scope="module")
def median_payload():
    """Выпуск на книге с `median` на малом числе прогонов (≈30 с ядром)."""
    from model.engine import run_release
    from model.payload import build_payload

    return build_payload(run_release(median_book(), gates=False))


@pytest.mark.needs_book
@pytest.mark.ci_only
def test_the_release_blocks_are_for_the_median(median_payload):
    """Полный выпуск (полоса, медиана) — в CI; согласованность блоков медианы
    сверяет и сама сборка (`validate`), кроме положения нейтральной маржи
    между строками — оно от данных, а не от построения."""
    p = median_payload
    rd = p["reverse_dcf"]
    assert rd["target"] == "median" and rd["draws"] == MEDIAN_DRAWS
    assert rd["criterion"].startswith("медиана")
    assert all("point_value" in r for r in rd["rows"])
    first = p["fair_value"]["ev_first_line"]
    assert first["target"] == "median"
    head = p["fair_value"]["headline"]
    assert first["v0"] == pytest.approx(head["v0"]["v0_lambda"]["0.50"], abs=0.06)
    assert [r["release"] for r in first["by_lambda"]].count(True) == 1
    book_row = next(r for r in first["by_lambda"] if r["release"])
    assert (book_row["v0"], book_row["v_star"]) == (first["v0"], first["v_star"])
    neutral = p["next_report_neutral"]
    assert neutral["target"] == "median"
    assert neutral["central"] == round(head["median"], 1)
    rows = p["next_report_value"]
    assert all({"median", "median_low", "median_high"} <= set(r) for r in rows)
    slope = (rows[-1]["median"] - rows[0]["median"]) / ((rows[-1]["margin"] - rows[0]["margin"]) * 1000)
    assert neutral["slope_rub_per_0p1pp"] == pytest.approx(slope, abs=0.6)
    assert rows[0]["median"] < neutral["central"] < rows[-1]["median"]
    assert rows[0]["margin"] < neutral["margin"] < rows[-1]["margin"]


# ------------------------------------ границы оси и уточнение (книга 1.5.1)


def _param_values(B: dict, j: int, path: str) -> list[float]:
    """Значения суждения оси j по прогонам полосы книги `B` (точки гиперкуба книги)."""
    from model.uncertainty import axis_overrides, draw_points

    U = B["valuation"]["uncertainty"]
    ax = U["axes"][j]
    return [axis_overrides(B, ax, s[j])[path] for s in draw_points(U["draws"], len(U["axes"]), U["seed"])]


@pytest.mark.needs_book
@pytest.mark.parametrize("path", ["valuation.erp", "financing.operating_cash_pct"])
def test_the_bound_of_a_value_axis_follows_a_centre_outside_it(path):
    """Центр оси-значения за её границей: граница следует за ним, и медиана
    суждения в прогонах — подменённое значение («центр суждения = медиана»).
    С неподвижной границей обе половины треугольника уходят от центра внутрь
    диапазона, и медиана суждения — не он."""
    from model.engine import with_overrides
    from model.uncertainty import follow_center, quantile, value_axis

    A = median_book(draws=201)
    j = value_axis(A, path)
    ax = A["valuation"]["uncertainty"]["axes"][j]
    low, high = ax["low"], ax["high"]
    for v in (low - 0.4 * (high - low), high + 0.3 * (high - low)):
        fixed = with_overrides(A, {path: v})
        moved = copy.deepcopy(fixed)
        follow_center(moved, j, v)
        got = moved["valuation"]["uncertainty"]["axes"][j]
        assert (got["low"], got["high"]) == (min(low, v), max(high, v))
        width = high - low
        assert abs(quantile(sorted(_param_values(moved, j, path)), 0.5) - v) <= 0.01 * width
        assert abs(quantile(sorted(_param_values(fixed, j, path)), 0.5) - v) > 0.1 * width


@pytest.mark.needs_book
def test_a_centre_inside_the_range_leaves_the_axis_as_it_is():
    """Центр внутри диапазона (и ровно на границе) — ось та же, бит в бит."""
    from model.engine import with_overrides
    from model.uncertainty import follow_center, value_axis

    A = median_book()
    j = value_axis(A, "valuation.beta_u")
    ax = A["valuation"]["uncertainty"]["axes"][j]
    for v in (ax["low"], 0.61, ax["high"]):
        B = with_overrides(A, {"valuation.beta_u": v})
        before = copy.deepcopy(B)
        follow_center(B, j, v)
        assert B == before


@pytest.mark.needs_book
def test_the_family_is_continuous_at_the_bound():
    """Центр чуть за границей даёт те же значения суждения по прогонам, что центр
    на границе, с точностью до расстояния до неё."""
    from model.engine import with_overrides
    from model.uncertainty import follow_center, value_axis

    path, eps = "valuation.erp", 1e-9
    A = median_book(draws=64)
    j = value_axis(A, path)
    low = A["valuation"]["uncertainty"]["axes"][j]["low"]
    at = _param_values(with_overrides(A, {path: low}), j, path)
    beyond = with_overrides(A, {path: low - eps})
    follow_center(beyond, j, low - eps)
    assert max(abs(a - b) for a, b in zip(at, _param_values(beyond, j, path))) <= 2 * eps


@pytest.mark.needs_book
def test_only_value_axes_of_the_band_follow_the_centre():
    from model.uncertainty import value_axis

    A = median_book()
    assert value_axis(A, "valuation.erp") is not None
    assert value_axis(A, "revenue.traffic.base") is None       # ось-сдвиг
    assert value_axis(A, "valuation.ronic_spread") is None     # не ось полосы


# Рынок проб «за границей оси» на книге «Ленты» 1.0 после пакета аудита и правок его
# проверки 30.09.2026 (щит терминала на среднем рычаге цикла; малая полоса: медиана
# ≈3 199 ₽): ERP и декабрьский базис ОК решаются за границей своих осей (≈6,89 % при
# верхе 6,8 %; ≈−1,7 % при верхе −2,54 %), плотность новой площади «у дома» — внутри
# (≈0,657 из 0,65–0,90), и её решение с подвижной границей то же бит в бит. Прежняя
# проба 3 050 ₽ после правок ставит ERP внутрь оси (≈6,62 %), а затравка поиска (×9/4 от
# точечного сдвига) заходит за край — «внутри диапазона — бит в бит» не выполняется по
# построению пробы; замер 30.09.2026: все условия теста держатся на 3 006–3 024 ₽
# (на 3 024 ₽ ERP ≈6,805 % — у самого верха), проба — 3 013 ₽. β_u в пробу не входит:
# на рынке, где ERP за границей, решение β_u у верха оси и затравка заходит за край. До
# правок проверки проба стояла на 3 050 ₽, до пакета — на 3 070 ₽ с β_u (верх ERP 6,7 %),
# до F1 — на 3 040 ₽. Дисконт за управление и целевой рычаг в пробу не входят: решение g
# у верха оси 14 %, у L — ниже низа оси, поиск заходит за край.
OUTSIDE_MARKET = 3013.0


def _reverse_rows(market: float, keys: dict, names: tuple) -> tuple[dict, dict, list[dict], object]:
    from model.uncertainty import MedianAnchor, reverse_dcf, reverse_dcf_median, uncertainty

    A = median_book()
    A["market"]["price"] = market
    A["reverse_dcf"] = [ax for ax in A["reverse_dcf"] if ax["name"] in names]
    # База проб — без правил границы и уточнения (явные умолчания); пробы включают их сами.
    A["valuation"]["uncertainty"].update({"reverse_bounds": "fixed", "median_refine": 0, **keys})
    band = uncertainty(A)
    anchor = MedianAnchor(A, band)
    return A, band, reverse_dcf_median(A, band, reverse_dcf(A), anchor), anchor


VALUE_ROWS = ("ERP", "ОК: декабрьский базис", "Плотность новой площади «у дома»")


@pytest.fixture(scope="module")
def follow_pair():
    fixed = _reverse_rows(OUTSIDE_MARKET, {}, VALUE_ROWS)
    moved = _reverse_rows(OUTSIDE_MARKET, {"reverse_bounds": "follow_center"}, VALUE_ROWS)
    return fixed, moved


# Поиск и уточнение на малой полосе — ≈20 с, поэтому в CI; в такте — свойства
# функций выше и ниже, числа книги держит регрессия `results.json`.
@pytest.mark.needs_book
@pytest.mark.ci_only
def test_the_median_reverse_dcf_follows_the_centre_only_outside_the_range(follow_pair):
    """`reverse_bounds: follow_center`: строки внутри диапазона — те же бит в бит,
    за границей — решение с подвижной границей, и в нём медиана полосы — рынок."""
    from model.engine import with_overrides
    from model.uncertainty import MEDIAN_TOLERANCE_RUB, MedianAnchor, follow_center, value_axis

    (_, _, fixed, _), (A, band, moved, _) = follow_pair
    anchor = MedianAnchor(A, band)
    outside = 0
    for f, m in zip(fixed, moved):
        if f["inside_range"]:
            assert m == f, f["name"]
            continue
        if m["value"] is None:
            continue
        outside += 1
        path = m["paths"][0]
        B = with_overrides(A, {path: m["value"]})
        follow_center(B, value_axis(A, path), m["value"])
        assert abs(anchor.at(B)["central"] - OUTSIDE_MARKET) <= MEDIAN_TOLERANCE_RUB, m["name"]
        assert m["value"] != f["value"], m["name"]
    assert outside >= 2, "строки за диапазоном не решены — проба пуста"


@pytest.mark.needs_book
@pytest.mark.ci_only
def test_the_median_refine_takes_one_secant_step_on_the_full_band(follow_pair):
    """`median_refine: 1`: решение строки внутри диапазона — шаг секущей по базе
    (значение книги, печатаемая медиана) и невязке полной полосы в решении поиска;
    строки за границей и без решения не уточняются."""
    from model.engine import with_overrides
    from model.uncertainty import band_medians

    (_, _, plain, _), _ = follow_pair
    A, band, rows, anchor = _reverse_rows(OUTSIDE_MARKET, {"median_refine": 1}, VALUE_ROWS)
    base_gap = band["central"]["0.50"] - OUTSIDE_MARKET
    refined = 0
    for p, r in zip(plain, rows):
        assert r["search_value"] == p["value"], r["name"]
        if not p["inside_range"] or p["value"] is None:
            assert (r["value"], r["search_gap_full"]) == (p["value"], None), r["name"]
            continue
        refined += 1
        x, b = p["value"], (0.0 if r["kind"] == "shift" else r["book_value"])
        trial = with_overrides(A, {q: ({"__shift__": x} if r["kind"] == "shift" else x) for q in r["paths"]})
        gap = band_medians(trial, band["draws"])["central"] - OUTSIDE_MARKET
        assert r["search_gap_full"] == gap, r["name"]
        assert r["value"] == pytest.approx(x - gap * (x - b) / (gap - base_gap), rel=1e-12), r["name"]
        assert r["inside_range"] == (r["range"][0] <= r["value"] <= r["range"][1])
    assert refined >= 1, "ни одна строка не уточнялась — проба пуста"
    assert anchor.full_grids == refined * band["draws"]


@pytest.mark.tact
def test_the_secant_step_hits_the_root_of_a_line():
    from model.uncertainty import secant_step

    f = lambda x: 3.0 * x - 1.5         # noqa: E731 — корень 0,5
    assert secant_step(0.9, f(0.9), 0.0, f(0.0)) == pytest.approx(0.5, rel=1e-15)
    assert secant_step(0.9, 2.0, 0.0, 2.0) == 0.9, "без наклона шага нет"
