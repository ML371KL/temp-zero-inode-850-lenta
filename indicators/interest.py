"""Канал 1: чистые проценты полугодия. Самый точный канал слоя.

Аудит второй итерации: «канал процентов (42,3 млрд ₽ за 2П2026) считается по
срезу „на сегодня“, 88 % долга считается плавающим, будущей траектории
ключевой нет; собственное ядро даёт 44,7 — расхождение больше заявленных
±5 %». Здесь исправлено четыре вещи, и каждая меняет ответ в одну сторону —
вверх.

**Простые проценты, а не сложные.** Было `(1+ставка)^0,5 − 1`: полугодие
считалось как половина СЛОЖНОГО года. Российские купоны и банковские
проценты начисляются простыми на номинал за фактические дни; при ставке 15 %
разница составляет 2,8 % суммы процентов — и всегда в минус.

**Долг взвешен по времени, а не взят срезом.** Было: остаток «на сегодня»
умножался на всё полугодие. Но 2П2026 началось с 922 млрд ₽, а срез на
21 сентября показывает 763: четыре погашения (13.07 — 25,0; 03.08 — 1,05;
27.08 — 84,8; 11.09 — 46,5) уже прошли. Средневзвешенный остаток полугодия —
810, то есть на 6 % больше среза.

**У ставки есть будущее.** Было: средняя ключевая по УЖЕ наблюдённым дням
полугодия. В сентябре это половина правды, в июле — ничего. Здесь ставка
берётся фактическая до последнего наблюдения и по траектории мира из книги
дальше: тот же путь, по которому считает ядро, иначе канал и оценка
разойдутся.

**Факт очищен от непроцентных статей.** Финансовые расходы по МСФО содержат
проценты по АРЕНДЕ (база расчёта — до МСФО 16), размотку обязательств по
опционам, проценты по займам связанных сторон и прочее. Собственно проценты по
долгу — проценты по облигациям плюс проценты по кредитам третьих лиц; минус
процентный доход — чистые проценты (у 850oa очистка сдвинула факт 1П2026 с
48,1 до 45,7 млрд ₽). Канал, который меряют против неочищенного числа, обязан
завышать.

Определение держится в одном месте —
`data/facts/accounting_base.json → net_interest_pre_ifrs16`, — и прогноз,
эталон и факт берут его оттуда (A5б).

Единицы: деньги — млрд ₽, ставки — доли единицы годовых.

**Заметка для Ленты (P3 → книга 1.0 / P4).** Канал оставлен общим: он
считает по реестру траншей (`data/facts/debt_register.json`, схема
`debt-register-v1`) и по пути ключевой ставки мира книги. У Ленты долг
разложен книгой на корзины ставок (`financing.rate_baskets`: фиксированный
69 %, плавающий 26 %, облигации «О'КЕЙ»; займ Севергрупп — отдельный транш,
D12). Когда реестр Ленты ляжет в `data/facts`, канал надо сверить с корзинами:
либо реестр строится из них, либо корзины — агрегат реестра; вторая траектория
ставок, расходящаяся с книгой, недопустима. Эталон «прошлый период ×
отношение средних ставок» написан для плавающего долга — при доле 26 % он
будет мерить структуру долга, а не эталон (урок 850oa, study/04 §5.4).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Iterable

from indicators import periods
from model.financing import Tranche

from model.paths import FACTS_DIR as FACTS

DAY_BASIS = 365
"""Фактические дни на 365. Так считают и купоны, и банковские проценты."""


@dataclass(frozen=True)
class InterestResult:
    period: str
    start: date
    end: date
    days: int
    debt_interest: float
    cash_income: float
    average_debt: float
    average_cash: float
    average_key_rate: float
    by_kind: dict[str, float] = field(default_factory=dict)

    @property
    def net_interest(self) -> float:
        return self.debt_interest - self.cash_income

    @property
    def effective_debt_rate(self) -> float:
        if not self.average_debt:
            return 0.0
        return self.debt_interest / self.average_debt * DAY_BASIS / self.days

    @property
    def effective_cash_yield(self) -> float:
        if not self.average_cash:
            return 0.0
        return self.cash_income / self.average_cash * DAY_BASIS / self.days

    @property
    def implied_spread(self) -> float:
        return self.effective_debt_rate - self.average_key_rate


# ----------------------------------------------------------------- периоды
#
# Границы периода — `indicators.periods.bounds` (квартал или полугодие): у
# 850oa здесь жил свой `half_bounds`, и канал умел только полугодия.


def days_in(period: str) -> int:
    return periods.days_in(period)


# -------------------------------------------------------------- пути ставок


def realised_key_rate(store) -> dict[date, float]:
    series = store.load("cbr.key_rate")
    if not series:
        return {}
    return {date.fromisoformat(p[:10]): v for p, v in series.history().items()}


def key_rate_path(store, A: dict, *, world: str | None = None) -> Callable[[date], float]:
    """Ключевая ставка на любой день: факт, пока он есть, дальше — книга.

    Почему траектория берётся из книги, а не из кривой ОФЗ. Кривая даёт
    ожидаемый путь КОРОТКОЙ ОФЗ, а не ключевой: 21.09.2026 нулевая ставка на
    0,25 года равна 12,50 % при ключевой 14,00 %, то есть уровень отличается
    на полтора пункта, и «взять кривую как ключевую» означало бы занизить
    проценты на весь этот разрыв. Строить же из кривой отдельную модель
    ключевой — значит завести в панели вторую траекторию ставок, которая
    разойдётся с той, по которой считается оценка. Книга уже согласована с
    кривой; канал берёт её.
    """
    from model.book import path_value

    realised = realised_key_rate(store)
    last_observed = max(realised) if realised else None
    days = sorted(realised)
    world = world or A["joint"]["macro_neutral_world"]
    curve = A["worlds"][world]["key_rate"]

    def on(day: date) -> float:
        if last_observed is not None and day <= last_observed:
            earlier = [d for d in days if d <= day]
            if earlier:
                return realised[earlier[-1]]
            return realised[days[0]]
        return path_value(curve, "%dH%d" % (day.year, 1 if day.month <= 6 else 2))

    return on


SURVEY_SERIES = "cbr.key_rate_survey"


def survey_key_rate_path(store, A: dict) -> "Callable[[date], float] | None":
    """Ключевая ставка по ВНЕШНЕМУ прогнозу — макроэкономическому опросу ЦБ.

    Зачем она нужна и почему только чувствительностью. Мир M книги — это
    форварды ОФЗ, и они говорят о ставке своё: во 2П2027 книга ставит 15,9 %
    при цикле снижения. Внешний прогноз — второе мнение, и сравнение с ним
    честнее, чем спор с самим собой. Но база остаётся книжной: подменять её
    консенсусом значит менять мир ставок мимо книги, а миры двигает только
    аудитор.

    Наблюдения вносятся руками (`collect record-keyrate`): опрос выходит раз
    в месяц, и переписать из него одно число дешевле, чем поддерживать разбор
    страницы cbr.ru.
    """
    series = store.load(SURVEY_SERIES)
    if not series or not series.points:
        return None
    survey = {p.period: float(p.value) for p in series.points}
    base = key_rate_path(store, A)

    def on(day: date) -> float:
        period = "%dH%d" % (day.year, 1 if day.month <= 6 else 2)
        if period in survey:
            return survey[period]
        year = f"{day.year}"
        return survey.get(year, base(day))

    return on


# ---------------------------------------------------------------- пути долга


def tranches_on_day(tranches: Iterable[Tranche]) -> Callable[[date], list[Tranche]]:
    """Живые транши на дату: УЖЕ размещённые и ещё не погашенные.

    Дата размещения появилась в реестре третьей итерацией (B5): её отдаёт
    MOEX ISS полем ISSUEDATE. Без неё реестровый бэктест 1П2025 начислял
    купоны по выпускам 2026 года, то есть мерил не реестр, а сумму всех
    когда-либо существовавших долгов.
    """
    frozen = list(tranches)

    def on(day: date) -> list[Tranche]:
        return [t for t in frozen
                if t.maturity >= day and (t.issued_on is None or t.issued_on <= day)]

    return on


def aggregate_debt(opening: float, closing: float, start: date, end: date
                   ) -> Callable[[date], float]:
    """Линейный путь долга между отчётными датами.

    Допущение, и не безобидное: во 2П2025 долг вырос с 503 до 746 млрд ₽, и
    если занимали в начале полугодия, средний остаток выше линейного. Именно
    поэтому оно названо, а не спрятано: величина ошибки видна в бэктесте.
    """
    span = (end - start).days or 1

    def on(day: date) -> float:
        share = min(max((day - start).days / span, 0.0), 1.0)
        return opening + (closing - opening) * share

    return on


def linear_cash(opening: float, closing: float, start: date, end: date
                ) -> Callable[[date], float]:
    return aggregate_debt(opening, closing, start, end)


def cash_path(opening: float, opening_date: date, *, outflows: dict[date, float],
              accrual_per_day: float = 0.0) -> Callable[[date], float]:
    """Касса: остаток минус датированные погашения плюс ровный приток.

    Ровный приток — самое слабое место канала, и оно единственное модельное:
    операционный поток внутри полугодия сезонен. Поэтому он вынесен
    параметром, а его вклад печатается отдельной строкой.
    """
    events = sorted(outflows.items())

    def on(day: date) -> float:
        value = opening + accrual_per_day * max((day - opening_date).days, 0)
        for when, amount in events:
            if when <= day:
                value -= amount
        return max(value, 0.0)

    return on


# ------------------------------------------------------------- интегрирование


def integrate(start: date, end: date, *, tranches_on: Callable[[date], list[Tranche]],
              cash_on: Callable[[date], float], key_on: Callable[[date], float],
              deposit_factor: float, period: str = "") -> InterestResult:
    """Посуточное начисление. День — достаточно мелкий шаг: все события
    канала (погашения, решения ЦБ) датированы днём, а не часом."""
    days = (end - start).days + 1
    debt_interest = cash_income = 0.0
    debt_sum = cash_sum = key_sum = 0.0
    by_kind: dict[str, float] = {}
    for offset in range(days):
        day = start + timedelta(days=offset)
        key = key_on(day)
        key_sum += key
        for tranche in tranches_on(day):
            accrued = tranche.principal * tranche.rate(key) / DAY_BASIS
            debt_interest += accrued
            by_kind[tranche.kind] = by_kind.get(tranche.kind, 0.0) + accrued
            debt_sum += tranche.principal
        cash = cash_on(day)
        cash_sum += cash
        cash_income += cash * deposit_factor * key / DAY_BASIS
    return InterestResult(
        period=period, start=start, end=end, days=days,
        debt_interest=debt_interest, cash_income=cash_income,
        average_debt=debt_sum / days, average_cash=cash_sum / days,
        average_key_rate=key_sum / days,
        by_kind={k: round(v, 4) for k, v in by_kind.items()})


def integrate_aggregate(period: str, *, opening_debt: float, closing_debt: float,
                        opening_cash: float, closing_cash: float,
                        key_on: Callable[[date], float], spread: float,
                        deposit_factor: float) -> InterestResult:
    """Тот же расчёт на АГРЕГАТЕ: один синтетический транш «ключ + спред».

    Нужен для бэктеста прошлых полугодий: реестра траншей на 31.12.2024 и
    30.06.2025 не существует — в фактах есть только баланс. Агрегат проверяет
    механику (простые проценты, взвешивание по времени, доход на кассу) и
    измеряет вменённый спред, но не проверяет реестр.
    """
    start, end = periods.bounds(period)
    debt = aggregate_debt(opening_debt, closing_debt, start, end)
    cash = linear_cash(opening_cash, closing_cash, start, end)

    def tranches(day: date) -> list[Tranche]:
        return [Tranche("портфель", debt(day), end, spread_to_key=spread, kind="aggregate")]

    return integrate(start, end, tranches_on=tranches, cash_on=cash, key_on=key_on,
                     deposit_factor=deposit_factor, period=period)


def registry_backtest(period: str, *, key_on, deposit_factor: float,
                      opening_debt: float, closing_debt: float,
                      opening_cash: float, closing_cash: float,
                      bank_spread: float = 0.011) -> "InterestResult":
    """Проценты полугодия ПО РЕЕСТРУ: облигации траншами, остальное — банками.

    Чем это отличается от агрегатного бэктеста. Агрегат берёт один
    синтетический транш «ключ + спред» на средний остаток: он проверяет
    механику (простые проценты, взвешивание по времени, доход на кассу), но
    ничего не говорит о реестре — спред в нём подобран так, чтобы сойтись.
    Здесь наоборот: облигации начисляют СВОИ купоны по своим датам и ставкам,
    и совпасть им не на чем.

    Возможным это стало, когда в реестр попали даты размещения (ISSUEDATE у
    MOEX ISS). Утверждение прежнего исполнителя, что их нет, было неверным, и
    из-за него реестровый бэктест считался невозможным.

    Небондовая часть долга (банки, РЕПО) реконструируется как разность между
    балансовым долгом и номиналом живых выпусков: её состав компания не
    раскрывает, но её РАЗМЕР известен из баланса на обе отчётные даты.
    """
    from model.financing import load_debt_register

    start, end = periods.bounds(period)
    bonds = [t for t in load_debt_register(include_redeemed=True) if t.kind == "bond"]
    alive_on = tranches_on_day(bonds)

    def bond_principal(day: date) -> float:
        return sum(t.principal for t in alive_on(day))

    total = aggregate_debt(opening_debt, closing_debt, start, end)
    cash = linear_cash(opening_cash, closing_cash, start, end)

    def tranches(day: date) -> list[Tranche]:
        rest = max(0.0, total(day) - bond_principal(day))
        return alive_on(day) + [
            Tranche("банки и РЕПО", rest, end, spread_to_key=bank_spread, kind="bank")]

    return integrate(start, end, tranches_on=tranches, cash_on=cash, key_on=key_on,
                     deposit_factor=deposit_factor, period=period)


def implied_spread(period: str, *, opening_debt: float, closing_debt: float,
                   key_on: Callable[[date], float], debt_interest: float) -> float:
    """Спред, при котором расчёт воспроизводит фактические проценты.

    Это ИЗМЕРЕНИЕ, а не подгонка: спред не подставляется обратно в прогноз, а
    сравнивается с книжным (банки 1,1 п.п., облигации 1,39). Совпадение —
    подтверждение допущения, расхождение — его опровержение.
    """
    start, end = periods.bounds(period)
    debt = aggregate_debt(opening_debt, closing_debt, start, end)
    days = (end - start).days + 1
    base = key_part = 0.0
    for offset in range(days):
        day = start + timedelta(days=offset)
        principal = debt(day)
        base += principal / DAY_BASIS
        key_part += principal * key_on(day) / DAY_BASIS
    if not base:
        return 0.0
    return (debt_interest - key_part) / base


# ------------------------------------------------------------------- факты


def _period_of_flow(flow: str) -> str:
    """«2026-01-01/2026-06-30» → «2026H1»."""
    end = flow.split("/")[-1]
    try:
        when = date.fromisoformat(end)
    except ValueError:
        return ""
    return "%dH%d" % (when.year, 1 if when.month <= 6 else 2)


def reported_interest(period: str) -> dict[str, float]:
    """Факт полугодия: отчётные финансовые расходы и очищенные проценты.

    **Одно определение чистых процентов на весь проект** (аудит третьей
    итерации, C9). Очищенное число читается из
    `data/facts/accounting_base.json → net_interest_pre_ifrs16` — оттуда же,
    откуда его берут `collect.half_year_facts` и книга. Прежняя версия
    складывала его здесь заново, `bonds + loans − процентный доход ИЗ
    DATABOOK`, и получала 45,727 против 45,700 по МСФО: процентный доход
    Databook (21,620) и примечания 27 (21,646) — разные числа, и второе
    определение появлялось из ничего, просто потому что доход взяли не из
    того файла.

    Что в словаре:

    * `net_interest` — очищенные проценты по долгу (`None`, если за это
      полугодие разбивки нет);
    * `debt_interest` — проценты по долгу до вычета дохода (купоны плюс
      банковские), из того же блока;
    * `interest_income` — процентный доход ТОГО ЖЕ определения, что в `net`
      (тождество `debt_interest − interest_income == net_interest` выполняется
      точно); `interest_income_databook` — число Databook, оно нужно только
      для воспроизведения неочищенной строки;
    * `reported_finance_costs`, `reported_net_interest` — строка отчёта
      целиком и она же минус доход Databook (48,086 за 1П2026): величина
      ДРУГАЯ, и годится только как грубая замена там, где очищенной нет;
    * `clean` — есть ли очищенный факт, `contaminated_by` — чем загрязнена
      замена, если очищенного нет.

    Для полугодий без раскрытой разбивки `net_interest` равен None: выдумывать
    её нельзя, а сравнивать канал с загрязнённым фактом — значит зашивать в
    него ошибку.
    """
    history = json.loads((FACTS / "history.json").read_text(encoding="utf-8"))
    row = history["pnl"]["pre_ifrs16"].get(period)
    if not row:
        return {}
    reported = -row["finance_costs"] / 1e6
    databook_income = row["interest_income"] / 1e6
    out = dict(reported_finance_costs=reported, interest_income=databook_income,
               interest_income_databook=databook_income,
               reported_net_interest=reported - databook_income, debt_interest=None,
               net_interest=None, nonrecurring=None, clean=False,
               contaminated_by=CONTAMINATION_NOTE)

    base = json.loads((FACTS / "accounting_base.json").read_text(encoding="utf-8"))
    # «2026H1» → «H1_2026»: в примечаниях полугодие названо так, а не так, как
    # в рядах модели. Ключ строится ровно как в `collect.half_year_facts`.
    clean = (base.get("net_interest_pre_ifrs16") or {}).get(
        "H%s_%s" % (period[5], period[:4]))
    if not clean:
        return out
    out.update(debt_interest=clean["bonds"] + clean["loans"],
               interest_income=clean["interest_income"],
               net_interest=clean["net"], clean=True, contaminated_by="")

    # Непроцентные статьи финансовых расходов раскрыты только за отчётный
    # период выгруженной базы (`flow_period`). Для более ранних полугодий их
    # нет, и подставлять туда долю «как в 1П2026» значит выдумывать факт.
    if _period_of_flow(base.get("meta", {}).get("flow_period", "")) != period:
        return out
    schedules = base.get("schedules", {})

    def value(key: str) -> float:
        item = schedules.get(key) or {}
        return item.get("value_rub_bn", 0.0)

    out["nonrecurring"] = (value("put_unwinding_expense")
                           + value("loan_modification_non_cash_expense")
                           + value("other_finance_expense"))
    return out


# Чем загрязнена неочищенная замена. Метка, а не оценка: в полугодиях без
# раскрытой разбивки непроцентные статьи сидят внутри «финансовые расходы
# минус доход» неотделимо (аудит 850oa, третья итерация, C9).
CONTAMINATION_NOTE = (
    "разбивка финансовых расходов за это полугодие не раскрыта: в числе сидят "
    "размотка обязательств по опционам, неденежные модификации займов и "
    "прочее. Это НЕ чистые проценты по долгу, и точность канала на таком "
    "факте не объявляется")
