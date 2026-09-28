"""Книга допущений: загрузка машинного файла и доступ к траекториям.

Книга (`data/assumptions/assumptions.yaml`) читается **целиком**.
Ни одно число из неё не переписывается в код руками: движок берёт всё оттуда,
и её таблицы (`results.json`) выпускает он же (`model/book_results.py`).

Формат траекторий книги: число, либо словарь с ключами периодов (`2026H2`),
годов (`2027`), `LT` (долгосрочное значение) и `LT_from` (год выхода на `LT`).
Между заданными годами — линейная интерполяция, после последнего года — сход
к `LT`. Семантика задана форматом книги, а не выбором реализации: и движок,
и контрольная модель обязаны читать её одинаково, иначе сверка потеряет смысл.
"""

from __future__ import annotations

import bisect
import datetime as _dt
import json
import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from model.book_schema import unknown_keys
from model.paths import BOOK_DIR
BOOK_YAML = BOOK_DIR / "assumptions.yaml"
BOOK_JSON = BOOK_DIR / "assumptions.json"


def load_book(path: Path | None = None) -> dict[str, Any]:
    """Читает книгу целиком. YAML — первичный формат, JSON — копия.

    Книга проверяется `validate_book`: незнакомый ключ блока, который ядро
    читает через `.get`, книга прежней версии и число вне границ — отказ.
    """
    path = path or BOOK_YAML
    if path.suffix == ".json":
        A = json.loads(path.read_text(encoding="utf-8"))
    else:
        try:
            import yaml
        except ImportError:  # pragma: no cover
            A = json.loads(BOOK_JSON.read_text(encoding="utf-8"))
        else:
            A = yaml.safe_load(path.read_text(encoding="utf-8"))
    validate_book(A)
    # Тождества у книги как она записана: подмены (оси полосы, обратный DCF)
    # двигают сами числа — дисконт за управление, плотность новой площади.
    check_governance_sum(A)
    check_effective_area_history(A)
    return A


@lru_cache(maxsize=1)
def book() -> dict[str, Any]:
    return load_book()


# ------------------------------------------------------ проверка ключей книги
#
# Ядро читает часть параметров книги через `.get(ключ, значение по умолчанию)`.
# Опечатка в таком ключе (`strike_markup_k` вместо `strike_premium`) молча дала
# бы умолчание, поэтому у этих блоков список ключей закрыт: незнакомый ключ —
# отказ, а не пропуск.

class BookError(ValueError):
    """Книга допущений не читается однозначно — считать по ней нельзя."""


# Обязательные ключи и блоки книги. Пропуск — отказ, а не умолчание: ядро
# читает их через `[...]`, и книга без ключа иначе упала бы на KeyError в
# середине расчёта или (у `.get` с умолчанием) молча посчиталась бы по другому
# правилу. Методы, которые есть в коде, — действующие; переключатель метода
# заводится в книге только тогда, когда метод действительно меняется.
REQUIRED_KEYS = (
    "meta.bridge_as_of",
    "facts.anchor", "facts.undrawn_credit_lines", "facts.shares_out_mln", "facts.segments",
    "valuation.headline", "valuation.headline.jump_guard", "valuation.terminal",
    "valuation.uncertainty", "valuation.uncertainty.median_draws",
    "valuation.distress", "capex.disposal_proceeds_pct", "capex.maintenance_area_share",
    "capex.segments", "revenue.segments", "revenue.ticket_lt_homogeneity", "joint.regime_update",
    "joint.growth_by_regime_override", "financing.spread_fair", "financing.rate_baskets",
    "margin.season_from_period", "margin.season_free_halves", "bridge.items",
)
_REQUIRED = tuple((dotted, tuple(dotted.split("."))) for dotted in REQUIRED_KEYS)
# Методы печати заголовка, которые знает ядро (`valuation.headline.method`):
# `intrinsic` — внутренняя стоимость max(V0 − D, 0)·(1 − g)/акции (DESIGN D4).
# Структурное отображение 850oa (колл Мертона) в этом репозитории не держится.
INTRINSIC = "intrinsic"
HEADLINE_METHODS = (INTRINSIC,)


def _missing(dotted: str) -> BookError:
    return BookError(f"книга: нет обязательного ключа {dotted}")


def _book_value(A: dict, dotted: str, keys: tuple) -> Any:
    """Значение по пути; нет ключа или пустой блок по дороге — отказ."""
    node = A
    for depth, key in enumerate(keys):
        if (not isinstance(node, dict) or key not in node
                or (node[key] is None and depth < len(keys) - 1)):
            raise _missing(dotted)
        node = node[key]
    return node


def _same(a: Any, b: Any) -> bool:
    """Равенство с типом: у флага 1 и True — разные значения."""
    return type(a) is type(b) and a == b


def refuse_incomplete(A: dict) -> None:
    """Отказ на книге без обязательного ключа или с незнакомым ключом закрытого блока.

    Зовут `validate_book` и ядро на входе клетки (`model.core.run_cell`): книга,
    пришедшая мимо `load_book` (подмена, словарь выпуска), не посчитается молча
    по неполным правилам. Раньше пропуска — незнакомые ключи закрытых блоков:
    опечатка (`strike_markup_k`) называется опечаткой, а не пропуском ключа.
    """
    for block, key, known in _CLOSED_BLOCKS:
        node = (A.get(block) or {}).get(key)
        if isinstance(node, dict):
            _refuse_unknown(node, known, f"{block}.{key}")
    for dotted, keys in _REQUIRED:
        if _book_value(A, dotted, keys) is None:
            raise _missing(dotted)


# Ключи заголовка: метод, шаг печати, диагностики, пороги скачка и гейта
# ограниченной ответственности.
HEADLINE_KEYS = frozenset({"method", "print_step", "diagnostics", "limited_liability",
                           "jump_guard"})
UNCERTAINTY_KEYS = frozenset({"draws", "seed", "quantiles", "axes", "median_draws",
                              "reverse_bounds", "median_refine"})
AXIS_KEYS = frozenset({"name", "path", "paths", "kind", "low", "high",
                       "shift_low", "shift_high", "low_label", "high_label"})
REVERSE_DCF_KEYS = frozenset({"name", "paths", "kind", "search", "range"})

# Опечатка в ключе однородности чека молча выключала бы правило Р9 (−514 ₽
# точки у 850oa): ключи этих блоков закрыты, обязательные ядро читает через `[...]`.
HOMOGENEITY_KEYS = frozenset({"reference_world", "ramp_from", "ramp_to"})
HOMOGENEITY_REQUIRED = ("reference_world", "ramp_from", "ramp_to")
REGIME_UPDATE_KEYS = frozenset({"sigma_pp", "sigma_range", "max_shift_pp",
                                "observations", "demo_period", "demo_values"})
REGIME_UPDATE_REQUIRED = ("sigma_pp", "max_shift_pp")
SPREAD_FAIR_KEYS = frozenset({"float", "fixed"})
SPREAD_FAIR_LEVELS = frozenset({"base", "stress"})
# Блок `valuation.distress` (пара A-T4): все три ключа обязательны.
DISTRESS_KEYS = frozenset({"cost_pct_ev", "net_leverage_trigger", "credit_limit_trigger"})
_CLOSED_BLOCKS = (
    ("valuation", "headline", HEADLINE_KEYS), ("valuation", "uncertainty", UNCERTAINTY_KEYS),
    ("valuation", "distress", DISTRESS_KEYS),
    ("revenue", "ticket_lt_homogeneity", HOMOGENEITY_KEYS),
    ("joint", "regime_update", REGIME_UPDATE_KEYS), ("financing", "spread_fair", SPREAD_FAIR_KEYS),
)

# Значения `kind` осей. Значение — тоже развилка через «всё остальное»:
# `reverse_dcf` сдвигает траекторию только при `kind == "shift"`, а любое
# другое слово читает как ЗАМЕНУ значением. Опечатка `shfit` у оси «сдвиг
# целей маржи LT» молча подставила бы в цели маржи сами числа отрезка поиска.
# `scale` — множитель пути или числа (s = 0 — ×1, концы — ×low/×high);
# `choice` — строковое значение (конец со стороны s, в центре — книга): срок
# присоединения `tax.acquired_nol.usable_from` (CONSISTENCY E11).
AXIS_KINDS = frozenset({"value", "shift", "dict", "scale", "choice"})
REVERSE_DCF_KINDS = frozenset({"value", "shift", "scale"})


def _refuse_unknown(block: dict, known: frozenset, where: str) -> None:
    unknown = sorted(set(block) - known)
    if unknown:
        raise BookError(
            f"книга: незнакомый ключ {where}: {', '.join(unknown)} "
            f"(известны: {', '.join(sorted(known))}). Ядро не угадывает опечатки — "
            "ключ с ошибкой молча дал бы значение по умолчанию.")


def _refuse_unknown_kind(axis: dict, known: frozenset, where: str) -> None:
    kind = axis.get("kind")
    if kind is not None and kind not in known:
        raise BookError(
            f"книга: {where}: kind = {kind!r} (известны: {', '.join(sorted(known))}). "
            "Незнакомое слово читалось бы как замена значением.")


def headline_block(A: dict) -> dict:
    """`valuation.headline` с проверкой ключей и метода печати (`HEADLINE_METHODS`)."""
    head = A["valuation"].get("headline")
    if head is None:
        raise _missing("valuation.headline")
    _refuse_unknown(head, HEADLINE_KEYS, "valuation.headline")
    if "method" not in head:
        raise _missing("valuation.headline.method")
    if not any(_same(head["method"], m) for m in HEADLINE_METHODS):
        raise BookError(f"книга: valuation.headline.method = {head['method']!r} (известны: "
                        f"{', '.join(HEADLINE_METHODS)}). Ядро не угадывает опечатки — "
                        "незнакомое слово не читается ни одним правилом")
    diagnostics = head.get("diagnostics", "median")
    if diagnostics != "median":
        raise BookError(f"книга: valuation.headline.diagnostics = {diagnostics!r} — диагностики "
                        "считаются только для печатаемой медианы (median)")
    return head


def headline_method(A: dict) -> str:
    """`valuation.headline.method` — `intrinsic` (другого метода ядро не знает)."""
    return headline_block(A)["method"]


def jump_guard(A: dict) -> dict:
    """`valuation.headline.jump_guard` — пороги защиты заголовка от скачка.

    {median_pct, v0_pct}: скачок печатаемой медианы больше `median_pct` (доля
    прошлой медианы) или V0 больше `v0_pct` допускается только при новой книге,
    новых фактах или записке выпуска. Пороги — книгой: у рычага Ленты 25 % цены
    850oa — это ≈16 % EV, защита почти не работала бы.
    """
    raw = headline_block(A).get("jump_guard")
    if raw is None:
        raise _missing("valuation.headline.jump_guard")
    if not isinstance(raw, dict):
        raise BookError(f"книга: valuation.headline.jump_guard — ожидается блок, а не {raw!r}")
    _refuse_unknown(raw, JUMP_GUARD_KEYS, "valuation.headline.jump_guard")
    _require(raw, sorted(JUMP_GUARD_KEYS), "valuation.headline.jump_guard")
    for key in sorted(JUMP_GUARD_KEYS):
        if not _is_number(raw[key]) or not 0.0 < raw[key] < 1.0:
            raise BookError(f"книга: valuation.headline.jump_guard.{key} = {raw[key]!r} — "
                            "ожидается доля в (0; 1)")
    return dict(median_pct=float(raw["median_pct"]), v0_pct=float(raw["v0_pct"]))


