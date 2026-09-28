"""Календарь событий: что выйдет раньше отчёта эмитента и когда выйдет сам отчёт.

Смысл слоя индикаторов — опережение относительно ОТЧЁТА компании, а не
относительно цены. Календарь (`data/calendar.json`, схема `lenta-calendar-v1`
из черновика книги) перечисляет события, приходящие раньше отчёта (решения по
ставке, тарифы, МРОТ, отчёты аналогов справочно), и сами отчёты с id
`<префикс эмитента>.<отчёт>` — те же имена, что `periods.report_id`.

**Окно, а не выдуманная дата.** Пока компания не объявила день, у отчёта есть
окно по лагам прошлых лет (`precision: window`, `window.from`–`window.to`) и
центральная оценка `when`. Центральная дата нужна обратному отсчёту и
горизонтам журнала; сторож журнала («плановая дата прошла, а факт не внесён»)
смотрит на КОНЕЦ окна: иначе релиз в последний день окна объявлялся бы
пропуском за несколько дней до выхода. У 850oa окна не было, и сторож стоял
на `when`.

Нет файла календаря (репозиторий до книги 1.0) — не ошибка: даты берутся по
правилу лагов `periods.fallback_report_window`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from indicators import issuer, periods
from model.paths import CALENDAR_FILE


@dataclass(frozen=True)
class Event:
    id: str
    when: date
    precision: str
    title: str
    gives: tuple[str, ...]
    prior_for: str | None
    note: str
    window_from: date | None = None
    window_to: date | None = None

    def days_until(self, today: date) -> int:
        return (self.when - today).days

    @property
    def latest(self) -> date:
        """Последний правдоподобный день события: конец окна или сама дата."""
        return self.window_to or self.when

    @property
    def is_fact(self) -> bool:
        """Событие, которое даёт ФАКТ отчёта эмитента (выручку или маржу), а не
        вход или справку."""
        return (self.prior_for is None and self.id.startswith(issuer.series(""))
                and any(g in self.gives for g in ("revenue", "ebitda_margin_pre16")))


def _date(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def load(path: Path | None = None) -> list[Event]:
    data = json.loads(Path(path or CALENDAR_FILE).read_text(encoding="utf-8"))
    events = []
    for item in data["events"]:
        window = item.get("window") or {}
        events.append(Event(
            id=item["id"], when=date.fromisoformat(item["when"]),
            precision=item["precision"], title=item["title"],
            gives=tuple(item.get("gives") or ()), prior_for=item.get("prior_for"),
            note=item.get("note", ""),
            window_from=_date(window.get("from")), window_to=_date(window.get("to"))))
    return sorted(events, key=lambda e: (e.when, e.id))


def _events_or_empty(path: Path | None = None) -> list[Event]:
    try:
        return load(path)
    except (OSError, ValueError, KeyError):
        return []


def upcoming(today: date, *, limit: int = 6, path: Path | None = None) -> list[Event]:
    """Ближайшие события, ещё не прошедшие (окно ещё открыто)."""
    return [event for event in _events_or_empty(path) if event.latest >= today][:limit]


def next_fact(today: date, *, path: Path | None = None) -> Event | None:
    """Ближайший ОТЧЁТ эмитента — точка, относительно которой мерится опережение."""
    for event in _events_or_empty(path):
        if event.latest >= today and event.is_fact:
            return event
    return None


def report_dates(period: str, gives: str = periods.GIVES_MARGIN, *,
                 path: Path | None = None) -> tuple[date, date] | None:
    """(ожидаемая дата, крайняя дата) отчёта, раскрывающего период.

    Календарь — источник правды: событие ищется по имени отчёта
    (`periods.report_id`), а не по дате. Нет события — правило лагов.
    None — период не разобран.
    """
    wanted = periods.report_id(period, gives)
    if not wanted:
        return None
    for event in _events_or_empty(path):
        if event.id == wanted:
            return event.when, event.latest
    return periods.fallback_report_window(period, gives)
