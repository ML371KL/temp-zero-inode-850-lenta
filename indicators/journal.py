"""Журнал прогнозов: append-only, с хэшем входов и версией уравнения. Кварталы Ленты.

Дисциплина перенесена из 850oa (там полугодия): каждый прогноз фиксируется
**до** выхода факта, после факта записывается ошибка против наивных эталонов.
У Ленты отчётность квартальная (D2, D15), поэтому событие журнала — квартальный
отчёт, горизонты короче, а правило допуска считает кварталы:

* горизонты 90 / 45 / 15 дней до публикации, в зачёт идёт **45** — почти весь
  квартал прожит, отчётов аналогов за него ещё нет (study/04 §10.3);
* квартал, чей предыдущий квартал отчитывается ПОЗЖЕ момента зачёта (1 кв.: маржа
  4 кв. выходит годовым отчётом ≈26.03, а 45 дней до отчёта 1 кв. — ≈15.03),
  прогноза на 45 дней иметь не может — слой переходит на квартал только после
  факта предыдущего. В зачёт у него идёт ПЕРВЫЙ прогноз после этого факта, на
  фактическом горизонте (≈34 дня; решение владельца 30.09.2026, вариант B2):
  модель и эталоны видят факт предыдущего квартала на одну дату;
* допуск к правилу A-P2u — решение владельца, не раньше чем после
  `ADMISSION_MIN_EVENTS` = 8 отчётных кварталов вне выборки и при отношении MSE
  к лучшему эталону (включая ожидание модели без индикаторов) не больше 0,8;
  понижение до справочного — если на последних `DEMOTION_WINDOW` = 4 отчётах
  отношение больше 1,0;
* события, чей главный эталон сломан разрывом периметра (`indicators.perimeter`:
  «О'КЕЙ» нет в базе прошлого года для 2026Q3…2027Q2 и т. п.), в счёт допуска
  НЕ идут — эталон без приобретённой сети смещён.

Правила, каждое — против конкретной ошибки предыдущих версий журнала (850oa).

**Версия = версия СПЕЦИФИКАЦИИ уравнения, а не хэш файлов.** Хэш шести файлов
дал четыре версии за две недели, и «одобрить» предиктор стало бы нельзя раньше
2030 года.

**Запись неизменяема — и это обеспечено базой, а не обещанием.** Правку и
удаление запрещают триггеры SQLite.

**Запись только при изменении значения.** Дубль не добавляет знания, но
превращает одно событие табло в пять.

**Прогноз нельзя записать после выхода факта.** Жёсткий сторож — факт за
период уже внесён; мягкий — крайний день окна отчёта прошёл, а факт не внесён
(внести забыли, и дальше писать прогнозы нечестно). Окно — из календаря
(`indicators.calendar.report_dates`), иначе по правилу лагов.

**Одно событие — один отчёт** (`periods.report_id`): 2 кв. и 1П — один релиз;
4 кв., 2П и год — два события (выручка в феврале, маржа в марте). Из периодов
одного отчёта в зачёт идёт КВАРТАЛ: именно его прогнозирует слой.

**Наивные эталоны обязательны и честны.** Квартальная маржа Ленты шумная
(RMSE лучшего эталона на истории ≈1,5 п.п. против 0,6 у полугодовой маржи
Магнита), поэтому главный эталон сезонный: «тот же квартал год назад + сдвиг
прошлого квартала г/г». Обязательный эталон маржи — ожидание модели без
индикаторов: уравнение, которое к приору книги ничего не прибавляет, отношения
MSE ниже 1,0 не получает.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from indicators import calendar, issuer, perimeter, periods, store
from indicators.periods import FIRST_FORECAST_QUARTER, GIVES_MARGIN, GIVES_REVENUE

# Журнал живёт рядом с рядами: на проде — в StateDirectory, локально — в репозитории.
# Один корень на оба, иначе прогноз записывается в одно место, а сверяется с другим.
DEFAULT_PATH = store.DEFAULT_ROOT / "journal.sqlite"

SCHEMA_VERSION = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS forecasts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    made_at     TEXT NOT NULL,
    target      TEXT NOT NULL,
    period      TEXT NOT NULL,
    value       REAL NOT NULL,
    std_error   REAL,
    equation    TEXT NOT NULL,
    version     TEXT NOT NULL,
    inputs_sha  TEXT NOT NULL,
    inputs      TEXT NOT NULL,
    note        TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS actuals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    target      TEXT NOT NULL,
    period      TEXT NOT NULL,
    value       REAL NOT NULL,
    reported_on TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    source      TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS naive (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    made_at     TEXT NOT NULL,
    target      TEXT NOT NULL,
    period      TEXT NOT NULL,
    method      TEXT NOT NULL,
    value       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_naive_target ON naive(target, period, method);
CREATE INDEX IF NOT EXISTS ix_forecasts_target ON forecasts(target, period);
CREATE INDEX IF NOT EXISTS ix_actuals_target ON actuals(target, period);
"""

# Правка и удаление запрещены на уровне базы. Исправление — только новая
# запись с новым временем.
TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS forecasts_no_update BEFORE UPDATE ON forecasts
    BEGIN SELECT RAISE(ABORT, 'журнал неизменяем: правка прогноза запрещена'); END;
CREATE TRIGGER IF NOT EXISTS forecasts_no_delete BEFORE DELETE ON forecasts
    BEGIN SELECT RAISE(ABORT, 'журнал неизменяем: удаление прогноза запрещено'); END;
CREATE TRIGGER IF NOT EXISTS actuals_no_update BEFORE UPDATE ON actuals
    BEGIN SELECT RAISE(ABORT, 'журнал неизменяем: правка факта запрещена'); END;