def limited_liability_rule(A: dict) -> dict | None:
    """`valuation.headline.limited_liability` — пороги совещательного гейта.

    {v0_to_d_min, max_share}: доля прогонов полосы, в которых V0 хотя бы одного
    из двух слоёв диапазона ниже `v0_to_d_min`·D, выше `max_share` — гейт
    «ограниченная ответственность существенна, нужна новая версия книги со
    сменой метода». Нет блока — гейта нет.
    """
    raw = headline_block(A).get("limited_liability")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise BookError(f"книга: valuation.headline.limited_liability — ожидается блок, а не {raw!r}")
    where = "valuation.headline.limited_liability"
    _refuse_unknown(raw, LIMITED_LIABILITY_KEYS, where)
    _require(raw, sorted(LIMITED_LIABILITY_KEYS), where)
    if not _is_number(raw["v0_to_d_min"]) or raw["v0_to_d_min"] <= 0.0:
        raise BookError(f"книга: {where}.v0_to_d_min = {raw['v0_to_d_min']!r} — ожидается "
                        "положительное отношение V0/D")
    if not _is_number(raw["max_share"]) or not 0.0 <= raw["max_share"] < 1.0:
        raise BookError(f"книга: {where}.max_share = {raw['max_share']!r} — ожидается доля в [0; 1)")
    return dict(v0_to_d_min=float(raw["v0_to_d_min"]), max_share=float(raw["max_share"]))


JUMP_GUARD_KEYS = frozenset({"median_pct", "v0_pct"})
LIMITED_LIABILITY_KEYS = frozenset({"v0_to_d_min", "max_share"})


def _require(block: dict, keys, where: str) -> None:
    missing = [k for k in keys if k not in block]
    if missing:
        raise BookError(f"книга: в {where} нет ключа {', '.join(missing)}")


def _refuse_unknown_axes(A: dict) -> None:
    """Незнакомые ключи и слова `kind` осей полосы, чувствительностей и обратного DCF."""
    U = (A.get("valuation") or {}).get("uncertainty")
    if isinstance(U, dict):
        for axis in U.get("axes", []):
            where = f"оси valuation.uncertainty «{axis.get('name')}»"
            _refuse_unknown(axis, AXIS_KEYS, where)
            _refuse_unknown_kind(axis, AXIS_KINDS, where)
    for axis in A.get("sensitivities", []) or []:
        where = f"строки sensitivities «{axis.get('name')}»"
        _refuse_unknown(axis, AXIS_KEYS, where)
        _refuse_unknown_kind(axis, AXIS_KINDS, where)
    for axis in A.get("reverse_dcf", []) or []:
        where = f"оси reverse_dcf «{axis.get('name')}»"
        _refuse_unknown(axis, REVERSE_DCF_KEYS, where)
        _refuse_unknown_kind(axis, REVERSE_DCF_KINDS, where)


def _validate_closed_blocks(A: dict) -> None:
    """Обязательные ключи однородности чека, обновления режимов и справедливых спредов."""
    where = "revenue.ticket_lt_homogeneity"
    H = A["revenue"]["ticket_lt_homogeneity"]
    _require(H, HOMOGENEITY_REQUIRED, where)
    if H["reference_world"] not in (A.get("worlds") or {}):
        raise BookError(f"книга: {where}.reference_world = {H['reference_world']!r} — такого мира нет")
    _require(A["joint"]["regime_update"], REGIME_UPDATE_REQUIRED, "joint.regime_update")
    where = "financing.spread_fair"
    S = A["financing"]["spread_fair"]
    _require(S, sorted(SPREAD_FAIR_KEYS), where)
    for kind in sorted(SPREAD_FAIR_KEYS):
        _refuse_unknown(S[kind], SPREAD_FAIR_LEVELS, f"{where}.{kind}")
        _require(S[kind], sorted(SPREAD_FAIR_LEVELS), f"{where}.{kind}")


def refuse_off_schema(A: dict) -> None:
    """Отказ на ключе, которого нет в закрытой схеме книги (`model/book_schema.py`)."""
    problems = unknown_keys(A)
    if problems:
        raise BookError(
            "книга: ключ вне схемы — " + "; ".join(problems[:5])
            + (f"; и ещё {len(problems) - 5}" if len(problems) > 5 else "")
            + ". Ядро не угадывает опечатки: ключ с ошибкой молча дал бы значение по "
            "умолчанию. Новый ключ регистрируется в model/book_schema.py тем же коммитом, "
            "что и код, который его читает.")


def validate_book(A: dict) -> None:
    """Громкий отказ на книге, которую ядро прочло бы не так, как она написана.

    Ключ вне закрытой схемы книги (`refuse_off_schema`); незнакомые ключи
    блоков, читаемых через `.get`, и пропуск обязательного ключа
    (`refuse_incomplete`); ключи и слова `kind` осей; обязательные ключи
    закрытых блоков, числа правил capex, издержки неустойчивости, дата
    кривых, метод и пороги заголовка (скачок, ограниченная ответственность);
    ключи диагностик медианы; факты якоря, периоды правил, терминал, корзины ставок, лестница
    дивидендов, сегменты сети, строки моста, необязательные траектории,
    запертые убытки, разложение дисконта за управление, квартальный слой.
    """
    refuse_off_schema(A)
    refuse_incomplete(A)
    _refuse_unknown_axes(A)
    _validate_closed_blocks(A)
    capex_network_rules(A)
    distress_rule(A)
    curve_date(A)
    headline_method(A)
    jump_guard(A)
    limited_liability_rule(A)
    median_draws(A)
    reverse_bounds(A)
    median_refine(A)
    anchor_facts(A)
    season_from_period(A)
    season_free_halves(A)
    meta_rules(A)
    terminal_rule(A)
    rate_baskets(A)
    dividend_ladder(A)
    dividend_timing(A)
    dividend_net_debt_basis(A)
    dividend_carry(A)
    segments(A)
    bridge_items(A)
    optional_paths(A)
    nwc_rules(A)
    acquired_nol(A)
    capex_tax_premium(A)
    governance_components(A)
    market_rules(A)
    guidance_rule(A)
    quarter_rules(A)


def curve_date(A: dict) -> str | None:
    """`meta.curve_as_of` — дата кривых миров (перекат по форвардам).

    Необязательна: без неё дата кривых — дата оценки того же словаря
    (`model.core.curve_as_of`). Если названа — только строкой ISO-даты: дата
    без кавычек в YAML читается объектом, опечатка молча сдвинула бы перекат.
    """
    stated = (A.get("meta") or {}).get("curve_as_of")
    if stated is None:
        return None
    try:
        if not isinstance(stated, str):
            raise ValueError
        _dt.date.fromisoformat(stated)
    except ValueError:
        raise BookError(f"книга: meta.curve_as_of — ожидается дата \"ГГГГ-ММ-ДД\", а не {stated!r}") from None
    return stated


def distress_rule(A: dict) -> dict:
    """Издержки финансовой неустойчивости (A-T4).

    Словарь {cost_pct_ev, net_leverage_trigger, credit_limit_trigger}: в
    клетке, где максимум ЧД/EBITDA по пути выше порога или (при
    `credit_limit_trigger`) валовой долг выходит за лимит линий, из EV клетки
    вычитается cost_pct_ev × max(EV, 0) — издержки только уменьшают стоимость
    (клетка с отрицательным EV остаётся как есть). Ключи закрыты и
    обязательны: пропуск или опечатка молча выключили бы правило.

    `credit_limit_trigger` решает и судьбу гейта `credit_lines`
    (`model.checks.check_gates`): лимит — суждение книги о том, ограничивает ли
    номинальная сумма «долг + линии» путь долга. При false (книга «Ленты» 1.0 с
    F1: номинальный лимит 30.06.2026 при выручке, растущей в 2,5 раза, —
    не ограничение; неустойчивость ловит порог ЧД/EBITDA) путь против лимита —
    справка (`results.json` → `checks.credit_lines`, флаг клетки на витрине), а
    не гейт и не издержки.
    """
    spec = A["valuation"].get("distress")
    if spec is None:
        raise _missing("valuation.distress")
    if not isinstance(spec, dict):
        raise BookError(f"книга: valuation.distress — ожидается блок, а не {spec!r}")
    _refuse_unknown(spec, DISTRESS_KEYS, "valuation.distress")
    missing = sorted(DISTRESS_KEYS - set(spec))
    if missing:
        raise BookError(f"книга: valuation.distress — нет ключей {', '.join(missing)}")
    cost, trigger = spec["cost_pct_ev"], spec["net_leverage_trigger"]
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not 0.0 <= cost < 1.0:
        raise BookError(f"книга: valuation.distress.cost_pct_ev = {cost!r} — ожидается доля в [0; 1)")
    if isinstance(trigger, bool) or not isinstance(trigger, (int, float)) or trigger <= 0.0:
        raise BookError(f"книга: valuation.distress.net_leverage_trigger = {trigger!r} — "
                        "ожидается положительное число (ЧД/EBITDA)")
    if not isinstance(spec["credit_limit_trigger"], bool):
        raise BookError("книга: valuation.distress.credit_limit_trigger — ожидается true/false, "
                        f"а не {spec['credit_limit_trigger']!r}")
    return dict(cost_pct_ev=float(cost), net_leverage_trigger=float(trigger),
                credit_limit_trigger=spec["credit_limit_trigger"])


def median_draws(A: dict) -> int:
    """`valuation.uncertainty.median_draws` — прогонов полосы в КАЖДОМ пересчёте
    медианы для диагностик (`headline_of`-медиана при подменённом суждении или
    новом наблюдении маржи), на общих случайных числах: seed и порядок те же,
    что у полосы."""
    raw = A["valuation"]["uncertainty"].get("median_draws")
    if raw is None:
        raise _missing("valuation.uncertainty.median_draws")
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 2:
        raise BookError(f"книга: valuation.uncertainty.median_draws = {raw!r} — "
                        "ожидается целое число прогонов не меньше 2")
    return raw


# Ключи правил диагностик медианы; без ключа — первое значение.
REVERSE_BOUNDS = ("fixed", "follow_center")
MEDIAN_REFINE_STEPS = (0, 1)


def _choice(block: dict, key: str, where: str, allowed: tuple) -> Any:
    raw = block.get(key, allowed[0])
    if not any(_same(raw, value) for value in allowed):
        raise BookError(f"книга: {where}.{key} = {raw!r} (известны: "
                        f"{', '.join(map(str, allowed))}). Незнакомое значение не читается "
                        "ни одним правилом")
    return raw


def reverse_bounds(A: dict) -> str:
    """`valuation.uncertainty.reverse_bounds` — границы оси-значения полосы, когда
    обратный DCF медианы подменяет её центр значением за границей:
    `follow_center` — граница сдвигается к центру, `fixed` (нет ключа) — остаётся."""
    return _choice(A["valuation"]["uncertainty"], "reverse_bounds", "valuation.uncertainty",
                   REVERSE_BOUNDS)


def median_refine(A: dict) -> int:
    """`valuation.uncertainty.median_refine` — шагов секущей на полной полосе после
    поиска корня медианы на `median_draws` прогонах (0 — нет ключа)."""
    return _choice(A["valuation"]["uncertainty"], "median_refine", "valuation.uncertainty",
                   MEDIAN_REFINE_STEPS)


# ---------------------------------------- факты якоря, годы и периоды правил


# Факты якоря: обязательные и необязательные (проверяются, если названы).
ANCHOR_REQUIRED = frozenset({"period", "da_pre16", "capex", "net_debt", "cash", "ebitda_ltm"})
# `fcfe_ytd` — FCFE финансового года якоря по якорь включительно: годовое правило
# дивидендов (`dividend_carry`).
ANCHOR_OPTIONAL = frozenset({"revenue_ltm", "margin_pro_forma", "margin_pro_forma_se",
                             "ebitda_ltm_reported", "fcfe_ytd"})
ANCHOR_KEYS = ANCHOR_REQUIRED | ANCHOR_OPTIONAL
# Допуск сверки выручки LTM якоря и суммы сегментов с выручкой группы: факты
# пишутся десятичными строками отчётности, сумма строк расходится с итогом в
# последнем знаке округления, а не в первом.
FACT_SUM_TOL = 1e-6


def _is_number(raw: Any) -> bool:
    return (not isinstance(raw, bool) and isinstance(raw, (int, float)) and raw == raw
            and raw not in (float("inf"), float("-inf")))


