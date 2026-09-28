"""Сетка 36 клеток, обновление вероятностей режимов по данным и три слоя цены.

Три идеи, ради которых этот модуль отделён от расчёта клетки.

**Печатается диапазон с центром, а не точка.** У «обрубка» с рычагом ~5×
одна цифра вводит в заблуждение: в 36 % клеток капитал по DCF обнуляется,
а среднее формируют клетки с восстановлением маржи. Нижняя граница — оценка
при рыночных ставках как есть (что акция стоит без собственного макро-взгляда),
верхняя — ожидаемая стоимость при аналитических весах миров, центр — между
ними с весом λ (A-P1c, по умолчанию 0,5, на дашборде ползунок).

**Медиана сетки центром не служит.** На 36 клетках она скачет: сдвиг маржи
2П2026 с 4,4 до 4,8 % переносит её с 290 на 2 220 ₽, тогда как центр по λ
меняется плавно (1 410 → 2 065 ₽). Медиана считается и показывается — но
для сверки, а не как заголовок.

**Вероятности режимов учатся на данных.** Режим маржи объясняет 63 %
дисперсии цены, поэтому его вероятности обновляются по явному правилу A-P2u,
а не пересматриваются на глаз. Руками таблицу A-P2 не трогают: её двигает
правило; пересмотр самих целей режимов — только новой версией книги.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from typing import Iterable

from model.book import (BookError, Cell, all_cells, headline_block, memoized_paths,
                        path_value, period_index)
from model.core import CellResult, margin_observations, margin_season, run_cell
from model.mapping import layer_mapping, mapped_equity, mapping_scale


# Перцентили распределения исходов — одни для сетки и для полосы книги 1.4.
PERCENTILES = (0.10, 0.25, 0.50, 0.75, 0.90)

# Отрезки поиска бисекций книги 1.4 (эталон: `solve_increasing(…, 0.2, 5.0)` для
# множителя активов центра и `(…, 0.03, 0.07, it=40)` для нейтральной маржи).
# Не допущения, а интервалы, в которых ищется корень: концы расширяются сами,
# если корень снаружи.
CENTER_EV_SEARCH = (0.2, 5.0)
NEUTRAL_MARGIN_SEARCH = (0.03, 0.07)
NEUTRAL_MARGIN_STEPS = 40


def own_macro_confidence(A: dict) -> float:
    """λ — вес собственного макро-взгляда (A-P1c); без ключа — середина, 0,5."""
    return float(A["joint"].get("own_macro_confidence", 0.5))


@dataclass
class GridCell:
    cell: Cell
    probability: float
    result: CellResult

    @property
    def price_floor(self) -> float:
        return self.result.price_floor

    @property
    def price(self) -> float:
        return self.result.price

    @property
    def equity(self) -> float:
        return self.result.equity


# ------------------------------------------------------- A-P2u: обучение


def regime_likelihoods(A: dict) -> list[dict[str, float]]:
    """Правдоподобие режимов по КАЖДОМУ наблюдению маржи, в порядке периодов.

    Отклонение наблюдения от цели режима — AR(1) с тем же затуханием, что и
    в прогнозе (A-C4 = 0,7), шум σ из истории полугодовой маржи 2018-1П2026.
    Для второго и следующих наблюдений берётся инновация, а не само
    отклонение: два подряд похожих отчёта не должны считаться за два
    независимых свидетельства.

    Возвращается СПИСОК, а не произведение: предел сдвига (Р12) действует на
    каждое наблюдение отдельно, два отчёта двигают вероятности сильнее одного.

    Наблюдение с ошибкой se > 0 (нау-каст) известно не точно, поэтому дальше
    переносится не его сырое отклонение, а апостериорное среднее и дисперсия
    отклонения (фильтр Калмана). Для факта (se = 0) это ровно прежняя формула:
    среднее — само отклонение, дисперсия — ноль.
    """
    M = A["margin"]
    update = A["joint"]["regime_update"]
    observations = margin_observations(A)
    if not observations:
        return []

    rho = M["deviation_persistence"]
    sigma2 = update["sigma_pp"] ** 2
    out: list[dict[str, float]] = []
    mean: dict[str, float] = {}
    var: dict[str, float] = {}
    previous_index = None
    for p in sorted(observations, key=period_index):
        value, se = observations[p]
        step: dict[str, float] = {}
        for name, spec in M["regimes"].items():
            dev = value - path_value(spec["target"], p) - margin_season(A, p)
            if previous_index is None:
                prior_mean, prior_var = 0.0, sigma2
            else:
                k = period_index(p) - previous_index
                prior_mean = rho ** k * mean[name]
                prior_var = sigma2 * (1 - rho ** (2 * k)) + rho ** (2 * k) * var[name]
            error = dev - prior_mean
            step[name] = math.exp(-0.5 * error * error / (prior_var + se ** 2))
            if not se:
                mean[name], var[name] = dev, 0.0
            else:
                gain = prior_var / (prior_var + se ** 2)
                mean[name] = prior_mean + gain * error
                var[name] = (1 - gain) * prior_var
        previous_index = period_index(p)
        out.append(step)
    return out


def cap_shift(priors: dict[str, float], posterior: dict[str, float],
              limit: float) -> dict[str, float]:
    """Ограничивает сдвиг вероятностей за ОДНО наблюдение.

    Один отчёт не может превратить режим из маловероятного в основной: сетка
    строится на четырёх режимах, различающихся целью маржи на доли процентного
    пункта, и без предела правдоподобие схлопывает распределение на одном
    режиме от единственного полугодия.

    Порядок действий — как в книге: сдвиг каждого режима обрезается по
    пределу, распределение перенормируется, и если перенормировка сама вывела
    какой-то сдвиг за предел, весь сдвиг сжимается пропорционально. Обрезать
    без перенормировки нельзя (сумма перестанет быть единицей), а сжимать без
    предварительной обрезки — значит наказывать все режимы за один выбившийся.
    """
    if not limit:
        return posterior
    out = {r: priors[r] + max(-limit, min(limit, posterior[r] - priors[r])) for r in priors}
    total = sum(out.values()) or 1.0
    out = {r: v / total for r, v in out.items()}
    worst = max(abs(out[r] - priors[r]) for r in priors)
    if worst > limit + 1e-12:
        out = {r: priors[r] + limit / worst * (out[r] - priors[r]) for r in priors}
    return out


def regime_table(A: dict) -> dict[str, dict[str, float]]:
    """P(режим | мир) после учёта наблюдений. Без наблюдений — априорная A-P2.

    Наблюдения учитываются ПОСЛЕДОВАТЕЛЬНО, и предел сдвига действует после
    каждого: иначе десять отчётов подряд сдвинули бы вероятности ровно
    настолько же, насколько один.
    """
    limit = float(A["joint"]["regime_update"]["max_shift_pp"])
    table = {}
    for world, priors in A["joint"]["regime_given_world"].items():
        current = dict(priors)
        for like in regime_likelihoods(A):
            norm = sum(current[r] * like[r] for r in current)
            posterior = {r: current[r] * like[r] / norm for r in current}
            current = cap_shift(current, posterior, limit)
        table[world] = current
    return table


def regime_unconditional(A: dict) -> dict[str, float]:
    """Безусловные вероятности режимов при аналитических весах миров."""
    J, table = A["joint"], regime_table(A)
    return {r: sum(J["world_prob"][w] * table[w][r] for w in J["world_prob"])
            for r in A["margin"]["regimes"]}


# ------------------------------------------------------------------ сетка


def build_grid(A: dict) -> list[GridCell]:
    """36 клеток. Вероятность = P(мир) × P(режим | мир, набл.) × P(capex | режим).

    Уровень capex зависит от режима (книга 1.2, Р4а): независимые оси
    порождали клетку «стресс × высокий capex» с EV около нуля — при марже
    4 % денег на цикл редизайна в 8 лет нет, в стрессе вложения режут.

    Наблюдения маржи двигают только вероятности режимов внутри каждого мира;
    веса миров меняются исключительно макро-данными (A-P2u).
    """
    J = A["joint"]
    table = regime_table(A)
    cells: list[GridCell] = []
    # Траектории книги разбираются один раз на сетку, а не на каждую из 36
    # клеток (`memoized_paths`): полоса неопределённости 1.4 — 2 000 сеток.
    with memoized_paths():
        for world, p_world in J["world_prob"].items():
            for regime, p_regime in table[world].items():
                for capex, p_capex in J["capex_prob_given_regime"][regime].items():
                    cell = Cell.build(A, world, regime, capex)
                    cells.append(GridCell(cell, p_world * p_regime * p_capex, run_cell(A, cell)))
    total = sum(c.probability for c in cells)
    for c in cells:
        c.probability /= total
    return cells


def summarize(cells: Iterable[GridCell], key: str = "price_floor") -> tuple[float, dict[float, float]]:
    """Среднее и перцентили по набору клеток.

    Вероятности НОРМИРУЮТСЯ на сумму по набору: перцентили считаются внутри
    того, что передали. Без этого подмножество (например, один мир с весом
    0,45) никогда не добиралось до медианы, и функция падала на отсутствующем
    ключе — ровно так и случилось при переходе к структурной оценке по мирам.
    """
    cells = list(cells)
    total = sum(c.probability for c in cells)
    if not cells or total <= 0:
        zero = dict.fromkeys(PERCENTILES, 0.0)
        return 0.0, zero
    mean = sum(c.probability * getattr(c.result, key) for c in cells) / total
    acc, percentiles, targets = 0.0, {}, list(PERCENTILES)
    for c in sorted(cells, key=lambda c: getattr(c.result, key)):
        acc += c.probability / total
        for t in list(targets):
            if acc >= t - 1e-12:
                percentiles[t] = getattr(c.result, key)
                targets.remove(t)
    last = max(cells, key=lambda c: getattr(c.result, key))
    for t in targets:                 # хвост округлений: добираем максимумом
        percentiles[t] = getattr(last.result, key)
    return mean, percentiles


@dataclass(frozen=True)
class Layer:
    """Слой = один набор весов на одних и тех же 36 клетках.

    По слою считаются V0 = E[EV] и D = E[требований], и уже из них — три
    числа: внутренняя стоимость (V0 − D без пола), заголовочная оценка
    (отображение книги, max(V0 − D, 0)·(1 − g)/акции) и «старый метод»
    (среднее цен клеток с полом) для преемственности.
    """

    name: str
    title: str
    v0: float
    claims: float
    intrinsic: float
    headline: float
    """Заголовочная оценка слоя, ₽/акцию, — отображением книги (`model.mapping`):
    max(V0 − D, 0)·(1 − g)·1000/акции."""
    old_method: float
    mean_without_floor: float
    p10: float
    p25: float
    p50: float
    p75: float
    p90: float
    p_equity_nonpositive: float
    p_above_market: float
    implied_creditor_loss_old: float
    """E[max(D − EV, 0)] по клеткам — недостача против требований «старого
    метода» (справочно)."""
    mapping: tuple = ()
    """Параметры отображения V0 → капитал слоя (`model.mapping.layer_mapping`)."""

    @property
    def mean(self) -> float:
        """Заголовочная оценка слоя под старым именем — для контракта выпуска."""
        return self.headline


LAYER_TITLES = {
    "macro_neutral": "при рыночных ставках как есть",
    "market_implied": "веса миров, вменённые рынком облигаций",
    "analytical": "аналитические веса миров",
}


def layer_stats(A: dict, cells: list[GridCell], name: str) -> Layer:
    """Сворачивает набор взвешенных клеток в слой.

    ПОЧЕМУ НЕ СРЕДНЕЕ ЦЕН КЛЕТОК С ПОЛОМ. Среднее от max(E, 0) по клеткам —
    это пол, выписанный на КАЖДУЮ клетку отдельно, то есть предположение,
    что в каждом исходе кредиторы теряют ровно столько, сколько не хватает
    акционеру. Здесь отображение одно — на ожидаемую стоимость активов слоя:
    max(V0 − D, 0) (внутренняя стоимость, DESIGN D4).
    """
    V, F = A["valuation"], A["facts"]
    shares, governance = F["shares_out_mln"], V.get("governance_discount", 0.0)
    # `headline_block` проверяет ключи блока и метод печати: опечатка —
    # отказ, а не молчаливое умолчание.
    headline_block(A)

    total = sum(c.probability for c in cells) or 1.0
    v0 = sum(c.probability * c.result.ev for c in cells) / total
    claims = sum(c.probability * c.result.claims for c in cells) / total

    per_share = 1000.0 / shares
    raw_intrinsic = v0 - claims
    intrinsic = (raw_intrinsic * (1 - governance) if raw_intrinsic > 0 else raw_intrinsic) * per_share
    # Отображение V0 → капитал слоя — отображением книги (`layer_mapping`):
    # max(V0 − D, 0); дисконт за управление — после отображения.
    mapping = layer_mapping(A, claims)
    headline = mapped_equity(mapping, v0) * (1 - governance) * per_share

    mean_floor, pct = summarize(cells, "price_floor")
    mean_plain, _ = summarize(cells, "price")

    # Вменённые потери кредиторов: сколько стоимости активов НЕ достаётся им
    # при каждом методе. Это и есть проверка против котировок облигаций.
    # «Старый метод» — E[max(D − EV, 0)] по клеткам: дисконт за управление —
    # передача внутри акционеров, кредиторам он ничего не даёт.
    shortfall = sum(c.probability * max(c.result.claims - c.result.ev, 0.0)
                    for c in cells) / total
    return Layer(
        name=name, title=LAYER_TITLES.get(name, name),
        v0=v0, claims=claims, intrinsic=intrinsic, headline=headline,
        old_method=mean_floor, mean_without_floor=mean_plain,
        p10=pct[0.10], p25=pct[0.25], p50=pct[0.50], p75=pct[0.75], p90=pct[0.90],
        p_equity_nonpositive=sum(c.probability for c in cells if c.equity <= 0) / total,
        p_above_market=sum(c.probability for c in cells
                           if c.result.price_floor > A["market"]["price"]) / total,
        implied_creditor_loss_old=shortfall, mapping=mapping,
    )


def layer_variants(A: dict, cells: list[GridCell]) -> dict[str, list[GridCell]]:
    """Три слоя справедливой цены — разные веса на одних и тех же клетках.

    Рыночная бескупонная кривая — это смесь дисконт-факторов разных миров по
    риск-нейтральным весам. Значит, спор с рынком — это спор о ВЕСАХ, а не
    о том, по какой кривой дисконтировать. Отсюда три набора весов вместо
    гибридной кривой (решение A-V5).
    """
    J = A["joint"]
    table, unconditional = regime_table(A), regime_unconditional(A)
    capex_probs = J["capex_prob_given_regime"]

    def reweighted(world_prob=None, only=None) -> list[GridCell]:
        out = []
        for c in cells:
            w, r, cx = c.cell.world, c.cell.margin_regime, c.cell.capex
            if only:
                prob = unconditional[r] * capex_probs[r][cx] if w == only else 0.0
            else:
                prob = world_prob[w] * table[w][r] * capex_probs[r][cx]
            if prob > 0:
                out.append(GridCell(c.cell, prob, c.result))
        total = sum(c.probability for c in out)
        for c in out:
            c.probability /= total
        return out

    return {
        "analytical": cells,
        "market_implied": reweighted(world_prob=J["world_prob_market_implied"]),
        "macro_neutral": reweighted(only=J["macro_neutral_world"]),
    }


def layers(A: dict, cells: list[GridCell]) -> dict[str, Layer]:
    return {name: layer_stats(A, cs, name)
            for name, cs in layer_variants(A, cells).items()}


def layer_mix(A: dict, layer_map: dict[str, Layer], attr: str) -> dict[str, float]:
    """Три точки одного числа слоя, как печатаемый диапазон: низ — слой «рыночные
    ставки как есть», верх — «свой макро-взгляд», центр — низ + λ·(верх − низ)."""
    low, high = getattr(layer_map["macro_neutral"], attr), getattr(layer_map["analytical"], attr)
    return dict(low=low, central=low + own_macro_confidence(A) * (high - low), high=high)


def layer_weights(A: dict, cells: list[GridCell]) -> dict[str, dict[tuple, float]]:
    """Вероятности клеток КАЖДОГО слоя — те самые, с которыми считался слой.

    Существует ради сверки с контрольной моделью. Прежде сверка весов
    ПЕРЕСЧИТЫВАЛА формулу книги в `ops/tools/publish_reports.py`
    (`_engine_weights`), то есть сравнивала две записи одной формулы, а не
    формулу с тем, что применило ядро: на мутантах N12 и X11 расхождение
    оставалось 0,00 при сдвинутом центре — тест был тавтологичен (аудит
    третьей итерации, B1).

    Значения берутся из `layer_variants` — той же функции, которой считает
    `layers`, — поэтому «веса ядра» здесь не могут разойтись с весами,
    которыми ядро взвешивало клетки.
    """
    return {name: {(c.cell.world, c.cell.margin_regime, c.cell.capex): c.probability
                   for c in cs}
            for name, cs in layer_variants(A, cells).items()}


@dataclass(frozen=True)
class LayerVsMarket:
    """Сравнение слоя с рынком на уровне EV (эталон: `fair_value.ev_comparison`).

    `v_star` — книга 1.4: стоимость активов, при которой ТА ЖЕ функция
    «EV → цена» слоя (страйк K, σ, срок, дисконт за управление) даёт рыночную
    цену. «Капитализация + D» (`market_implied_ev`) — прежнее сравнение: оно
    приравнивает опцион акционера к внутренней стоимости и при k > 0 и σ > 0
    с отображением V0 → цена не согласовано.
    """

    v0: float
    claims: float
    market_cap: float
    market_implied_ev: float
    gap_pct: float
    rub_per_share_per_1pct_ev: float
    market_ev_to_equity: float
    v_star: float
    gap_vs_v_star: float


@dataclass(frozen=True)
class CenterEV:
    """EV ПЕЧАТАЕМОГО центра против рынка (книга 1.4, эталон: `center_ev`).

    Активы обоих слоёв умножаются на общий множитель x; `v_star` — V0 центра
    (λ-смесь V0 слоёв), умноженный на тот x, при котором центр равен рыночной
    цене. `rub_per_1pct_ev` — сколько рублей печатаемого центра стоит 1 %
    стоимости бизнеса: центр(1,01) − центр(1).
    """

    v0: float
    v_star: float
    gap_vs_v_star: float
    rub_per_1pct_ev: float


@dataclass(frozen=True)
class RatesView:
    """Вклад собственного взгляда на инфляцию и ставки: верх минус низ диапазона (₽) и
    разность V0 слоёв «свой макро-взгляд» и «рыночные ставки как есть» (млрд ₽)."""

    rub: float
    v0: float


@dataclass(frozen=True)
class FairValue:
    low: float
    central: float
    high: float
    printed_low: float
    printed_central: float
    printed_high: float
    own_macro_confidence: float
    grid_median: float
    modal_world: str
    modal_world_price: float
    market: float
    market_percentile: float
    p_equity_nonpositive: float
    ev_market_implied: float
    ev_model: float
    ev_gap: float
    rub_per_ev_percent: float
    ev_comparison: dict
    """{"analytical" | "macro_neutral": LayerVsMarket}."""
    center_ev: CenterEV
    rates_view: RatesView
    method: str = "intrinsic"


# --------------------------------------------- обращение «EV → цена» (1.4)


def headline_at(A: dict, layer: Layer, v0: float) -> float:
    """Заголовочная оценка слоя при ПОДМЕНЁННОЙ стоимости активов v0, ₽/акцию.

    Отображение — слоя (`layer.mapping`: D у внутренней стоимости; K, σ, T у
    структурной); дисконт за управление и число акций — книги.
    """
    governance = A["valuation"].get("governance_discount", 0.0)
    return (mapped_equity(layer.mapping, v0) * (1 - governance) * 1000.0
            / A["facts"]["shares_out_mln"])


def solve_v0(target_price: float, mapping: tuple, governance: float, shares: float) -> float:
    """V0, при котором заголовочная оценка слоя равна `target_price` (V*).

    Та же численная процедура, что у 850oa, при любом методе: бисекция на
    [1e-6; 50·K], 200 шагов (K — масштаб требований отображения: D у
    внутренней стоимости, страйк у структурной), оценка монотонна по V0. У
    внутренней стоимости при цене > 0 решение совпадает с замкнутой формой
    V* = E/(1 − g) + D до шага бисекции (тест).
    """
    lo, hi = 1e-6, 50.0 * mapping_scale(mapping)
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if mid <= lo or mid >= hi:
            break                     # отрезок в одно ulp: дальнейшие шаги его не меняют
        call = mapped_equity(mapping, mid)
        if call * (1 - governance) * 1000 / shares < target_price:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def solve_increasing(f, lo: float, hi: float, it: int = 200) -> float:
    """Корень возрастающей функции f на [lo; hi] бисекцией (эталон: `solve_increasing`).

    Концы расширяются, если корень снаружи: нижний — делением пополам (или
    шагом −1 у неположительного), верхний — удвоением (или шагом +1).
    """
    for _ in range(60):
        if f(lo) <= 0:
            break
        lo = lo / 2.0 if lo > 0 else lo - 1.0
    for _ in range(60):
        if f(hi) >= 0:
            break
        hi = hi * 2.0 if hi > 0 else hi + 1.0
    for _ in range(it):
        mid = 0.5 * (lo + hi)
        if mid <= lo or mid >= hi:
            break                     # отрезок в одно ulp: дальнейшие шаги его не меняют
        if f(mid) < 0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def center_ev_at(A: dict, neutral: Layer, own: Layer, lam: float, market: float) -> CenterEV:
    """EV центра при весе своего взгляда `lam` против рыночного V* (книга 1.4).

    Активы обоих слоёв умножаются на общий множитель x; x* — тот, при котором
    центр `низ + λ·(верх − низ)` равен рыночной цене. Отдельной функцией — чтобы
    выпуск мог напечатать строку «EV против V*» и при λ, отличном от книги
    (ползунок витрины), ТЕМ ЖЕ расчётом: при λ книги `fair_value` зовёт её же,
    и число выпуска не меняется ни в одном знаке.
    """
    def center_at(x: float) -> float:
        lo_ = headline_at(A, neutral, neutral.v0 * x)
        hi_ = headline_at(A, own, own.v0 * x)
        return lo_ + lam * (hi_ - lo_)

    x_star = solve_increasing(lambda x: center_at(x) - market, *CENTER_EV_SEARCH)
    v0_lam = neutral.v0 + lam * (own.v0 - neutral.v0)
    return CenterEV(v0=v0_lam, v_star=v0_lam * x_star, gap_vs_v_star=1.0 / x_star - 1.0,
                    rub_per_1pct_ev=center_at(1.01) - center_at(1.0))


def _round_to(value: float, step: float) -> float:
    return round(value / step) * step


def fair_value(A: dict, cells: list[GridCell], layer_map: dict[str, Layer]) -> FairValue:
    """Печатаемый результат: [нижняя; верхняя] с центром по λ.

    Границы — ЗАГОЛОВОЧНЫЕ оценки двух слоёв (методом книги), а не средние
    цен клеток. Нижняя
    отвечает на вопрос «сколько стоит акция, если рынок облигаций прав»,
    верхняя — «если прав собственный макро-взгляд». Центр между ними с весом
    λ; λ — это вес собственного взгляда, а не результат какой-либо процедуры
    вроде Блэка — Литтермана, и так и должно быть написано на экране.

    Печатаемые числа округляются до 50 ₽. Точность до рубля здесь была бы
    ложной: размах центра только по поддерживающему capex — около 1 600 ₽.
    """
    J, V = A["joint"], A["valuation"]
    lam = own_macro_confidence(A)
    head = headline_block(A)                    # метод печати книги
    low = layer_map["macro_neutral"].headline
    high = layer_map["analytical"].headline
    central = low + lam * (high - low)
    step = float(head.get("print_step", 50.0))

    modal = max(J["world_prob"], key=J["world_prob"].get)
    market = A["market"]["price"]
    shares = A["facts"]["shares_out_mln"]

    # Сравнение с рынком на уровне EV, а не цены акции. При рычаге ~6× по EV
    # разница в 5 % стоимости активов меняет цену акции на треть, поэтому
    # спор модели с рынком на уровне акции выглядит куда драматичнее, чем он
    # есть на уровне бизнеса.
    ev_market = market * shares / 1000.0 + layer_map["analytical"].claims
    ev_model = layer_map["analytical"].v0

    # V* каждого слоя — обращение той же функции «EV → цена».
    governance = V.get("governance_discount", 0.0)
    cap = market * shares / 1000.0
    comparison = {}
    for name in ("analytical", "macro_neutral"):
        L = layer_map[name]
        ev_mkt = cap + L.claims
        v_star = solve_v0(market, L.mapping, governance, shares)
        comparison[name] = LayerVsMarket(
            v0=L.v0, claims=L.claims, market_cap=cap, market_implied_ev=ev_mkt,
            gap_pct=L.v0 / ev_mkt - 1, rub_per_share_per_1pct_ev=ev_mkt / 100.0 * 1000 / shares,
            market_ev_to_equity=ev_mkt / cap, v_star=v_star, gap_vs_v_star=L.v0 / v_star - 1)

    neutral, own = layer_map["macro_neutral"], layer_map["analytical"]
    center_ev = center_ev_at(A, neutral, own, lam, market)
    rates_view = RatesView(rub=high - low, v0=own.v0 - neutral.v0)

    return FairValue(
        low=low, central=central, high=high,
        printed_low=_round_to(low, step), printed_central=_round_to(central, step),
        printed_high=_round_to(high, step),
        own_macro_confidence=lam,
        grid_median=layer_map["analytical"].p50,
        modal_world=modal,
        modal_world_price=world_price(A, cells, modal),
        market=market,
        market_percentile=sum(c.probability for c in cells if c.price_floor <= market),
        p_equity_nonpositive=layer_map["analytical"].p_equity_nonpositive,
        ev_market_implied=ev_market,
        ev_model=ev_model,
        ev_gap=(ev_model / ev_market - 1) if ev_market else 0.0,
        rub_per_ev_percent=ev_market / 100.0 * 1000.0 / shares,
        method=head["method"], ev_comparison=comparison, center_ev=center_ev,
        rates_view=rates_view,
    )


def crosscheck_peers(A: dict) -> list[tuple[str, float]]:
    """Аналоги сверки мультипликатором — данными книги (`market.peers`).

    Каждый аналог — {key, name, ev_ebitda, subject?}; `subject: true` — сама
    компания: её мультипликатор справочный и в сверку не входит. Порядок —
    книги.
    """
    return [(p["name"], p["ev_ebitda"]) for p in A["market"]["peers"] if not p.get("subject")]


def peer_crosscheck(A: dict, layer_map: dict[str, Layer]) -> dict:
    """Независимая сверка аналогами — НЕ вход модели (эталон: `peer_crosscheck`).

    V0 = мультипликатор аналога × EBITDA за 12 месяцев отчётной даты против D
    слоя «свой макро-взгляд»; заголовочная оценка — та же функция, что у
    печатаемой (`model.mapping`), дисконт за управление только у положительной
    внутренней стоимости.
    """
    V, F, L = A["valuation"], A["facts"], layer_map["analytical"]
    governance, shares = V.get("governance_discount", 0.0), F["shares_out_mln"]
    ebitda = F["anchor"]["ebitda_ltm"]
    claims = L.claims

    def per_share(x: float) -> float:
        return (x * (1 - governance) if x > 0 else x) * 1000 / shares

    rows = []
    mapping = layer_mapping(A, claims)
    for name, multiple in crosscheck_peers(A):
        v0 = multiple * ebitda
        rows.append(dict(peer=name, multiple=multiple, v0=v0, d=claims,
                         intrinsic=per_share(v0 - claims),
                         headline=per_share(mapped_equity(mapping, v0))))
    cap = A["market"]["price"] * shares / 1000.0
    return dict(ebitda_ltm=ebitda, d=claims,
                governance_discount=governance, peers=rows,
                model_v0_multiple={k: layer_map[k].v0 / ebitda for k in layer_map},
                market_implied_ev_multiple=(cap + claims) / ebitda)


def world_price(A: dict, cells: list[GridCell], world: str) -> float:
    """Структурная оценка одного мира — тем же способом, что и слой.

    Считается ОДИН опцион на ожидаемые активы мира, а не среднее опционов по
    его клеткам. Разница не косметическая: среднее выпуклой функции больше
    функции от среднего, и усреднение по клеткам завышало цену мира в
    несколько раз (N: 3 046 против 2 288 ₽). Тот же аргумент, по которому
    заголовочная оценка перестала быть средним цен клеток с полом.
    """
    subset = [c for c in cells if c.cell.world == world]
    if not subset:
        return 0.0
    return layer_stats(A, subset, f"world:{world}").headline


def weights_from_grid(A: dict, cells: list[GridCell], named: dict[str, CellResult]) -> dict[str, float]:
    """Веса именованных сценариев выводятся из сетки, а не задаются руками:
    каждая клетка относится к ближайшему по EV сценарию."""
    out = {k: 0.0 for k in named}
    for c in cells:
        nearest = min(named, key=lambda k: abs(named[k].ev - c.result.ev))
        out[nearest] += c.probability
    return out


# --------------------------------------------- разложение разброса


def variance_breakdown(cells: list[GridCell], key: str = "price_floor") -> dict:
    """Среднее и стандартное отклонение величины по сетке, доля межгрупповой
    дисперсии по каждой оси и средние групп (оси — мир, режим, capex; группы —
    в порядке первого появления в сетке)."""
    values = [(c.probability, getattr(c.result, key)) for c in cells]
    mean = sum(p * v for p, v in values)
    total = sum(p * (v - mean) ** 2 for p, v in values)
    share, group_mean = {}, {}
    for axis in ("world", "margin_regime", "capex"):
        groups: dict[str, list[tuple[float, float]]] = {}
        for c in cells:
            groups.setdefault(getattr(c.cell, axis), []).append((c.probability, getattr(c.result, key)))
        between, means = 0.0, {}
        for name, members in groups.items():
            weight = sum(p for p, _ in members)
            means[name] = sum(p * v for p, v in members) / weight
            between += weight * (means[name] - mean) ** 2
        share[axis] = between / total if total > 0 else 0.0
        group_mean[axis] = means
    return dict(mean=mean, sd=total ** 0.5, share=share, group_mean=group_mean)


def variance_decomposition(cells: list[GridCell], key: str = "price_floor") -> dict[str, float]:
    """Доля межгрупповой дисперсии по каждой оси сетки.

    Это карта того, куда направлять внимание и опережающие индикаторы:
    там, где дисперсия, там и ценность прогноза.
    """
    share = variance_breakdown(cells, key)["share"]
    return {axis: share[axis] for axis in ("margin_regime", "capex", "world")}


# ------------------------------------------- таблица «что даст отчёт»


def report_period(A: dict, period: str | None = None) -> str:
    """Полугодие «ближайшего отчёта»: названное, `demo_period` книги или первое прогнозное.

    Без `demo_period` прежде бралось «2026H2» литералом. После перезаякоривания
    книги на отчёт FY2026 это ЗАКРЫТОЕ полугодие: таблица «что даст отчёт»
    молча считала бы замену уже внесённого факта демонстрационным значением
    (ревью потока R1, аудит G1-exam №8). Теперь по умолчанию — первое
    прогнозное полугодие книги (`meta.first_period`), а закрытое полугодие —
    отказ, названный или взятый из книги одинаково: забытый `demo_period` в
    перезаякоренной книге должен остановить выпуск, а не напечатать таблицу.
    """
    first = A["meta"]["first_period"]
    period = period or A["joint"]["regime_update"].get("demo_period") or first
    if period_index(period) < period_index(first):
        raise BookError(
            f"«что даст отчёт»: полугодие {period} уже закрыто — первое прогнозное "
            f"полугодие книги {first}. После перезаякоривания `joint.regime_update."
            "demo_period` переставляется на первое прогнозное полугодие "
            "(ops/tools/reanchor.py делает это сам).")
    return period


def next_report_table(A: dict, values: Iterable[float] | None = None,
                      period: str | None = None, se: float = 0.0) -> list[dict]:
    """Что даст ближайший отчёт: маржа полугодия → вероятности режимов → цена.

    Главная таблица экрана «Ближайший отчёт»: она переводит прогноз слоя
    индикаторов прямо в рубли на акцию и показывает, сколько стоит точность.
    Полугодие — `report_period`.
    """
    period = report_period(A, period)
    values = values if values is not None else A["joint"]["regime_update"].get("demo_values", [])

    rows = []
    for value in values:
        trial = with_observation(A, period, {"value": value, "se": se} if se else value)
        cells = build_grid(trial)
        lm = layers(trial, cells)
        fv = fair_value(trial, cells, lm)
        probs = regime_unconditional(trial)
        rows.append(dict(
            period=period, margin=value, probabilities=probs,
            low=fv.low, central=fv.central, high=fv.high,
            p_equity_nonpositive=lm["analytical"].p_equity_nonpositive,
            old_method_central=layer_mix(trial, lm, "old_method")["central"],
        ))
    return rows


def with_observation(A: dict, period: str, observation) -> dict:
    """Копия книги с ещё ОДНИМ наблюдением маржи — поверх уже внесённых.

    Книга 1.4: новое наблюдение ДОПОЛНЯЕТ факты, а не заменяет их. Прежде
    таблица «что даст отчёт» подставляла наблюдение вместо всего списка, и
    после первого вышедшего отчёта строка «что даст следующий» считалась так,
    будто первого не было.
    """
    trial = copy.deepcopy(A)
    update = trial["joint"].setdefault("regime_update", {})
    update["observations"] = {**(update.get("observations") or {}), period: observation}
    return trial


def next_report_neutral(A: dict, period: str | None = None) -> dict:
    """Нейтральная маржа ближайшего отчёта (книга 1.4, эталон: `next_report_neutral`).

    Факт маржи периода, при котором ПЕЧАТАЕМЫЙ центр не меняется, — ожидание
    модели, а не цель модального режима. Бисекция `solve_increasing` на
    [3 %; 7 %], 40 шагов, как в эталоне: центр монотонно растёт с маржой.
    Полугодие — `report_period`.
    """
    period = report_period(A, period)
    cells = build_grid(A)
    base = fair_value(A, cells, layers(A, cells)).central

    def gap(margin: float) -> float:
        trial = with_observation(A, period, margin)
        trial_cells = build_grid(trial)
        return fair_value(trial, trial_cells, layers(trial, trial_cells)).central - base

    return dict(period=period, central=base,
                margin=solve_increasing(gap, *NEUTRAL_MARGIN_SEARCH, it=NEUTRAL_MARGIN_STEPS))
