"""Точка входа слоя индикаторов: сбор, нау-каст, фиксация прогноза.

    python -m indicators.collect collect   # утренний такт: весь реестр (= daily)
    python -m indicators.collect daily     # ежедневные сборщики реестра
    python -m indicators.collect weekly    # недельные сборщики реестра (сейчас нет)
    python -m indicators.collect nowcast   # прогноз ближайшего квартала + запись в журнал
    python -m indicators.collect status    # что собрано, свежесть, объём, допуск
    python -m indicators.collect health    # живые ряды и объём состояния (код 1 — тревога)
    python -m indicators.collect salary-index   # пересчёт справочного индекса вилок
    python -m indicators.collect record-actual \\
        --target <префикс эмитента>.ebitda_margin_pre16 --period 2026Q3 \\
        --value 0.0715 --reported-on 2026-10-29 --source "Лента, 3 кв. 2026"

**Кварталы, а не полугодия** (D2, D15). Модель полугодовая, а Лента
отчитывается поквартально: нау-каст и журнал ведут ближайший квартал
(сегодня — 3 кв. 2026, отчёт ≈29–30.10.2026), а маржа его полугодия (2026H2)
пишется рядом — она идёт в правило A-P2u и в таблицу «что даст отчёт».
Факт вносится ТОЛЬКО командой `record-actual`: квартал — маржа и выручка
квартального релиза (1–3 кв.), полугодие и год — годового МСФО. У 4 квартала
два события: выручка — операционными результатами (≈февраль), маржа —
годовым отчётом (≈март); команда принимает оба, журнал сам разводит их по
событиям (`periods.report_id`).

Факт вносится в день выхода отчёта или позже: `--reported-on` — день
ПУБЛИКАЦИИ, в будущем его не бывает. Проверяются величина (из
`RECORDABLE_TARGETS`), период (не раньше первого прогнозного квартала
`periods.FIRST_FORECAST_QUARTER`, не позже текущего квартала), значение в
своём коридоре (`VALUE_RANGES`) и день публикации. Каждая запись неснимаема:
журнал запрещает правку и удаление триггерами.

Сбой одного источника не останавливает прогон: отчёт возвращает статус по
каждому, а ненулевой код выхода поднимает тревогу. Все операции идемпотентны —
повтор в тот же день не портит архив.

Коды выхода тактов сбора: 0 — собрано; 1 — отказал КРИТИЧЕСКИЙ источник,
выпуска не будет; `ALARM_EXIT` (3) — работа выполнена, но отказал
НЕВОСПОЛНИМЫЙ источник (такт доводится до конца и кончается кодом тревоги).
Флаги: `--retry` — одна повторная попытка по невосполнимым источникам
(вечерний такт), `--simulate-failure ИМЯ` — проба доставки тревоги без
обращения к источнику.

Тот же `ALARM_EXIT` возвращает такт `nowcast`, когда нарушена дисциплина
журнала (окно отчёта закрылось, а факт не внесён): выпуск от нау-каста не
зависит и обязан выйти.

Канал процентов оставлен общим (реестр траншей × путь ключевой ставки мира
книги); у Ленты его предстоит свести с корзинами ставок книги
(`financing.rate_baskets`, D12) — заметка в `indicators/interest.py`. Пока
реестра долга Ленты в фактах нет, такт пропускает канал с заметкой.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from indicators import issuer, periods
from indicators.journal import (
    GUIDANCE,
    INTEREST_TARGET,
    MARGIN_TARGET,
    MODEL_EXPECTATION,
    REVENUE_TARGET,
    Journal,
    JournalError,
    forecast_period,
    naive_interest,
    naive_margin,
    naive_revenue,
    report_date,
)
from indicators.nowcast import (
    MARGIN_VERSION,
    REVENUE_VERSION,
    interest_nowcast,
    margin_nowcast,
)
from indicators.sources import COLLECTORS, DAILY, IRRECOVERABLE, WEEKLY, run_collectors
from indicators.store import STATE_CEILING_BYTES, Store
from model.paths import FACTS_DIR

FACTS = FACTS_DIR
ANCHOR_FACTS = "anchor.json"


def quarterly_facts(store: Store | None = None) -> dict:
    """Квартальная история IAS 17 (выручка, маржа, чистые проценты) — для эталонов.

    Источник — ряды датабука в хранилище (последний винтаж), иначе факты
    книги (`accounting_base.json → quarters`): одна функция на журнал,
    ретро-проверку и выпуск (`indicators.retro.quarterly_history`). Прежняя
    версия читала полугодовой `history.json` схемы 850oa, которого у Ленты
    нет.
    """
    from indicators.retro import quarterly_history

    return quarterly_history(store=store)


def anchor_facts() -> dict:
    """Факты якоря (`anchor.json`): гайденс года и отчётные полугодия. Нет — {}."""
    try:
        return json.loads((FACTS / ANCHOR_FACTS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def guidance_value(period: str, *, A: dict, half_revenue: float | None) -> float | None:
    """Эталон «гайденс» для периода: маржа, которую требует годовая цель компании.

    Цель «EBITDA ≥ 7 % за 2026» при отчётном 1П требует ≈7,7–7,9 % во 2П (D2);
    квартал 2П — маржа 2П + поправка квартала книги. Нет фактов гайденса или
    выручки 2П — эталона нет (None), и это честнее числа из кода.
    """
    from indicators import quarterly

    if not periods.is_period(period) or half_revenue is None:
        return None
    guidance = quarterly.load_guidance(anchor_facts(), periods.parse(period).year)
    if guidance is None:
        return None
    return quarterly.guidance_benchmark(
        period, guidance_year=guidance["year"], fy_margin=guidance["fy_margin"],
        h1_revenue=guidance["h1_revenue"], h1_ebitda=guidance["h1_ebitda"],
        h2_revenue=half_revenue, quarter_offset_pp=A["margin"].get("quarter_offset_pp"))



# Код выхода «работа выполнена ПОЛНОСТЬЮ, но прогон требует внимания».
# Отличается от 1 намеренно: 1 у `cmd_collect` означает «выпуска не будет»
# (отказал критический источник) и под `set -Eeuo` останавливает такт, а
# невосполнимый пропуск такт останавливать не должен — выпуск обязан выйти, а
# тревога уйти КОДОМ ЮНИТА в самом конце (`ops/run.sh`).
ALARM_EXIT = 3

# Имя нау-каста в отчёте сборщиков. Это НЕ источник данных: строка нужна
# только для того, чтобы отказ шага «нау-каст» ехал к выпуску по тому же
# каналу, что и отказ сборщика, — иначе флагов деградации стало бы два.
NOWCAST_SOURCE = "nowcast"

# Человеческие названия сборщиков. Строка из отчёта уходит в `live.degraded` и
# оттуда на витрину плашкой: владелец читает «котировка LENT», а не
# `moex_quote`. Имя сборщика в скобках остаётся — по нему ищут в journalctl.
SOURCE_TITLES = {
    "cbr_key_rate": "ключевая ставка ЦБ",
    "moex_curve": "бескупонная кривая ОФЗ",
    "moex_quote": f"котировка {issuer.TICKER}",
    "moex_security": f"карточка акции {issuer.TICKER} (листинг, выпуск)",
    "bonds": f"облигации группы ({issuer.NAME})",
    "lenta_disclosure": "лента существенных фактов",
    "lenta_databook": "датабук эмитента",
    "trudvsem_vacancies": "вакансии «Работы России»",
    # Не сборщик, а шаг такта. Живёт в том же отчёте намеренно: канал «отказ →
    # флаг выпуска → плашка» уже есть, и второй такой канал развёлся бы с
    # первым (аудит третьей итерации, C6).
    NOWCAST_SOURCE: "нау-каст ближайшего отчёта",
}

# Что именно не удалось. У сборщика — обход источника, у нау-каста — запись
# прогноза в журнал. Строка уходит на витрину, и «сбор не удался» о журнале
# читалось бы как отказ сети.
FAILURE_WHAT = {NOWCAST_SOURCE: "прогноз не записан в журнал"}

# Сколько дней отказ помнится выпуском. Недельная панель опрашивается по
# четвергам: при окне короче недели плашка о пропущенном четверге исчезла бы
# раньше, чем появится попытка, которая её снимет.
DEGRADED_MEMORY_DAYS = 7

# Пауза перед повтором невосполнимого источника. Секунды, а не минуты: такт
# ограничен `RuntimeMaxSec` юнита (у суточного — 30 минут), а вежливость к
# источнику считается обходами в сутки, не запросами в минуту.
RETRY_PAUSE_SECONDS = 30.0

# Отчёт сборщиков лежит в состоянии рядом с рядами: сбор идёт ОДНИМ процессом
# (`indicators.collect`), а выпуск собирает ДРУГОЙ (`ops/build_release.py`), и
# «какие источники отказали» между ними больше передать нечем.
COLLECTOR_REPORT_NAME = "collector_report.json"

SIMULATED_STATUS = ("ОШИБКА: ПРОБА ТРЕВОГИ: источник объявлен отказавшим флагом "
                    "--simulate-failure, обращения к нему не было")


def read_collector_report(store: Store) -> dict:
    """Что отчёт сбора знает об отказах. Нет файла или мусор — пусто."""
    path = store.root / COLLECTOR_REPORT_NAME
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return saved if isinstance(saved, dict) else {}


def write_collector_report(store: Store, report: dict[str, str], *,
                           simulated=(), now: datetime | None = None) -> Path:
    """Сохраняет отказы сборщиков для выпуска.

    Отказы накапливаются ПО ИСТОЧНИКАМ, а не переписываются целиком: такты
    разные (`daily` в 03:10 и 16:41, `weekly` в четверг 06:00), и суточный
    прогон, переписав файл своими шестью именами, стёр бы отказ недельной
    панели — то есть ровно тот пропуск, который невосполним.

    Три исхода различаются: «ок» снимает отказ, «ОШИБКА» ставит, а
    «пропущен: обход за эту дату уже собран» не значит ни того, ни другого и
    прошлую запись не трогает — иначе повторный такт того же дня снимал бы
    тревогу об обходе, которого не было.

    **Запись ПРОБЫ снимается ближайшим тактом** (доделка приёмки, S1.6), чем
    бы тот ни кончился. Проба — это заявление «источника мы не спрашивали», и
    жить дольше одного такта оно не вправе: у вакансий следующий такт суток
    отвечает «пропущен: обход за эту дату уже собран», отказ не снимался, и
    плашка о несуществующем пропуске висела на витрине до завтра.
    """
    now = now or datetime.now(timezone.utc)
    simulated = set(simulated)
    sources = dict(read_collector_report(store).get("sources") or {})
    for name, item in list(sources.items()):
        if isinstance(item, dict) and item.get("simulated") and name not in simulated:
            sources.pop(name)
    for name, status in report.items():
        if status.startswith("ОШИБКА"):
            sources[name] = dict(status=status[:300], at=now.date().isoformat(),
                                 irrecoverable=name in IRRECOVERABLE,
                                 simulated=name in simulated)
        elif status.startswith("ок"):
            sources.pop(name, None)
    cutoff = now.date() - timedelta(days=DEGRADED_MEMORY_DAYS)
    sources = {name: item for name, item in sources.items()
               if _report_day(item, now.date()) >= cutoff}
    path = store.root / COLLECTOR_REPORT_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(at=now.isoformat(timespec="seconds"), sources=sources),
                               ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    return path


def _report_day(item: dict, fallback: date) -> date:
    try:
        return date.fromisoformat(str(item.get("at"))[:10])
    except ValueError:
        return fallback


def degradation_notes(saved: dict, *, today: date | None = None) -> list[str]:
    """Отказы сборщиков строками для `live.degraded` — то есть для человека.

    Аудит третьей итерации, A4(4): строка «выпуск выйдет с флагом деградации»
    печаталась в журнал сбора, а в выпуске флага не было — `live.degraded`
    заполнялся только ценой и кривой. Обещание флага и было всей приёмкой.

    **Строка ПРОБЫ начинается словом «ПРОБА»** (доделка приёмки, S1.6).
    `--simulate-failure` объявляет источник отказавшим, не обращаясь к нему;
    в отчёте это отмечено полем `simulated`, а плашка его не читала — и
    владелец видел на витрине «пропуск невосполним» там, где ничего не
    пропущено. Слово стоит ПЕРВЫМ: на телефоне строка обрезается.
    """
    today = today or datetime.now(timezone.utc).date()
    notes = []
    for name, item in sorted((saved.get("sources") or {}).items()):
        if not isinstance(item, dict):
            continue
        day = _report_day(item, today)
        if (today - day).days > DEGRADED_MEMORY_DAYS:
            continue
        reason = str(item.get("status") or "").replace("ОШИБКА: ", "", 1)
        if item.get("simulated"):
            notes.append(
                f"ПРОБА ТРЕВОГИ, а не отказ: {SOURCE_TITLES.get(name, name)} "
                f"({name}) объявлена отказавшей вручную {day.strftime('%d.%m.%Y')}, "
                "обращения к источнику не было — данные за день на месте. "
                "Запись снимается ближайшим тактом.")
            continue
        loss = " — пропуск невосполним" if item.get("irrecoverable") else ""
        what = FAILURE_WHAT.get(name, "сбор не удался")
        notes.append(f"{SOURCE_TITLES.get(name, name)} ({name}): {what} "
                     f"{day.strftime('%d.%m.%Y')}{loss}. {reason}")
    return notes


def cmd_collect(names, store: Store, *, retry: bool = False, simulate=(),
                pause: float | None = None) -> int:
    """Отказ второстепенного сборщика — тревога, а не остановка выпуска.

    Прежняя версия (850oa) возвращала 1 при падении любого источника, а
    `run.sh` работает под `set -e`: недоступность второстепенного источника
    останавливала публикацию оценки, которая от него не зависит. Критических
    источников два (`indicators.sources.CRITICAL`) — котировка эмитента и
    кривая; их отказ по-прежнему роняет прогон.

    Отказ НЕВОСПОЛНИМОГО источника (`sources.IRRECOVERABLE`) даёт код
    `ALARM_EXIT`: все шаги при этом выполнены, но `run.sh` обязан довести такт
    до конца и вернуть кодом юнита 1 — только так тревога дойдёт до владельца
    через мост `dash-alert` (решение владельца от 22.09.2026).

    `retry=True` — одна повторная попытка по невосполнимым источникам:
    суточному такту стоит попробовать ещё раз, прежде чем объявлять день
    потерянным. `simulate` — имена, которые объявляются отказавшими БЕЗ
    обращения к источнику (проба доставки тревоги на сервере).
    """
    from indicators.sources import CRITICAL

    names = list(names)
    simulate = tuple(n for n in simulate if n in names)
    report = run_collectors([n for n in names if n not in simulate], store)
    for name in simulate:
        report[name] = SIMULATED_STATUS
    for name, status in report.items():
        mark = " [критический]" if name in CRITICAL else ""
        print(f"  {name:<20}{status}{mark}")

    if retry:
        _retry_irrecoverable(report, store, skip=simulate,
                             pause=RETRY_PAUSE_SECONDS if pause is None else pause)

    write_collector_report(store, report, simulated=simulate)
    failed = [k for k, v in report.items() if v.startswith("ОШИБКА")]
    # Единицы — десятичные (МБ = /1e6), те же, что у `health` и у потолка.
    # Прежде эта строка печатала МиБ под подписью «МБ», и в одном журнале
    # такта соседствовали «сырой архив: 421.5 МБ» и «сырой архив 442.0 МБ»
    # про ОДИН И ТОТ ЖЕ архив (находка скептика на делегированный S2.5).
    print(f"сырой архив: {store.raw_size_bytes() / 1e6:.1f} МБ, "
          f"рядов: {len(store.all_series())}")

    _post_collection(names, report, store)

    critical_failed = [k for k in failed if k in CRITICAL]
    secondary_failed = [k for k in failed if k not in CRITICAL]
    lost = [k for k in failed if k in IRRECOVERABLE]
    if secondary_failed:
        print("ТРЕВОГА: отказали второстепенные источники: "
              + ", ".join(secondary_failed)
              + ". Выпуск выйдет с флагом деградации.", file=sys.stderr)
    if critical_failed:
        print("ОТКАЗ КРИТИЧЕСКОГО ИСТОЧНИКА: " + ", ".join(critical_failed)
              + " — выпуска не будет, на витрине останется прежний.", file=sys.stderr)
        return 1
    if lost:
        # Эти источники отдают только текущее состояние: пропущенный день не
        # докачивается никогда. Поэтому код ненулевой — мост `dash-alert`
        # срабатывает по коду юнита, и другого канала у владельца нет.
        print("НЕВОСПОЛНИМЫЙ ПРОПУСК: " + ", ".join(lost)
              + " — пропущенный день не добирается никогда. Код такта будет 1.",
              file=sys.stderr)
        return ALARM_EXIT
    return 0


def _post_collection(names, report: dict[str, str], store: Store) -> None:
    """Что сказать вслух после сбора и что пересчитать по свежему архиву.

    * справочный индекс вилок — после удачного обхода «Работы России»;
    * «ВЫШЕЛ ОТЧЁТ» — новая версия датабука сегодня (сигнал источника);
    * «СОБЫТИЕ» — сегодняшние сообщения классов `DISCLOSURE_ALARM_KINDS`
      (сделки, собственные акции, дивиденды, листинг, оферты);
    * «КАРТОЧКА АКЦИИ» — смена уровня листинга или объёма выпуска (гейт
      «пересмотреть g», D15).

    Телеграм-канала под события нет (решение владельца 850oa): здесь строка
    журнала такта, в выпуске — флаг, на витрине — плашка (P4).
    """
    from indicators import salary_index
    from indicators.sources import DISCLOSURE_ALARM_KINDS, UTC_TODAY, security_changes

    today = UTC_TODAY()
    if "trudvsem_vacancies" in names and report.get("trudvsem_vacancies", "").startswith("ок"):
        try:
            print("  " + salary_index.write_series(store))
        except Exception as error:  # noqa: BLE001 — справочный ряд не роняет такт
            print(f"  зарплатный индекс не пересчитан: {type(error).__name__}: {error}",
                  file=sys.stderr)
    version = store.load(issuer.series("databook.new_version"))
    if version and any(p.period == today for p in version.points):
        note = next(p.note for p in version.points if p.period == today)
        print(f"ВЫШЕЛ ОТЧЁТ: новая версия датабука ({note}). Внести факты — "
              "`record-actual`; проверить календарь.", file=sys.stderr)
    fresh = []
    for kind in DISCLOSURE_ALARM_KINDS:
        series = store.load(issuer.series(f"disclosure.{kind}"))
        if series and any(p.period == today for p in series.points):
            fresh.append(kind)
    if fresh:
        print("СОБЫТИЕ: сегодня в ленте раскрытия — " + ", ".join(fresh)
              + ". Проверьте, не нужна ли новая версия книги.", file=sys.stderr)
    for change in security_changes(store):
        if change["since"] == today:
            print(f"КАРТОЧКА АКЦИИ: {change['field']} {change['before']:g} → "
                  f"{change['after']:g} — пересмотреть дисконт за управление (g).",
                  file=sys.stderr)


def _retry_irrecoverable(report: dict[str, str], store: Store, *, skip=(),
                         pause: float = RETRY_PAUSE_SECONDS) -> None:
    """Одна повторная попытка по невосполнимым источникам.

    Утренний отказ вакансий 22.09.2026 (HTTP 503) стоил бы дня, если бы
    вечерний такт не попробовал ещё раз. Повтор берёт РОВНО отказавшие имена
    и ровно один раз: источник, который лежит, от третьего запроса не встанет.

    Успехом считается только «ок»: «пропущен: обход за эту дату уже собран»
    отказ не отменяет — это ответ о дате, а не о полноте обхода.
    """
    again = [name for name, status in report.items()
             if status.startswith("ОШИБКА") and name in IRRECOVERABLE and name not in skip]
    if not again:
        return
    print(f"повтор невосполнимых источников через {pause:.0f} с: " + ", ".join(again))
    time.sleep(pause)
    for name in again:
        status = run_collectors([name], store)[name]
        print(f"  повтор {name:<20}{status}")
        report[name] = status if status.startswith("ок") else f"{report[name]}; повтор: {status}"


def cmd_nowcast(store: Store, *, record: bool = True, today: date | None = None,
                A: dict | None = None, journal: Journal | None = None,
                expectation=None) -> int:
    """Прогноз ближайшего КВАРТАЛА и его фиксация ДО выхода факта.

    Что пишется в журнал (всё — на тот же день, эталоны рядом с прогнозом):

    * маржа квартала `lenta-margin-v1` — ожидание модели (`nowcast.margin_nowcast`)
      и эталоны: «тот же квартал год назад + сдвиг прошлого квартала г/г»
      (главный), сезонный наивный, прошлый квартал, среднее года, ожидание
      модели (обязательный) и гайденс компании;
    * маржа его полугодия (для правила A-P2u и таблицы «что даст отчёт», D2);
    * выручка квартала (`lenta-revenue-v1`: выручка полугодия ядра × доля
      квартала) с эталонами «тот же квартал × рост прошлого г/г» и сезонным;
    * чистые проценты квартала — если реестр долга Ленты уже в фактах.

    Период — самый ранний НЕОТЧИТАВШИЙСЯ квартал (`journal.forecast_period`):
    с 1 октября календарь перевёл бы слой на 4 кв., хотя отчёт за 3 кв.
    выходит в конце октября.

    **Нау-каст деградирует в флаг, а не роняет выпуск** (аудит 850oa, C6):
    нарушение дисциплины журнала — строка в отчёте сборщиков (оттуда плашка)
    и код `ALARM_EXIT`; выпуск от нау-каста не зависит.

    `A`, `journal`, `expectation` — подмены для тестов (книга, журнал,
    ожидание модели на квартал); по умолчанию — книга `model.book.book()`,
    журнал состояния и `indicators.quarterly.expected_quarter`.
    """
    from indicators import quarterly

    today = today or datetime.now(timezone.utc).date()
    journal = journal or Journal()
    if A is None:
        from model.book import book

        A = book()
    quarter = forecast_period(journal, MARGIN_TARGET, today=today)
    revenue_quarter = forecast_period(journal, REVENUE_TARGET, today=today)
    expected = (expectation(A, quarter) if callable(expectation) else
                expectation if expectation is not None else
                quarterly.expected_quarter(A, quarter))
    margin = margin_nowcast(store, A, quarter, expectation=expected)
    half = margin.half
    print(f"маржа {margin.period}: {margin.value * 100:.2f} % ± {margin.std_error * 100:.2f} п.п. "
          f"(полугодие {half}: {margin.half_value * 100:.2f} %)")
    for name, value in margin.components.items():
        print(f"    {name:<16}{value * 100:+.3f} п.п." if name != "base"
              else f"    {name:<16}{value * 100:.2f} %")

    interest = None
    try:
        interest = interest_nowcast(store, A, period=quarter)
    except (OSError, KeyError, ValueError) as error:
        print(f"чистые проценты {quarter}: канал пропущен — {type(error).__name__}: "
              f"{str(error)[:120]} (реестр долга Ленты и корзины ставок — заметка в "
              "indicators/interest.py)")
    if interest is not None:
        print(f"чистые проценты {interest.period}: {interest.net_interest:.1f} млрд ₽ "
              f"= {interest.debt_interest:.1f} по долгу − {interest.cash_income:.1f} на кассу")

    if not record:
        return 0

    history = quarterly_facts(store)
    written = repeated = 0

    def keep(forecast) -> None:
        nonlocal written, repeated
        if forecast.recorded:
            written += 1
        else:
            repeated += 1

    try:
        keep(journal.record(
            target=MARGIN_TARGET, period=quarter, value=margin.value,
            std_error=margin.std_error, equation=margin.equation, version=margin.version,
            inputs=margin.inputs, today=today,
            note=f"зафиксирован до выхода отчёта за {quarter}"))
        journal.record_naive(MARGIN_TARGET, quarter, {
            **_not_yet_frozen(journal, MARGIN_TARGET, quarter,
                              naive_margin(history["margin"], quarter)),
            MODEL_EXPECTATION: margin.expectation,
            GUIDANCE: guidance_value(quarter, A=A, half_revenue=expected.half_revenue)})

        # Полугодие квартала — то, что идёт в правило A-P2u (D2). Эталоны у него
        # — ожидание модели и гайденс: наивные эталоны полугодия на квартальной
        # истории и полугодовом шаге не сопоставимы с зачётом кварталов.
        keep(journal.record(
            target=MARGIN_TARGET, period=half, value=margin.half_value,
            std_error=margin.half_std_error, equation=margin.equation,
            version=margin.version, today=today,
            inputs=dict(quarter=quarter, half_margin=round(margin.half_value, 6)),
            note=f"маржа {half}, которой соответствует прогноз {quarter} (правило A-P2u)"))
        journal.record_naive(MARGIN_TARGET, half, {
            MODEL_EXPECTATION: margin.half_value,
            GUIDANCE: guidance_value(half, A=A, half_revenue=expected.half_revenue)})

        revenue_expected = (expected if revenue_quarter == quarter else
                            (expectation(A, revenue_quarter) if callable(expectation) else
                             quarterly.expected_quarter(A, revenue_quarter)))
        if revenue_expected.revenue is not None:
            keep(journal.record(
                target=REVENUE_TARGET, period=revenue_quarter, value=revenue_expected.revenue,
                std_error=None, equation="выручка полугодия ядра × доля квартала книги",
                version=REVENUE_VERSION, today=today,
                inputs=dict(half_revenue=round(revenue_expected.half_revenue, 6),
                            share=round(revenue_expected.share, 6)),
                note=f"отчёт ≈{_iso(report_date(revenue_quarter, 'revenue'))}"))
            journal.record_naive(REVENUE_TARGET, revenue_quarter, _not_yet_frozen(
                journal, REVENUE_TARGET, revenue_quarter,
                naive_revenue(history["revenue"], revenue_quarter)))

        if interest is not None:
            keep(journal.record(
                target=INTEREST_TARGET, period=quarter, value=interest.net_interest,
                std_error=interest.net_interest * INTEREST_ACCURACY,
                equation="реестр долга x ключевая + спред",
                version="interest-v1", inputs=interest.inputs, today=today))
            journal.record_naive(INTEREST_TARGET, quarter, _interest_benchmark(store, quarter))
    except JournalError as error:
        print(f"ЖУРНАЛ: {error}", file=sys.stderr)
        _report_nowcast(store, today, error=str(error))
        print("ТРЕВОГА: нау-каст деградирует в флаг выпуска; выпуск выйдет, "
              "код такта будет 1. Закрывается только `record-actual`.",
              file=sys.stderr)
        return ALARM_EXIT

    _report_nowcast(store, today)
    print(f"журнал: {journal.path}")
    print(f"записей новых {written}, без изменений {repeated}")
    return 0


def _not_yet_frozen(journal: Journal, target: str, period: str,
                    values: dict[str, float]) -> dict[str, float]:
    """Эталоны из закрытых периодов, которых в журнале ещё нет: первая запись — навсегда.

    Датабук переписывает историю каждым релизом, и в день отчёта (датабук уже
    новый, факт ещё не внесён) пересчитанный эталон разошёлся бы с
    замороженным: журнал отказал бы (`JournalError`), и такт поднимал бы
    тревогу до внесения факта. Замороженный эталон не пересчитывается вовсе;
    сторож журнала остаётся для прямых вызовов.
    """
    held = journal.naive(target, period)
    return {method: value for method, value in values.items() if method not in held}


def _iso(day: date | None) -> str:
    return day.isoformat() if day else "—"


def _report_nowcast(store: Store, today: date, *, error: str | None = None) -> None:
    """Исход нау-каста — в ТОТ ЖЕ отчёт, что и исходы сборщиков.

    Успех пишет «ок» и тем самым СНИМАЕТ прежний флаг: иначе плашка о
    невнесённом факте осталась бы на витрине и после того, как факт внесли.
    """
    status = ("ок: прогноз записан" if error is None
              else "ОШИБКА: дисциплина журнала: " + error)
    write_collector_report(
        store, {NOWCAST_SOURCE: status},
        now=datetime(today.year, today.month, today.day, tzinfo=timezone.utc))


# Заявляемая точность канала процентов (850oa: ±8 % до первой вневыборочной
# проверки). У Ленты канала ещё нет — число общее, пересматривается вместе с
# корзинами ставок (D12).
INTEREST_ACCURACY = 0.08


def _interest_benchmark(store: Store, period: str) -> dict[str, float]:
    """«Прошлый период × отношение средних ставок» — эталон канала процентов.

    Прошлый период — того же вида (квартал к кварталу, полугодие к
    полугодию: сумма двух кварталов), факты — квартальные ряды датабука
    (`quarterly_facts`), средние ставки — посуточно из ряда ключевой ставки
    (`indicators.retro.average_key_rate`). Не хватает данных — эталона нет.
    """
    from indicators.retro import average_key_rate

    if not periods.is_period(period):
        return {}
    previous = periods.previous_period(period)
    history = quarterly_facts(store)["net_interest"]
    parts = [history.get(q) for q in periods.quarters_of(previous)]
    if not parts or any(v is None for v in parts):
        return {}
    now, before = average_key_rate(store, period), average_key_rate(store, previous)
    if not now or not before:
        return {}
    return naive_interest(sum(parts), average_rate_now=now, average_rate_before=before)


# Величины, у которых факт вообще бывает: ровно те, что прогнозирует слой.
RECORDABLE_TARGETS = (MARGIN_TARGET, INTEREST_TARGET, REVENUE_TARGET)


# Границы правдоподобия вносимого факта — по ВЕЛИЧИНЕ, а не по формату. Ловят
# подмену единиц: маржа процентами вместо доли (7,15 вместо 0,0715), выручка
# в миллионах вместо миллиардов. Коридоры нарочно широкие: это проверка на
# опечатку, а не допущение о будущем. Выручка — за квартал, полугодие или год.
VALUE_RANGES = {
    MARGIN_TARGET: (0.0, 0.15, "маржа — ДОЛЯ, а не проценты: 7,15 % это 0,0715 "
                               "(2 кв. 2026 — 0,0669; 1П2026 — 0,0606)"),
    INTEREST_TARGET: (0.0, 300.0, "чистые проценты — млрд ₽ за период "
                                  "(2 кв. 2026 — 3,3)"),
    REVENUE_TARGET: (50.0, 5000.0, "выручка — млрд ₽ за период "
                                   "(2 кв. 2026 — 341,6; 1П2026 — 648,5)"),
}


def _value_problem(target: str, value: float) -> str:
    """Почему значение не годится по величине. Пустая строка — годится."""
    low, high, hint = VALUE_RANGES[target]
    if value != value or not (low <= value <= high):   # noqa: PLR0124 — NaN тоже не годится
        return f"вне коридора {low:g}…{high:g}: {hint}"
    return ""


def _period_problem(period: str, *, today: date | None = None) -> str:
    """Почему период не годится. Пустая строка — годится.

    Период — квартал, полугодие или год (`periods`). Снизу — первый прогнозный
    квартал: у периода должен быть квартал не раньше него (2026FY годится —
    его 3 и 4 кварталы прогнозные). Сверху — календарный квартал: факт за
    период, который ещё не начался, — опечатка в цифре года, а стереть её из
    журнала нечем.
    """
    if not periods.is_period(period):
        return ("не разобран как период: ожидается ГГГГQn, ГГГГHn или ГГГГFY, "
                "например 2026Q3, 2026H2 или 2026FY")
    floor = periods.FIRST_FORECAST_QUARTER
    if periods.index(periods.last_quarter(period)) < periods.index(floor):
        return (f"слой не прогнозирует периоды раньше {floor}: факт за {period} "
                "не с чем сравнивать, а история закрытых периодов живёт в фактах книги")
    limit = periods.quarter_of(today or datetime.now(timezone.utc).date())
    if periods.index(periods.first_quarter(period)) > periods.index(limit):
        return (f"период ещё не наступил: идёт {limit}, а факта за {period} быть не может")
    return ""


def cmd_record_actual(args) -> int:
    """Внесение факта — единственный способ закрыть период в журнале.

    Без этой команды факт вносился только из кода и на практике не вносился
    вовсе: журнал копил прогнозы, которые не с чем было сравнить, а табло
    «прогноз против факта» оставалось пустым при любом числе записей.

    **Аргументы проверяются** (аудит третьей итерации, C6). Команда принимала
    любую строку: пример из `README.md` (`--target margin`) проходил с кодом 0
    и писал факт под НЕСУЩЕСТВУЮЩЕЙ величиной. Тихо: запись ложилась в журнал,
    триггеры запрещают её удалять, а с прогнозом она не встречалась никогда —
    табло «прогноз против факта» оставалось пустым, и выглядело это как
    «прогнозов не было», а не как «факт записан не туда».

    Код 64 (`EX_USAGE`), а не 1: это ошибка вызова, и `record-actual` — ручная
    команда, в конвейере её нет.

    **Проверяются все четыре аргумента** (доделка приёмки, S1.7). Прежде
    принимались период, который ещё не начался (`2030H1`), значение в чужих
    единицах (`4.82` вместо `0.0482`) и день публикации в будущем
    (`2099-01-01`). Каждая такая запись — неснимаемая: удалять факты
    запрещают триггеры журнала, а табло «прогноз против факта» после неё
    показывает не то, что было.
    """
    if args.target not in RECORDABLE_TARGETS:
        print(f"record-actual: --target {args.target!r} — не та величина, которую "
              "прогнозирует слой. Допустимые: " + ", ".join(RECORDABLE_TARGETS),
              file=sys.stderr)
        return 64
    problem = _period_problem(args.period)
    if problem:
        print(f"record-actual: --period {args.period!r} {problem}", file=sys.stderr)
        return 64
    problem = _value_problem(args.target, args.value)
    if problem:
        print(f"record-actual: --value {args.value!r} {problem}", file=sys.stderr)
        return 64

    today = datetime.now(timezone.utc).date()
    if args.reported_on:
        try:
            reported = date.fromisoformat(args.reported_on)
        except ValueError:
            print(f"record-actual: --reported-on {args.reported_on!r} — не дата "
                  "в формате ГГГГ-ММ-ДД", file=sys.stderr)
            return 64
        # День ПУБЛИКАЦИИ отчёта. В будущем его не бывает: от него меряются
        # горизонты прогнозов, и сдвинутая вперёд дата тихо обнуляет зачёт.
        # Ровно один день запаса — потому что конвейер живёт в UTC, а отчёт
        # выходит по Москве: раскрытие в 00:30 МСК это ещё вчерашний UTC.
        if reported > today + timedelta(days=1):
            print(f"record-actual: --reported-on {reported.isoformat()} ещё не "
                  f"наступил (сегодня {today.isoformat()}): это день ПУБЛИКАЦИИ "
                  "отчёта, факт вносится после его выхода", file=sys.stderr)
            return 64
    else:
        reported = today

    journal = Journal()
    actual = journal.record_actual(args.target, args.period, args.value,
                                   source=args.source, reported_on=reported)
    print(f"факт {actual.target} {actual.period} = {actual.value:.6g} "
          f"(опубликован {actual.reported_on.isoformat()})")
    for row in journal.scoreboard(args.target):
        if row["period"] != args.period:
            continue
        print("  горизонты до отчёта:")
        for days, item in sorted(row["horizons"].items(), key=lambda kv: -int(kv[0])):
            if item is None:
                print(f"    {days:>4} дн  — прогноза не было")
            else:
                print(f"    {days:>4} дн  {item['value']:.6g}  ошибка {item['error']:+.6g}")
        verdict = ("обгоняет главный эталон" if row["beats_benchmark"]
                   else "не обгоняет главный эталон" if row["beats_benchmark"] is False
                   else "не зачтено: " + row["note"])
        print(f"  зачёт на {row['scoring_horizon_days']} дн: {verdict}")
    return 0


def cmd_record_keyrate(args, store: Store) -> int:
    """Ручной ввод внешнего прогноза ключевой ставки (макроопрос Банка России).

    Период — полугодие («2027H1») или год («2027»): опрос публикует
    среднегодовые значения, и растягивать их на полугодия за него мы не
    вправе. База оценки при этом не меняется: прогноз участвует только в
    чувствительности канала процентов.
    """
    from indicators.interest import SURVEY_SERIES
    from indicators.store import Point

    if args.value is None or not 0.0 < args.value < 0.5:
        print("ставка задаётся долей единицы, например 0.12", file=sys.stderr)
        return 64
    if not args.period or not args.period[:4].isdigit():
        print("период — «2027H1» или «2027»", file=sys.stderr)
        return 64
    if not args.link:
        print("нужна ссылка на публикацию опроса: --link", file=sys.stderr)
        return 64

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    store.upsert(SURVEY_SERIES,
                 [Point(period=args.period, value=args.value, fetched_at=now,
                        note=f"{args.reported_on or now[:10]}; {args.link}"[:200])],
                 channel=1, cadence="ежемесячно", unit="доля",
                 label="ключевая ставка: макроэкономический опрос Банка России")
    print(f"прогноз принят: {args.period} {args.value * 100:.2f} % ({args.link})")
    print("  база расчёта не меняется — это чувствительность, а не мир ставок")
    return 0


def cmd_backfill(store: Store) -> int:
    """Разовая глубокая догрузка ключевой ставки.

    Ряд ключевой в суточном прогоне идёт на год назад — этого хватает
    нау-касту и не хватает бэктесту канала процентов. Ставка не
    пересматривается, поэтому глубокая история — тот же факт, а не другой
    винтаж.
    """
    from indicators.sources import COLLECTORS, KEY_RATE_FIRST_DAY, collect_cbr_key_rate

    series = store.load("cbr.key_rate")
    before = len(series.points) if series else 0
    result = collect_cbr_key_rate(since=KEY_RATE_FIRST_DAY)
    for filename, body, response in result.raw:
        store.save_raw(result.source, filename, body, url=response.url,
                       fetched_at=response.fetched_at, sha256=response.sha256)
    for series_id, points in result.series.items():
        store.upsert(series_id, points, channel=result.channel, label=series_id,
                     cadence=COLLECTORS["cbr_key_rate"][1])
    series = store.load("cbr.key_rate")
    after = len(series.points) if series else 0
    first = min(p.period for p in series.points) if series and series.points else "—"
    print(f"ключевая ставка: было {before} точек, стало {after}, с {first}")
    return 0


def cmd_compact(store: Store) -> int:
    """Разовая уборка повторов в рядах.

    Прежнее правило различало точки по моменту загрузки, и суточный прогон
    дописывал весь запрошенный год заново. Уборка оставляет по одной записи
    на пару «период + значение» — самую раннюю, то есть момент, когда
    значение стало известно.
    """
    total_before = total_after = 0
    for series in store.all_series():
        before, after = store.compact(series.id)
        if before != after:
            print(f"  {series.id:<44}{before:>7} → {after:>7}")
        total_before += before
        total_after += after
    print(f"точек: было {total_before}, стало {total_after} "
          f"(убрано {total_before - total_after})")
    return 0


def _is_event_series(series_id: str) -> bool:
    return (series_id.startswith(issuer.series("disclosure.")) and not series_id.endswith(".latest"))


def cmd_status(store: Store) -> int:
    today = date.today()
    series = store.all_series()
    print(f"{'ряд':<42}{'канал':>6}{'точек':>7}{'последняя':>12}{'возраст':>9}")
    stale = 0
    for s in sorted(series, key=lambda s: (s.channel, s.id)):
        last = s.latest()
        if not last:
            print(f"{s.id:<42}{s.channel:>6}{len(s.points):>7}{'—':>12}{'—':>9}")
            continue
        age = (today - date.fromisoformat(last.period[:10])).days
        # Событийные ряды разрежены ПО ПРИРОДЕ: оферт не было с февраля, и это
        # факт о компании, а не о сборщике. Считать их устаревшими значит
        # держать тревогу включённой постоянно — то есть выключенной.
        if age > 10 and not _is_event_series(s.id):
            stale += 1
        print(f"{s.id:<42}{s.channel:>6}{len(s.points):>7}{last.period:>12}{age:>7} дн")
    print(f"\nрядов {len(series)}, устаревших (>10 дн) {stale}, "
          f"сырой архив {store.raw_size_bytes() / 1e6:.1f} МБ")   # МБ десятичные, как у health
    days = {name: len(store.raw_days(name)) for name in
            sorted({"cbr", "moex", *COLLECTORS})}
    print("дней в архиве: " + ", ".join(f"{k} {v}" for k, v in days.items() if v))

    journal = Journal()
    forecasts = journal.forecasts()
    print(f"журнал: записей {len(forecasts)}, "
          f"величин {len({(f.target, f.period) for f in forecasts})}")
    for target in (MARGIN_TARGET, INTEREST_TARGET, REVENUE_TARGET):
        events = journal.events(target)
        if events:
            # Правило допуска (`journal.admission`, D15): для маржи — по
            # отчётам, зачтённым ТЕКУЩЕЙ версии уравнения.
            version = MARGIN_VERSION if target == MARGIN_TARGET else None
            status = journal.admission(target, version=version)
            print(f"  {target:<34}событий {len(events)}, {status.title}: {status.reason}")
    return 0


# Ряды, на которых стоят уравнения слоя. Список ЯВНЫЙ: «здоровье состояния»
# должно ломаться при потере именно этих рядов, а не усредняться по всем 71.
VITAL_SERIES = {
    "cbr.key_rate": 10,
    issuer.PRICE_SERIES: 5,
    "moex.zcyc.10y": 5,
}

# Невосполнимые ряды слоя {ряд: предел возраста, дней}: старше предела — «не в
# порядке». Датабук: день публикации последнего релиза на странице; отчёты
# выходят раз в квартал (самый длинный промежуток — от операционных 4 кв. в
# феврале до 1 кв. в конце апреля, ≈85 дней), поэтому предел — 130 дней: дольше
# — компания пропустила отчёт или страница перестала разбираться. Ряда ещё
# нет (свежее состояние) — строка без тревоги. Вакансии «Работы России» сюда
# войдут вместе с `IRRECOVERABLE` — после месяца устойчивой работы (D15).
IRREPLACEABLE_SERIES: dict[str, int] = {issuer.series("databook.published"): 130}


# Журнал объёма состояния: по строке «дата;байты» на день. Лежит внутри
# состояния, рядом с рядами, — он описывает именно его, и переезжает вместе с
# ним. Прирост считает конвейер: «перемерить после 29.09» — это работа для
# машины, а не поручение человеку, которое он вспомнит или нет.
STATE_SIZE_LOG = "state_size.log"
STATE_SIZE_WINDOW_DAYS = 7


def append_state_size(store: Store, size: int, *, today: date | None = None) -> list[tuple[date, int]]:
    """Дописывает замер объёма и возвращает журнал целиком.

    На дату — ОДНА строка, последняя за день: `health` в такте идёт раз в
    сутки, но руками его запускают сколько угодно, и без этого правила
    «прирост за 7 дней» считался бы по числу запусков, а не по дням.

    Дата — UTC (`sources.UTC_TODAY`), как у всего слоя: такт в 03:10 UTC и
    ручной запуск в 01:30 по Москве — одни сутки, а по местной дате прежнего
    кода на ноутбуке это были бы два разных дня (финальная итерация, S1.3).

    Битая строка пропускается С ПРЕДУПРЕЖДЕНИЕМ и остаётся в файле как была:
    файл переписывается, и прежде такая строка молча исчезала при первой же
    перезаписи — вместе с тем, что по ней можно было бы восстановить.
    Предупреждение — не «НЕ В ПОРЯДКЕ»: код `health` от него не меняется.
    Байты не в UTF-8 — тоже битая строка, а не падение: файл читается и
    пишется с `surrogateescape`, и такая строка возвращается в файл побайтово.
    Иначе `UnicodeDecodeError` ронял бы `health`, и такт поднимал бы тревогу
    каждое утро, пока файл не поправят руками (доработка по проверке S1.3).
    """
    from indicators.sources import UTC_TODAY

    today = today or date.fromisoformat(UTC_TODAY())
    path = store.root / STATE_SIZE_LOG
    rows: dict[date, int] = {}
    kept: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="surrogateescape").splitlines()
    except OSError:
        lines = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        day, _, value = line.partition(";")
        try:
            measured, size_then = date.fromisoformat(day.strip()), int(value.strip())
        except ValueError:
            print(f"  предупреждение: {STATE_SIZE_LOG}, строка {number} не разобрана "
                  f"и пропущена (в файле оставлена): {line[:60]!r}")
            kept.append(line)
            continue
        if measured == today:
            continue          # сегодняшний замер заменяется новым
        rows[measured] = size_then
        kept.append(line)
    rows[today] = size
    kept.append(f"{today.isoformat()};{size}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{line}\n" for line in kept), encoding="utf-8",
                    errors="surrogateescape", newline="\n")
    return sorted(rows.items())


def state_growth_line(rows: list[tuple[date, int]],
                      window: int = STATE_SIZE_WINDOW_DAYS) -> str:
    """Прирост состояния за последнюю неделю — строкой, или пусто.

    Печатается, когда в ОКНЕ набралось `window + 1` замеров — восемь на
    неделю: семь суток прироста лежат МЕЖДУ восемью замерами. Прежний порог
    «семь замеров» печатал «за 6 дн» под видом недельного прироста (аудит
    закрытия, A3). Окно — последние `window` суток ПО ДАТАМ, а не последние
    строки: пропуск такта не должен превращать неделю в месяц. Поэтому
    пропущенный день замера гасит строку на неделю — это честнее, чем
    печатать недельный прирост по шести суткам. В строке названы обе границы —
    по ним видно, за что именно посчитано.

    Порог считается ПО ОКНУ, а не по длине журнала (находка скептика): после
    простоя дольше недели старые строки в журнале остаются, порог по числу
    строк проходит, а в окне оказывается две точки — и `health` печатал
    «прирост состояния 500 МБ/сутки» по двум замерам за одни сутки. Решение
    «на сколько лет хватит потолка» принимается именно по этой строке.
    """
    if not rows:
        return ""
    last_day, last_size = rows[-1]
    inside = [row for row in rows if (last_day - row[0]).days <= window]
    first_day, first_size = inside[0]
    days = (last_day - first_day).days
    if len(inside) < window + 1 or days < window:
        return ""
    per_day = (last_size - first_size) / 1e6 / days
    return (f"  {'прирост состояния':<24}{per_day:>9.2f} МБ/сутки  "
            f"за {days} дн ({first_day.isoformat()} → {last_day.isoformat()}, "
            f"замеров {len(inside)})")


def cmd_health(store: Store) -> int:
    """Есть ли живые ряды — вопрос ЭКСПЛУАТАЦИИ, а не тестов.

    Прежде живость проверяли три теста канала процентов: они читали
    собранный ряд ключевой ставки и падали, если его нет. В CI ряда не было
    никогда, и семь коммитов подряд шли с красным CI; на сервере такой тест
    останавливал публикацию, хотя код был цел. Теперь тесты ходят в фикстуры,
    а этот шаг печатает состояние и возвращает 1 — `run.sh` превращает это в
    тревогу, а не в остановку выпуска.

    Заодно шаг ведёт журнал объёма (`state_size.log`) и, когда замеров
    набралось на неделю, печатает прирост МБ/сутки: вопрос «на сколько лет
    хватит потолка» решается числом из конвейера, а не памятью человека.
    Рост журнала — 18 байт в сутки. «Сегодня» — по UTC, как у рядов и тактов.
    """
    from indicators.sources import UTC_TODAY

    today = date.fromisoformat(UTC_TODAY())
    bad = []
    for series_id, limit in sorted(VITAL_SERIES.items()):
        series = store.load(series_id)
        last = series.latest() if series else None
        if not last or last.value is None:
            bad.append(f"{series_id}: ряда нет")
            continue
        age = (today - date.fromisoformat(last.period[:10])).days
        mark = "ок" if age <= limit else f"СТАРШЕ {limit} дн"
        print(f"  {series_id:<24}{last.period}  возраст {age:>3} дн  {mark}")
        if age > limit:
            bad.append(f"{series_id}: возраст {age} дн при пределе {limit}")
    for series_id, limit in IRREPLACEABLE_SERIES.items():
        series = store.load(series_id)
        last = series.latest() if series else None
        if not last or last.value is None:
            print(f"  {series_id:<32}ряда ещё нет")
            continue
        age = (today - date.fromisoformat(last.period[:10])).days
        mark = "ок" if age <= limit else f"СТАРШЕ {limit} дн"
        print(f"  {series_id:<32}{last.period}  возраст {age:>3} дн  {mark}")
        if age > limit:
            bad.append(f"{series_id}: возраст {age} дн при пределе {limit} — невосполнимый ряд")
    # Объём состояния — тоже вопрос эксплуатации: потолок один и тот же для
    # ротации и для этой проверки (`STATE_CEILING_BYTES`), иначе «2 ГБ» жило
    # бы прописью в каждом файле по-своему.
    #
    # Единицы у размера и у потолка ОДНИ И ТЕ ЖЕ — десятичные (МБ = /1e6,
    # ГБ = /1e9). Прежде в одной строке соседствовали МиБ (size/1024/1024) и
    # десятичные ГБ, и из этой строки в отчёты уехало «442 МБ = 21,2 %
    # потолка», хотя 442 МБ десятичных — это 22,1 % от 2 ГБ (делегировано
    # потоку S1 из пункта S2.5).
    size = store.raw_size_bytes()
    share = size / STATE_CEILING_BYTES * 100
    print(f"  {'сырой архив':<24}{size / 1e6:>9.1f} МБ  "
          f"{share:>5.1f} % потолка {STATE_CEILING_BYTES / 1e9:.0f} ГБ")
    if size > STATE_CEILING_BYTES:
        bad.append(f"сырой архив {size / 1e6:.0f} МБ выше потолка "
                   f"{STATE_CEILING_BYTES / 1e9:.0f} ГБ: ротация не справляется")
    growth = state_growth_line(append_state_size(store, size, today=today))
    if growth:
        print(growth)
    for line in bad:
        print(f"  НЕ В ПОРЯДКЕ: {line}")
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Слой опережающих индикаторов")
    parser.add_argument("--link", default="", help="ссылка на источник для record-keyrate")
    parser.add_argument("command", choices=("collect", "daily", "weekly", "all", "nowcast",
                                            "status", "health", "record-actual",
                                            "record-keyrate", "salary-index",
                                            "backfill", "compact"))
    parser.add_argument("--no-record", action="store_true",
                        help="посчитать нау-каст, но не писать в журнал")
    parser.add_argument("--retry", action="store_true",
                        help="одна повторная попытка по невосполнимым источникам")
    parser.add_argument("--simulate-failure", action="append", default=[], metavar="ИСТОЧНИК",
                        help="ПРОБА ТРЕВОГИ: объявить источник отказавшим, "
                             "не обращаясь к нему (проверка доставки в Telegram)")
    parser.add_argument("--target", help="величина для record-actual")
    parser.add_argument("--period", help="период для record-actual: 2026Q3, 2026H2 или 2026FY")
    parser.add_argument("--value", type=float, help="фактическое значение")
    parser.add_argument("--reported-on", help="день ПУБЛИКАЦИИ отчёта, ГГГГ-ММ-ДД")
    parser.add_argument("--source", default="", help="откуда взят факт")
    args = parser.parse_args(argv)

    # `collect` — утренний такт: тот же реестр, что `daily` (второй проход дня
    # ловит отчёт в 10:00 МСК; раз-в-сутки источники повторно не трогаются).
    tacts = {"collect": DAILY, "daily": DAILY, "weekly": WEEKLY, "all": tuple(COLLECTORS)}
    if args.simulate_failure and args.command not in tacts:
        print("--simulate-failure задаётся только тактам collect, daily, weekly и all",
              file=sys.stderr)
        return 64

    if args.command == "record-actual":
        missing = [k for k in ("target", "period", "value") if getattr(args, k) is None]
        if missing:
            parser.error("record-actual требует " + ", ".join("--" + m for m in missing))
        return cmd_record_actual(args)

    store = Store()
    if args.command == "record-keyrate":
        return cmd_record_keyrate(args, store)
    if args.command in tacts:
        names = tacts[args.command]
        # Проба обязана падать громко: имя не из такта означало бы «проба
        # прошла, тревоги нет», то есть ровно тот тихий ноль, который чинится.
        unknown = [n for n in args.simulate_failure if n not in names]
        if unknown:
            print(f"--simulate-failure: {', '.join(unknown)} не собирается тактом "
                  f"{args.command}; в такте: {', '.join(names)}", file=sys.stderr)
            return 64
        return cmd_collect(names, store, retry=args.retry,
                           simulate=tuple(args.simulate_failure))
    if args.command == "nowcast":
        return cmd_nowcast(store, record=not args.no_record)
    if args.command == "salary-index":
        from indicators import salary_index

        print(salary_index.write_series(store))
        return 0
    if args.command == "backfill":
        return cmd_backfill(store)
    if args.command == "compact":
        return cmd_compact(store)
    if args.command == "health":
        return cmd_health(store)
    return cmd_status(store)


if __name__ == "__main__":
    sys.exit(main())
