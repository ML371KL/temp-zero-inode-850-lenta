# -*- coding: utf-8 -*-
"""Общая обвязка прогона: состояние из фикстур и запрет тихого пропуска.

Аудит второй итерации нашёл две связанные вещи (находки 1 и B3).

**Тесты читали собранные данные.** Три теста канала процентов падали в CI,
потому что смотрели в `var/` — каталог, который на раннере GitHub пуст, а на
машине разработчика полон. Красный CI означал не дефект, а географию прогона.
Здесь состояние тестов уводится в отдельный каталог, собираемый из фикстур:
прогон не зависит от того, запускали ли на этой машине сбор.

**Пропуск выдавался за успех.** Два теста связи книги с фактами искали
несуществующие ключи и «честно пропускались» — то есть были выключены
навсегда, а конвейер считал это зелёным. Ниже пропуск, объявленный ВО ВРЕМЯ
исполнения теста, становится ошибкой прогона. Пропускать можно только
маркером (`network`, `needs_book`), и тогда это видно в команде запуска, а не
спрятано в теле теста.
"""
from __future__ import annotations

# Прогон «в будущем» (`FAKE_TODAY=ГГГГ-ММ-ДД`): подмена «сегодня» обязана
# встать ДО импорта модели и слоя индикаторов — и до `from datetime import
# date` ниже. Без переменной окружения вызов ничего не делает. Зачем это нужно
# и как устроено — в докстроке `tests/fakedate_plugin.py`.
from tests import fakedate_plugin as _fakedate  # noqa: E402  (порядок важен)

_fakedate.install()

import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
from contextlib import contextmanager  # noqa: E402
from datetime import date, timedelta  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"

# Каталог состояния для тестов. Не `var/`: там лежит собранный архив, и тест,
# случайно на него опершийся, зеленеет только на этой машине.
STATE = ROOT / "var" / "test-state"


def _expand_key_rate(decisions: list, last_day: date) -> list[dict]:
    """График решений ЦБ → посуточный ряд.

    Ставка действует со дня решения до дня следующего решения. Разворачивать
    обязательно: `_average_key_rate` считает среднюю как среднее ТОЧЕК ряда, а
    не взвешенную по дням, и на 66 ступенях вместо 3 262 дней она дала бы
    другое число. Проверка развёртки — `test_key_rate_fixture_matches_the_cbr_schedule`.
    """
    fetched = f"{last_day.isoformat()}T08:08:34+00:00"
    points = []
    for index, (day, rate) in enumerate(decisions):
        start = date.fromisoformat(day)
        stop = (date.fromisoformat(decisions[index + 1][0]) - timedelta(days=1)
                if index + 1 < len(decisions) else last_day)
        current = start
        while current <= stop and current <= last_day:
            points.append({"period": current.isoformat(), "value": rate,
                           "fetched_at": fetched, "status": "ok",
                           "source_sha256": "", "note": ""})
            current += timedelta(days=1)
    return points


def _build_state() -> None:
    """Собирает каталог состояния тестов из фикстур. Идемпотентно."""
    series_dir = STATE / "indicators" / "series"
    series_dir.mkdir(parents=True, exist_ok=True)
    (STATE / "release").mkdir(parents=True, exist_ok=True)

    spec = json.loads((FIXTURES / "cbr_key_rate_decisions.json").read_text(encoding="utf-8"))
    points = _expand_key_rate(spec["decisions"], date.fromisoformat(spec["last_observed"]))
    (series_dir / "cbr.key_rate.json").write_text(json.dumps({
        "id": spec["series_id"], "unit": spec["unit"], "cadence": "ежедневно",
        "label": "Ключевая ставка Банка России", "channel": 1,
        "updates": spec["source"], "points": points,
    }, ensure_ascii=False), encoding="utf-8")


def pytest_report_header(config):
    """Прогон «в будущем» обязан быть виден в шапке, а не только в окружении."""
    return _fakedate.report_header()


def pytest_configure(config):
    """Состояние — в фикстурный каталог, настоящее — под отдельным именем.

    Выполняется ДО импорта тестовых модулей, поэтому `DEFAULT_ROOT` в
    `indicators/store.py` (константа уровня модуля) видит уже подменённый путь.
    """
    _build_state()
    os.environ["LENTA_STATE_DIR"] = str(STATE)
    # Рабочие процессы полосы читают код с диска и не видят monkeypatch и
    # мутантов: весь прогон — последовательно, пул включает `parallel_band`.
    # Кэш медленных блоков на диске сверку бы подменял — его нет, кроме его теста.
    _OUTER_WORKERS[0] = os.environ.get(WORKERS_ENV, "auto")
    os.environ[WORKERS_ENV] = "1"
    os.environ.pop(SLOW_CACHE_ENV, None)
    from model.book import BOOK_YAML

    # Без книги (репозиторий до книги 1.0: тесты `needs_book` исключены) полного
    # выпуска быть не может — сторожить нечего.
    if re.search(r"\bnot\s+ci_only\b", config.getoption("markexpr") or "") and BOOK_YAML.exists():
        _forbid_the_full_release()


