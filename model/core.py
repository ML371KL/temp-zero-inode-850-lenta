"""Ядро: один полугодовой проход клетки сетки и её оценка.

Экономика задана книгой допущений (`data/assumptions/ASSUMPTIONS-BOOK.md`,
машинный файл `data/assumptions/assumptions.yaml`). Таблицы книги (`results.json`) —
выпуск того же ядра (`model/book_results.py`).

Ядро — чистая функция книги: `run_cell(A, cell)` не читает диск, всё, что
нужно клетке (факты якоря, сегменты сети, строки моста с выплатами по ним,
ставки наращения, корзины ставок), лежит в словаре книги.

Сеть — сумма сегментов (`revenue.segments`, режимы `yoy`/`level`/`revenue`):
LFL сегмента `yoy` = LFL группы + `lfl_offset`, опт (`revenue`) = LFL группы +
`growth`, уровень (`level`) — площадь × плотность × индекс ИПЦ мира;
маржа, ОК, налог, D&A и долг — на уровне группы. Контракт ядра для верхнего
слоя — пара (EV, D) на клетку — от сегментов не зависит.

Что движок считает сверх книги (и почему это не меняет её чисел): **реестр
долга по траншам** — стена рефинансирования и чувствительность к ключевой
ставке считаются по фактическим условиям, а не по средней ставке на средний
остаток (`model.financing`). Этот слой **отчётный**: он объясняет результат,
но не меняет его. Маржа периода задаётся режимом, а не собирается снизу из
статей — иначе пришлось бы калибровать десяток коэффициентов на девяти
наблюдениях полугодового режима.

Единицы: деньги — млрд ₽, площадь — тыс. м², ставки и доли — доли единицы,
цена — ₽/акцию.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

from model.book import (
    BridgeItem,
    Cell,
    acquired_nol,
    bridge_items,
    capex_network_rules,
    capex_tax_premium,
    distress_rule,
    dividend_carry,
    dividend_ladder,
    dividend_net_debt_basis,
    dividend_timing,
    half_rate,
    interp_curve,
    lfl_offset_spec,
    nondeductible_da_anchor,
    path_value,
    period_index,
    periods,
    previous_period,
    previous_same_half,
    rate_baskets,
    refuse_incomplete,
    segments,
    terminal_rule,
    unit_price_basis,
)


@dataclass(frozen=True)
class SegmentStep:
    """Сегмент сети в строке полугодия — поля карточки «сеть по форматам».

    У сегмента без площади (`mode: revenue`) площадь и эффективная площадь —
    None, открытия и закрытия — ноль.
    """

    revenue: float
    growth: float | None
    """Рост выручки сегмента «год к году» к тому же полугодию (None — база 0)."""
    area_end: float | None
    effective_area_avg: float | None
    opened: float
    closed: float


@dataclass(frozen=True)
class StepRow:
    """Строка полугодия — то, что уходит в годовую таблицу дашборда."""

    period: str
    year: int
    half: int
    revenue: float
    lfl_ticket: float
    lfl_traffic: float
    area_end: float
    effective_area_avg: float
    margin: float
    ebitda: float
    da: float
    ebit: float
    capex: float
    capex_maintenance: float
    capex_growth: float
    capex_infra: float
    nwc_change: float
    operating_cash_change: float
    lease_adjustment: float
    tax_unlevered: float
    tax_actual: float
    tax_shield: float
    fcff: float
    net_interest: float
    debt_rate: float
    net_debt: float
    cash: float
    """Денежные средства на КОНЕЦ полугодия — то, из чего складывается валовой долг.

    Поле появилось потому, что без него `max_gross_debt` считался по кассе
    ПОСЛЕДНЕГО полугодия для всех строк сразу (аудит третьей итерации, A3б):
    путь валового долга получался кривым в 21 клетке из 36, а на этом поле
    стоит гейт `credit_lines`. Касса хранится в строке, а не берётся из
    переменной после цикла.
    """
    dividends: float
    leverage: float
    interest_cover: float
    operating_cash_growth: float = 0.0
    """Накопленный с отчётной даты прирост операционной кассы на конец полугодия
    — ТА ЧАСТЬ, которую валовой долг не должен считать.

    Модельный чистый долг строки — «отчётный плюс прирост операционной кассы»:
    прирост вычтен из FCFF как отток, то есть уже сидит в `net_debt`. Касса
    строки (`cash`) содержит саму операционную кассу. Сумма `net_debt + cash`
    поэтому считала прирост дважды — и проценты на него, и путь валового долга
    против лимита линий.
    """
    disposal_proceeds: float = 0.0
    """Поступления от выбытия ОС (`capex.disposal_proceeds_pct` × выручка) —
    отдельная строка, потому что FCFF собирается из опубликованных строк
    (`checks.fcff_identity`); при доле 0 — ноль."""
    tax_loss_pool: float = 0.0
    """Пул налоговых убытков (A-T3) на КОНЕЦ полугодия — отчётное поле, расчёт
    его не читает.

    Нужно перезаякориванию (`ops/tools/reanchor.py`): стартовый пул новой книги
    (`tax.nol_start`) — это пул на дату отчёта, и ожидание модели для него
    берётся отсюда, а не второй записью правила зачёта в инструменте."""
    tax_loss_pool_acquired: float = 0.0
    """Запертые убытки приобретённых юрлиц (`tax.acquired_nol`) на конец полугодия."""
    capex_integration: float = 0.0
    """Интеграционный capex (`capex.integration_capex`) — входит в `capex`."""
    settlement_payment: float = 0.0
    """Выплата (плюс) или поступление (минус) по строкам моста с `settle_period`
    в этом полугодии — выведены из `bridge.items`, входят в путь чистого долга."""
    fcfe: float = 0.0
    """Деньги полугодия акционерам до дивидендов: ЧД(p−1) − ЧД до дивидендов."""
    dividend_rung: int | None = None
    """Номер ступени `financing.dividend_ladder` (None — без лестницы или до года выплат)."""
    segments: dict = field(default_factory=dict)
    """{id сегмента: SegmentStep} в порядке `revenue.segments` книги."""

    @property
    def gross_debt(self) -> float:
        """Валовой долг конца полугодия: чистый долг плюс касса того же полугодия
        минус прирост операционной кассы, уже сидящий в чистом долге."""
        return self.net_debt + self.cash - self.operating_cash_growth

    @property
    def capex_pct(self) -> float:
        return self.capex / self.revenue if self.revenue else 0.0


@dataclass(frozen=True)
class CellResult:
    cell: Cell
    rows: list[StepRow]
    ev: float
    pv_fcff: float
    pv_tax_shield: float
    terminal_value: float
    terminal_share: float
    claims: float
    equity: float
    equity_before_governance: float
    price: float
    price_floor: float
    ev_ebitda_ltm: float
    ev_ebitda_ntm: float | None
    """EV к EBITDA следующих двенадцати месяцев: двух полугодий, начиная с
    текущего (на дате книги — первых двух прогнозных). Прежнее поле
    `ev_ebitda_2027` было зашито на календарный 2027 год: после
    перезаякоривания на 2П2027 знаменатель стал бы полугодовым, после FY2027 —
    нулём и `ZeroDivisionError` (аудит 26.09.2026, п. 5.3). Нет двух полугодий
    впереди или EBITDA не положительна — None."""
    r_long: float
    r_real: float
    exit_multiple: float
    fcff_terminal_margin: float
    debt_cost_addon: float = 0.0
    terminal_growth: float = 0.0
    # Терминал разложен на поток и щит. Поле не украшение: без него щит
    # терминала нельзя проверить аналитически, и две мутации аудита
    # (α_terminal игнорируется, налоговая ставка задвоена) не ловились НИЧЕМ,
    # кроме сверки с книгой. Оба значения — ДО приведения к дате оценки.
    terminal_flow_value: float = 0.0
    terminal_shield_value: float = 0.0
    terminal_debt_cost_addon: float = 0.0
    """Терминальная часть добавки Р11 — ДО приведения к дате оценки.

    Поле по той же причине, что и два предыдущих: без него терминальную
    добавку избыточного спреда нельзя проверить аналитически. В базе книги
    спреды справедливые, добавка равна нулю, и мутация «терминальная добавка
    приведена ГОДОВЫМ Гордоном вместо полугодового» не ловилась НИЧЕМ — ни
    парити с книгой (эталон считает базу так же), ни таблицей
    чувствительностей (она двигает `spread_float`, а добавка зависит от
    `spread_fixed`). Аудит третьей итерации, мутация X17."""
    max_net_debt: float = 0.0
    max_gross_debt: float = 0.0
    max_leverage: float = 0.0
    distress_cost: float = 0.0
    """Издержки финансовой неустойчивости клетки, млрд ₽ (`valuation.distress`,
    пара A-T4): EV клетки = PV потоков + PV щита + терминал − это поле; поле =
    доля × max(EV до издержек, 0) — издержки только уменьшают стоимость."""
    cash_carry: float = 0.0
    """Кэрри предфинансированной кассы, млрд ₽ (A-F8):
    приведённая недополученная доходность излишка кассы сверх цели «операционная
    касса + буфер» против ключевой ставки мира. Входит в требования."""
    max_net_debt_period: str = ""
    """Полугодие максимума чистого долга (первое при равенстве; якорь, если путь
    не выше отчётной даты)."""
    max_gross_debt_period: str = ""
    """Полугодие максимума валового долга — по тому же правилу."""
    claims_par: float = 0.0
    """Требования моста по номиналу — до добавки Р11 и кэрри кассы."""
    terminal_ebitda: float = 0.0
    """EBITDA первого терминального года с сезонностью полугодий, млрд ₽ — знаменатель
    мультипликатора выхода таблиц книги (`exit_multiple` выпуска делит на годовую
    выручку × целевую маржу)."""

    def annual(self) -> list[dict]:
        out: dict[int, dict] = {}
        for r in self.rows:
            a = out.setdefault(r.year, dict(
                year=r.year, revenue=0.0, ebitda=0.0, capex=0.0, fcff=0.0,
                tax_actual=0.0, net_interest=0.0, dividends=0.0, lfl=[], rate=[]))
            a["revenue"] += r.revenue
            a["ebitda"] += r.ebitda
            a["capex"] += r.capex
            a["fcff"] += r.fcff
            a["tax_actual"] += r.tax_actual
            a["net_interest"] += r.net_interest
            a["dividends"] += r.dividends
            a["lfl"].append(r.lfl_ticket + r.lfl_traffic)
            a["rate"].append(r.debt_rate)
            a["halves"] = a.get("halves", 0) + 1
            a["net_debt"] = r.net_debt
            a["area_end"] = r.area_end
            a["leverage"] = r.leverage
        for a in out.values():
            a["margin"] = a["ebitda"] / a["revenue"]
            a["capex_pct"] = a["capex"] / a["revenue"]
            a["lfl"] = sum(a["lfl"]) / len(a["lfl"])
            a["rate"] = sum(a["rate"]) / len(a["rate"])
        # Покрытие процентов на экране стоит рядом с ГОДОВЫМИ EBITDA и
        # процентами, значит и считаться обязано годовым. Раньше сюда
        # попадало значение последнего полугодия, и колонка расходилась
        # с собственными соседями на 5–6 %.
        for a in out.values():
            interest = a["net_interest"]
            a["interest_cover"] = (a["ebitda"] / interest) if interest > 0 else 99.0
        return [out[y] for y in sorted(out)]


def margin_season(A: dict, p: str) -> float:
    """Сезонность маржи: ± `margin.seasonal_h1_pp` (1П/2П симметрично; у Ленты
    знак ключа отрицательный — 2П сильнее 1П).

    Поправка действует с `margin.season_from_period`. `margin.season_free_halves`
    полугодий перед ним (у 850oa — два: якорь книги и первое прогнозное
    полугодие, чья цель задана явно по полугодию) — без поправки, и это
    свойство ПЕРИОДА, а не положения якоря: перезаякоренная книга судит
    наблюдение того полугодия по той же явной цели. Более ранняя история — с
    поправкой. Прежде исключение было литералом года в коде, затем константой
    ширины окна; теперь и начало, и ширина — ключи книги.
    """
    M = A["margin"]
    start = period_index(M["season_from_period"])
    if start - M["season_free_halves"] <= period_index(p) < start:
        return 0.0
    return M["seasonal_h1_pp"] if p[5] == "1" else -M["seasonal_h1_pp"]


def period_bounds(period: str) -> tuple[dt.date, dt.date]:
    """Календарные границы полугодия «2026H2» → (01.07.2026; 31.12.2026)."""
    year, half = int(period[:4]), int(period[5])
    return ((dt.date(year, 7, 1), dt.date(year, 12, 31)) if half == 2
            else (dt.date(year, 1, 1), dt.date(year, 6, 30)))


def grid_position(A: dict, day: dt.date) -> tuple[int, float]:
    """Где стоит ЛЮБАЯ дата на прогнозной сетке: (закрытых периодов; доля текущего).

    Вынесено из `time_position` (которая спрашивает про дату оценки), потому
    что на сетку приходится ставить и другие даты: статьи моста названы на
    дату книги, и «сколько полугодий прошло с тех пор» измеряется той же
    линейкой, что и положение даты оценки. Мерить размотку пута календарными
    днями было бы второй линейкой: полугодия книги длятся 181-184 дня, и две
    меры разошлись бы уже на третьем году.
    """
    from model.book import periods as _periods

    P = _periods(A["meta"]["first_period"], A["meta"]["last_period"])
    closed = 0
    for period in P:
        _, end = period_bounds(period)
        if day > end:
            closed += 1
        else:
            break
    if closed >= len(P):
        return len(P) - 1, 1.0
    start, end = period_bounds(P[closed])
    span = (end - start).days + 1
    return closed, max(0.0, min(1.0, (day - start).days / span))


def halves_on_grid(A: dict, day: dt.date) -> float:
    """Положение даты на сетке одним числом — в полугодиях от начала прогноза."""
    closed, elapsed = grid_position(A, day)
    return closed + elapsed


def time_position(A: dict) -> tuple[int, float]:
    """Где стоит дата оценки на прогнозной сетке: (закрытых периодов; доля текущего).

    ЗАЧЕМ ОБОБЩЕНИЕ. Прежняя версия считала только долю ПЕРВОГО прогнозного
    периода и обрезала её единицей. Значит, после конца этого периода время
    останавливалось: оценки на 02.01.2027, 05.07.2027 и 10.01.2028 были
    ТОЖДЕСТВЕННЫ — доля упиралась в единицу, а больше дата оценки ни на что не
    влияла. Панель при этом продолжала печатать «оценка на такое-то число»,
    и ни флага, ни расхождения не было видно.

    Теперь дата оценки может лежать в любом периоде. Правило одно и то же для
    всех: период, закончившийся ДО даты оценки, в приведённую стоимость не
    входит вовсе, а его денежный результат уже сидит в чистом долге на дату
    оценки — ровно так, как это делалось для прошедшей части первого периода.

    Возвращается пара, а не одно число: доля прошедшего времени сама по себе
    ничего не говорит о том, сколько периодов уже закрыто.

    Дата оценки за пределами последнего прогнозного периода — вырожденный
    случай (книга устарела на десять лет): считается, что закрыты все периоды
    кроме последнего, и он пройден целиком. Флаг `book_is_stale` в выпуске
    скажет об этом громче, чем любое обрезание.
    """
    return grid_position(A, dt.date.fromisoformat(A["meta"]["valuation_date"]))


def bridge_as_of(A: dict) -> dt.date:
    """Дата, на которую НАЗВАНЫ строки моста (`meta.bridge_as_of`, обязательный ключ).

    Это не дата оценки: мост выписан в книге один раз, а оценка ездит по
    сетке. Дата берётся только из словаря книги: прежде без ключа она читалась
    из файла книги на диске, и кандидат или перезаякоренная книга в памяти
    взяли бы чужую дату.
    """
    return dt.date.fromisoformat(A["meta"]["bridge_as_of"])


def curve_as_of(A: dict) -> dt.date:
    """Дата, на которую сняты кривые миров книги (её рыночные данные).

    Одно определение для ядра, эталона и контрольной модели: `meta.curve_as_of`,
    а без ключа — `meta.valuation_date` ТОГО ЖЕ словаря (книга называет её
    «дата рыночных данных»). С диска дата не читается: книга не из
    `data/assumptions/` (кандидат, перезаякоренная) перекатывалась бы от чужой
    даты. Поэтому каждое место, которое переписывает дату оценки при книжных
    кривых, сначала записывает дату кривых — как с `bridge_as_of`:
    `model.live.apply_live_inputs`, `ops/tools/reanchor.py` (`rolled`,
    перезаякоривание), `ops/tools/refresh_worlds.py` (`at_release`; кандидату
    со свежими кривыми — дата этих кривых).
    """
    stated = A["meta"].get("curve_as_of")
    return dt.date.fromisoformat(stated or A["meta"]["valuation_date"])


def halves_on_ruler(A: dict, day: dt.date) -> float:
    """Положение даты на линейке полугодий книги БЕЗ обрезки.

    Та же мера, что у `halves_on_grid` (целые полугодия + доля текущего по
    его календарной длине), но продолженная за края сетки: дата до начала
    прогноза — отрицательное число (перезаякоренная книга со старыми
    кривыми), после конца — больше длины сетки. Внутри сетки совпадает с
    `halves_on_grid` бит в бит.
    """
    first = A["meta"]["first_period"]
    start, end = period_bounds(f"{day.year}H{1 if day.month <= 6 else 2}")
    index = 2 * (day.year - int(first[:4])) + (1 if day.month > 6 else 0) - (int(first[5]) - 1)
    return index + (day - start).days / ((end - start).days + 1)


def accreted(A: dict, item: BridgeItem, day: dt.date) -> float:
    """Сумма строки моста, наращенная от её даты `as_of` до даты `day`.

    Множитель (1 + `accrete_rate_half`)^n, n — полугодия между датами линейкой
    самой сетки (`halves_on_ruler`: целые полугодия + доля текущего по его
    календарной длине), а не делением календарных дней на 182,5: полугодия
    книги длятся 181–184 дня, и вторая линейка разошлась бы с первой уже на
    третьем году. На дате строки множитель — единица по построению; до неё —
    дисконт (перезаякоренная назад книга). Без ставки — сама сумма.
    """
    if item.accrete_rate_half is None:
        return item.amount
    halves = halves_on_ruler(A, day) - halves_on_ruler(A, item.as_of)
    return item.amount * (1.0 + item.accrete_rate_half) ** halves


def settlement_amount(A: dict, item: BridgeItem) -> float:
    """Сумма расчёта строки моста в её `settle_period` (со знаком строки не связана).

    `settle_amount`, если книга его назвала; иначе сумма, наращенная по
    `accrete_rate_half` до КОНЦА полугодия расчёта (`accreted` на последний
    день полугодия): чистый долг пути — величина конца полугодия, и на этой
    дате требование моста и выплата совпадают — оценка непрерывна через границу.
    У актива поступление — признаваемая часть: × (1 − `haircut`).
    """
    if item.settle_amount is not None:
        value = item.settle_amount
    else:
        value = accreted(A, item, period_bounds(item.settle_period)[1] + dt.timedelta(days=1))
    if item.kind == "asset" and item.haircut is not None:
        value *= 1.0 - item.haircut
    return value


def settlement_payments(A: dict) -> dict[str, float]:
    """Выплаты пути чистого долга по строкам моста {полугодие: сумма}.

    ВЫВОДЯТСЯ из `bridge.items` (строки с `settle_period`), а не читаются из
    отдельного списка: у 850oa период выплаты пута и её сумма жили в двух
    местах книги и разошлись (скачок +37 ₽ на границе). Требование — выплата
    (плюс, увеличивает чистый долг), актив — поступление (минус). Сумма
    полугодия — в порядке строк книги.
    """
    out: dict[str, float] = {}
    for item in bridge_items(A):
        if item.settle_period is None:
            continue
        value = settlement_amount(A, item)
        out[item.settle_period] = out.get(item.settle_period, 0.0) + (
            value if item.kind == "claim" else -value)
    return out


def bridge_item_value(A: dict, item: BridgeItem, closed_periods: int, P: list[str]) -> float:
    """Строка моста на дату оценки со знаком: требование — плюс, актив — минус.

    ОДНА ЗАПИСЬ — ОДИН СЧЁТ.

    *До расчёта* строка — требование (актив) моста, наращенное по своей ставке
    от `as_of` до даты оценки (`accreted`): размотка обязательства по МСФО.

    *Расчёт* — выплата в пути чистого долга в полугодии `settle_period`
    (`settlement_payments`, выводится из этой же строки). Как только это
    полугодие ЗАКРЫТО, его чистый долг стал якорем моста, то есть выплата уже
    сидит в требованиях, и строка снимается. Строка сверх этого считала бы
    выплату дважды (у 850oa требования слоя прыгали 589,0 → 627,3 за двое
    суток, аудит A1). Внутри полугодия расчёта строка ещё в мосте: чистый долг
    на дату оценки берёт прошедшую долю потока полугодия без выплат.

    Актив входит со знаком минус и долей признания (1 − `haircut`): `haircut`
    — доля, которая НЕ засчитывается.
    """
    if item.settle_period is not None and item.settle_period in P \
            and P.index(item.settle_period) < closed_periods:
        return 0.0
    value = accreted(A, item, dt.date.fromisoformat(A["meta"]["valuation_date"]))
    if item.kind == "asset":
        return -(value * (1.0 - item.haircut) if item.haircut is not None else value)
    return value


def bridge_lines(A: dict, closed_periods: int, P: list[str]) -> list[tuple[BridgeItem, float]]:
    """Строки моста книги с их значениями на дату оценки (со знаком), в порядке книги."""
    return [(item, bridge_item_value(A, item, closed_periods, P)) for item in bridge_items(A)]


QUARTER_STARTS = {"Q1": (1, 1), "Q2": (4, 1), "Q3": (7, 1), "Q4": (10, 1)}


def nwc_quarter_weight(A: dict, closed_periods: int, day: dt.date | None = None) -> float | None:
    """Доля изменения ОК текущего полугодия, прошедшая к дате оценки (A9).

    `nwc.quarter_share {Q1..Q4}` — доли изменения ОК по кварталам ВНУТРИ
    полугодия (пары дают 1; у Ленты 3 кв. отрицателен: ОК ещё растёт, 4 кв.
    высвобождает). Внутри квартала — по дням, той же линейкой, что доля
    полугодия (день считается на своё начало):
      в первом квартале полугодия  w = s_a · (дней от начала квартала / длина);
      во втором                    w = s_a + s_b · (дней от начала квартала / длина).
    Тот же w — в перекате чистого долга на дату оценки и в доле текущего
    полугодия в EV (`run_cell`), поэтому поток полугодия считается ровно один
    раз. Нет ключа — None (перекат линейный: доля полугодия по дням).
    """
    shares = (A.get("nwc") or {}).get("quarter_share")
    if shares is None:
        return None
    day = day or dt.date.fromisoformat(A["meta"]["valuation_date"])
    P = periods(A["meta"]["first_period"], A["meta"]["last_period"])
    period = P[min(closed_periods, len(P) - 1)]
    year, half = int(period[:4]), int(period[5])
    first, second = ("Q1", "Q2") if half == 1 else ("Q3", "Q4")
    q2_start = dt.date(year, *QUARTER_STARTS[second])
    start, end = period_bounds(period)
    day = min(max(day, start), end + dt.timedelta(days=1))
    if day < q2_start:
        span = (q2_start - start).days
        return shares[first] * (day - start).days / span
    span = (end - q2_start).days + 1
    return shares[first] + shares[second] * min(1.0, (day - q2_start).days / span)


def nwc_start_level(A: dict, revenue_ltm: float, anchor: str) -> float:
    """ОК на конец якоря — стартовый уровень пути ОК клетки, млрд ₽.

    `nwc.anchor_level` — ОК якоря по балансу (определение A-W0, лист ОК книги):
    стартовый уровень — ровно он, и подмена июньского излишка
    `seasonal_june_excess` (ось, чувствительность) доли старта не ломает: доля
    старта (anchor_level − излишек)/LTM выводится, а не пишется (решение ведущего
    D27). Без ключа — `nwc_pct_start` × LTM + излишек (у якоря на 1П; правило
    850oa). Ключи — ровно один из двух (`model.book.nwc_rules`).
    """
    N = A["nwc"]
    if "anchor_level" in N:
        return float(N["anchor_level"])
    return N["nwc_pct_start"] * revenue_ltm + (N["seasonal_june_excess"] if anchor.endswith("H1")
                                               else 0.0)


def elapsed_of_first_period(A: dict) -> float:
    """Какая доля ТЕКУЩЕГО прогнозного полугодия прошла на дату оценки.

    Границы берутся ИЗ `meta.first_period`, а не из года даты оценки. Прежний
    код считал их как «1 июля — 31 декабря года оценки», и это работало ровно
    до конца 2026-го: при дате оценки в первом полугодии следующего года
    разность уходила в минус, обрезалась нулём, и первый период вдруг
    дисконтировался целиком. Цена скакала примерно на −9 % от смены года —
    без единой правки книги.

    Имя осталось прежним: пока дата оценки лежит внутри первого прогнозного
    периода (то есть всегда, пока книга свежая), это та же величина.
    """
    return time_position(A)[1]


def observation_weight(A: dict, se: float, prior_var: float | None = None) -> float:
    """Вес наблюдения против собственной ошибки: P / (P + se²), P — априорная
    дисперсия отклонения (до первого наблюдения — σ²).

    Ноль ошибки — вес единица (вышедший отчёт), большая ошибка — вес к нулю:
    прогноз с ошибкой ±0,5 п.п. не двигает цену так же, как факт
    (A-P2u).
    """
    if not se:
        return 1.0
    sigma2 = (A["joint"]["regime_update"]["sigma_pp"] ** 2 if prior_var is None
              else prior_var)
    return sigma2 / (sigma2 + se * se)


def margin_observations(A: dict) -> dict[str, tuple[float, float]]:
    """A-P2u: наблюдения маржи {период: (значение, стандартная ошибка)}.

    Факт подаётся с нулевой ошибкой, прогноз слоя индикаторов — со своей.
    Слабый прогноз двигает вероятности режимов слабо, и это свойство правила,
    а не настройка. Маржа якоря на проформенном периметре
    (`facts.anchor.margin_pro_forma` с `margin_pro_forma_se`) — наблюдение
    полугодия якоря со своей ошибкой: проформа — оценка, а не отчёт (у Ленты
    1П2026 — периметр с «О'КЕЙ» за полгода). Она же задаёт стартовое
    отклонение AR(1) клетки (`run_cell`, правило якоря с фильтром Калмана).
    """
    raw = A["joint"]["regime_update"].get("observations") or {}
    out = {}
    anchor = A["facts"]["anchor"]
    if "margin_pro_forma" in anchor:
        out[anchor["period"]] = (float(anchor["margin_pro_forma"]),
                                 float(anchor["margin_pro_forma_se"]))
    for p, o in raw.items():
        if isinstance(o, dict):
            out[p] = (float(o["value"]), float(o.get("se", 0.0)))
        else:
            out[p] = (float(o), 0.0)
    return out


def lt_homogeneity(A: dict, cell: Cell, year: int) -> float:
    """Добавка к росту чека: в долгую номинальный LFL у миров одинаков (Р9).

    Реальный смысл: разная долгосрочная инфляция между мирами не может вечно
    означать разный РЕАЛЬНЫЙ рост чека. Коэффициент переноса `ticket_k` < 1
    описывает краткосрочное отставание чека от продовольственной инфляции;
    если оставить его навсегда, мир с высокой инфляцией получает вечное
    реальное падение чека, а мир с низкой — вечный реальный рост. Добавка
    гасит ровно непере­несённую часть (1 − k) и включается плавно за четыре
    года, а не скачком.
    """
    spec = A["revenue"]["ticket_lt_homogeneity"]
    start, end = spec["ramp_from"], spec["ramp_to"]
    weight = 0.0 if end <= start else (year - start) / (end - start)
    weight = max(0.0, min(1.0, weight))
    k = A["revenue"]["ticket_k"][cell.demand]
    reference = A["worlds"][spec["reference_world"]]["lt"]["inflation"]
    return (1 - k) * (A["worlds"][cell.world]["lt"]["inflation"] - reference) * weight


def nondeductible_da(amount: float | None, legacy_halves: int, i: int, half_life: float) -> float:
    """Невычитаемая в налоге часть учётной D&A в полугодии i прогноза, млрд ₽.

    `tax.nondeductible_da_anchor` (`amount`; амортизация торговых марок ППА в базе
    D&A якоря, аудит 30.09.2026, control-model-02) убывает вместе с базой якоря:
    amount × max(0, 1 − (legacy_halves + i + 1)/half_life), legacy_halves —
    полугодий, которые база уже списывается (`facts.da_straight_line`, иначе 0),
    half_life = 2·срок службы. Одна функция для ядра и инструмента
    перезаякоривания. Нет ключа — 0.
    """
    if not amount:
        return 0.0
    return amount * max(0.0, 1 - (legacy_halves + i + 1) / half_life)


def annuity_ratio(rate: float, life: float) -> float:
    """D&A к capex при постоянном темпе роста `rate` и сроке службы `life`.

    При нулевом темпе отношение равно единице; формула с делением на `rate`
    там обращается в 0/0, поэтому предел берётся явно, а не «почти нулём».
    """
    if abs(rate) < 1e-9:
        return 1.0
    return (1 - (1 + rate) ** (-life)) / (life * rate)


def _discount_factory(A: dict, cell: Cell):
    """Коэффициент дисконтирования: кривая СВОЕГО мира плюс β_u × ERP.

    Решение A-V5: каждый мир дисконтируется вдоль своих ставок, а позиция
    рынка учитывается весами миров, а не гибридной кривой. Гибрид оставлен
    контрольной чувствительностью через `valuation.market_curve_years`.
    """
    V = A["valuation"]
    premium = V["beta_u"] * V["erp"]
    own = A["worlds"][cell.world]["zero_curve"]
    market = A["worlds"]["M"]["zero_curve"]
    years_on_market = float(V.get("market_curve_years", 0.0))

    def own_df(t: float) -> float:
        return (1.0 + interp_curve(own, t) + premium) ** (-t)

    def df(t: float) -> float:
        if years_on_market <= 0 or cell.world == "M":
            return own_df(t)
        tm = min(t, years_on_market)
        return (1.0 + interp_curve(market, tm) + premium) ** (-tm) * own_df(t) / own_df(tm)

    # Перекат по форвардам (`valuation.roll_along_forwards`). Кривые книги сняты
    # на её дату, а ключевые ставки миров заданы календарём: дата оценки позже
    # даты кривых дисконтирует по форвардам кривой книги,
    # DF(v→t) = DF_книги(Δ + t) / DF_книги(Δ), а не той же спот-кривой от даты
    # оценки (она молча опускала бы ставки нисходящей кривой). Δ — расстояние
    # от даты кривых до даты оценки той же линейкой полугодий, что и сроки
    # потоков, без обрезки: у перезаякоренной книги кривые старше начала сетки.
    # На дате кривых Δ = 0; дата кривых позже даты оценки (ретроспектива) —
    # без переката.
    valuation = dt.date.fromisoformat(A["meta"]["valuation_date"])
    shift = 0.5 * (halves_on_grid(A, valuation) - halves_on_ruler(A, curve_as_of(A)))
    if shift <= 0:
        return df, premium
    df_shift = df(shift)

    def rolled(t: float) -> float:
        return df(shift + t) / df_shift

    return rolled, premium


class _SegmentState:
    """Состояние сегмента внутри прохода клетки: площадь, когорты, история.

    Правила сети (созревание, плотность новой площади, закрытия, эффективная
    площадь) — те же, что у однородной сети 850oa, но у каждого сегмента свои
    площадь, когорты, `new_space_density`, `closed_productivity` и сценарии
    площади; кривая созревания — общая группы.
    """

    __slots__ = ("seg", "space", "cohorts", "history_cohorts", "area_end", "effective_end",
                 "effective_hist", "revenue_hist", "offset", "other")

    def __init__(self, seg, A: dict, cell: Cell, maturity: list[float]):
        self.seg = seg
        self.revenue_hist = dict(seg.facts["revenue"])
        self.offset = self.other = None
        if seg.network:
            self.space = seg.spec["space"][cell.growth]
            self.cohorts = list(seg.facts.get("new_area_gross_hist", []))
            self.history_cohorts = len(self.cohorts) - seg.dense_history_cohorts
            self.area_end = seg.facts["area_end"]
            self.effective_end = anchor_effective_end(seg, maturity)
            self.effective_hist = dict(seg.facts.get("eff_area_avg_hist") or {})
            # E12: история эффективной площади двух полугодий до якоря — выводом
            # правила сети назад от уровня якоря (закрытия истории —
            # `closed_area_hist`), чтобы подмена d или продуктивности закрытых
            # (оси, чувствительности) двигала и базу «год к году», а не только
            # уровень якоря. Без закрытий истории — значения книги.
            derived = effective_history(seg, maturity, self.effective_end, A)
            if derived is not None:
                self.effective_hist.update(derived)
        if seg.mode == "yoy":
            self.offset = lfl_offset_spec(seg, cell.margin_regime)
            self.other = seg.spec.get("other_growth")


def anchor_effective_end(seg, maturity: list[float]) -> float:
    """Эффективная площадь сегмента сети на конец якоря: физическая минус незрелая.

    Последние `new_area_dense_cohorts` исторических когорт — открытые уже по
    правилу плотности — дозревают до d, как в прогнозе, старшие — до 1,0; после
    перезаякоривания уровень — из фактов (`eff_area_end`): ряд непрерывен.
    """
    if seg.effective_area_end is not None:
        return seg.effective_area_end
    density, dense = seg.new_space_density, seg.dense_history_cohorts
    cohorts = seg.facts.get("new_area_gross_hist") or []
    immature = sum(n * (1 - maturity[min(a, len(maturity) - 1)] * (density if a < dense else 1.0))
                   for a, n in enumerate(reversed(cohorts)))
    return seg.facts["area_end"] - immature


def effective_history(seg, maturity: list[float], effective_end: float, A: dict) -> dict | None:
    """Средняя эффективная площадь двух полугодий до якоря правилом сети назад (E12).

    У сегмента сети с закрытиями истории (`facts.segments.<id>.closed_area_hist`
    на те же два полугодия) каждое историческое полугодие j — шаг того же
    правила, что в прогнозе:
      прирост_j = −закрыто_j·cp + открыто_j·m₀·d + Σ_{a=1..k} n_{−a}·(m_a − m_{a−1})·(d, если a ≤ плотных, иначе 1),
    открыто_j — когорта `new_area_gross_hist` этого полугодия, n_{−a} — более
    старые когорты, плотных — `new_area_dense_cohorts` (в истории — сколько
    младших когорт дозревают до d; правило то же, что у когорт на якоре).
    Уровень конца якоря — эффективная площадь якоря ядра (физическая минус
    незрелая), средние — полусуммы концов:
      ср(якорь) = L − прирост(якорь)/2, ср(якорь−1) = L − прирост(якорь) − прирост(якорь−1)/2.
    Непрерывный индекс листа «Сеть» (`netlib.eff_area_chain`) на тех же входах
    даёт те же числа. Нет ключа — None (история — значения книги).
    """
    closed = seg.facts.get("closed_area_hist")
    if not closed:
        return None
    P = periods(A["meta"]["first_period"], A["meta"]["last_period"])
    anchor = previous_period(P[0])
    base = (previous_period(anchor), anchor)
    cohorts = list(seg.facts.get("new_area_gross_hist") or [])
    d, cp, dense = seg.new_space_density, seg.closed_productivity, seg.dense_history_cohorts
    k = len(maturity) - 1

    def increment(j: int, period: str) -> float:
        older = cohorts[:j]
        maturing = sum(older[-a] * (maturity[a] - maturity[a - 1]) * (d if a <= dense else 1.0)
                       for a in range(1, min(k, len(older)) + 1))
        return -closed[period] * cp + cohorts[j] * maturity[0] * d + maturing

    step_anchor = increment(len(cohorts) - 1, base[1])
    step_before = increment(len(cohorts) - 2, base[0])
    return {base[1]: effective_end - step_anchor / 2.0,
            base[0]: effective_end - step_anchor - step_before / 2.0}


def terminal_revenue_factors(network: list, maturity: list[float], P: list[str],
                             first_half, second_half) -> tuple[float, float]:
    """Множители выручки двух полугодий терминала при базе «площадь на выходе»
    (`valuation.terminal.revenue_base: exit_area`; аудит 30.09.2026,
    discount-terminal-01).

    Выручка сегмента сети ∝ эффективной площади. Правило «годовая выручка
    последнего года × (1 + g)» берёт среднюю эффективную площадь полугодий
    последнего года, а capex терминала (замещающие открытия, физическая доля) —
    физическую площадь на конец горизонта: открытия последнего года оплачены,
    но их дозревание в выручку терминала не входит никогда. Здесь база
    сегмента — эффективная площадь на выходе:
      A_ss = A_eff(конец) + Σ_{две младшие когорты} n·(m_∞ − m_возраст)·d
             − площадь × закрытия/2 × d × Σ_{a<k}(m_∞ − m_a)
    (дозревание младших когорт минус стационарная незрелость замещающих
    открытий, которые при нулевом чистом росте идут каждое полугодие), и
    множитель полугодия k терминала — A_ss / A_eff,ср(то же полугодие
    последнего года) × (1 + τ_k·rot) / (1 + rot), rot = (d·m_∞ − cp)·закрытия —
    подъём ротации, который g уже содержит за целый год (τ = 0,25 / 0,75: от
    конца горизонта до середин полугодий). Сегменты без площади — множитель 1;
    множитель группы — средний по выручке сегментов своего полугодия.
    """
    k_last = len(maturity) - 1
    factors = {}
    for s in network:
        d = s.seg.new_space_density
        n_c = len(s.cohorts)
        remaining = sum(s.cohorts[-1 - a] * (maturity[-1] - maturity[a])
                        * (d if n_c - 1 - a >= s.history_cohorts else 1.0)
                        for a in range(min(k_last, n_c)))
        close = path_value(s.space["close"], P[-1])
        steady = (s.area_end * close / 2.0 * d
                  * sum(maturity[-1] - maturity[a] for a in range(k_last)))
        exit_area = s.effective_end + remaining - steady
        rotation = (d * maturity[-1] - s.seg.closed_productivity) * close
        factors[s.seg.id] = (
            exit_area / s.effective_hist[P[-2]] * (1 + 0.25 * rotation) / (1 + rotation),
            exit_area / s.effective_hist[P[-1]] * (1 + 0.75 * rotation) / (1 + rotation))
    out = []
    for k, row in enumerate((first_half, second_half)):
        weighted = sum(step.revenue * (factors[sid][k] if sid in factors else 1.0)
                       for sid, step in row.segments.items())
        out.append(weighted / row.revenue)
    return out[0], out[1]


def dividend_rule(A: dict, year: int, net_debt_prev: float, net_debt_pre: float,
                  ebitda_ltm: float, ladder) -> tuple[float, int | None]:
    """Дивиденды полугодия и номер ступени лестницы (None — без лестницы или ниже года).

    Правило (книга: `financing.dividend_ladder`, `leverage_target`,
    `dividends_from_year`; докстрока `model.book.dividend_ladder`):
      до года `dividends_from_year` — 0;
      FCFE = ЧД(p−1) − ЧД_до(p); запас = max(0, L·EBITDA LTM − ЧД_до(p));
      без лестницы — D = запас (правило 850oa);
      с лестницей — ступень: первая с λ = ЧД_до/EBITDA LTM < max_leverage
      (нет такой — D = 0), D = min(max(запас, FCFE⁺), payout_max·FCFE⁺).
    """
    FN = A["financing"]
    if year < FN["dividends_from_year"]:
        return 0.0, None
    if ladder is None:
        # Ровно правило 850oa (и его порядок операций: числа 850oa бит в бит).
        target = FN["leverage_target"] * ebitda_ltm
        return (target - net_debt_pre if net_debt_pre < target else 0.0), None
    return ladder_payout(FN, net_debt_prev - net_debt_pre, net_debt_pre, ebitda_ltm, ladder)


def ladder_payout(FN: dict, fcfe: float, net_debt: float, ebitda_ltm: float,
                  ladder) -> tuple[float, int | None]:
    """Выплата «верх ступени» и номер ступени: одна формула для полугодия
    (`dividend_rule`) и для года (`financing.dividend_timing`, `run_cell`).

    λ = ЧД / EBITDA LTM выбирает ступень (нет такой — 0); запас = max(0,
    L·EBITDA LTM − ЧД); D = min(max(запас, FCFE⁺), payout_max·FCFE⁺).
    """
    target = FN["leverage_target"] * ebitda_ltm
    leverage = net_debt / ebitda_ltm if ebitda_ltm > 0 else math.inf
    for rung_no, rung in enumerate(ladder):
        if rung.max_leverage is None or leverage < rung.max_leverage:
            break
    else:
        return 0.0, None
    fcfe = max(fcfe, 0.0)
    room = max(target - net_debt, 0.0)
    wanted = max(room, fcfe)
    cap = math.inf if rung.payout_max is None else rung.payout_max * fcfe
    return min(wanted, cap), rung_no


def basket_debt_rate(baskets, p: str, key: float, new_debt: float) -> float:
    """Ставка всего долга в полугодии p по корзинам (`financing.rate_baskets`, A10).

    Живая корзина (p ≤ `until`) платит свою ставку (`fixed` — договорную,
    `key_plus` — ключевую `key` + спред); доля долга вне живых корзин — ставку
    нового долга `new_debt` (смесь фиксированной и плавающей частей по
    `financing.fixed_share`). Без корзин — ставка нового долга.
    """
    index = period_index(p)
    live = [b for b in baskets if index <= period_index(b.until)]
    rate = (1.0 - sum(b.share for b in live)) * new_debt
    for b in live:
        rate += b.share * b.annual(key)
    return rate


def run_cell(A: dict, cell: Cell) -> CellResult:
    """Полный проход одной клетки: ряды до FCFF, APV, мост, цена.

    Чистая функция книги: диск не читается.
    """
    refuse_incomplete(A)
    F, R, M, C, N, TX, FN, V = (
        A["facts"], A["revenue"], A["margin"], A["capex"], A["nwc"],
        A["tax"], A["financing"], A["valuation"],
    )
    AF = F["anchor"]
    P = periods(A["meta"]["first_period"], A["meta"]["last_period"])

    # Где стоит дата оценки: сколько прогнозных периодов уже закрыто и какая
    # доля текущего прошла. Поток текущего периода дисконтируется на остаток,
    # закрытые периоды в приведённую стоимость не входят вовсе.
    # Имя `closed_periods`, а не `closed`: внутри цикла `closed` — это
    # закрытая площадь магазинов, и совпадение имён стоило бы разбора
    # падения «list indices must be integers, not float».
    closed_periods, elapsed = time_position(A)
    # Доля изменения ОК текущего полугодия, прошедшая к дате оценки
    # (`nwc.quarter_share`); без ключа — None: перекат линейный по дням.
    nwc_weight = nwc_quarter_weight(A, closed_periods)

    discount, premium = _discount_factory(A, cell)

    rules = capex_network_rules(A)

    # --- стартовое состояние сети по сегментам (`revenue.segments`): когорты
    # новой площади с кривой созревания, эффективная площадь, история выручки.
    maturity = R["maturity_curve"]
    states = [_SegmentState(seg, A, cell, maturity) for seg in segments(A)]
    network = [s for s in states if s.seg.network]
    area_end = sum(s.area_end for s in network)

    revenue_hist, ebitda_hist = dict(F["revenue"]), dict(F["ebitda_pre16"])
    # Последнее ОТЧЁТНОЕ полугодие выводится из первого прогнозного, а не
    # зашивается: иначе движок нельзя заякорить на другую дату, а без этого
    # невозможен бэктест (бриф, п. 5.11.4).
    anchor = previous_period(P[0])
    anchor_prev = previous_period(anchor)
    # Индекс цен сегментов режима `level` — индекс ИПЦ мира от якоря
    # (`inflation_index`, тот же, что индексирует capex): плотность книги — в
    # ценах полугодия якоря (A-R11 книги 1.0: «цены 2026H1 × индекс ИПЦ мира»).
    regime = M["regimes"][cell.margin_regime]
    deviation, rho = 0.0, M["deviation_persistence"]
    shocks = M.get("nowcast_margin_shocks_pp") or {}
    observed = margin_observations(A)
    # Дисперсия отклонения после последнего наблюдения и его индекс: у
    # наблюдения с ошибкой переносится апостериорное среднее (фильтр Калмана).
    deviation_var, observed_at = 0.0, None
    sigma2 = A["joint"]["regime_update"]["sigma_pp"] ** 2
    # Правило якоря (A-C4, книга §4 п. 6): наблюдение за полугодие ЯКОРЯ задаёт
    # отклонение на якоре, и дальше оно затухает тем же ρ, что и без
    # перезаякоривания. Факт (se = 0, так его пишет перезаякоривание) —
    # отклонение ровно факт − цель − сезон; проформа со своей ошибкой (se > 0,
    # `facts.anchor.margin_pro_forma`) — шаг Калмана от априорного N(0, σ²).
    # Индекс −1 — якорь: наблюдение первого прогнозного полугодия берёт
    # априорную дисперсию σ²(1 − ρ²) + ρ²·P_якоря, как правдоподобие режимов.
    # Без наблюдения якоря — ноль.
    if anchor in observed:
        value, se = observed[anchor]
        raw_deviation = value - path_value(regime["target"], anchor) - margin_season(A, anchor)
        if not se:
            deviation = raw_deviation
        else:
            weight = observation_weight(A, se, sigma2)
            deviation = weight * raw_deviation
            deviation_var = (1 - weight) * sigma2
        observed_at = -1

    da, capex_prev = AF["da_pre16"], AF["capex"]
    revenue_ltm0 = revenue_hist[anchor_prev] + revenue_hist[anchor]
    june_excess = N["seasonal_june_excess"]
    # ОК приобретённого периметра (`nwc.acquired_path`, доля годовой выручки):
    # прибавка к доле ОК `nwc_pct` в каждом полугодии прогноза и в терминале
    # (A-W3 книги 1.0: ОК «О'КЕЙ» сверх условий покупателя уходит, когда
    # поставщики переходят на его условия, — поток ΔОК). На якоре пути нет:
    # стартовый уровень — баланс, и в нём этот ОК уже сидит.
    acquired_nwc = N.get("acquired_path")
    # Стартовый уровень ОК (`nwc_start_level`): ОК якоря по балансу
    # `nwc.anchor_level`, если книга его назвала, — тогда подмена июньского
    # излишка (ось, чувствительность) пересчитывает долю старта сама и ОК якоря
    # остаётся балансом (решение ведущего D27, урок 850oa № 19); иначе
    # `nwc_pct_start` × LTM + июньский излишек (только при якоре на 1П: на 31.12
    # пика нет, книга 1.4 — ложное высвобождение ≈35 млрд ₽ при перезаякоривании).
    nwc_prev = nwc_start_level(A, revenue_ltm0, anchor)
    operating_cash_0 = FN["operating_cash_pct"] * revenue_ltm0
    operating_cash_prev = operating_cash_0

    net_debt, cash, nol = AF["net_debt"], AF["cash"], TX["nol_start"]
    tau = TX["rate"]
    alpha = TX["alpha"]
    # Запертые убытки приобретённых юрлиц (`tax.acquired_nol`, докстрока
    # `model.book.acquired_nol`): свой пул, с `usable_from`, против доли базы.
    locked = acquired_nol(A)
    nol_acquired = locked["amount"] if locked else 0.0
    merged = False
    # Ускоренная налоговая амортизация (`tax.capex_tax_premium_share`, решение
    # ведущего D25): налоговая D&A = премия·capex + (1 − премия)·линейная по
    # когортам; учётная — линейная; разница уменьшает налоговую базу. Нет ключа
    # — премии нет (правило 850oa).
    tax_premium = capex_tax_premium(A)
    # Невычитаемая часть D&A якоря (`tax.nondeductible_da_anchor`; нет ключа — None).
    nondeductible_anchor = nondeductible_da_anchor(A)
    area_share = rules.maintenance_area_share
    area_intensity_0 = rules.maintenance_area_base
    integration = C.get("integration_capex")
    # Линейная D&A: база якоря, сколько полугодий она уже списывается и
    # когорты capex до якоря — из фактов перезаякоривания, иначе с нуля.
    da_anchor, legacy_halves, vintages = ((rules.da_state[0], rules.da_state[1],
                                           list(rules.da_state[2]))
                                          if rules.da_state else (da, 0, []))

    pv_fcff = pv_shield = 0.0
    inflation_index = 1.0
    # Дата удельных цен открытий и инфраструктуры (`capex.unit_price_basis`,
    # докстрока `model.book.unit_price_basis`): при ценах конца полугодия якоря
    # индекс цен полугодия — индекс ядра, делённый на (1 + h_p)^0,5.
    prices_at_anchor_end = unit_price_basis(A) == "anchor_end"
    # Р11б: проценты сверх СПРАВЕДЛИВОГО спреда — потеря акционеров, а не
    # только прирост налогового щита. Считается в главном цикле, на кассе
    # своего полугодия.
    fair_spread = FN["spread_fair"]
    pv_excess = 0.0
    # Кэрри предфинансированной кассы (A-F8). Излишек кассы
    # сверх цели «операционная касса + буфер ликвидности» занят впрок под
    # погашения: он оплачен долгом (валовой долг = чистый + касса), а приносит
    # `cash_yield_k` × ключевая. В требованиях касса неттится по номиналу, а
    # щит недополученная доходность УВЕЛИЧИВАЕТ (через чистые проценты) —
    # поэтому к требованиям она идёт до налога. Начисляется на излишек НАЧАЛА
    # полугодия сверх цели того же момента; буфер — норма ликвидности, а не
    # излишек. Приводится как добавка Р11.
    pv_carry = 0.0
    cash_target = operating_cash_0 + FN["liquidity_buffer_pct"] * revenue_ltm0
    # Выплаты пути долга по полугодиям ВЫВОДЯТСЯ из строк моста с
    # `settle_period` (`settlement_payments`): одна запись — один счёт.
    payments = settlement_payments(A)
    baskets = rate_baskets(A)
    ladder = dividend_ladder(A)
    # Сроки выплаты лестницы (`financing.dividend_timing`, докстрока
    # `model.book.dividend_timing`) и база ЧД её порогов
    # (`financing.dividend_net_debt_basis`): при `reported` пороги и запас
    # меряются по отчётному ЧД — модельный минус прирост операционной кассы с
    # якоря (находка I1d); FCFE правила от базы не зависит.
    timing = dividend_timing(A)
    reported_basis = dividend_net_debt_basis(A) == "reported"
    # Годовое правило: FCFE финансового года копится по полугодиям; год, 1П
    # которого закрыт на якоре, начинается с факта `facts.anchor.fcfe_ytd`.
    # Объявленная по итогам года выплата (сумма, ступень) ждёт 1П следующего
    # года; при якоре на 2П она объявлена по факту года якоря и платится в
    # первом прогнозном полугодии.
    carry = dividend_carry(A)
    year_fcfe = carry if (carry is not None and anchor.endswith("H1")) else 0.0
    declared: tuple[float, int | None] | None = None
    if (timing is not None and anchor.endswith("H2")
            and int(anchor[:4]) + 1 >= FN["dividends_from_year"]):
        declared = ladder_payout(FN, carry, AF["net_debt"], AF["ebitda_ltm"], ladder)
    rows: list[StepRow] = []
    ltm_ebitda_by_period: list[float] = []

    for i, p in enumerate(P):
        year, half = int(p[:4]), int(p[5])
        W = A["worlds"][cell.world]
        cpi, food_cpi, key = (path_value(W[k], p) for k in ("cpi", "food_cpi", "key_rate"))
        inflation_index *= 1 + half_rate(cpi)
        unit_price_index = (inflation_index / (1 + half_rate(cpi)) ** 0.5 if prices_at_anchor_end
                            else inflation_index)

        # --- чек и трафик группы: общие для сегментов (поправки — у сегментов)
        prev = previous_same_half(p)
        ticket = (R["ticket_k"][cell.demand] * food_cpi
                  + path_value(R["ticket_shift"][cell.demand], p)
                  + path_value(R["vat_adjustment"], p)
                  + lt_homogeneity(A, cell, year))
        traffic = path_value(R["traffic"][cell.demand], p)
        # LFL группы в мультипликативной форме: (1 + чек)(1 + трафик) − 1.
        lfl_group = (1 + ticket) * (1 + traffic) - 1.0

        # --- сеть по сегментам: открытия, закрытия, созревание, выручка
        opened = closed = 0.0
        segment_steps: dict[str, SegmentStep] = {}
        for s in states:
            seg = s.seg
            if seg.network:
                density = seg.new_space_density
                s_opened = s.area_end * path_value(s.space["gross_open"], p) / 2.0
                s_closed = s.area_end * path_value(s.space["close"], p) / 2.0
                # A-K5 (`new_space_density`): когорты ПРОГНОЗА дозревают до
                # плотности d средней площади сегмента, исторические — до 1,0.
                # Иначе ротация сети (закрытия с 0,6, открытия до 1,0) растила
                # бы эффективную площадь быстрее физической.
                maturing = sum(s.cohorts[-a] * (maturity[a] - maturity[a - 1])
                               * (density if len(s.cohorts) - a >= s.history_cohorts else 1.0)
                               for a in range(1, min(len(maturity), len(s.cohorts) + 1)))
                effective_new_end = (s.effective_end
                                     - s_closed * seg.closed_productivity
                                     + s_opened * maturity[0] * density
                                     + maturing)
                effective_avg = (s.effective_end + effective_new_end) / 2.0
                s.area_end, s.effective_end = s.area_end + s_opened - s_closed, effective_new_end
                s.cohorts.append(s_opened)
                s.effective_hist[p] = effective_avg
                opened += s_opened
                closed += s_closed
            else:
                s_opened = s_closed = 0.0
                effective_avg = None
            # --- выручка сегмента по его режиму
            if seg.mode == "yoy":
                # Год к году к тому же полугодию: сезонность снимается сама.
                # LFL сегмента = LFL группы + поправка сегмента (прибавка к LFL,
                # как её калибрует лист «Сеть», A-R9/A-R10).
                s_lfl = lfl_group if s.offset is None else lfl_group + path_value(s.offset, p)
                s_revenue = (s.revenue_hist[prev]
                             * (effective_avg / s.effective_hist[prev]) * (1 + s_lfl))
                if s.other is not None:
                    s_revenue *= 1 + path_value(s.other, p)
            elif seg.mode == "level":
                # Эффективная площадь × плотность (тыс. ₽/м² в год, в ценах
                # полугодия якоря) × индекс ИПЦ мира × доля полугодия;
                # тыс. м² × тыс. ₽/м² = млн ₽ → /1000 = млрд ₽.
                share = seg.spec["h1_share"] if half == 1 else 1.0 - seg.spec["h1_share"]
                s_revenue = (effective_avg * path_value(seg.spec["density_path"], p) / 1000.0
                             * inflation_index * share)
            else:
                # Опт (`revenue`): рост = LFL группы + `growth` (прибавка к LFL,
                # решение ведущего B14), а не номинальный рост.
                s_revenue = s.revenue_hist[prev] * (1 + lfl_group
                                                    + path_value(seg.spec["growth"], p))
            s.revenue_hist[p] = s_revenue
            base_revenue = s.revenue_hist[prev]
            segment_steps[seg.id] = SegmentStep(
                revenue=s_revenue, growth=(s_revenue / base_revenue - 1.0 if base_revenue else None),
                area_end=s.area_end if seg.network else None,
                effective_area_avg=effective_avg, opened=s_opened, closed=s_closed)
        area_end = sum(s.area_end for s in network)
        effective_total = sum(s.effective_hist[p] for s in network)
        revenue = sum(step.revenue for step in segment_steps.values())
        revenue_hist[p] = revenue
        revenue_annual = revenue + (revenue_hist[P[i - 1]] if i > 0 else revenue_hist[anchor])
        revenue_index = revenue_annual / revenue_ltm0

        # --- маржа: цель режима + сезонность + затухающее отклонение
        deviation = rho * deviation + float(shocks.get(p, 0.0))
        season = margin_season(A, p)
        if p in observed:
            # Факт (se = 0) заменяет отклонение. Нау-каст сдвигает переносимое
            # отклонение к наблюдённому с весом P/(P + se²): с ошибкой в
            # полпункта он не двигает оценку так же уверенно, как отчёт.
            value, se = observed[p]
            raw_deviation = value - path_value(regime["target"], p) - season
            if not se:
                deviation, deviation_var = raw_deviation, 0.0
            else:
                prior_var = sigma2
                if observed_at is not None:
                    decay = rho ** (2 * (i - observed_at))
                    prior_var = sigma2 * (1 - decay) + decay * deviation_var
                weight = observation_weight(A, se, prior_var)
                deviation += weight * (raw_deviation - deviation)
                deviation_var = (1 - weight) * prior_var
            observed_at = i
        margin = path_value(regime["target"], p) + season + deviation
        ebitda = revenue * margin
        ebitda_hist[p] = ebitda
        ebitda_ltm = ebitda + (ebitda_hist[P[i - 1]] if i > 0 else ebitda_hist[anchor])
        # EBITDA за последние двенадцать месяцев на КОНЕЦ каждого периода.
        # Нужна не внутри цикла, а в мосте: знаменатель `ev_ebitda_ltm` берётся
        # на последней ЗАКРЫТОЙ границе. Без этого списка его пришлось бы
        # восстанавливать из `leverage` делением, то есть считать дважды.
        ltm_ebitda_by_period.append(ebitda_ltm)

        # --- capex: поддерживающий + открытия + инфраструктура на чистый прирост
        # A-K4 (решение ведущего D23): чистый прирост — по сегментам,
        # Σ max(0, открыто_s − закрыто_s): закрытия гипермаркетов не гасят
        # потребность в РЦ растущего «у дома» (правило 850oa по группе при
        # смешении форматов ошибочно; у одного сегмента — то же число).
        net_new = sum(max(0.0, step.opened - step.closed) for step in segment_steps.values())
        infra = (net_new * C["infra_capex_per_net_m2"] * unit_price_index
                 if year >= C["infra_from_year"] else 0.0)
        maintenance = revenue * path_value(C["maintenance_pct"][cell.capex], p)
        if area_share:
            # A-K1 (`capex.maintenance_area_share`): физические
            # статьи поддерживающего capex (редизайн, оборудование, здания)
            # посчитаны на метры в ценах якоря, поэтому их доля s идёт за
            # физической площадью × индексом цен, а не за выручкой:
            # множитель (1 − s) + s·x_t/x_0, x = площадь середины полугодия ×
            # индекс цен / годовая выручка, x_0 — первого прогнозного полугодия.
            area_intensity = (area_end - (opened - closed) / 2.0) * inflation_index / revenue_annual
            if i == 0 and rules.maintenance_area_base is None:
                area_intensity_0 = area_intensity
            maintenance *= 1.0 - area_share + area_share * area_intensity / area_intensity_0
        # A-K3 по сегментам: открытия × capex на м² своего формата × индекс цен
        # удельных цен (`capex.unit_price_basis`).
        growth_capex = sum(segment_steps[s.seg.id].opened * s.seg.growth_capex_per_m2
                           * unit_price_index for s in network)
        capex = maintenance + growth_capex + infra
        # Интеграционный capex приобретённого периметра (`capex.integration_capex`,
        # млрд ₽ по полугодиям): отдельная строка, только явный период.
        capex_integration = path_value(integration, p) if integration is not None else 0.0
        if integration is not None:
            capex += capex_integration

        # A-K6: линейно, как в терминале.
        # База D&A якоря списывается линейно за срок службы, каждая когорта
        # capex — равными долями 1/(2L) в 2L следующих полугодиях (дробный срок
        # — неполной последней долей). После перезаякоривания база, её возраст
        # и прежние когорты — из фактов.
        half_life = 2 * C["asset_life_years"]
        vintages.append(capex_prev)
        da = (da_anchor * max(0.0, 1 - (legacy_halves + i + 1) / half_life)
              + sum(v * min(1.0, max(0.0, half_life - age))
                    for age, v in enumerate(reversed(vintages))) / half_life)
        capex_prev = capex
        ebit = ebitda - da

        # --- оборотный капитал, операционная касса, денежная аренда
        nwc = (path_value(N["nwc_pct"][cell.nwc], p) * revenue_annual
               + (june_excess * revenue_index if half == 1 else 0.0))
        if acquired_nwc is not None:
            nwc += path_value(acquired_nwc, p) * revenue_annual
        nwc_change, nwc_prev = nwc - nwc_prev, nwc
        operating_cash = FN["operating_cash_pct"] * revenue_annual
        operating_cash_change, operating_cash_prev = operating_cash - operating_cash_prev, operating_cash
        lease_adjustment = M["cash_lease_adj_pct"] * revenue

        # --- налог: без рычага для FCFF, фактический для денег
        base = ebit + path_value(TX["permanent_addback_pct"], p) * revenue
        if tax_premium:
            base -= tax_premium * (capex - da)
        # Невычитаемая часть учётной D&A (`tax.nondeductible_da_anchor`,
        # `nondeductible_da`): налоговая D&A = премия·capex + (1 − премия)·
        # (учётная − невычитаемая).
        base += (1.0 - (tax_premium or 0.0)) * nondeductible_da(
            nondeductible_anchor, legacy_halves, i, half_life)
        tax_unlevered = max(0.0, tau * base)

        # Валовой долг для процентов и для пути против лимита линий не считает
        # прирост операционной кассы дважды (см. `StepRow.operating_cash_growth`).
        # Прирост на НАЧАЛО полугодия — уровень конца минус приращение: так
        # таблицы книги закреплены до последнего бита.
        opc_growth_start = operating_cash - operating_cash_change - operating_cash_0
        debt = net_debt + cash - opc_growth_start
        # Долг — корзины действующего долга (`financing.rate_baskets`: доли всего
        # долга, своя ставка по `until`) и новый долг вне живых корзин: доля
        # `fixed_share` — кривая мира на 3 года + спред, остальное — ключевая +
        # спред (решение ведущего A10).
        fixed_share = path_value(FN["fixed_share"], p)
        curve_3y = interp_curve(W["zero_curve"], 3.0)
        new_rate = (fixed_share * (curve_3y + FN["spread_fixed"][cell.credit])
                    + (1 - fixed_share) * (key + FN["spread_float"][cell.credit]))
        debt_rate = basket_debt_rate(baskets, p, key, new_rate)
        net_interest = debt * half_rate(debt_rate) - cash * half_rate(FN["cash_yield_k"] * key)
        carry = (max(0.0, cash - cash_target)
                 * max(0.0, half_rate(key) - half_rate(FN["cash_yield_k"] * key)))

        # max() применяется к разности ПОЛУГОДОВЫХ ставок, а не к разности
        # годовых: `half_rate(a) − half_rate(b)` и `half_rate(a − b)` — разные
        # числа (в чувствительности «спред +5 п.п.» — 149,5 против 138,1).
        # Справедливая ставка: новый долг — по справедливым спредам, корзина —
        # по СВОЕЙ ставке (премия старых купонов — строка моста, A10).
        new_fair = (fixed_share * (curve_3y + fair_spread["fixed"][cell.credit])
                    + (1 - fixed_share) * (key + fair_spread["float"][cell.credit]))
        rate_fair = basket_debt_rate(baskets, p, key, new_fair)
        excess_interest = debt * max(0.0, half_rate(debt_rate) - half_rate(rate_fair))

        pool = alpha * base - net_interest
        # Запертые убытки приобретённых юрлиц (`tax.acquired_nol`, правило
        # `frozen_until_usable_from`, решение ведущего D26): до `usable_from`
        # убытки запертых юрлиц прогноза (`locked_addback`) консолидированную
        # базу не уменьшают — прибавляются к ней и копятся в их пуле; в
        # `usable_from` (присоединение к основной «дочке») пул переходит в пул
        # группы с долей (1 − haircut); дальше прибавки нет. Налог без рычага
        # правило не трогает: ценность — через щит APV.
        if locked:
            if period_index(p) < period_index(locked["usable_from"]):
                addback = locked["locked_addback"].get(p, 0.0)
                pool += addback
                nol_acquired += addback
            elif not merged:
                nol += (1.0 - locked["haircut"]) * nol_acquired
                nol_acquired, merged = 0.0, True
        if pool < 0:
            nol, taxable_pool = nol - pool, 0.0
        else:
            limit = TX["nol_limit"] if year < TX["nol_full_from_year"] else 1.0
            used = min(nol, limit * pool)
            nol, taxable_pool = nol - used, pool - used
        tax_actual = max(0.0, tau * (1 - alpha) * base) + tau * taxable_pool
        shield = tax_unlevered - tax_actual

        # Поступления от выбытия ОС (`capex.disposal_proceeds_pct`): остаточная
        # стоимость выбывшего (прибыль от выбытия уже в EBITDA), доля выручки,
        # налогом не облагаются.
        disposal_proceeds = rules.disposal_proceeds_pct * revenue
        fcff = (ebitda - tax_unlevered - capex - nwc_change
                - operating_cash_change + lease_adjustment
                + disposal_proceeds)

        # --- дисконтирование от ДАТЫ ОЦЕНКИ, а не от начала прогноза
        #
        # Закрытый период (i < closed) не приводится вовсе: его деньги уже
        # получены и лежат в чистом долге на дату оценки. Текущий период
        # (i == closed) идёт остатком, остальные — целиком, и время у них
        # отсчитывается от даты оценки.
        if i < closed_periods:
            fraction, t_mid = 0.0, 0.0
        elif i == closed_periods:
            fraction, t_mid = (1 - elapsed), (1 - elapsed) * 0.25
        else:
            fraction = 1.0
            t_mid = (1 - elapsed) * 0.5 + (i - closed_periods - 1) * 0.5 + 0.25
        df = discount(t_mid) if fraction else 0.0
        if i == closed_periods and nwc_weight is not None:
            # Доля текущего полугодия в EV — та же, что в перекате чистого
            # долга (`nwc.quarter_share`, решение ведущего A9): поток без ΔОК —
            # остатком по дням, ΔОК — остатком по квартальным весам ОК.
            pv_fcff += ((fcff + nwc_change) * fraction - nwc_change * (1 - nwc_weight)) * df
        else:
            pv_fcff += fcff * fraction * df
        pv_shield += shield * fraction * df
        pv_excess += excess_interest * fraction * df
        pv_carry += carry * fraction * df

        # --- долг, выплаты по строкам моста, дивиденды, касса
        put = payments.get(p, 0.0)
        net_debt_new = net_debt - (fcff + shield - net_interest - put)
        fcfe = net_debt - net_debt_new
        # Прирост операционной кассы с якоря — разница модельного и отчётного ЧД.
        opc_growth = operating_cash - operating_cash_0 if reported_basis else 0.0
        if timing is None:
            dividends, rung = dividend_rule(A, year, net_debt - opc_growth,
                                            net_debt_new - opc_growth, ebitda_ltm, ladder)
        else:
            # Годовое правило: в 1П платится объявленное по итогам прошлого
            # года, во 2П выплат нет; FCFE года копится.
            dividends, rung = (declared if half == 1 and declared is not None else (0.0, None))
            if half == 1:
                declared, year_fcfe = None, fcfe
            else:
                year_fcfe += fcfe
        net_debt_new += dividends
        if timing is not None and half == 2 and year + 1 >= FN["dividends_from_year"]:
            # Конец года: ступень по рычагу 31.12, выплата — в 1П следующего года.
            declared = ladder_payout(FN, year_fcfe, net_debt_new - opc_growth, ebitda_ltm, ladder)
        net_debt = net_debt_new

        cash_target = operating_cash + FN["liquidity_buffer_pct"] * revenue_annual
        cash = max(cash_target, cash_target + (cash - cash_target) * FN["prefunded_cash_decay"])

        rows.append(StepRow(
            period=p, year=year, half=half, revenue=revenue,
            lfl_ticket=ticket, lfl_traffic=traffic, area_end=area_end,
            effective_area_avg=effective_total, margin=margin, ebitda=ebitda,
            da=da, ebit=ebit, capex=capex, capex_maintenance=maintenance,
            capex_growth=growth_capex, capex_infra=infra, nwc_change=nwc_change,
            operating_cash_change=operating_cash_change, lease_adjustment=lease_adjustment,
            disposal_proceeds=disposal_proceeds,
            tax_unlevered=tax_unlevered, tax_actual=tax_actual, tax_shield=shield,
            fcff=fcff, net_interest=net_interest, debt_rate=debt_rate,
            net_debt=net_debt, cash=cash, dividends=dividends,
            leverage=net_debt / ebitda_ltm if ebitda_ltm else float("inf"),
            interest_cover=(ebitda_ltm / (2 * net_interest)) if net_interest > 0 else 99.0,
            operating_cash_growth=operating_cash - operating_cash_0,
            tax_loss_pool=nol, tax_loss_pool_acquired=nol_acquired,
            capex_integration=capex_integration, settlement_payment=put,
            fcfe=fcfe, dividend_rung=rung, segments=segment_steps,
        ))

    # ---------------------------------------------------- терминальная стоимость
    #
    # ПОЛУГОДОВЫМИ ПОТОКАМИ (Р10, A-V3t): как и в явном периоде, потоки падают
    # на середины полугодий; Гордон «на конец года» рассогласовал бы сроки и
    # занизил терминал примерно на 8 %.
    #
    # Рост в терминале — номинальный LFL последней клетки, а не инфляция. При
    # LFL ниже инфляции вечный рост «по инфляции» означал бы, что сеть вечно
    # отыгрывает потерянную долю рынка; при LFL выше — наоборот. Берётся именно
    # LFL клетки: состояние спроса привязано к режиму маржи.
    last, prev_half = rows[-1], rows[-2]
    W = A["worlds"][cell.world]
    t_end = (1 - elapsed) * 0.5 + (len(P) - closed_periods - 1) * 0.5
    r_long = W["zero_curve"]["LT"] + premium
    inflation_lt = W["lt"]["inflation"]

    # Рост сегмента в терминале. yoy — LFL группы последнего периода плюс
    # поправка сегмента, как выручка явного периода («прочий рост» — не вечный
    # рост сети); level — ИПЦ мира последнего периода (плотность в LT
    # постоянна); revenue — LFL группы плюс его `growth` последнего периода. У
    # сегментов сети — подъём ротации: закрытая площадь уходит с
    # продуктивностью сегмента, замещающая дозревает до d·maturity[−1], и
    # эффективная площадь растёт на эту разницу × долю закрытий.
    # Рост группы — средний рост сегментов с весами выручки последнего года
    # (у одного сегмента — ровно его рост).
    year_revenue = last.revenue + prev_half.revenue
    lfl_last = (1 + last.lfl_ticket) * (1 + last.lfl_traffic) - 1.0
    g = 0.0
    for s in states:
        seg = s.seg
        if seg.mode == "yoy":
            s_g = lfl_last + (0.0 if s.offset is None else path_value(s.offset, P[-1]))
        elif seg.mode == "level":
            s_g = path_value(A["worlds"][cell.world]["cpi"], P[-1])
        else:
            s_g = lfl_last + path_value(seg.spec["growth"], P[-1])
        if seg.network:
            s_g = (1 + s_g) * (1 + (seg.new_space_density * maturity[-1] - seg.closed_productivity)
                               * path_value(s.space["close"], P[-1])) - 1
        weight = (last.segments[seg.id].revenue + prev_half.segments[seg.id].revenue) / year_revenue
        g += weight * s_g
    if g >= r_long:                       # вечный рост выше ставки не считается
        g = r_long - 1e-4

    life = C["asset_life_years"]
    ratio = annuity_ratio(g, life)
    # Статьи capex, индексируемые ценами (замещающие открытия, физическая доля
    # поддерживающего capex), капитализируются отдельным Гордоном с ростом π,
    # остальное — с ростом g; амортизация каждой статьи — аннуитетом своего
    # роста (раздельный Гордон).
    growth_pi = min(inflation_lt, r_long - 1e-4)
    ratio_pi = annuity_ratio(growth_pi, life)
    maintenance_lt = path_value(C["maintenance_pct"][cell.capex], P[-1])
    margin_lt = path_value(regime["target"], P[-1])
    addback_lt = path_value(TX["permanent_addback_pct"], P[-1])
    nwc_lt = path_value(N["nwc_pct"][cell.nwc], P[-1])
    acquired_lt = path_value(acquired_nwc, P[-1]) if acquired_nwc is not None else 0.0

    r_real = (1 + r_long) / (1 + inflation_lt) - 1
    # Множитель реального роста (`valuation.ronic_spread`,
    # `valuation.terminal_real_growth`). При нулевом спреде RONIC он РАВЕН
    # ЕДИНИЦЕ: доходность новых вложений равна стоимости капитала, и рост
    # стоимости не создаёт. Параметры всё равно читаются, а не игнорируются —
    # иначе первая же их правка в книге не изменила бы ничего.
    growth_real = V["terminal_real_growth"]
    ronic_spread = V["ronic_spread"]
    growth_mult = (1 + growth_real * (1 - r_real / (r_real + ronic_spread))
                   / (r_real - growth_real)) if ronic_spread else 1.0

    margin_terminal = margin_lt
    # Оборотный капитал терминала — на СКОЛЬЗЯЩЕЙ годовой выручке, как в явном
    # периоде, и от ФАКТИЧЕСКИХ уровней последнего полугодия: одна годовая
    # величина на оба полугодия дала бы в первом терминальном полугодии разовый
    # приток или отток, которого в природе нет. Июньский сезонный набор
    # индексируется ВЫРУЧКОЙ, а не ИПЦ: это запас товара, а не цена.
    nwc_level_prev = nwc_prev
    opc_level_prev = operating_cash_prev
    revenue_back = last.revenue
    # Индекс capex растёт ПОЛУГОДИЯМИ: целый год инфляции обоим терминальным
    # полугодиям сразу — половина года лишней инфляции в первом из них.
    index_terminal = inflation_index

    assert P[-1].endswith("H2") and len(rows) >= 2, (
        "полугодовой терминал требует, чтобы явный период кончался вторым полугодием")

    terminal = terminal_rule(A)
    # База выручки терминала (`valuation.terminal.revenue_base`): при `exit_area` —
    # эффективная площадь на выходе явного периода (`terminal_revenue_factors`);
    # уровни ОК и операционной кассы на границе — тем же множителем, чтобы
    # разовый сдвиг базы не капитализировался Гордоном как поток.
    revenue_factors = (1.0, 1.0)
    if terminal["revenue_base"] == "exit_area":
        revenue_factors = terminal_revenue_factors(network, maturity, P, prev_half, last)
        revenue_back = last.revenue * revenue_factors[1]
        nwc_level_prev = nwc_prev * revenue_factors[1]
        opc_level_prev = operating_cash_prev * revenue_factors[1]

    halves = []
    for half_row, half_no in ((prev_half, 1), (last, 2)):
        index_terminal *= 1 + half_rate(inflation_lt)
        unit_price_terminal = (index_terminal / (1 + half_rate(inflation_lt)) ** 0.5
                               if prices_at_anchor_end else index_terminal)
        rev = half_row.revenue * (1 + g) * revenue_factors[half_no - 1]
        revenue_annual_half, revenue_back = rev + revenue_back, rev
        season = margin_season(A, f"{int(P[-1][:4]) + 1}H{half_no}")
        # Замещающие открытия: сеть не растёт, но изношенную площадь обновляют
        # (по сегментам сети — своей долей закрытий и своим capex на м²).
        replacement = sum(s.area_end * path_value(s.space["close"], P[-1]) / 2.0
                          * s.seg.growth_capex_per_m2 * unit_price_terminal for s in network)
        if area_share:
            # Физическая доля поддерживающего capex — тем же x, что в явном
            # периоде: площадь (в терминале постоянна) × индекс цен.
            physical = (rev * maintenance_lt * area_share
                        * (area_end * index_terminal / revenue_annual_half) / area_intensity_0)
            maintenance_h = rev * maintenance_lt * (1 - area_share) + physical
        else:
            physical = 0.0
            maintenance_h = rev * maintenance_lt
        capex_h = maintenance_h + replacement
        nwc_level = (nwc_lt * revenue_annual_half
                     + (june_excess * revenue_annual_half / revenue_ltm0 if half_no == 1 else 0.0))
        if acquired_nwc is not None:
            nwc_level += acquired_lt * revenue_annual_half
        opc_level = FN["operating_cash_pct"] * revenue_annual_half
        halves.append(dict(rev=rev, ebitda=rev * (margin_lt + season), capex=capex_h,
                           capex_pi=replacement + physical,
                           d_nwc=nwc_level - nwc_level_prev,
                           d_opc=opc_level - opc_level_prev))
        nwc_level_prev, opc_level_prev = nwc_level, opc_level

    revenue_next = sum(h["rev"] for h in halves)
    ebitda_terminal = sum(h["ebitda"] for h in halves)
    capex_terminal = sum(h["capex"] for h in halves)
    # Амортизация терминала — аннуитет от ГОДОВОГО capex, поделённый между
    # полугодиями пополам: в вечном потоке амортизация не сезонна, и сезонность
    # выручки не должна протекать в налог.
    capex_pi = sum(h["capex_pi"] for h in halves)
    da_pi = capex_pi * ratio_pi
    da_terminal = (capex_terminal - capex_pi) * ratio + da_pi
    # Налоговая D&A терминала: без премии — аннуитет пополам; с премией
    # (`tax.capex_tax_premium_share`) — премия·capex полугодия + (1 − премия)·
    # аннуитет: стационарная разница налога и учёта при росте g (решение D25).
    def tax_da(h: dict) -> float:
        if tax_premium:
            return tax_premium * h["capex"] + (1 - tax_premium) * da_terminal / 2.0
        return da_terminal / 2.0

    flows = [h["ebitda"]
             - max(0.0, tau * (h["ebitda"] - tax_da(h) + addback_lt * h["rev"]))
             - h["capex"] - h["d_nwc"] - h["d_opc"] + M["cash_lease_adj_pct"] * h["rev"]
             + rules.disposal_proceeds_pct * h["rev"]
             for h in halves]

    # Формула книги: полугодовые потоки первого терминального года, приведённые
    # к концу явного периода, делятся на (r − g). Номинальная
    # `NOPAT·(1−g/RONIC)/(r−g)` занижает терминал на 18–22 % — решение A-V5.
    def gordon(first_half: float, second_half: float) -> float:
        """Пара полугодовых потоков, растущая темпом g раз в год."""
        return (first_half * (1 + r_long) ** 0.75
                + second_half * (1 + r_long) ** 0.25) / (r_long - g)

    # Часть потока, растущая с π: минус capex этих статей плюс щит их
    # амортизации (пока налог полугодия положителен — иначе щита нет).
    def pi_shield(h: dict) -> float:
        if tax_premium:
            return tau * (tax_premium * h["capex_pi"] + (1 - tax_premium) * da_pi / 2.0)
        return tau * da_pi / 2.0

    pi_flows = [(pi_shield(h) if h["ebitda"] - tax_da(h) + addback_lt * h["rev"] > 0 else 0.0)
                - h["capex_pi"] for h in halves]
    tv = (gordon(flows[0] - pi_flows[0], flows[1] - pi_flows[1])
          + (pi_flows[0] * (1 + r_long) ** 0.75 + pi_flows[1] * (1 + r_long) ** 0.25)
          / (r_long - growth_pi)) * growth_mult
    fcff_terminal = flows[0] + flows[1]

    # Долг терминала — от EBITDA с сезонностью, посчитанной по полугодиям, а не
    # от «годовая выручка × целевая маржа»: полугодия различаются и выручкой, и
    # маржой, и произведение сумм не равно сумме произведений.
    debt_terminal = FN["leverage_target"] * ebitda_terminal
    shield_rate = W["zero_curve"]["LT"] + FN["spread_fixed"][cell.credit]
    rate_fair_lt = W["zero_curve"]["LT"] + fair_spread["fixed"][cell.credit]
    # Полугодовая доля годовой ставки терминала (`valuation.terminal.
    # half_rate_convention`): `simple` — r/2 (850oa), `compound` — (1 + r)^0,5 − 1,
    # как проценты явного периода.
    compound = terminal["half_rate_convention"] == "compound"
    if compound:
        shield_half = tau * TX["alpha_terminal_shield"] * debt_terminal * half_rate(shield_rate)
        excess_half = debt_terminal * max(0.0, half_rate(shield_rate) - half_rate(rate_fair_lt))
    else:
        shield_terminal = tau * TX["alpha_terminal_shield"] * debt_terminal * shield_rate
        shield_half = shield_terminal / 2.0
        excess_lt = max(0.0, shield_rate - rate_fair_lt)
        excess_half = debt_terminal * excess_lt / 2.0
    # Налоговый щит терминала — тем же ПОЛУГОДОВЫМ Гордоном, что поток: щит
    # возникает вместе с процентами, в тех же серединах полугодий.
    tv_shield = gordon(shield_half, shield_half)
    df_end = discount(t_end)
    ev = pv_fcff + pv_shield + (tv + tv_shield) * df_end
    ev_dcf = ev

    # Контроль лимита кредитных линий: путь долга внутри клетки против суммы
    # «валовой долг + нераскрытые линии». Ветки докапитализации в модели нет,
    # поэтому превышение не чинится расчётом — оно должно быть ВИДНО.
    # Путь начинается с отчётной даты (якорь): там уже есть точка пути, и часто
    # она и есть максимум валового долга; касса — своего полугодия (`r.gross_debt`).
    max_net_debt, max_net_debt_period = _path_peak(
        AF["net_debt"], anchor, [(r.period, r.net_debt) for r in rows])
    max_gross_debt, max_gross_debt_period = _path_peak(
        AF["net_debt"] + AF["cash"], anchor, [(r.period, r.gross_debt) for r in rows])
    max_leverage = max(r.leverage for r in rows)

    # Издержки финансовой неустойчивости (`valuation.distress`, пара A-T4): щит
    # терминала по правилу двух корзин (`tax.alpha_terminal_shield` = 1,0) и
    # явные издержки — доля EV в клетках, где ЧД/EBITDA по пути выше порога или
    # валовой долг выходит за лимит линий. Лимит — функция одних фактов книги
    # (валовой долг отчётной даты + нераскрытые линии), одинаковая для всех
    # клеток и та же, что у гейта `credit_lines`: EV клетки не зависит от того,
    # кто её посчитал — сетка, сценарий или проверка.
    # Триггер — только полугодия после даты оценки (`rows[closed_periods:]`):
    # издержки — ожидаемые будущие потери, и прокатанная книга определяет
    # нарушение так же, как перезаякоренная. Поля пути выше — диагностика.
    from model.financing import credit_limit

    distress = distress_rule(A)
    distress_cost = 0.0
    ahead_path = rows[closed_periods:]
    hit = max(r.leverage for r in ahead_path) > distress["net_leverage_trigger"] or (
        distress["credit_limit_trigger"]
        and max(r.gross_debt for r in ahead_path) > credit_limit(A))
    if hit:
        # Издержки только уменьшают стоимость: доля берётся от положительной
        # части EV. Клетка с отрицательным EV (M × стресс × высокий capex,
        # EV ≈ −20 млрд ₽) при EV × (1 − доля) ПОВЫШАЛА бы стоимость.
        distress_cost = distress["cost_pct_ev"] * max(ev_dcf, 0.0)
        ev = ev_dcf - distress_cost

    # Избыточная стоимость долга (Р11): долг с купоном выше справедливого
    # спреда стоит дороже номинала, и эта разница — требование сверх учтённого
    # в мосте. Без неё рост спреда ПОВЫШАЛ оценку: щит рос, а долг вычитался
    # по номиналу. Односторонне: спред НИЖЕ справедливого требований не режет.
    # Явный период посчитан в главном цикле; здесь — терминал, тем же
    # полугодовым Гордоном, что щит: избыточный купон платится в те же сроки.
    addon = pv_excess
    addon += gordon(excess_half, excess_half) * df_end
    # Терминальная часть добавки — вычитанием: строка выше приводит её тем же
    # Гордоном, что щит, и мутации портят именно её.
    addon_terminal = (addon - pv_excess) / df_end if df_end else 0.0

    # ------------------------------------------------------------------ мост
    #
    # Чистый долг на дату оценки: якорь последнего ЗАКРЫТОГО периода (а при
    # свежей книге — отчётный факт) минус деньги, заработанные прошедшей
    # частью текущего периода. Закрытые периоды уже учтены в `net_debt`
    # своей строки — вместе с дивидендами и выплатами по строкам моста.
    current = rows[closed_periods]
    anchor_debt = (AF["net_debt"] if closed_periods == 0
                   else rows[closed_periods - 1].net_debt)
    if nwc_weight is None:
        net_debt_valuation = anchor_debt - elapsed * (
            current.fcff + current.tax_shield - current.net_interest)
    else:
        # Перекат с квартальными весами ОК (`nwc.quarter_share`, решение
        # ведущего A9): у Ленты второе полугодие делает 4 кв. (ОК 3 кв. ещё
        # растёт), и линейный перекат занижал бы ЧД на дату оценки. Поток без
        # ΔОК — по дням, ΔОК — по весу w; тот же w — в доле полугодия в EV.
        net_debt_valuation = anchor_debt - (
            elapsed * (current.fcff + current.nwc_change + current.tax_shield
                       - current.net_interest)
            - nwc_weight * current.nwc_change)

    # EBITDA LTM «на дату оценки» — на последней ЗАКРЫТОЙ границе, там же, где
    # стоит якорь долга. Прежде знаменатель `ev_ebitda_ltm` стоял на 2026H1 при
    # любой дате оценки, и гейт `ev_ebitda` дрейфовал вместе с одним только
    # числителем (аудит A4). Шаг, а не плавное изменение внутри периода, — это
    # и есть поведение отчётной величины: EV меняется каждый день, LTM — на
    # отчёте.
    ebitda_ltm_valuation = (AF["ebitda_ltm"] if closed_periods == 0
                            else ltm_ebitda_by_period[closed_periods - 1])

    # ОПЕРАЦИОННАЯ КАССА В МОСТЕ ОСТАЁТСЯ УРОВНЕМ ОТЧЁТНОЙ ДАТЫ — и это не
    # недоделка, а тождество. Прирост операционной кассы уже вычтен из FCFF
    # (`operating_cash_change` в главном цикле), поэтому модельный чистый долг
    # строки — это НЕ отчётный чистый долг, а «отчётный плюс операционная
    # касса»: money, ушедшие в кассовый поплавок, модель считает израсходованными.
    # Сложение даёт ровно то, что нужно требованиям, при ЛЮБОЙ дате оценки:
    #   отчётный долг(t) + касса(t)
    #     = [якорь − накопленный Δкассы − elapsed·(поток + Δкассы текущего)]
    #       + [касса(граница) + elapsed·Δкассы текущего]
    #     = якорь − elapsed·поток + касса₀.
    # Подстановка сюда «уровня на дату оценки» посчитала бы прирост поплавка
    # дважды: 6,8 млрд ₽ к началу 2029 года и ≈7,4 к 2030-му, то есть −55 ₽
    # печатаемого центра. Тождество закреплено тестом
    # `test_operating_cash_in_the_bridge_is_the_reported_level`.
    # Строки моста (`bridge.items`) — в порядке книги, требования со знаком
    # плюс, активы — минус с долей признания.
    claims = net_debt_valuation + operating_cash_0
    for _, value in bridge_lines(A, closed_periods, P):
        claims += value
    claims_par = claims               # требования по номиналу, до добавки Р11 и кэрри кассы
    claims += addon
    claims += pv_carry
    equity_raw = ev - claims
    governance = V.get("governance_discount", 0.0)
    equity = equity_raw * (1 - governance) if equity_raw > 0 else equity_raw

    # Цена клетки с полом — то же отображение, что у слоя (внутренняя
    # стоимость max(EV − D, 0)·(1 − g), `model.mapping`): отдельной
    # «вспомогательной» оценки клетки у внутренней стоимости нет.
    shares = F["shares_out_mln"]

    ahead = rows[closed_periods:closed_periods + 2]
    ebitda_ntm = sum(r.ebitda for r in ahead) if len(ahead) == 2 else 0.0
    return CellResult(
        cell=cell, rows=rows, ev=ev, pv_fcff=pv_fcff, pv_tax_shield=pv_shield,
        terminal_value=tv + tv_shield,
        terminal_flow_value=tv, terminal_shield_value=tv_shield,
        terminal_debt_cost_addon=addon_terminal,
        terminal_share=(tv + tv_shield) * df_end / ev_dcf if ev_dcf else 0.0,
        claims=claims, equity=equity, equity_before_governance=equity_raw,
        price=equity * 1000 / shares, price_floor=max(equity, 0.0) * 1000 / shares,
        debt_cost_addon=addon, terminal_growth=g,
        max_net_debt=max_net_debt, max_gross_debt=max_gross_debt, max_leverage=max_leverage,
        max_net_debt_period=max_net_debt_period, max_gross_debt_period=max_gross_debt_period,
        claims_par=claims_par, terminal_ebitda=ebitda_terminal,
        distress_cost=distress_cost, cash_carry=pv_carry,
        ev_ebitda_ltm=ev / ebitda_ltm_valuation,
        ev_ebitda_ntm=ev / ebitda_ntm if ebitda_ntm > 0 else None,
        r_long=r_long, r_real=r_real,
        exit_multiple=(tv + tv_shield) / (revenue_next * margin_terminal),
        fcff_terminal_margin=fcff_terminal / revenue_next,
    )


def _path_peak(start: float, anchor: str, path: list[tuple[str, float]]) -> tuple[float, str]:
    """Максимум пути долга от отчётной даты и его полугодие.

    Строгое «>»: при равенстве остаётся первое полугодие, при пути не выше
    старта — якорь.
    """
    peak, when = start, anchor
    for period, value in path:
        if value > peak:
            peak, when = value, period
    return peak, when
