# -*- coding: utf-8 -*-
"""Перезаякоривание книги функцией: инвариант «факты = ожидание ⇒ ничего не сдвинулось».

ЗАЧЕМ. У книги-источника (850oa) ручная процедура перезаякоривания на сухом
прогоне давала заметный скачок центра чистой механики, и ещё несколько мест
проходили все тесты молча: страж скачка заголовка отключается именно новой
версией книги. Здесь механика проверяется там, где её видно, — против
НЕперезаякоренной книги, прокатанной к той же дате оценки. Книга —
синтетическая (`tests/fixtures/toy_book`): в ней есть всё, что умеет ядро, —
сегменты трёх режимов (yoy, level, revenue), строки моста с расчётом,
приобретённый периметр (ОК, запертые убытки), маржа якоря на проформе.

ТРИ УРОВНЯ ПРОВЕРКИ.

1. **Клетка на своём пути** — главный сторож. Факты отчёта берутся с пути
   одной клетки, и перезаякоренная клетка обязана дать ту же EV и те же
   требования, что прокатанная (EV — до 0,1 %, требования — до 2 млн ₽).
   Неравенства Йенсена здесь нет. Каждая механическая ошибка ручной
   процедуры — и новые места сегментов: индекс эффективной площади сегмента,
   плотность сегмента `level` в ценах якоря, ОК приобретённого периметра — выводит клетку
   из допуска (`test_each_mechanical_error_is_caught`).
2. **Слой** — факты = ожидание слоя «свой макро-взгляд». Допуск задания:
   V0 каждого слоя в пределах ±1 %, печатаемая точка — в пределах цены 1 % EV
   центра. Измеренный сторож — V0 до 0,1 %, точка — до четверти цены 1 % EV —
   держится на книге, где закрываемое полугодие у всех клеток идёт одной
   сетью и одним спросом (`_one_closing_half`): иначе остаток слоя —
   неравенство Йенсена, а не механика. Отчёт — одно число, а прокатанная
   книга ведёт каждую клетку своим путём: у синтетической книги сценарии
   роста сети различаются по мирам уже в закрываемом полугодии (N — high,
   M — low), и слой «рыночные ставки» (мир M) получает из отчёта площадь
   среднего мира; спрос по режимам (чек, трафик) различается тоже. Замер
   15.02.2027: V0 слоя «рыночные ставки» +0,72 %, «свой макро-взгляд» −0,15 %,
   точка +0,15 цены 1 % EV; с одной сетью и одним спросом закрываемого
   полугодия — V0 всех слоёв в пределах ±0,05 %, точка до 0,03 цены 1 % EV.
   Это свойство факта: настоящий отчёт снимает
   неопределённость полугодия, а не ошибка переноса (клетки на своих путях
   держат 0,1 % EV).
3. **Числа кандидата** на двух датах закреплены (регрессия): правка механики
   якоря, не меняющая инвариантов выше, не пройдёт молча.

И отдельно — **печатаемый заголовок**, медиана полосы и полоса 80 %: в
пределах цены 1 % EV центра.
"""
from __future__ import annotations

import copy
import dataclasses
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from model.book import (BookError, Cell, all_cells, load_book, path_value, previous_period,
                        segments)
from model.core import run_cell
from model.engine import central_price
from model.grid import (build_grid, next_report_neutral, next_report_table, regime_table,
                        report_period)
from tests.toy import TOY_BOOK, toy_book

# Инструмент оператора: такт его не вызывает, опубликованный выпуск он не
# защищает. Гоняется в CI и руками перед перезаякориванием.
pytestmark = pytest.mark.ci_only

ROOT = Path(__file__).resolve().parents[1]

DATES = ("2027-01-01", "2027-02-15")          # начало 1П2027 и дата внутри него
CELL_EV_TOLERANCE = 1e-3                      # клетка: EV, доля EV прокатанной
CELL_EV_FLOOR = 0.02                          # млрд ₽ — клетки с EV около нуля
CELL_CLAIMS_TOLERANCE = 2e-3                  # млрд ₽
SPEC_V0 = 0.01                                # задание: ±1 % V0
TIGHT_V0 = 1e-3                               # измеренный сторож слоя
TIGHT_POINT = 0.25                            # доля цены 1 % EV центра
# Низ / центр / верх ядра на кандидате, собранном по ожидаемому отчёту
# синтетической книги. Ожидаемый факт несёт на якоре отклонение каждого режима
# (правило якоря).
CANDIDATE_POINTS = {
    "2027-01-01": (1930.9335080494131, 1926.3069076627767, 1921.6803072761402),
    "2027-02-15": (1969.7575871349432, 1965.0154521975314, 1960.2733172601197),
}
PIN_REL = 1e-9
POWER_CELLS = ("H|partial|base", "N|full|high", "M|floor|base")


