# -*- coding: utf-8 -*-
"""Пересборка макро-миров книги по рецепту: кандидат и отчёт CANDIDATE.md.

Когда запускать: после опорного заседания ЦБ с прогнозом, после итоговой
ОНДКП, при гейте `book_update` (сдвиг кривой >= 50 б.п. на 5-10 годах или
книга старше 45 дней). Сначала аудитор вписывает новые входы записи (таблица
ЦБ, сноска о ставке до конца года, ОНДКП, макроопрос, действующая ключевая)
в worlds_inputs.yaml — data/assumptions/WORLDS-RECIPE.md, раздел 6.

Как запускать (из корня репозитория):
  python -B ops/tools/refresh_worlds.py --out DIR
      кривая, цена и дата оценки — из боевого выпуска
      https://tzi-850-lenta.pages.dev/api/model (единственное обращение в сеть)
  python -B ops/tools/refresh_worlds.py --release api_model.json --out DIR
      то же по сохранённому снимку выпуска
  python -B ops/tools/refresh_worlds.py --curve zcyc.json [--curve-date ГГГГ-ММ-ДД] --out DIR
      кривая — снимок MOEX ISS zcyc.json (дата — из tradedate снимка или ключом)
  python -B ops/tools/refresh_worlds.py --state [КАТАЛОГ] --out DIR
      кривая — ряды moex.zcyc.* локального состояния (по умолчанию var/indicators
      или $LENTA_STATE_DIR/indicators)
  --inputs ФАЙЛ — входы записи (по умолчанию data/assumptions/worlds_inputs.yaml;
      при пересборке — отредактированная копия аудитора вне каталога книги).
      Цена и дата оценки всегда берутся из выпуска (--release), кривая — из
      --curve/--state, если они заданы.
  --ofz-in ФАЙЛ [ФАЙЛ …] — реальные доходности ОФЗ-ИН на дату кривой (книга 1.4)
  --draws N — прогонов полосы A-V9 на кандидате (по умолчанию как в книге, 2 000)
  python -B ops/tools/refresh_worlds.py --check
      только строгая проверка канона: рецепт на входах книги = её worlds_source.json
      целиком (63 строки, lt_inflation, m_inflation) — как worlds_recipe.py --check

Реальные доходности ОФЗ-ИН (книга 1.4, `m_inflation.source: forward_bei`).
Инфляция мира M — рыночные форвардные BEI на дату кривой, и рецепт требует
доходностей ОФЗ-ИН ТОЙ ЖЕ даты (иначе отказ). Откуда они берутся — первое,
что есть на дату кривой:
  1. --ofz-in: история торгов ISS на дату кривой (`YIELDCLOSE`; например
     https://iss.moex.com/iss/history/engines/stock/markets/bonds/boards/TQOB/securities.json?date=ГГГГ-ММ-ДД)
     или YAML/JSON-блок `ofz_in` (`date`, `settlement_date`, `bonds`:
     [{secid, real_yield}] или {secid: real_yield}, % годовых);
  2. входы записи, если их `ofz_in.date` уже равна дате кривой (аудитор
     вписал доходности сам);
  3. ряды `moex.ofz_in.*` состояния (при --state) или `live.observed_ofz_in`
     выпуска — доходности последней сделки, датированные днём торгов.
Нужны ВСЕ выпуски входов (`ofz_in.bonds`); нет хотя бы одного — код 1 с
подсказкой. Дата расчётов — `settlement_date` файла или T+1 (следующий
будний день; праздники не учитываются — перед праздником задайте её в
YAML-блоке). В режиме `judgement` ОФЗ-ИН не нужны.

Что пишет — ТОЛЬКО в --out: worlds_source.json кандидата, anchors_by_rule.json
(якоря по правилам яруса 2 и замечания), CANDIDATE.md — отчёт рецепта (кривая,
сдвиги механики, справедливые кривые и уровни терминала по записи и по
правилу, якоря запись/правило, замечания правил, ядро на входах книги) и
блоки инструмента: замечания к входам (если есть), заголовок и слои «выпуск →
кандидат» ядром при цене и дате выпуска, печатаемый заголовок A-V9 на входах
книги, гейты model/checks.py на кандидате, черновик строки журнала версий книги.

Чего НЕ делает: не пишет в data/assumptions/ (каталог внутри книги как --out
отвергается), не публикует и не трогает выпуск, не выбирает якоря и веса
миров — это решение аудитора и новая версия книги с подписью. Формулы миров
не переписаны: build_worlds, candidate_anchors и отчёт импортируются из
data/assumptions/worlds_recipe.py.

Коды возврата:
  0 — кандидат собран, отчёт записан;
  1 — негодные входы: выпуск недоступен или без обязательных полей, кривая
      пуста или негодна, во входах записи нет обязательных полей, при
      forward_bei нет доходностей ОФЗ-ИН всех выпусков на дату кривой;
  2 — кандидат не собрался: книга-кандидат не строится или ядро падает на ней.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import importlib.util
import json
import math
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from model.book import BOOK_DIR, BOOK_YAML, load_book  # noqa: E402
from model.checks import (RELEASE_LABEL, Severity, check_book_is_current,  # noqa: E402
                          summarize_gates)
from model.book_results import fair_value_block, layer_block  # noqa: E402
from model.engine import run_release  # noqa: E402
from model.grid import layer_variants  # noqa: E402
from model.live import (CURVE_PREFIX, MIN_CURVE_NODES, OFZ_IN_MAX, OFZ_IN_MIN,  # noqa: E402
                        OFZ_IN_PREFIX, RATE_MAX, RATE_MIN, LiveReport, curve_shift)
from model.uncertainty import headline_of, uncertainty  # noqa: E402

BOOK = BOOK_DIR
RELEASE_URL = "https://tzi-850-lenta.pages.dev/api/model"
# Только ASCII: заголовки HTTP кодируются latin-1 (урок ops/publish.py).
HEADERS = {"accept": "application/json", "user-agent": "lenta-850-refresh-worlds/1.0"}
LAYERS = (("macro_neutral", "рыночные ставки как есть"),
          ("market_implied", "вменённые рынком веса"),
          ("analytical", "свой макро-взгляд"))
# Поля входов записи, без которых рецепт не считается. Проверяются до расчёта:
# KeyError из глубины рецепта не говорит аудитору, какую строку он забыл.
REQUIRED_INPUTS = (
    "version", "asof", "units", "grid.first_period", "grid.n_periods",
    "curve.date", "curve.nodes", "parameters.spread_short_vs_key_pp",
    "parameters.tp_shape", "parameters.first_period_blend_observed",
    "parameters.m_key_offset_pp", "parameters.round_decimals", "anchor_rules",
    "worlds.N.anchors", "worlds.N.steady", "worlds.H.anchors", "worlds.H.steady",
    "worlds.M.anchors", "worlds.M.steady", "probabilities", "probability_ranges", "notes",
    "cbr_forecast.key_avg", "cbr_forecast.rest_of_year.range", "cbr_forecast.cpi_eop",
    "cbr_forecast.cpi_avg", "ondkp.proinflation.key_avg", "survey")
REQUIRED_RELEASE = ("meta.valuation_date", "market.price", "fair_value.low",
                    "fair_value.central", "fair_value.high")
# Поля входов, без которых рецепт в режиме forward_bei не строит реальную кривую.
REQUIRED_FORWARD_BEI = ("ofz_in.bonds", "ofz_in.coupon_pct", "ofz_in.coupon_period_days",
                        "m_inflation.index_lag_months")
ISS_HISTORY = ("https://iss.moex.com/iss/history/engines/stock/markets/bonds/boards/TQOB/"
               "securities.json?date={date}")
OK, BAD_INPUT, NOT_BUILT = 0, 1, 2


class Refused(Exception):
    """Негодные входы — код 1."""


class NotBuilt(Exception):
    """Кандидат не собрался — код 2."""


def _module(path: Path, name: str):
    """Модуль по пути: рецепт — файл книги, а не пакет кода.

    Байткод при импорте не пишется: без `-B` (так тесты гоняет суточный такт)
    рядом с каноном под sha появился бы `__pycache__/`, а инструмент в книгу
    не пишет ни байта. Флаг возвращается как был: тест, импортирующий
    инструмент, не должен выключать кэш всему прогону pytest.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    was, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = was
    return module


