"""Живые входы выпуска: цена и дата оценки; кривая — только диагностика.

Аудит второй итерации 850oa: «ежедневный выпуск ничего не пересчитывает».
Цена, дата оценки и кривая мира M были зашиты в книгу и в код; собранные
котировки в расчёт не входили вовсе.

**Что берётся живым и что нет.** Живыми становятся цена акции и дата оценки
(она равна дате цены). Кривая **в оценку не подставляется** — см. ниже.
Миры N и H остаются аналитическими: это не наблюдение, а суждение о том, как
разойдутся ставки, и менять его ежедневным тиком нельзя.

**Почему кривая мира M больше не живая.** Прежняя версия подставляла
собранную бескупонную кривую в мир M, и мир расходился сам с собой: кривая
новая, а инфляция (по ОФЗ-ИН), путь ключевой и долгосрочные значения того же
мира — книжные. Мир M книги — не кривая, а согласованный набор: форварды ОФЗ
дают путь ключевой, вменённая инфляция ОФЗ-ИН сглаживается суждением до 9 %,
от неё идут зарплаты и продовольственная инфляция. Рецепт пересборки этого
набора содержит суждения и принадлежит книге, а не коду. Отступление
согласовано аудитором как временное: механический рецепт живого мира M
придёт версией книги 1.3.

Собранная кривая при этом не выбрасывается: она проверяется и кладётся в
отчёт как НАБЛЮДЕНИЕ. На ней стоит диагностический блок «сдвиг кривой с даты
книги» и гейт «книга требует обновления» — то есть кривая отвечает на вопрос
«не устарела ли книга», а не «сколько стоит акция».

**Отказ второстепенного источника не отменяет выпуск.** Не прошедший
проверку вход заменяется последним ГОДНЫМ значением (из прошлого выпуска, а
при его отсутствии — книжным), а причина пишется в `live.degraded`: панель
покажет, что часть входов не свежая. Останавливать публикацию из-за одного
ряда хуже — прежний выпуск не станет свежее от того, что новый не вышел.

Сюда же попадают ОТКАЗАВШИЕ СБОРЩИКИ: их отчёт пишет другой процесс
(`indicators.collect`) в состояние, а выпуск читает его и добавляет строки в
тот же `live.degraded` — иначе флаг деградации обещался в журнале сбора и не
ставился в выпуске (аудит третьей итерации, A4).

**Коридоры годности.** Аудит показал, что сборка завершалась успехом при
кривой в процентах вместо долей (диапазон 0–350 ₽ без единого флага), при
цене 15,6 ₽, при кривой месячной давности и при отрицательной ставке. Ни
одна из этих величин не невозможна технически — все они невозможны
экономически, и именно это записано ниже числами.
"""

from __future__ import annotations

import copy
import datetime as dt
from dataclasses import dataclass, field

from indicators.issuer import PRICE_SERIES as ISSUER_PRICE, TICKER as ISSUER_TICKER
CURVE_PREFIX = "moex.zcyc."
OFZ_IN_PREFIX = "moex.ofz_in."
MAX_PRICE_AGE_DAYS = 7
# Реальная доходность ОФЗ-ИН вне 0,5–20 % — не рынок, а единицы: 6,31 вместо
# 0,0631 (проценты вместо долей), 0,000631 (доля, поделённая на 100 ещё раз)
# или нуль («сделки не было» в поле доходности). На 18.09.2026 все четыре
# выпуска — 6,3–8,8 %; коридор на порядок шире рынка и узок для ошибки единиц.
OFZ_IN_MIN, OFZ_IN_MAX = 0.005, 0.20

