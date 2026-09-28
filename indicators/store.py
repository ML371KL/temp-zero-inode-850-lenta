"""Хранилище: сырой архив винтажей и нормализованные ряды.

**Главный класс ловушек этого проекта — point-in-time.** Databook
перезаписывается вместе с пересчитанной историей; Росстат держит на сайте
только два последних месячных файла; СберИндекс дважды за 2025 год переписал
всю историю недельного ряда; у источников, отдающих только текущее
состояние (вакансии, снимки консенсуса), истории нет вообще. Модель, обученная на сегодняшней выгрузке,
видит цифры, которых на дату не существовало.

Поэтому хранилище двухслойное:

```
raw/<source>/<YYYY-MM-DD>/<name>.<ext>   сырой ответ как есть + .meta.json
series/<series_id>.json                  нормализованный ряд
```

Сырой слой неизменяем: перезапись существующего файла запрещена. Если в тот
же день источник ответил иначе — рядом ляжет вторая копия с суффиксом.
Нормализованный слой хранит для каждой точки дату периода И дату получения,
поэтому `value_as_of` отвечает на вопрос «что было известно на дату» —
без него ни один бэктест не честен.

Различие «фетч упал» и «данных нет» хранится явно: `status` у точки.
`null ≠ 0` и здесь.
"""

from __future__ import annotations

import gzip
import json
import math
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# На проде состояние живёт в StateDirectory (`/var/lib/lenta-850`), а не в
# репозитории: бюджет разделяет код с venv и состояние, и сырой архив в рабочем
# дереве мешал бы `git reset --hard` при обновлении кода. Локально переменной
# нет — работает путь внутри репозитория.
DEFAULT_ROOT = (Path(os.environ["LENTA_STATE_DIR"]) / "indicators"
                if os.environ.get("LENTA_STATE_DIR")
                else Path(__file__).resolve().parents[1] / "var" / "indicators")


# Источники без истории: пропущенный день не добирается никогда. Ротация их
# не трогает. Список держится здесь, а не в вызывающем коде, чтобы забыть его
# было нельзя.
# Крупные ответы кладутся сжатыми (у 850oa вакансии давали ~10 МБ в сутки без
# сжатия и ~1,5 МБ со сжатием, а ротации не подлежали — без сжатия потолок
# состояния кончился бы за считаные месяцы). Порог не нулевой: у мелких ответов gzip
# съедает больше на заголовке, чем экономит, а имена усложняются на ровном месте.
COMPRESS_ABOVE_BYTES = 32 * 1024
GZIP_LEVEL = 6
GZIP_MAGIC = bytes((0x1F, 0x8B))

# Потолок состояния (`/var/lib/lenta-850`). Недельный прирост печатает `health`.
#
# Число живёт ЗДЕСЬ в одном экземпляре: его читает команда `health`, а
# `ops/README.md` цитирует его же. Ротация (`prune_raw`) к нему не привязана:
# у незащищённых источников свой бюджет — окно `keep_days`. (Значение — как у
# 850oa; бюджет Ленты — 1 ГБ сырого архива, пересмотреть с первыми сборщиками
# эмитента.)
STATE_CEILING_BYTES = 2_000_000_000

# Источники, сырое которых ротация не трогает НИКОГДА: версии датабука
# (старая версия со страницы исчезает), вакансии «Работы России» (у источника
# нет истории) и лента раскрытия (тела сообщений читаются по три за такт —
# удалённое тело пришлось бы запрашивать снова). Имена — сборщиков
# `indicators.sources`; тест держит их в реестре.
PROTECTED_SOURCES: frozenset[str] = frozenset(
    {"lenta_databook", "trudvsem_vacancies", "lenta_disclosure"})


@dataclass(frozen=True)
class Point:
    """Одно наблюдение ряда."""

    period: str
    """Дата или метка периода, к которому ОТНОСЯТСЯ данные."""
    value: float | None
    fetched_at: str
    """Когда получено — это НЕ то же самое, что период."""
    status: str = "ok"
    """ok | missing | failed — «данных нет» и «не смогли взять» разные вещи."""
    source_sha256: str = ""
    note: str = ""


# Пересчёт индекса даёт то же число в последнем бите мантиссы — это не новая
# версия наблюдения, а шум представления. Без порога недельный пересчёт
# зарплатного индекса плодил бы винтаж на каждом прогоне.
SAME_VALUE_RELATIVE = 1e-12


def _same_observation(first: Point, second: Point) -> bool:
    """Говорят ли две точки одно и то же о своём периоде."""
    if first.status != second.status:
        return False
    if first.value is None or second.value is None:
        return first.value is second.value
    scale = max(abs(first.value), abs(second.value), 1.0)
    return abs(first.value - second.value) <= SAME_VALUE_RELATIVE * scale