def anchor_facts(A: dict) -> dict:
    """`facts.anchor` — отчётные величины на дату якоря (последнее закрытое полугодие).

    Имена нейтральны к дате: `period` называет полугодие якоря и обязан быть
    предыдущим к `meta.first_period` — иначе стартовое состояние клетки взято
    бы из одного отчёта, а история выручки и маржи — из другого. Площадь якоря —
    у сегментов (`facts.segments.<id>.area_end`).

    EBITDA LTM — один канон (решение ведущего A12): `ebitda_ltm` — проформа,
    сумма `facts.ebitda_pre16` двух полугодий до якоря (сверяется, если оба
    полугодия в книге); отчётная — рядом, `ebitda_ltm_reported` (сверяется с
    суммой `facts.reported.ebitda_pre16`), справочно.

    Необязательные: `revenue_ltm` (сверяется с суммой `facts.revenue` двух
    полугодий до якоря), `margin_pro_forma` и `margin_pro_forma_se` — маржа
    якоря на проформенном периметре и её стандартная ошибка: наблюдение правила
    A-P2u за полугодие якоря (`model.core.margin_observations`), только парой.
    """
    raw = _book_value(A, "facts.anchor", ("facts", "anchor"))
    if not isinstance(raw, dict):
        raise BookError(f"книга: facts.anchor — ожидается блок, а не {raw!r}")
    _refuse_unknown(raw, ANCHOR_KEYS, "facts.anchor")
    _require(raw, sorted(ANCHOR_REQUIRED), "facts.anchor")
    expected = previous_period(A["meta"]["first_period"])
    if raw["period"] != expected:
        raise BookError(f"книга: facts.anchor.period = {raw['period']!r}, а якорь сетки с "
                        f"meta.first_period = {A['meta']['first_period']!r} — {expected}")
    for key in sorted(set(raw) - {"period"}):
        if not _is_number(raw[key]):
            raise BookError(f"книга: facts.anchor.{key} = {raw[key]!r} — ожидается число")
    if ("margin_pro_forma" in raw) != ("margin_pro_forma_se" in raw):
        raise BookError("книга: facts.anchor.margin_pro_forma и margin_pro_forma_se — только "
                        "парой: проформа без ошибки читалась бы отчётным фактом")
    if "margin_pro_forma_se" in raw and raw["margin_pro_forma_se"] < 0.0:
        raise BookError("книга: facts.anchor.margin_pro_forma_se — ожидается неотрицательная "
                        "стандартная ошибка")
    if "margin_pro_forma" in raw:
        observed = (A["joint"]["regime_update"].get("observations") or {})
        if raw["period"] in observed:
            raise BookError(f"книга: маржа якоря {raw['period']} записана дважды — "
                            "facts.anchor.margin_pro_forma и joint.regime_update.observations")
    if "revenue_ltm" in raw:
        revenue = A["facts"]["revenue"]
        ltm = revenue[previous_period(raw["period"])] + revenue[raw["period"]]
        if abs(raw["revenue_ltm"] - ltm) > FACT_SUM_TOL * abs(ltm):
            raise BookError(f"книга: facts.anchor.revenue_ltm = {raw['revenue_ltm']!r}, а сумма "
                            f"facts.revenue двух полугодий до якоря — {ltm!r}")
    halves = (previous_period(raw["period"]), raw["period"])
    for key, block, where in (("ebitda_ltm", A["facts"].get("ebitda_pre16") or {},
                               "facts.ebitda_pre16"),
                              ("ebitda_ltm_reported",
                               (A["facts"].get("reported") or {}).get("ebitda_pre16") or {},
                               "facts.reported.ebitda_pre16")):
        if key not in raw or not all(_is_number(block.get(p)) for p in halves):
            continue
        ltm = block[halves[0]] + block[halves[1]]
        if abs(raw[key] - ltm) > FACT_SUM_TOL * abs(ltm):
            raise BookError(f"книга: facts.anchor.{key} = {raw[key]!r}, а сумма {where} двух "
                            f"полугодий до якоря — {ltm!r} (одна EBITDA LTM — решение ведущего A12)")
    return raw


def season_from_period(A: dict) -> str:
    """`margin.season_from_period` — первое полугодие с сезонной поправкой маржи.

    `margin.season_free_halves` полугодий перед ним — без поправки (у 850oa —
    два: якорь книги и первое прогнозное полугодие, чья цель режима задана
    явно по полугодию); с него — ± `margin.seasonal_h1_pp`; более ранняя
    история — с поправкой (`model.core.margin_season`).
    """
    raw = _book_value(A, "margin.season_from_period", ("margin", "season_from_period"))
    if not (isinstance(raw, str) and _is_period(raw)):
        raise BookError(f"книга: margin.season_from_period = {raw!r} — ожидается полугодие "
                        "вида \"ГГГГH1\" или \"ГГГГH2\"")
    return raw


def season_free_halves(A: dict) -> int:
    """`margin.season_free_halves` — сколько полугодий перед
    `margin.season_from_period` идут без сезонной поправки (целое ≥ 0).

    Свойство периода, а не положения якоря: перезаякоренная книга судит
    наблюдение того полугодия по той же явной цели (`model.core.margin_season`).
    """
    raw = _book_value(A, "margin.season_free_halves", ("margin", "season_free_halves"))
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise BookError(f"книга: margin.season_free_halves = {raw!r} — ожидается целое ≥ 0")
    return raw


PERIOD_UNITS = ("half",)


def meta_rules(A: dict) -> None:
    """`meta.period_unit` (только `half`: шаг ядра — полугодие) и `meta.company`
    ({name, ticker} строками; справочно — ядро по ним не считает)."""
    M = A["meta"]
    unit = M.get("period_unit", "half")
    if unit not in PERIOD_UNITS:
        raise BookError(f"книга: meta.period_unit = {unit!r} — ядро считает полугодиями (half)")
    company = M.get("company")
    if company is not None:
        if not isinstance(company, dict) or not all(
                isinstance(company.get(k), str) and company[k] for k in ("name", "ticker")):
            raise BookError(f"книга: meta.company = {company!r} — ожидается {{name, ticker}} строками")


# Соглашения полугодовой ставки в терминале (`valuation.terminal.half_rate_convention`).
HALF_RATE_CONVENTIONS = ("simple", "compound")
TERMINAL_KEYS = frozenset({"half_rate_convention"})


def terminal_rule(A: dict) -> dict:
    """`valuation.terminal` — правила терминала, которые выбирает книга.

    `half_rate_convention`: как годовая ставка щита и избыточного купона Р11
    делится на полугодие в ТЕРМИНАЛЕ — `simple` (r/2; так считал 850oa) или
    `compound` ((1 + r)^0,5 − 1 — как проценты явного периода, `half_rate`).
    """
    raw = _book_value(A, "valuation.terminal", ("valuation", "terminal"))
    if not isinstance(raw, dict):
        raise BookError(f"книга: valuation.terminal — ожидается блок, а не {raw!r}")
    _refuse_unknown(raw, TERMINAL_KEYS, "valuation.terminal")
    _require(raw, sorted(TERMINAL_KEYS), "valuation.terminal")
    convention = raw["half_rate_convention"]
    if convention not in HALF_RATE_CONVENTIONS:
        raise BookError(f"книга: valuation.terminal.half_rate_convention = {convention!r} "
                        f"(известны: {', '.join(HALF_RATE_CONVENTIONS)})")
    return dict(half_rate_convention=convention)


# ------------------------------------------------ корзины ставок и дивиденды


RATE_BASKET_KEYS = frozenset({"name", "share", "rate", "until", "basis"})
# Вид ставки корзины (`rate: {вид: число}`): договорная фиксированная или
# ключевая плюс спред (решение ведущего A10).
RATE_KINDS = ("fixed", "key_plus")


@dataclass(frozen=True)
class RateBasket:
    """Корзина действующего долга (`financing.rate_baskets`, решение ведущего A10).

    Доля `share` ВСЕГО долга платит свою ставку по полугодие `until`
    включительно: `rate: {fixed: x}` — договорную x, `rate: {key_plus: s}` —
    ключевую мира + s; после — ставку нового долга мира. Остаток долга сверх
    долей живых корзин — новый (и рефинансированный) долг: доля
    `financing.fixed_share` — по кривой на 3 года + спред, остальное — ключевая +
    спред. Справедливая ставка корзины — её собственная: премия старых купонов
    к рынку — строка моста `fixed_debt_fv_premium`, второй раз она не считается.
    """

    name: str
    share: float
    kind: str
    rate: float
    until: str

    def annual(self, key: float) -> float:
        """Годовая ставка корзины при ключевой `key`."""
        return self.rate if self.kind == "fixed" else key + self.rate


def rate_baskets(A: dict) -> tuple[RateBasket, ...]:
    """`financing.rate_baskets` — корзины действующего долга, порядок книги."""
    raw = _book_value(A, "financing.rate_baskets", ("financing", "rate_baskets"))
    if not isinstance(raw, list):
        raise BookError(f"книга: financing.rate_baskets — ожидается список корзин, а не {raw!r}")
    out, total = [], 0.0
    for i, item in enumerate(raw):
        where = f"financing.rate_baskets[{i}]"
        if not isinstance(item, dict):
            raise BookError(f"книга: {where} — ожидается строка-словарь, а не {item!r}")
        _refuse_unknown(item, RATE_BASKET_KEYS, where)
        _require(item, ("name", "share", "rate", "until"), where)
        if not _is_number(item["share"]) or not 0.0 < item["share"] <= 1.0:
            raise BookError(f"книга: {where}.share = {item['share']!r} — ожидается доля в (0; 1]")
        rate = item["rate"]
        if (not isinstance(rate, dict) or len(rate) != 1 or next(iter(rate)) not in RATE_KINDS
                or not _is_number(next(iter(rate.values())))):
            raise BookError(f"книга: {where}.rate = {rate!r} — ожидается {{fixed: ставка}} или "
                            "{key_plus: спред к ключевой}")
        kind, value = next(iter(rate.items()))
        if kind == "fixed" and value <= -1.0:
            raise BookError(f"книга: {where}.rate.fixed = {value!r} — ожидается годовая ставка")
        if not (isinstance(item["until"], str) and _is_period(item["until"])):
            raise BookError(f"книга: {where}.until = {item['until']!r} — ожидается полугодие "
                            "вида \"ГГГГH1\"/\"ГГГГH2\" (последнее по договорной ставке)")
        total += item["share"]
        out.append(RateBasket(name=str(item["name"]), share=item["share"], kind=kind,
                              rate=float(value), until=item["until"]))
    if total > 1.0 + 1e-12:
        raise BookError(f"книга: financing.rate_baskets — сумма долей {total!r} больше единицы")
    return tuple(out)


LADDER_KEYS = frozenset({"max_leverage", "payout_max"})


@dataclass(frozen=True)
class LadderRung:
    """Ступень дивидендной лестницы: при ЧД/EBITDA < `max_leverage` (None — без
    верхней границы) выплата не больше `payout_max` × FCFE (None — без предела)."""

    max_leverage: float | None
    payout_max: float | None