# Ставка вне 3–40 % годовых — это не рынок, а единицы измерения: 16,46
# вместо 0,1646 (кривая в процентах) или отрицательный узел.
RATE_MIN, RATE_MAX = 0.03, 0.40
# Меньше восьми узлов — не кривая, а обрывок: пять коротких узлов без
# 1/3/5/10 лет давали печатаемый диапазон 1 000–1 200 ₽ вместо 700–1 100.
MIN_CURVE_NODES = 8
# Возраст кривой в РАБОЧИХ днях: ЦБ не публикует её в выходные, и календарный
# порог ложно срабатывал бы каждый понедельник.
MAX_CURVE_AGE_BUSINESS_DAYS = 4
# Сдвиг узла к прошлому выпуску: 3 п.п. за сутки — это не движение рынка.
MAX_NODE_SHIFT = 0.03
# Узлы, по которым печатается сдвиг кривой с даты книги. Четыре, а не все
# одиннадцать: короткий конец живёт ключевой ставкой и шумит, а решение
# «пора обновлять книгу» принимается по среднему и длинному концу.
DIAGNOSTIC_NODES = (1.0, 3.0, 5.0, 10.0)
# Цена: ±30 % к ПОСЛЕДНЕЙ ПРИНЯТОЙ цене (у ликвидной акции планка не бывает шире).
# У LENT крупнейшее движение последнего года — выход из IMOEX: −10 % за день
# 18.09.2026, −11 % за две недели (книга, канал (в) дисконта за управление);
# коридор втрое шире — для порчи единиц, а не для рынка.
MAX_PRICE_JUMP = 0.30
# «Скачок за такт» определён, пока последняя принятая цена не старше недели к
# дате новой цены. После простоя дольше (такт стоял, сборщик молчал) скачок
# больше 30 % — уже не порча одного такта, а накопленное движение рынка.
PRICE_REFERENCE_MAX_AGE_DAYS = 7
# Такой скачок принимается, только когда его ПОДТВЕРЖДАЕТ соседний торговый
# день ряда: предыдущая точка не старше недели, позже последней принятой цены
# и в пределах 5 % от новой. Одна свеча после простоя — не рынок.
PRICE_CONFIRM_BAND = 0.05
# Отличие в 10 раз и больше — не рынок ни после какого простоя, а чужие
# единицы (копейки, тысячи рублей). Такая цена не принимается никогда: её
# лечит правка сборщика, а не ожидание.
PRICE_UNITS_FACTOR = 10.0


@dataclass
class LiveReport:
    """Что удалось взять живым, что осталось книжным и что наблюдено."""

    applied: dict[str, str] = field(default_factory=dict)
    degraded: list[str] = field(default_factory=list)
    observed_curve: dict[str, float] = field(default_factory=dict)
    """Проверенная собранная кривая — для диагностики, не для оценки."""
    observed_curve_date: str = ""
    curve_shift: dict[str, float] = field(default_factory=dict)
    """Сдвиг собранной кривой к книжной по узлам 1, 3, 5, 10 лет, в долях."""
    curve_reference: dict[str, float] = field(default_factory=dict)
    """ПОСЛЕДНЯЯ ГОДНАЯ кривая — эталон для проверки «сдвиг узла за такт».

    Отличается от `observed_curve` ровно в одном случае: когда сегодняшняя
    кривая отвергнута. Тогда `observed_curve` пуст (отвергнутая кривая не
    заменяется последней годной — так решил аудит), а эталон переносится из
    прошлого выпуска: без этого проверка выключала бы себя на следующем такте
    (аудит третьей итерации, D4)."""
    curve_reference_date: str = ""
    book_date: str = ""
    """Дата, на которую откалибрована книга (её `meta.valuation_date` ДО
    подстановки живой цены: после подстановки там стоит дата котировки)."""
    book_age_days: int = 0
    price_reference: float = 0.0
    """ПОСЛЕДНЯЯ ПРИНЯТАЯ цена — эталон проверки скачка и замена отвергнутой.

    Переносится из прошлого выпуска, пока новая цена не принята, — как
    `curve_reference` у кривой. Без собственной даты эталона «последнее
    годное» было ценой прошлого ВЫПУСКА без возраста: после простоя с
    движением рынка больше 30 % каждая новая цена отвергалась против
    старой, выпуск публиковал старую, и следующий такт снова сравнивал с
    ней — вечная блокировка (независимый аудит, G1-exam §5.1)."""
    price_reference_date: str = ""
    observed_ofz_in: dict[str, float] = field(default_factory=dict)
    """Реальные доходности ОФЗ-ИН (доли) на ДАТУ КРИВОЙ — вход пересборки миров.

    В оценку не идут. Их читает `ops/tools/refresh_worlds.py`: рецепту миров
    книги 1.4 (инфляция мира M по форвардным BEI) доходности нужны той же
    даты, что и кривая (`_observe_ofz_in`)."""
    observed_ofz_in_date: str = ""
    observed_ofz_in_missing: dict[str, str] = field(default_factory=dict)
    """Выпуск ОФЗ-ИН → почему его доходности на дату кривой в выпуске нет."""
    security: dict = field(default_factory=dict)
    """Карточка акции эмитента на бирже (D15): уровень листинга, акций в выпуске,
    дата последней точки и смены карточки (`indicators.sources.security_changes`).

    В оценку не идёт: смена уровня листинга или объёма выпуска — повод
    пересмотреть дисконт за управление (совещательный гейт `security_change`,
    `model.checks.check_security_card`), а не число модели."""

    @property
    def is_degraded(self) -> bool:
        return bool(self.degraded)

    def as_dict(self) -> dict:
        return {"applied": dict(self.applied), "degraded": list(self.degraded),
                "degraded_flag": self.is_degraded,
                "observed_curve": dict(self.observed_curve),
                "observed_curve_date": self.observed_curve_date,
                "curve_shift": dict(self.curve_shift),
                "curve_reference": dict(self.curve_reference),
                "curve_reference_date": self.curve_reference_date,
                "book_date": self.book_date,
                "book_age_days": self.book_age_days,
                "price_reference": self.price_reference,
                "price_reference_date": self.price_reference_date,
                "observed_ofz_in": dict(self.observed_ofz_in),
                "observed_ofz_in_date": self.observed_ofz_in_date,
                "observed_ofz_in_missing": dict(self.observed_ofz_in_missing),
                "security": dict(self.security)}