CREATE TRIGGER IF NOT EXISTS actuals_no_delete BEFORE DELETE ON actuals
    BEGIN SELECT RAISE(ABORT, 'журнал неизменяем: удаление факта запрещено'); END;
CREATE TRIGGER IF NOT EXISTS naive_no_update BEFORE UPDATE ON naive
    BEGIN SELECT RAISE(ABORT, 'журнал неизменяем: правка эталона запрещена'); END;
CREATE TRIGGER IF NOT EXISTS naive_no_delete BEFORE DELETE ON naive
    BEGIN SELECT RAISE(ABORT, 'журнал неизменяем: удаление эталона запрещено'); END;
"""

MARGIN_TARGET = issuer.series("ebitda_margin_pre16")
REVENUE_TARGET = issuer.series("revenue_pre16")
INTEREST_TARGET = issuer.series("net_interest")

# Горизонты от публикации: 90 дней — ≈36-й день квартала (сразу после прошлого
# релиза), 45 — за ≈11 дней до конца квартала (в зачёт), 15 — ≈20 дней после
# конца квартала (справочно). Для годового МСФО с лагом ≈85 дней горизонты
# отсчитываются от его собственной даты, как и у всех.
HORIZONS_DAYS = (90, 45, 15)
SCORING_HORIZON_DAYS = 45
# Как выбран зачётный прогноз: на горизонте `SCORING_HORIZON_DAYS` или первым
# после факта предыдущего периода, если тот вышел позже момента зачёта.
SCORE_AT_HORIZON = "horizon"
SCORE_AFTER_PREVIOUS_FACT = "after_previous_fact"

MODEL_EXPECTATION = "model_expectation"
GUIDANCE = "guidance"

# ГЛАВНЫЙ эталон величины — ровно один. Остальные считаются и печатаются
# справочно; зачёт «обогнал / не обогнал» идёт против главного, а правило
# допуска — против ЛУЧШЕГО из всех.
MAIN_BENCHMARK = {
    MARGIN_TARGET: "yoy_plus_shift",
    REVENUE_TARGET: "yoy_growth_carried",
    INTEREST_TARGET: "previous_period_scaled_by_rates",
}

# Эталоны из ЗАКРЫТЫХ периодов замораживаются при первой записи: переписанный
# задним числом эталон превращает зачёт в подгонку. Ожидание модели и гайденс
# — не из закрытых периодов (книга, заявление компании), они пишутся рядом во
# времени и сравниваются на том же горизонте, что и прогноз.
FROZEN_BENCHMARKS = frozenset({MARGIN_TARGET, REVENUE_TARGET})
UPDATING_BENCHMARKS = frozenset({MODEL_EXPECTATION, GUIDANCE})
REQUIRED_BENCHMARKS = {MARGIN_TARGET: MODEL_EXPECTATION}

# ПРАВИЛО ДОПУСКА (D15). Числа — решение владельца, не подгонка. Симуляция
# study/04 §5.3 (ошибки нормальны, корреляция с эталоном 0,5 / 0,9, 100 000
# розыгрышей): при n = 8 уравнение без умения проходит порог 0,8 в 25–36 %
# случаев, на 20 % точнее эталона — в 64–75 %; против обязательного эталона
# «ожидание модели» пустое уравнение не проходит никогда. Квартальные ошибки
# автокоррелированы (сделки, режимы): эффективное n меньше восьми.
ADMISSION_MIN_EVENTS = 8
ADMISSION_MAX_MSE_RATIO = 0.8
DEMOTION_WINDOW = 4
DEMOTION_MSE_RATIO = 1.0

BENCHMARK_TITLES = {
    "yoy_plus_shift": "тот же квартал год назад + сдвиг прошлого квартала г/г",
    "seasonal_naive": "тот же квартал год назад (сезонный наивный)",
    "last_period": "прошлый квартал",
    "mean_of_year": "среднее четырёх последних кварталов",
    "yoy_growth_carried": "тот же квартал год назад × рост прошлого квартала г/г",
    "previous_period_scaled_by_rates": "прошлый квартал × отношение средних ставок",
    MODEL_EXPECTATION: "ожидание модели без индикаторов",
    GUIDANCE: "гайденс компании (маржа за год → маржа 2П и её кварталов)",
}

# Изменение меньше тысячной доли собственной ошибки — не новый прогноз.
SAME_VALUE_FRACTION_OF_SE = 1e-3


class JournalError(RuntimeError):
    """Нарушение дисциплины журнала (а не техническая ошибка базы)."""


@dataclass(frozen=True)
class Forecast:
    made_at: str
    target: str
    period: str
    value: float
    std_error: float | None
    equation: str
    version: str
    inputs_sha: str
    note: str = ""
    # Ложь означает «такой прогноз уже был, новая строка не создавалась».
    recorded: bool = True


@dataclass(frozen=True)
class Actual:
    target: str
    period: str
    value: float
    reported_on: date
    recorded_at: str
    source: str = ""


def target_gives(target: str) -> str:
    """Что даёт отчёт для величины: выручку или маржу/прочее (выбор события 4 кв.)."""
    return GIVES_REVENUE if target == REVENUE_TARGET or target.endswith(".revenue_pre16") \
        else GIVES_MARGIN


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def current_quarter(today: date | None = None) -> str:
    """Квартал КАЛЕНДАРЯ. Не то же, что период прогноза, — см. `forecast_period`."""
    return periods.quarter_of(today or _utc_today())


def report_date(period: str, gives: str = GIVES_MARGIN) -> date | None:
    """ОЖИДАЕМАЯ дата выхода факта за период (центр окна календаря).

    `gives` — «revenue» или «margin»: у 4 квартала два события. Можно передать
    и имя величины (`lenta.revenue_pre16`) — событие выберется по нему.
    """
    dates = calendar.report_dates(period, _gives(gives))
    return dates[0] if dates else None


def report_deadline(period: str, gives: str = GIVES_MARGIN) -> date | None:
    """Крайний день окна отчёта: после него факт обязан быть внесён."""
    dates = calendar.report_dates(period, _gives(gives))
    return dates[1] if dates else None


def _gives(value: str) -> str:
    return value if value in (GIVES_MARGIN, GIVES_REVENUE) else target_gives(value)


def forecast_period(journal: "Journal", target: str, *, today: date | None = None,
                    floor: str = FIRST_FORECAST_QUARTER) -> str:
    """Самый ранний КВАРТАЛ, факт которого ещё не внесён.

    Период закрывается не календарём, а ФАКТОМ (`collect record-actual`): с
    1 октября календарь перевёл бы слой на 4 кв., хотя отчёт за 3 кв. выходит
    в конце октября, и прогнозы на горизонтах 15 дней до него совпали бы с
    прогнозом от 30 сентября. Ограничение сверху — календарный квартал:
    забегать вперёд за квартал, который ещё не начался, слой не должен.
    """
    limit = current_quarter(today)
    period = floor
    while periods.index(period) <= periods.index(limit):
        if journal.actual(target, period) is None:
            return period
        period = periods.next_period(period)
    return limit


def expected_scoring(period: str, target: str = "") -> tuple[str, int]:
    """Как период пойдёт в зачёт ПО КАЛЕНДАРЮ отчётов: (правило, горизонт в днях).

    `forecast_period` переходит на квартал только после ФАКТА предыдущего. Если
    отчёт предыдущего периода ожидается позже, чем за `SCORING_HORIZON_DAYS` до
    отчёта самого периода, прогноза на горизонте зачёта не будет, и в зачёт
    пойдёт первый прогноз после факта предыдущего — на горизонте «от отчёта до
    отчёта» (у 1 кв. ≈34 дня: годовой отчёт ≈26.03, отчёт 1 кв. ≈29.04). Иначе —
    обычный горизонт. То же правило, что применяет `Journal.scored` к внесённым
    фактам; здесь — для календаря допуска вперёд.
    """
    gives = target_gives(target) if target else GIVES_MARGIN
    own = report_date(period, gives)
    before = report_date(periods.previous_period(period), gives) if own else None
    if own is None or before is None or before <= own - timedelta(days=SCORING_HORIZON_DAYS):
        return SCORE_AT_HORIZON, SCORING_HORIZON_DAYS
    return SCORE_AFTER_PREVIOUS_FACT, (own - before).days


class Journal:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or DEFAULT_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db:
            _migrate(db)
            db.executescript(SCHEMA)
            db.executescript(TRIGGERS)
            db.execute("PRAGMA user_version = " + str(SCHEMA_VERSION))
            db.commit()

    # ------------------------------------------------------------ запись

    def record(self, *, target: str, period: str, value: float, equation: str,
               version: str, inputs: dict, std_error: float | None = None,
               note: str = "", today: date | None = None) -> Forecast:
        """Фиксирует прогноз, если он изменился и факт ещё не вышел."""
        today = today or _utc_today()
        self._refuse_if_fact_is_out(target, period, today)

        previous = self.latest_forecast(target, period, equation=equation, version=version)
        if previous is not None and _same_value(previous.value, value, std_error):
            return Forecast(previous.made_at, target, period, previous.value, previous.std_error,
                            equation, version, previous.inputs_sha, previous.note, recorded=False)

        payload = json.dumps(inputs, ensure_ascii=False, sort_keys=True)
        sha = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        made_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with closing(sqlite3.connect(self.path)) as db:
            db.execute(
                "INSERT INTO forecasts (made_at, target, period, value, std_error, equation,"
                " version, inputs_sha, inputs, note) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (made_at, target, period, value, std_error, equation, version, sha, payload, note))
            db.commit()
        return Forecast(made_at, target, period, value, std_error, equation, version, sha, note)

    def _refuse_if_fact_is_out(self, target: str, period: str, today: date) -> None:
        if self.actual(target, period) is not None:
            raise JournalError(
                target + " " + period + ": факт уже внесён — "
                "прогноз задним числом не записывается")
        deadline = report_deadline(period, target_gives(target))
        if deadline is not None and today > deadline:
            raise JournalError(
                target + " " + period + ": окно отчёта закрылось " + deadline.isoformat() +
                " (плановая дата отчёта прошла), а факт не внесён. "
                "Сначала `record-actual`, потом прогнозы")

    def record_naive(self, target: str, period: str, values: dict[str, float]) -> int:
        """Наивные эталоны фиксируются ВМЕСТЕ с прогнозом, а не после факта.

        Эталоны из закрытых периодов (`FROZEN_BENCHMARKS`) замораживаются при
        первой записи: попытка записать другое значение — отказ. Если
        изменилась ИСТОРИЯ (датабук переписал прошлое) — это новое событие
        книги, а не новая версия эталона. Ожидание модели и гайденс
        (`UPDATING_BENCHMARKS`) — ряды во времени.

        Возвращает число новых строк: неизменившийся эталон не пишется.
        """
        made_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        existing = self.naive(target, period)
        written = 0
        with closing(sqlite3.connect(self.path)) as db:
            for method, value in values.items():
                if value is None:
                    continue
                was = existing.get(method)
                if was is not None and _same_value(was, value, None):
                    continue
                frozen = target in FROZEN_BENCHMARKS and method not in UPDATING_BENCHMARKS
                if was is not None and frozen:
                    raise JournalError(
                        f"эталон {target}/{period}/{method} заморожен на {was!r}, "
                        f"а записывается {value!r}. Эталон из закрытых периодов "
                        "уточняться не может; переписанный эталон превращает зачёт в "
                        "подгонку. Если изменилась ИСТОРИЯ — это новое событие книги, "
                        "а не новая версия эталона")
                db.execute("INSERT INTO naive (made_at, target, period, method, value)"
                           " VALUES (?,?,?,?,?)", (made_at, target, period, method, value))
                written += 1
            db.commit()
        return written

    def record_actual(self, target: str, period: str, value: float, source: str = "",
                      reported_on: date | None = None) -> Actual:
        """Вносит факт. `reported_on` — день ПУБЛИКАЦИИ, а не день внесения.

        Исправление — новой строкой (правка и удаление запрещены триггерами):
        действует последняя. Строка не пишется, только если совпали И число, И
        день публикации: от дня публикации меряются горизонты зачёта, и
        исправить его при том же числе должно быть можно (внешний аудит
        30.09.2026, T12 — прежде такое исправление молча возвращало старую
        строку). Без `reported_on` день берётся из прежней записи периода
        (исправление числа не сдвигает день публикации на день внесения), а у
        первой записи — сегодня.
        """
        recorded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        previous = self.actual(target, period)
        if reported_on is None:
            reported_on = previous.reported_on if previous is not None else _utc_today()
        if (previous is not None and _same_value(previous.value, value, None)
                and previous.reported_on == reported_on):
            return previous
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("INSERT INTO actuals (target, period, value, reported_on, recorded_at,"
                       " source) VALUES (?,?,?,?,?,?)",
                       (target, period, value, reported_on.isoformat(), recorded_at, source))
            db.commit()
        return Actual(target, period, value, reported_on, recorded_at, source)

    # ------------------------------------------------------------ чтение

    def forecasts(self, target: str | None = None, period: str | None = None) -> list[Forecast]:
        query = ("SELECT made_at, target, period, value, std_error, equation, version,"
                 " inputs_sha, note FROM forecasts")
        where, args = [], []
        if target:
            where.append("target = ?")
            args.append(target)
        if period:
            where.append("period = ?")
            args.append(period)
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY made_at, id"
        with closing(sqlite3.connect(self.path)) as db:
            return [Forecast(*row) for row in db.execute(query, tuple(args))]

    def latest_forecast(self, target: str, period: str, *, equation: str | None = None,
                        version: str | None = None) -> Forecast | None:
        rows = [f for f in self.forecasts(target, period)
                if (equation is None or f.equation == equation)
                and (version is None or f.version == version)]
        return rows[-1] if rows else None

    def forecast_as_of(self, target: str, period: str, moment: date) -> Forecast | None:
        """Последний прогноз, сделанный НЕ ПОЗЖЕ указанного дня."""
        rows = [f for f in self.forecasts(target, period)
                if date.fromisoformat(f.made_at[:10]) <= moment]
        return rows[-1] if rows else None

    def actual(self, target: str, period: str) -> Actual | None:
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute(
                "SELECT target, period, value, reported_on, recorded_at, source FROM actuals"
                " WHERE target = ? AND period = ? ORDER BY id DESC LIMIT 1",
                (target, period)).fetchone()
        if not row:
            return None
        return Actual(row[0], row[1], row[2], date.fromisoformat(row[3]), row[4], row[5])

    def naive(self, target: str, period: str) -> dict[str, float]:
        """Последнее значение каждого эталона."""
        return self.naive_as_of(target, period, None)

    def naive_as_of(self, target: str, period: str,
                    moment: date | None) -> dict[str, float]:
        """Эталоны, какими они были НЕ ПОЗЖЕ указанного дня."""
        query = "SELECT method, value, made_at FROM naive WHERE target = ? AND period = ?"
        args: list = [target, period]
        if moment is not None:
            query += " AND made_at <= ?"
            args.append(moment.isoformat() + "T99")  # весь этот день включительно
        query += " ORDER BY made_at, id"
        out: dict[str, float] = {}
        with closing(sqlite3.connect(self.path)) as db:
            for method, value, _ in db.execute(query, tuple(args)):
                out[method] = value
        return out

    def horizons(self, target: str, period: str) -> dict[int, dict | None]:
        """Что уравнение говорило за 90 / 45 / 15 дней до публикации факта.

        `None` на горизонте — «на тот момент прогноза не существовало», и
        такое событие не идёт в зачёт. Эталон берётся на ТОТ ЖЕ момент, что и
        прогноз: эталоны-ряды (ожидание модели, гайденс, проценты) уточняются
        во времени, и сравнение на разных множествах информации мерило бы
        возраст, а не качество.
        """
        actual = self.actual(target, period)
        if actual is None:
            return {}
        out: dict[int, dict | None] = {}
        for days in HORIZONS_DAYS:
            moment = actual.reported_on - timedelta(days=days)
            forecast = self.forecast_as_of(target, period, moment)
            out[days] = (None if forecast is None
                         else self._against_the_fact(target, period, actual, forecast, moment))
        return out

    def _against_the_fact(self, target: str, period: str, actual: Actual, forecast: Forecast,
                          moment: date) -> dict:
        """Прогноз против факта и главный эталон, каким он был на день `moment`."""
        main = MAIN_BENCHMARK.get(target)
        benchmark = self.naive_as_of(target, period, moment).get(main) if main else None
        item = dict(
            made_at=forecast.made_at, value=forecast.value, std_error=forecast.std_error,
            version=forecast.version, equation=forecast.equation,
            error=forecast.value - actual.value,
            abs_error=abs(forecast.value - actual.value),
            benchmark=main if benchmark is not None else None,
            benchmark_value=benchmark,
            benchmark_abs_error=(None if benchmark is None
                                 else abs(benchmark - actual.value)))
        item["beats_benchmark"] = (None if benchmark is None else
                                   item["abs_error"] < item["benchmark_abs_error"])
        return item

    def scored(self, target: str, period: str) -> dict | None:
        """Прогноз, который идёт в ЗАЧЁТ, эталон на тот же момент и горизонт.

        Обычно — прогноз на `SCORING_HORIZON_DAYS` до публикации факта
        (`rule: horizon`). Если на тот момент прогноза не было, а факт
        предыдущего периода вышел позже него, раньше прогноз появиться и не мог:
        слой переходит на период только после факта предыдущего
        (`forecast_period`). Тогда в зачёт идёт ПЕРВЫЙ прогноз периода,
        сделанный до дня публикации его факта (`rule: after_previous_fact`), на
        фактическом горизонте — днях от этого прогноза до публикации. Эталоны
        берутся на день того же прогноза: модель и эталоны видят факт
        предыдущего периода на одну дату. `None` — в зачёт идти нечему.

        К словарю `_against_the_fact` добавлены `rule`, `moment` (день, на
        который взяты прогноз и эталоны), `horizon_days` и, для второго
        правила, `previous_period` и `previous_reported_on`.
        """
        actual = self.actual(target, period)
        if actual is None:
            return None
        nominal = actual.reported_on - timedelta(days=SCORING_HORIZON_DAYS)
        forecast = self.forecast_as_of(target, period, nominal)
        if forecast is not None:
            item = self._against_the_fact(target, period, actual, forecast, nominal)
            item.update(rule=SCORE_AT_HORIZON, moment=nominal.isoformat(),
                        horizon_days=SCORING_HORIZON_DAYS)
            return item
        if not periods.is_period(period):
            return None
        before = periods.previous_period(period)
        previous = self.actual(target, before)
        if previous is None or previous.reported_on <= nominal:
            return None
        first = next((f for f in self.forecasts(target, period)
                      if date.fromisoformat(f.made_at[:10]) < actual.reported_on), None)
        if first is None:
            return None
        moment = date.fromisoformat(first.made_at[:10])
        item = self._against_the_fact(target, period, actual, first, moment)
        item.update(rule=SCORE_AFTER_PREVIOUS_FACT, moment=moment.isoformat(),
                    horizon_days=(actual.reported_on - moment).days,
                    previous_period=before,
                    previous_reported_on=previous.reported_on.isoformat())
        return item

    def events(self, target: str) -> list[str]:
        """СОБЫТИЯ — отчёты, а не периоды. Один отчёт даёт одно событие.

        Группировка — по `periods.report_id(period, gives)`, а не по совпадению
        `reported_on`: годовой отчёт вносится и уточняется неделями, и два
        периода одного раскрытия легко получают разные даты. Из периодов
        одного отчёта в зачёт идёт квартал, затем полугодие, затем год.
        """
        # День публикации — из ПОСЛЕДНЕЙ строки периода, как у `actual`: исправленный
        # день (T12) двигает и порядок событий, а не только горизонты.
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute(
                "SELECT a.period, a.reported_on FROM actuals a JOIN (SELECT MAX(id) AS id"
                " FROM actuals WHERE target = ? GROUP BY period) last ON a.id = last.id"
                " ORDER BY a.reported_on, a.period", (target,)).fetchall()
        gives = target_gives(target)
        by_report: dict[str, list[tuple[str, str]]] = {}
        for period, reported_on in rows:
            key = periods.report_id(period, gives) or "период:" + period
            by_report.setdefault(key, []).append((period, reported_on))
        rank = {periods.QUARTER: 0, periods.HALF: 1, periods.YEAR: 2}
        out = []
        for key in sorted(by_report, key=lambda k: min(r for _, r in by_report[k])):
            names = [p for p, _ in by_report[key]]
            parsed = sorted(names, key=lambda p: (rank.get(periods.kind(p), 3)
                                                  if periods.is_period(p) else 9, p))
            out.append(parsed[0])
        return out

    def scoreboard(self, target: str) -> list[dict]:
        """«Прогноз против факта»: ОДНА строка на событие, а не на запись.

        В зачёт идёт прогноз `scored`: на горизонте `SCORING_HORIZON_DAYS`, а у
        периода, чей предыдущий отчитался позже, — первый после его факта
        (`scoring_rule`, фактический горизонт — `scoring_horizon_days`, причина
        — в `note`). Рядом — остальные горизонты, последнее слово перед отчётом
        и разрывы периметра главного эталона (`perimeter_breaks`): событие со
        сломанным главным эталоном печатается, но в счёт допуска не идёт
        (`admission_excluded`).
        """
        out = []
        for period in self.events(target):
            actual = self.actual(target, period)
            horizons = self.horizons(target, period)
            scored = self.scored(target, period)
            last = self.forecast_as_of(target, period, actual.reported_on)
            main = MAIN_BENCHMARK.get(target)
            broken = perimeter.broken_by(period, main) if main else []
            row = dict(
                period=period, target=target,
                actual=actual.value, reported_on=actual.reported_on.isoformat(),
                scoring_horizon_days=(SCORING_HORIZON_DAYS if scored is None
                                      else scored["horizon_days"]),
                scoring_rule=None if scored is None else scored["rule"],
                horizons={str(k): v for k, v in horizons.items()},
                last_word=None if last is None else dict(
                    made_at=last.made_at, value=last.value, version=last.version,
                    error=last.value - actual.value, abs_error=abs(last.value - actual.value)),
                perimeter_breaks=[b.id for b in broken],
                admission_excluded=bool(broken),
            )
            if scored is None:
                row.update(made_at=None, equation=None, version=None, forecast=None,
                           error=None, abs_error=None, beats_benchmark=None,
                           note=self._unscored_reason(target, period, actual))
            else:
                row.update(made_at=scored["made_at"], equation=scored["equation"],
                           version=scored["version"], forecast=scored["value"],
                           error=scored["error"], abs_error=scored["abs_error"], note="")
                if scored["rule"] == SCORE_AFTER_PREVIOUS_FACT:
                    row["note"] = (
                        f"зачёт на фактическом горизонте {scored['horizon_days']} дн: факт за "
                        f"{scored['previous_period']} вышел {scored['previous_reported_on']} — "
                        f"позже чем за {SCORING_HORIZON_DAYS} дней до отчёта, в зачёт идёт "
                        "первый прогноз после него")
            if broken:
                row["note"] = ((row["note"] + "; ") if row["note"] else "") + (
                    "разрыв периметра главного эталона (" + ", ".join(b.name for b in broken)
                    + ") — в счёт допуска не идёт")
            at = None if scored is None else date.fromisoformat(scored["moment"])
            benchmarks = self.naive_as_of(target, period, at)
            if main is None and len(benchmarks) == 1:
                main = next(iter(benchmarks))
            row["main_benchmark"] = main
            for method, value in benchmarks.items():
                row["naive_" + method + "_error"] = abs(value - actual.value)
                row["naive_" + method] = value
            main_error = row.get("naive_" + main + "_error") if main else None
            if scored is None or main_error is None:
                row["beats_benchmark"] = None
            else:
                row["beats_benchmark"] = row["abs_error"] < main_error
            out.append(row)
        return out

    def _unscored_reason(self, target: str, period: str, actual: Actual) -> str:
        """Почему у события нет зачётного прогноза — словами для табло."""
        reason = "прогноза за %d дней до отчёта не было" % SCORING_HORIZON_DAYS
        rows = self.forecasts(target, period)
        if not rows:
            return reason
        reason += f": первый записан {rows[0].made_at[:10]}"
        previous = (self.actual(target, periods.previous_period(period))
                    if periods.is_period(period) else None)
        if previous is not None:
            reason += (f", факт за {previous.period} (опубликован "
                       f"{previous.reported_on.isoformat()}) внесён {previous.recorded_at[:10]}")
        return reason

    def admission(self, target: str, *, version: str | None = None,
                  min_events: int = ADMISSION_MIN_EVENTS) -> "Admission":
        """Статус допуска уравнения к правилу A-P2u.

        Считаются СОБЫТИЯ с зачётным прогнозом (`scored`) и эталонами на тот
        же момент, без разрыва периметра главного эталона; с `version` —
        только отчёты, где зачтённый прогноз сделан этой версией уравнения.
        """
        rows = [r for r in self.scoreboard(target)
                if r.get("forecast") is not None and not r.get("admission_excluded")
                and (version is None or r.get("version") == version)]
        return admission_from_rows(rows, target=target, version=version,
                                   min_events=min_events)

    def demoted(self, target: str, min_events: int = ADMISSION_MIN_EVENTS) -> bool:
        """Понижено ли уравнение до справочного по правилу допуска."""
        return self.admission(target, min_events=min_events).demoted

    def recent(self, per_target: int = 8) -> list[Forecast]:
        """Последние ИЗМЕНЕНИЯ по каждой величине — то, что уходит в выпуск."""
        grouped: dict[tuple[str, str], list[Forecast]] = {}
        for f in self.forecasts():
            grouped.setdefault((f.target, f.period), []).append(f)
        out: list[Forecast] = []
        for rows in grouped.values():
            changes = [row for index, row in enumerate(rows)
                       if index == 0 or not _same_value(rows[index - 1].value,
                                                        row.value, row.std_error)]
            out.extend(changes[-per_target:])
        return sorted(out, key=lambda f: (f.made_at, f.target, f.period))


def _same_value(old: float, new: float, std_error: float | None) -> bool:
    if old == new:
        return True
    tolerance = abs(std_error or 0.0) * SAME_VALUE_FRACTION_OF_SE
    return abs(new - old) <= tolerance


# ------------------------------------------------------------ правило допуска


@dataclass(frozen=True)
class Admission:
    """Статус уравнения по правилу допуска — для выпуска и экрана.

    `admitted` — уравнение ИМЕЕТ ПРАВО быть поданным в правило A-P2u, если так
    решит владелец. Автоматического подключения нет: к цене нау-каст не
    подключён (D15).
    """

    target: str
    version: str | None
    status: str
    """Машинный код: `accumulating` / `admitted` / `not_admitted` / `demoted`."""
    title: str
    admitted: bool
    demoted: bool
    events: int
    """Отчётных кварталов вне выборки, зачтённых этой версии (без разрывов периметра)."""
    events_needed: int
    mse_ratio: float | None
    best_benchmark: str | None
    recent_mse_ratio: float | None
    """То же на последних `DEMOTION_WINDOW` отчётах — основание понижения."""
    recent_best_benchmark: str | None
    reason: str
    rule: str

    def as_dict(self) -> dict:
        from dataclasses import asdict

        return asdict(self)


def admission_rule_text(min_events: int = ADMISSION_MIN_EVENTS, target: str = "") -> str:
    """Правило одной строкой — из констант, а не пересказом."""
    required = REQUIRED_BENCHMARKS.get(target)
    among = (f", включая {BENCHMARK_TITLES[required]}," if required else "")
    return ("допуск к правилу A-P2u — решение владельца, не раньше чем после "
            f"{min_events} отчётных кварталов вне выборки (без кварталов с разрывом "
            f"периметра главного эталона) и при отношении MSE уравнения к лучшему "
            f"эталону{among} не больше {_ru(ADMISSION_MAX_MSE_RATIO)}; понижение до "
            f"справочного — если на последних {DEMOTION_WINDOW} отчётах отношение "
            f"больше {_ru(DEMOTION_MSE_RATIO)}; в зачёт идёт прогноз за "
            f"{SCORING_HORIZON_DAYS} дней до отчёта, а у квартала, чей предыдущий квартал "
            "отчитался позже этого момента (1 кв.: маржа 4 кв. выходит годовым отчётом), — "
            "первый прогноз после факта предыдущего, на фактическом горизонте")


def _ru(value: float, digits: int = 1) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def _benchmark_methods(row: dict) -> set[str]:
    """Эталоны, значения которых есть в строке табло."""
    return {key[len("naive_"):] for key in row
            if key.startswith("naive_") and not key.endswith("_error")
            and row[key] is not None}


def mse_against_best(rows: list[dict]) -> tuple[float | None, str | None]:
    """(MSE прогноза / MSE лучшего эталона, имя эталона) на ОДНИХ И ТЕХ ЖЕ отчётах.

    Лучший — эталон с наименьшим MSE среди тех, что есть во ВСЕХ строках. Нет
    общего эталона — (None, None). Эталон без ошибки при ненулевой ошибке
    прогноза — None, а не бесконечность; 0/0 — 1,0 (выигрыша нет).
    """
    if not rows:
        return None, None
    common = set.intersection(*(_benchmark_methods(r) for r in rows))
    if not common:
        return None, None
    mse_forecast = sum((r["forecast"] - r["actual"]) ** 2 for r in rows) / len(rows)
    by_method = {m: sum((r["naive_" + m] - r["actual"]) ** 2 for r in rows) / len(rows)
                 for m in common}
    best = min(sorted(by_method), key=by_method.get)
    if by_method[best] == 0.0:
        return (1.0 if mse_forecast == 0.0 else None), best
    return mse_forecast / by_method[best], best


def admission_from_rows(rows: list[dict], *, target: str = "", version: str | None = None,
                        min_events: int = ADMISSION_MIN_EVENTS) -> Admission:
    """Правило допуска по строкам табло (`Journal.scoreboard`).

    * отчётов меньше `min_events` — «копит отчёты», ни допуска, ни понижения;
    * отношение MSE к лучшему эталону на ПОСЛЕДНИХ `DEMOTION_WINDOW` отчётах
      больше `DEMOTION_MSE_RATIO` — «понижено» (понижение сильнее допуска);
    * отношение на ВСЕХ зачтённых отчётах не больше `ADMISSION_MAX_MSE_RATIO`
      — «допущено» к решению владельца;
    * иначе — «не допущено».
    """
    required = REQUIRED_BENCHMARKS.get(target)
    usable = [r for r in rows if r.get("forecast") is not None
              and r.get("actual") is not None and _benchmark_methods(r)
              and not r.get("admission_excluded")
              and (required is None or required in _benchmark_methods(r))]
    n = len(usable)
    rule = admission_rule_text(min_events, target)
    ratio, best = mse_against_best(usable)
    window = min(DEMOTION_WINDOW, min_events)
    recent, recent_best = mse_against_best(usable[-window:])
    common = dict(target=target, version=version, events=n,
                  events_needed=max(0, min_events - n),
                  mse_ratio=ratio, best_benchmark=best,
                  recent_mse_ratio=recent if n >= min_events else None,
                  recent_best_benchmark=recent_best if n >= min_events else None,
                  rule=rule)
    label = f" (версия {version})" if version else ""

    def title_of(method):
        return BENCHMARK_TITLES.get(method, method or "—")

    if n < min_events:
        left = min_events - n
        return Admission(
            status="accumulating", title="копит отчёты", admitted=False, demoted=False,
            reason=(f"зачтено {n} из {min_events} отчётов вне выборки{label}; "
                    f"до решения о допуске — ещё {left} {_reports_word(left)}"),
            **common)
    if recent_best is None:
        return Admission(
            status="not_admitted", title="не допущено", admitted=False, demoted=False,
            reason="нет эталона, общего для всех зачтённых отчётов: сравнивать не с чем",
            **common)
    if recent is None or recent > DEMOTION_MSE_RATIO:
        shown = "не определено" if recent is None else _ru(recent, 2)
        return Admission(
            status="demoted", title="понижено до справочного", admitted=False, demoted=True,
            reason=(f"на последних {window} отчётах отношение MSE уравнения к "
                    f"лучшему эталону («{title_of(recent_best)}») — {shown}, "
                    f"больше {_ru(DEMOTION_MSE_RATIO)}"),
            **common)
    if ratio is not None and ratio <= ADMISSION_MAX_MSE_RATIO:
        return Admission(
            status="admitted", title="допущено к решению владельца", admitted=True,
            demoted=False,
            reason=(f"отношение MSE уравнения к лучшему эталону («{title_of(best)}») "
                    f"за {n} отчётов — {_ru(ratio, 2)}, не больше "
                    f"{_ru(ADMISSION_MAX_MSE_RATIO)}; к правилу A-P2u автоматически "
                    "не подключается"),
            **common)
    shown = "не определено" if ratio is None else _ru(ratio, 2)
    return Admission(
        status="not_admitted", title="не допущено", admitted=False, demoted=False,
        reason=(f"отношение MSE уравнения к лучшему эталону («{title_of(best)}») "
                f"за {n} отчётов — {shown}, больше порога {_ru(ADMISSION_MAX_MSE_RATIO)}"),
        **common)


def _reports_word(n: int) -> str:
    """«1 отчёт», «2 отчёта», «5 отчётов»."""
    if n % 10 == 1 and n % 100 != 11:
        return "отчёт"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "отчёта"
    return "отчётов"


def _migrate(db: sqlite3.Connection) -> None:
    """Переносит журнал на текущую схему, сохраняя записи (схемы 850oa v1, v2)."""
    if db.execute("PRAGMA user_version").fetchone()[0] >= SCHEMA_VERSION:
        return
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}

    if "actuals" in tables and "reported_on" not in _columns(db, "actuals"):
        rows = list(db.execute("SELECT target, period, value, recorded_at, source FROM actuals"))
        db.execute("DROP TABLE actuals")
        db.executescript(SCHEMA)
        for target, period, value, recorded_at, source in rows:
            db.execute(
                "INSERT INTO actuals (target, period, value, reported_on, recorded_at, source)"
                " VALUES (?,?,?,?,?,?)",
                (target, period, value, str(recorded_at)[:10], recorded_at, source))

    if "naive" in tables and "id" not in _columns(db, "naive"):
        rows = list(db.execute("SELECT made_at, target, period, method, value FROM naive"))
        db.execute("DROP TABLE naive")
        db.executescript(SCHEMA)
        for made_at, target, period, method, value in rows:
            db.execute("INSERT INTO naive (made_at, target, period, method, value)"
                       " VALUES (?,?,?,?,?)", (made_at, target, period, method, value))
    db.commit()


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in db.execute("PRAGMA table_info(%s)" % table)}


# ------------------------------------------------------------ наивные эталоны


def _strictly_before(history: dict[str, float], period: str) -> dict[str, float]:
    """История ДО прогнозируемого периода, того же вида.

    Без отсечения эталон, пересчитанный после выхода факта, включил бы сам
    факт. Эталон, знающий ответ, — не эталон.
    """
    wanted = periods.kind(period)
    return {p: v for p, v in history.items()
            if periods.is_period(p) and periods.kind(p) == wanted
            and periods.index(p) < periods.index(period)}


def _per_year(period: str) -> int:
    return 4 if periods.is_quarter(period) else 2


def naive_margin(history: dict[str, float], period: str) -> dict[str, float]:
    """Эталоны маржи периода по истории того же вида {период: доля выручки}.

    * `yoy_plus_shift` (главный): m(t−год) + [m(t−1) − m(t−1−год)] — сезонность
      квартала из прошлого года и свежий сдвиг уровня (лучший на истории Ленты,
      RMSE 1,50 п.п. на кварталах 2022–2026, study/04 §8.2);
    * `seasonal_naive`: m(t−год) — тот же квартал год назад;
    * `last_period`: m(t−1);
    * `mean_of_year`: среднее последнего года (четырёх кварталов / двух полугодий).

    Эталон, для которого не хватает истории, не пишется: пустой честнее
    выдуманного. Разрыв периметра эталон не исправляет — его помечает журнал
    (`indicators.perimeter`).
    """
    before = _strictly_before(history, period)
    y = _per_year(period)
    get = lambda steps: before.get(periods.shift(period, steps))  # noqa: E731
    out: dict[str, float] = {}
    base, last, base_last = get(-y), get(-1), get(-y - 1)
    if base is not None and last is not None and base_last is not None:
        out["yoy_plus_shift"] = base + (last - base_last)
    if base is not None:
        out["seasonal_naive"] = base
    if last is not None:
        out["last_period"] = last
    year = [get(-k) for k in range(1, y + 1)]
    if all(v is not None for v in year):
        out["mean_of_year"] = sum(year) / y
    return out


def naive_revenue(history: dict[str, float], period: str) -> dict[str, float]:
    """Эталоны выручки периода (млрд ₽) по истории того же вида.

    * `yoy_growth_carried` (главный): R(t−год) × R(t−1) / R(t−1−год) — тот же
      квартал год назад, умноженный на рост прошлого квартала г/г;
    * `seasonal_naive`: R(t−год).

    Рост — отчётный, не органический: органическую выручку «О'КЕЙ» по кварталам
    Лента не раскрывает (июнь 2026 сидит внутри строки гипермаркетов). Сломанные
    сделками кварталы помечает журнал и исключает ретро-проверка.
    """
    before = _strictly_before(history, period)
    y = _per_year(period)
    get = lambda steps: before.get(periods.shift(period, steps))  # noqa: E731
    out: dict[str, float] = {}
    base, last, base_last = get(-y), get(-1), get(-y - 1)
    if base is not None and last is not None and base_last:
        out["yoy_growth_carried"] = base * last / base_last
    if base is not None:
        out["seasonal_naive"] = base
    return out


def naive_interest(previous: float, *, average_rate_now: float,
                   average_rate_before: float) -> dict[str, float]:
    """Эталон чистых процентов: прошлый период × отношение средних ключевых ставок.

    Написан для плавающего долга. У Ленты плавающая доля ≈26 % (D12): эталон
    будет меряться на корзинах ставок книги (`financing.rate_baskets`), когда
    канал процентов перейдёт на них (заметка в `indicators/interest.py`).
    """
    if not average_rate_before:
        return {}
    return {"previous_period_scaled_by_rates":
            previous * (average_rate_now / average_rate_before)}
