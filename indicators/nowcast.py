"""Нау-каст ближайшего квартального отчёта Ленты: `lenta-margin-v1`.

**Что это.** Маржа EBITDA (до МСФО 16) ближайшего неотчитавшегося квартала
(сегодня — 3 кв. 2026, отчёт ≈29–30.10.2026) = **ожидание модели** на квартал
(`indicators.quarterly.expected_quarter`: маржа полугодия ядра + квартальная
поправка книги) со своей ошибкой. Ни одного подгоняемого коэффициента и ни
одного внешнего ряда: главный урок 850oa — внешние бесплатные ряды не
улучшают прогноз маржи EBITDA целиком (study/04 §0, §6). Любое будущее
слагаемое — новая версия уравнения и обнуление счёта допуска; добавлять только
то, что доказано на истории вне выборки.

**К цене не подключён.** Три пути влияния на оценку (стартовая точка, приоры
драйверов, вероятности режимов по A-P2u) остаются закрытыми до решения
владельца по правилу допуска (`journal.admission`): не раньше 8 отчётных
кварталов вне выборки и отношение MSE к лучшему эталону ≤ 0,8. В правило A-P2u
идёт полугодие (D2), поэтому и путь (в) — `apply_to_book` — подаёт ПОЛУГОДИЕ.

**Почему ожидание, а не цель модального режима** (margin-v4 у 850oa, аудит
D10-2): модальная цель выше ожидания, и прогноз без единого индикатора уже
«говорил» бы приор книги вторым разом. Без данных нау-каст РАВЕН ожиданию, а
всё, что скажут индикаторы, — `deviation`. Сегодня отклонение — ноль.

**Для экрана «Ближайший отчёт»** — `implied_half` (какая маржа 2П следует из
факта 3 кв., `quarterly.implied_half_margin`) и статус допуска.

Канал процентов (`interest_nowcast`) — общий из 850oa на реестре траншей;
переход на корзины ставок книги (`financing.rate_baskets`, D12) — заметка в
`indicators/interest.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from indicators import periods
from indicators.store import Store

MARGIN_VERSION = "lenta-margin-v1"
"""Версия уравнения маржи — одна на журнал, выпуск и правило допуска.

Правило допуска (`journal.admission`) считает отчёты, зачтённые ИМЕННО ЭТОЙ
версии: прогнозы прежних формул в её трек-рекорд не входят.