WORKERS_ENV, SLOW_CACHE_ENV = "LENTA_WORKERS", "LENTA_SLOW_CACHE"
_OUTER_WORKERS = ["auto"]             # LENTA_WORKERS окружения, из которого запущен прогон


@contextmanager
def _parallel_band():
    before = os.environ.get(WORKERS_ENV)
    os.environ[WORKERS_ENV] = _OUTER_WORKERS[0]
    try:
        yield
    finally:
        os.environ[WORKERS_ENV] = before if before is not None else "1"


@pytest.fixture(scope="session")
def parallel_band():
    """Контекст, в котором полоса считается пулом (`LENTA_WORKERS` окружения
    прогона, по умолчанию auto). Только для полного расчёта на НЕИЗМЕНЁННОМ
    коде и книге без monkeypatch ядра: рабочим видны лишь код и файлы на диске."""
    return _parallel_band


FULL_RELEASE_IN_THE_TACT = (
    "полный выпуск (полоса на числе прогонов книги, обратный DCF медианы, таблица "
    "суждений) в прогоне без `ci_only`: суточный такт его не собирает — его собирает "
    "и проверяет сама сборка. Пометьте тест `@pytest.mark.ci_only` или соберите "
    "быстрый выпуск (`build_payload(..., with_slow=False)`)")


def _forbid_the_full_release() -> None:
    """Сторож такта: в прогоне, исключающем `ci_only`, полный выпуск не считается.

    Медленные блоки кэшируются на процесс (`model.payload.slow_blocks`), поэтому
    один непомеченный тест полного выпуска вернул бы в такт всю их цену, и
    увидеть это можно было бы только по часам. Импорт модели — после подмены
    каталога состояния: `DEFAULT_ROOT` слоя индикаторов читается при импорте.
    """
    from model import payload, uncertainty
    from model.book import book

    full = int(book()["valuation"]["uncertainty"]["draws"])
    band, rows = uncertainty.uncertainty, uncertainty.band_rows

    def no_slow_blocks(A):
        raise AssertionError(FULL_RELEASE_IN_THE_TACT)

    # Полоса на полном числе прогонов считается и мимо `uncertainty` — уточнением
    # корня медианы (`refine_on_full_band`) — поэтому сторож стоит и на прогонах.
    def small_rows_only(A, n):
        if n >= full:
            raise AssertionError(FULL_RELEASE_IN_THE_TACT)
        return rows(A, n)

    def small_band_only(A, draws=None):
        try:
            n = int(draws or A["valuation"]["uncertainty"]["draws"])
        except (KeyError, TypeError, ValueError):
            n = 0                      # книга без полосы: откажет сама `uncertainty`
        if n >= full:
            raise AssertionError(FULL_RELEASE_IN_THE_TACT)
        return band(A, draws)

    payload.slow_blocks = no_slow_blocks
    uncertainty.uncertainty = small_band_only
    uncertainty.band_rows = small_rows_only


# Маркеры, под которыми пропуск ЗАКОНЕН: они объявлены в `pytest.ini`, видны
# в команде запуска (`-m "not network and not needs_book"`) и означают «этому
# тесту нужна среда, которой здесь нет» (сеть; книга допущений в data/).
SKIP_MARKERS = ("network", "needs_book")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Пропуск не по маркеру — ошибка, а не успех. НА ЛЮБОЙ ФАЗЕ.

    Прежняя версия смотрела только фазу `call`, и в обход оставались два
    входа, которыми пропуск выдавался за успех (аудит третьей итерации, B6):

    * `pytest.skip()` ВНУТРИ ФИКСТУРЫ — пропуск объявляется на фазе `setup`,
      тело теста не исполняется вовсе;
    * `@pytest.mark.skipif(...)` — тоже фаза `setup`, причём условие может
      стать вечно-истинным от любой правки окружения.

    Проба аудитора (`pytest.skip` в фикстуре и `skipif` рядом) дала `Fss` при
    коде выхода 0: две проверки были выключены, а прогон считался зелёным.

    Законный пропуск отличается ровно одним признаком — маркером из
    `SKIP_MARKERS` на самом тесте. Его видно в `-m`, то есть в команде
    запуска, а не в теле теста.
    """
    outcome = yield
    report = outcome.get_result()
    if not report.skipped:
        return
    if any(item.get_closest_marker(name) for name in SKIP_MARKERS):
        return
    report.outcome = "failed"
    report.longrepr = (
        f"{item.nodeid}: тест пропущен на фазе «{report.when}» без маркера "
        f"{'/'.join(SKIP_MARKERS)}. Пропуск неотличим от успеха и однажды уже "
        "выключил две проверки навсегда; в фикстуре и в `skipif` он вдобавок "
        "не виден в теле теста. Если тесту нужна среда, которой здесь нет — "
        "маркер `network` или `needs_book` и явное исключение в команде запуска; "
        "если данные можно положить фикстурой — положите фикстуру."
    )