RECIPE = _module(BOOK / "worlds_recipe.py", "worlds_recipe")


def _get(tree: dict, dotted: str):
    node = tree
    for key in dotted.split("."):
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def _missing(tree: dict, paths) -> list[str]:
    return [p for p in paths if _get(tree, p) is None]


# ------------------------------------------------------------------ входы


def read_inputs(path: Path) -> dict:
    try:
        inp = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise Refused(f"входы записи не читаются ({path}): {exc}") from exc
    if not isinstance(inp, dict):
        raise Refused(f"входы записи пусты: {path}")
    missing = _missing(inp, REQUIRED_INPUTS)
    if missing:
        raise Refused(f"во входах записи {path} нет обязательных полей: " + ", ".join(missing))
    return inp


def read_release(src: str) -> dict:
    try:
        if src.startswith(("http://", "https://")):
            request = urllib.request.Request(src, headers=HEADERS)
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read()
        else:
            raw = Path(src).read_bytes()
        release = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise Refused(f"выпуск недоступен ({src}): {exc}; укажите снимок --release <файл>") from exc
    missing = _missing(release, REQUIRED_RELEASE)
    if missing:
        raise Refused(f"в выпуске {src} нет обязательных полей: " + ", ".join(missing))
    return release


def _checked_nodes(nodes: dict[float, float], source: str, why_empty: str = "") -> dict[float, float]:
    """Кривая в ДОЛЯХ проходит те же коридоры, что наблюдение в `model/live.py`.

    Рецепт принимает любую кривую, и кривая в процентах вместо долей дала бы
    миры со ставками в сотни процентов — отчёт выглядел бы как отчёт.
    `why_empty` — пояснение только к пустой кривой: у кривой в неверных
    единицах причина другая, и метка «её нет» увела бы искать не туда.
    """
    if not nodes:
        raise Refused(f"кривая пуста ({source}{why_empty}) — кандидат без кривой не строится")
    bad = [f"{t:g}y={v:.4g}" for t, v in sorted(nodes.items())
           if not (math.isfinite(v) and RATE_MIN <= v <= RATE_MAX)]
    if bad:
        raise Refused(f"узлы кривой вне коридора {RATE_MIN:.0%}–{RATE_MAX:.0%} ({source}): "
                      + ", ".join(bad[:4]))
    if len(nodes) < MIN_CURVE_NODES:
        raise Refused(f"кривая из {len(nodes)} узлов при минимуме {MIN_CURVE_NODES} ({source})")
    return nodes


def release_curve(release: dict):
    live = release.get("live") or {}
    nodes = {float(k): float(v) for k, v in (live.get("observed_curve") or {}).items()}
    _checked_nodes(nodes, "live.observed_curve выпуска", " — сборка выпуска кривую отвергла или её нет")
    if not live.get("observed_curve_date"):
        raise Refused("в выпуске нет live.observed_curve_date")
    return RECIPE.Curve.from_release(release)


def zcyc_curve(path: Path, date: str | None):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        cols, rows = payload["yearyields"]["columns"], payload["yearyields"]["data"]
        nodes = {float(r[cols.index("period")]): float(r[cols.index("value")]) / 100 for r in rows}
        days = {r[cols.index("tradedate")] for r in rows} if "tradedate" in cols else set()
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        raise Refused(f"снимок zcyc не читается ({path}): {exc}") from exc
    date = date or (days.pop() if len(days) == 1 else None)
    if not date:
        raise Refused(f"дата снимка {path} не определена: укажите --curve-date")
    _checked_nodes(nodes, str(path))
    return RECIPE.Curve.from_zcyc(payload, date)


def state_curve(root: Path | None):
    """Ряды `moex.zcyc.*` состояния: последние точки ОДНОГО дня.

    Узлы разных дней в одну кривую не склеиваются: MOEX отдаёт кривую целиком,
    и разнобой дат значит, что часть узлов не собралась.
    """
    from indicators.store import Store

    store = Store(root) if root else Store()
    nodes, days = {}, set()
    for series in store.all_series():
        if not series.id.startswith(CURVE_PREFIX):
            continue
        point = series.latest()
        try:
            tenor = float(series.id[len(CURVE_PREFIX):].removesuffix("y"))
        except ValueError:
            continue
        if point is None or point.value is None:
            continue
        nodes[tenor] = float(point.value)
        days.add(point.period[:10])
    _checked_nodes(nodes, f"ряды {CURVE_PREFIX}* в {store.root}")
    if len(days) != 1:
        raise Refused(f"узлы кривой в состоянии из разных дней: {sorted(days)}")
    return RECIPE.Curve({t: v * 100 for t, v in nodes.items()}, days.pop())


# ------------------------------------------- ОФЗ-ИН на дату кривой (1.4)


def forward_bei(inp: dict) -> bool:
    return (inp.get("m_inflation") or {}).get("source") == "forward_bei"


def _secid(code) -> str:
    """`SU52002RMFS1` (ISS, ряды состояния, выпуск) → `52002` (входы рецепта)."""
    found = re.fullmatch(r"SU(\d{5})RMFS\d", str(code))
    return found.group(1) if found else str(code)


def settlement_after(day: str) -> str:
    """Расчёты T+1 — следующий будний день после дня торгов.

    Праздники не учитываются: их календаря у проекта нет. День сдвига меняет
    реальную кривую на доли базисного пункта, а при торговом дне перед
    праздником дату задают явно — `settlement_date` в YAML-блоке `--ofz-in`.
    """
    d = dt.date.fromisoformat(day) + dt.timedelta(days=1)
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return d.isoformat()