lenta-margin-v1: ожидание модели на квартал (маржа полугодия ядра + поправка
квартала книги `margin.quarter_offset_pp`), ошибка — σ квартала
(`quarterly.quarter_se`). Слагаемых нет."""

REVENUE_VERSION = "lenta-revenue-v1"
"""Выручка квартала = выручка полугодия ядра × доля квартала книги
(`revenue.quarter_share`); ошибки выручки книга не задаёт — `std_error` нет."""

EQUATION = ("маржа квартала = ожидание модели (маржа полугодия ядра: цели режимов, "
            "взвешенные вероятностями книги, и затухающее отклонение внесённого факта) "
            "+ квартальная поправка книги")


@dataclass(frozen=True)
class MarginNowcast:
    """Прогноз маржи квартала — главный выход слоя."""

    period: str
    value: float
    std_error: float
    equation: str
    version: str
    inputs: dict = field(default_factory=dict)
    components: dict = field(default_factory=dict)
    expectation: float | None = None
    """Ожидание модели на квартал — база уравнения и эталон журнала."""
    half: str | None = None
    half_value: float | None = None
    """Маржа полугодия, которой соответствует прогноз квартала (для A-P2u, D2)."""
    half_std_error: float | None = None
    revenue: float | None = None
    """Ожидание выручки квартала, млрд ₽."""
    source: str = ""
    connected_to_price: bool = False
    """Всегда ложь: подключение — решение владельца (D15)."""
    modal_target: float | None = None
    modal_regime: str | None = None
    """Поля выпуска 850oa (цель модального режима). У квартального нау-каста их
    нет; оставлены пустыми, пока витрина (P4) читает их по имени."""

    @property
    def deviation(self) -> float | None:
        """Отклонение нау-каста от ожидания модели — то, что сказали индикаторы."""
        return None if self.expectation is None else self.value - self.expectation


def margin_nowcast(store: Store | None, A: dict, period: str, *,
                   expectation=None) -> MarginNowcast:
    """Прогноз маржи квартала `period` (`2026Q3`).

    `expectation` — готовое `QuarterExpectation` (или функция `(A, квартал)`,
    его возвращающая): так тесты подставляют двойника ядра, а такт не считает
    сетку дважды. По умолчанию — `quarterly.expected_quarter`.
    `store` — вход будущих слагаемых; сегодня не читается.
    """
    from indicators import quarterly

    if not periods.is_quarter(period):
        raise ValueError(f"{period}: нау-каст квартальный (ГГГГQn)")
    if expectation is None:
        expected = quarterly.expected_quarter(A, period)
    elif callable(expectation):
        expected = expectation(A, period)
    else:
        expected = expectation
    sigma_half = float(A["joint"]["regime_update"]["sigma_pp"])
    inputs = dict(expectation=round(expected.margin, 6),
                  half_margin=round(expected.half_margin, 6),
                  offset_pp=round(expected.offset_pp, 6), share=round(expected.share, 6),
                  source=expected.source)
    return MarginNowcast(
        period=period, value=expected.margin, std_error=expected.margin_se,
        equation=EQUATION, version=MARGIN_VERSION, inputs=inputs,
        components=dict(base=expected.half_margin, quarter_offset=expected.offset_pp),
        expectation=expected.margin, half=expected.half, half_value=expected.half_margin,
        half_std_error=sigma_half, revenue=expected.revenue, source=expected.source)


def implied_half(nowcast_or_period, quarter_fact: float, A: dict, *,
                 other_quarter_expectation: float | None = None) -> dict:
    """«Какая маржа 2П следует из факта 3 кв.» — для экрана «Ближайший отчёт».

    Обёртка `quarterly.implied_half_margin` на ключах книги; ожидание второго
    квартала передаёт вызывающий (`quarterly.expected_quarter(A, квартал).margin`),
    без него считается только следствие при полной персистентности.
    """
    from indicators import quarterly

    quarter = getattr(nowcast_or_period, "period", nowcast_or_period)
    expectation = getattr(nowcast_or_period, "expectation", None)
    return quarterly.implied_half_margin(
        quarter_fact, quarter=quarter,
        quarter_offset_pp=A["margin"]["quarter_offset_pp"],
        quarter_share=A["revenue"]["quarter_share"],
        other_quarter_expectation=other_quarter_expectation,
        quarter_expectation=expectation)


def model_expectation(A: dict, period: str) -> tuple[float, str, float]:
    """Ожидание маржи ПОЛУГОДИЯ по модели: (ожидание, модальный режим, его цель).

    Ожидание — средняя маржа ЯДРА в полугодии по сетке клеток
    (`model.grid.build_grid`: Σ вероятность клетки × маржа её строки), а не
    пересказ формулы ядра. Без наблюдений внутри горизонта это ровно
    Σ P(режим) × (цель режима + сезонность). Наблюдения и шоки нау-каста за
    сам период и позже в расчёт не входят (иначе база была бы кругом). Период
    вне горизонта сетки — ожидание по целям режимов и сезонности.
    """
    from model.book import path_value
    from model.core import margin_season
    from model.grid import build_grid, regime_unconditional

    A = _without_own_forecasts(A, period)
    probabilities = regime_unconditional(A)
    season = margin_season(A, period)
    targets = {name: path_value(spec["target"], period) + season
               for name, spec in A["margin"]["regimes"].items()}
    modal = max(probabilities, key=probabilities.get)
    margins = [(cell.probability, row.margin) for cell in build_grid(A)
               for row in cell.result.rows if row.period == period]
    if margins:
        expectation = (sum(p * m for p, m in margins)
                       / sum(p for p, _ in margins))
    else:
        expectation = sum(probabilities[name] * targets[name] for name in targets)
    return expectation, modal, targets[modal]


def _without_own_forecasts(A: dict, period: str) -> dict:
    """Книга без наблюдений и шоков маржи за `period` и позже (копия, если они есть)."""
    import copy

    update = A["joint"].get("regime_update") or {}
    observations = update.get("observations") or {}
    shocks = A["margin"].get("nowcast_margin_shocks_pp") or {}
    if not any(p >= period for p in observations) and not any(p >= period for p in shocks):
        return A
    trial = copy.deepcopy(A)
    trial["joint"]["regime_update"]["observations"] = {
        p: v for p, v in observations.items() if p < period}
    trial["margin"]["nowcast_margin_shocks_pp"] = {
        p: v for p, v in shocks.items() if p < period}
    return trial


def apply_to_book(A: dict, nowcast: MarginNowcast) -> dict:
    """Путь (в): прогноз → вероятности режимов по правилу A-P2u. ПОЛУГОДИЕ (D2).

    Копия книги, а не правка на месте. Квартальный нау-каст подаёт маржу
    своего полугодия (`half_value`) с ошибкой полугодия; прогноз ДОПОЛНЯЕТ
    внесённые наблюдения, а не заменяет их. Сейчас не вызывается ничем:
    подключение — решение владельца.
    """
    from model.grid import with_observation

    half = nowcast.half or nowcast.period
    value = nowcast.half_value if nowcast.half_value is not None else nowcast.value
    se = nowcast.half_std_error if nowcast.half_std_error is not None else nowcast.std_error
    if not periods.is_half(half):
        raise ValueError(f"{half}: в правило A-P2u подаётся полугодие")
    return with_observation(A, half, {"value": value, "se": se})


# ------------------------------------------------------------------ проценты


@dataclass(frozen=True)
class InterestNowcast:
    """Канал 1: почти детерминированный прогноз чистых процентов периода."""

    period: str
    net_interest: float
    debt_interest: float
    cash_income: float
    average_key_rate: float
    average_debt: float
    average_cash: float
    floating_share: float
    effective_rate: float
    implied_spread: float
    opening_gross_debt: float = 0.0
    redemptions_count: int = 0
    redemptions_done: int = 0
    redemptions_ahead: int = 0
    redemptions_done_amount: float = 0.0
    redemptions_ahead_amount: float = 0.0
    redemptions_note: str = ""
    survey_delta: float | None = None
    by_kind: dict = field(default_factory=dict)
    inputs: dict = field(default_factory=dict)


def _row_of(rows, period: str):
    """Строка ядра за ПОЛУГОДИЕ периода (у квартала — его полугодие)."""
    half = period if periods.is_half(period) else periods.half_of(periods.bounds(period)[0])
    order = [r.period for r in rows]
    if half not in order:
        return None, None
    index = order.index(half)
    return rows[index], (rows[index - 1] if index > 0 else None)


def interest_nowcast(store: Store, A: dict, period: str) -> InterestNowcast:
    """Чистые проценты периода (квартал или полугодие): посуточно по реестру долга.

    Расчёт — `indicators.interest.integrate` (простые проценты на фактические
    дни, остаток взвешен по времени, ставка — факт до последнего наблюдения,
    дальше путь мира книги). Касса привязана к пути чистого долга ядра:
    внутри полугодия — линейно от начала полугодия к концу, квартал берёт
    свою часть пути. Корзины ставок книги (D12) — следующий шаг (заметка в
    `indicators/interest.py`).
    """
    from indicators import interest as channel
    from model.book import Cell
    from model.core import run_cell
    from model.financing import load_debt_register

    start, end = periods.bounds(period)
    register = load_debt_register(include_redeemed=True)
    tranches = [t for t in register if t.maturity >= start]
    on_tranches = channel.tranches_on_day(tranches)

    world = max(A["joint"]["world_prob"], key=A["joint"]["world_prob"].get)
    priors = A["joint"]["regime_given_world"][world]
    regime = max(priors, key=priors.get)
    cell = run_cell(A, Cell.build(A, world, regime, "base"))
    row, previous = _row_of(cell.rows, period)
    half_start, half_end = (periods.bounds(row.period) if row is not None else (start, end))
    opening_net_debt = (previous.net_debt if previous is not None
                        else A["facts"]["anchor"]["net_debt"])
    closing_net_debt = row.net_debt if row else opening_net_debt
    span = (half_end - half_start).days or 1

    def cash_on(day):
        gross = sum(t.principal for t in on_tranches(day))
        share = min(max((day - half_start).days / span, 0.0), 1.0)
        return max(gross - (opening_net_debt
                            + (closing_net_debt - opening_net_debt) * share), 0.0)

    result = channel.integrate(
        start, end, tranches_on=on_tranches, cash_on=cash_on,
        key_on=channel.key_rate_path(store, A),
        deposit_factor=A["financing"]["cash_yield_k"], period=period)

    survey = channel.survey_key_rate_path(store, A)
    survey_delta = None
    if survey is not None:
        alternative = channel.integrate(
            start, end, tranches_on=on_tranches, cash_on=cash_on, key_on=survey,
            deposit_factor=A["financing"]["cash_yield_k"], period=period)
        survey_delta = alternative.net_interest - result.net_interest

    floating = sum(t.principal for t in tranches if t.fixed_rate is None)
    total = sum(t.principal for t in tranches) or 1.0
    return InterestNowcast(
        period=period, net_interest=result.net_interest,
        debt_interest=result.debt_interest, cash_income=result.cash_income,
        average_key_rate=result.average_key_rate, average_debt=result.average_debt,
        average_cash=result.average_cash, floating_share=floating / total,
        effective_rate=result.effective_debt_rate, implied_spread=result.implied_spread,
        by_kind=result.by_kind,
        opening_gross_debt=sum(t.principal for t in tranches),
        survey_delta=survey_delta,
        **_redemptions(tranches, start, end, _as_date(A["meta"]["valuation_date"])),
        inputs=dict(tranches=len(tranches), register_total=round(total, 3),
                    opening_net_debt=round(opening_net_debt, 3),
                    closing_net_debt=round(closing_net_debt, 3),
                    observed_days=len(channel.realised_key_rate(store)),
                    world=world, regime=regime),
    )


def _as_date(stamp: str):
    from datetime import date

    return date.fromisoformat(stamp[:10])


_COUNT_WORDS = {1: "одно", 2: "два", 3: "три", 4: "четыре", 5: "пять",
                6: "шесть", 7: "семь", 8: "восемь", 9: "девять"}


def _count_word(n: int) -> str:
    return _COUNT_WORDS.get(n, str(n))


def _passed_phrase(n: int) -> str:
    """«три погашения уже прошли» — с русским согласованием числа и глагола."""
    word = _count_word(n)
    if n % 10 == 1 and n % 100 != 11:
        return f"{word} погашение уже прошло"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{word} погашения уже прошли"
    return f"{word} погашений уже прошло"


def _bn(value: float) -> str:
    return f"{value:.1f}".replace(".", ",") + " млрд ₽"


def _redemptions(tranches, start, end, as_of) -> dict:
    """Погашения периода, разделённые ДАТОЙ ОЦЕНКИ (из реестра долга)."""
    inside = [t for t in tranches if start <= t.maturity <= end]
    done = [t for t in inside if t.maturity <= as_of]
    ahead = [t for t in inside if t.maturity > as_of]
    done_sum = sum(t.principal for t in done)
    ahead_sum = sum(t.principal for t in ahead)
    if not inside:
        note = "Погашений внутри периода по реестру нет"
    elif not done:
        note = (f"Погашений на дату оценки ещё не было, впереди "
                f"{_count_word(len(ahead))} до конца периода ({_bn(ahead_sum)})")
    elif not ahead:
        note = (f"{_passed_phrase(len(done))} ({_bn(done_sum)}), "
                "до конца периода их больше нет").capitalize()
    else:
        note = (f"{_passed_phrase(len(done))} ({_bn(done_sum)}), ещё "
                f"{_count_word(len(ahead))} — до конца периода "
                f"({_bn(ahead_sum)})").capitalize()
    return dict(redemptions_count=len(inside),
                redemptions_done=len(done), redemptions_ahead=len(ahead),
                redemptions_done_amount=done_sum,
                redemptions_ahead_amount=ahead_sum,
                redemptions_note=note)