def _tenor_from_series(series_id: str) -> float | None:
    """`moex.zcyc.0.25y` → 0.25. Нераспознанное имя пропускается, а не падает."""
    tail = series_id[len(CURVE_PREFIX):]
    if not tail.endswith("y"):
        return None
    try:
        return float(tail[:-1])
    except ValueError:
        return None


def _latest(store, series_id: str):
    series = store.load(series_id)
    if not series or not series.points:
        return None
    return series.latest()


def _business_days(start: dt.date, end: dt.date) -> int:
    """Рабочих дней между датами. Отрицательный результат не сглаживается."""
    if end < start:
        return -_business_days(end, start)
    return sum(1 for i in range((end - start).days)
               if (start + dt.timedelta(days=i + 1)).weekday() < 5)


def apply_live_inputs(A: dict, store, *, today: dt.date | None = None) -> tuple[dict, LiveReport]:
    """Возвращает книгу с живыми входами и отчёт о том, что применилось."""
    A = copy.deepcopy(A)
    report = LiveReport()
    today = today or dt.date.today()
    # Дата книги снимается ДО подстановки цены: та перепишет `valuation_date`
    # датой котировки, и возраст книги стал бы нулём каждый день.
    report.book_date = A["meta"]["valuation_date"]
    # Кривые миров остаются книжными: их дата записывается до того, как дата
    # оценки уйдёт на дату котировки (`model.core.curve_as_of`; перекат по
    # форвардам книги 1.5 меряет расстояние от неё).
    A["meta"].setdefault("curve_as_of", report.book_date)
    report.book_age_days = (today - dt.date.fromisoformat(report.book_date)).days
    # Прошлый выпуск — источник «последнего годного». Импорт отложенный:
    # `model.payload` тянет `model.engine`, а тот — этот модуль; круг
    # разрывается тем, что чтение происходит во время вызова.
    from model.payload import previous_release

    previous = previous_release() or {}

    # Сборщики — первыми: невосполнимый пропуск важнее устаревшего узла кривой,
    # а плашка на витрине рисует список в этом же порядке.
    _apply_collector_report(store, report, today)
    _apply_price(A, store, report, today, previous)
    _observe_curve(store, report, today, previous, A)
    _observe_ofz_in(store, report, today)
    _observe_security(store, report)
    return A, report