@dataclass
class Series:
    id: str
    unit: str
    cadence: str
    label: str
    channel: int
    """Канал по разделу 6.2 брифа: 1 — проценты и долг, 2 — персонал,
    3 — чек и трафик, 7 — событийный слой."""
    updates: str = ""
    """Какое допущение книги обновляет (раздел 15 книги)."""
    points: list[Point] = field(default_factory=list)

    def latest(self) -> Point | None:
        ok = [(p.period, p.fetched_at, order, p)
              for order, p in enumerate(self.points) if p.status == "ok"]
        return max(ok)[3] if ok else None

    def value_as_of(self, period: str, as_of: str) -> float | None:
        """Что было известно про `period` на дату `as_of`.

        Берётся последняя версия точки, полученная НЕ ПОЗЖЕ `as_of`. Именно
        так и только так строится честный бэктест: сегодняшняя выгрузка знает
        ревизии, которых на дату не было.

        **При РАВНЫХ `fetched_at` побеждает записанная последней** (доделка
        приёмки, S1.5). С тех пор как винтажом зарплатного индекса стал день
        обхода, равные метки стали штатными: повторный `salary-index` в тот же
        день кладёт рядом вторую точку — и прежний `max` возвращал ПЕРВУЮ,
        потому что при равенстве ключа он отдаёт раннего кандидата. Вечерний
        пересчёт после добора локаций в нау-каст не попадал: ряд отвечал
        утренним уровнем, посчитанным по неполному дню.

        Порядок в списке — это порядок записи: `save` сортирует точки
        устойчиво, а `upsert` дописывает новую в конец.
        """
        candidates = [(p.fetched_at, order, p) for order, p in enumerate(self.points)
                      if p.period == period and p.fetched_at[:10] <= as_of and p.status == "ok"]
        if not candidates:
            return None
        return max(candidates)[2].value

    def history(self, as_of: str | None = None) -> dict[str, float]:
        """Ряд, каким он был виден на дату (по умолчанию — сейчас).

        То же правило, что `value_as_of` для каждого периода (последняя версия,
        полученная не позже `as_of`; при равных `fetched_at` — записанная
        последней), но ОДНИМ проходом. Прежняя запись звала `value_as_of` на
        каждый период, а та просматривала весь ряд: на посуточной ключевой
        ставке (≈4 700 точек) это 22 млн сравнений на вызов, и ретро-проверка
        процентов (38 вызовов) стоила сборке выпуска ≈40 секунд (замер потока
        R2, 24.09.2026). Равенство двух записей держит тест.
        """
        as_of = as_of or date.today().isoformat()
        best: dict[str, tuple] = {}
        for order, p in enumerate(self.points):
            if p.status != "ok" or p.fetched_at[:10] > as_of:
                continue
            key = (p.fetched_at, order)
            held = best.get(p.period)
            if held is None or key > held[0]:
                best[p.period] = (key, p.value)
        return {period: best[period][1] for period in sorted(best)
                if best[period][1] is not None}


def _finite(point: Point) -> bool:
    return point.value is None or math.isfinite(point.value)