def dividend_ladder(A: dict) -> tuple[LadderRung, ...] | None:
    """`financing.dividend_ladder` — лестница выплат; нет ключа — правило без лестницы.

    ПРАВИЛО (`model.core.dividend_rule`), полугодие p с годом ≥
    `financing.dividends_from_year`:
      FCFE = ЧД(p−1) − ЧД_до(p) — деньги полугодия акционерам до дивидендов
             (FCFF + щит − чистые проценты − выплаты по строкам моста);
      λ    = ЧД_до(p) / EBITDA LTM(p);
      запас до цели = max(0, L·EBITDA LTM(p) − ЧД_до(p)), L = `leverage_target`;
      без лестницы:  D = запас до цели (правило 850oa);
      с лестницей:   ступень — первая строка (по возрастанию `max_leverage`) с
                     λ < max_leverage; нет такой — D = 0; иначе
                     D = min(max(запас до цели, max(FCFE, 0)), payout_max·max(FCFE, 0)),
                     payout_max = None — без предела.
    То есть компания отдаёт весь свой FCFE и выбирает запас до целевого рычага,
    но не больше потолка своей ступени (положение 2021 года: > 100 % FCF при
    ЧД/EBITDA < 1,0×, ≤ 100 % при 1,0–1,5×, ≤ 50 % выше).

    Так — без `financing.dividend_timing`. С `dividend_timing: annual_next_h1`
    та же ступень и та же формула считаются раз в год (`dividend_timing`), а
    ЧД порогов — отчётный при `financing.dividend_net_debt_basis: reported`
    (`dividend_net_debt_basis`).
    """
    F = A["financing"]
    if "dividend_ladder" not in F:
        return None
    raw = F["dividend_ladder"]
    if not isinstance(raw, list) or not raw:
        raise BookError(f"книга: financing.dividend_ladder — ожидается непустой список ступеней, "
                        f"а не {raw!r} (без лестницы — ключа нет)")
    out, previous = [], None
    for i, item in enumerate(raw):
        where = f"financing.dividend_ladder[{i}]"
        if not isinstance(item, dict):
            raise BookError(f"книга: {where} — ожидается строка-словарь, а не {item!r}")
        _refuse_unknown(item, LADDER_KEYS, where)
        _require(item, sorted(LADDER_KEYS), where)
        top, payout = item["max_leverage"], item["payout_max"]
        if top is not None and (not _is_number(top) or top <= 0.0):
            raise BookError(f"книга: {where}.max_leverage = {top!r} — ожидается ЧД/EBITDA > 0 "
                            "или null (без верхней границы)")
        if top is None and i != len(raw) - 1:
            raise BookError(f"книга: {where}.max_leverage = null — только у последней ступени")
        if top is not None and previous is not None and not top > previous:
            raise BookError(f"книга: {where}.max_leverage = {top!r} — ступени идут по "
                            "возрастанию рычага")
        if payout is not None and (not _is_number(payout) or payout < 0.0):
            raise BookError(f"книга: {where}.payout_max = {payout!r} — ожидается доля FCFE ≥ 0 "
                            "или null (без предела)")
        previous = top
        out.append(LadderRung(max_leverage=None if top is None else float(top),
                              payout_max=None if payout is None else float(payout)))
    return tuple(out)


# Сроки выплаты лестницы (`financing.dividend_timing`) и база ЧД её порогов
# (`financing.dividend_net_debt_basis`). Нет ключа — правило P2: каждое
# полугодие, по модельному ЧД.
ANNUAL_NEXT_H1 = "annual_next_h1"
DIVIDEND_TIMINGS = (ANNUAL_NEXT_H1,)
DIVIDEND_NET_DEBT_BASES = ("model", "reported")


def dividend_timing(A: dict) -> str | None:
    """`financing.dividend_timing` — когда лестница платит; нет ключа — None (каждое полугодие).

    `annual_next_h1` (политика «Ленты» годовая, решение ведущего F1): по итогам
    финансового года Y (1П + 2П) —
      FCFE_Y = FCFE 1П + FCFE 2П (FCFE полугодия — как у правила полугодия:
               ЧД(p−1) − ЧД_до(p), до дивидендов), в правило — max(FCFE_Y, 0);
      λ      = ЧД конца года / EBITDA LTM конца года — ступень по рычагу 31.12;
      запас  = max(0, L·EBITDA LTM конца года − ЧД конца года);
      D_Y    = min(max(запас, FCFE_Y⁺), payout_max·FCFE_Y⁺) — «верх ступени»;
    выплата — в 1П года Y + 1 (после годового собрания); во 2П выплат нет.
    `financing.dividends_from_year` — год ПЕРВОЙ ВЫПЛАТЫ (у «Ленты» 2028 —
    выплата в 1П2028 по итогам 2027 г.). Сезонный приток ОК 2П и отток 1П одного
    года гасятся внутри года, а не платятся по полугодиям.

    Год, 1П которого закрыт на якоре (якорь — 1П), берёт FCFE закрытого
    полугодия из факта `facts.anchor.fcfe_ytd`; при якоре на 2П этот факт — FCFE
    всего года, и выплата по нему — в первом прогнозном полугодии
    (`dividend_carry`). Только с лестницей: без неё правило 850oa (запас до
    цели) годового варианта не имеет.
    """
    F = A["financing"]
    if "dividend_timing" not in F:
        return None
    raw = F["dividend_timing"]
    if raw not in DIVIDEND_TIMINGS:
        raise BookError(f"книга: financing.dividend_timing = {raw!r} — известны "
                        f"{', '.join(DIVIDEND_TIMINGS)} (без ключа — выплата каждое полугодие)")
    if "dividend_ladder" not in F:
        raise BookError("книга: financing.dividend_timing — только вместе с financing.dividend_ladder "
                        "(годовое правило — правило лестницы)")
    return raw


def dividend_net_debt_basis(A: dict) -> str:
    """`financing.dividend_net_debt_basis` — ЧД, по которому меряются пороги лестницы.

    `model` (нет ключа — правило P2): модельный ЧД, в котором сидит прирост
    операционной кассы с даты якоря (он вычтен из FCFF как отток, а деньги
    лежат в кассе). `reported` (находка I1d, решение ведущего F1): отчётный ЧД =
    модельный − (операционная касса(p) − операционная касса якоря) — положение о
    дивидендах 2021 г. задаёт пороги по отчётному ЧД, и перезаякоривание на
    ожидаемом пути равно перекату. FCFE правила не меняется: прирост кассы
    вычитается из обоих ЧД одинаково.
    """
    raw = A["financing"].get("dividend_net_debt_basis", "model")
    if raw not in DIVIDEND_NET_DEBT_BASES:
        raise BookError(f"книга: financing.dividend_net_debt_basis = {raw!r} — известны "
                        f"{', '.join(DIVIDEND_NET_DEBT_BASES)}")
    return raw


def dividend_carry(A: dict) -> float | None:
    """`facts.anchor.fcfe_ytd` — FCFE финансового года якоря по якорь включительно, млрд ₽.

    Определение — то же, что FCFE полугодия в ядре (до дивидендов, без M&A и
    строк моста, кроме выплат по ним): при якоре на 1П — FCFE 1П, при якоре на
    2П — FCFE года. Нужен годовому правилу (`dividend_timing`), когда выплата по
    году якоря Y попадает в горизонт выплат: Y + 1 ≥ `dividends_from_year`;
    тогда без факта — отказ (ноль молча занизил бы или завысил выплату). Без
    годового правила ключ ничего не меняет — отказ. Нет ключа и не нужен — None.
    """
    anchor = A["facts"]["anchor"]
    timing = dividend_timing(A)
    if "fcfe_ytd" in anchor:
        value = anchor["fcfe_ytd"]
        if timing is None:
            raise BookError("книга: facts.anchor.fcfe_ytd — только при financing.dividend_timing "
                            "(правило полугодия его не читает)")
        if not _is_number(value):
            raise BookError(f"книга: facts.anchor.fcfe_ytd = {value!r} — ожидается число")
        return float(value)
    if timing is not None and int(str(anchor["period"])[:4]) + 1 >= A["financing"]["dividends_from_year"]:
        raise BookError(
            f"книга: нет facts.anchor.fcfe_ytd — годовая выплата по {str(anchor['period'])[:4]} г. "
            f"(в 1П{int(str(anchor['period'])[:4]) + 1}) не посчитается без FCFE года по якорь "
            "включительно")
    return None


# ------------------------------------------------------------------ сегменты


SEGMENT_MODES = ("yoy", "level", "revenue")
NETWORK_MODES = ("yoy", "level")
SEGMENT_ID = re.compile(r"^[a-z][a-z0-9_]*$")
SPACE_LEVELS = ("low", "mid", "high")
SEGMENT_REVENUE_KEYS = frozenset({"name", "mode", "new_space_density", "closed_productivity",
                                  "space", "lfl_offset", "other_growth", "density_path",
                                  "h1_share", "growth"})
SEGMENT_FACT_KEYS = frozenset({"revenue", "revenue_basis", "revenue_se", "area_end", "stores_end",
                               "new_area_gross_hist", "eff_area_avg_hist", "eff_area_end",
                               "new_area_dense_cohorts", "closed_area_hist"})
# Допуск сверки записанной истории эффективной площади с выводом правила сети
# (`check_effective_area_history`), тыс. м²: книга пишет историю, когорты и
# закрытия с двумя знаками, и округление входов расходится с выводом на ≈0,01
# (20 м² — 0,004 % базы «год к году»).
EFF_HIST_TOL = 0.02
REVENUE_BASES = ("reported", "pro_forma", "estimate")
SEGMENT_CAPEX_KEYS = frozenset({"growth_capex_per_m2"})
# Какие ключи сегмента читает каждый режим; остальные у режима — отказ (ключ,
# который никто не читает, выглядел бы действующим суждением).
_MODE_KEYS = {
    "yoy": frozenset({"new_space_density", "closed_productivity", "space", "lfl_offset",
                      "other_growth"}),
    "level": frozenset({"new_space_density", "closed_productivity", "space", "density_path",
                        "h1_share"}),
    "revenue": frozenset({"growth"}),
}
_MODE_REQUIRED = {
    "yoy": ("new_space_density", "closed_productivity", "space"),
    "level": ("new_space_density", "closed_productivity", "space", "density_path", "h1_share"),
    "revenue": ("growth",),
}


@dataclass(frozen=True)
class Segment:
    """Сегмент сети (`revenue.segments.<id>`) с его фактами и правилами capex.

    Режимы выручки (`model.core.run_cell`):
      yoy     — R(p) = R(p−2)·A_eff(p)/A_eff(p−2)·(1 + чек)·(1 + трафик + lfl_offset)·(1 + other_growth);
      level   — R(p) = A_eff(p)·плотность(p)/1000·индекс цен(p)·доля полугодия;
      revenue — R(p) = R(p−2)·(1 + growth(p)).
    """

    id: str
    name: str
    mode: str
    spec: dict
    facts: dict
    new_space_density: float | None = None
    closed_productivity: float | None = None
    growth_capex_per_m2: float | None = None
    dense_history_cohorts: int = 0
    effective_area_end: float | None = None

    @property
    def network(self) -> bool:
        return self.mode in NETWORK_MODES


def _segment_blocks(A: dict) -> tuple[dict, dict, dict]:
    R = _book_value(A, "revenue.segments", ("revenue", "segments"))
    F = _book_value(A, "facts.segments", ("facts", "segments"))
    C = _book_value(A, "capex.segments", ("capex", "segments"))
    for where, block in (("revenue.segments", R), ("facts.segments", F), ("capex.segments", C)):
        if not isinstance(block, dict):
            raise BookError(f"книга: {where} — ожидается блок сегментов, а не {block!r}")
    if not R:
        raise BookError("книга: revenue.segments — нет ни одного сегмента")
    return R, F, C


def _check_space(space: Any, where: str) -> None:
    if not isinstance(space, dict):
        raise BookError(f"книга: {where} — ожидается блок {{low, mid, high}}, а не {space!r}")
    _refuse_unknown(space, frozenset(SPACE_LEVELS), where)
    _require(space, SPACE_LEVELS, where)
    for level in SPACE_LEVELS:
        _refuse_unknown(space[level], frozenset({"gross_open", "close"}), f"{where}.{level}")
        _require(space[level], ("gross_open", "close"), f"{where}.{level}")


def _check_path(spec: Any, where: str, P: list[str]) -> None:
    """Траектория — числа (строка «0.1» — не число) и покрывает все полугодия
    горизонта (иначе KeyError в середине расчёта)."""
    values = ([v for k, v in spec.items() if k != "LT_from"] if isinstance(spec, dict)
              else [spec])
    if not values or not all(_is_number(v) for v in values):
        raise BookError(f"книга: {where} = {spec!r} — ожидается траектория из чисел")
    try:
        for p in P:
            if not _is_number(path_value(spec, p)):
                raise ValueError
    except (KeyError, TypeError, ValueError):
        raise BookError(f"книга: {where} — траектория не покрывает горизонт "
                        f"{P[0]}…{P[-1]} числами (без ключа периода нужен LT)") from None