def _read_any(path: Path):
    """JSON (ответ ISS — с табуляциями, их YAML не читает) или YAML."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise Refused(f"--ofz-in {path} не читается: {exc}") from exc
    try:
        return json.loads(text)
    except ValueError:
        pass
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise Refused(f"--ofz-in {path}: ни JSON, ни YAML ({exc})") from exc


def linkers_from_files(paths, day: str) -> tuple[dict, str, str | None, str, list[str]]:
    """Доходности из файлов `--ofz-in`: история ISS или блок `ofz_in` входов.

    История ISS отбирается по `TRADEDATE` = дата кривой (в файле может быть
    окно дат или вся доска TQOB) — берётся `YIELDCLOSE`, как велит
    `WORLDS-RECIPE.md` (раздел 6, п. 1). Блок `ofz_in` несёт свою дату — она
    сверяется с кривой ниже, как у любого источника.
    """
    yields, dates, settle, other_days = {}, set(), None, set()
    for path in paths:
        payload = _read_any(path)
        if not isinstance(payload, dict):
            raise Refused(f"--ofz-in {path}: не история ISS и не блок ofz_in")
        try:
            if "history" in payload:
                block = payload["history"]
                col = {c: i for i, c in enumerate(block["columns"])}
                for row in block["data"]:
                    if not str(row[col["SECID"]]).startswith("SU52"):
                        continue
                    if str(row[col["TRADEDATE"]]) != day:
                        other_days.add(str(row[col["TRADEDATE"]]))
                    elif row[col["YIELDCLOSE"]]:
                        yields[_secid(row[col["SECID"]])] = float(row[col["YIELDCLOSE"]])
                        dates.add(day)
                continue
            block = payload.get("ofz_in", payload)
            dates.add(str(block["date"]))
            settle = block.get("settlement_date") or settle
            bonds = block["bonds"]
            pairs = (bonds.items() if isinstance(bonds, dict)
                     else ((b["secid"], b["real_yield"]) for b in bonds))
            yields.update({_secid(k): float(v) for k, v in pairs})
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise Refused(f"--ofz-in {path}: не читается как история ISS или блок ofz_in "
                          f"({type(exc).__name__} {exc})") from exc
    notes = [f"в истории ISS есть только {', '.join(sorted(other_days)[-3:])}"] if other_days and not yields else []
    source = "--ofz-in " + ", ".join(Path(p).name for p in paths)
    return yields, (dates.pop() if len(dates) == 1 else ", ".join(sorted(dates))), settle, source, notes


def linkers_from_release(release: dict) -> tuple[dict, str, list[str]]:
    live = release.get("live") or {}
    if "observed_ofz_in" not in live:
        return {}, "", ["выпуск собран без поля live.observed_ofz_in (раньше, чем выпуск стал его нести)"]
    notes = [f"{_secid(k)}: {why}" for k, why in sorted((live.get("observed_ofz_in_missing") or {}).items())]
    # Доли выпуска → % рецепта; шесть знаков снимают шум умножения (ISS даёт сотые).
    return ({_secid(k): round(100 * float(v), 6) for k, v in live["observed_ofz_in"].items()},
            str(live.get("observed_ofz_in_date") or ""), notes)


def linkers_from_state(root: Path | None, day: str) -> tuple[dict, list[str]]:
    """Ряды `moex.ofz_in.*` состояния: точки ровно на дату кривой (дата торгов)."""
    from indicators.store import Store

    store = Store(root) if root else Store()
    yields, notes = {}, []
    for series in store.all_series():
        if not series.id.startswith(OFZ_IN_PREFIX):
            continue
        value = series.value_as_of(day, dt.date.today().isoformat())
        if value is None:
            notes.append(f"{_secid(series.id[len(OFZ_IN_PREFIX):])}: нет точки на {day}")
            continue
        yields[_secid(series.id[len(OFZ_IN_PREFIX):])] = round(100 * float(value), 6)
    return yields, notes


def linker_inputs(inp: dict, curve, release: dict, *, files=None, state: Path | None = None,
                  use_state: bool = False) -> tuple[dict, dict | None]:
    """Входы рецепта с реальными доходностями ОФЗ-ИН на дату кривой и сводка для отчёта.

    Рецепт 1.4 сам сверяет дату доходностей с датой кривой и выходит через
    SystemExit; здесь то же условие проверяется ДО расчёта, с именем
    недостающего выпуска и подсказкой, где взять доходности. Условия выпуска
    (погашение, купон, период купона) — из входов записи: это не рынок.
    """
    if not forward_bei(inp):
        if files:
            raise Refused("--ofz-in задан, а инфляция мира M во входах — суждение "
                          "(m_inflation.source не forward_bei): рецепт ОФЗ-ИН не читает")
        return inp, None
    missing = _missing(inp, REQUIRED_FORWARD_BEI)
    if missing:
        raise Refused("во входах записи нет полей forward_bei: " + ", ".join(missing))
    OI = inp["ofz_in"]
    need = [str(b["secid"]) for b in OI["bonds"]]
    info = dict(record={str(b["secid"]): float(b["real_yield"]) for b in OI["bonds"]},
                record_date=str(OI.get("date") or "—"),
                maturity={str(b["secid"]): str(b["maturity"]) for b in OI["bonds"]}, release=None)
    rel_yields, rel_day, rel_notes = linkers_from_release(release)
    if rel_day == curve.date:
        info["release"] = rel_yields
    settle, notes, own = None, [], False
    if files:
        yields, day, settle, source, notes = linkers_from_files(files, curve.date)
    elif str(OI.get("date")) == curve.date:
        # Аудитор вписал доходности сам: они берутся как есть, но проходят те
        # же проверки, что у любого источника, — доли вместо процентов во
        # входах так же негодны, как в файле или выпуске.
        yields, day, settle, own = dict(info["record"]), curve.date, OI.get("settlement_date"), True
        source = "входы записи (`ofz_in` на дату кривой)"
    elif use_state:
        yields, notes = linkers_from_state(state, curve.date)
        day, source = curve.date, f"ряды {OFZ_IN_PREFIX}* состояния"
    else:
        yields, day, notes = rel_yields, rel_day, rel_notes
        source = "выпуск, `live.observed_ofz_in`"
    lacking = [s for s in need if s not in yields]
    if day != curve.date or lacking:
        got = (f"есть на {day or 'дату —'}: {', '.join(sorted(yields)) or 'ничего'}"
               + (f"; {'; '.join(notes)}" if notes else ""))
        raise Refused(
            f"рецепту миров (m_inflation.source: forward_bei) нужны реальные доходности ОФЗ-ИН "
            f"{', '.join(need)} на дату кривой {curve.date}; {source}: "
            + (f"нет {', '.join(lacking)}" if day == curve.date else "дата другая")
            + f" ({got}). Укажите --ofz-in <файл>: история торгов ISS на {curve.date} "
            f"({ISS_HISTORY.format(date=curve.date)}) или YAML-блок ofz_in "
            "(date, settlement_date, bonds: {secid: real_yield})")
    # Коридор тот же, что у выпуска (`model/live.py`), но в % годовых — в них
    # доходности ждёт рецепт: 0,0878 из файла — это доля, а не 0,09 %.
    bad = [f"{s}={yields[s]:g}" for s in need if not 100 * OFZ_IN_MIN <= yields[s] <= 100 * OFZ_IN_MAX]
    if bad:
        raise Refused(f"реальные доходности ОФЗ-ИН вне коридора {OFZ_IN_MIN:.1%}–{OFZ_IN_MAX:.0%} ({source}): "
                      + ", ".join(bad) + " — рецепт ждёт % годовых, а не доли")
    inp = copy.deepcopy(inp)
    OI = inp["ofz_in"]
    # Даты — строкой ISO. YAML без кавычек (`settlement_date: 2026-10-26`)
    # читает их объектом даты, а рецепт сверяет дату доходностей с датой
    # кривой как строку и разбирает дату расчётов `fromisoformat`: без
    # приведения отказ звучал бы «(2026-10-23) должны быть на дату кривой
    # (2026-10-23)» или «TypeError fromisoformat».
    OI["date"], OI["settlement_date"] = curve.date, str(settle or settlement_after(curve.date))
    if not own:
        OI["source"] = f"{source}, {curve.date}"
        for bond in OI["bonds"]:
            bond["real_yield"] = yields[str(bond["secid"])]
    info.update(source=source, yields={s: yields[s] for s in need}, date=curve.date,
                settlement_date=OI["settlement_date"], substituted=not own)
    return inp, info


# ------------------------------------------------------ книга и расчёт


def build_book(worlds: dict, workdir: Path) -> dict:
    """Книга-кандидат штатным сборщиком книги во временном каталоге.

    `build_assumptions.py` пишет рядом с собой, поэтому он и шаблон копируются
    к кандидату: книга в `data/assumptions/` не трогается ни байтом.
    """
    for name in ("build_assumptions.py", "assumptions_template.yaml"):
        (workdir / name).write_bytes((BOOK / name).read_bytes())
    (workdir / "worlds_source.json").write_text(
        json.dumps(worlds, ensure_ascii=False, indent=1), encoding="utf-8")
    done = subprocess.run([sys.executable, "-B", "build_assumptions.py"], cwd=workdir,
                          capture_output=True, text=True, encoding="utf-8")
    if done.returncode != 0:
        raise NotBuilt("build_assumptions.py на кандидате: " + (done.stderr or done.stdout)[-800:])
    return load_book(workdir / "assumptions.yaml")


def at_release(A: dict, release: dict) -> dict:
    """Книга на цене и дате оценки выпуска; других живых входов нет.

    Мост выписан на дату СВОЕЙ книги (`meta.bridge_as_of`, обязательный ключ),
    и ядро наращивает строки моста от неё; перенос даты оценки её не трогает.
    """
    A = copy.deepcopy(A)
    A["meta"].setdefault("curve_as_of", A["meta"]["valuation_date"])
    A["market"]["price"] = float(release["market"]["price"])
    A["meta"]["valuation_date"] = str(release["meta"]["valuation_date"])[:10]
    return A


def _three(fv) -> tuple[float, float, float]:
    if isinstance(fv, dict):
        return float(fv["low"]), float(fv["central"]), float(fv["high"])
    return fv.low, fv.central, fv.high


def _engine_layers(release) -> dict:
    return {k: dict(v0=release.layers[k].v0, claims=release.layers[k].claims,
                    intrinsic=release.layers[k].intrinsic, headline=release.layers[k].headline)
            for k, _ in LAYERS}


def book_tables(A: dict):
    """Выпуск ядра на книге `A`, её слои и результат в форме таблиц книги (`results.json`)."""
    release = run_release(copy.deepcopy(A), gates=False)
    variants = layer_variants(A, release.cells)
    layers = {name: layer_block(A, layer, variants[name]) for name, layer in release.layers.items()}
    return release, layers, fair_value_block(A, release)


def evaluate(cand_book: dict, record_book: dict, release: dict, curve, draws: int | None = None) -> dict:
    """Заголовок, слои и гейты кандидата — ядром.

    На входах книги (её дата и цена, как в журнале версий) — точка, слои и
    результат в форме таблиц книги и печатаемый заголовок A-V9: `draws`
    прогонов полосы (по умолчанию — сколько задано в книге, 2 000 ≈ 40 с).
    На входах выпуска — числа и гейты, которые напечатал бы выпуск на кандидате.
    """
    out = {}
    at_book, layers_b, out["book_fv"] = book_tables(cand_book)
    out["book_engine"] = _three(at_book.fair_value)
    band = uncertainty(copy.deepcopy(cand_book), draws=draws)
    out["book_headline"] = headline_of(cand_book, at_book.fair_value, band)
    # Заголовок записи — из её `results.json` (2 000 прогонов), не пересчётом.
    out["record_headline"] = json.loads((BOOK / "results.json").read_text(encoding="utf-8")).get("headline")
    out["journal"] = journal_result(layers_b, out["book_fv"], out["book_headline"])

    cand = at_release(cand_book, release)
    engine = run_release(copy.deepcopy(cand), gates=True)
    record = run_release(at_release(record_book, release), gates=False)
    out["record_version"] = record_book["meta"]["version"]
    out["engine"] = _three(engine.fair_value)
    out["printed"] = (engine.fair_value.printed_low, engine.fair_value.printed_central,
                      engine.fair_value.printed_high)
    out["record"] = _three(record.fair_value)
    out["layers_engine"] = _engine_layers(engine)
    out["gates"], out["invariants"], out["book_update"] = _gates(engine, cand_book, cand, release, curve)
    return out


def _gates(engine, cand_book: dict, cand: dict, release: dict, curve):
    """Гейты кандидата так, как их увидела бы сборка выпуска на нём.

    Совещательный `book_update` считается на ЖИВОЙ кривой против мира M
    кандидата: он и отвечает на вопрос «гаснет ли плашка после пересборки».
    """
    live = LiveReport(book_date=cand_book["meta"]["valuation_date"])
    live.curve_shift = curve_shift(cand, {t: v / 100 for t, v in curve.nodes.items()})
    live.book_age_days = (dt.date.fromisoformat(cand["meta"]["valuation_date"])
                          - dt.date.fromisoformat(live.book_date)).days
    book_update = check_book_is_current(cand, live)
    findings = engine.findings + book_update
    masses = {c.cell.key: c.probability for c in engine.cells} | {RELEASE_LABEL: 1.0}
    invariants = [f for f in findings if f.severity is Severity.INVARIANT]
    return summarize_gates(findings, masses), invariants, (live, book_update)


# ----------------------------------------------------------------- отчёт


def _r(x: float, digits: int = 2) -> str:
    return f"{x:.{digits}f}"


def _signed(x: float, digits: int = 2) -> str:
    """Со знаком и без «−0,00»: округление до печати, иначе ноль выглядит сдвигом."""
    return f"{round(x, digits) + 0.0:+.{digits}f}"


def _ru(x: float) -> str:
    """Целое с пробелом в тысячах — как в журнале версий книги."""
    return f"{round(x):,}".replace(",", " ")


def _diff_paths(a, b, prefix: str = "") -> list[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b), key=str):
            out += _diff_paths(a.get(k), b.get(k), f"{prefix}.{k}" if prefix else str(k))
        return out
    return [] if a == b else [prefix]


def headline_block(ev: dict, release: dict) -> list[str]:
    meta, fv = release["meta"], release["fair_value"]
    printed = (f"{fv['printed_low']:.0f}–{fv['printed_high']:.0f}, центр {fv['printed_central']:.0f}"
               if "printed_low" in fv else "")
    L = ["## Заголовок: выпуск → кандидат (ядро)", "",
         f"Цена и дата оценки — выпуска: {float(release['market']['price']):.0f} ₽ на "
         f"{meta['valuation_date']} (выпуск {str(meta.get('payload_sha256', ''))[:12] or '—'}, "
         f"книга {meta.get('book_version', '—')}); других живых входов нет, миры — кандидата.", "",
         "| | низ | центр | верх | печать точки |", "|---|---:|---:|---:|---|",
         "| выпуск (`/api/model`) | " + " | ".join(_r(x) for x in _three(fv)) + f" | {printed} |"]
    L.append(f"| запись {ev['record_version']}, движок на входах выпуска | "
             + " | ".join(_r(x) for x in ev["record"]) + " | |")
    pl, pc, ph = ev["printed"]
    L.append("| **кандидат, движок** | " + " | ".join(f"**{_r(x)}**" for x in ev["engine"])
             + f" | {pl:.0f}–{ph:.0f}, центр {pc:.0f} |")
    L += ["", "Сдвиг от миров кандидата (движок, те же входы): "
          + " / ".join(_signed(a - b, 0) for a, b in zip(ev["engine"], ev["record"])) + " ₽.", ""]
    L.append(f"На входах книги (дата и цена книги) кандидат: {' / '.join(_r(x) for x in ev['book_engine'])} ₽.")
    if ev.get("book_headline"):
        L += ["", headline_line(ev.get("record_headline"), ev["book_headline"])]
    L += ["", "| слой | V0, млрд ₽ | D, млрд ₽ | внутренняя, ₽ | заголовочная, ₽ |", "|---|---:|---:|---:|---:|"]
    was = release.get("layers") or {}
    for key, title in LAYERS:
        e, w = ev["layers_engine"][key], was.get(key) or {}
        cells = []
        for field, digits in (("v0", 1), ("claims", 1), ("intrinsic", 0), ("headline", 1)):
            before = w.get(field)
            cells.append(("—" if before is None else _r(before, digits)) + f" → {_r(e[field], digits)}")
        L.append(f"| {title} | " + " | ".join(cells) + " |")
    L += ["", "В клетке слоя: выпуск → кандидат."]
    return L


def gates_block(res: dict, release: dict) -> list[str]:
    was = {g["key"]: g for g in release.get("gates") or []}
    now = {g.key: g for g in res["gates"]}
    L = ["## Гейты `model/checks.py` на кандидате (входы выпуска)", "",
         f"Инвариантов нарушено: {len(res['invariants'])}."
         + ("" if not res["invariants"] else " " + "; ".join(
             f"{f.key} ({f.label})" for f in res["invariants"][:6]) + " — сборка выпуска упала бы."), "",
         "| гейт | выпуск: клеток / масса | кандидат: клеток / масса | ожидаемая масса | на кандидате |",
         "|---|---:|---:|---:|---|"]
    for key in sorted(set(was) | set(now)):
        w, g = was.get(key), now.get(key)
        before = f"{w['cells']} / {w['mass']:.4f}" if w else "—"
        after = f"{g.cells} / {g.mass:.4f}" if g else "—"
        expected = (g.expected_mass if g else (w or {}).get("mass_expected"))
        # Диапазон `expected_mass_range` — то, с чем идёт сверка (коридор
        # `GateSummary.mass_corridor`); печатать рядом с ним точку значило бы
        # показывать аудитору не ту величину, по которой вынесен статус.
        if g is not None and g.expected_mass_range is not None:
            shown = f"{g.expected_mass_range[0]:.3f}–{g.expected_mass_range[1]:.3f}"
        else:
            shown = "—" if expected is None else f"{float(expected):.3f}"
        if g is None:
            status = "не срабатывает"
        elif g.advisory:
            status = "совещательный: плашка «книгу пора обновлять», сборку не блокирует"
        elif g.blocking:
            status = ("**объяснение просрочено — сборка упала бы**" if g.stale
                      else "**нет объяснения — сборка упала бы**")
        elif g.mass_mismatch:
            status = "объяснён; **масса вне коридора объяснения — тревога такта**"
        else:
            status = "объяснён"
        L.append(f"| {key} | {before} | {after} | {shown} | {status} |")
    live, fired = res["book_update"]
    shift = " / ".join(_signed(v * 10000, 0) for v in live.curve_shift.values())
    L += ["", f"`book_update` на кандидате: сдвиг живой кривой к миру M кандидата на "
          f"{'/'.join(live.curve_shift)} годах {shift} б.п.; книга "
          f"откалибрована {live.book_date}, {live.book_age_days} дн к дате выпуска — "
          + ("срабатывает." if fired else "не срабатывает."), ""]
    blocking = [g.key for g in res["gates"] if g.blocking]
    mismatch = [g.key for g in res["gates"] if g.mass_mismatch and not g.blocking]
    L.append("Итог: сборка выпуска на кандидате "
             + ("прошла бы" if not blocking and not res["invariants"] else "УПАЛА бы: " + ", ".join(blocking))
             + ("; тревога такта по массе: " + ", ".join(mismatch) if mismatch else "; тревог по массе нет")
             + ". Объяснения — `data/assumptions/gate_explanations.yaml` (правит аудитор вместе с книгой).")
    return L


def journal_result(layers: dict, fv: dict, headline: dict) -> str:
    """Последний столбец журнала версий книги — из слоёв, результата и заголовка
    в форме таблиц книги (`results.json`: `layers`, `fair_value`, `headline`).

    Книга печатает медиану распределения центра по суждениям (A-V9), и
    столбец её строки журнала — «по старой методике средняя / медиана клеток
    при своих весах / слой при рыночных ставках, точка; **печатается медиана
    по суждениям (≈ печать), полосы 80 % и 50 %, точка при центральных
    значениях, P(ниже рынка)**».
    """
    old, printed = fv["by_method"]["old_floor_mean"], fv["printed"]
    method = (f"по старой методике {_ru(layers['analytical']['mean'])} / {_ru(layers['analytical']['p50'])} / "
              f"{_ru(layers['macro_neutral']['mean'])}")
    h = headline

    def pair(ab) -> str:
        return f"{_ru(ab[0])}–{_ru(ab[1])}"

    return (method + f", точка {_ru(old['central'])}; **печатается медиана по суждениям {_ru(h['central'])} "
            f"(≈{_ru(h['printed_central'])}), полоса 80 % — {pair(h['band'])} (≈{pair(h['printed_band'])}), "
            f"50 % — {pair(h['inner'])} (≈{pair(h['printed_inner'])}); точка при центральных значениях "
            f"{_ru(fv['low'])} / {_ru(fv['central'])} / {_ru(fv['high'])} "
            f"(≈{_ru(printed['low'])} / {_ru(printed['central'])} / {_ru(printed['high'])}); "
            f"P(ниже рынка {_ru(h['market'])}) {round(100 * h['p_central_below_market'])} %**")


def headline_line(record: dict | None, cand: dict) -> str:
    """Печатаемый заголовок A-V9 записи и кандидата — одной строкой отчёта."""
    def one(h: dict) -> str:
        return (f"медиана {_ru(h['central'])} (≈{_ru(h['printed_central'])}), полоса 80 % "
                f"{_ru(h['band'][0])}–{_ru(h['band'][1])}, 50 % {_ru(h['inner'][0])}–{_ru(h['inner'][1])}, "
                f"P(ниже рынка {_ru(h['market'])}) {100 * h['p_central_below_market']:.1f} % "
                f"({_ru(h['draws'])} прогонов)")
    return ("Печатаемый заголовок книги (A-V9; на входах книги — дата и цена книги): "
            + (f"запись — {one(record)}; " if record else "")
            + f"кандидат — **{one(cand)}**."
            + (" Прогонов у кандидата меньше, чем у записи: квантили — оценка, сдвиг меньше шума Монте-Карло "
               "не читается." if record and cand.get("draws", 0) < record.get("draws", 0) else ""))


def linkers_block(linkers: dict, record: dict, cand: dict) -> list[str]:
    """ОФЗ-ИН на дату кривой, π_ss и путь BEI мира M: запись → кандидат.

    `WORLDS-RECIPE.md` (раздел 6, п. 2) требует в отчёте π_ss и путь BEI мира
    M, а отчёт рецепта их не печатает: у него запись и кандидат — только
    якоря ключевой и ИПЦ.
    """
    was, now = record.get("m_inflation") or {}, cand.get("m_inflation") or {}
    release = linkers.get("release")
    show_release = release is not None and linkers["source"] != "выпуск, `live.observed_ofz_in`"
    L = ["## Мир M: реальные доходности ОФЗ-ИН и форвардные BEI (книга 1.4, `forward_bei`)", "",
         f"Доходности на дату кривой {linkers['date']} (расчёты {linkers['settlement_date']}) — "
         f"{linkers['source']}.", "",
         f"| выпуск | погашение | запись {linkers['record_date']}, % | кандидат {linkers['date']}, % | Δ, б.п. |"
         + (" выпуск (`live.observed_ofz_in`), % |" if show_release else ""),
         "|---|---|---:|---:|---:|" + ("---:|" if show_release else "")]
    for secid, value in linkers["yields"].items():
        before = linkers["record"][secid]
        L.append(f"| {secid} | {linkers['maturity'][secid]} | {before:.2f} | {value:.2f} | "
                 f"{_signed(100 * (value - before), 0)} |"
                 + (f" {release[secid]:.2f} |" if show_release and secid in release
                    else " — |" if show_release else ""))
    L += ["", f"π_ss мира M (среднее полугодовых форвардных BEI за последние "
          f"шесть периодов): запись {was.get('pi_ss', '—')} → кандидат **{now.get('pi_ss', '—')}** % "
          f"(неокруглённый {now.get('pi_ss_unrounded', float('nan')):.4f}); поправка на ликвидность "
          f"{now.get('liquidity_adj_pp', 0.0):g} п.п.", "",
          "| полугодие | BEI записи, сглаж. | BEI кандидата, сглаж. | ИПЦ M на конец: запись → кандидат |",
          "|---|---:|---:|---|"]
    rec_rows = {r["period"]: r for r in record["rows"] if r["world"] == "M"}
    first = now.get("first_market_period")
    for row in (r for r in cand["rows"] if r["world"] == "M"):
        p = row["period"]
        bw = (was.get("pi_half_year_fwd_smoothed") or {}).get(p)
        bn = (now.get("pi_half_year_fwd_smoothed") or {}).get(p)
        mark = "" if first is None or p >= first else " (запись)"
        L.append(f"| {p}{mark} | {'—' if bw is None else f'{bw:.2f}'} | {'—' if bn is None else f'{bn:.2f}'} | "
                 f"{rec_rows[p]['cpi_yoy_eop']} → {row['cpi_yoy_eop']} |")
    return L


def journal_block(res: dict, inp: dict, record_inputs: dict, record: dict, curve,
                  cand: dict | None = None) -> list[str]:
    rec_curve = RECIPE.Curve(inp["curve"]["nodes"], inp["curve"]["date"])
    moves = " / ".join(_signed(100 * (curve.node(float(t)) - rec_curve.node(float(t))), 0)
                       for t in RECIPE.TENORS)
    linkers = res.get("linkers")
    # Доходности ОФЗ-ИН, подставленные инструментом, — рынок даты кривой, как
    # и сама кривая: они названы ниже отдельно, а не «изменёнными входами».
    market = ("curve", "notes_record") + (
        ("ofz_in.date", "ofz_in.settlement_date", "ofz_in.bonds", "ofz_in.source")
        if linkers and linkers["substituted"] else ())
    changed = [p for p in _diff_paths(record_inputs, inp) if not p.startswith(market)]
    weights = "/".join(f"{100 * float(inp['probabilities'][w]):.0f}" for w in ("N", "H", "M"))
    same_w = inp["probabilities"] == record.get("probabilities")
    bei = ""
    if linkers:
        pi = ((cand or {}).get("m_inflation") or {}).get("pi_ss", "—")
        bei = (f"; инфляция мира M — форвардные BEI на {linkers['date']} (реальные доходности ОФЗ-ИН — "
               f"{linkers['source']}), π_ss {(record.get('m_inflation') or {}).get('pi_ss', '—')} → {pi} %")
    text = (f"пересборка миров по рецепту `WORLDS-RECIPE.md` на кривой ОФЗ {curve.date} (узлы 1/3/5/10 лет: "
            f"{moves} б.п. к записи {inp['curve']['date']}){bei}; "
            + (f"входы записи книги {inp['version']} без изменений" if not changed
               else "изменены входы записи: " + ", ".join(changed[:12]) + (" …" if len(changed) > 12 else ""))
            + f"; веса миров {weights}" + (" без изменений" if same_w else "")
            + "; правила яруса 2 — в `CANDIDATE.md`, выбор якорей — решение аудитора")
    return ["## Черновик строки журнала версий (`V<версия>-CHANGES.md`)", "",
            f"| <версия> | <дата> | {text} | {res['journal']} |", "",
            "Числа — ядро на входах книги (дата и цена книги), как в журнале; версию, дату и текст "
            "изменений вписывает аудитор."]


def input_remarks(inp: dict, record: dict, cand: dict, curve) -> list[str]:
    """Замечания инструмента к входам — то, что рецепт делает молча.

    Канон рецепта не правится; здесь названы два места, где его числа зависят
    не от содержания входов. Ключ `grid.first_period_mid_years` записан для
    кривой записи, а рецепт берёт его при любой дате кривой, хотя по его же
    разделу 4 при пересборке mid_0 считается из дат (на 23.10 это 0, а не
    0,03). И сравнение с записью построчное: миры, переставленные во входах
    (так делает `yaml.safe_dump`, сортируя ключи), дают сотни «различий» при
    тех же строках.
    """
    remarks = []
    grid = inp["grid"]
    if grid.get("first_period_mid_years") is not None and curve.date != inp["curve"]["date"]:
        periods = RECIPE.periods_of(grid["first_period"], int(grid["n_periods"]))
        by_dates = RECIPE.first_period_mid({"grid": {}}, curve, periods)
        alt_inp = copy.deepcopy(inp)
        del alt_inp["grid"]["first_period_mid_years"]
        alt = RECIPE.build_worlds(alt_inp, curve)
        alt["asof"] = cand["asof"]
        moved = [abs(a[k] - b[k]) for a, b in zip(cand["rows"], alt["rows"]) for k in a
                 if a[k] != b[k] and isinstance(a[k], (int, float))]
        effect = (f"без ключа кандидат отличается от этого в {len(moved)} полях строк миров, до "
                  f"{100 * max(moved):.0f} б.п." if moved else "без ключа кандидат тот же")
        remarks.append(
            f"`grid.first_period_mid_years` = {grid['first_period_mid_years']:g} во входах задан для кривой "
            f"записи {inp['curve']['date']}, а кривая кандидата — {curve.date}: рецепт берёт ключ как есть "
            f"при любой дате кривой, хотя по его разделу 4 при пересборке mid_0 считается из дат (не меньше "
            f"0) — здесь {by_dates:.4f} лет; {effect} (различий с записью "
            f"{len(RECIPE.compare(record, alt))} вместо {len(RECIPE.compare(record, cand))}). Убрать или "
            "обновить ключ — решение аудитора.")
    # Заметка мира M во входах 1.4 называет дату кривой записи словами
    # («…breakevens of 18.09.2026…»): рецепт подставляет в неё только π_ss и
    # поправку, и `notes` кандидата на другой кривой вышли бы с чужой датой.
    note, rec_day = str((inp.get("m_inflation") or {}).get("note_forward_bei", "")), inp["curve"]["date"]
    if forward_bei(inp) and curve.date != rec_day:
        spelled = dt.date.fromisoformat(rec_day).strftime("%d.%m.%Y")
        if spelled in note:
            remarks.append(
                f"`m_inflation.note_forward_bei` называет дату кривой записи {spelled}, а кривая "
                f"кандидата — {curve.date}: заметка мира M в `notes` кандидата выйдет с чужой датой. "
                "Обновить текст во входах — решение аудитора.")
    order = [list(dict.fromkeys(r["world"] for r in w["rows"])) for w in (cand, record)]
    if order[0] != order[1]:
        remarks.append(f"миры во входах идут в порядке {'/'.join(order[0])}, в записи — {'/'.join(order[1])}: "
                       "рецепт сравнивает строки по порядку, и счёт различий с записью завышен "
                       "перестановкой. Расставить миры во входах как в записи.")
    return remarks


def report(inp: dict, record: dict, cand: dict, curve, anchors: dict, notes: list[str],
           res: dict, release: dict, record_inputs: dict) -> str:
    """Отчёт рецепта как есть, блоки инструмента — перед его последней строкой."""
    record_results = json.loads((BOOK / "results.json").read_text(encoding="utf-8"))
    base = RECIPE.candidate_report(inp, record, cand, curve, anchors, notes,
                                   {"fair_value": res["book_fv"]}, record_results)
    remarks = (["## Замечания инструмента к входам", ""] + [f"- {r}" for r in res["remarks"]] + [""]
               if res["remarks"] else [])
    linkers = linkers_block(res["linkers"], record, cand) + [""] if res.get("linkers") else []
    extra = (remarks + linkers + headline_block(res, release) + [""] + gates_block(res, release) + [""]
             + journal_block(res, inp, record_inputs, record, curve, cand) + [""])
    head, sep, tail = base.rpartition("Кандидат не публикуется")
    if not sep:
        return base + "\n" + "\n".join(extra)
    return head + "\n".join(extra) + "\n" + sep + tail


# ---------------------------------------------------------------- запуск


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def refresh(out: Path, *, inputs: Path = BOOK / "worlds_inputs.yaml", release: str = RELEASE_URL,
            curve: Path | None = None, curve_date: str | None = None,
            state: Path | None = None, use_state: bool = False, ofz_in=None,
            draws: int | None = None) -> dict:
    """Кандидат и отчёт в `out`. Возвращает сводку; исключения — Refused/NotBuilt."""
    out = Path(out)
    if _inside(out, BOOK):
        raise Refused(f"--out {out} внутри книги ({BOOK}): кандидат туда не кладётся")
    inp = read_inputs(Path(inputs))
    payload = read_release(release)
    if curve:
        obs = zcyc_curve(Path(curve), curve_date)
    elif use_state:
        obs = state_curve(state)
    else:
        obs = release_curve(payload)
    record = json.loads((BOOK / "worlds_source.json").read_text(encoding="utf-8"))
    record_inputs = yaml.safe_load((BOOK / "worlds_inputs.yaml").read_text(encoding="utf-8"))
    inp, linkers = linker_inputs(inp, obs, payload, files=ofz_in, state=state, use_state=use_state)
    try:
        cand = RECIPE.build_worlds(inp, obs)
        cand["asof"] = obs.date
        anchors, notes = RECIPE.candidate_anchors(inp, obs)
        remarks = input_remarks(inp, record, cand, obs)
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise Refused(f"входы записи не годятся для рецепта: {type(exc).__name__} {exc}") from exc
    except SystemExit as exc:  # отказы рецепта (защита по дате ОФЗ-ИН) — это входы, а не падение
        raise Refused(f"рецепт отказал: {exc}") from exc

    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out, prefix=".book-") as tmp:
        cand_book = build_book(cand, Path(tmp))
    # Кривые кандидата сняты на дату наблюдения, а не на дату книги из
    # шаблона: от неё идёт перекат по форвардам (`model.core.curve_as_of`).
    cand_book["meta"]["curve_as_of"] = str(obs.date)[:10]
    try:
        res = evaluate(cand_book, load_book(BOOK_YAML), payload, obs, draws=draws)
    except Exception as exc:  # noqa: BLE001 — любая ошибка расчёта = кандидат не собрался
        raise NotBuilt(f"ядро на кандидате: {type(exc).__name__} {exc}") from exc
    res["remarks"], res["linkers"] = remarks, linkers

    # Как пишет сам рецепт: одинаковые байты на одной машине — кандидат
    # инструмента сверяется с кандидатом аудитора побайтово.
    (out / "worlds_source.json").write_text(json.dumps(cand, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "anchors_by_rule.json").write_text(
        json.dumps({"anchors": anchors, "notes": notes}, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "CANDIDATE.md").write_text(
        report(inp, record, cand, obs, anchors, notes, res, payload, record_inputs), encoding="utf-8")
    return dict(res, worlds=cand, anchors=anchors, notes=notes, curve_date=obs.date,
                differences=len(RECIPE.compare(record, cand)))


def check_canon() -> tuple[list[str], dict]:
    """Строгая проверка канона — то же, что `worlds_recipe.py --check` из каталога книги.

    Рецепт на входах книги обязан дать `worlds_source.json` книги целиком:
    63 строки, справедливые кривые, веса и их диапазоны, заметки, `asof`,
    `units` и с 1.4 блоки `lt_inflation` и `m_inflation`. Кандидат строится
    тем же рецептом, и если канон им не воспроизводится, кандидату верить
    нельзя. Сравнение — сначала по полям (чтобы назвать различия), затем
    объекта целиком: ключ, которого `compare` рецепта не знает, тоже различие.
    """
    record = json.loads((BOOK / "worlds_source.json").read_text(encoding="utf-8"))
    inputs = yaml.safe_load((BOOK / "worlds_inputs.yaml").read_text(encoding="utf-8"))
    rebuilt = RECIPE.build_worlds(inputs)
    diffs = RECIPE.compare(record, rebuilt)
    if len(rebuilt["rows"]) != len(record["rows"]):
        diffs.append(f"строк {len(rebuilt['rows'])} против {len(record['rows'])} в записи")
    if not diffs and rebuilt != record:
        diffs.append("объекты различаются вне полей сравнения рецепта: "
                     + ", ".join(sorted(k for k in set(record) | set(rebuilt) if record.get(k) != rebuilt.get(k))))
    return diffs, rebuilt


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", help="каталог кандидата (вне data/assumptions/); не нужен при --check")
    ap.add_argument("--check", action="store_true",
                    help="только строгая проверка: рецепт на входах книги воспроизводит её worlds_source.json "
                         "целиком (как worlds_recipe.py --check); код 0 или 2")
    ap.add_argument("--inputs", default=str(BOOK / "worlds_inputs.yaml"), help="входы записи (YAML)")
    ap.add_argument("--release", default=RELEASE_URL, help="URL или файл выпуска (/api/model)")
    ap.add_argument("--curve", help="снимок MOEX ISS zcyc.json вместо кривой выпуска")
    ap.add_argument("--curve-date", help="дата снимка --curve (ГГГГ-ММ-ДД), если в нём нет tradedate")
    ap.add_argument("--state", nargs="?", const="", default=None,
                    help="кривая из рядов moex.zcyc.* состояния (каталог indicators/; по умолчанию штатный)")
    ap.add_argument("--draws", type=int, default=None,
                    help="прогонов полосы A-V9 на кандидате (по умолчанию — как в книге, 2 000 ≈ 40 с)")
    ap.add_argument("--ofz-in", nargs="+", metavar="ФАЙЛ",
                    help="реальные доходности ОФЗ-ИН на дату кривой: история торгов ISS (YIELDCLOSE) "
                         "или YAML/JSON-блок ofz_in; без ключа — входы записи на ту же дату или выпуск")
    args = ap.parse_args(argv)
    if args.check:
        diffs, rebuilt = check_canon()
        if diffs:
            print(f"НЕ воспроизведено: {len(diffs)} различий", file=sys.stderr)
            print("\n".join(diffs[:40]), file=sys.stderr)
            return NOT_BUILT
        print(f"воспроизведено: {len(rebuilt['rows'])} строк, кривые {sorted(rebuilt['fair_zero_curve_today'])}, "
              f"блоки {', '.join(k for k in ('lt_inflation', 'm_inflation') if k in rebuilt) or '—'} — "
              f"совпадают с {BOOK / 'worlds_source.json'} строго")
        return OK
    if not args.out:
        ap.error("--out обязателен (кроме --check)")
    try:
        res = refresh(Path(args.out), inputs=Path(args.inputs), release=args.release,
                      curve=Path(args.curve) if args.curve else None, curve_date=args.curve_date,
                      state=Path(args.state) if args.state else None, use_state=args.state is not None,
                      ofz_in=[Path(p) for p in args.ofz_in] if args.ofz_in else None, draws=args.draws)
    except Refused as exc:
        print(f"НЕГОДНЫЕ ВХОДЫ: {exc}", file=sys.stderr)
        return BAD_INPUT
    except NotBuilt as exc:
        print(f"КАНДИДАТ НЕ СОБРАЛСЯ: {exc}", file=sys.stderr)
        return NOT_BUILT
    fmt = lambda xs: " / ".join(f"{x:.2f}" for x in xs)  # noqa: E731
    blocking = [g.key for g in res["gates"] if g.blocking] + [f.key for f in res["invariants"]]
    print(f"кандидат записан: {args.out} (кривая {res['curve_date']}, {res['differences']} различий с записью)")
    if res["linkers"]:
        linkers, pi = res["linkers"], (res["worlds"].get("m_inflation") or {}).get("pi_ss")
        print(f"ОФЗ-ИН на {linkers['date']} ({linkers['source']}): "
              + ", ".join(f"{s} {v:.2f}" for s, v in linkers["yields"].items()) + f" %; π_ss мира M {pi} %")
    for remark in res["remarks"]:
        print(f"замечание: {remark}")
    print(f"заголовок на входах выпуска: движок {fmt(res['engine'])} ₽; запись {fmt(res['record'])} ₽")
    print(f"на входах книги: движок {fmt(res['book_engine'])} ₽")
    print(headline_line(res["record_headline"], res["book_headline"]).replace("**", ""))
    print("гейты: " + ("сборка выпуска на кандидате прошла бы" if not blocking
                       else "сборка выпуска на кандидате упала бы: " + ", ".join(sorted(set(blocking)))))
    return OK


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