def _apply_collector_report(store, report: LiveReport, today: dt.date) -> None:
    """Отказавшие сборщики — в `live.degraded`, иначе флага нет ни у кого.

    Аудит третьей итерации, A4(4): сбор печатал «выпуск выйдет с флагом
    деградации», а `degraded` заполнялся только ценой и кривой — обещание
    флага и было всей приёмкой. Сбор идёт ДРУГИМ процессом
    (`indicators.collect`), поэтому его отчёт лежит в состоянии рядом с
    рядами, а выпуск читает его отсюда. Импорт отложенный: слой индикаторов
    тянет книгу, и на верхнем уровне это был бы круг.
    """
    from indicators.collect import degradation_notes, read_collector_report

    report.degraded.extend(degradation_notes(read_collector_report(store), today=today))


def _reference_age(stamp: str, today: dt.date) -> int | None:
    """Возраст эталона сравнения в рабочих днях. None — даты нет или мусор."""
    try:
        return _business_days(dt.date.fromisoformat(stamp[:10]), today)
    except ValueError:
        return None


def _price_reference(A: dict, previous: dict) -> tuple[float, str, str]:
    """Последняя принятая цена, её дата и откуда она взята.

    Выпуск несёт эталон в `live.price_reference`; выпуски до 24.09.2026 его не
    знали — у них последняя принятая цена это цена выпуска, а её дата — дата
    оценки (при принятой цене они совпадали). Без прошлого выпуска эталон —
    книжная цена на дату книги.
    """
    was_live = previous.get("live") or {}
    if was_live.get("price_reference"):
        return (float(was_live["price_reference"]),
                str(was_live.get("price_reference_date") or ""),
                "последняя принятая, из прошлого выпуска")
    published = float((previous.get("market") or {}).get("price") or 0.0)
    if published:
        return (published, str((previous.get("meta") or {}).get("valuation_date") or ""),
                "цена прошлого выпуска")
    return (float(A["market"]["price"]), str(A["meta"]["valuation_date"]),
            "книжная — прошлого выпуска нет")


def _day(stamp: str) -> dt.date | None:
    """«ГГГГ-ММ-ДД…» → дата; пусто или мусор — None."""
    try:
        return dt.date.fromisoformat(str(stamp)[:10])
    except (TypeError, ValueError):
        return None


def _confirming_day(store, price: float, price_date: dt.date,
                    reference_date: dt.date | None) -> tuple[dt.date, float] | None:
    """Соседний торговый день, подтверждающий цену после простоя, или None.

    Берётся ближайшая ПРЕДЫДУЩАЯ точка ряда: не старше недели к дате цены,
    позже последней принятой цены (иначе «подтверждала» бы сама старая
    цена) и в пределах `PRICE_CONFIRM_BAND` от новой.
    """
    series = store.load(ISSUER_PRICE)
    if not series or not series.points:
        return None
    earlier = []
    for period, value in series.history(price_date.isoformat()).items():
        day = _day(period)
        if day is not None and day < price_date and value:
            earlier.append((day, float(value)))
    if not earlier:
        return None
    day, value = max(earlier)
    if (price_date - day).days > PRICE_REFERENCE_MAX_AGE_DAYS:
        return None
    if reference_date is not None and day <= reference_date:
        return None
    if abs(value / price - 1) > PRICE_CONFIRM_BAND:
        return None
    return day, value