def lfl_offset_spec(segment: Segment, regime: str) -> Any:
    """Траектория `lfl_offset` сегмента для режима маржи клетки; None — поправки нет.

    Значение ключа — траектория или блок `{by_regime: {режим: траектория}}`
    (у «О'КЕЙ» сходимость плотности зависит от режима интеграции).
    """
    raw = segment.spec.get("lfl_offset")
    if isinstance(raw, dict) and "by_regime" in raw:
        return raw["by_regime"][regime]
    return raw


def segments(A: dict) -> tuple[Segment, ...]:
    """Сегменты сети в порядке `revenue.segments` с проверенными правилами.

    Множества сегментов `revenue.segments`, `facts.segments` и сегментов сети в
    `capex.segments` обязаны совпадать; сумма выручки сегментов в каждом
    полугодии `facts.revenue` — выручка группы (допуск FACT_SUM_TOL).
    """
    R, F, C = _segment_blocks(A)
    if set(F) != set(R):
        raise BookError("книга: сегменты facts.segments и revenue.segments не совпадают: "
                        f"{sorted(set(F) ^ set(R))}")
    P = periods(A["meta"]["first_period"], A["meta"]["last_period"])
    anchor = previous_period(P[0])
    base = (previous_period(anchor), anchor)
    regimes = list(A["margin"]["regimes"])
    out = []
    for sid, spec in R.items():
        where = f"revenue.segments.{sid}"
        if not isinstance(sid, str) or not SEGMENT_ID.match(sid):
            raise BookError(f"книга: {where} — имя сегмента латиницей в нижнем регистре")
        if not isinstance(spec, dict):
            raise BookError(f"книга: {where} — ожидается блок сегмента, а не {spec!r}")
        _refuse_unknown(spec, SEGMENT_REVENUE_KEYS, where)
        _require(spec, ("name", "mode"), where)
        mode = spec["mode"]
        if mode not in SEGMENT_MODES:
            raise BookError(f"книга: {where}.mode = {mode!r} (известны: {', '.join(SEGMENT_MODES)})")
        unread = sorted(set(spec) - {"name", "mode"} - _MODE_KEYS[mode])
        if unread:
            raise BookError(f"книга: {where}: режим {mode} не читает ключи {', '.join(unread)}")
        _require(spec, _MODE_REQUIRED[mode], where)
        facts = F[sid]
        fwhere = f"facts.segments.{sid}"
        if not isinstance(facts, dict):
            raise BookError(f"книга: {fwhere} — ожидается блок фактов сегмента, а не {facts!r}")
        _refuse_unknown(facts, SEGMENT_FACT_KEYS, fwhere)
        _require(facts, ("revenue",), fwhere)
        revenue = facts["revenue"] or {}
        # База «год к году» нужна режимам yoy и revenue; режиму level выручка
        # истории не нужна (null ≠ 0: базы нет — рост г/г на витрине пустой).
        needed = base if mode != "level" else (anchor,)
        for p in needed:
            if not _is_number(revenue.get(p)):
                raise BookError(f"книга: {fwhere}.revenue — нет выручки {p} (база «год к году»)")
        _revenue_basis_and_se(facts, revenue, fwhere)
        kwargs: dict[str, Any] = {}
        if mode in NETWORK_MODES:
            _check_space(spec["space"], f"{where}.space")
            kwargs["new_space_density"] = _book_number(spec, "new_space_density", None, where,
                                                       0.0, 2.0, low_open=True)
            kwargs["closed_productivity"] = _book_number(spec, "closed_productivity", None, where,
                                                         0.0, 2.0)
            _require(facts, ("area_end",), fwhere)
            _book_number(facts, "area_end", None, fwhere, 0.0, float("inf"), low_open=True)
            hist = facts.get("new_area_gross_hist", [])
            if not isinstance(hist, list) or not all(_is_number(v) and v >= 0 for v in hist):
                raise BookError(f"книга: {fwhere}.new_area_gross_hist — ожидается список "
                                "неотрицательных чисел")
            dense = facts.get("new_area_dense_cohorts", 0)
            if isinstance(dense, bool) or not isinstance(dense, int) or not 0 <= dense <= len(hist):
                raise BookError(f"книга: {fwhere}.new_area_dense_cohorts = {dense!r} — ожидается "
                                "целое от 0 до числа исторических когорт")
            kwargs["dense_history_cohorts"] = dense
            closed = facts.get("closed_area_hist")
            if closed is not None:
                if mode != "yoy":
                    raise BookError(f"книга: {fwhere}.closed_area_hist — история эффективной "
                                    "площади нужна только режиму yoy")
                if (not isinstance(closed, dict) or set(closed) != set(base)
                        or not all(_is_number(v) and v >= 0.0 for v in closed.values())):
                    raise BookError(f"книга: {fwhere}.closed_area_hist — ожидаются закрытия "
                                    f"(тыс. м², ≥ 0) ровно за {', '.join(base)}")
                if len(hist) < 2:
                    raise BookError(f"книга: {fwhere}.closed_area_hist — нужны когорты "
                                    "new_area_gross_hist обоих полугодий истории")
            if "eff_area_end" in facts:
                kwargs["effective_area_end"] = _book_number(facts, "eff_area_end", None, fwhere,
                                                            0.0, float("inf"), low_open=True)
            cspec = C.get(sid)
            if not isinstance(cspec, dict):
                raise BookError(f"книга: capex.segments.{sid} — у сегмента сети нет блока "
                                "{growth_capex_per_m2}")
            _refuse_unknown(cspec, SEGMENT_CAPEX_KEYS, f"capex.segments.{sid}")
            kwargs["growth_capex_per_m2"] = _book_number(cspec, "growth_capex_per_m2", None,
                                                         f"capex.segments.{sid}", 0.0, float("inf"))
        elif sid in C:
            raise BookError(f"книга: capex.segments.{sid} — у сегмента без площади (mode: revenue) "
                            "capex открытий нет, ключ никто не читает")
        if mode == "yoy":
            hist = facts.get("eff_area_avg_hist") or {}
            for p in base:
                if not _is_number(hist.get(p)) or not hist[p] > 0.0:
                    raise BookError(f"книга: {fwhere}.eff_area_avg_hist.{p} — нужна положительная "
                                    "площадь (база «год к году»: на неё делится рост площади)")
            offset = spec.get("lfl_offset")
            if isinstance(offset, dict) and "by_regime" in offset:
                if set(offset) != {"by_regime"} or not isinstance(offset["by_regime"], dict):
                    raise BookError(f"книга: {where}.lfl_offset — блок {{by_regime: {{режим: траектория}}}}")
                if set(offset["by_regime"]) != set(regimes):
                    raise BookError(f"книга: {where}.lfl_offset.by_regime — нужны все режимы маржи "
                                    f"({', '.join(regimes)})")
                for r, path in offset["by_regime"].items():
                    _check_path(path, f"{where}.lfl_offset.by_regime.{r}", P)
            elif offset is not None:
                _check_path(offset, f"{where}.lfl_offset", P)
            if "other_growth" in spec:
                _check_path(spec["other_growth"], f"{where}.other_growth", P)
        elif mode == "level":
            _check_path(spec["density_path"], f"{where}.density_path", P)
            _book_number(spec, "h1_share", None, where, 0.0, 1.0, low_open=True)
            if not spec["h1_share"] < 1.0:
                raise BookError(f"книга: {where}.h1_share — доля первого полугодия в (0; 1)")
        else:
            _check_path(spec["growth"], f"{where}.growth", P)
        out.append(Segment(id=sid, name=str(spec["name"]), mode=mode, spec=spec, facts=facts,
                           **kwargs))
    extra = sorted(set(C) - set(R))
    if extra:
        raise BookError(f"книга: capex.segments — сегменты вне revenue.segments: {', '.join(extra)}")
    group = A["facts"]["revenue"]
    for p in group:
        # Сегмент режима level без базы (null) в сумму полугодия не входит —
        # так книга и пишет выручку группы (база «Дом Ленты» до покупки не
        # раскрыта); у остальных режимов пропуск — отказ ниже.
        values = [(s.facts["revenue"] or {}).get(p) for s in out]
        if any(v is None for s, v in zip(out, values) if s.mode != "level"):
            continue
        total = sum(v for v in values if v is not None)
        if abs(total - group[p]) > FACT_SUM_TOL * abs(group[p]):
            raise BookError(f"книга: выручка сегментов {p} в сумме {total!r}, а facts.revenue "
                            f"группы — {group[p]!r}")
    for p in base:
        if any((s.facts["revenue"] or {}).get(p) is None for s in out if s.mode != "level"):
            raise BookError(f"книга: у сегментов нет выручки {p}")
    return tuple(out)


def _revenue_basis_and_se(facts: dict, revenue: dict, where: str) -> None:
    """`revenue_basis` и `revenue_se` сегмента (D3, решение ведущего B13).

    Основа выручки полугодия — `reported` (отчёт), `pro_forma` (проформа
    покупки), `estimate` (расчёт правилом книги); у полугодия без выручки
    (null) основы нет. `revenue_se` — стандартная ошибка базы, млрд ₽: те же
    полугодия, что у выручки, null там, где выручки нет, иначе число ≥ 0; у
    отчёта ошибка — ноль (отчёт не оценка), у расчёта и проформы — любая ≥ 0.
    Ядро выручку по ошибке не двигает: ошибка — вход сверки листа «Сеть» и
    карточки сети (ось базы — строка чувствительности книги).
    """
    basis = facts.get("revenue_basis") or {}
    for p, b in basis.items():
        if b is None:
            if revenue.get(p) is not None:
                raise BookError(f"книга: {where}.revenue_basis.{p} — null при выручке "
                                f"{revenue[p]!r}")
            continue
        if b not in REVENUE_BASES:
            raise BookError(f"книга: {where}.revenue_basis.{p} = {b!r} (известны: "
                            f"{', '.join(REVENUE_BASES)})")
    se = facts.get("revenue_se")
    if se is None:
        return
    if not isinstance(se, dict) or set(se) != set(revenue):
        raise BookError(f"книга: {where}.revenue_se — ожидаются те же полугодия, что у revenue")
    for p, value in se.items():
        if revenue[p] is None:
            if value is not None:
                raise BookError(f"книга: {where}.revenue_se.{p} — ошибка у полугодия без выручки")
            continue
        if not _is_number(value) or value < 0.0:
            raise BookError(f"книга: {where}.revenue_se.{p} = {value!r} — ожидается число ≥ 0")
        if basis.get(p) == "reported" and value != 0.0:
            raise BookError(f"книга: {where}.revenue_se.{p} = {value!r} — у отчёта (reported) "
                            "ошибки нет")


def check_effective_area_history(A: dict) -> None:
    """Записанная история эффективной площади = вывод правила сети (E12).

    У сегмента с `closed_area_hist` ядро выводит `eff_area_avg_hist` двух
    полугодий до якоря само (`model.core.effective_history`), чтобы подмены d и
    продуктивности закрытых двигали и базу «год к году». Записанные числа книги
    (читаемые, с двумя знаками) обязаны совпадать с выводом при значениях самой
    книги — проверяется у книги как она записана (`load_book`), а не у подмен.
    """
    from model.core import anchor_effective_end, effective_history

    maturity = A["revenue"]["maturity_curve"]
    for seg in segments(A):
        if not seg.network or "closed_area_hist" not in seg.facts:
            continue
        stated = seg.facts.get("eff_area_avg_hist") or {}
        end = anchor_effective_end(seg, maturity)
        for p, value in effective_history(seg, maturity, end, A).items():
            if p in stated and abs(stated[p] - value) > EFF_HIST_TOL:
                raise BookError(f"книга: facts.segments.{seg.id}.eff_area_avg_hist.{p} = "
                                f"{stated[p]!r}, а правило сети с closed_area_hist даёт {value:.4f}")