def _module(path: Path, name: str):
    """Модуль по пути; в sys.modules — до исполнения (его dataclass ищет свой модуль там)."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tool():
    return _module(ROOT / "ops" / "tools" / "reanchor.py", "reanchor_tool")


@pytest.fixture
def A():
    return toy_book()


def _cell(A: dict, key: str) -> Cell:
    return Cell.build(A, *key.split("|"))


def _network(A: dict) -> list[str]:
    return [s.id for s in segments(A) if s.network]


def _level(A: dict) -> str:
    return next(s.id for s in segments(A) if s.mode == "level")


def _cell_violation(tool, A: dict, key: str, date: str, mutate=None) -> tuple[float, str]:
    """Насколько клетка вышла из допуска (> 1 — вышла) и описание расхождения."""
    cell = _cell(A, key)
    report = tool.expected_report(A, cell=cell)
    cand = tool.reanchor(A, report, valuation_date=date).book
    if mutate:
        mutate(A, cand, report)
    was = run_cell(tool.rolled(A, date), cell)
    now = run_cell(cand, _cell(cand, key))
    ev_gap, claims_gap = now.ev - was.ev, now.claims - was.claims
    ev_allowed = CELL_EV_TOLERANCE * abs(was.ev) + CELL_EV_FLOOR
    ratio = max(abs(ev_gap) / ev_allowed, abs(claims_gap) / CELL_CLAIMS_TOLERANCE)
    return ratio, (f"{key} на {date}: EV {was.ev:.3f} → {now.ev:.3f} ({ev_gap:+.4f}, допуск "
                   f"±{ev_allowed:.3f}), требования {was.claims:.4f} → {now.claims:.4f} "
                   f"({claims_gap:+.5f})")


def _one_closing_half(A: dict, *periods: str) -> dict:
    """Книга, где закрываемые полугодия `periods` у всех клеток идут одной сетью и одним спросом.

    Открытия и закрытия всех сценариев роста — как у `mid`, сдвиг чека и
    трафик всех состояний спроса — как у `base`, поправка LFL сегмента по
    режимам — как у `floor`; дальше книги траектории прежние. Остаются
    различия миров (инфляция, ставки) и режимов (цели маржи), уровней capex.
    Так из остатка слоя уходит неравенство Йенсена по сети и спросу, и
    измеренный сторож слоя видит механику (шапка модуля, уровень 2).
    """
    B = copy.deepcopy(A)

    def pin(block: dict, key: str, value: float, p: str) -> None:
        spec = block[key]
        spec = dict(spec) if isinstance(spec, dict) else {"LT": spec}
        spec[p] = value
        block[key] = spec

    R = B["revenue"]
    for p in periods:
        for spec in R["segments"].values():
            if "space" in spec:
                for level in ("low", "high"):
                    for key in ("gross_open", "close"):
                        pin(spec["space"][level], key, path_value(spec["space"]["mid"][key], p), p)
            offset = spec.get("lfl_offset")
            if isinstance(offset, dict) and "by_regime" in offset:
                common = path_value(offset["by_regime"]["floor"], p)
                for regime in offset["by_regime"]:
                    pin(offset["by_regime"], regime, common, p)
        for block in ("ticket_shift", "traffic"):
            common = path_value(R[block]["base"], p)
            for state in ("bear", "bull"):
                pin(R[block], state, common, p)
    return B


# ------------------------------------------------------ правила инструмента = ядро


def test_the_tool_rules_are_the_core_rules(tool, A):
    """Эффективная площадь сегментов, плотность сегмента `level` и пулы убытков по
    фактам — те же правила, что в ядре.

    Инструменту приходится повторить шаги ядра на ФАКТИЧЕСКИХ числах (площадь и
    открытия сегмента, его выручка, чистые проценты): ядро считает их только по
    своему пути. Здесь повтор сверяется с ядром на путях всех 36 клеток трёх
    книг — исходной и дважды перезаякоренной: пул запертых убытков
    приобретённых юрлиц в первых двух копит прибавки, в третьей (полугодие
    `usable_from`) переходит в пул группы.
    """
    first = tool.reanchor(A, tool.expected_report(A), valuation_date=DATES[1]).book
    second = tool.reanchor(first, tool.expected_report(first), valuation_date="2027-08-31").book
    level = _level(A)
    worst, added, merged = 0.0, False, False
    for B in (A, first, second):
        period = B["meta"]["first_period"]
        book_segments = {s.id: s for s in segments(B)}
        for cell in all_cells(B):
            row = run_cell(B, cell).rows[0]
            for sid, seg in book_segments.items():
                step_row = row.segments[sid]
                if not seg.network:
                    continue
                step = tool.effective_area_step(B, seg, step_row.opened, step_row.area_end)
                worst = max(worst, abs(step["average"] - step_row.effective_area_avg),
                            abs(step["closed"] - step_row.closed))
                # сдвиг индекса за полугодие — ровно (1 − продуктивность закрываемой)
                # × закрытия; у перенесённого уровня к нему прибавлен прежний сдвиг
                carried = step["start"] - tool.effective_area_step(
                    B, dataclasses.replace(seg, effective_area_end=None),
                    step_row.opened, step_row.area_end)["start"]
                assert step["delta"] == pytest.approx(
                    (1 - seg.closed_productivity) * step["closed"] + carried, abs=1e-9), sid
                if sid == level:
                    # плотность факта на пути клетки — плотность книги (индекс ИПЦ
                    # первого прогнозного полугодия — ИПЦ мира клетки)
                    cpi = path_value(B["worlds"][cell.world]["cpi"], period)
                    density = tool.level_density(B, seg, period, step_row.revenue,
                                                 step_row.effective_area_avg,
                                                 1 + tool.half_rate(cpi))
                    worst = max(worst, abs(density / path_value(seg.spec["density_path"],
                                                                period) - 1))
            pool, acquired = tool.tax_loss_pools_by_rule(B, period, row.revenue, row.ebitda,
                                                         row.da, row.capex, row.net_interest)
            worst = max(worst, abs(pool - row.tax_loss_pool),
                        abs(acquired - row.tax_loss_pool_acquired),
                        abs(tool.acquired_pool_by_rule(B, period) - row.tax_loss_pool_acquired))
            amount = B["tax"]["acquired_nol"]["amount"]
            added |= acquired > amount + 1e-9
            merged |= amount > 0 and acquired == 0.0
    assert added and merged, "правило запертых убытков не пройдено целиком — проверка пуста"
    assert worst < 1e-9, f"правило инструмента разошлось с ядром на {worst:.2e}"


# ------------------------------------------------------ уровень 1: клетка на своём пути


@pytest.mark.parametrize("date", DATES)
def test_a_cell_reanchored_on_its_own_path_keeps_its_value(tool, A, date):
    """Все 36 клеток: EV до 0,1 %, требования до 2 млн ₽ (см. шапку модуля)."""
    worst = max((_cell_violation(tool, A, cell.key, date) for cell in all_cells(A)),
                key=lambda item: item[0])
    assert worst[0] <= 1.0, worst[1]


def _undo(*paths):
    def mutate(A, X, report):
        for dotted in paths:
            node_a, node_x = A, X
            keys = dotted.split(".")
            for key in keys[:-1]:
                node_a, node_x = node_a[key], node_x[key]
            node_x[keys[-1]] = copy.deepcopy(node_a[keys[-1]])
    return mutate


def _undo_prices(A, X, report):
    for sid in _network(A):
        X["capex"]["segments"][sid]["growth_capex_per_m2"] = \
            A["capex"]["segments"][sid]["growth_capex_per_m2"]
    X["capex"]["infra_capex_per_net_m2"] = A["capex"]["infra_capex_per_net_m2"]


def _no_area_rebase(A, X, report):
    # уровень индекса не перенесён: ядро сбрасывает его к «физическая − незрелая»
    for sid in _network(A):
        X["facts"]["segments"][sid].pop("eff_area_end")


def _undo_density(A, X, report):
    # плотность сегмента level осталась в ценах старого якоря
    sid = _level(A)
    X["revenue"]["segments"][sid]["density_path"] = \
        copy.deepcopy(A["revenue"]["segments"][sid]["density_path"])


def _stale_cohorts(A, X, report):
    # когорты не сдвинуты: открытия закрытого полугодия не дозревают
    for sid in _network(A):
        X["facts"]["segments"][sid]["new_area_gross_hist"] = \
            list(A["facts"]["segments"][sid].get("new_area_gross_hist", []))
        X["facts"]["segments"][sid].pop("new_area_dense_cohorts", None)


def _stale_bridge_row(A, X, report):
    # строка с наращением названа на новую дату, а сумма — старая
    row = next(r for r in X["bridge"]["items"] if r.get("accrete_rate_half"))
    row["amount"] = next(r for r in A["bridge"]["items"] if r["id"] == row["id"])["amount"]


def _model_net_debt(A, X, report):
    F, FN = A["facts"], A["financing"]
    period, anchor = A["meta"]["first_period"], F["anchor"]["period"]
    revenue = sum(block["revenue"][period] for block in report["segments"].values())
    ltm0 = F["revenue"][previous_period(anchor)] + F["revenue"][anchor]
    X["facts"]["anchor"]["net_debt"] += FN["operating_cash_pct"] * (
        revenue + F["revenue"][anchor] - ltm0)


def _june_excess_in_december(A, X, report):
    revenue = X["facts"]["revenue"]
    ltm = sum(revenue[p] for p in sorted(revenue)[-2:])
    X["nwc"]["nwc_pct_start"] += X["nwc"]["seasonal_june_excess"] / ltm


def _acquired_nwc_dropped(A, X, report):
    # ОК приобретённого периметра вычтена из старта: она — часть траектории
    # (`acquired_path` поверх `nwc_pct`), и ядро «вложило» бы её второй раз
    X["nwc"]["nwc_pct_start"] -= path_value(A["nwc"]["acquired_path"], A["meta"]["first_period"])


MECHANICAL_ERRORS = [
    ("июньский излишек не переиндексирован", _undo("nwc.seasonal_june_excess")),
    ("цены capex сегментов и инфраструктуры не переиндексированы", _undo_prices),
    ("индекс эффективной площади сегментов сброшен на якоре", _no_area_rebase),
    ("плотность сегмента level не переиндексирована", _undo_density),
    ("когорты открытий не сдвинуты", _stale_cohorts),
    ("пул убытков не обновлён", _undo("tax.nol_start")),
    ("строка моста не наращена к дате отчёта", _stale_bridge_row),
    ("чистый долг модельный, а не отчётный", _model_net_debt),
    ("июньский излишек в декабрьском стартовом уровне", _june_excess_in_december),
    ("ОК приобретённого периметра вычтена из старта", _acquired_nwc_dropped),
]


@pytest.mark.parametrize("name,mutate", MECHANICAL_ERRORS, ids=[m[0] for m in MECHANICAL_ERRORS])
def test_each_mechanical_error_is_caught(tool, A, name, mutate):
    """Мощность уровня 1: каждая ошибка ручной процедуры выводит клетку из допуска.

    Без этой проверки допуск клетки мог бы оказаться таким же слепым, как ±1 %
    V0 задания, — а он и есть главный сторож.
    """
    worst = max((_cell_violation(tool, A, key, DATES[1], mutate) for key in POWER_CELLS),
                key=lambda item: item[0])
    assert worst[0] > 1.0, f"ошибка «{name}» не поймана: {worst[1]}"


def test_a_derived_area_history_is_written_as_numbers(tool, A):
    """История эффективной площади, выведенная правилом сети назад (`closed_area_hist`),
    на новом якоре — числами: старый якорь — как его видело ядро, закрытое
    полугодие — шаг ядра на пути клетки; ключ закрытий снят.

    Почему не перенести и закрытия: вывод назад от перенесённого уровня
    считает плотными (дозревающими до d) на одну когорту больше, чем шаг
    вперёд, по которому шла прокатанная книга, — база «год к году» закрытого
    полугодия разошлась бы с путём (проверено ниже на том же отчёте).
    """
    for sid in _network(A)[:2]:
        A["facts"]["segments"][sid]["closed_area_hist"] = {"2025H2": 6.0, "2026H1": 7.0}
    period, anchor = A["meta"]["first_period"], A["facts"]["anchor"]["period"]
    book = {s.id: s for s in segments(A)}
    cell = _cell(A, "H|partial|base")
    row = run_cell(A, cell).rows[0]
    cand = tool.reanchor(A, tool.expected_report(A, cell=cell), valuation_date=DATES[1])
    X = cand.book
    for sid in _network(A)[:2]:
        facts = X["facts"]["segments"][sid]
        assert "closed_area_hist" not in facts
        assert facts["eff_area_avg_hist"][anchor] == pytest.approx(
            tool.book_effective_hist(A, book[sid])[anchor], abs=1e-9)
        assert facts["eff_area_avg_hist"][period] == pytest.approx(
            row.segments[sid].effective_area_avg, abs=1e-9)
        rolled_keys = copy.deepcopy(X)
        rolled_keys["facts"]["segments"][sid]["closed_area_hist"] = {
            anchor: 7.0, period: cand.derived["segments"][sid]["closed"]}
        seg = next(s for s in segments(rolled_keys) if s.id == sid)
        assert abs(tool.book_effective_hist(rolled_keys, seg)[period]
                   - row.segments[sid].effective_area_avg) > 1e-6, \
            "вывод назад совпал с шагом вперёд — снимать закрытия незачем"
    assert sum("closed_area_hist снят" in w for w in cand.warnings) == 2
    worst = max((_cell_violation(tool, A, key, DATES[1]) for key in POWER_CELLS),
                key=lambda item: item[0])
    assert worst[0] <= 1.0, worst[1]


# ------------------------------------------------------ уровень 2: слой


def _layer_gaps(before: dict, after: dict) -> tuple[float, float, str]:
    worst_v0 = max(abs(after["v0"][k] / before["v0"][k] - 1) for k in before["v0"])
    point = abs(after["central"] - before["central"]) / before["rub_per_1pct_ev"]
    text = (f"точка {before['low']:.1f}/{before['central']:.1f}/{before['high']:.1f} → "
            f"{after['low']:.1f}/{after['central']:.1f}/{after['high']:.1f} (1 % EV = "
            f"{before['rub_per_1pct_ev']:.1f} ₽); V0 " + ", ".join(
                f"{k} {before['v0'][k]:.2f} → {after['v0'][k]:.2f}" for k in before["v0"]))
    return worst_v0, point, text


def _layer_check(tool, A: dict, date: str, with_fact: bool) -> tuple[float, float, str]:
    report = tool.expected_report(A)
    cand = tool.reanchor(A, report, valuation_date=date)
    X = copy.deepcopy(cand.book)
    if not with_fact:
        del X["joint"]["regime_update"]["observations"][cand.period]
    obs = {cand.period: cand.derived["margin"]} if with_fact else None
    return _layer_gaps(tool.headline(tool.rolled(A, date, obs)), tool.headline(X))


@pytest.mark.parametrize("date", DATES)
@pytest.mark.parametrize("with_fact", [False, True], ids=["механика", "с фактом маржи"])
def test_reanchoring_on_the_expected_path_keeps_the_headline(tool, A, date, with_fact):
    """Инвариант задания ядром: факты = ожидание слоя «свой макро-взгляд».

    «механика» — наблюдение маржи закрываемого полугодия убрано из кандидата
    и не добавлено в прокатанную; «с фактом» — ожидаемый факт в наблюдениях
    обеих книг: правило A-P2u реагирует на него одинаково, и расходиться может
    только механика.

    Книга — без издержек неустойчивости (`distress.cost_pct_ev` = 0) в обеих
    книгах: порог рычага делает перезаякоривание разрывным, а отчёт — одно
    усреднённое число; клетка у порога по усреднённому факту уходит за него или
    под него, и издержки появляются или исчезают целиком — это выпуклость у
    порога, а не ошибка механики. Механику издержек по клеткам держат уровень 1
    и `test_the_distress_trigger_is_the_same_rolled_or_reanchored`.

    Допуск задания — на книге как есть; измеренный сторож — на книге с одним
    закрываемым полугодием у всех клеток (шапка модуля, уровень 2).
    """
    A["valuation"]["distress"]["cost_pct_ev"] = 0.0
    v0, point, text = _layer_check(tool, A, date, with_fact)
    assert v0 <= SPEC_V0 and point <= 1.0, f"задание ±1 % V0 нарушено: {text}"
    v0, point, text = _layer_check(tool, _one_closing_half(A, A["meta"]["first_period"]),
                                   date, with_fact)
    assert v0 <= TIGHT_V0 and point <= TIGHT_POINT, f"сторож слоя: {text}"


def test_the_pro_forma_anchor_margin_stays_evidence(tool, A):
    """Маржа якоря на проформе переходит в наблюдения: вероятности режимов — как у прокатанной.

    Правдоподобие A-P2u у кандидата то же, что у прокатанной книги с тем же
    фактом (таблица режимов по мирам совпадает до 1e-12), и проформа в
    наблюдениях — с её ошибкой. Потерянная проформа сдвинула бы вероятности
    режимов всех клеток сразу — уровень клетки этого не видит, поэтому здесь
    и проверка мощности.
    """
    period = A["meta"]["first_period"]
    anchor = A["facts"]["anchor"]
    cand = tool.reanchor(A, tool.expected_report(A), valuation_date=DATES[1])
    X = cand.book
    assert "margin_pro_forma" not in X["facts"]["anchor"]
    assert X["joint"]["regime_update"]["observations"][anchor["period"]] == {
        "value": anchor["margin_pro_forma"], "se": anchor["margin_pro_forma_se"]}
    was = regime_table(tool.rolled(A, DATES[1], {period: cand.derived["margin"]}))
    now = regime_table(X)
    assert max(abs(now[w][r] - was[w][r]) for w in was for r in was[w]) < 1e-12
    lost = copy.deepcopy(X)
    del lost["joint"]["regime_update"]["observations"][anchor["period"]]
    moved = regime_table(lost)
    assert max(abs(moved[w][r] - was[w][r]) for w in was for r in was[w]) > 1e-4, \
        "проверка пуста: без проформы вероятности режимов те же"


BAND_DRAWS = 80                               # прогонов полосы в тесте (пулом, `parallel_band`)


def test_reanchoring_on_the_expected_path_keeps_the_printed_headline(tool, A, parallel_band):
    """ЗАГОЛОВОК — медиана полосы, а не точка при центрах суждений.

    Крупно печатаются медиана и полоса 80 %. Тесты выше держат точку
    `fair_value.central`; здесь — то, что печатается: медиана, 10-й и 90-й
    процентили центра и P(центр < рынка), прокатанная книга с ожидаемым фактом
    против кандидата. Точки гиперкуба у обеих книг одни (тот же seed, те же
    оси), сравнение парное. Допуск — задания: цена 1 % EV центра. Измерено на
    синтетической книге 15.02.2027: 80 прогонов — 10 % +21 ₽, медиана +15,
    90 % +3 при цене 1 % EV 31 ₽, P(центр < рынка) без изменений. Остаток шире,
    чем у точки: оси полосы, которые двигают и закрываемое полугодие (трафик,
    продуктивность закрываемой площади, инфляция мира), у прокатанной книги
    двигают его вместе с будущим, а у кандидата оно — факт, замороженный при
    центральных значениях. Это свойство факта, а не механика.
    """
    from model.uncertainty import uncertainty

    date = DATES[1]
    cand = tool.reanchor(A, tool.expected_report(A), valuation_date=date)
    rolled = tool.rolled(A, date, {cand.period: cand.derived["margin"]})
    with parallel_band():
        before = uncertainty(copy.deepcopy(rolled), draws=BAND_DRAWS)
        after = uncertainty(copy.deepcopy(cand.book), draws=BAND_DRAWS)
    price_1pct = tool.headline(rolled)["rub_per_1pct_ev"]
    gaps = {q: after["central"][q] - before["central"][q] for q in ("0.10", "0.50", "0.90")}
    text = ", ".join(f"{q}: {before['central'][q]:.1f} → {after['central'][q]:.1f}" for q in gaps)
    assert max(abs(g) for g in gaps.values()) <= price_1pct, \
        f"заголовок сдвинулся больше цены 1 % EV ({price_1pct:.1f} ₽): {text}"
    assert abs(after["p_central_below_market"] - before["p_central_below_market"]) <= 0.02, text


# ------------------------------------------------------ уровень 3: числа кандидата


@pytest.mark.parametrize("date", DATES)
def test_the_candidate_prices_as_pinned(tool, A, date):
    """Регрессия: числа кандидата на ожидаемом отчёте синтетической книги.

    Числа зависят от ядра и синтетической книги: намеренная правка ядра или
    книги меняет их, и тогда CANDIDATE_POINTS перезакрепляются значениями из
    сообщения — после того как тесты уровней 1 и 2 выше зелёные (механика
    цела). Правка одного инструмента их менять не должна.
    """
    from model.grid import fair_value, layers

    X = copy.deepcopy(tool.reanchor(A, tool.expected_report(A), valuation_date=date).book)
    cells = build_grid(X)
    fv = fair_value(X, cells, layers(X, cells))
    assert cells[0].result.rows[0].period == X["meta"]["first_period"] == "2027H1"
    got = (fv.low, fv.central, fv.high)
    assert got == pytest.approx(CANDIDATE_POINTS[date], rel=PIN_REL), (
        f"числа кандидата на {date}: {got!r} — если правка ядра или синтетической книги "
        "намеренная и уровни 1–2 зелёные, перезакрепить CANDIDATE_POINTS")


def test_two_reanchorings_in_a_row_keep_the_headline(tool, A):
    """2П2026, затем 1П2027: второй якорь — 30.06, с июньским излишком в факте ОК.

    Факт ОК на 30.06 содержит июньский пик (так его и даёт отчёт); инструмент
    раскладывает его на декабрьский базис и излишек, иначе июньский излишек
    вошёл бы в стартовый уровень дважды. Во втором полугодии выплачивается
    строка моста с наращением (снимается вместе со своей осью), пул запертых
    убытков копит прибавку полугодия, прибавки закрытых полугодий снимаются.
    """
    first = tool.reanchor(A, tool.expected_report(A), valuation_date="2027-02-15").book
    date = "2027-08-31"
    report = tool.expected_report(first)
    assert report["period"] == "2027H1"
    second = tool.reanchor(first, report, valuation_date=date)
    assert second.book["meta"]["first_period"] == "2027H2"
    # старт 30.06 — без июньского излишка: траектория сетки (nwc_pct + acquired_path)
    assert second.book["nwc"]["nwc_pct_start"] == pytest.approx(
        tool.nwc_trajectory(first, "2027H1"), abs=1e-6)
    settled = [r["id"] for r in first["bridge"]["items"] if r.get("settle_period") == "2027H1"]
    assert settled and not {r["id"] for r in second.book["bridge"]["items"]} & set(settled)
    assert not any(str(a.get("path", "")).startswith(f"bridge.items[{settled[0]}]")
                   for a in second.book["sensitivities"])
    locked, locked2 = first["tax"]["acquired_nol"], second.book["tax"]["acquired_nol"]
    assert locked2["amount"] == pytest.approx(
        locked["amount"] + locked["discount_rule"]["locked_addback"]["2027H1"], abs=1e-9)
    assert all(tool.period_index(p) > tool.period_index("2027H1")
               for p in locked2["discount_rule"]["locked_addback"])
    for key in POWER_CELLS:
        cell = _cell(first, key)
        was = run_cell(tool.rolled(first, date), cell)
        now = run_cell(tool.reanchor(first, tool.expected_report(first, cell=cell),
                                     valuation_date=date).book, _cell(second.book, key))
        assert abs(now.ev - was.ev) <= CELL_EV_TOLERANCE * abs(was.ev) + CELL_EV_FLOOR, key
        assert abs(now.claims - was.claims) <= CELL_CLAIMS_TOLERANCE, key
    # Слой — с ожидаемым фактом 1П2027 в обеих книгах: правило A-P2u и хвост
    # факта (правило якоря) у них одни, расходиться может только механика;
    # измеренный сторож — на книге с одним закрываемым полугодием у всех клеток.
    obs = {second.period: second.derived["margin"]}
    before, after = tool.headline(tool.rolled(first, date, obs)), tool.headline(second.book)
    v0, point, text = _layer_gaps(before, after)
    assert v0 <= SPEC_V0 and point <= 1.0, f"задание ±1 % V0 нарушено: {text}"
    flat = _one_closing_half(A, "2026H2", "2027H1")
    first = tool.reanchor(flat, tool.expected_report(flat), valuation_date="2027-02-15").book
    second = tool.reanchor(first, tool.expected_report(first), valuation_date=date)
    obs = {second.period: second.derived["margin"]}
    v0, point, text = _layer_gaps(tool.headline(tool.rolled(first, date, obs)),
                                  tool.headline(second.book))
    assert v0 <= TIGHT_V0 and point <= 2 * TIGHT_POINT, f"сторож слоя: {text}"


# ------------------------------------------------------ факт маржи — один раз


def test_the_margin_fact_is_counted_once(tool, A):
    """Уровень — в истории EBITDA, свидетельство о режиме — в наблюдениях; больше нигде.

    Таблица A-P2 остаётся априорной (её двигает правило), цели режимов на
    закрытое полугодие не трогаются (против них судится факт), прогноз
    нау-каста за то же полугодие и его шок заменяются фактом; проформа якоря —
    наблюдением со своей ошибкой, а не фактом.
    """
    B = copy.deepcopy(A)
    period = B["meta"]["first_period"]
    anchor = B["facts"]["anchor"]
    B["joint"]["regime_update"]["observations"] = {period: {"value": 0.057, "se": 0.003}}
    B["margin"]["nowcast_margin_shocks_pp"] = {period: -0.001}
    report = tool.expected_report(A)
    cand = tool.reanchor(B, report, valuation_date=DATES[1])
    X = cand.book
    revenue = sum(block["revenue"][period] for block in report["segments"].values())
    margin = report["ebitda_pre16"][period] / revenue
    assert X["joint"]["regime_update"]["observations"] == {
        anchor["period"]: {"value": anchor["margin_pro_forma"], "se": anchor["margin_pro_forma_se"]},
        period: margin}
    assert X["facts"]["ebitda_pre16"][period] == report["ebitda_pre16"][period]
    assert X["joint"]["regime_given_world"] == A["joint"]["regime_given_world"]
    assert X["margin"]["regimes"] == A["margin"]["regimes"]
    assert X["margin"]["nowcast_margin_shocks_pp"] == {}
    assert any("заменён фактом" in w for w in cand.warnings)


@pytest.mark.parametrize("fact", [0.061, 0.054], ids=["выше ожидания", "ниже ожидания"])
def test_the_margin_fact_tail_crosses_the_anchor(tool, A, fact):
    """AR(1)-хвост факта маржи переходит якорь: его несёт ядро (правило якоря).

    Факт закрываемого полугодия выше или ниже ожидания. Прокатанная книга
    несёт отклонение факта от цели КАЖДОГО режима в следующее полугодие и
    дальше затуханием ρ; кандидат — то же из факта на якоре (`model/core.py`).
    Издержки неустойчивости выключены в обеих книгах: у порогов отчёт — одно
    усреднённое число, а прокатанная книга ведёт каждую клетку своим путём, и
    выпуклость у порога — не хвост. Механику издержек по клеткам держат
    уровень 1 и `test_the_distress_trigger_is_the_same_rolled_or_reanchored`.
    """
    from indicators.nowcast import model_expectation

    B = copy.deepcopy(A)
    B["valuation"]["distress"]["cost_pct_ev"] = 0.0
    date = DATES[1]
    observed = tool.rolled(B, B["meta"]["valuation_date"], {B["meta"]["first_period"]: fact})
    before = tool.rolled(observed, date)
    cand = tool.reanchor(observed, tool.expected_report(observed), valuation_date=date)
    X, first = cand.book, cand.book["meta"]["first_period"]
    assert abs(cand.derived["margin_tail_shock"]) > 1e-3, "хвост ≈ 0 — проверка пуста"
    # путь маржи всех 36 клеток — как у прокатанной книги
    worst = 0.0
    for cell in all_cells(X):
        was = {row.period: row.margin for row in run_cell(before, _cell(before, cell.key)).rows}
        worst = max(worst, max(abs(row.margin - was[row.period]) for row in run_cell(X, cell).rows))
    assert worst < 1e-12, f"путь маржи кандидата разошёлся с прокатанной на {worst:.2e} (доли)"
    # слой — в допуске задания; измеренный сторож — на книге с одним закрываемым полугодием
    v0, point, text = _layer_gaps(tool.headline(before), tool.headline(X))
    assert v0 <= SPEC_V0 and point <= 1.0, f"хвост факта: {text}"
    flat = tool.rolled(_one_closing_half(B, B["meta"]["first_period"]), B["meta"]["valuation_date"],
                       {B["meta"]["first_period"]: fact})
    flat_cand = tool.reanchor(flat, tool.expected_report(flat), valuation_date=date)
    v0, point, text = _layer_gaps(tool.headline(tool.rolled(flat, date)),
                                  tool.headline(flat_cand.book))
    assert v0 <= TIGHT_V0 and point <= TIGHT_POINT, f"хвост факта, сторож слоя: {text}"
    # хвоста нет в шоках нау-каста; вероятности режимов — как у прокатанной
    assert X["margin"]["nowcast_margin_shocks_pp"] == {}
    was, now = regime_table(before), regime_table(X)
    assert max(abs(now[w][r] - was[w][r]) for w in was for r in was[w]) < 1e-12
    # база нау-каста первого полугодия кандидата — средняя маржа ядра прокатанной
    grid = [(c.probability, row.margin) for c in build_grid(before)
            for row in c.result.rows if row.period == first]
    mean = sum(p * m for p, m in grid) / sum(p for p, _ in grid)
    assert model_expectation(X, first)[0] == pytest.approx(mean, abs=1e-12)


def test_axes_of_a_reindexed_price_follow_it_or_the_tool_refuses(tool, A):
    """Оси переиндексированной цены — тем же множителем во всех трёх списках, или отказ.

    Ось обратного DCF и ось на несколько цен сразу иначе молча остались бы в
    ценах старого якоря. В синтетической книге таких осей нет — они добавлены
    здесь на копии.
    """
    B = copy.deepcopy(A)
    path = next(a["path"] for a in B["sensitivities"]
                if str(a.get("path")).endswith("growth_capex_per_m2"))
    capex_axis = next(a for a in B["sensitivities"] if a.get("path") == path)
    low, high = capex_axis["low"], capex_axis["high"]
    prices = [p for p in tool.price_indexed(B) if p.startswith("capex.")]
    assert path in prices and "capex.infra_capex_per_net_m2" in prices
    assert f"revenue.segments.{_level(B)}.density_path" in tool.price_indexed(B)
    B["reverse_dcf"].append({"name": "цена открытия", "paths": [path],
                             "kind": "value", "search": [low / 2, 2 * high], "range": [low, high]})
    B["sensitivities"].append({"name": "все цены capex", "paths": prices,
                               "shift_low": -low / 10, "shift_high": high / 10})
    cand = tool.reanchor(B, tool.expected_report(A), valuation_date=DATES[1])
    k = cand.derived["price_index_factor"]
    assert k > 1
    moved = next(a for a in cand.book["sensitivities"] if a.get("path") == path)
    assert (moved["low"], moved["high"]) == pytest.approx((low * k, high * k), rel=1e-12)
    assert cand.book["reverse_dcf"][-1]["range"] == pytest.approx([low * k, high * k], rel=1e-12)
    assert cand.book["reverse_dcf"][-1]["search"] == pytest.approx([low / 2 * k, 2 * high * k], rel=1e-12)
    every = cand.book["sensitivities"][-1]                 # один раз, а не по разу на путь
    assert (every["shift_low"], every["shift_high"]) == pytest.approx((-low / 10 * k, high / 10 * k),
                                                                      rel=1e-12)
    mixed = copy.deepcopy(A)
    mixed["valuation"]["uncertainty"]["axes"].append(
        {"name": "смесь", "paths": [path, "capex.maintenance_pct.base"],
         "shift_low": -0.001, "shift_high": 0.001})
    with pytest.raises(tool.FactsError, match="одним множителем"):
        tool.reanchor(mixed, tool.expected_report(A), valuation_date=DATES[1])


# ------------------------------------------------------ мост


def test_a_settled_bridge_row_leaves_the_bridge_with_its_axes(tool, A):
    """Строка, выплаченная к концу закрываемого полугодия, снимается с моста вместе со
    своими осями: выплата уже в чистом долге отчёта. Ось, которая двигает снятую
    строку вместе с другими, — решение автора книги (отказ). Строки с фактом
    названы на дату моста кандидата — первый день после полугодия."""
    period = A["meta"]["first_period"]
    settled = [r["id"] for r in A["bridge"]["items"] if r.get("settle_period") == period]
    assert settled, "в синтетической книге нет строки с расчётом в первом полугодии"
    B = copy.deepcopy(A)
    B["sensitivities"].append({"name": "остаток оплаты", "path": f"bridge.items[{settled[0]}].amount",
                               "low": 3.0, "high": 5.0})
    cand = tool.reanchor(B, tool.expected_report(A), valuation_date=DATES[1])
    X = cand.book
    assert not {r["id"] for r in X["bridge"]["items"]} & set(settled)
    assert not any(a["name"] == "остаток оплаты" for a in X["sensitivities"])
    assert any("остаток оплаты" in w for w in cand.warnings)
    assert all(r["as_of"] == X["meta"]["bridge_as_of"] == f"{int(period[:4]) + 1}-01-01"
               for r in X["bridge"]["items"])
    mixed = copy.deepcopy(A)
    mixed["sensitivities"].append({"name": "два обязательства",
                                   "paths": [f"bridge.items[{settled[0]}].amount",
                                             "bridge.items[ltip].amount"],
                                   "shift_low": -1.0, "shift_high": 1.0})
    with pytest.raises(tool.FactsError, match="выплаченную строку"):
        tool.reanchor(mixed, tool.expected_report(A), valuation_date=DATES[1])


# ------------------------------------------------------ отказы


def _report(tool, A, **changes):
    report = copy.deepcopy(tool.expected_report(A))
    for key, value in changes.items():
        if value is None:
            report.pop(key)
        else:
            report[key] = value
    return report


def _with_segment(report: dict, sid: str, **changes) -> dict:
    out = copy.deepcopy(report)
    for key, value in changes.items():
        if value is None:
            out["segments"][sid].pop(key)
        else:
            out["segments"][sid][key] = value
    return out


def test_the_tool_refuses_what_it_would_read_wrongly(tool, A):
    """Каждое «прочёл бы молча не так» — отказ с объяснением, и именно СВОЙ отказ.

    Каждый случай ловится своим текстом: без `match` случай «EBITDA после
    МСФО 16» проходил бы и без проверки маржи — его ловила сверка EBITDA LTM
    (синтетический отчёт несёт LTM прежней EBITDA), и снятие проверки маржи
    осталось бы незамеченным. Поэтому в том случае LTM убран.
    """
    period = A["meta"]["first_period"]
    good = tool.expected_report(A)
    revenue = sum(block["revenue"][period] for block in good["segments"].values())
    network, level = _network(A)[0], _level(A)
    flat = next(s.id for s in segments(A) if not s.network)
    settled = next(r["id"] for r in A["bridge"]["items"] if r.get("settle_period") == period)
    accreting = next(r["id"] for r in A["bridge"]["items"] if r.get("accrete_rate_half"))
    segs = good["segments"]
    cases = {
        "не то полугодие": (_report(tool, A, period="2027H1"), "по одному полугодию"),
        "незнакомый ключ": (_report(tool, A, net_debtt=1.0), "незнакомые ключи net_debtt"),
        "нет пула и процентов": (_report(tool, A, tax_loss_pool=None), "нужен tax_loss_pool"),
        "маржа после МСФО 16 в сверке": (_report(tool, A, margin_pre16=0.095), "≠ EBITDA/выручка"),
        "EBITDA после МСФО 16": (_report(tool, A, ebitda_pre16={period: 0.095 * revenue},
                                         ebitda_ltm=None), "вокруг целей режимов"),
        "закрытия отрицательны": (
            _with_segment(good, network,
                          area_end=segs[network]["area_end"] + 10 * segs[network]["opened_gross"] + 1),
            "открытия валовые"),
        "LTM не сходится": (_report(tool, A, ebitda_ltm=good["ebitda_ltm"] + 1.0), "сумме полугодий"),
        "незнакомая строка моста": (_report(tool, A, bridge={**good["bridge"], "put2": 1.0}),
                                    "незнакомые строки моста put2"),
        "строка с наращением без факта": (
            _report(tool, A, bridge={k: v for k, v in good["bridge"].items() if k != accreting}),
            "обязателен"),
        "выплаченная строка в мосте": (_report(tool, A, bridge={**good["bridge"], settled: 4.0}),
                                       "выплачен"),
        "незнакомый сегмент": (_report(tool, A, segments={**segs, "mini": segs[flat]}),
                               "незнакомые сегменты mini"),
        "нет сегмента": (_report(tool, A, segments={k: v for k, v in segs.items() if k != level}),
                         f"нет сегментов {level}"),
        "незнакомый ключ сегмента": (_with_segment(good, network, opened=1.0),
                                     f"segments.{network}: незнакомые ключи opened"),
        "площадь у сегмента без площади": (_with_segment(good, flat, area_end=10.0),
                                           f"segments.{flat}: незнакомые ключи area_end"),
        "нет открытий у сегмента сети": (_with_segment(good, network, opened_gross=None),
                                         "нет обязательных ключей opened_gross"),
        "выручка группы ≠ сумме сегментов": (_report(tool, A, revenue={period: revenue + 1.0}),
                                             "≠ сумме выручки сегментов"),
        "мост раньше даты отчёта": (_report(tool, A, bridge_as_of=f"{period[:4]}-12-30"),
                                    "раньше конца полугодия"),
        "запертые убытки без книги": (
            _report(tool, _without_acquired_nol(A), tax_loss_pool_acquired=1.0), "нет tax.acquired_nol"),
    }
    for name, (report, text) in cases.items():
        book = _without_acquired_nol(A) if name == "запертые убытки без книги" else A
        with pytest.raises(tool.FactsError, match=text):
            tool.reanchor(book, report)
            pytest.fail(f"не отказал: {name}")
    with pytest.raises(tool.FactsError, match="не позже даты фактов"):
        tool.reanchor(A, good, valuation_date=good["period"][:4] + "-12-31")


def _without_acquired_nol(A: dict) -> dict:
    B = copy.deepcopy(A)
    del B["tax"]["acquired_nol"]
    return B


def test_a_restated_half_needs_the_group_line_or_every_segment(tool, A):
    """Отчёт пересчитал прошлое полугодие сегмента: строка группы — из файла или сумма
    всех сегментов; пересчёт части сегментов без строки группы — отказ."""
    period, anchor = A["meta"]["first_period"], A["facts"]["anchor"]["period"]
    good = tool.expected_report(A)
    network = _network(A)[0]
    restated = _with_segment(good, network, revenue={
        **good["segments"][network]["revenue"],
        anchor: A["facts"]["segments"][network]["revenue"][anchor] + 1.0})
    with pytest.raises(tool.FactsError, match=f"пересчёт {anchor}"):
        tool.reanchor(A, restated, valuation_date=DATES[1])
    restated["revenue"] = {period: sum(b["revenue"][period] for b in good["segments"].values()),
                           anchor: A["facts"]["revenue"][anchor] + 1.0}
    X = tool.reanchor(A, restated, valuation_date=DATES[1]).book
    assert X["facts"]["revenue"][anchor] == A["facts"]["revenue"][anchor] + 1.0
    assert X["facts"]["segments"][network]["revenue"][anchor] == \
        A["facts"]["segments"][network]["revenue"][anchor] + 1.0


def test_the_reported_perimeter_rolls_with_the_window(tool, A):
    """`facts.reported` (отчётный периметр рядом с проформой) сдвигается окном; без ключа
    `reported` в файле фактов отчётное полугодие = проформа (с предупреждением);
    `facts.anchor.ebitda_ltm_reported` — сумма двух полугодий reported (решение 12)."""
    period, anchor = A["meta"]["first_period"], A["facts"]["anchor"]["period"]
    good = tool.expected_report(A)
    cand = tool.reanchor(A, good, valuation_date=DATES[1])
    R, FA = cand.book["facts"]["reported"], cand.book["facts"]["anchor"]
    assert R["ebitda_pre16"][period] == cand.book["facts"]["ebitda_pre16"][period]
    assert R["revenue"][period] == cand.book["facts"]["revenue"][period]
    assert len(R["revenue"]) == len(A["facts"]["reported"]["revenue"])
    assert FA["ebitda_ltm_reported"] == pytest.approx(
        A["facts"]["reported"]["ebitda_pre16"][anchor] + R["ebitda_pre16"][period], abs=1e-12)
    assert any("facts.reported" in w for w in cand.warnings)
    stated = copy.deepcopy(good)
    stated["reported"] = {"revenue": {period: 500.0}, "ebitda_pre16": {period: 29.0}}
    X = tool.reanchor(A, stated, valuation_date=DATES[1]).book
    assert X["facts"]["reported"]["revenue"][period] == 500.0
    assert X["facts"]["anchor"]["ebitda_ltm_reported"] == pytest.approx(
        A["facts"]["reported"]["ebitda_pre16"][anchor] + 29.0, abs=1e-12)


def test_the_revenue_basis_and_error_roll_with_the_window(tool, A):
    """Основа выручки сегмента и её ошибка (`revenue_basis`, `revenue_se`) сдвигаются тем
    же окном, что выручка: отчёт — reported с нулевой ошибкой; расчёт или проформа в
    файле фактов — со своей ошибкой, без неё — отказ, если книга ведёт ошибку базы."""
    period, anchor = A["meta"]["first_period"], A["facts"]["anchor"]["period"]
    for sid, facts in A["facts"]["segments"].items():
        facts["revenue_se"] = {p: (0.0 if facts["revenue_basis"][p] == "reported" else 1.5)
                               for p in facts["revenue"]}
    good = tool.expected_report(A)
    X = tool.reanchor(A, good, valuation_date=DATES[1]).book
    acq = next(sid for sid, f in A["facts"]["segments"].items()
               if f["revenue_basis"][anchor] != "reported")
    facts = X["facts"]["segments"][acq]
    assert set(facts["revenue_se"]) == set(facts["revenue"]) == set(facts["revenue_basis"])
    assert facts["revenue_basis"][period] == "reported" and facts["revenue_se"][period] == 0.0
    assert facts["revenue_se"][anchor] == 1.5
    estimated = _with_segment(good, acq, revenue_basis={period: "estimate"})
    with pytest.raises(tool.FactsError, match=rf"revenue_se\[{period}\] обязателен"):
        tool.reanchor(A, estimated, valuation_date=DATES[1])
    estimated["segments"][acq]["revenue_se"] = {period: 2.0}
    X = tool.reanchor(A, estimated, valuation_date=DATES[1]).book
    assert X["facts"]["segments"][acq]["revenue_basis"][period] == "estimate"
    assert X["facts"]["segments"][acq]["revenue_se"][period] == 2.0
    wrong = _with_segment(good, acq, revenue_se={period: 0.5})
    with pytest.raises(tool.FactsError, match="у отчёта ошибки нет"):
        tool.reanchor(A, wrong, valuation_date=DATES[1])


def test_the_candidate_is_written_outside_the_book_and_reads_back(tool, A, tmp_path, monkeypatch,
                                                                  parallel_band):
    """Командная строка: синтетический отчёт, кандидат, отчёт; канон не трогается."""
    canon = tmp_path / "canon"
    canon.mkdir()
    monkeypatch.setattr(tool, "BOOK", canon)
    facts = tmp_path / "var" / "reanchor" / "facts.json"   # каталога ещё нет (свежая выкладка)
    assert tool.main(["--book", str(TOY_BOOK), "--expected", str(facts)]) == 0
    out = tmp_path / "candidate"
    with parallel_band():
        assert tool.main(["--book", str(TOY_BOOK), "--facts", str(facts), "--out", str(out),
                          "--valuation-date", DATES[1], "--band", "10"]) == 0
    written = load_book(out / "assumptions.yaml")
    cand = tool.reanchor(A, json.loads(facts.read_text(encoding="utf-8")),
                         valuation_date=DATES[1])
    assert written == cand.book
    # JSON — копия для потребителей без YAML: ключи-числа (узлы кривых) в нём строки
    assert json.loads((out / "assumptions.json").read_text(encoding="utf-8")) == json.loads(
        json.dumps(cand.book, ensure_ascii=False))
    text = (out / "REANCHOR.md").read_text(encoding="utf-8")
    assert "Что сдвинулось" in text and "механика" in text and "nwc.seasonal_june_excess" in text
    assert "Сеть по сегментам" in text and "плотность факт / книга" in text
    assert "Механика на путях клеток (каждая из 36 клеток" in text
    # заголовок — медиана полосы: её строки есть при --band (здесь 10 прогонов)
    assert "| медиана |" in text and "10 прогонов на книгу" in text
    assert tool.main(["--book", str(TOY_BOOK), "--facts", str(facts),
                      "--out", str(canon / "x")]) == 1
    assert not (canon / "x").exists()


def test_the_synthetic_report_is_not_written_into_the_book(tool, tmp_path, monkeypatch):
    """`--expected` в канон книги — отказ, как у `--out` (канон подменён пустышкой)."""
    canon = tmp_path / "canon"
    canon.mkdir()
    monkeypatch.setattr(tool, "BOOK", canon)
    target = canon / "expected.json"
    assert tool.main(["--book", str(TOY_BOOK), "--expected", str(target)]) == 1
    assert not target.exists()


# ------------------------------------------------------ «что даст отчёт»


def test_the_next_report_defaults_to_the_first_forecast_period(tool, A):
    """Без `demo_period` — первое прогнозное полугодие; закрытое — отказ.

    После перезаякоривания таблица иначе молча считала бы замену внесённого
    факта демонстрационным значением.
    """
    X = tool.reanchor(A, tool.expected_report(A), valuation_date=DATES[1]).book
    assert report_period(X) == X["meta"]["first_period"] == "2027H1"
    bare = copy.deepcopy(X)
    del bare["joint"]["regime_update"]["demo_period"]
    assert report_period(bare) == "2027H1"
    assert next_report_table(bare, values=[0.05])[0]["period"] == "2027H1"
    stale = copy.deepcopy(X)
    stale["joint"]["regime_update"]["demo_period"] = "2026H2"
    with pytest.raises(BookError, match="закрыто"):
        next_report_table(stale)
    with pytest.raises(BookError, match="закрыто"):
        next_report_neutral(stale)
    with pytest.raises(BookError, match="закрыто"):
        next_report_table(X, values=[0.05], period="2026H2")


# -------------------- оборотный капитал: факт против траектории


def _one_off(tool, X: dict, period: str) -> float:
    """Разрыв старта ОК кандидата с его же траекторией в закрытом полугодии — то, что
    ядро превратит в разовый поток первого прогнозного полугодия (> 0 — высвобождение)."""
    return X["nwc"]["nwc_pct_start"] - tool.nwc_trajectory(X, period)


def test_a_fact_equal_to_the_expectation_gives_no_one_off_nwc_flow(tool, A):
    """Факт оборотного капитала = ожиданию модели: разрыва нет, решения не нужно,
    разового потока в первом прогнозном полугодии нет (остаток — среднее
    отношения по клеткам: ОК приобретённого периметра делится на выручку LTM
    каждой клетки)."""
    period = A["meta"]["first_period"]
    cand = tool.reanchor(A, tool.expected_report(A), valuation_date=DATES[0])
    assert abs(cand.derived["nwc_gap"]) < 1e-6
    assert abs(_one_off(tool, cand.book, period)) < 1e-6
    assert cand.book["nwc"]["nwc_pct"] == A["nwc"]["nwc_pct"]
    assert not any("ОБОРОТНЫЙ КАПИТАЛ" in w for w in cand.warnings)
    cell = _cell(A, "H|partial|base")
    exact = tool.reanchor(A, tool.expected_report(A, cell=cell), valuation_date=DATES[0])
    assert abs(exact.derived["nwc_gap"]) < 1e-12


def test_a_balance_sheet_nwc_start_is_carried_as_a_level(tool, A):
    """Книга задаёт старт ОК балансом (`nwc.anchor_level`, млрд ₽): кандидат пишет уровень
    ОК на дату отчёта — с июньским излишком и ОК приобретённого периметра, как в
    балансе, — и клетки на своих путях держат инвариант; разрыв с траекторией
    считается так же, как у доли."""
    N = A["nwc"]
    anchor = A["facts"]["anchor"]["period"]
    revenue = A["facts"]["revenue"]
    ltm = revenue[previous_period(anchor)] + revenue[anchor]
    N["anchor_level"] = N.pop("nwc_pct_start") * ltm + (
        N["seasonal_june_excess"] if anchor.endswith("H1") else 0.0)
    cell = _cell(A, "H|partial|base")
    report = tool.expected_report(A, cell=cell)
    cand = tool.reanchor(A, report, valuation_date=DATES[1])
    assert "nwc_pct_start" not in cand.book["nwc"]
    assert cand.book["nwc"]["anchor_level"] == pytest.approx(cand.derived["nwc_level"], abs=1e-12)
    assert abs(cand.derived["nwc_gap"]) < 1e-12
    worst = max((_cell_violation(tool, A, key, DATES[1]) for key in POWER_CELLS),
                key=lambda item: item[0])
    assert worst[0] <= 1.0, worst[1]
    gapped = copy.deepcopy(report)
    gapped["nwc_to_revenue"] += 0.005
    with pytest.raises(tool.FactsError, match="--nwc-path"):
        tool.reanchor(A, gapped, valuation_date=DATES[1])


def test_a_nwc_gap_needs_an_explicit_decision(tool, A, tmp_path):
    """Разрыв факта с траекторией больше 0,2 п.п. — отказ без --nwc-path;
    `hold` оставляет разовый поток и громко говорит о нём, `fact` сдвигает траектории."""
    period = A["meta"]["first_period"]
    base = tool.expected_report(A, cell=_cell(A, "H|partial|base"))
    facts = copy.deepcopy(base)
    facts["nwc_to_revenue"] += 0.005
    with pytest.raises(tool.FactsError, match="--nwc-path"):
        tool.reanchor(A, facts)
    small = copy.deepcopy(base)
    small["nwc_to_revenue"] += 0.0015
    assert abs(tool.reanchor(A, small).derived["nwc_gap"] - 0.0015) < 1e-12

    held = tool.reanchor(A, facts, valuation_date=DATES[0], nwc_path="hold")
    assert abs(held.derived["nwc_gap"] - 0.005) < 1e-12
    assert abs(_one_off(tool, held.book, period) - 0.005) < 1e-12
    loud = [w for w in held.warnings if "ОБОРОТНЫЙ КАПИТАЛ" in w]
    assert len(loud) == 1 and "РАЗОВЫМ потоком" in loud[0] and "высвобождение" in loud[0]

    kept = tool.reanchor(A, facts, valuation_date=DATES[0], nwc_path="fact")
    assert abs(_one_off(tool, kept.book, period)) < 1e-12
    for key, spec in A["nwc"]["nwc_pct"].items():
        assert abs(path_value(kept.book["nwc"]["nwc_pct"][key], period) - path_value(spec, period) - 0.005) < 1e-12
    assert any("сдвинуты на разрыв" in w for w in kept.warnings)
    # Разовый поток — половина и больше разрыва × выручка LTM на акцию после
    # дисконта за управление: без решения он прошёл бы молча.
    F, V = held.book["facts"], held.book["valuation"]
    per_share = (held.derived["nwc_one_off"] * 1000.0 / F["shares_out_mln"]
                 * (1 - V["governance_discount"]))
    assert central_price(held.book) - central_price(kept.book) > 0.5 * per_share

    # Командная строка: без ключа — код 1, с ключом — кандидат и громкая строка в REANCHOR.md.
    path = tmp_path / "facts.json"
    path.write_text(json.dumps(facts, ensure_ascii=False), encoding="utf-8")
    common = ["--book", str(TOY_BOOK), "--facts", str(path), "--no-attribution"]
    assert tool.main(common + ["--out", str(tmp_path / "no")]) == 1
    assert tool.main(common + ["--out", str(tmp_path / "hold"), "--nwc-path", "hold"]) == 0
    assert "РАЗРЫВ ФАКТА С ТРАЕКТОРИЕЙ +0.50 п.п." in (tmp_path / "hold" / "REANCHOR.md").read_text(encoding="utf-8")


# ---------------------------------------- издержки неустойчивости — только будущее


def test_the_distress_trigger_is_the_same_rolled_or_reanchored(tool, A):
    """Порог ЧД/EBITDA между рычагом закрытого полугодия и будущего пути: нарушение
    живёт в закрытом полугодии, будущий путь ниже. Прокатанная книга (закрытое
    полугодие в строках пути) и перезаякоренная на его отчёт определяют издержки
    одинаково — по полугодиям после даты оценки, — и обе без издержек."""
    key = "N|full|base"
    first = tool.reanchor(A, tool.expected_report(A, cell=_cell(A, key)),
                          valuation_date="2027-01-01").book
    rows = run_cell(first, _cell(first, key)).rows
    closed, future = rows[0].leverage, max(r.leverage for r in rows[1:])
    assert closed > future, "рычаг закрытого полугодия не выше будущего — проверка пуста"
    trigger = (closed + future) / 2.0
    first["valuation"]["distress"]["net_leverage_trigger"] = trigger
    date = "2027-07-01"
    rolled = run_cell(tool.rolled(first, date), _cell(first, key))
    report = tool.expected_report(first, cell=_cell(first, key))
    anchored_book = tool.reanchor(first, report, valuation_date=date).book
    anchored = run_cell(anchored_book, _cell(anchored_book, key))
    assert rolled.max_leverage > trigger, "в закрытом полугодии нарушения нет — проверка пуста"
    assert rolled.distress_cost == anchored.distress_cost == 0.0
    assert abs(anchored.ev - rolled.ev) <= CELL_EV_TOLERANCE * abs(rolled.ev) + CELL_EV_FLOOR


# ------------------------- дивиденды: пороги по отчётному ЧД и годовое правило (F1)


EXACT = 1e-6                                  # доля допуска клетки: «бит в бит» с запасом


def _dividend_book(A: dict, **financing) -> dict:
    B = copy.deepcopy(A)
    B["financing"].update(financing)
    return B


def _exact(tool, A: dict, date: str, keys=None) -> tuple[float, str]:
    """Худшая клетка в долях допуска плюс выплаты строк: перезаякоренная против прокатанной."""
    worst, text = 0.0, ""
    for key in keys or [c.key for c in all_cells(A)]:
        ratio, line = _cell_violation(tool, A, key, date)
        cell = _cell(A, key)
        cand = tool.reanchor(A, tool.expected_report(A, cell=cell), valuation_date=date).book
        was = run_cell(tool.rolled(A, date), cell).rows[1:]
        now = run_cell(cand, _cell(cand, key)).rows
        gap = max(abs(a.dividends - b.dividends) for a, b in zip(was, now))
        ratio = max(ratio, gap / CELL_CLAIMS_TOLERANCE)
        if ratio >= worst:
            worst, text = ratio, f"{line}; выплаты строк расходятся до {gap:.2e}"
    return worst, text


@pytest.mark.parametrize("date", DATES)
def test_the_reported_basis_makes_reanchoring_equal_rolling(tool, A, date):
    """Находка I1d: пороги лестницы по МОДЕЛЬНОМУ ЧД (в нём прирост
    операционной кассы с якоря) расходятся с перезаякоренной книгой, которая
    начинает с отчётного ЧД, — на синтетической книге до 0,04 допуска, у клетки
    на грани ступени или лимита — разрывно. С `dividend_net_debt_basis:
    reported` перезаякоривание на ожидаемом пути равно перекату бит в бит (замер
    1,4·10⁻¹¹ допуска — округление требований) на всех 36 клетках, выплаты
    строк — тоже. Мощность: на модельной базе остаток ненулевой."""
    worst, text = _exact(tool, _dividend_book(A, dividend_net_debt_basis="reported"), date)
    assert worst <= EXACT, text
    model_basis, _ = _exact(tool, A, date, keys=["H|full|low"])
    assert model_basis > 100 * EXACT, "на модельной базе остатка нет — проба пуста"


def test_the_annual_dividend_carry_crosses_two_anchors(tool, A):
    """Годовое правило (`dividend_timing: annual_next_h1`) через два якоря:
    2П2026 (якорь на 2П — выплата по 2026 г. объявлена по факту года и платится
    в первом прогнозном 1П2027), затем 1П2027 (год 2027 начинается с FCFE
    закрытого 1П). FCFE года переносит `facts.anchor.fcfe_ytd`: FCFE 1П2026
    книги + FCFE полугодия из отчёта (ЧД якоря − ЧД отчёта + dividends_paid −
    прирост операционной кассы). Перезаякоренная клетка = прокатанная бит в бит."""
    B = _dividend_book(A, dividend_net_debt_basis="reported", dividend_timing="annual_next_h1",
                       dividends_from_year=int(A["facts"]["anchor"]["period"][:4]) + 1)
    B["facts"]["anchor"]["fcfe_ytd"] = 5.0           # FCFE 1П года якоря (синтетика): FCFE 2026 > 0
    date = DATES[1]
    worst, text = _exact(tool, B, date)
    assert worst <= EXACT, text
    for key in POWER_CELLS:
        cell = _cell(B, key)
        rows = run_cell(B, cell).rows
        first = tool.reanchor(B, tool.expected_report(B, cell=cell), valuation_date=date).book
        assert first["facts"]["anchor"]["fcfe_ytd"] == pytest.approx(5.0 + rows[0].fcfe, abs=1e-9)
        paid = run_cell(first, _cell(first, key)).rows[0]
        assert paid.period.endswith("H1") and paid.dividends == pytest.approx(rows[1].dividends,
                                                                              abs=1e-9)
        report = tool.expected_report(first, cell=_cell(first, key))
        assert report["period"].endswith("H1") and report["dividends_paid"] > 0.0, key
        second = tool.reanchor(first, report, valuation_date="2027-08-31").book
        assert second["facts"]["anchor"]["fcfe_ytd"] == pytest.approx(rows[1].fcfe, abs=1e-9)
    worst, text = _exact(tool, first, "2027-08-31", keys=list(POWER_CELLS))
    assert worst <= EXACT, text


def test_the_annual_rule_reads_the_dividends_of_the_half(tool, A):
    """Факты годового правила: без `dividends_paid` (или прямого `fcfe_ytd`) —
    отказ; у книги с правилом полугодия эти ключи ничего не значат — отказ; у
    якоря на 2П без FCFE 1П, когда выплата по году в горизонте, — отказ."""
    report = tool.expected_report(A)
    with pytest.raises(tool.FactsError, match="dividends_paid"):
        tool.reanchor(A, dict(report, dividends_paid=0.0))
    B = _dividend_book(A, dividend_timing="annual_next_h1")
    annual = tool.expected_report(B)
    assert annual["dividends_paid"] == 0.0
    with pytest.raises(tool.FactsError, match="dividends_paid"):
        tool.reanchor(B, {k: v for k, v in annual.items() if k != "dividends_paid"})
    assert tool.reanchor(B, dict(annual, fcfe_ytd=7.0)).book["facts"]["anchor"]["fcfe_ytd"] == 7.0
    C = _dividend_book(B, dividends_from_year=int(A["facts"]["anchor"]["period"][:4]) + 1)
    C["facts"]["anchor"]["fcfe_ytd"] = -3.0
    D = copy.deepcopy(C)
    del D["facts"]["anchor"]["fcfe_ytd"]
    D["financing"]["dividends_from_year"] += 1       # книга читается: FCFE 1П не нужен
    later = copy.deepcopy(D)
    later["financing"]["dividends_from_year"] -= 1
    with pytest.raises(tool.FactsError, match="fcfe_ytd"):
        tool.fcfe_ytd_after(later, tool.expected_report(D), 1.0, 1.0)
    assert "fcfe_ytd" not in tool.reanchor(D, tool.expected_report(D)).book["facts"]["anchor"]


@pytest.mark.needs_book
def test_reanchoring_book_one_on_the_expected_path_equals_rolling(tool):
    """Книга «Ленты» 1.0 (пороги лестницы по отчётному ЧД, годовое правило):
    каждая из 36 клеток, перезаякоренная на отчёт 2П2026 своего пути, — бит в
    бит прокатанная (EV, требования, выплаты строк). До правки F1 клетка у
    лимита линий расходилась на 98 допусков (I1d)."""
    B = load_book()
    assert B["financing"].get("dividend_net_debt_basis") == "reported"
    worst, text = _exact(tool, B, DATES[1])
    assert worst <= EXACT, text