def _apply_price(A: dict, store, report: LiveReport, today: dt.date, previous: dict) -> None:
    """Цена акции и дата оценки.

    Цена участвует ТОЛЬКО в сравнении с рынком (разрыв по EV, вменённые
    рынком веса), но именно поэтому её порча незаметна: при цене 15,6 ₽
    заголовок остаётся прежним, а разрыв «модель против рынка» меняет знак
    с −6 % на +11 %, и панель начинает утверждать обратное.

    **Последнее годное — последняя ПРИНЯТАЯ цена, с датой** (аудит третьей
    итерации, D4; независимый аудит 24.09.2026, G1-exam §5.1). Эталон едет в
    выпуске (`live.price_reference`) и обновляется только принятой ценой.

    **Дата оценки при отказе — дата такта, а не дата книги.** Прежде при
    отвергнутой или отсутствующей цене `valuation_date` оставалась книжной
    (18.09.2026): модель молча откатывалась назад во времени — на 01.03.2027
    это −42 ₽ центра, к 30.04.2027 −83 ₽, — и пропадала плашка «книга
    устарела». Дата оценки — положение модели во времени, и отказ котировки
    не отменяет того, что время прошло. Цена при этом — последняя принятая,
    со своей датой, и выпуск помечен деградацией.

    **Скачок больше 30 % не блокирует навсегда.** Правило «±30 % за такт»
    имеет смысл, пока эталон свежий (`PRICE_REFERENCE_MAX_AGE_DAYS`). Если
    эталон старше — такт стоял или сборщик молчал, — скачок принимается,
    когда его подтверждает соседний торговый день (`_confirming_day`), с
    пометкой деградации: снятая проверка обязана быть ВИДНА. Отказ не двигает
    эталон, поэтому любая серия отказов кончается не позже чем через неделю
    и один торговый день. Исключение одно — отличие в `PRICE_UNITS_FACTOR`
    раз и больше: это чужие единицы, и принимать их не будет ни один срок.
    """
    reference, reference_stamp, source = _price_reference(A, previous)
    reference_date = _day(reference_stamp)
    report.price_reference, report.price_reference_date = reference, reference_stamp
    book_date = dt.date.fromisoformat(report.book_date or A["meta"]["valuation_date"])
    dated = f" от {reference_stamp[:10]}" if reference_stamp else ""

    def keep_last(reason: str) -> None:
        # Перекат даты не откатывается: дата оценки — дата такта (но не
        # раньше книги), цена — последняя принятая со своей датой.
        valuation = max(today, book_date)
        A["market"]["price"] = reference
        if reference_date is not None:
            A["market"]["price_date"] = reference_date.isoformat()
        A["meta"]["valuation_date"] = valuation.isoformat()
        report.degraded.append(
            f"{reason} — взята {reference:.0f} ₽{dated} ({source}); "
            f"дата оценки — дата такта {valuation.isoformat()}")

    point = _latest(store, ISSUER_PRICE)
    if not point or not point.value:
        keep_last(f"нет собранной цены {ISSUER_TICKER}")
        return

    price = float(point.value)
    price_date = dt.date.fromisoformat(point.period[:10])
    age = (today - price_date).days
    if price <= 0:
        keep_last(f"цена {price} не положительна")
        return
    if age > MAX_PRICE_AGE_DAYS:
        keep_last(f"цена устарела на {age} дн")
        return

    ratio = price / reference
    confirmed_note = ""
    if abs(ratio - 1) > MAX_PRICE_JUMP:
        against = f"последней принятой ({reference:.0f} ₽{dated})"
        if ratio >= PRICE_UNITS_FACTOR or ratio <= 1 / PRICE_UNITS_FACTOR:
            keep_last(f"цена {price:.1f} ₽ отличается от {against} в "
                      f"{max(ratio, 1 / ratio):.0f} раз — это чужие единицы, а не рынок; "
                      "нужна правка сборщика")
            return
        gap = (price_date - reference_date).days if reference_date else None
        if gap is not None and gap <= PRICE_REFERENCE_MAX_AGE_DAYS:
            keep_last(f"цена {price:.1f} ₽ отличается от {against} на {abs(ratio - 1):.0%} "
                      f"при пределе {MAX_PRICE_JUMP:.0%} за такт")
            return
        stale = f"{gap} дн" if gap is not None else "дата неизвестна"
        confirming = _confirming_day(store, price, price_date, reference_date)
        if confirming is None:
            keep_last(f"цена {price:.1f} ₽ отличается от {against} на {abs(ratio - 1):.0%}; "
                      f"эталон старше {PRICE_REFERENCE_MAX_AGE_DAYS} дн ({stale}), и скачок "
                      "после простоя принимается, когда его подтвердит соседний торговый "
                      f"день в пределах {PRICE_CONFIRM_BAND:.0%}, — ждём подтверждения")
            return
        day, value = confirming
        confirmed_note = (
            f"проверка скачка цены снята: последняя принятая {reference:.0f} ₽{dated} "
            f"старше {PRICE_REFERENCE_MAX_AGE_DAYS} дн ({stale}); скачок {ratio - 1:+.0%} "
            f"подтверждён торговым днём {day.isoformat()} ({value:.1f} ₽) — цена "
            f"{price:.1f} ₽ на {price_date} принята")

    A["market"]["price"] = price
    A["market"]["price_date"] = price_date.isoformat()
    # Дата оценки — это дата ЦЕНЫ. Иначе сравнение «модель против рынка»
    # сопоставляет стоимость на одну дату с котировкой на другую, и разрыв
    # частично объясняется просто сдвигом во времени.
    A["meta"]["valuation_date"] = price_date.isoformat()
    report.applied["price"] = f"{price:.0f} ₽ на {price_date}"
    report.price_reference, report.price_reference_date = price, price_date.isoformat()
    if confirmed_note:
        report.degraded.append(confirmed_note)


