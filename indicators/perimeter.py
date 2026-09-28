"""Разрывы периметра: покупки, после которых период несопоставим со своей базой.

Лента растёт покупками, и почти каждый год её отчётный ряд ломается
консолидацией: «Монетка» с 4 кв. 2023 (выручка квартала 136 → 223 млрд),
«Улыбка радуги» с 01.12.2024, «Молния» с 24.06.2025, «Реми» с 03.12.2025,
«Дом Лента» (бывш. OBI) поэтапно в 1 кв. 2026, гипермаркеты «О'КЕЙ» с
02.06.2026. Эталон «тот же квартал год назад + сдвиг прошлого квартала г/г»
на таком стыке меряет не бизнес, а арифметику консолидации: в базе прошлого
года приобретённой сети нет (D15).

**Правило разрыва.** Сравнение периода `t` с опорными периодами эталона
(`METHOD_STEPS`: у «г/г + сдвиг» это t−1, t−4, t−5 для кварталов) сломано,
если хотя бы одна дата консолидации лежит в полуинтервале
(начало самого раннего периода сравнения; конец `t`] — то есть периметры
периодов сравнения различаются. Консолидация ровно с первого дня самого
раннего периода периметры не различает. Отсюда у «О'КЕЙ» база «г/г» сломана
для 2026Q2…2027Q2 (июнь 2026 — неполный квартал), а у «г/г + сдвиг» — ещё и
для 2027Q3: сдвиг прошлого квартала сравнивает 2027Q2 с 2026Q2, где «О'КЕЙ»
только за июнь.

**Что с этим делают.** Ретро-проверка печатает метрики на всех точках и на
«чистых»; сломанные точки — отдельным списком с причиной (спрятанная точка —
тоже подгонка). Журнал помечает событие, чей главный эталон сломан, и в счёт
допуска его НЕ берёт (`journal.Journal.admission`): эталон без приобретённой
сети смещён (у «О'КЕЙ» и «Дом Ленты» — убыточные сети, база завышена), и
уравнение «обгоняло» бы его за счёт арифметики. Проформенной базы по
кварталам нет: «О'КЕЙ» раскрыт только годовой выручкой 2025 и полугодием 2026
(прим. 5 МСФО 6М2026) — проформа квартальной маржи была бы выдумкой.

Список — факты с источником (`data/facts/inorganic.json` черновика книги,
`deals[].control`). Если в каталоге фактов лежит `perimeter_breaks.json`
(схема ниже), он сильнее списка по умолчанию: разрыв следующей сделки
(«Мария-Ра» и т. п.) вносится данными, а не кодом.

    {"schema": "lenta-perimeter-breaks-v1",
     "breaks": [{"id": "okey", "name": "…", "control": "2026-06-02", "basis": "…"}]}
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from indicators import periods
from model.paths import FACTS_DIR

BREAKS_FILE = "perimeter_breaks.json"
SCHEMA = "lenta-perimeter-breaks-v1"


@dataclass(frozen=True)
class Break:
    id: str
    name: str
    control: date
    """Первый день в периметре группы (дата консолидации)."""
    basis: str

    @property
    def quarter(self) -> str:
        return periods.quarter_of(self.control)

    def as_dict(self) -> dict:
        return dict(id=self.id, name=self.name, control=self.control.isoformat(),
                    quarter=self.quarter, basis=self.basis)


DEFAULT_BREAKS: tuple[Break, ...] = (
    Break("monetka", "«Монетка» (ООО «Элемент-Трейд»)", date(2023, 10, 1),
          "консолидация с октября 2023 (МСФО FY2023; inorganic.json deals.monetka); "
          "выручка квартала 136 → 223 млрд ₽ (датабук, Financials quarterly)"),
    Break("ulybka", "«Улыбка радуги» (ООО «Дрогери Ритейл»)", date(2024, 12, 1),
          "контроль с 01.12.2024 (МСФО FY2024; inorganic.json deals.ulybka); строка "
          "Drogerie датабука с 4 кв. 2024"),
    Break("molniya", "«Молния» (Spar, Челябинск; 72 магазина)", date(2025, 6, 24),
          "контроль с 24.06.2025 (МСФО 6М2025; inorganic.json deals.molniya); магазины "
          "переведены в форматы группы, отдельной строки нет"),
    Break("remi", "«Реми» (67 %, Дальний Восток)", date(2025, 12, 3),
          "контроль с 03.12.2025 (МСФО FY2025; inorganic.json deals.remi); строка Remi "
          "датабука с 4 кв. 2025, вне LFL до 12.2026"),
    Break("domlenta", "«Дом Лента» (бывш. OBI Россия)", date(2026, 1, 1),
          "поэтапно январь–март 2026 (МСФО 6М2026; inorganic.json deals.obi_domlenta); "
          "строка Dom Lenta датабука с 1 кв. 2026 — разрыв датирован началом квартала"),
    Break("okey", "гипермаркеты «О'КЕЙ» (75)", date(2026, 6, 2),
          "контроль с 02.06.2026 (МСФО 6М2026, прим. 5; inorganic.json deals.okey); "
          "отдельной строки в датабуке нет — июнь внутри Hypermarkets"),
)


def breaks(facts_dir: Path | None = None) -> tuple[Break, ...]:
    """Разрывы периметра: файл фактов, если он есть, иначе список по умолчанию."""
    path = Path(facts_dir or FACTS_DIR) / BREAKS_FILE
    if not path.exists():
        return DEFAULT_BREAKS
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA:
        raise ValueError(f"{path.name}: схема {data.get('schema')!r}, ожидается {SCHEMA}")
    return tuple(Break(id=str(b["id"]), name=str(b["name"]),
                       control=date.fromisoformat(str(b["control"])), basis=str(b["basis"]))
                 for b in data["breaks"])


def _per_year(period: str) -> int:
    return 4 if periods.is_quarter(period) else 2 if periods.is_half(period) else 1


def method_steps(method: str, period: str) -> tuple[int, ...]:
    """Опорные периоды эталона как сдвиги от прогнозируемого периода.

    Пустой кортеж — эталон на текущем периметре (ожидание модели, гайденс):
    разрывом он не ломается.
    """
    y = _per_year(period)
    steps = {
        "yoy_plus_shift": (-1, -y, -y - 1),
        "yoy_growth_carried": (-1, -y, -y - 1),
        "seasonal_naive": (-y,),
        "last_period": (-1,),
        "mean_of_year": tuple(range(-1, -y - 1, -1)),
        "previous_period_scaled_by_rates": (-1,),
    }
    return steps.get(method, ())


def broken_by(period: str, method: str, *, items: tuple[Break, ...] | None = None) -> list[Break]:
    """Разрывы, ломающие сравнение периода с опорными периодами эталона."""
    steps = method_steps(method, period)
    if not steps or not periods.is_period(period):
        return []
    earliest = periods.shift(period, min(steps))
    low = periods.bounds(earliest)[0]
    high = periods.bounds(period)[1]
    return [b for b in (items if items is not None else breaks()) if low < b.control <= high]


def level_broken_by(period: str, *, items: tuple[Break, ...] | None = None) -> list[Break]:
    """Разрывы ВНУТРИ периода: консолидация не с первого дня (неполный период)."""
    if not periods.is_period(period):
        return []
    low, high = periods.bounds(period)
    return [b for b in (items if items is not None else breaks()) if low < b.control <= high]