# ------------------------------------------- строки моста и выплаты по ним


BRIDGE_ITEM_KEYS = frozenset({"id", "name", "kind", "amount", "as_of", "accrete_rate_half",
                              "settle_period", "settle_amount", "haircut", "basis"})
BRIDGE_KINDS = ("claim", "asset")


@dataclass(frozen=True)
class BridgeItem:
    """Строка моста EV → требования: требование (`claim`, прибавляется) или
    актив (`asset`, вычитается с учётом `haircut`).

    Строка — ЕДИНСТВЕННОЕ место суммы: выплата в пути чистого долга выводится
    из неё (`model.core.settlement_payments`), отдельного списка выплат нет.
    """

    id: str
    name: str
    kind: str
    amount: float
    as_of: date
    """Дата, на которую названа сумма (обязательна у каждой строки)."""
    accrete_rate_half: float | None = None
    """Ставка наращения (размотки) суммы ЗА ПОЛУГОДИЕ между `as_of` и датой оценки."""
    settle_period: str | None = None
    """Полугодие расчёта: в нём выплата (у актива — поступление) идёт в путь
    чистого долга, после его закрытия строка из моста снимается."""
    settle_amount: float | None = None
    """Сумма расчёта; без ключа — сумма, наращенная по `accrete_rate_half` до
    конца полугодия расчёта."""
    haircut: float | None = None
    """Доля суммы актива, которая НЕ засчитывается (актив = сумма × (1 − haircut))."""


def _iso_day(raw: Any, where: str) -> date:
    try:
        if not isinstance(raw, str):
            raise ValueError
        return date.fromisoformat(raw)
    except ValueError:
        raise BookError(f"книга: {where} — ожидается дата \"ГГГГ-ММ-ДД\", а не {raw!r}") from None


def bridge_items(A: dict) -> tuple[BridgeItem, ...]:
    """`bridge.items` — строки моста в порядке книги (порядок суммирования — тоже книги)."""
    raw = _book_value(A, "bridge.items", ("bridge", "items"))
    if not isinstance(raw, list):
        raise BookError(f"книга: bridge.items — ожидается список строк, а не {raw!r}")
    # Дата, на которую книга называет мост целиком (справочно для витрины и
    # перезаякоривания); наращение считается от `as_of` каждой строки.
    _iso_day(A["meta"].get("bridge_as_of"), "meta.bridge_as_of")
    P = periods(A["meta"]["first_period"], A["meta"]["last_period"])
    out, seen = [], set()
    for i, item in enumerate(raw):
        where = f"bridge.items[{i}]"
        if not isinstance(item, dict):
            raise BookError(f"книга: {where} — ожидается строка-словарь, а не {item!r}")
        _refuse_unknown(item, BRIDGE_ITEM_KEYS, where)
        _require(item, ("id", "name", "kind", "amount", "as_of"), where)
        if not isinstance(item["id"], str) or not item["id"] or item["id"] in seen:
            raise BookError(f"книга: {where}.id = {item['id']!r} — ожидается непустое "
                            "уникальное имя")
        seen.add(item["id"])
        if item["kind"] not in BRIDGE_KINDS:
            raise BookError(f"книга: {where}.kind = {item['kind']!r} (известны: "
                            f"{', '.join(BRIDGE_KINDS)})")
        if not _is_number(item["amount"]):
            raise BookError(f"книга: {where}.amount = {item['amount']!r} — ожидается число")
        rate = item.get("accrete_rate_half")
        if rate is not None and (not _is_number(rate) or rate <= -1.0):
            raise BookError(f"книга: {where}.accrete_rate_half = {rate!r} — ожидается "
                            "полугодовая ставка больше −1")
        settle = item.get("settle_period")
        if settle is not None and not (isinstance(settle, str) and _is_period(settle)):
            raise BookError(f"книга: {where}.settle_period = {settle!r} — ожидается полугодие")
        if settle is not None and settle not in P:
            raise BookError(f"книга: {where}.settle_period = {settle!r} вне горизонта "
                            f"{P[0]}…{P[-1]}: выплата не попала бы в путь чистого долга")
        settle_amount = item.get("settle_amount")
        if settle_amount is not None:
            if settle is None:
                raise BookError(f"книга: {where}.settle_amount без settle_period")
            if not _is_number(settle_amount):
                raise BookError(f"книга: {where}.settle_amount = {settle_amount!r} — ожидается число")
        haircut = item.get("haircut")
        if haircut is not None:
            if item["kind"] != "asset":
                raise BookError(f"книга: {where}.haircut — только у строки kind: asset")
            if not _is_number(haircut) or not 0.0 <= haircut <= 1.0:
                raise BookError(f"книга: {where}.haircut = {haircut!r} — ожидается доля в [0; 1]")
        out.append(BridgeItem(
            id=item["id"], name=str(item["name"]), kind=item["kind"], amount=item["amount"],
            as_of=_iso_day(item["as_of"], f"{where}.as_of"),
            accrete_rate_half=rate, settle_period=settle, settle_amount=settle_amount,
            haircut=haircut))
    return tuple(out)


# ------------------------------------ необязательные траектории и налог


def optional_paths(A: dict) -> None:
    """Необязательные траектории покрывают горизонт: `nwc.acquired_path` (ОК
    приобретённого периметра, доля годовой выручки — прибавка к `nwc_pct`),
    `capex.integration_capex` (млрд ₽)."""
    P = periods(A["meta"]["first_period"], A["meta"]["last_period"])
    for block, key in (("nwc", "acquired_path"), ("capex", "integration_capex")):
        if key in A[block]:
            _check_path(A[block][key], f"{block}.{key}", P)


NWC_QUARTER_SHARE_TOL = 1e-9


def nwc_rules(A: dict) -> None:
    """Старт ОК и квартальные веса переката (`nwc`).

    Старт — ровно один из двух ключей: `anchor_level` (ОК якоря по балансу, млрд
    ₽; доля старта выводится, подмена июньского излишка её пересчитывает —
    решение ведущего D27) или `nwc_pct_start` (доля выручки LTM, правило 850oa).
    `quarter_share {Q1..Q4}` — доли изменения ОК по кварталам внутри полугодия
    (пары дают 1, знак любой: у Ленты ОК 3 кв. ещё растёт) для переката чистого
    долга и доли текущего полугодия в EV (`model.core.nwc_quarter_weight`, A9).
    """
    N = A["nwc"]
    if ("anchor_level" in N) == ("nwc_pct_start" in N):
        raise BookError("книга: nwc — старт ОК задаётся ровно одним ключом: anchor_level (ОК "
                        "якоря по балансу) или nwc_pct_start")
    if "anchor_level" in N and not _is_number(N["anchor_level"]):
        raise BookError(f"книга: nwc.anchor_level = {N['anchor_level']!r} — ожидается ОК якоря, "
                        "млрд ₽")
    if "nwc_pct_start" in N and not _is_number(N["nwc_pct_start"]):
        raise BookError(f"книга: nwc.nwc_pct_start = {N['nwc_pct_start']!r} — ожидается доля")
    shares = N.get("quarter_share")
    if shares is None:
        return
    if not isinstance(shares, dict):
        raise BookError("книга: nwc.quarter_share — ожидается блок {Q1..Q4}")
    _refuse_unknown(shares, frozenset(QUARTERS), "nwc.quarter_share")
    _require(shares, QUARTERS, "nwc.quarter_share")
    for q in QUARTERS:
        if not _is_number(shares[q]):
            raise BookError(f"книга: nwc.quarter_share.{q} = {shares[q]!r} — ожидается число")
    for a, b in (("Q1", "Q2"), ("Q3", "Q4")):
        if abs(shares[a] + shares[b] - 1.0) > NWC_QUARTER_SHARE_TOL:
            raise BookError(f"книга: nwc.quarter_share — доли изменения ОК внутри полугодия, "
                            f"{a} + {b} = 1 (сумма пары {shares[a] + shares[b]!r})")


ACQUIRED_NOL_KEYS = frozenset({"amount", "usable_from", "discount_rule"})
DISCOUNT_RULE_KEYS = frozenset({"method", "haircut", "locked_addback"})
# Правила дисконта пула запертых убытков (`discount_rule.method`).
DISCOUNT_METHODS = ("frozen_until_usable_from",)


def acquired_nol(A: dict) -> dict | None:
    """`tax.acquired_nol` — запертые налоговые убытки приобретённых юрлиц (D11, D26).

    {amount, usable_from, discount_rule: {method, haircut, locked_addback}}.
    Консолидированной группы налогоплательщиков нет: убытки «О'КЕЙ» и «Дом
    Ленты» заперты в своих юрлицах, отложенный актив при покупке не признан.
    Правило `frozen_until_usable_from` (`model.core.run_cell`):
      до полугодия `usable_from` — убыток запертых юрлиц прогноза
        `locked_addback[p]` (млрд ₽ за полугодие; нет ключа — 0) консолидированную
        базу не уменьшает: база пула группы = α·база − проценты + locked_addback,
        пул запертых юрлиц += locked_addback (стартовый пул — `amount` на якоре);
      в `usable_from` (присоединение к основной «дочке» — путь, которым группа
        уже пользовалась) пул группы += (1 − haircut)·пул запертых юрлиц; `haircut`
        — риск отказа в зачёте и неприсоединения (1 — «никогда»);
      дальше `locked_addback` не действует.
    Налог без рычага правило не трогает: ценность — только через щит APV, дисконт
    до использования — сроком самого зачёта. Нет ключа — пула нет.
    """
    T = A["tax"]
    if "acquired_nol" not in T:
        return None
    raw, where = T["acquired_nol"], "tax.acquired_nol"
    if not isinstance(raw, dict):
        raise BookError(f"книга: {where} — ожидается блок, а не {raw!r}")
    _refuse_unknown(raw, ACQUIRED_NOL_KEYS, where)
    _require(raw, sorted(ACQUIRED_NOL_KEYS), where)
    amount = _book_number(raw, "amount", None, where, 0.0, float("inf"))
    if not (isinstance(raw["usable_from"], str) and _is_period(raw["usable_from"])):
        raise BookError(f"книга: {where}.usable_from = {raw['usable_from']!r} — ожидается полугодие")
    rule, rwhere = raw["discount_rule"], f"{where}.discount_rule"
    if not isinstance(rule, dict):
        raise BookError(f"книга: {rwhere} — ожидается блок {{method, haircut, locked_addback}}")
    _refuse_unknown(rule, DISCOUNT_RULE_KEYS, rwhere)
    _require(rule, sorted(DISCOUNT_RULE_KEYS), rwhere)
    if not any(_same(rule["method"], m) for m in DISCOUNT_METHODS):
        raise BookError(f"книга: {rwhere}.method = {rule['method']!r} (известны: "
                        f"{', '.join(DISCOUNT_METHODS)})")
    haircut = _book_number(rule, "haircut", None, rwhere, 0.0, 1.0)
    addback = rule["locked_addback"]
    if addback is None:
        addback = {}
    P = periods(A["meta"]["first_period"], A["meta"]["last_period"])
    if (not isinstance(addback, dict)
            or not all(isinstance(k, str) and k in P for k in addback)
            or not all(_is_number(v) and v >= 0.0 for v in addback.values())):
        raise BookError(f"книга: {rwhere}.locked_addback — ожидаются убытки запертых юрлиц "
                        f"(млрд ₽ ≥ 0) по полугодиям горизонта {P[0]}…{P[-1]}; нет полугодия — 0")
    return dict(amount=amount, usable_from=raw["usable_from"], method=rule["method"],
                haircut=haircut, locked_addback={k: float(v) for k, v in addback.items()})