def curve_shift(A: dict, curve: dict[float, float]) -> dict[str, float]:
    """Сдвиг собранной кривой к КНИЖНОЙ кривой мира M по узлам диагностики.

    Это единственное, что делает собранная кривая после возврата оценки на
    книжные миры (A2, п. 5): она отвечает на вопрос «не устарела ли книга», а
    не «сколько стоит акция». Сравнение идёт с кривой мира M, потому что
    рыночный мир книги и есть слепок рынка на дату калибровки.
    """
    from model.book import interp_curve

    book_curve = A["worlds"]["M"]["zero_curve"]
    out = {}
    for tenor in DIAGNOSTIC_NODES:
        if not curve:
            continue
        observed = (curve[tenor] if tenor in curve
                    else interp_curve({str(k): v for k, v in curve.items()}, tenor))
        out[f"{tenor:g}"] = observed - interp_curve(book_curve, tenor)
    return out


def _observe_curve(store, report: LiveReport, today: dt.date, previous: dict,
                   book: dict) -> None:
    """Собранная кривая: проверить и записать НАБЛЮДЕНИЕМ, в оценку не ставить.

    Проверки остаются нужны и без подстановки: на этой кривой стоят
    диагностический блок «сдвиг с даты книги» и гейт обновления книги. Кривая
    в процентах вместо долей дала бы сдвиг 1 600 б.п. и тревогу об устаревшей
    книге каждый день — то есть выключенную тревогу.

    **Проверка сдвига узлов больше не выключает себя** (аудит третьей
    итерации, D4). Она сравнивала кривую с `observed_curve` прошлого выпуска,
    а отвергнутая кривая в выпуск не попадает: после первого же срабатывания
    следующий такт сравнивал не с чем и принимал любую кривую. То есть сторож
    ловил ровно один такт из бесконечности, причём именно тот, за которым шёл
    незащищённый. Теперь эталон сравнения — отдельное поле
    `curve_reference`: оно переносится из прошлого выпуска, когда сегодняшняя
    кривая отвергнута, и обновляется только годной кривой.

    Эталон живёт столько же, сколько годна сама кривая
    (`MAX_CURVE_AGE_BUSINESS_DAYS`): «сдвиг за такт» через неделю простоя
    перестаёт быть сдвигом за такт, и порог 3 п.п. начал бы отвергать честное
    движение рынка навсегда. Просроченный эталон снимает проверку — и ГОВОРИТ
    об этом строкой деградации: молчаливо снятая проверка и есть та болезнь,
    которая здесь лечится.
    """
    # Эталон сравнения ставится ПЕРЕД проверками: любой выход ниже — это
    # «сегодняшняя кривая не годится», и эталон обязан доехать до следующего
    # такта нетронутым.
    was_live = previous.get("live") or {}
    report.curve_reference = dict(was_live.get("curve_reference")
                                  # Выпуски до этой правки поля не знали: у них
                                  # эталоном была сама наблюдённая кривая.
                                  or was_live.get("observed_curve") or {})
    report.curve_reference_date = str(was_live.get("curve_reference_date")
                                      or was_live.get("observed_curve_date") or "")

    curve: dict[float, float] = {}
    days: dict[float, dt.date] = {}
    out_of_range: list[str] = []
    for series in store.all_series():
        if not series.id.startswith(CURVE_PREFIX):
            continue
        tenor = _tenor_from_series(series.id)
        point = series.latest()
        if tenor is None or not point or point.value is None:
            continue
        value = float(point.value)
        if not (RATE_MIN <= value <= RATE_MAX):
            out_of_range.append(f"{tenor:g}y={value:.4g}")
            continue
        curve[tenor] = value
        days[tenor] = dt.date.fromisoformat(point.period[:10])

    if out_of_range:
        report.degraded.append(
            f"узлы кривой вне коридора {RATE_MIN:.0%}–{RATE_MAX:.0%}: "
            + ", ".join(sorted(out_of_range)[:4])
            + " — кривая не принята как наблюдение")
        return
    # Узлы разных дней в одну кривую не склеиваются — то же правило, что у
    # `state_curve` инструмента пересборки миров (внешний аудит 30.09.2026, T06).
    # Сборщик пишет каждый узел своим рядом: узел, не пришедший в ответе (или
    # срок, который биржа сняла), остаётся в состоянии со старой датой и
    # подклеивался бы к свежим. Тогда минимум узлов и возраст кривой мерились бы
    # по самому свежему узлу, а сдвиг застрявшего узла к книге стоял бы на месте
    # — гейт обновления книги молчал бы при движении «брюха» кривой. Отказ, а не
    # «оставить узлы свежего дня»: недостающий узел сдвига заполнила бы
    # интерполяция соседних сроков.
    if len(set(days.values())) > 1:
        newest = max(days.values())
        behind = sorted((tenor, day) for tenor, day in days.items() if day != newest)
        report.degraded.append(
            "узлы кривой из разных дней: "
            + ", ".join(f"{tenor:g}y ({day})" for tenor, day in behind[:4])
            + f" при кривой {newest} — ответ источника неполный, кривая не принята как "
              "наблюдение")
        return
    if len(curve) < MIN_CURVE_NODES:
        report.degraded.append(
            f"кривая из {len(curve)} узлов при минимуме {MIN_CURVE_NODES} "
            "— не принята как наблюдение")
        return

    curve_date = max(days.values())
    age = _business_days(curve_date, today)
    if age > MAX_CURVE_AGE_BUSINESS_DAYS:
        report.degraded.append(
            f"кривая от {curve_date} старше {MAX_CURVE_AGE_BUSINESS_DAYS} рабочих дней "
            f"({age}) — не принята как наблюдение")
        return

    was = report.curve_reference
    reference_age = _reference_age(report.curve_reference_date, curve_date)
    if was and reference_age is not None and reference_age > MAX_CURVE_AGE_BUSINESS_DAYS:
        report.degraded.append(
            f"проверка сдвига узлов не выполнена: последняя годная кривая от "
            f"{report.curve_reference_date} старше "
            f"{MAX_CURVE_AGE_BUSINESS_DAYS} рабочих дней ({reference_age}), "
            "и «сдвиг за такт» на таком разрыве не определён")
        was = {}

    jumps = [f"{k}y {was[k]:.4f}→{curve[float(k)]:.4f}" for k in was
             if float(k) in curve and abs(curve[float(k)] - was[k]) > MAX_NODE_SHIFT]
    if jumps:
        report.degraded.append(
            f"узлы кривой сдвинулись больше {MAX_NODE_SHIFT * 100:.0f} п.п. "
            f"к последней годной от {report.curve_reference_date}: "
            + ", ".join(sorted(jumps)[:4]) + " — кривая не принята как наблюдение")
        return

    report.observed_curve = {f"{tenor:g}": value for tenor, value in sorted(curve.items())}
    report.observed_curve_date = curve_date.isoformat()
    report.curve_shift = curve_shift(book, curve)
    # Годная кривая — новый эталон. Только здесь: любой отказ выше оставляет
    # прежний, иначе проверка снимала бы себя на следующем такте.
    report.curve_reference = dict(report.observed_curve)
    report.curve_reference_date = report.observed_curve_date
    report.applied["curve_observed"] = (
        f"{len(curve)} узлов ЦБ на {curve_date} — наблюдение для диагностики; "
        "в оценку идёт кривая книги")