class Store:
    def __init__(self, root: Path | None = None):
        self.root = Path(root or DEFAULT_ROOT)
        self.raw = self.root / "raw"
        self.series_dir = self.root / "series"
        self.rejected: list[str] = []
        """Отброшенные при приёме точки («ряд период: значение»): сборщики
        превращают их в статус «ОШИБКА» источника, то есть в деградацию сбора."""

    # ------------------------------------------------------------ сырой слой

    def save_raw(self, source: str, name: str, body: bytes, *, url: str,
                 fetched_at: str, sha256: str, day: str | None = None,
                 extra: dict | None = None) -> Path:
        """Сохраняет сырой ответ. Существующий файл НЕ перезаписывается.

        `sha256` — контрольная сумма ИСХОДНОГО содержимого ответа. Если на диск
        кладётся сжатая копия, это надо отметить в `extra` (`stored_encoding`),
        иначе проверка «файл против меты» будет сравнивать разные вещи.
        """
        day = day or datetime.now(timezone.utc).date().isoformat()
        directory = self.raw / source / day
        directory.mkdir(parents=True, exist_ok=True)

        # sha256 в мете — сумма ИСХОДНОГО содержимого ответа, даже если на диск
        # легла сжатая копия: по ней ответ опознаётся, а не файл.
        extra = dict(extra or {})
        if len(body) >= COMPRESS_ABOVE_BYTES and not name.endswith(".gz"):
            # mtime=0: иначе gzip кладёт в заголовок текущее время, один и тот же
            # ответ каждый раз даёт разные байты, и повтор прогона плодит копии.
            body = gzip.compress(body, GZIP_LEVEL, mtime=0)
            name += ".gz"
            extra["stored_encoding"] = "gzip"

        path = directory / name
        if path.exists() and path.read_bytes() != body:
            # Источник ответил в тот же день иначе — кладём рядом копию с
            # номером. Но СНАЧАЛА проверяем уже лежащие номера: без этого
            # повторный прогон не находил «своё» тело среди копий, брал первое
            # СВОБОДНОЕ имя и добавлял ещё одну. Каждый повтор — плюс файл, и
            # архив рос от самого факта перезапуска. Поймано на переносе архива
            # 850cl: два ответа repowatch за один день давали +2 файла за прогон.
            stem, suffix = path.stem, path.suffix
            index = 1
            while path.exists():
                if path.read_bytes() == body:
                    return path
                path = directory / f"{stem}.{index}{suffix}"
                index += 1
        elif path.exists():
            return path

        path.write_bytes(body)
        meta = dict(url=url, fetched_at=fetched_at, sha256=sha256, bytes=len(body))
        meta.update(extra)
        path.with_suffix(path.suffix + ".meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
        return path

    @staticmethod
    def read_raw(path: Path) -> bytes:
        """Читает сырой файл, снимая сжатие. Знание о `.gz` живёт здесь одно."""
        body = path.read_bytes()
        # Подпись gzip — два байта 0x1f 0x8b; пишем их числами, а не escape-
        # последовательностью, чтобы правка файла не могла превратить их в текст.
        return gzip.decompress(body) if body.startswith(GZIP_MAGIC) else body

    def raw_days(self, source: str) -> list[str]:
        directory = self.raw / source
        if not directory.exists():
            return []
        return sorted(p.name for p in directory.iterdir() if p.is_dir())

    def raw_size_bytes(self) -> int:
        if not self.raw.exists():
            return 0
        return sum(p.stat().st_size for p in self.raw.rglob("*") if p.is_file())

    # ------------------------------------------------------ нормализованный

    def path_for(self, series_id: str) -> Path:
        return self.series_dir / f"{series_id}.json"

    def load(self, series_id: str) -> Series | None:
        path = self.path_for(series_id)
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        return Series(
            id=raw["id"], unit=raw["unit"], cadence=raw["cadence"], label=raw["label"],
            channel=raw.get("channel", 0), updates=raw.get("updates", ""),
            points=[Point(**p) for p in raw["points"]],
        )

    def save(self, series: Series) -> Path:
        """Атомарная запись: сначала во временный файл, потом os.replace."""
        self.series_dir.mkdir(parents=True, exist_ok=True)
        path = self.path_for(series.id)
        payload = dict(
            id=series.id, unit=series.unit, cadence=series.cadence, label=series.label,
            channel=series.channel, updates=series.updates,
            points=[asdict(p) for p in sorted(series.points, key=lambda p: (p.period, p.fetched_at))],
        )
        tmp = path.with_suffix(".tmp")
        # NaN и ±Infinity в архив не пишутся: строгий JSON их не знает, а из
        # ряда они уехали бы в выпуск.
        body = json.dumps(payload, ensure_ascii=False, indent=1, allow_nan=False)
        tmp.write_text(body, encoding="utf-8", newline="\n")
        os.replace(tmp, path)
        return path

    def upsert(self, series_id: str, points: Iterable[Point], *, unit: str = "",
               cadence: str = "", label: str = "", channel: int = 0,
               updates: str = "") -> Series:
        """Добавляет точки, НЕ затирая прежние версии.

        Ретро-правка источника не стирает то, что было известно раньше: точка
        с тем же периодом, но ДРУГИМ ЗНАЧЕНИЕМ, ложится рядом. Иначе слой
        винтажей теряет смысл.

        Точка отбрасывается, только если СОСЕДНЯЯ ПО ВРЕМЕНИ ПОЛУЧЕНИЯ точка
        того же периода говорит то же самое. Раньше ключом была пара «период +
        `fetched_at`», и каждый суточный прогон дописывал весь запрошенный год
        заново: ряд ключевой ставки набрал 7 559 точек на 3 262 календарных
        дня и рос на 365 точек в сутки. Вторая итерация починила это
        сравнением с ПОСЛЕДНЕЙ известной точкой — и тем самым сломала загрузку
        архива задним числом (аудит, C6).

        **Почему сосед, а не последний.** Значение, импортированное задним
        числом и равное сегодняшнему, говорит не «ничего нового», а «это было
        известно РАНЬШЕ». Ровно на этом держится `value_as_of`: архив линии
        850cl за 03–18.09.2026 импортировался ПОСЛЕ того, как живой сборщик
        уже записал те же уровни, поэтому сравнение с последним отбрасывало
        весь архив, и ряды за те дни оставались пустыми. Проверка приёмки —
        `value_as_of` на дату внутри 03–18.09.2026 обязан вернуть архивное
        значение.

        A→A→A по-прежнему становится одной точкой, A→B→A остаётся тремя, а
        A(поздно) + A(рано) — двумя: вторая датирует знание.

        Неконечное значение (NaN, ±Infinity) не принимается: точка
        отбрасывается и записывается в `rejected` — наблюдения за период нет,
        ряд живёт на прежних точках, а сбор называет ряд и период.
        """
        series = self.load(series_id) or Series(
            id=series_id, unit=unit, cadence=cadence, label=label,
            channel=channel, updates=updates)
        for point in [p for p in series.points if not _finite(p)]:
            self._reject(series_id, point)
        series.points = [p for p in series.points if _finite(p)]
        for point in points:
            if not _finite(point):
                self._reject(series_id, point)
                continue
            same_period = sorted((p for p in series.points if p.period == point.period),
                                 key=lambda p: p.fetched_at)
            earlier = [p for p in same_period if p.fetched_at <= point.fetched_at]
            neighbour = earlier[-1] if earlier else None
            if neighbour is not None and _same_observation(neighbour, point):
                continue
            series.points.append(point)
        self.save(series)
        return series

    def _reject(self, series_id: str, point: Point) -> None:
        line = f"{series_id} {point.period}: {point.value!r} — не конечное число, точка отброшена"
        self.rejected.append(line)
        print(f"ОТБРОШЕНО: {line}", file=sys.stderr)

    def compact(self, series_id: str) -> tuple[int, int]:
        """Убирает из ряда повторы значения за один период.

        Нужен один раз — разобрать наследство прежнего правила. Возвращает
        «было, стало». Выбрасываются только повторы ПОДРЯД: точка, чьё
        значение совпадает с последним известным за тот же период. Смена
        значения туда и обратно сохраняется целиком — это правка источника,
        а не шум.
        """
        series = self.load(series_id)
        if not series:
            return (0, 0)
        before = len(series.points)
        kept: list[Point] = []
        previous: dict[str, Point] = {}
        # Порядок — по времени ПОЛУЧЕНИЯ внутри периода: «подряд» означает
        # соседство во времени наблюдения, а не в порядке записи в файл.
        for point in sorted(series.points, key=lambda p: (p.period, p.fetched_at)):
            was = previous.get(point.period)
            if was is not None and _same_observation(was, point):
                continue
            previous[point.period] = point
            kept.append(point)
        series.points = kept
        self.save(series)
        return (before, len(kept))

    def all_series(self) -> list[Series]:
        if not self.series_dir.exists():
            return []
        out = []
        for path in sorted(self.series_dir.glob("*.json")):
            series = self.load(path.stem)
            if series:
                out.append(series)
        return out

    # ------------------------------------------------------------ ротация

    def prune_raw(self, keep_days: int = 400,
                  protected: Iterable[str] = PROTECTED_SOURCES, *,
                  today: date | None = None) -> list[str]:
        """Ротация сырого архива: дни незащищённых источников старше `keep_days`.

        Удаляются дни целиком, а не выборочные файлы: полудень без части
        источников хуже, чем отсутствующий день.

        `protected` — источники, которые ротация не трогает НИКОГДА. Это не
        осторожность, а разница в природе данных: вакансии, Чек Индекс, лента
        раскрытия и версии Databook не имеют истории у источника. Удалённый
        день такого источника не докачивается ничем и никогда; удалённый день
        кривой ЦБ докачивается одним запросом.

        Потолок состояния (`STATE_CEILING_BYTES`) ротация НЕ применяет (аудит
        26.09.2026, п. 5.7). Прежде она сравнивала с ним ВЕСЬ архив, а удалять
        могла только незащищённые дни: когда защищённые источники сами дошли
        бы до потолка, каждый прогон удалял бы все сырые дни ISS, ЦБ и
        Росстата, включая сегодняшний, а архив остался бы выше потолка. Бюджет
        незащищённых источников — их собственный: окно `keep_days` (при
        нынешнем темпе ≈0,1 ГБ), и дни моложе него не удаляются никогда.
        Превышение потолка — тревога `collect health` и решение владельца.
        """
        removed: list[str] = []
        if not self.raw.exists():
            return removed
        protected = set(protected)
        today = today or date.today()
        days: list[tuple[str, Path]] = []
        for source_dir in self.raw.iterdir():
            if source_dir.is_dir() and source_dir.name not in protected:
                days += [(d.name, d) for d in source_dir.iterdir() if d.is_dir()]
        for day, path in sorted(days):
            if (today - date.fromisoformat(day)).days <= keep_days:
                continue
            for file in sorted(path.rglob("*"), reverse=True):
                file.unlink() if file.is_file() else file.rmdir()
            path.rmdir()
            removed.append(f"{path.parent.name}/{day}")
        return removed
