"""Финансирование: реестр долга (отчётный слой) и лимит кредитных линий.

Долг на экране катится по фактическому реестру траншей, а не по средней
ставке на средний остаток: стена рефинансирования и переоценка флоатеров при
смене ключевой — движители стоимости, и усреднение их стирает. Денежный контур
оценки (проценты, погашения, выплаты, касса) считает ядро по книге — там он
согласован с миром ставок клетки (`model.core`). Реестр в оценку не входит.

Реестр — ДАННЫЕ, а не разбор отчётности в коде: `data/facts/debt_register.json`
(схема `debt-register-v1`) перечисляет транши с условиями (купон или спред к
ключевой, срок, дата размещения), кассу на отчётную дату и датированные
события после неё. Спреды действующего долга — поля траншей с источником в
фактах, а не литералы кода.

Правила, которые остаются здесь:

**Отрицательной кассы не бывает.** Дефицит — рост долга, а не «касса в
минус», которая ничего не стоит.

**Лимит линий — функция фактов книги.** Валовой долг отчётной даты плюс
неиспользованные линии (`facts.undrawn_credit_lines` — обязательный факт:
пропуск ключа дал бы лимит, равный текущему долгу, и триггер неустойчивости
сработал бы почти везде).

Единицы: деньги — млрд ₽, ставки — доли единицы годовых.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from model.paths import FACTS_DIR

REGISTER_FILE = "debt_register.json"
REGISTER_SCHEMA = "debt-register-v1"
TRANCHE_KINDS = frozenset({"bond", "bank", "repo", "refinanced", "other"})


@dataclass(frozen=True)
class Tranche:
    """Транш долга: облигационный выпуск, банковская когорта или иной инструмент."""

    name: str
    principal: float
    maturity: date
    issued_on: date | None = None
    """Дата размещения. Без неё реестровый бэктест считает, что выпуск
    платил купон и до размещения: транш существует «всегда»."""
    fixed_rate: float | None = None
    """Фиксированный купон. None — флоатер."""
    spread_to_key: float | None = None
    """Спред к ключевой для флоатера."""
    kind: str = "bond"

    def rate(self, key_rate: float) -> float:
        if self.fixed_rate is not None:
            return self.fixed_rate
        return key_rate + (self.spread_to_key or 0.0)


@dataclass
class FinancingState:
    tranches: list[Tranche] = field(default_factory=list)
    cash: float = 0.0
    revolver: float = 0.0

    @property
    def term_debt(self) -> float:
        return sum(t.principal for t in self.tranches)

    @property
    def total_debt(self) -> float:
        return self.term_debt + self.revolver

    @property
    def net_debt(self) -> float:
        return self.total_debt - self.cash


class RegisterError(ValueError):
    """Реестр долга не читается однозначно."""


def _register_path(path: Path | None) -> Path:
    return path or FACTS_DIR / REGISTER_FILE


def load_register_data(path: Path | None = None) -> dict:
    """Файл реестра целиком (схема `debt-register-v1`) с проверкой формы."""
    data = json.loads(_register_path(path).read_text(encoding="utf-8"))
    if data.get("schema") != REGISTER_SCHEMA:
        raise RegisterError(f"реестр долга: схема {data.get('schema')!r}, ожидается {REGISTER_SCHEMA}")
    for key in ("as_of", "cash", "tranches", "events_after_balance_date"):
        if key not in data:
            raise RegisterError(f"реестр долга: нет ключа {key}")
    return data


def _tranche(raw: dict, where: str) -> Tranche:
    for key in ("name", "principal", "maturity", "kind"):
        if key not in raw:
            raise RegisterError(f"реестр долга: {where} — нет ключа {key}")
    if raw["kind"] not in TRANCHE_KINDS:
        raise RegisterError(f"реестр долга: {where}.kind = {raw['kind']!r}")
    fixed, spread = raw.get("fixed_rate"), raw.get("spread_to_key")
    if (fixed is None) == (spread is None):
        raise RegisterError(f"реестр долга: {where} — ровно одно из fixed_rate и spread_to_key")
    return Tranche(
        name=raw["name"], principal=float(raw["principal"]),
        maturity=date.fromisoformat(raw["maturity"]),
        issued_on=date.fromisoformat(raw["issued_on"]) if raw.get("issued_on") else None,
        fixed_rate=fixed, spread_to_key=spread, kind=raw["kind"])


def load_debt_register(path: Path | None = None, *, include_redeemed: bool = False
                       ) -> list[Tranche]:
    """Транши реестра в порядке файла.

    `include_redeemed` возвращает и уже погашенные транши (`status: redeemed`),
    у которых срок — ДЕНЬ ПОГАШЕНИЯ. Это нужно каналу процентов: погашенные в
    текущем полугодии выпуски до своих дат платили купон, и реестр «как
    сегодня» занижал бы проценты полугодия.
    """
    out = []
    for i, raw in enumerate(load_register_data(path)["tranches"]):
        if raw.get("status") == "redeemed" and not include_redeemed:
            continue
        out.append(_tranche(raw, f"tranches[{i}]"))
    return out


def initial_financing_state(as_of: date, path: Path | None = None) -> FinancingState:
    """Состояние на дату оценки: реестр минус датированные погашения, касса
    минус эти же погашения. Никакого «расчётного открытия» прогоном модели."""
    data = load_register_data(path)
    tranches = [t for t in load_debt_register(path) if t.maturity > as_of]
    cash = float(data["cash"])
    for event in data["events_after_balance_date"]:
        if date.fromisoformat(event["date"]) <= as_of:
            cash -= event["principal"]
    return FinancingState(tranches=tranches, cash=cash)


def register_state(as_of: date, new_debt_spread: float, path: Path | None = None) -> FinancingState:
    """Реестр для ЭКРАНА «Деньги и долг»: срок без события — рефинансирование.

    `initial_financing_state` убирает транш, чей срок наступил, а кассу не
    трогает: погашение, о котором в фактах нет события, выглядело бы как долг,
    исчезнувший сам собой. Банковские когорты раскрыты обычно только годами
    сроков, событий по ним нет, и реестровый чистый долг падал бы без единого
    события. Здесь такой транш остаётся в долге: он рефинансирован на год под
    спред нового долга книги (`new_debt_spread`), срок переносится на
    годовщину после даты оценки. Погашения с событием
    (`events_after_balance_date`) по-прежнему уменьшают кассу, а погашенные
    транши в реестр не входят.

    Оценку это не меняет: ядро читает долг из книги и моста, а не из реестра.
    """
    state = initial_financing_state(as_of, path)
    for t in load_debt_register(path):
        if t.maturity > as_of:
            continue
        maturity = t.maturity
        while maturity <= as_of:
            maturity = _next_anniversary(maturity)
        state.tranches.append(Tranche(f"{t.name}, рефинансирование", t.principal, maturity,
                                      issued_on=t.maturity, spread_to_key=new_debt_spread,
                                      kind="refinanced"))
    return state


def _next_anniversary(day: date) -> date:
    """Та же дата через год; 29 февраля — 28-е."""
    try:
        return day.replace(year=day.year + 1)
    except ValueError:
        return day.replace(year=day.year + 1, day=28)


def refinancing_wall(tranches: list[Tranche]) -> dict[int, float]:
    """Стена рефинансирования по годам — показывается на экране долга."""
    wall: dict[int, float] = {}
    for t in tranches:
        wall[t.maturity.year] = wall.get(t.maturity.year, 0.0) + t.principal
    return dict(sorted(wall.items()))


def rate_sensitivity(tranches: list[Tranche], key_rate: float, shift: float = 0.01) -> float:
    """Сколько стоит 1 п.п. ключевой ставки в год ВАЛОВО — только по флоатерам."""
    floating = sum(t.principal for t in tranches if t.fixed_rate is None)
    return floating * shift


def rate_sensitivity_net(tranches: list[Tranche], cash: float, *, deposit_factor: float,
                         floating_share: float | None = None,
                         shift: float = 0.01) -> float:
    """То же, но НЕТТО: касса тоже переоценивается вместе со ставкой.

    Валовая чувствительность завышает боль от подъёма ставки, когда на
    депозитах лежат сотни миллиардов: касса дорожает вместе с долгом.

    `floating_share` — доля плавающего долга ИЗ КНИГИ (A-F4). Реестр может
    считать плавающим весь банковский портфель, если его состав не раскрыт, и
    без этого параметра доля выходит выше книжной.
    """
    if floating_share is None:
        floating = sum(t.principal for t in tranches if t.fixed_rate is None)
    else:
        floating = sum(t.principal for t in tranches) * floating_share
    return (floating - cash * deposit_factor) * shift


def credit_limit(A: dict) -> float:
    """Потолок валового долга: долг отчётной даты плюс неиспользованные кредитные линии.

    Смысл контроля: модель позволяет долгу расти сколько угодно, потому что
    ветки докапитализации в ней нет. Клетка, где долг перерастает доступные
    линии, физически означает либо допэмиссию, либо реструктуризацию — и то и
    другое обнулило бы долю нынешнего акционера сильнее, чем это считает
    модель. Поэтому превышение не «чинится» расчётом, а помечается флагом.

    `facts.undrawn_credit_lines` обязателен (null ≠ 0).
    """
    F = A["facts"]
    anchor = F["anchor"]
    gross = anchor["net_debt"] + anchor["cash"]
    return gross + F["undrawn_credit_lines"]