def capex_tax_premium(A: dict) -> float | None:
    """`tax.capex_tax_premium_share` — доля capex, вычитаемая в налоге в полугодии
    затрат (амортизационная премия, п. 9 ст. 258 НК; решение ведущего D25).

    Налоговая D&A = премия·capex + (1 − премия)·линейная по когортам (учётная
    D&A не меняется); разница уменьшает налоговую базу, в терминале —
    стационарная разница при росте g (`model.core.run_cell`). Нет ключа — None.
    """
    raw = A["tax"].get("capex_tax_premium_share")
    if raw is None:
        return None
    return _book_number(A["tax"], "capex_tax_premium_share", None, "tax", 0.0, 1.0)


GOVERNANCE_COMPONENT_KEYS = frozenset({"name", "value", "sign", "basis", "in_850oa_scope"})
# Допуск тождества «сумма каналов = дисконт за управление».
GOVERNANCE_SUM_TOL = 1e-9


def governance_components(A: dict) -> list[dict] | None:
    """`valuation.governance_components` — разложение дисконта за управление (витрина).

    Список {name, value ≥ 0, sign: +1|−1, basis}; ядро число не читает — оно
    берёт `valuation.governance_discount`. Нет ключа — разложения нет.
    """
    V = A["valuation"]
    if "governance_components" not in V:
        return None
    raw = V["governance_components"]
    if not isinstance(raw, list) or not raw:
        raise BookError("книга: valuation.governance_components — ожидается непустой список каналов")
    out = []
    for i, item in enumerate(raw):
        where = f"valuation.governance_components[{i}]"
        if not isinstance(item, dict):
            raise BookError(f"книга: {where} — ожидается строка-словарь, а не {item!r}")
        _refuse_unknown(item, GOVERNANCE_COMPONENT_KEYS, where)
        _require(item, ("name", "value", "sign"), where)
        if not _is_number(item["value"]) or item["value"] < 0.0:
            raise BookError(f"книга: {where}.value = {item['value']!r} — ожидается доля ≥ 0")
        if isinstance(item["sign"], bool) or item["sign"] not in (1, -1):
            raise BookError(f"книга: {where}.sign = {item['sign']!r} — ожидается +1 или −1")
        scope = item.get("in_850oa_scope", True)
        if not isinstance(scope, bool):
            raise BookError(f"книга: {where}.in_850oa_scope = {scope!r} — ожидается true/false")
        out.append(dict(name=str(item["name"]), value=float(item["value"]), sign=int(item["sign"]),
                        basis=item.get("basis"), in_850oa_scope=scope))
    return out


def governance_on_850oa_scope(A: dict) -> float | None:
    """Дисконт за управление на охвате 850oa — Σ sign·value каналов с
    `in_850oa_scope` (по умолчанию — все): строка витрины рядом с разложением g
    (решение ведущего E31; у «Ленты» — без канала будущих сделок). Ядро число
    не читает. Нет разложения — None."""
    parts = governance_components(A)
    if parts is None:
        return None
    return sum(p["sign"] * p["value"] for p in parts if p["in_850oa_scope"])


def check_governance_sum(A: dict) -> None:
    """Сумма подписанных каналов равна `valuation.governance_discount` (допуск 1e-9).

    Проверяется у книги как она записана (`load_book`), а не у подмен: ось g
    полосы и обратный DCF двигают само число, разложение при этом справочно.
    """
    parts = governance_components(A)
    if parts is None:
        return
    total = sum(p["sign"] * p["value"] for p in parts)
    g = float(A["valuation"].get("governance_discount", 0.0))
    if abs(total - g) > GOVERNANCE_SUM_TOL:
        raise BookError(f"книга: сумма valuation.governance_components {total!r} ≠ "
                        f"valuation.governance_discount {g!r}")


PEER_KEYS = frozenset({"key", "name", "ev_ebitda", "basis", "as_of", "subject"})
SELLSIDE_TARGET_KEYS = frozenset({"house", "target", "date", "rating", "source"})
SELLSIDE_SUMMARY_KEYS = frozenset({"n", "median", "mean", "min", "max", "as_of", "note"})
# Допуск сверки агрегатов целей с их списком, ₽: книга пишет среднее с одним знаком.
SELLSIDE_TOL = 0.05


def guidance_rule(A: dict) -> dict | None:
    """`facts.guidance {period, ebitda_margin_min, source?}` — гайденс компании по
    марже EBITDA за год (гейт `guidance_gap`, `model.checks.check_guidance`).
    Нет ключа — гейта нет."""
    raw = A["facts"].get("guidance")
    if raw is None:
        return None
    where = "facts.guidance"
    if not isinstance(raw, dict):
        raise BookError(f"книга: {where} — ожидается блок")
    _require(raw, ("period", "ebitda_margin_min"), where)
    if not (isinstance(raw["period"], str) and re.fullmatch(r"\d{4}", raw["period"])):
        raise BookError(f"книга: {where}.period = {raw['period']!r} — ожидается год \"ГГГГ\"")
    _book_number(raw, "ebitda_margin_min", None, where, 0.0, 1.0, low_open=True)
    return raw


def market_rules(A: dict) -> None:
    """Аналоги и цели инвестдомов (`market`, справочно — ядро по ним не считает).

    `peers[]` — {key, name, ev_ebitda, basis?, as_of?, subject?}: ключ уникален
    (выпуск адресует аналог ключом). `sellside_targets[]` — последняя цель
    каждого дома {house, target, date, rating?, source}; `sellside_summary` —
    агрегаты {n, median, mean, min, max, as_of, note?} (решение ведущего E32):
    n, медиана, среднее, минимум и максимум обязаны быть функцией списка.
    """
    import statistics

    M = A["market"]
    seen = set()
    for i, peer in enumerate(M.get("peers") or []):
        where = f"market.peers[{i}]"
        if not isinstance(peer, dict):
            raise BookError(f"книга: {where} — ожидается строка-словарь")
        _refuse_unknown(peer, PEER_KEYS, where)
        _require(peer, ("key", "name", "ev_ebitda"), where)
        if peer["key"] in seen or not isinstance(peer["key"], str):
            raise BookError(f"книга: {where}.key = {peer['key']!r} — ожидается уникальный ключ")
        seen.add(peer["key"])
    targets, summary = M.get("sellside_targets"), M.get("sellside_summary")
    if targets is None and summary is None:
        return
    if not isinstance(targets, list) or not targets or not isinstance(summary, dict):
        raise BookError("книга: market.sellside_targets (список целей домов) и "
                        "market.sellside_summary (агрегаты) — только парой")
    values = []
    for i, row in enumerate(targets):
        where = f"market.sellside_targets[{i}]"
        if not isinstance(row, dict):
            raise BookError(f"книга: {where} — ожидается строка-словарь")
        _refuse_unknown(row, SELLSIDE_TARGET_KEYS, where)
        _require(row, ("house", "target", "date", "source"), where)
        if not _is_number(row["target"]) or row["target"] <= 0.0:
            raise BookError(f"книга: {where}.target = {row['target']!r} — ожидается цена > 0")
        values.append(float(row["target"]))
    _refuse_unknown(summary, SELLSIDE_SUMMARY_KEYS, "market.sellside_summary")
    _require(summary, ("n", "median", "mean", "min", "max", "as_of"), "market.sellside_summary")
    want = dict(n=len(values), median=statistics.median(values), mean=sum(values) / len(values),
                min=min(values), max=max(values))
    for key, value in want.items():
        if not _is_number(summary[key]) or abs(summary[key] - value) > SELLSIDE_TOL:
            raise BookError(f"книга: market.sellside_summary.{key} = {summary[key]!r}, а по списку "
                            f"целей — {value!r}")


QUARTERS = ("Q1", "Q2", "Q3", "Q4")
QUARTER_SUM_TOL = 1e-12
# Сумма долей пары кварталов — единица с допуском записи десятичных долей.
QUARTER_SHARE_TOL = 1e-9


def quarter_rules(A: dict) -> dict | None:
    """`revenue.quarter_share` и `margin.quarter_offset_pp` — квартальный слой.

    Доли кварталов ВНУТРИ своего полугодия (DESIGN D2: выручка квартала =
    выручка полугодия × доля квартала): s1 + s2 = 1 и s3 + s4 = 1, каждая > 0;
    поправки маржи квартала к марже его полугодия обнуляются внутри полугодия с
    весами выручки: s1·o1 + s2·o2 = 0 и s3·o3 + s4·o4 = 0 (решение ведущего 21,
    `model.quarters`). Доли в году ключ не несёт: соотношение полугодий — дело
    ядра, а не квартального слоя. Оба ключа — только парой; нет обоих —
    квартального слоя нет. `margin.quarter_sigma_pp` (σ квартала для нау-каста,
    `indicators.quarterly`) — необязательное положительное число.
    """
    share = A["revenue"].get("quarter_share")
    offset = A["margin"].get("quarter_offset_pp")
    if share is None and offset is None:
        return None
    if share is None or offset is None:
        raise BookError("книга: revenue.quarter_share и margin.quarter_offset_pp — только парой")
    for where, block in (("revenue.quarter_share", share), ("margin.quarter_offset_pp", offset)):
        if not isinstance(block, dict):
            raise BookError(f"книга: {where} — ожидается блок {{Q1..Q4}}")
        _refuse_unknown(block, frozenset(QUARTERS), where)
        _require(block, QUARTERS, where)
        for q in QUARTERS:
            if not _is_number(block[q]):
                raise BookError(f"книга: {where}.{q} = {block[q]!r} — ожидается число")
    for pair in (("Q1", "Q2"), ("Q3", "Q4")):
        total = share[pair[0]] + share[pair[1]]
        if share[pair[0]] <= 0.0 or share[pair[1]] <= 0.0 or abs(total - 1.0) > QUARTER_SHARE_TOL:
            raise BookError(f"книга: revenue.quarter_share — доли кварталов внутри полугодия, "
                            f"положительные, {pair[0]} + {pair[1]} = 1 (сумма пары {total!r})")
    sigma = A["margin"].get("quarter_sigma_pp")
    if sigma is not None and (not _is_number(sigma) or sigma <= 0.0):
        raise BookError(f"книга: margin.quarter_sigma_pp = {sigma!r} — ожидается σ квартала > 0")
    for pair in (("Q1", "Q2"), ("Q3", "Q4")):
        weighted = sum(share[q] * offset[q] for q in pair)
        if abs(weighted) > QUARTER_SUM_TOL:
            raise BookError(f"книга: margin.quarter_offset_pp {pair[0]}+{pair[1]} с весами выручки "
                            f"{weighted!r} ≠ 0 — маржа полугодия изменилась бы")
    return dict(share={q: float(share[q]) for q in QUARTERS},
                offset={q: float(offset[q]) for q in QUARTERS})


# ------------------------------------------------ правила capex и сети
#
# Числовые правила книги (поступления от выбытия ОС, физическая доля
# поддерживающего capex) читаются только здесь и проверяются строго: доли —
# числа в своих границах. Правила сети — у сегментов (`segments`).


@dataclass(frozen=True)
class CapexNetworkRules:
    """Числовые правила capex книги с проверенными значениями."""

    disposal_proceeds_pct: float            # capex.disposal_proceeds_pct
    maintenance_area_share: float           # capex.maintenance_area_share
    # Состояние, которое пишет перезаякоривание (ops/tools/reanchor.py), чтобы
    # правила продолжались через якорь без скачка; в свежей книге ключей нет.
    maintenance_area_base: float | None = None  # capex.maintenance_area_base (нет — первое полугодие)
    da_state: tuple | None = None           # facts.da_straight_line: (база, полугодий, когорты)


def _book_number(block: dict, key: str, default: float | None, where: str,
                 low: float, high: float, low_open: bool = False) -> float:
    """Число книги в границах; `default=None` — ключ обязателен."""
    if default is None and key not in block:
        raise _missing(f"{where}.{key}")
    raw = block.get(key, default)
    if (isinstance(raw, bool) or not isinstance(raw, (int, float))
            or not (low < raw if low_open else low <= raw) or not raw <= high):
        bound = f"({low}; {high}]" if low_open else f"[{low}; {high}]"
        raise BookError(f"книга: {where}.{key} = {raw!r} — ожидается число в {bound}")
    return float(raw)


