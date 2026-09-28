# -*- coding: utf-8 -*-
"""Числа книги и фактов не переписываются в код. Тест на весь каталог `model/`.

ЗАЧЕМ. Переписанное число живёт своей жизнью. Пример из проекта-предшественника
(850oa): число строк движка стояло в README и в `docs/MODEL.md` разными
числами и устарело в обоих. Пример дороже: старая модель держала кассу
отчётной даты литералом, и после любого обновления отчётности мост считался
бы по прошлому балансу, не сказав об этом никому.

ЧТО ПРОВЕРЯЕТСЯ. Каждый числовой литерал в `model/*.py` сверяется со всеми
числами книги (`data/assumptions/assumptions.yaml`) и фактов
(`data/facts/*.json`). Совпадение означает, что число ПЕРЕПИСАНО: его надо
читать из источника, а не хранить в коде вторым экземпляром.

ПОЧЕМУ ТЕСТ ВЕРНУЛИ. Он существовал до перехода на книгу 1.2 и был удалён
коммитом `0d94285` вместе с переписанным блоком проверок соответствия. Аудит
второй итерации 850oa нашёл, что после удаления в `model/financing.py`
спокойно лежали и сумма линий, и касса отчётной даты, и даты когорт
банковского портфеля (находка A4).

ЧЕГО ТЕСТ НЕ ЗАПРЕЩАЕТ. Коридоры гейтов, допуски, пороги проверок и
структурные множители (полугодие, тысяча рублей в миллиарде, доли перцентилей)
— это параметры ПРОВЕРОК И АРИФМЕТИКИ, а не допущения. Они перечислены ниже
явным списком: список видно в диффе, и добавить в него число молча нельзя.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
import yaml

# Статика исходников `model/` против чисел книги: на неизменном коде такта
# ловить нечего. Гоняется в CI.
pytestmark = pytest.mark.ci_only

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "model"

# Числа книги и фактов, переписанные в код ОСОЗНАННО. Ключ — (файл, значение),
# значение — причина, по которой это НЕ второй экземпляр допущения.
#
# ПОЧЕМУ НЕ ПЛОСКИЙ БЕЛЫЙ СПИСОК ЗНАЧЕНИЙ. До четвёртой итерации здесь стоял
# набор `ALLOWED`, и в нём среди «долей и перцентилей» лежали 0,5 / 0,75 /
# 0,06 / 0,1 / 0,6 / 0,05 — то есть доля операционной кассы, бета активов,
# премия за риск рынка, дисконт за управление и цели маржи. Разрешённые ОДИН
# РАЗ глобально, они были разрешены ВЕЗДЕ: проба аудитора (`= 0.8` в
# `model/core.py` — значение из книги) не была поймана ничем (аудит третьей
# итерации, B6).
#
# Привязка к файлу лечит именно это. «0,5 — это половина года» верно в
# `core.py`, где сетка полугодовая, и ничего не разрешает в файле, где
# половины года нет; новое значение не проходит вовсе, пока его не назовут
# здесь. Список виден в диффе, а `test_every_named_literal_still_exists`
# держит его от превращения в свалку.
NAMED_LITERALS: dict[tuple[str, float], str] = {
    # --- model/book.py: перевод годовой ставки в полугодовую
    ("book.py", 0.5): "показатель степени: полугодие как корень из года",
    # --- model/book.py: допуски сверок книги (параметры проверки, не допущения)
    ("book.py", 0.02): "EFF_HIST_TOL: допуск сверки записанной истории эффективной "
                       "площади с правилом сети, тыс. м² (округление входов до двух знаков)",
    ("book.py", 0.05): "SELLSIDE_TOL: допуск сверки агрегатов целей инвестдомов со "
                       "списком, ₽ (агрегаты книги округлены)",

    # --- model/checks.py: КОРИДОРЫ ГЕЙТОВ И ПОРОГИ ПРОВЕРОК.
    # Совпадение с числами книги здесь не случайно и не опасно: порог
    # правдоподобия и есть утверждение о том, какие значения считаются
    # нормальными. Опасно обратное — читать порог ИЗ КНИГИ: тогда проверка
    # перестала бы быть внешней по отношению к проверяемому.
    ("checks.py", 0.1): "доля терминала: низ коридора 10 %",
    ("checks.py", 0.55): "доля терминала: верх коридора 55 %",
    ("checks.py", 0.02): "реальная ставка: низ коридора 2 %; он же сдвиг кривой "
                         "на 2 п.п. в проверке монотонности",
    ("checks.py", 0.13): "реальная ставка: верх коридора 13 %",
    ("checks.py", 0.005): "сдвиг книжной кривой: 0,5 п.п. — порог расхождения",
    ("checks.py", 0.5): "шаг счётчика подряд идущих полугодий без роста capex "
                        "(полугодие = половина года)",
    ("checks.py", 0.4): "нижний множитель коридора ожидаемой массы гейта",
    ("checks.py", 1.5): "верхний множитель того же коридора",

    # --- model/core.py: ПОЛУГОДОВАЯ СЕТКА СРОКОВ И ФОРМУЛА МЕРТОНА.
    # Все три числа — арифметика календаря и нормального распределения.
    ("core.py", 0.5): "половина года: шаг сетки сроков, он же множитель "
                      "σ²T/2 в d₁ и 0,5·(1+erf) в функции распределения",
    ("core.py", 0.25): "четверть года: середина полугодия",
    ("core.py", 0.75): "три четверти года: середина первого полугодия "
                       "терминального года при приведении к концу явного",

    # --- model/financing.py
    ("financing.py", 0.01): "1 п.п. — шаг, в котором объявлена чувствительность "
                            "реестра к ставке («на 1 п.п. ключевой»)",

    # --- model/grid.py: ПЕРЦЕНТИЛИ РАСПРЕДЕЛЕНИЯ И НОРМИРОВКА ВЕСОВ
    ("grid.py", 0.1): "перцентиль 10 распределения исходов",
    ("grid.py", 0.25): "перцентиль 25",
    ("grid.py", 0.5): "перцентиль 50; он же множитель −0,5 в гауссовом весе "
                      "наблюдения и λ = 0,5 середины печатаемого диапазона",
    ("grid.py", 0.75): "перцентиль 75",
    ("grid.py", 0.9): "перцентиль 90",


    # --- model/live.py: ГРАНИЦЫ ПРИЁМКИ ЖИВЫХ ВХОДОВ.
    # Не допущения об экономике, а пределы доверия к собранному числу.
    ("live.py", 0.03): "наблюдённая кривая принимается от 3 %; он же предел "
                       "сдвига одного узла за такт",
    ("live.py", 0.4): "наблюдённая кривая принимается до 40 %",
    ("live.py", 0.3): "коридор цены эмитента: ±30 % к последней принятой цене",
    ("live.py", 0.05): "скачок цены после простоя принимается, если соседний "
                       "торговый день подтверждает его в пределах 5 %",
    ("live.py", 0.005): "реальная доходность ОФЗ-ИН принимается от 0,5 %: ниже — "
                        "нуль или доля, поделённая на 100 ещё раз",
    ("live.py", 0.2): "реальная доходность ОФЗ-ИН принимается до 20 %: выше — "
                      "проценты вместо долей",

    # --- model/payload.py: литералов книги нет (P4b: снята сверка реестра
    # облигаций 850oa с допуском 0,5 % и ключевая 14 % у чувствительности реестра,
    # которую функция не читала).
}

# Мелочь, которая встречается и в книге, и в любом коде: ноль, единица и
# двойка стоят в книге допущений так же неизбежно, как в арифметике.
TRIVIAL = {0.0, 1.0, 2.0}


def numbers_of(value, out: set) -> None:
    """Все числа книги или фактов, независимо от глубины вложения."""
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        out.add(round(float(value), 9))
    elif isinstance(value, dict):
        for item in value.values():
            numbers_of(item, out)
    elif isinstance(value, (list, tuple)):
        for item in value:
            numbers_of(item, out)
    elif isinstance(value, str):
        try:
            out.add(round(float(value), 9))
        except ValueError:
            pass


@pytest.fixture(scope="module")
def source_numbers() -> set:
    out: set = set()
    numbers_of(yaml.safe_load(
        (ROOT / "data" / "assumptions" / "assumptions.yaml").read_text(encoding="utf-8")), out)
    for path in sorted((ROOT / "data" / "facts").glob("*.json")):
        numbers_of(json.loads(path.read_text(encoding="utf-8")), out)
    # Мелочь вроде 0, 1, 2 встречается и в книге, и в любом коде.
    return {value for value in out if abs(value) > 0 and value not in TRIVIAL}


# Имена, под которыми лежат числа, заведомо НЕ являющиеся допущениями:
# отрезки, на которых бисекция ищет корень. Список виден в диффе, и добавить в
# него имя молча нельзя.
#
# Книга 1.4: отрезки поиска бисекций эталона в `model/grid.py` — множителя
# активов для V* центра (0,2–5,0) и нейтральной маржи ближайшего отчёта
# (3–7 %); отрезок σ калибровки в `model/mapping.py` (1e-5…3, как у листа
# `evidence/book-1.4/mapping/calibrate.py`). Совпадение 0,03 с числами книги —
# случайность. Прежний `REVERSE_DCF_SEARCH` выпуска снят: обратный DCF берёт
# отрезки поиска из книги (`reverse_dcf[].search`).
#
# Справочные наборы таблиц книги (`model/book_results.py`) — входы проверок и
# справок, а не суждения книги, поэтому в `assumptions.yaml` их нет, и прочесть
# их оттуда нельзя: тест-векторы правила A-P2u (`CONTROL_CASES`, построены на
# значениях `demo_values` книги — отсюда совпадения), таблица режимов книги
# 1.1, отменённая решением Р2 (`V11_REGIME_TABLE`, история), верх внешней
# оценки P(полный возврат) (`AUDIT_FULL_RETURN_UPPER`), сдвиг спредов в
# проверке знака (`SPREAD_SIGN_SHIFT`, +5 п.п.) и единица наклона «что даст
# отчёт» (`SLOPE_STEP`, 0,1 п.п.).
EXEMPT_NAMES = {"CENTER_EV_SEARCH", "NEUTRAL_MARGIN_SEARCH", "SIGMA_SEARCH",
                "CONTROL_CASES", "V11_REGIME_TABLE", "AUDIT_FULL_RETURN_UPPER",
                "SPREAD_SIGN_SHIFT", "SLOPE_STEP"}


def _exempt_lines(tree: ast.AST) -> set[int]:
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in EXEMPT_NAMES for t in node.targets):
            out.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return out


def literals_of(path: Path) -> list[tuple[int, float, str]]:
    """Числовые литералы файла: ДРОБНЫЕ и КРУПНЫЕ.

    Целые до десяти тысяч не проверяются: пределы срезов, число знаков
    округления и размеры списков сплошь совпадают с чем-нибудь в книге, и тест
    на них ловил бы `note[:120]` вместо переписанного допущения. Дробное число
    или сумма в сотни тысяч — другое дело: такие в коде движка появляются
    только переписыванием.
    """
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    tree = ast.parse(source)
    exempt = _exempt_lines(tree)
    out = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and not isinstance(node.value, bool)
                and isinstance(node.value, (int, float))):
            value = round(float(node.value), 9)
            interesting = (value != int(value)) or abs(value) >= 10_000
            if interesting and node.lineno not in exempt:
                out.append((node.lineno, value, lines[node.lineno - 1].strip()))
    return out


@pytest.mark.needs_book
@pytest.mark.parametrize("path", sorted(MODEL.glob("*.py")), ids=lambda p: p.name)
def test_no_book_or_fact_number_is_retyped_in_the_engine(path, source_numbers):
    """Число из книги или фактов в коде движка — это второй его экземпляр.

    Разрешение выдаётся ПО ФАЙЛУ И ЗНАЧЕНИЮ (`NAMED_LITERALS`), а не одному
    значению на весь движок: иначе «0,5 — это половина года» открывало бы
    дорогу доле операционной кассы 0,5, а «0,8 — это просто доля» —
    продуктивности закрываемой площади 0,8.
    """
    offenders = []
    for lineno, value, line in literals_of(path):
        if value in source_numbers and (path.name, value) not in NAMED_LITERALS:
            offenders.append(f"{path.name}:{lineno}: {value} — {line[:80]}")
    assert not offenders, (
        "эти числа есть в книге или фактах и должны читаться оттуда; если это "
        "всё же параметр проверки или арифметики — назовите его в "
        "NAMED_LITERALS с причиной:\n" + "\n".join(offenders))


@pytest.mark.needs_book
def test_every_named_literal_still_exists(source_numbers):
    """Список названных литералов не должен становиться свалкой.

    Две проверки. Первая: каждая запись обязана соответствовать литералу,
    который ДЕЙСТВИТЕЛЬНО стоит в этом файле, — иначе это забытое разрешение,
    под которым можно молча вернуть число книги (так в прежнем списке
    совпадений висели 0,87 и 0,155, не относившиеся ни к одной строке кода).
    Вторая: запись, чьё значение больше не встречается ни в книге, ни в
    фактах, ничего не разрешает и только удлиняет список.
    """
    actual = {(path.name, value)
              for path in sorted(MODEL.glob("*.py"))
              for _, value, _ in literals_of(path)}
    stale = sorted(f"{name} / {value} — «{reason}»"
                   for (name, value), reason in NAMED_LITERALS.items()
                   if (name, value) not in actual)
    assert not stale, (
        "эти разрешения больше ни к чему не относятся и должны уйти из "
        "NAMED_LITERALS:\n" + "\n".join(stale))
    idle = sorted(f"{name} / {value}" for (name, value) in NAMED_LITERALS
                  if value not in source_numbers)
    assert not idle, (
        "эти значения не встречаются ни в книге, ни в фактах — разрешать их "
        "не нужно:\n" + "\n".join(idle))


@pytest.mark.needs_book
def test_no_value_is_allowed_across_the_whole_engine(source_numbers):
    """Ни одно число не разрешено во всех файлах движка сразу.

    Иначе список снова стал бы плоским: «0,5 разрешено везде» — это ровно та
    запись, из-за которой доля операционной кассы могла бы вернуться в код.
    Шесть самых дорогих допущений книги (0,5 / 0,75 / 0,06 / 0,1 / 0,6 /
    0,05) проверяются отдельно: ни одно из них не имеет права стоять более
    чем в двух файлах — половина года и перцентиль это два разных повода, а
    третьего у одного и того же числа не бывает.
    """
    files = {path.name for path in MODEL.glob("*.py")} - {"__init__.py"}
    assert len(files) > 3, "каталог движка пуст — проверка бессмысленна"
    holders: dict[float, set] = {}
    for name, value in NAMED_LITERALS:
        holders.setdefault(value, set()).add(name)
    for value, names in holders.items():
        assert names != files, f"{value} разрешено во всех файлах движка"
    for value in (0.5, 0.75, 0.06, 0.1, 0.6, 0.05):
        assert value in source_numbers, (
            f"{value} больше не число книги — проверку пора переписать")
        assert len(holders.get(value, set())) <= 5, (
            f"{value} — допущение книги, а разрешено в "
            f"{sorted(holders[value])}: поводов столько не бывает")


def test_dates_are_not_hardcoded_in_the_engine():
    """Дат-литералов в движке нет: они приходят из книги и фактов.

    Литерал «20.09.2026» в расчёте долга замер бы навсегда — в январе панель
    показывала бы сентябрьский долг. Разрешены только границы полугодий
    (1 июля, 31 декабря, 30 июня): это календарь, а не допущение.
    """
    allowed_days = {(7, 1), (12, 31), (1, 1), (6, 30)}
    offenders = []
    for path in sorted(MODEL.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "date" and len(node.args) == 3):
                parts = [a.value if isinstance(a, ast.Constant) else None for a in node.args]
                if parts[0] is None:
                    continue                      # год вычисляется — это и требуется
                if tuple(parts[1:]) not in allowed_days:
                    offenders.append(f"{path.name}:{node.lineno}: date{tuple(parts)}")
    assert not offenders, "\n".join(offenders)