def _observe_ofz_in(store, report: LiveReport, today: dt.date) -> None:
    """Реальные доходности ОФЗ-ИН на ДАТУ КРИВОЙ — наблюдение для пересборки миров.

    Рецепт миров книги 1.4 строит инфляцию мира M по форвардным BEI: номинальные
    форварды кривой против реальных форвардов ОФЗ-ИН, и обе стороны обязаны
    быть одного дня (`worlds_recipe.py`, защита по дате; VERIFY листа worlds,
    §3.1: смешанный мир — «номинал одного дня / BEI другого» — вдвое
    преувеличивает шум даты). Поэтому берётся точка ряда, датированная ровно
    днём принятой кривой (сборщик датирует её днём сделки, а не днём съёма).

    **Ближайший общий торговый день не подбирается.** Если выпуск не торговался
    в день кривой, пара «кривая дня D / доходность дня D−1» — тот самый
    смешанный мир; подобрать общий день значит взять и кривую другого дня, а
    кривая выпуска — одна, сегодняшняя. Согласованную пару другого дня
    инструмент берёт явно (`--curve zcyc.json` и `--ofz-in <история ISS>`).

    **Деградация поштучная и тихая для оценки.** Каждый выпуск либо даёт
    доходность, либо причину в `observed_ofz_in_missing`; в `live.degraded`
    (плашка «часть входов не свежая» над каждым экраном) это не идёт:
    доходности ОФЗ-ИН в оценку выпуска не входят, и отсутствие сделки по
    малоликвидному линкеру не делает несвежим ни одно число панели. Отказ
    виден там, где он что-то меняет, — инструмент пересборки откажет с
    названием выпуска. Кривая не принята — нет и даты, с которой сверять.
    """
    from indicators.sources import OFZ_IN_SECIDS

    day = report.observed_curve_date
    yields: dict[str, float] = {}
    missing: dict[str, str] = {}
    for secid in OFZ_IN_SECIDS:
        series = store.load(OFZ_IN_PREFIX + secid)
        if not day:
            missing[secid] = "кривая дня не принята — дату доходности сверить не с чем"
            continue
        if not series or not series.points:
            missing[secid] = "ряд не собран"
            continue
        value = series.value_as_of(day, today.isoformat())
        if value is None:
            last = series.latest()
            missing[secid] = (f"нет сделки на дату кривой {day}"
                              + (f" (последняя точка — {last.period[:10]})" if last else ""))
            continue
        if not OFZ_IN_MIN <= value <= OFZ_IN_MAX:
            missing[secid] = (f"доходность {value:.4g} вне коридора {OFZ_IN_MIN:.1%}–{OFZ_IN_MAX:.0%} "
                              "— единицы или мусор")
            continue
        yields[secid] = value
    report.observed_ofz_in, report.observed_ofz_in_missing = yields, missing
    report.observed_ofz_in_date = day if yields else ""
    if yields:
        report.applied["ofz_in_observed"] = (
            f"{len(yields)} из {len(OFZ_IN_SECIDS)} выпусков ОФЗ-ИН на {day} — наблюдение "
            "для пересборки миров; в оценку не идёт")


def _observe_security(store, report: LiveReport) -> None:
    """Карточка акции (D15): уровень листинга и объём выпуска — НАБЛЮДЕНИЕ.

    Сборщик `moex_security` пишет ряды раз в сутки; смены считает сам слой
    индикаторов (`indicators.sources.security_changes`: последнее значение
    против предыдущего другого, с днём, с которого действует новое). Ряда нет
    (первый запуск, сборщик не отработал) — пустая карточка, а не отказ:
    второстепенный вход выпуск не останавливает.
    """
    from indicators.sources import SECURITY_FIELDS, security_changes, security_series_id

    card: dict = {}
    for name in SECURITY_FIELDS:
        point = _latest(store, security_series_id(name))
        if point is not None and point.value is not None:
            card[name.lower()] = float(point.value)
            card["as_of"] = max(card.get("as_of", ""), point.period[:10])
    if card:
        card["changes"] = security_changes(store)
    report.security = card