def capex_network_rules(A: dict) -> CapexNetworkRules:
    """Числовые ключи правил capex и перенесённое через якорь состояние D&A."""
    C = A["capex"]
    F = A.get("facts") or {}
    # «Не задано» — отсутствие ключа, а не ноль: ноль — число, и на нём правило
    # делило бы на ноль (физическая доля).
    base = (_book_number(C, "maintenance_area_base", None, "capex", 0.0, float("inf"),
                         low_open=True) if "maintenance_area_base" in C else None)
    state = F.get("da_straight_line")
    if state is not None:
        if (not isinstance(state, dict) or set(state) != {"legacy", "legacy_halves", "vintages"}
                or isinstance(state["legacy_halves"], bool)
                or not isinstance(state["legacy_halves"], int) or state["legacy_halves"] < 0
                or not isinstance(state["vintages"], list)):
            raise BookError("книга: facts.da_straight_line — ожидается {legacy, legacy_halves, "
                            f"vintages}}, а не {state!r}")
        state = (_book_number(state, "legacy", 0.0, "facts.da_straight_line", 0.0, float("inf")),
                 state["legacy_halves"],
                 tuple(_book_number({"v": v}, "v", 0.0, "facts.da_straight_line.vintages",
                                    0.0, float("inf")) for v in state["vintages"]))
    return CapexNetworkRules(
        maintenance_area_base=base, da_state=state,
        disposal_proceeds_pct=_book_number(C, "disposal_proceeds_pct", None, "capex", 0.0, 1.0),
        maintenance_area_share=_book_number(C, "maintenance_area_share", None, "capex", 0.0, 1.0),
    )


# ------------------------------------------------------------ пути в книге
#
# Путь — ключи через точку (`valuation.beta_u`, `tax.permanent_addback_pct.2028`).
# Элемент списка строк с полем `id` адресуется селектором `[id]`:
# `bridge.items[put].amount` — поле `amount` строки моста с id `put`. Ключи —
# строки, как в YAML книги (`"2028"`, `LT`), поэтому путь режется по точкам без
# попытки угадать число.


def path_segments(dotted: str) -> list[str | tuple[str, str]]:
    """«a.b[x].c» → ["a", ("b", "x"), "c"]."""
    out: list[str | tuple[str, str]] = []
    for part in dotted.split("."):
        if part.endswith("]") and "[" in part:
            key, _, selector = part[:-1].partition("[")
            out.append((key, selector))
        else:
            out.append(part)
    return out


def _step(node: Any, segment: str | tuple[str, str], dotted: str) -> Any:
    if isinstance(segment, tuple):
        key, selector = segment
        rows = node[key] if isinstance(node, dict) else None
        if not isinstance(rows, list):
            raise KeyError(f"{dotted}: {key} — не список строк")
        for row in rows:
            if isinstance(row, dict) and row.get("id") == selector:
                return row
        raise KeyError(f"{dotted}: в {key} нет строки с id {selector!r}")
    return node[segment]


def get_path(A: dict, dotted: str) -> Any:
    """Значение книги по пути (с селекторами `[id]` для списков строк)."""
    node = A
    for segment in path_segments(dotted):
        node = _step(node, segment, dotted)
    return node


def path_parent(A: dict, dotted: str, *, create: bool = False) -> tuple[dict, str]:
    """(блок, последний ключ) пути; `create=True` — недостающие блоки создаются."""
    segments = path_segments(dotted)
    if isinstance(segments[-1], tuple):
        raise KeyError(f"{dotted}: путь кончается строкой списка, а не ключом")
    node = A
    for i, segment in enumerate(segments[:-1]):
        if not isinstance(segment, tuple) and create and isinstance(node, dict) and segment not in node:
            node[segment] = {}
        node = _step(node, segment, dotted)
        if not isinstance(node, dict):
            raise KeyError(f"{dotted}: {'.'.join(map(str, segments[:i + 1]))} — не блок книги")
    return node, segments[-1]


# ------------------------------------------------------------------ периоды


def periods(first: str, last: str) -> list[str]:
    """Полугодия от `first` до `last` включительно: '2026H2' … '2036H2'."""
    year, half = int(first[:4]), int(first[5])
    out: list[str] = []
    while True:
        out.append(f"{year}H{half}")
        if out[-1] == last:
            return out
        year, half = (year, 2) if half == 1 else (year + 1, 1)


def period_index(p: str) -> int:
    """Сквозной номер полугодия — для расстояний между наблюдениями."""
    return 2 * int(p[:4]) + int(p[5]) - 1


def previous_same_half(p: str) -> str:
    """То же полугодие прошлого года: база расчёта «год к году»."""
    return f"{int(p[:4]) - 1}H{p[5]}"


def previous_period(p: str) -> str:
    """Предыдущее полугодие."""
    year, half = int(p[:4]), int(p[5])
    return f"{year}H1" if half == 2 else f"{year - 1}H2"


# --------------------------------------------------------------- траектории


def _is_period(key: str) -> bool:
    key = str(key)
    return len(key) == 6 and key[:4].isdigit() and key[4] == "H" and key[5] in "12"


# Память значений траекторий на время ОДНОЙ сетки (`memoized_paths`).
#
# Сетка — 36 проходов клеток по одной и той же книге, и каждый проход спрашивает
# одни и те же траектории в одних и тех же периодах (≈300 вызовов на клетку).
# Полоса неопределённости книги 1.4 считает 2 000 сеток, и разбор траекторий
# занимал треть её времени. Ключ — объект траектории и период; запись ДЕРЖИТ
# сам объект, поэтому его id не может достаться другому словарю, пока память
# жива. Память живёт только внутри `with memoized_paths()`: книгу в это время
# никто не правит, а вне блока всё считается заново, как прежде.
_PATH_MEMO: dict | None = None


class memoized_paths:
    """Контекст, в котором `path_value` запоминает ответы (вложенный — общий)."""

    def __enter__(self):
        global _PATH_MEMO
        self._outer = _PATH_MEMO
        if _PATH_MEMO is None:
            _PATH_MEMO = {}
        return self

    def __exit__(self, *exc):
        global _PATH_MEMO
        if self._outer is None:
            _PATH_MEMO = None
        return False


def path_value(spec: Any, p: str) -> float:
    """Значение траектории в периоде `p`.

    Порядок разрешения, заданный форматом книги: точный ключ периода → ключ
    года → линейная интерполяция между заданными годами → сход к `LT` к году
    `LT_from` (по умолчанию — сразу после последнего заданного года).
    """
    if isinstance(spec, (int, float)):
        return float(spec)
    memo = _PATH_MEMO
    if memo is not None:
        hit = memo.get((id(spec), p))
        if hit is not None and hit[0] is spec:
            return hit[1]
        value = _path_value(spec, p)
        memo[(id(spec), p)] = (spec, value)
        return value
    return _path_value(spec, p)


def _path_value(spec: Any, p: str) -> float:
    if p in spec:
        return float(spec[p])
    year = int(p[:4])
    if str(year) in spec:
        return float(spec[str(year)])

    points = sorted((int(k), float(v)) for k, v in spec.items() if str(k).isdigit())
    lt = spec.get("LT")
    if not points:
        # Траектория задана только ключами периодов (как миры книги: 2026H2 …
        # 2036H2). Внутри горизонта книги сюда не попадают — только бэктест,
        # который якорится раньше её начала. Назад продлеваем первым значением:
        # это явная и проверяемая конвенция, а не молчаливая экстраполяция.
        period_keys = sorted(k for k in spec if _is_period(k))
        if period_keys and p < period_keys[0]:
            return float(spec[period_keys[0]])
        if lt is None:
            raise KeyError(f"траектория не покрывает период {p}: {spec}")
        return float(lt)
    if year <= points[0][0]:
        return points[0][1]
    if year >= points[-1][0]:
        if lt is None:
            return points[-1][1]
        lt_year = int(spec.get("LT_from", points[-1][0] + 1))
        if year >= lt_year:
            return float(lt)
        span = max(1, lt_year - points[-1][0])
        return points[-1][1] + (year - points[-1][0]) / span * (float(lt) - points[-1][1])
    i = bisect.bisect_left([y for y, _ in points], year)
    (y0, v0), (y1, v1) = points[i - 1], points[i]
    return v0 + (v1 - v0) * (year - y0) / (y1 - y0)


def interp_curve(curve: dict, tenor: float) -> float:
    """Бескупонная кривая мира. За последним узлом — линейный сход к `LT`
    за пять лет (конвенция книги).

    Внутри `memoized_paths` ответ запоминается так же, как у `path_value`:
    сроки дисконтирования у 12 клеток одного мира одни и те же.
    """
    memo = _PATH_MEMO
    if memo is not None:
        key = (id(curve), "curve", tenor)
        hit = memo.get(key)
        if hit is not None and hit[0] is curve:
            return hit[1]
        value = _interp_curve(curve, tenor)
        memo[key] = (curve, value)
        return value
    return _interp_curve(curve, tenor)


def _interp_curve(curve: dict, tenor: float) -> float:
    points = sorted((float(k), float(v)) for k, v in curve.items() if k != "LT")
    if tenor <= points[0][0]:
        return points[0][1]
    for (t0, z0), (t1, z1) in zip(points, points[1:]):
        if t0 <= tenor <= t1:
            return z0 + (z1 - z0) * (tenor - t0) / (t1 - t0)
    t_last, z_last = points[-1]
    lt = float(curve.get("LT", z_last))
    return z_last + (lt - z_last) * min(1.0, (tenor - t_last) / 5.0)


def half_rate(annual: float) -> float:
    """Годовая ставка в полугодовую. Корень, а не половина."""
    return (1.0 + annual) ** 0.5 - 1.0


# ------------------------------------------------------------------- клетка


@dataclass(frozen=True)
class Cell:
    """Клетка сетки: мир × режим маржи × уровень capex плюс привязки.

    Привязки не свободны: рост сети, оборотный капитал и кредитные спреды
    определяются миром (`joint.by_world`), состояние спроса — режимом маржи
    (`joint.demand_by_regime`). Это и есть согласованность сценария.
    """

    world: str
    margin_regime: str
    capex: str
    growth: str
    demand: str
    nwc: str
    credit: str

    @classmethod
    def build(cls, A: dict, world: str, regime: str, capex: str) -> "Cell":
        link = A["joint"]["by_world"][world]
        # Режим маржи может ПЕРЕБИТЬ траекторию площади (Р4б): в стрессе сеть
        # не растёт при любом мире ставок — иначе при марже 4 % сеть росла бы на
        # 29 %, а чистый долг уходил бы к 7× EBITDA.
        overrides = A["joint"].get("growth_by_regime_override")
        if overrides is None:
            raise _missing("joint.growth_by_regime_override")
        override = overrides.get(regime)
        return cls(
            world=world, margin_regime=regime, capex=capex,
            growth=override or link["growth"],
            demand=A["joint"]["demand_by_regime"][regime],
            nwc=link["nwc"], credit=link["credit"],
        )

    @classmethod
    def from_dict(cls, d: dict) -> "Cell":
        return cls(**{k: d[k] for k in
                      ("world", "margin_regime", "capex", "growth", "demand", "nwc", "credit")})

    def as_dict(self) -> dict[str, str]:
        return {
            "world": self.world, "margin_regime": self.margin_regime, "capex": self.capex,
            "growth": self.growth, "demand": self.demand, "nwc": self.nwc, "credit": self.credit,
        }

    @property
    def key(self) -> str:
        return f"{self.world}|{self.margin_regime}|{self.capex}"


def all_cells(A: dict) -> list[Cell]:
    """36 клеток: 3 мира × 4 режима маржи × 3 уровня capex."""
    return [
        Cell.build(A, w, r, c)
        for w in A["joint"]["world_prob"]
        for r in A["margin"]["regimes"]
        for c in A["capex"]["maintenance_pct"]
    ]


def named_cells(A: dict) -> dict[str, Cell]:
    return {name: Cell.from_dict(spec) for name, spec in A["scenarios"].items()}
