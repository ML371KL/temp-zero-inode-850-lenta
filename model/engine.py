"""Движок: собирает книгу, сетку, слои и отчётные надстройки в один выпуск.

Один прогон = всё, что нужно дашборду и проверкам. Расчёт 36 клеток занимает
доли секунды, поэтому сетка считается целиком каждым выпуском, а не выборочно.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import date

from model.book import (BookError, Cell, book, interp_curve, named_cells, path_parent, path_value,
                        validate_book)
from model.checks import (RELEASE_LABEL, Finding, GateSummary, check_book_is_current,
                          check_gates, check_guidance, check_invariants,
                          check_pending_gates_are_dated, check_worlds_are_monotone_in_rates,
                          gate_corridors, summarize_gates)
from model.core import CellResult, run_cell
from model.financing import (credit_limit, initial_financing_state, rate_sensitivity,
                             refinancing_wall)
from model.grid import (
    FairValue,
    GridCell,
    Layer,
    build_grid,
    fair_value,
    layer_mix,
    layers,
    next_report_table,
    regime_table,
    regime_unconditional,
    summarize,
    variance_decomposition,
    weights_from_grid,
    world_price,
)

def valuation_date(A: dict) -> date:
    """Дата оценки — ИЗ КНИГИ, а не литералом в коде.

    Расхождение было реальным: книга говорила 18.09.2026, код — 20.09.2026.
    Два дня разницы меняют долю прошедшего полугодия и, через неё, цену.
    """
    return date.fromisoformat(A["meta"]["valuation_date"])


@dataclass
class Release:
    """Один выпуск."""

    book: dict
    cells: list[GridCell]
    layers: dict[str, Layer]
    fair_value: FairValue
    named: dict[str, CellResult]
    named_weights: dict[str, float]
    variance_price: dict[str, float]
    variance_ev: dict[str, float]
    regime_unconditional: dict[str, float]
    regime_given_world: dict[str, dict[str, float]]
    next_report: list[dict]
    refinancing_wall: dict[int, float]
    rate_sensitivity: float
    findings: list[Finding] = field(default_factory=list)
    live: object | None = None
    gate_summary: list[GateSummary] = field(default_factory=list)

    @property
    def unexplained_gates(self) -> list[GateSummary]:
        """Гейты без действующего письменного объяснения. Блокируют выпуск."""
        return [g for g in self.gate_summary if g.blocking]

    @property
    def market(self) -> float:
        return self.book["market"]["price"]

    def world_prices(self) -> dict[str, float]:
        return {w: world_price(self.book, self.cells, w) for w in self.book["joint"]["world_prob"]}

    def grid_mean(self, key: str = "price_floor") -> float:
        return summarize(self.cells, key)[0]

    @property
    def blocking(self) -> list[Finding]:
        return [f for f in self.findings if f.blocking]


def _register_summary(A: dict) -> dict:
    """Стена рефинансирования и чувствительность к ставке по реестру долга.

    Реестр (`data/facts/debt_register.json`) — отчётный слой, оценку он не
    меняет: без него выпуск выходит с пустой стеной (P4b; блок `debt` выпуска
    называет причину в `debt.register`, канал процентов — `unavailable`).
    Отказ чтения — только данные (нет файла, не та схема), ошибка кода падает.
    """
    from model.financing import RegisterError

    try:
        tranches = initial_financing_state(valuation_date(A)).tranches
    except (OSError, KeyError, ValueError, RegisterError):
        return dict(refinancing_wall={}, rate_sensitivity=0.0)
    key = path_value(A["worlds"][A["joint"]["macro_neutral_world"]]["key_rate"],
                     A["meta"]["first_period"])
    return dict(refinancing_wall=refinancing_wall(tranches),
                rate_sensitivity=rate_sensitivity(tranches, key))


def run_release(A: dict | None = None, gates: bool = True, *,
                live: bool = False, store=None) -> Release:
    """Собирает выпуск. `live=True` подставляет собранные рыночные входы.

    По умолчанию выпуск считается на книге как есть — так его считают тесты,
    и так он воспроизводим. Конвейер вызывает с `live=True`: цена, дата
    оценки и кривая рыночного мира берутся из рядов, собранных сегодня.
    """
    A = A if A is not None else book()
    # Книга, пришедшая мимо `load_book` (подмена в памяти, копия выпуска), —
    # тоже с закрытыми ключами: опечатка не должна дойти до витрины молча.
    validate_book(A)
    live_report = None
    if live:
        from indicators.store import Store
        from model.live import apply_live_inputs

        A, live_report = apply_live_inputs(A, store or Store())
    cells = build_grid(A)
    layer_map = layers(A, cells)
    fv = fair_value(A, cells, layer_map)
    named = {name: run_cell(A, cell) for name, cell in named_cells(A).items()}

    findings: list[Finding] = []
    if gates:
        # И инварианты, и гейты проверяются на ВСЕХ 36 клетках.
        #
        # Первая итерация считала гейты на трёх именованных сценариях — то
        # есть на 3 клетках из 36 — и аудит показал, что на полной сетке они
        # срабатывают в 19 клетках с вероятностной массой 55 %. Смотреть на
        # три клетки и объявлять «гейты чисты» значит не проверять ничего.
        # Лимит кредитных линий читает факты с диска, поэтому считается один
        # раз на все 36 клеток, а не по клетке.
        limit = credit_limit(A)
        # Коридоры гейтов — данные (`gate_explanations.yaml`, поле `corridor`),
        # читаются один раз на все клетки.
        corridors = gate_corridors()
        for c in cells:
            findings += check_invariants(c.result, label=c.cell.key)
            findings += check_gates(A, c.result, label=c.cell.key, credit_limit=limit,
                                    corridors=corridors)
            findings += check_guidance(A, c.result, label=c.cell.key)
        # Гейт, которого по клетке не видно в принципе: монотонность по
        # стоимости денег — свойство мира, а не клетки, и до третьей итерации
        # она вызывалась только из теста, то есть проверяла книгу на момент
        # написания теста, а не выпуск, который уходит на витрину. (Флага
        # перекалибровки σ у внутренней стоимости нет — D4.)
        findings += check_worlds_are_monotone_in_rates(A)
        # Гейт «книга требует обновления» считается только на живом выпуске:
        # на книжных входах сдвигу кривой быть неоткуда, и в тестах он молчал
        # бы всегда.
        findings += check_book_is_current(A, live_report)
        # Сторож самого механизма ожидания: ключ в `PENDING_GATES` без срока
        # или с истёкшим сроком роняет сборку. Без этого «ожидание» было бы
        # выключателем проверки в одну строку (аудит третьей итерации, B4).
        findings += check_pending_gates_are_dated()

    return Release(
        book=A, cells=cells, layers=layer_map, fair_value=fv, named=named,
        named_weights=weights_from_grid(A, cells, named),
        variance_price=variance_decomposition(cells, "price_floor"),
        variance_ev=variance_decomposition(cells, "ev"),
        regime_unconditional=regime_unconditional(A),
        regime_given_world=regime_table(A),
        next_report=next_report_table(A),
        **_register_summary(A),
        findings=findings,
        live=live_report,
        gate_summary=summarize_gates(
            findings, {c.cell.key: c.probability for c in cells} | {RELEASE_LABEL: 1.0},
        ) if gates else [],
    )


# ------------------------------------------------- обратный DCF и суждения


def apply_override(A: dict, dotted: str, value, *, create: bool = False) -> None:
    """Точечная правка книги по пути вида `valuation.beta_u`.

    Значение `{'__shift__': d}` сдвигает всю траекторию на `d` — так книга
    описывает чувствительности к сдвигу кривой capex или маржи; `{'__scale__':
    f}` умножает число или все числа траектории на `f` (кроме `LT_from`) — ось
    вида `scale` (множитель capex открытия, темпа открытий, пути книги).

    Путь обязан существовать в книге (аудит 26.09.2026, п. 5.4): подмена
    незнакомого последнего ключа молча создавала новый ключ, который никто не
    читает, — «чувствительность» с Δ = 0 без ошибки. `create=True` — явное
    «создать ключ (и недостающие блоки)»: для параметра, которого в этой
    версии книги ещё нет. Строка списка адресуется селектором `[id]`
    (`bridge.items[put].amount`) и создана быть не может.
    """
    shift = isinstance(value, dict) and "__shift__" in value
    scale = isinstance(value, dict) and "__scale__" in value
    try:
        node, last = path_parent(A, dotted, create=create and not (shift or scale))
    except (KeyError, TypeError) as exc:
        raise BookError(f"подмена {dotted}: в книге нет такого пути ({exc}) — опечатка в пути "
                        "дала бы «чувствительность» без эффекта (новый параметр — create=True)"
                        ) from None
    if last not in node and not (create and not (shift or scale)):
        raise BookError(f"подмена {dotted}: в книге нет ключа {dotted} — "
                        "опечатка в пути дала бы «чувствительность» без эффекта "
                        "(новый параметр — create=True)")
    if shift:
        d, cur = value["__shift__"], node[last]
        node[last] = (cur + d if isinstance(cur, (int, float))
                      else {k: (v + d if k != "LT_from" else v) for k, v in cur.items()})
    elif scale:
        f, cur = value["__scale__"], node[last]
        node[last] = (cur * f if isinstance(cur, (int, float))
                      else {k: (v * f if k != "LT_from" else v) for k, v in cur.items()})
    else:
        node[last] = value


def with_overrides(A: dict, overrides: dict, *, create: bool = False) -> dict:
    trial = copy.deepcopy(A)
    for k, v in overrides.items():
        apply_override(trial, k, v, create=create)
    return trial


def central_price(A: dict) -> float:
    cells = build_grid(A)
    return fair_value(A, cells, layers(A, cells)).central


def solve_for_price(A: dict, target_price: float, dotted: str, lo: float, hi: float,
                    tol: float = 1.0, max_iter: int = 40) -> float | None:
    """Какое значение допущения оправдывает заданную цену (обратный DCF).

    Делением пополам по одному допущению при прочих равных. Возвращает None,
    если цена недостижима на отрезке: притворяться, что решение есть, нельзя.
    """
    f_lo = central_price(with_overrides(A, {dotted: lo})) - target_price
    f_hi = central_price(with_overrides(A, {dotted: hi})) - target_price
    if f_lo * f_hi > 0:
        return None
    for _ in range(max_iter):
        mid = (lo + hi) / 2
        f_mid = central_price(with_overrides(A, {dotted: mid})) - target_price
        if abs(f_mid) < tol:
            return mid
        if f_lo * f_mid <= 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


@dataclass(frozen=True)
class Judgement:
    key: str
    label: str
    low_label: str
    high_label: str
    price_low: float
    price_high: float
    scenario_low: float
    scenario_high: float
    # Книга 1.4: строка может двигать НЕСКОЛЬКО путей сразу (ось A-C0 —
    # цели LT всех четырёх режимов). `key` — первый из них, как `path` строки
    # эталона; полный список и род оси — для экрана, чтобы строка «общий
    # уровень маржи» не читалась как цель одного «стресса».
    paths: tuple = ()
    kind: str = "value"
    # Концы строки целиком — для таблицы книги (`model.book_results`): диапазон
    # точки [низ; верх], центр и верх («свой макро-взгляд») по среднему цен клеток с полом.
    range_low: tuple = ()
    range_high: tuple = ()
    old_method_low: float = 0.0
    old_method_high: float = 0.0
    grid_low: float = 0.0
    grid_high: float = 0.0

    @property
    def spread(self) -> float:
        return abs(self.price_high - self.price_low)


def judgement_table(A: dict | None = None, limit: int | None = 12) -> list[Judgement]:
    """Суждения книги и цена каждого — из блока `sensitivities` книги.

    Таблица не составляется руками: книга сама перечисляет, какие допущения
    двигают оценку и на каких границах их проверять. Сортировка по размаху —
    это и есть правило внимания: всё, что двигает центр меньше чем на 150 ₽
    (10 млрд ₽ капитала), не заслуживает усложнения модели. `limit=None` —
    все строки книги (выпуск с 1.4 печатает все: обрезка двенадцатью строками
    прятала дешёвые суждения вместе с тем, что они дешёвые).
    """
    # Импорт отложенный: `model.uncertainty` сам импортирует этот модуль
    # (`with_overrides`), и импорт на уровне модуля замкнул бы круг.
    from model.uncertainty import axis_overrides, axis_spec

    A = A if A is not None else book()
    out: list[Judgement] = []
    for spec in A.get("sensitivities", []):
        # Книга задаёт границы двумя способами: прямыми значениями
        # (`low`/`high`) и сдвигом всей траектории (`shift_low`/`shift_high`),
        # а с 1.4 — ещё и по НЕСКОЛЬКИМ путям сразу (`paths`: сдвиг целей LT
        # всех режимов, трафик всех состояний спроса). Концы строятся той же
        # функцией, что оси полосы неопределённости, — `axis_overrides` в
        # точках s = −1 и s = +1, как в эталоне книги. Прежняя развилка
        # читала только `path` и на строках с `paths` падала на KeyError.
        axis = axis_spec(spec)
        if axis["kind"] == "shift":
            bounds = ({"__shift__": axis["low"]}, {"__shift__": axis["high"]})
        elif axis["kind"] == "scale":
            bounds = (f"×{axis['low']:g}", f"×{axis['high']:g}")
        else:
            bounds = (axis["low"], axis["high"])
        prices, scenarios, ranges, old, grid = [], [], [], [], []
        for side in (-1.0, 1.0):
            trial = with_overrides(A, axis_overrides(A, spec, side))
            cells = build_grid(trial)
            layer_map = layers(trial, cells)
            fv = fair_value(trial, cells, layer_map)
            prices.append(fv.central)
            scenarios.append(run_cell(trial, named_cells(trial)["base"]).price)
            ranges.append((fv.low, fv.high))
            old.append(layer_mix(trial, layer_map, "old_method")["central"])
            grid.append(layer_map["analytical"].old_method)
        out.append(Judgement(
            # Ключ — ПЕРВЫЙ путь оси, как `path` строки в эталоне книги.
            key=axis["paths"][0], label=spec["name"],
            # Подпись конца, если книга её дала (`low_label`/`high_label`: у
            # строки A-W3 концы — словари миров, и формат словаря растягивал
            # таблицу суждений на экране во всю ширину), иначе — значение.
            low_label=spec.get("low_label") or _fmt(bounds[0]),
            high_label=spec.get("high_label") or _fmt(bounds[1]),
            price_low=prices[0], price_high=prices[1],
            scenario_low=scenarios[0], scenario_high=scenarios[1],
            paths=tuple(axis["paths"]), kind=axis["kind"],
            range_low=ranges[0], range_high=ranges[1],
            old_method_low=old[0], old_method_high=old[1],
            grid_low=grid[0], grid_high=grid[1],
        ))
    out.sort(key=lambda j: j.spread, reverse=True)
    return out[:limit]


def _fmt(value) -> str:
    """Человекочитаемое значение допущения для таблицы суждений.

    Словарь словарей — не редкость: вероятности capex заданы по режимам
    (`{режим: {уровень: p}}`). Раньше такой случай падал на форматировании
    вложенного словаря числом.
    """
    if isinstance(value, dict):
        if "__shift__" in value:
            return f"сдвиг {value['__shift__']:+.3g}"
        if any(isinstance(v, dict) for v in value.values()):
            return "; ".join(f"{k}: {_fmt(v)}" for k, v in value.items())
        if all(isinstance(v, (int, float)) for v in value.values()):
            return "/".join(f"{v:.0%}" for v in value.values())
        return "/".join(_fmt(v) for v in value.values())
    if isinstance(value, float) and abs(value) < 1:
        return f"{value:.4g}"
    if isinstance(value, (int, float)):
        return f"{value:g}"
    return str(value)
