"""Сборка payload дашборда (`schema: lenta-v1`).

Контракт из брифа, п. 7. Правило одно: **в payload попадает только то, что
пересчитывается из книги и фактов**. Никаких чисел, набранных руками, —
иначе дашборд однажды покажет то, чего в модели нет.

Размер ≤ 500 КБ. Проверка схемы терпима к новым полям: добавление поля не
должно ломать фронт, удаление — должно.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import pickle
import platform
import re
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from statistics import NormalDist

from indicators import issuer
from model.book import (BOOK_YAML, book, get_path, jump_guard, median_draws, named_cells,
                        path_value, periods)
from model.checks import (CORRIDOR_BASIS, RELEASE_LABEL, Finding, check_limited_liability,
                          check_security_card, corridor_history, gate_corridors, summarize_gates)
from model.core import CellResult, bridge_lines, time_position
from model.engine import (
    Release,
    judgement_table,
    run_release,
)
from model.attribution import attribute, inputs_snapshot, total
from model.financing import (
    credit_limit,
    rate_sensitivity_net,
)
from model.grid import GridCell
from model.paths import CHECKS_DIR

SCHEMA = "lenta-v1"
REGIME_TITLES = {"stress": "Стресс", "floor": "Удержание дна",
                 "partial": "Частичный возврат", "full": "Полный возврат"}
CAPEX_TITLES = {"low": "низкий", "base": "базовый", "high": "высокий"}


def _plain(value: float, digits: int) -> float:
    """Округление без «−0,0»: крошечный минус (колл на активах ≤ 0, разность
    σ у самой калибровки) округляется в −0,0, а в выпуске ноль пишется нулём."""
    return round(value, digits) + 0.0


def _round(value, digits: int = 4):
    if isinstance(value, float):
        return round(value, digits)
    if isinstance(value, dict):
        return {k: _round(v, digits) for k, v in value.items()}
    if isinstance(value, list):
        return [_round(v, digits) for v in value]
    return value


def _annual_rows(result: CellResult) -> list[dict]:
    """Годовые строки. Неполный год помечается: у него EBITDA за одно
    полугодие, а рычаг и покрытие считаны на скользящий год — сравнивать их
    между собой нельзя, и на экране это должно быть видно."""
    return [
        dict(year=a["year"], revenue=round(a["revenue"], 1), ebitda=round(a["ebitda"], 1),
             margin=round(a["margin"], 5), capex=round(a["capex"], 1),
             capex_pct=round(a["capex_pct"], 5), fcff=round(a["fcff"], 1),
             tax=round(a["tax_actual"], 1), interest=round(a["net_interest"], 1),
             net_debt=round(a["net_debt"], 1), leverage=round(a["leverage"], 2),
             interest_cover=round(min(a["interest_cover"], 99), 2),
             dividends=round(a["dividends"], 1), area=round(a["area_end"], 0),
             lfl=round(a["lfl"], 5),
             partial=a.get("halves", 2) < 2)
        for a in result.annual()
    ]


def _scenario(name: str, result: CellResult, weight: float, A: dict) -> dict:
    return dict(
        name=name,
        title=A["scenario_names"][name].split(":")[0],
        narrative=A["scenario_names"][name],
        cell=result.cell.as_dict(),
        weight=round(weight, 4),
        claims_lines=claims_lines(A, result),
        price=round(result.price, 0),
        price_published=round(result.price_floor, 0),
        equity=round(result.equity, 1),
        equity_before_governance=round(result.equity_before_governance, 1),
        debt_cost_addon=round(result.debt_cost_addon, 2),
        max_leverage=round(result.max_leverage, 2),
        deficit=round(max(0.0, -result.equity), 1),
        ev=round(result.ev, 1),
        ev_ebitda_ltm=round(result.ev_ebitda_ltm, 2),
        ev_ebitda_ntm=(round(result.ev_ebitda_ntm, 2)
                       if result.ev_ebitda_ntm is not None else None),
        pv_fcff=round(result.pv_fcff, 1),
        pv_tax_shield=round(result.pv_tax_shield, 1),
        terminal_share=round(result.terminal_share, 4),
        claims=round(result.claims, 1),
        exit_multiple=round(result.exit_multiple, 2),
        r_long=round(result.r_long, 4),
        r_real=round(result.r_real, 4),
        annual=_annual_rows(result),
        # Блок юнит-экономики магазина УБРАН (аудит второй итерации): он
        # давал IRR 48–53 % и окупаемость 2 года против 21 % и 4,6 года
        # в карточке книги — без налога, без каннибализации, с плотностью
        # 100 % и маржой «цель режима + 2,5 п.п.» литералом. Вердикт
        # `passes: true` стоял во всех клетках и не проверял ничего.
        # На цену он не влиял; на экране не показывался.
        # Постатейная раскладка маржи (`lines`, модуль model/lines.py 850oa)
        # снята при переносе: статьи e-com и литералы якоря были магнитовскими,
        # в число она не входила (порт P1, H\reports\P1-port.md).
    )


# Допуск сверки весов слоёв. Не «примерно»: веса и агрегаты слоя считаются из
# одних и тех же чисел, и разойтись они могут только ошибкой. 1e-9 — запас на
# порядок суммирования float, не на вольность.
LAYER_WEIGHTS_TOLERANCE = 1e-9


def layer_cell_weights(release) -> dict[str, dict[str, float]]:
    """Веса клеток КАЖДОГО слоя — в выпуск (задание B3(1)).

    Зачем это в выпуске. Сверка весов с контрольной моделью до сих пор
    сравнивала пересчёт формулы книги с пересчётом формулы книги: ядро своих
    весов наружу не отдавало (`Layer` их не хранит), и «веса совпадают» было
    утверждением о двух копиях одной формулы. Теперь веса лежат в выпуске, и
    сверять с `cell_weights` контрольной модели есть что.

    **Как они сверяются с ядром, а не просто пересчитываются.** Веса
    выводятся здесь по книге, но затем ими ЗАНОВО собирается слой — тем же
    `model.grid.layer_stats`, которым его собрало ядро, — и все шестнадцать
    чисел слоя сравниваются с числами выпуска. Совпадение шестнадцати
    агрегатов (V₀, требования, пять перцентилей, две вероятности, вменённые
    потери кредиторов) на 36 клетках означает, что веса ТЕ САМЫЕ: подобрать
    другой набор, дающий те же шестнадцать сумм, нельзя иначе как случайно.
    Худшее расхождение печатается рядом с весами (`cell_weights_gap`), и
    `validate` требует, чтобы оно было нулевым.
    """
    from model.grid import (
        GridCell,
        layer_stats,
        regime_table,
        regime_unconditional,
    )

    A = release.book
    J = A["joint"]
    table, unconditional = regime_table(A), regime_unconditional(A)
    capex = J["capex_prob_given_regime"]
    by_key = {c.cell.key: c for c in release.cells}

    def weights(name: str) -> dict[str, float]:
        raw: dict[str, float] = {}
        if name == "macro_neutral":
            # Слой «рыночные ставки как есть» — один мир, поэтому режимы
            # берутся БЕЗУСЛОВНЫЕ: внутри одного мира условные дали бы другую
            # смесь (так считает `grid.layers`, и так же — контрольная модель).
            world = J["macro_neutral_world"]
            for regime, p_regime in unconditional.items():
                for level, p_level in capex[regime].items():
                    raw[f"{world}|{regime}|{level}"] = p_regime * p_level
        else:
            world_prob = (J["world_prob"] if name == "analytical"
                          else J["world_prob_market_implied"])
            for world, p_world in world_prob.items():
                for regime, p_regime in table[world].items():
                    for level, p_level in capex[regime].items():
                        raw[f"{world}|{regime}|{level}"] = p_world * p_regime * p_level
        total = sum(v for v in raw.values() if v > 0) or 1.0
        return {k: v / total for k, v in raw.items() if v > 0}

    out = {}
    for name in release.layers:
        share = weights(name)
        rebuilt = layer_stats(A, [GridCell(by_key[k].cell, w, by_key[k].result)
                                  for k, w in share.items()], name)
        out[name] = dict(weights=share, gap=_layer_gap(rebuilt, release.layers[name]))
    return out


def _layer_gap(rebuilt, engine) -> float:
    """Худшее относительное расхождение слоя, пересобранного из весов, с ядром.

    Сравниваются ВСЕ числовые поля слоя. Относительное, а не абсолютное:
    требования измеряются сотнями миллиардов, вероятности — долями единицы, и
    один абсолютный допуск на оба был бы либо слепым, либо ложным.
    """
    import dataclasses

    worst = 0.0
    for item in dataclasses.fields(rebuilt):
        was, now = getattr(engine, item.name), getattr(rebuilt, item.name)
        if not isinstance(was, (int, float)) or isinstance(was, bool):
            continue
        scale = max(abs(was), 1.0)
        worst = max(worst, abs(now - was) / scale)
    return worst


def _grid_block(cells: list[GridCell], limit: float) -> list[dict]:
    """Клетки сетки. `over_credit_limit` — не украшение: ветки докапитализации
    в модели нет, и клетка, где долг перерастает доступные линии, входит в
    среднее как есть. Это должно быть видно, а не скрыто."""
    return [
        dict(world=c.cell.world, regime=c.cell.margin_regime, capex=c.cell.capex,
             probability=round(c.probability, 5),
             price=round(c.result.price, 0),
             price_published=round(c.result.price_floor, 0),
             ev=round(c.result.ev, 1),
             ev_ebitda=round(c.result.ev_ebitda_ltm, 2),
             equity=round(c.result.equity, 1),
             debt_cost_addon=round(c.result.debt_cost_addon, 2),
             max_leverage=round(c.result.max_leverage, 2),
             max_gross_debt=round(c.result.max_gross_debt, 1),
             over_credit_limit=bool(c.result.max_gross_debt > limit))
        for c in cells
    ]


def release_root():
    """Каталог выпусков. На проде — StateDirectory, локально — `var/release`."""
    import os
    from pathlib import Path as _Path

    return (_Path(os.environ["LENTA_STATE_DIR"]) / "release"
            if os.environ.get("LENTA_STATE_DIR")
            else _Path(__file__).resolve().parents[1] / "var" / "release")


# Каталоги (и файл зависимостей), правка которых меняет ЧИСЛО. `docs/`,
# `data/checks/` и `web/` сюда не входят намеренно: их перезаписывает генератор
# отчётов, который зовут тесты, и пометка «грязное» стояла бы на каждом боевом
# выпуске. `requirements.txt` — входит: другая версия numpy/yaml — другой
# расчёт. Тот же список — `CODE_DIRS` в `ops/run.sh` (сверяет
# `tests/test_run_sh.py::test_run_sh_code_dirs_are_the_release_code_dirs`).
CODE_DIRS = ("model", "indicators", "ops", "data/assumptions", "data/facts", "requirements.txt")

_ENGINE_COMMIT: str | None = None


def engine_commit() -> str:
    """Коммит КОДА, которым собран выпуск. Пустая строка — узнать не удалось.

    Аудит третьей итерации, A5: атрибуция «Что изменилось» не знала шага
    «код/методика», и +32 ₽ от приведения терминала к книге напечатались как
    «прочее (взаимодействие шагов) · версии совпадают — остаток расчёта».
    Версии действительно совпадали: книга та же, факты те же. Изменился код —
    и об этом выпуск ничего не знал.

    На сервере тождество точное: `ops/run.sh` делает `git reset --hard
    origin/v5` перед каждым тактом, то есть HEAD и есть выложенный код. На
    ноутбуке дерево может быть грязным, и тогда к хэшу добавляется `+грязное`:
    выпуск, собранный из неотправленных правок, не имеет права называть чужой
    коммит своим.

    Грязь считается ТОЛЬКО по коду, который считает число (`CODE_DIRS`). Иначе
    пометка стояла бы всегда и ничего не значила: такт прогоняет тесты ПЕРЕД
    сборкой, а `tests/test_legacy_bridge.py` запускает
    `ops/tools/publish_reports.py`, и тот перезаписывает `docs/*.md` и
    `data/checks/control_model.json` — то есть к моменту сборки дерево грязно
    на каждом боевом прогоне.

    Ошибка здесь не роняет сборку: отсутствие git — причина не печатать хэш, а
    не причина не публиковать оценку.
    """
    global _ENGINE_COMMIT
    if _ENGINE_COMMIT is not None:
        return _ENGINE_COMMIT
    import subprocess

    root = Path(__file__).resolve().parents[1]

    def git(*args: str) -> str | None:
        try:
            done = subprocess.run(("git",) + args, cwd=root, capture_output=True,
                                  text=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout.strip() if done.returncode == 0 else None

    head = git("rev-parse", "HEAD")
    if not head:
        _ENGINE_COMMIT = ""
        return _ENGINE_COMMIT
    dirty = git("status", "--porcelain", "--untracked-files=no", "--", *CODE_DIRS)
    _ENGINE_COMMIT = head[:40] + ("+грязное" if dirty else "")
    return _ENGINE_COMMIT


def previous_release(*, different_from: str | None = None) -> dict | None:
    """Последний выпуск с ДРУГИМ содержанием, а не просто предыдущий файл.

    Аудит второй итерации: три сборки на одинаковых входах дали три разных
    релиза, а блок «Что изменилось» после повтора показывал нули. Причина —
    ссылка на собственный прошлый выпуск входила в хэш, поэтому повтор всегда
    отличался от предшественника, и «прошлым» оказывалась копия себя.

    Теперь хэш считается без блока `changes`, а «прошлым выпуском» считается
    последний, чьё СОДЕРЖАНИЕ отличается от нынешнего. Повтор прогона на тех
    же входах видит перед собой тот же предшественник, что и первый прогон, и
    «Что изменилось» остаётся тем же текстом вместо нулей.
    """
    root = release_root()
    candidates = []
    latest = root / "latest.json"
    if latest.exists():
        candidates.append(latest)
    # Остальные выпуски — по времени записи, новые первыми: `latest.json`
    # может оказаться копией нынешнего содержания, и тогда нужен тот, что был
    # до него.
    candidates += sorted((p for p in root.glob("*.json") if p.name != "latest.json"),
                         key=lambda p: p.stat().st_mtime, reverse=True)
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if different_from and data.get("meta", {}).get("payload_sha256") == different_from:
            continue
        return data
    return None


def _changes_block(A: dict, release, *, own_digest: str | None = None) -> dict:
    """«Что изменилось» с прошлого выпуска. Первый выпуск честно говорит, что не с чем.

    `own_digest` — хэш СОБСТВЕННОГО содержания: выпуск с тем же содержанием
    не может быть своим же предшественником.
    """
    previous = previous_release(different_from=own_digest)
    if previous is None:
        return dict(note="первый выпуск — сравнивать не с чем", items=[], total=None)

    def run(book_dict):
        from model.engine import run_release as _run

        result = _run(book_dict, gates=False)
        return result.layers["macro_neutral"].v0, result.fair_value.central

    try:
        steps = attribute(previous, A, run=run, engine_commit=engine_commit())
    except Exception as exc:                      # noqa: BLE001
        return dict(note=f"разложение не сошлось: {type(exc).__name__}", items=[], total=None)
    if not steps:
        return dict(note="в прошлом выпуске нет снимка входов", items=[], total=None)

    delta_v0, delta_price = total(steps)
    return dict(
        note="изменение с прошлого выпуска, разложенное по источникам",
        previous=previous["meta"].get("payload_sha256", "")[:12],
        items=[dict(key=s.key, title=s.title, delta_v0=round(s.delta_v0, 2),
                    delta_price=round(s.delta_price, 1), note=s.note) for s in steps],
        total=dict(delta_v0=round(delta_v0, 2), delta_price=round(delta_price, 1)),
    )


CONTROL_SUMMARY = CHECKS_DIR / "control_model.json"


def _control_model_block() -> dict:
    """Сводка сверки с независимой контрольной моделью — в выпуск.

    Читается готовым файлом, а не считается здесь: контрольная модель живёт в
    `tests/`, и тянуть тестовый каталог в боевую сборку нельзя. Файл
    генерируется `ops/tools/publish_reports.py` вместе со страницей
    `docs/CONTROL-MODEL.md`, а тест следит, чтобы он не разошёлся с расчётом.
    """
    if not CONTROL_SUMMARY.exists():
        return {}
    return json.loads(CONTROL_SUMMARY.read_text(encoding="utf-8"))


def _checks_block(findings: list[Finding], peers: dict | None = None) -> dict:
    """Инварианты, находки гейтов по клеткам и коридоры гейтов против истории «Ленты».

    `corridors` — коридор каждого гейта с коридором (данные `gate_explanations.yaml`,
    E20), его основание (`model.checks.CORRIDOR_BASIS`) и история компании из
    фактов (`model.checks.corridor_history`); у EV/EBITDA истории цен в фактах
    нет — рядом нынешний мультипликатор эмитента на той же базе (`peers`).
    """
    corridors = gate_corridors()
    history = _soft(corridor_history)
    subject = next((r for r in (peers or {}).get("rows") or [] if r.get("key") == issuer.TICKER),
                   None)
    block = {}
    for key, corridor in corridors.items():
        entry = dict(corridor=(dict(corridor) if isinstance(corridor, dict) else list(corridor)),
                     basis=CORRIDOR_BASIS.get(key, ""))
        if key in history:
            h = history[key]
            entry["history"] = dict(annual={p: round(v, 5) for p, v in h["annual"].items()},
                                    halves_range=_round(h["halves_range"], 5),
                                    window_from=h["window_from"],
                                    annual_outside=h["annual_outside"],
                                    window_outside=h["window_outside"])
        elif key == "ev_ebitda":
            entry["history"] = dict(current=(subject or {}).get("ev_ebitda"),
                                    current_date=(subject or {}).get("price_date"),
                                    basis="EV/EBITDA LTM эмитента на одной базе с аналогами "
                                          "(`market.peers_same_base`)")
        block[key] = entry
    return dict(
        invariants_broken=sum(1 for f in findings if f.blocking),
        gates=[dict(key=f.key, label=f.label, message=f.message) for f in findings
               if not f.blocking],
        corridors=block,
        control_model=_control_model_block(),
    )


# Человеческие названия рядов. Аудит 850oa: на экране стояли машинные
# идентификаторы рядов — владелец читает панель, а не отлаживает конвейер.
# Ряды эмитента называются `<префикс>.<имя>` (`indicators.issuer`); сборщики и
# имена рядов — `indicators/sources.py` (D15). Точное имя — здесь, семейства
# рядов (облигации, кривая, датабук, вакансии по ИНН) — правилами
# `indicator_title`.
INDICATOR_TITLES = {
    "cbr.key_rate": "Ключевая ставка ЦБ",
    issuer.PRICE_SERIES: f"Котировка {issuer.TICKER}",
    f"moex.security.{issuer.TICKER}.listlevel": f"Уровень листинга {issuer.TICKER} на Мосбирже",
    f"moex.security.{issuer.TICKER}.issuesize": f"Акций в выпуске {issuer.TICKER}",
    issuer.series("bonds.traded"): "Облигаций группы в обращении (выпусков)",
    issuer.series("databook.published"): "Датабук: последняя версия на сайте (дата публикации)",
    issuer.series("databook.new_version"): "Датабук: новая версия скачана (сигнал «вышел отчёт»)",
    issuer.series("disclosure.new"): "Раскрытия: новых сообщений с прошлого снимка",
    issuer.series("disclosure.events"): "Раскрытия: события (дата по п. 1.7 сообщения)",
    issuer.series("disclosure.latest"): "Раскрытия: последнее сообщение",
    issuer.series("disclosure.results"): "Раскрытия: отчётность и результаты",
    issuer.series("disclosure.ma"): "Раскрытия: сделки M&A",
    issuer.series("disclosure.dividends"): "Раскрытия: дивиденды",
    issuer.series("disclosure.own_shares"): "Раскрытия: собственные акции",
    issuer.series("disclosure.listing"): "Раскрытия: листинг",
    issuer.series("disclosure.offer"): "Раскрытия: оферты",
    issuer.series("disclosure.placement"): "Раскрытия: размещения",
    issuer.series("disclosure.coupon"): "Раскрытия: выплаты по бумагам",
    issuer.series("disclosure.redemption"): "Раскрытия: погашения облигаций",
    issuer.series("disclosure.rating"): "Раскрытия: рейтинговые действия",
    issuer.series("disclosure.treasury"): "Раскрытия: квазиказначейские акции",
    issuer.series("disclosure.board"): "Раскрытия: совет директоров и собрания",
    issuer.series("disclosure.material"): "Раскрытия: существенное влияние на цену",
    issuer.series("disclosure.programme"): "Раскрытия: программа облигаций",
    issuer.series("disclosure.record_date"): "Раскрытия: дата фиксации прав",
    issuer.series("disclosure.other"): "Раскрытия: прочее",
    issuer.series("vacancies.total"): "Вакансии группы на «Работе России»",
    issuer.series("vacancies.with_salary"): "Вакансии группы с зарплатной вилкой",
    issuer.series("salary.vacancy_chain_low"): "Зарплатный индекс вилок: нижняя граница (цепной)",
    issuer.series("salary.vacancy_chain_high"): "Зарплатный индекс вилок: верхняя граница (цепной)",
    issuer.series("salary.vacancy_chain_matched"): "Зарплатный индекс вилок: совпавших вакансий в звене",
    issuer.series("salary.cell_chain_low"): "Зарплатный индекс вилок: нижняя граница (роль × регион)",
    issuer.series("salary.cell_chain_high"): "Зарплатный индекс вилок: верхняя граница (роль × регион)",
}

# Строки квартального блока датабука (`sources.databook_series_id`) — справочно.
DATABOOK_TITLES = {
    "revenue": "выручка квартала", "ebitda_pre16": "EBITDA квартала (IAS 17)",
    "ebitda_margin_pre16": "маржа EBITDA квартала (IAS 17)",
    "ebitdar_pre16": "EBITDAR квартала (IAS 17)", "gross_profit": "валовая прибыль квартала",
    "lease": "аренда квартала", "net_income_pre16": "чистая прибыль квартала (IAS 17)",
    "net_interest_pre16": "чистые проценты квартала (IAS 17)", "payroll": "расходы на персонал квартала",
    "pbt_pre16": "прибыль до налога квартала (IAS 17)",
}
DATABOOK_SEGMENTS = {
    "hyper": "гипермаркеты", "super": "супермаркеты", "conv": "у дома", "droge": "дрогери",
    "remi": "«Реми»", "diy": "«Дом Лента»", "wholesale": "опт", "other": "прочие форматы",
    "retail": "розница", "utkonos": "«Утконос»",
}

# Группы рядов на экране «Ближайший отчёт» — в самом выпуске (`indicators[].group`),
# чтобы витрина не держала префиксов эмитента (study/05, 13.3).
INDICATOR_GROUPS = {
    "market": "Рынок и ставки",
    "bonds": "Облигации группы («О'КЕЙ»)",
    "databook": "Отчётность: датабук",
    "disclosure": "Раскрытия и карточка акции",
    "labor": "Вакансии и зарплаты (справочно)",
    "other": "Прочее",
}


def indicator_group(series_id: str) -> str:
    if series_id.startswith(("cbr.", "moex.zcyc.", "moex.ofz_in.", "moex.price.")):
        return "market"
    if series_id.startswith((issuer.series("bond."), issuer.series("bonds."))):
        return "bonds"
    if series_id.startswith(issuer.series("databook.")):
        return "databook"
    if series_id.startswith((issuer.series("disclosure."), "moex.security.")):
        return "disclosure"
    if series_id.startswith((issuer.series("vacancies."), issuer.series("salary."))):
        return "labor"
    return "other"


# Ряды, помеченные на витрине как СПРАВОЧНЫЕ: собираются, показываются, но ни
# в одно уравнение не входят. Помечать обязательно — иначе плитка рядом с
# работающими индикаторами читается как драйвер оценки. У «Ленты» в расчёт
# выпуска входят только котировка эмитента (живая цена), ключевая ставка (канал
# процентов) и кривая (диагностика книги); облигации «О'КЕЙ», датабук, вакансии и
# зарплатный индекс — справочно (P3, §10; D15: индекс вилок — только через
# правило допуска). Совпадение — по префиксу.
REFERENCE_ONLY: tuple[str, ...] = (
    issuer.series("bond."), issuer.series("bonds."), issuer.series("databook.q."),
    issuer.series("vacancies."), issuer.series("salary."), "moex.price.",
)
# Котировка эмитента — вход выпуска (живая цена), не справочный ряд.
REFERENCE_EXCEPT: tuple[str, ...] = (issuer.PRICE_SERIES,)


def is_reference_only(series_id: str) -> bool:
    return (series_id.startswith(REFERENCE_ONLY) and series_id not in REFERENCE_EXCEPT)


# Плитки экрана «Ближайший отчёт» (с линией ряда за год) — независимо от того,
# как их отсортировал конвейер. Состав — задание P4b: котировка эмитента,
# ключевая, кривая (10 лет), облигации «О'КЕЙ» (все доходности выпусков в
# обращении — `INDICATOR_TILE_FAMILIES`), сигнал датабука, события раскрытия,
# уровень листинга, зарплатный индекс «Работы России» (справочно). Плитки, ряда
# которой ещё нет (первый такт, индекс без второго дня обхода), в выпуске —
# строка с `missing`: пустое место на экране называет причину.
INDICATOR_TILES = (
    issuer.PRICE_SERIES,
    "cbr.key_rate",
    "moex.zcyc.10y",
    issuer.series("databook.new_version"),
    issuer.series("disclosure.events"),
    f"moex.security.{issuer.TICKER}.listlevel",
    issuer.series("salary.vacancy_chain_low"),
    issuer.series("salary.vacancy_chain_high"),
)
# Семейства плиток: (префикс, суффикс) → все ряды семейства — плитки; нет ни
# одного — строка `missing` с идентификатором `<префикс>*<суффикс>`.
INDICATOR_TILE_FAMILIES = ((issuer.series("bond."), ".yield"),)
INDICATOR_TILE_MISSING = {
    issuer.series("salary.vacancy_chain_low"):
        "индекс считается со второго дня обхода «Работы России» с вилками",
    issuer.series("salary.vacancy_chain_high"):
        "индекс считается со второго дня обхода «Работы России» с вилками",
}


def is_tile(series_id: str) -> bool:
    return series_id in INDICATOR_TILES or any(
        series_id.startswith(prefix) and series_id.endswith(suffix)
        for prefix, suffix in INDICATOR_TILE_FAMILIES)


# Префиксы рядов-уровней с базой 1 (печатаются как изменение к базе): цепной и
# ячеечный зарплатные индексы вилок (`indicators/salary_index.py`); счётчик
# совпавших вакансий звена — не уровень.
INDEX_PREFIXES: tuple[str, ...] = (issuer.series("salary.vacancy_chain_"),
                                   issuer.series("salary.cell_chain_"))
INDEX_EXCEPT_SUFFIXES = ("_matched",)


# Единица измерения ряда. Без неё экран печатал «0,1400» там, где имелось в
# виду «14,00 %», и «0,9995» вместо «99,95 % номинала»: доля единицы — это
# внутреннее представление, а не то, чем меряют ставку.
INDICATOR_UNITS = ("share", "price", "price_pct_nominal", "count", "index", "bn", "level",
                   "number")


def indicator_unit(series_id: str) -> str:
    if (INDEX_PREFIXES and series_id.startswith(INDEX_PREFIXES)
            and not series_id.endswith(INDEX_EXCEPT_SUFFIXES)):
        return "index"          # уровень с базой 1: печатается как изменение
    if series_id.startswith(issuer.series("bond.")):
        # Цена облигации ISS — доля номинала; доходность — доля годовых.
        return "price_pct_nominal" if series_id.endswith(".price") else "share"
    if series_id.startswith("moex.price."):
        return "price"
    if series_id.startswith(issuer.series("databook.q.")):
        return "share" if series_id.endswith("margin_pre16") else "bn"
    if series_id == f"moex.security.{issuer.TICKER}.listlevel":
        return "level"
    if series_id.startswith((issuer.series("disclosure."), issuer.series("vacancies."),
                             issuer.series("databook."), issuer.series("bonds."),
                             "moex.security.")) or series_id.endswith(
            ("_matched", ".total", ".unregistered")):
        return "count"
    if series_id.startswith(("cbr.", "moex.zcyc.", "moex.ofz_in.")):
        return "share"          # доля единицы → проценты
    return "number"


def indicator_title(series_id: str) -> str:
    if series_id in INDICATOR_TITLES:
        return INDICATOR_TITLES[series_id]
    bond = issuer.series("bond.")
    if series_id.startswith(bond):
        isin, _, what = series_id[len(bond):].partition(".")
        return f"Облигация {isin}: " + {"price": "цена, % номинала",
                                        "yield": "доходность"}.get(what, what)
    databook = issuer.series("databook.q.")
    if series_id.startswith(databook):
        name = series_id[len(databook):]
        if name.startswith("revenue."):
            seg = name.split(".", 1)[1]
            return f"Датабук: выручка квартала — {DATABOOK_SEGMENTS.get(seg, seg)}"
        return "Датабук: " + DATABOOK_TITLES.get(name, name.replace("_", " "))
    vacancies = issuer.series("vacancies.total.")
    if series_id.startswith(vacancies):
        return f"Вакансии на «Работе России»: ИНН {series_id[len(vacancies):]}"
    if series_id.startswith("moex.zcyc."):
        # Срок узла — всё после префикса, с десятичной запятой: «0,25y».
        # Разрез по последней точке давал «25y», а узел 0,5 года печатался
        # «5y» — как настоящий пятилетний (сверка 850oa 25.09.2026).
        return "Кривая ОФЗ, " + series_id[len("moex.zcyc."):].replace(".", ",")
    if series_id.startswith("moex.ofz_in."):
        return "ОФЗ-ИН (линкер) " + series_id.rsplit(".", 1)[-1]
    if series_id.startswith("moex.price."):
        return "Котировка " + series_id.rsplit(".", 1)[-1]
    # Осмысленного названия не нашлось — но печатать машинный идентификатор
    # всё равно нельзя: владелец читает панель, а не отлаживает конвейер.
    # Точки на пробелы и подчёркивания на пробелы — не перевод, но читаемо,
    # а отсутствие строки в словаре видно тестом.
    return series_id.replace("_", " ").replace(".", " · ")


# Ряды, которые СОБИРАЮТСЯ, но на экран не идут (история в хранилище, которую
# печатать рядом с действующими рядами значило бы предлагать выбор, какому
# верить). У «Ленты» пока нет.
HIDDEN_SERIES: frozenset[str] = frozenset()

# История плитки индикатора — год до её последней точки (решение владельца
# 25.09.2026: у плиток экрана «Ближайший отчёт» — линия ряда, а не одна
# цифра). Каждая точка — последняя версия значения на сегодня
# (`Series.history`, один проход по ряду). Только плитки: у рядов в таблицах
# экрана — последняя точка, как прежде; год посуточной ключевой ставки —
# ≈370 точек, ≈6 КБ выпуска.
INDICATOR_HISTORY_DAYS = 366


def _period_day(period: str):
    try:
        return date.fromisoformat(str(period)[:10])
    except ValueError:
        return None


def tile_history(series) -> list[list]:
    """[[период, значение], …] за год до последней точки ряда, по возрастанию.

    Версии — все полученные (`as_of` без предела, как у `Series.latest`, по
    которой печатается цифра плитки): иначе на машине западнее UTC линия
    обрывалась бы на точке раньше цифры. Нечисловое значение точки
    пропускается: история — справочная, и выпуск из-за неё не встаёт.
    """
    points = [(p, v) for p, v in series.history(as_of="9999-12-31").items()
              if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)]
    if not points:
        return []
    last = _period_day(points[-1][0])
    if last is not None:
        start = last - timedelta(days=INDICATOR_HISTORY_DAYS)
        points = [(p, v) for p, v in points if (_period_day(p) or last) >= start]
    return [[p, round(v, 6)] for p, v in points]


def _indicator_row(series_id: str, *, series=None, point=None, tile: bool) -> dict:
    group = indicator_group(series_id)
    row = dict(id=series_id, title=indicator_title(series_id), unit=indicator_unit(series_id),
               group=group, group_title=INDICATOR_GROUPS[group],
               channel=series.channel if series is not None else None,
               period=point.period if point is not None else None,
               value=(round(point.value, 6) if point is not None and point.value is not None
                      else None),
               note=(point.note[:120] if point is not None else ""),
               points=len(series.points) if series is not None else 0,
               tile=tile, reference_only=is_reference_only(series_id))
    return row


def _indicators_block(store=None) -> list[dict]:
    """Ряды индикаторов: последнее значение, дата, канал, группа; плитки — с историей.

    Слой может быть ещё не собран (первый запуск) — тогда рядов нет, а у
    объявленных плиток строка `missing`: это не ошибка, а честное «данных нет».
    """
    try:
        from indicators.store import Store
    except ImportError:  # pragma: no cover
        return []
    store = store or Store()
    out, seen = [], set()
    for series in store.all_series():
        point = series.latest()
        if not point or series.id in HIDDEN_SERIES:
            continue
        tile = is_tile(series.id)
        item = _indicator_row(series.id, series=series, point=point, tile=tile)
        if tile:
            try:
                item["history"] = tile_history(series)
            except Exception:                                   # noqa: BLE001
                item["history"] = []
        out.append(item)
        seen.add(series.id)
    for series_id in INDICATOR_TILES:
        if series_id not in seen:
            item = _indicator_row(series_id, tile=True)
            item.update(history=[], missing=INDICATOR_TILE_MISSING.get(
                series_id, "ряда ещё нет: сборщик не отработал ни разу"))
            out.append(item)
    for prefix, suffix in INDICATOR_TILE_FAMILIES:
        if not any(s.startswith(prefix) and s.endswith(suffix) for s in seen):
            item = _indicator_row(f"{prefix}*{suffix}", tile=True)
            item.update(title="Доходности облигаций группы в обращении", unit="share",
                        history=[], missing="ряда ещё нет: сборщик облигаций не отработал ни разу")
            out.append(item)
    # Плитки — первыми, пустые плитки (ряда ещё нет) — после плиток с рядом.
    return sorted(out, key=lambda x: (not x["tile"], "missing" in x, x["channel"] or 0, x["id"]))


def _nowcast_block(A: dict, release=None) -> dict:
    """Нау-каст ближайшего отчёта — КВАРТАЛЬНЫЙ (D2, D15). Период — ИЗ ЖУРНАЛА.

    Отчётность «Ленты» квартальная: ближайший отчёт — квартал (3 кв. 2026,
    окно 26.10–03.11.2026), а в правило A-P2u и в таблицу «что даст отчёт» идёт
    полугодие (2026H2, годовой отчёт). Поэтому блок несёт и квартал (прогноз,
    ожидание модели с ошибкой, выручку), и его полугодие (маржа полугодия,
    которой соответствует прогноз), и «какая маржа 2П следует из факта 3 кв.»
    (`implied_half`), и эталон гайденса (`guidance`).

    Определение периода ОДНО и в одном месте — `journal.forecast_period` с тем
    же показателем, которым её зовёт `collect.cmd_nowcast` (аудит 850oa, C3):
    журнал прогнозирует самый ранний период, ФАКТ которого не внесён, и
    закрывает период факт, а не дата. Своего «сегодня» выпуск не заводит.
    """
    try:
        from indicators.collect import MARGIN_TARGET
        from indicators.journal import Journal, forecast_period
        from indicators.nowcast import margin_nowcast
        from indicators.retro import retro_block
        from indicators.store import Store
    except ImportError:  # pragma: no cover
        return {}
    store = Store()
    journal = Journal()
    nowcast = margin_nowcast(store, A, period=forecast_period(journal, MARGIN_TARGET))
    admission = _round(journal.admission(MARGIN_TARGET, version=nowcast.version).as_dict(), 4)
    admission.update(_admission_calendar(nowcast.period, admission.get("events_needed")))
    return dict(
        period_unit="quarter",
        target=MARGIN_TARGET,
        target_title=NOWCAST_TARGET_TITLES.get(MARGIN_TARGET, MARGIN_TARGET),
        # Подписи всех величин журнала (выручка, проценты) — витрина печатает
        # журнал и эталоны по ним и ключей эмитента не держит.
        target_titles=dict(NOWCAST_TARGET_TITLES),
        target_quarter=nowcast.period,
        margin=dict(period=nowcast.period, value=round(nowcast.value, 5),
                    std_error=round(nowcast.std_error, 5),
                    # Ожидание модели на квартал — база уравнения и обязательный
                    # эталон журнала; его ошибка — σ квартала книги
                    # (`indicators.quarterly.quarter_se`).
                    expectation=_round(nowcast.expectation, 5),
                    expectation_se=round(nowcast.std_error, 5),
                    deviation=_round(nowcast.deviation, 5),
                    # Полугодие квартала: в правило A-P2u подаётся оно (D2).
                    half=nowcast.half, half_value=_round(nowcast.half_value, 5),
                    half_std_error=_round(nowcast.half_std_error, 5),
                    revenue=_round(nowcast.revenue, 2),
                    equation=nowcast.equation, version=nowcast.version,
                    source=nowcast.source, connected_to_price=bool(nowcast.connected_to_price),
                    components=_round(nowcast.components, 6),
                    inputs=_round(nowcast.inputs, 6)),
        implied_half=_soft(_implied_half_block, A, nowcast, journal, release),
        guidance=_soft(_nowcast_guidance, A, nowcast, release),
        # Правило допуска (`journal.admission`): статус, причина и сколько
        # отчётов осталось — по отчётам ТЕКУЩЕЙ версии уравнения; плюс первый
        # квартал, который пойдёт в счёт (главный эталон не сломан сделкой), и
        # самый ранний квартал решения. Только маржа: правило A-P2u обновляет
        # вероятности режимов маржи. Допуск — право владельца решить, а не
        # подключение.
        admission={MARGIN_TARGET: admission},
        # Ретро-проверка эталонов на истории (`indicators/retro.py`): планка,
        # которую уравнению предстоит взять. У самого уравнения истории нет.
        retro=retro_block(A, store=store),
        interest=_interest_nowcast_block(store, A, nowcast.period),
        # В выпуск уходят ПОСЛЕДНИЕ записи по каждой величине, а не весь
        # журнал: он растёт вечно, выпуск — нет.
        journal=[dict(target=f.target, period=f.period, value=round(f.value, 5),
                      made_at=f.made_at, version=f.version, equation=f.equation,
                      std_error=None if f.std_error is None else round(f.std_error, 5),
                      note=f.note, inputs_sha=f.inputs_sha[:16])
                 for f in journal.recent()],
        scoreboard={target: journal.scoreboard(target) for target in
                    (issuer.series("ebitda_margin_pre16"), issuer.series("revenue_pre16"),
                     issuer.series("net_interest"))},
        naive=_naive_block(journal),
    )


# Подписи величин журнала — в выпуске, чтобы витрина не держала ключей эмитента.
NOWCAST_TARGET_TITLES = {
    issuer.series("ebitda_margin_pre16"): "маржа EBITDA до МСФО 16",
    issuer.series("revenue_pre16"): "выручка до МСФО 16",
    issuer.series("net_interest"): "чистые процентные расходы",
}
# Сколько кварталов вперёд искать первый засчитываемый: предел цикла, а не
# допущение (8 засчитываемых кварталов при разрывах ≈ через 3 года).
ADMISSION_SEARCH_QUARTERS = 40


def _admission_calendar(period: str, events_needed) -> dict:
    """Первый квартал, который пойдёт в счёт допуска, и самый ранний квартал решения.

    Засчитывается отчёт, чей ГЛАВНЫЙ эталон не сломан разрывом периметра
    (`indicators.perimeter.broken_by` с методом `journal.MAIN_BENCHMARK` — то же
    правило, что у `Journal.admission`). Решение о допуске — не раньше квартала,
    в котором наберётся `events_needed` засчитанных отчётов (D15: 8 кварталов).
    """
    from indicators import perimeter, periods
    from indicators.collect import MARGIN_TARGET
    from indicators.journal import MAIN_BENCHMARK

    main = MAIN_BENCHMARK[MARGIN_TARGET]
    need = int(events_needed or 0)
    countable, broken, q = [], [], period
    for _ in range(ADMISSION_SEARCH_QUARTERS):
        hit = perimeter.broken_by(q, main)
        if hit:
            broken.append(dict(quarter=q, deals=[b.id for b in hit]))
        else:
            countable.append(q)
            if len(countable) >= max(need, 1):
                break
        q = periods.shift(q, 1)
    return dict(first_countable=countable[0] if countable else None,
                earliest_decision=(countable[need - 1] if need and len(countable) >= need
                                   else None),
                broken_ahead=broken, main_benchmark=main)


def _implied_half_block(A: dict, nowcast, journal, release=None) -> dict:
    """«Какая маржа 2П следует из факта 3 кв.» (D2) — правило книги и таблица.

    Полугодие прогнозируемого квартала и его ПЕРВЫЙ квартал (3 кв. для 2П):
    факт первого квартала из журнала (пока не внесён — None) и следствие для
    полугодия двумя прочтениями (`indicators.quarterly.implied_half_margin`:
    полная персистентность отклонения и без неё, со вторым кварталом по
    ожиданию модели). Таблица — по строкам «что даст отчёт» (`next_report_value`,
    полугодие ядра): факт первого квартала, при котором маржа полугодия при
    полной персистентности равна марже строки, — витрина соединяет строку
    факта квартала с ценой строки без своих формул.
    """
    from indicators import periods, quarterly
    from indicators.collect import MARGIN_TARGET
    from indicators.nowcast import implied_half

    half = periods.half_of_quarter(nowcast.period)
    first, second = periods.quarters_of(half)
    expected_first = (nowcast.expectation if first == nowcast.period
                      else quarterly.expected_quarter(A, first).margin)
    expected_second = (nowcast.expectation if second == nowcast.period
                       else quarterly.expected_quarter(A, second).margin)
    offsets = A["margin"]["quarter_offset_pp"]
    offset_first = float(offsets[periods.quarter_key(first)])
    actual = journal.actual(MARGIN_TARGET, first)
    fact = None
    if actual is not None:
        got = implied_half(first, float(actual.value), A, other_quarter_expectation=expected_second)
        fact = dict(value=round(float(actual.value), 5),
                    implied_persistent=round(got["implied_persistent"], 5),
                    implied_independent=_round(got["implied_independent"], 5),
                    surprise=round(float(actual.value) - expected_first, 5))
    rows = []
    for row in (release.next_report if release is not None else []):
        q_fact = row["margin"] + offset_first
        got = implied_half(first, q_fact, A, other_quarter_expectation=expected_second)
        rows.append(dict(quarter_fact=round(q_fact, 5),
                         implied_persistent=round(got["implied_persistent"], 5),
                         implied_independent=_round(got["implied_independent"], 5),
                         half_margin_row=round(row["margin"], 4)))
    rule = quarterly.implied_half_margin(expected_first, quarter=first,
                                         quarter_offset_pp=offsets,
                                         quarter_share=A["revenue"]["quarter_share"],
                                         other_quarter_expectation=expected_second)["rule"]
    return dict(half=half, quarter=first, second_quarter=second,
                quarter_expectation=round(expected_first, 5),
                second_quarter_expectation=round(expected_second, 5),
                quarter_offset=round(offset_first, 6),
                fact=fact, table=rows, rule=rule,
                second_quarter_reports=quarter_reports(
                    second, date.fromisoformat(A["meta"]["valuation_date"])))


def quarter_reports(quarter: str, today: date) -> list[dict]:
    """Отчёты, которые раскрывают квартал, — события журнала (D2).

    У 4 кв. их ДВА: выручка приходит операционными результатами (≈февраль),
    маржа — финансовыми за 12 месяцев (≈март); у прочих кварталов — один релиз
    на обе величины. Имя события — `periods.report_id` (то же, чем журнал
    сопоставляет период с отчётом), дата — из календаря книги, а если события
    там нет — по правилу лагов (`precision: rule`). Витрина печатает события
    экрана «Ближайший отчёт» отсюда и своих дат не держит.
    """
    from indicators import calendar, periods

    try:
        events = {e.id: e for e in calendar.load()}
    except (OSError, ValueError, KeyError):
        events = {}
    out: list[dict] = []
    for gives in (periods.GIVES_REVENUE, periods.GIVES_MARGIN):
        rid = periods.report_id(quarter, gives)
        if not rid:
            continue
        known = next((row for row in out if row["id"] == rid), None)
        if known is not None:
            known["gives"].append(gives)
            continue
        event = events.get(rid)
        if event is not None:
            when, latest, precision, title = (event.when, event.latest, event.precision,
                                              event.title)
            earliest = event.window_from or event.when
        else:
            window = periods.fallback_report_window(quarter, gives)
            if window is None:
                continue
            (when, latest), precision, title = window, "rule", ""
            earliest = when
        out.append(dict(id=rid, gives=[gives], when=when.isoformat(),
                        earliest=earliest.isoformat(), latest=latest.isoformat(),
                        precision=precision, title=title, days=(when - today).days))
    return out


def _nowcast_guidance(A: dict, nowcast, release=None) -> dict | None:
    """Эталон «гайденс» для прогнозируемого квартала и его полугодия (D2).

    Годовая цель компании (`facts.guidance`, `data/facts/anchor.json`) → маржа
    2П, которой она требует при выручке 2П модели (`quarterly.guidance_half_margin`),
    → эталон квартала 2П = маржа 2П + поправка квартала книги
    (`quarterly.guidance_benchmark`). Гайденса на год квартала нет — None.
    """
    from indicators import periods, quarterly

    state = guidance_state(A, release) if release is not None else None
    if not state:
        return None
    year = int(state["year"])
    h2_revenue = state["h2"]["revenue_model"]
    quarter = quarterly.guidance_benchmark(
        nowcast.period, guidance_year=year, fy_margin=state["fy_margin_min"],
        h1_revenue=state["h1"]["revenue"], h1_ebitda=state["h1"]["ebitda"],
        h2_revenue=h2_revenue, quarter_offset_pp=A["margin"]["quarter_offset_pp"])
    return dict(year=state["year"], fy_margin_min=state["fy_margin_min"],
                required_half=state["h2"]["half"],
                required_half_margin=round(state["h2"]["margin_required"], 5),
                quarter=nowcast.period,
                quarter_benchmark=None if quarter is None else round(quarter, 5),
                half_of_quarter=periods.half_of_quarter(nowcast.period),
                source=state["source"])


def _naive_block(journal) -> dict:
    """Эталоны рядом с прогнозом — до выхода факта, а не после.

    Владельцу нужно видеть, с чем уравнение соревнуется, ещё до отчёта: иначе
    «модель ошиблась на 0,2 п.п.» звучит как успех, пока не выяснится, что
    среднее двух полугодий ошиблось на 0,1.
    """
    from indicators.journal import MAIN_BENCHMARK

    out = {}
    for forecast in journal.recent(per_target=1):
        values = journal.naive(forecast.target, forecast.period)
        if values:
            # ГЛАВНЫЙ эталон назван явно. Без этого экран брал первый ключ
            # словаря и показывал 4,56 % вместо 4,82 % — сравнивал не с тем,
            # с чем идёт зачёт.
            main = MAIN_BENCHMARK.get(forecast.target)
            if main not in values:
                main = next(iter(values)) if len(values) == 1 else None
            out[forecast.target + " " + forecast.period] = dict(
                forecast=round(forecast.value, 5), main=main,
                naive={k: round(v, 5) for k, v in values.items()})
    return out


def _calendar_block(A: dict) -> dict:
    """Календарь ближайших событий и обратный отсчёт до отчёта.

    Экран «Ближайший отчёт» без даты отчёта — это экран без ближайшего
    отчёта. Обратный отсчёт берётся от ПЛАНОВОЙ даты: точную компания
    объявляет за две недели, и выдуманная точная дата врала бы с точностью
    до дня.
    """
    try:
        from indicators import calendar as cal
    except ImportError:  # pragma: no cover
        return {}
    today = date.fromisoformat(A["meta"]["valuation_date"])
    fact = cal.next_fact(today)
    return dict(
        today=today.isoformat(),
        # Окно даты (`earliest`–`latest`): у отчёта с точностью «окно» дату
        # ставит компания, и экран печатает окно, а не выдуманный день.
        next_fact=(None if fact is None else dict(
            id=fact.id, when=fact.when.isoformat(), precision=fact.precision,
            earliest=(fact.window_from or fact.when).isoformat(),
            latest=fact.latest.isoformat(),
            title=fact.title, days=fact.days_until(today), note=fact.note)),
        events=[dict(id=e.id, when=e.when.isoformat(), precision=e.precision,
                     earliest=(e.window_from or e.when).isoformat(), latest=e.latest.isoformat(),
                     title=e.title, days=e.days_until(today),
                     gives=list(e.gives), prior_for=e.prior_for, note=e.note)
                for e in cal.upcoming(today, limit=6)],
    )


# Единица значения суждения книги — правило ВИДА ЧИСЛА на экране, а не
# допущение: доля печатается процентами, бета и α — как есть, миллиарды — с
# единицей. До 25.09.2026 её угадывала витрина регулярными выражениями по
# ключу (`UNIT_BY_KEY` в web/app.js); теперь единицу печатает выпуск, и у
# правила один экземпляр. Первое совпадение выигрывает; незнакомый ключ —
# «plain», число без единицы, — честно.
JUDGEMENT_UNIT_RULES = (
    (r"^valuation\.beta_u$|^tax\.alpha$|^tax\.alpha_terminal_shield$|"
     r"^revenue\.segments\.[a-z0-9_]+\.closed_productivity$",
     "plain"),
    (r"^financing\.leverage_target$", "times"),
    (r"^capex\.segments\.[a-z0-9_]+\.growth_capex_per_m2$", "bn_per_m2"),
    (r"^tax\.nol_start$|^bridge\.", "bn"),
    (r"^nwc\.seasonal_june_excess$", "bn"),
    (r"^valuation\.market_curve_years$", "years"),
    (r"erp|_pct|target\.LT|governance_discount|spread|"
     r"terminal_real_growth|ronic|addback|\.cpi|inflation|world_prob", "pct"),
)
JUDGEMENT_UNITS = ("plain", "times", "bn_per_m2", "bn", "years", "pct")


def judgement_unit(key: str) -> str:
    """Единица значения суждения по его ключу (первый путь строки книги)."""
    for pattern, unit in JUDGEMENT_UNIT_RULES:
        if re.search(pattern, key or ""):
            return unit
    return "plain"


def _assumption_notes() -> dict[str, str]:
    """Комментарий рядом с каждым допущением книги: источник и обоснование.

    Источник должен быть на экране: ссылка в приватный репозиторий с телефона
    не открывается. Комментарии живут в самой книге, строкой рядом со
    значением, и читаются построчно с отслеживанием отступа: разбор YAML
    комментарии выбрасывает, а второй файл с ними был бы второй правдой.
    """
    notes: dict[str, str] = {}
    stack: list[tuple[int, str]] = []
    # Заголовок раздела книги — строки комментария НАД ключом верхнего уровня
    # («МАКРО-МИРЫ (A-M1…A-M6)» над `worlds`): у миров нет комментариев в
    # строках, и источник суждения «инфляция мира M» (A-M6) называет заголовок.
    header: list[str] = []
    for raw in BOOK_YAML.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        if raw.lstrip().startswith("#"):
            text = raw.lstrip().lstrip("#").strip().strip("-").strip()
            if text:
                header.append(text)
            continue
        indent = len(raw) - len(raw.lstrip())
        head, _, comment = raw.partition("#")
        if raw.lstrip().startswith("- {"):
            # Строка списка (`bridge.items`, `financing.scheduled_payments`, оси):
            # у строки с `id` комментарий — источник пути `<список>[<id>]`.
            found = re.search(r"\bid:\s*([^,}\s]+)", head)
            if found and comment.strip() and stack:
                notes[".".join(part for _, part in stack) + f"[{found.group(1)}]"] = comment.strip()
            header = []
            continue
        key = head.split(":")[0].strip().strip('"')
        if not key or ":" not in head:
            header = []
            continue
        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, key))
        if comment.strip():
            notes[".".join(part for _, part in stack)] = comment.strip()
        elif indent == 0 and header:
            notes[key] = ". ".join(header)
        header = []
    return notes


def judgement_rows(judgements) -> list[dict]:
    """Строки таблицы суждений выпуска из `judgement_table(limit=None)`: все
    строки `sensitivities` книги (обрезка спрятала бы дешёвые суждения вместе
    с тем, что они дешёвые), цены до рубля."""
    return [dict(key=j.key, label=j.label, paths=list(j.paths), kind=j.kind,
                 unit=judgement_unit(j.key),
                 low_label=j.low_label, high_label=j.high_label,
                 price_low=round(j.price_low, 0), price_high=round(j.price_high, 0),
                 scenario_low=round(j.scenario_low, 0), scenario_high=round(j.scenario_high, 0),
                 spread=round(j.spread, 0))
            for j in judgements]


def _assumptions_block(A: dict, judgements: list[dict]) -> list[dict]:
    """Таблица допущений для экрана: значение, диапазон, цена ошибки, источник.

    Порядок — по цене ошибки: допущение, которое двигает оценку на 1 500 ₽,
    важнее того, что двигает на 50, и стоять они должны не по алфавиту.
    """
    notes = _assumption_notes()

    def value_of(key: str):
        try:
            node = get_path(A, key)
        except (KeyError, TypeError):
            return None
        if isinstance(node, (int, float, str)):
            return node
        if isinstance(node, dict):
            # Траектория — это тоже значение, и прятать её за «None» значит
            # оставить самое дорогое допущение книги без числа на экране.
            return ", ".join(f"{k}: {v}" for k, v in list(node.items())[:4]
                             if isinstance(v, (int, float)))
        return None

    def note_of(key: str) -> str:
        parts = key.split(".")
        for cut in range(len(parts), 0, -1):
            found = notes.get(".".join(parts[:cut]))
            if found:
                return found
        return ""

    def value_of_row(item: dict):
        """Значение строки. У оси по нескольким путям (книга 1.4: A-C0 — цели
        LT всех режимов, σ заголовка и клетки, инфляция мира M) — значение
        КАЖДОГО пути под его отличающимся именем, а не одного первого: иначе
        «общий уровень маржи» печатался бы целью одного «стресса»."""
        paths = item.get("paths") or [item["key"]]
        if len(paths) == 1:
            return value_of(paths[0])
        split = [p.split(".") for p in paths]
        differ = next((i for i in range(min(map(len, split)))
                       if len({parts[i] for parts in split}) > 1), 0)

        def one(parts, path):
            # Число печатается; траектория по нескольким путям — только
            # именем пути: четыре траектории подряд растягивали ячейку на
            # десяток строк, а значение каждой видно в строке её суждения.
            value = value_of(path)
            name = ".".join(parts[differ:])
            return f"{name}: {value}" if isinstance(value, (int, float)) else name

        return "; ".join(one(parts, p) for parts, p in zip(split, paths))

    out = []
    for item in judgements:
        key = item["key"]
        out.append(dict(
            key=key, label=item["label"], paths=item.get("paths") or [key],
            kind=item.get("kind", "value"),
            value=value_of_row(item), unit=item.get("unit") or judgement_unit(key),
            low=item["low_label"], high=item["high_label"],
            spread=item["spread"],
            price_low=item["price_low"], price_high=item["price_high"],
            source=note_of(key)))
    return sorted(out, key=lambda x: -x["spread"])


def claims_lines(A: dict, result: CellResult) -> list[dict]:
    """Требования к EV построчно.

    Аудит: на экране стояла одна строка «чистый долг, пут, НДУ, операционная
    касса −572,0». Сумма из четырёх слагаемых, напечатанная одним числом, —
    это не мост, а его отсутствие: проверить её нечем.

    Долг на дату оценки выводится вычитанием: остальные слагаемые известны из
    фактов, и если мост сойдётся, значит и он посчитан верно. Тест сверяет
    сумму строк с `claims` клетки.

    Вычитание честно, только если каждое «известное» слагаемое то же, что в
    требованиях ядра: всё, что стоит иначе, молча уходит в долг. Строки моста
    берутся той же функцией, что у ядра (`model.core.bridge_lines`): с
    наращением от их даты и снятием после выплаты. Книжная сумма верна лишь на
    дату строки (у 850oa строка долга с книжным путом к 20.12.2026 была
    завышена на 1,05 млрд ₽, а после выплаты пута занижена на 27,6).
    """
    closed, _ = time_position(A)
    P = periods(A["meta"]["first_period"], A["meta"]["last_period"])
    bridge = bridge_lines(A, closed, P)
    known = {item.id: value for item, value in bridge}
    known.update(debt_cost_addon=result.debt_cost_addon, cash_carry=result.cash_carry)
    operating_cash = A["financing"]["operating_cash_pct"] * _revenue_ltm0(A)
    rest = result.claims - sum(known.values())
    titles = {
        "net_debt": "Чистый долг на дату оценки",
        "operating_cash": "Операционный минимум кассы (не подлежит распределению)",
        **{item.id: item.name for item, _ in bridge},
        "debt_cost_addon": "Дороговизна долга сверх справедливого спреда",
        "cash_carry": "Недополученная доходность кассы, занятой впрок",
    }
    lines = [dict(key="net_debt", title=titles["net_debt"],
                  value=round(rest - operating_cash, 2)),
             dict(key="operating_cash", title=titles["operating_cash"],
                  value=round(operating_cash, 2))]
    lines += [dict(key=k, title=titles[k], value=round(v, 2))
              for k, v in known.items() if abs(v) > 1e-9]
    return lines


def _revenue_ltm0(A: dict) -> float:
    """Выручка за последние 12 отчётных месяцев — база операционной кассы.

    Считается ровно как в ядре: два последних ОТЧЁТНЫХ полугодия. Взять
    вместо них прогнозные значит получить разные числа в мосте и в расчёте.
    """
    revenue = A["facts"]["revenue"]
    halves = sorted(revenue)
    return revenue[halves[-1]] + revenue[halves[-2]]


# Главный эталон канала процентов — тот же, что у журнала (`journal.MAIN_BENCHMARK`):
# витрина берёт его число из `naive` по этому имени, и два имени одного эталона
# (у 850oa было «previous_half_…», у квартального журнала «Ленты» —
# «previous_period_…») давали пустое место на экране.
def _main_benchmark_interest() -> str:
    from indicators.journal import INTEREST_TARGET, MAIN_BENCHMARK

    return MAIN_BENCHMARK[INTEREST_TARGET]


def _interest_nowcast_block(store, A: dict, period: str) -> dict:
    """Канал процентов на экран: число, из чего оно сложилось и эталон.

    Прогноз без эталона нечем измерить, поэтому «прошлое полугодие ×
    отношение средних ставок» стоит рядом, а не в отдельном отчёте.
    """
    try:
        from indicators.collect import _interest_benchmark
        from indicators.nowcast import interest_nowcast
    except ImportError:  # pragma: no cover
        return {}
    try:
        now = interest_nowcast(store, A, period=period)
    except (OSError, KeyError, ValueError) as error:
        # Канал процентов Ленты ждёт реестра долга и корзин ставок (заметка в
        # indicators/interest.py): без них выпуск идёт без канала, а не падает —
        # как такт нау-каста (`indicators.collect.cmd_nowcast`). Витрина пишет
        # «Канала процентов в выпуске нет». Текст ошибки в выпуск не идёт (в нём
        # бывают пути сервера) — только класс.
        return dict(period=period, unavailable=type(error).__name__)
    benchmark = _interest_benchmark(store, period)
    return dict(
        period=now.period,
        net_interest=round(now.net_interest, 2),
        debt_interest=round(now.debt_interest, 2),
        cash_income=round(now.cash_income, 2),
        average_debt=round(now.average_debt, 1),
        average_cash=round(now.average_cash, 1),
        average_key_rate=round(now.average_key_rate, 5),
        effective_rate=round(now.effective_rate, 5),
        implied_spread=round(now.implied_spread, 5),
        opening_gross_debt=round(now.opening_gross_debt, 1),
        redemptions_count=now.redemptions_count,
        # Погашения разделены ДАТОЙ ОЦЕНКИ: «три уже прошли, ещё два — до конца
        # полугодия». Подпись собрана здесь, а не в вёрстке, потому что оба
        # числа приходят из реестра долга (ответ аудитора 8).
        redemptions_done=now.redemptions_done,
        redemptions_ahead=now.redemptions_ahead,
        redemptions_done_amount=round(now.redemptions_done_amount, 1),
        redemptions_ahead_amount=round(now.redemptions_ahead_amount, 1),
        redemptions_note=now.redemptions_note,
        main_benchmark=_main_benchmark_interest(),
        floating_share=round(now.floating_share, 4),
        by_kind=_round(now.by_kind, 2),
        naive={k: round(v, 2) for k, v in benchmark.items()},
        # Заявляемая точность канала и чувствительность к внешней траектории
        # ключевой ставки. Обе величины — про ДОВЕРИЕ к числу слева, и стоять
        # они должны рядом с ним, а не в документации.
        accuracy=_interest_accuracy(),
        survey_delta=(round(now.survey_delta, 2) if now.survey_delta is not None else None),
    )


def _interest_accuracy() -> float:
    from indicators.collect import INTEREST_ACCURACY

    return INTEREST_ACCURACY


def _debt_block(A: dict) -> dict:
    """Долг на дату оценки: факты якоря и отчётный слой реестра траншей.

    Факты якоря (`facts.anchor`: чистый долг, касса; неиспользованные линии)
    есть всегда. Реестр долга (`data/facts/debt_register.json`) — отчётный слой:
    стена рефинансирования, чувствительность к ключевой, транши. Нет реестра
    или он не читается — поля реестра `null` с причиной в `register`, а выпуск
    выходит (как канал процентов: `nowcast.interest.unavailable`). Оценку реестр
    не меняет: ядро читает долг из книги и моста.
    """
    from model.financing import RegisterError

    F = A["facts"]
    out = dict(
        net_debt_reported=F["anchor"]["net_debt"],
        cash_reported=F["anchor"]["cash"],
        undrawn_facilities=F["undrawn_credit_lines"],
        floating_share_book=round(1 - path_value(A["financing"]["fixed_share"],
                                                 A["meta"]["first_period"]), 4),
        cash_yield_k=A["financing"]["cash_yield_k"],
        recent_events=_recent_events(),
    )
    try:
        out.update(_register_fields(A))
        out["register"] = dict(available=True, as_of=_debt_facts().get("as_of"))
    except (OSError, KeyError, ValueError, RegisterError) as error:
        # Текст ошибки в выпуск не идёт (в нём бывают пути) — только класс.
        out.update({key: None for key in REGISTER_FIELDS})
        out["register"] = dict(available=False, reason=type(error).__name__)
    return out


# Поля долга, которые считает реестр траншей (без реестра — null).
REGISTER_FIELDS = ("net_debt", "term_debt", "cash", "cash_at", "rate_sensitivity_per_pp",
                   "rate_sensitivity_net_per_pp", "rate_sensitivity_net_register_per_pp",
                   "floating_share_register", "wall", "tranches", "events")


def _register_fields(A: dict) -> dict:
    from model.financing import rate_sensitivity, refinancing_wall, register_state

    # Дата оценки, а не литерал: от неё зависит, какие погашения уже прошли.
    # Срок транша без события в фактах — рефинансирование под спред нового
    # долга книги, а не исчезнувший долг (`register_state`): реестровый чистый
    # долг, стена и чувствительность к ставке не сползают с датой оценки.
    state = register_state(date.fromisoformat(A["meta"]["valuation_date"]),
                           A["financing"]["spread_float"]["base"])
    wall = refinancing_wall(state.tranches)
    floating_book = 1 - path_value(A["financing"]["fixed_share"], A["meta"]["first_period"])
    return dict(
        # РЕЕСТРОВЫЙ чистый долг: номиналы живых траншей минус касса отчётной
        # даты за вычетом погашений после неё. В оценку идёт другой —
        # модельный, из моста (`claims_lines`, строка `net_debt`). Экран «Деньги
        # и долг» печатает оба под разными именами и объясняет разницу.
        net_debt=round(state.net_debt, 1),
        term_debt=round(state.term_debt, 1),
        cash=round(state.cash, 1),
        cash_at=state.cash,
        # Валовая чувствительность — 1 п.п. ключевой на номинал флоатеров
        # (уровень ключевой в неё не входит).
        rate_sensitivity_per_pp=round(rate_sensitivity(state.tranches, 0.0), 2),
        rate_sensitivity_net_per_pp=round(rate_sensitivity_net(
            state.tranches, state.cash, deposit_factor=A["financing"]["cash_yield_k"],
            floating_share=floating_book), 2),
        rate_sensitivity_net_register_per_pp=round(rate_sensitivity_net(
            state.tranches, state.cash, deposit_factor=A["financing"]["cash_yield_k"]), 2),
        # Доля плавающего долга ПО РЕЕСТРУ — ровно та, на которой считает канал
        # процентов: реестр на начало первого прогнозного полугодия.
        floating_share_register=round(_register_floating_share(A), 4),
        wall={str(y): round(v, 1) for y, v in wall.items()},
        tranches=[dict(name=t.name, principal=round(t.principal, 3),
                       maturity=t.maturity.isoformat(), kind=t.kind,
                       fixed_rate=t.fixed_rate, spread=t.spread_to_key)
                  for t in sorted(state.tranches, key=lambda t: t.maturity)],
        events=[dict(date=e["date"], isin=e.get("isin"), principal=e["principal"])
                for e in _debt_facts()["events_after_balance_date"]],
    )


# Классы событий раскрытия, о которых панель говорит вслух (плашка «события за
# 7 дней» витрины, `debt.recent_events`) — те же классы, что строка «СОБЫТИЕ»
# такта сбора (`indicators.sources.DISCLOSURE_ALARM_KINDS`: сделки, собственные
# акции, дивиденды, листинг, оферты, размещения). Окно — 30 дней.
ALARM_EVENT_DAYS = 30


def _alarm_event_kinds() -> tuple[str, ...]:
    from indicators.sources import DISCLOSURE_ALARM_KINDS

    return tuple(DISCLOSURE_ALARM_KINDS)


def _recent_events(store=None) -> list[dict]:
    """Недавние корпоративные события, о которых стоит знать читателю.

    Телеграм-тревога по ним не делается по решению владельца (общий мостик
    панелей шлёт только падение юнита). Вместо неё — флаг в выпуске, плашка на
    витрине и строка в журнале прогона: событие видно каждому, кто открыл панель.
    """
    try:
        from indicators.store import Store
    except ImportError:                                   # pragma: no cover
        return []
    store, out = store or Store(), []
    edge = (datetime.now(timezone.utc).date() - timedelta(days=ALARM_EVENT_DAYS)).isoformat()
    for kind in _alarm_event_kinds():
        series = store.load(issuer.series(f"disclosure.{kind}"))
        if not series:
            continue
        for point in series.points:
            if point.period >= edge:
                out.append(dict(date=point.period, kind=kind, title=point.note[:160]))
    return sorted(out, key=lambda item: item["date"], reverse=True)[:8]


def _register_floating_share(A: dict) -> float:
    """Доля плавающего долга в реестре на начало первого прогнозного полугодия.

    Считается тем же набором траншей, что и канал процентов
    (`indicators.nowcast.interest_nowcast`): иначе экран и прогноз говорили бы
    о разном долге, и сверить их было бы нечем.
    """
    from model.financing import load_debt_register

    period = A["meta"]["first_period"]
    year, half = int(period[:4]), int(period[5])
    start = date(year, 7, 1) if half == 2 else date(year, 1, 1)
    tranches = [t for t in load_debt_register(include_redeemed=True) if t.maturity >= start]
    total = sum(t.principal for t in tranches) or 1.0
    return sum(t.principal for t in tranches if t.fixed_rate is None) / total


def _debt_facts() -> dict:
    """Реестр долга (`data/facts/debt_register.json`, `model.financing`)."""
    from model.financing import load_register_data

    return load_register_data()


def _history_block() -> dict:
    """История для витрины из `data/facts/accounting_base.json` (лист фактов).

    Маржа EBITDA и выручка до МСФО 16 по полугодиям с 2018H1
    (`halves.<полугодие>.ias17.pl.{revenue, ebitda}`, млрд ₽), торговая площадь
    на конец первого полугодия и года (`operating.area_sqm.<период>.total`,
    м² → тыс. м²; «FY2025» → «2025FY», как у рядов 850oa). Один винтаж
    датабука: прошлое переписывается каждым релизом (как у квартальной
    истории слоя индикаторов, `indicators/retro.py`).
    """
    from model.paths import FACTS_DIR

    base = json.loads((FACTS_DIR / "accounting_base.json").read_text(encoding="utf-8"))
    pl = {p: row["ias17"]["pl"] for p, row in base["halves"].items()
          if p.endswith(("H1", "H2")) and p >= "2018H1"}
    halves = sorted(p for p in pl if pl[p].get("revenue") and pl[p].get("ebitda"))
    area = {}
    for period, row in (base.get("operating") or {}).get("area_sqm", {}).items():
        key = f"{period[2:]}FY" if period.startswith("FY") else period
        if key.endswith(("H1", "FY")) and "total" in row:
            area[key] = round(row["total"]["v"] / 1000.0, 1)
    doc = base.get("source_doc") or {}
    return dict(
        margin={p: round(pl[p]["ebitda"]["v"] / pl[p]["revenue"]["v"], 5) for p in halves},
        revenue={p: round(pl[p]["revenue"]["v"], 1) for p in halves},
        area=dict(sorted(area.items())),
        vintage=doc.get("description", "").split(" (")[0] or doc.get("id", ""),
        pit_warning="один винтаж датабука: прошлое переписано при каждом релизе",
    )


# ------------------------------------------ книга 1.4: медленные блоки выпуска
#
# Три блока считают сетку сотни и тысячи раз: полоса неопределённости A-V9
# (2 000 сеток), обратный DCF по осям книги (9 осей × ≈52 сетки) и таблица
# суждений (28 строк × 2 сетки). Считаются они в ПОЛНОЙ сборке (`with_slow`)
# и один раз на книгу в процессе: набор тестов собирает полный выпуск на одной
# и той же книге в трёх модулях, и без памяти каждый платил бы полторы минуты.
# Ключ — содержание книги (sha256 канонического JSON): любая подмена, живая
# цена или дата оценки дают другую книгу и другой расчёт.

_SLOW_BLOCKS: dict[str, dict] = {}


def book_digest(A: dict) -> str:
    """sha256 содержания книги — ключ памяти медленных блоков.

    Ключи словарей приводятся к строке с меткой типа: у кривых мира ключи —
    числа сроков (`1`, `2.5`), у траекторий — строки периодов, и `sort_keys`
    на смешанных ключах падает; `1` и `"1"` при этом остаются разными.
    """
    def canonical(node):
        if isinstance(node, dict):
            return {f"{type(k).__name__}:{k}": canonical(v) for k, v in node.items()}
        if isinstance(node, (list, tuple)):
            return [canonical(v) for v in node]
        return node

    body = json.dumps(canonical(A), sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def slow_blocks(A: dict) -> dict:
    """Полоса, обратный DCF, все суждения книги и нейтральная маржа ближайшего
    отчёта — один раз на содержание книги.

    Возвращает {uncertainty, reverse_dcf, point_reverse_dcf, judgements, neutral,
    next_report, seconds, median_grids, median_evaluations}. Время каждого блока —
    для журнала сборки (потолок суточного такта — 30 минут). Возвращённое не
    правится: выпуск строит из него свои словари.

    Обратный DCF, таблица «что даст отчёт» и нейтральная маржа решаются для
    печатаемой медианы (`model.uncertainty`); точечный обратный DCF — старт
    поиска (`point_reverse_dcf`). `next_report` — строки медианы (None, если
    её расчёт отказал), `median_evaluations` и `median_grids` — сколько
    пересчётов медианы и сеток они стоили.
    """
    key = book_digest(A)
    cached = _SLOW_BLOCKS.get(key)
    if cached is None:
        cached = _SLOW_BLOCKS[key] = slow_cache(key, lambda: _slow_blocks(A))
    return cached


SLOW_CACHE_ENV = "LENTA_SLOW_CACHE"


def slow_cache(key: str, compute) -> dict:
    """Медленные блоки с диска — только с `LENTA_SLOW_CACHE=<каталог>` (локальные
    проверки; на сервере и в CI не ставится). Без переменной — `compute()`.

    Файл — pickle словаря под ключом sha256 от ключа книги, кода модели, фактов,
    файла книги, версии Python и платформы (`slow_cache_key`): другой код или
    другой интерпретатор — промах, а не чужие числа. Запись атомарна.
    """
    folder = os.environ.get(SLOW_CACHE_ENV, "").strip()
    if not folder:
        return compute()
    path = Path(folder) / f"{slow_cache_key(key)}.pickle"
    try:
        value = pickle.loads(path.read_bytes())
    except Exception:                                   # noqa: BLE001 — кэш: любой сбой чтения — промах
        pass
    else:
        # Видно в журнале: числа не посчитаны, а прочитаны (и `seconds` — прошлого расчёта).
        print(f"медленные блоки — из кэша {path}", file=sys.stderr)
        return value
    value = compute()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            pickle.dump(value, fh, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, path)
    except Exception:                                   # noqa: BLE001 — кэш не записался: числа уже есть
        Path(tmp).unlink(missing_ok=True)
    return value


def slow_cache_key(key: str) -> str:
    """Ключ файла кэша: книга + код и данные, которые читает расчёт, + интерпретатор."""
    return hashlib.sha256("\n".join([key, code_digest(), sys.version, platform.platform()])
                          .encode("utf-8")).hexdigest()


@lru_cache(maxsize=1)
def code_digest() -> str:
    """sha256 файлов model/*.py, data/facts/** и файла книги (ядро читает с диска
    дату книги — `core._book_file_valuation_date`)."""
    root = Path(__file__).resolve().parents[1]
    files = sorted([*root.glob("model/*.py"), *(p for p in root.glob("data/facts/**/*") if p.is_file()),
                    BOOK_YAML])
    h = hashlib.sha256()
    for p in files:
        h.update(p.relative_to(root).as_posix().encode("utf-8") + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()


def _slow_blocks(A: dict) -> dict:
    """Расчёт медленных блоков (`slow_blocks`)."""
    import time

    from model.uncertainty import (MedianAnchor, next_report_median, reverse_dcf,
                                   reverse_dcf_median, uncertainty)

    t0 = time.perf_counter()
    band = uncertainty(A)
    t1 = time.perf_counter()
    point_reverse = reverse_dcf(A)
    anchor = MedianAnchor(A, band)
    reverse = reverse_dcf_median(A, band, point_reverse, anchor)
    t2 = time.perf_counter()
    judgements = judgement_table(A, limit=None)
    t3 = time.perf_counter()
    # Нейтральная маржа — пересчёты медианы (≈2 с на сетку). Отказ здесь не
    # имеет права стоить выпуска: блок справочный, и без него экран пишет
    # «нет в выпуске» с причиной.
    report = None
    try:
        report, neutral = next_report_median(A, band, anchor=anchor)
    except Exception as exc:                        # noqa: BLE001
        neutral = dict(error=f"{type(exc).__name__}: {exc}"[:200])
    t4 = time.perf_counter()
    return dict(
        uncertainty=band, reverse_dcf=reverse, judgements=judgements, neutral=neutral,
        next_report=report, point_reverse_dcf=point_reverse,
        seconds=dict(uncertainty=t1 - t0, reverse_dcf=t2 - t1, judgements=t3 - t2,
                     neutral=t4 - t3),
        median_grids=anchor.evaluations * anchor.n + anchor.full_grids,
        median_evaluations=anchor.evaluations)


# Сетка λ выпуска — положения ползунка витрины: λ = i / LAMBDA_STEPS, то есть
# шаг 0,05 (`web/app.js`, input#lambda, `step`; совпадение сверяет тест). На
# каждом положении выпуск печатает то, что витрина иначе замораживала бы с
# подписью «при λ книги»: P(ниже рынка), перцентиль рынка и среднее прогонов
# (из прогонов полосы) и EV центра против рыночного V* (тем же расчётом ядра,
# что и при λ книги). Решение владельца 25.09.2026.
LAMBDA_STEPS = 20


def lambda_grid(book_lambda: float) -> list[float]:
    """Положения ползунка λ: 0, 1/20, …, 1 — и λ книги, если его нет на сетке."""
    grid = [i / LAMBDA_STEPS for i in range(LAMBDA_STEPS + 1)]
    if not any(math.isclose(g, book_lambda, abs_tol=1e-12) for g in grid):
        grid = sorted(grid + [book_lambda])
    return grid


def band_by_lambda(head: dict) -> list[dict]:
    """P(ниже рынка), перцентиль рынка и среднее прогонов при каждом λ сетки.

    Центр прогона при λ — низ + λ·(верх − низ) (книга 1.4, §5 п. 4; то же
    правило, по которому витрина пересчитывает медиану и полосы) на тех самых
    прогонах, что публикует выпуск (`low_draws`/`high_draws`, до рубля): таблицу
    можно воспроизвести из выпуска без допусков. При λ книги строка — числа
    самого заголовка (`release: True`), посчитанные ядром до округления прогонов.
    """
    lows, highs = head["low_draws"], head["high_draws"]
    market = head["market"]
    n = min(len(lows), len(highs))
    book_lam = head["own_macro_confidence"]
    out = []
    for lam in lambda_grid(book_lam):
        if math.isclose(lam, book_lam, abs_tol=1e-12):
            out.append({"lambda": round(lam, 4), "p_below_market": head["p_below_market"],
                        "market_percentile": head["market_percentile"], "mean": head["mean"],
                        "release": True})
            continue
        centres = [lows[i] + lam * (highs[i] - lows[i]) for i in range(n)]
        out.append({"lambda": round(lam, 4),
                    "p_below_market": round(sum(1 for c in centres if c < market) / n, 4),
                    "market_percentile": round(sum(1 for c in centres if c <= market) / n, 4),
                    "mean": round(math.fsum(centres) / n, 1), "release": False})
    return out


def center_ev_by_lambda(release: Release, band: dict | None = None) -> list[dict]:
    """EV центра против рыночного V* при каждом λ сетки — функцией ядра.

    `model.grid.center_ev_at` — тот же расчёт, которым `fair_value` печатает
    строку при λ книги; здесь меняется только вес своего взгляда. Цена 1 % EV
    тоже своя у каждого λ: слои по-разному чувствительны к активам. С полосой
    (`band`, книга 1.5, диагностика по медиане) — `center_ev_median` при
    каждом λ: медиана прогонов полосы при этом λ против рынка.
    """
    from model.grid import center_ev_at
    from model.uncertainty import center_ev_median

    fv = release.fair_value
    neutral, own = release.layers["macro_neutral"], release.layers["analytical"]
    first = center_ev_median(release.book, band) if band else fv.center_ev
    out = []
    for lam in lambda_grid(fv.own_macro_confidence):
        at_book = math.isclose(lam, fv.own_macro_confidence, abs_tol=1e-12)
        # При λ книги — сама строка выпуска: совпадение с первой строкой
        # держится построением, а не повтором расчёта.
        ce = (first if at_book
              else center_ev_median(release.book, band, lam) if band
              else center_ev_at(release.book, neutral, own, lam, fv.market))
        out.append({"lambda": round(lam, 4), "v0": round(ce.v0, 1), "v_star": round(ce.v_star, 1),
                    "gap": round(ce.gap_vs_v_star, 4),
                    "rub_per_1pct_ev": round(ce.rub_per_1pct_ev, 1), "release": at_book})
    return out


def axis_paths(A: dict) -> dict[str, list[str]]:
    """Имя оси полосы → её пути в книге (`valuation.uncertainty.axes`)."""
    from model.uncertainty import axis_spec

    U = A["valuation"]["uncertainty"]
    return {a["name"]: list(axis_spec(a)["paths"]) for a in U.get("axes", [])}


def link_contributions(contributions: list[dict], judgements: list[dict]) -> None:
    """Ключ строки таблицы суждений у каждого вклада в полосу — на месте.

    Вклад подписан именем оси полосы, а таблица суждений — своими строками
    (`sensitivities` книги); это два разных списка книги. Связь — строка
    суждений с ТЕМ ЖЕ набором путей, что у оси (ось A-C0 «долгосрочный уровень
    маржи» — строка A-C0, а не цель одного режима A-C1, хотя пути пересекаются
    и у них), иначе — с наибольшим пересечением путей (у оси «поддерживающий
    capex (все уровни)» это строка `capex.maintenance_pct.base`). Порядок
    строк таблицы (по цене ошибки) на выбор не влияет: он плывёт от выпуска к
    выпуску. Нет пересечения — `judgement_key: None`, а не угадывание по
    названию.
    """
    for c in contributions:
        paths = set(c.get("paths") or [])
        best, best_score = None, (0, 0)
        for j in judgements:
            other = set(j.get("paths") or [j["key"]])
            common = len(paths & other)
            score = (int(bool(common) and other == paths), common)
            if common and score > best_score:
                best, best_score = j["key"], score
        c["judgement_key"] = best


def headline_payload(A: dict, fv, band: dict) -> dict:
    """Печатаемый заголовок — медиана распределения центра и полосы.

    Крупно — медиана распределения центра по суждениям книги (A-V9) и полосы
    80 % (P10–P90) и 50 % (P25–P75); точка «все суждения в центре» — строкой
    рядом. Числа — `model.uncertainty.headline_of` на книге ВЫПУСКА (живые
    цена и дата оценки): своих формул здесь нет, выпуск только округляет.

    `low_draws` / `high_draws` — низ и верх диапазона ставок в каждом из
    прогонов (до рубля): центр прогона при любом λ = низ + λ·(верх − низ), и
    витрина пересчитывает медиану и полосы ползунком без нового прогона.
    Перцентиль рынка — доля прогонов, чей центр не выше рыночной цены.
    """
    from model.uncertainty import headline_of

    head = headline_of(A, fv, band)
    market = band["market"]
    centres = band["central_sorted"]
    step = float(A["valuation"]["headline"].get("print_step", 50.0))
    paths = axis_paths(A)
    out = dict(
        method=head["method"],
        median=round(head["central"], 1), printed_median=head["printed_central"],
        band80=[round(x, 1) for x in head["band"]], printed_band80=head["printed_band"],
        band50=[round(x, 1) for x in head["inner"]], printed_band50=head["printed_inner"],
        point=round(head["point_at_book_centres"], 1), printed_point=head["printed_point"],
        mean=round(band["mean_central"], 1),
        market=market,
        p_below_market=round(head["p_central_below_market"], 4),
        market_percentile=round(sum(1 for c in centres if c <= market) / len(centres), 4),
        draws=band["draws"], seed=band["seed"],
        own_macro_confidence=band["own_macro_confidence"], print_step=step,
        quantiles={name: _round(band[name], 1) for name in ("central", "low", "high")},
        v0={name: _round(band[name], 2) for name in ("v0_own", "v0_market", "v0_lambda")},
        # Ось полосы — с её путями в книге; ключ строки суждений
        # (`judgement_key`) дописывает `build_payload`, когда таблица суждений
        # собрана (`link_contributions`).
        contributions=[dict(axis=c["axis"], paths=paths.get(c["axis"], []),
                            share=round(c["share"], 4), rank_corr=round(c["rank_corr"], 4))
                       for c in band["contributions"]],
        low_draws=[int(round(x)) for x in band["low_draws"]],
        high_draws=[int(round(x)) for x in band["high_draws"]],
    )
    out["by_lambda"] = band_by_lambda(out)
    return out


def ev_first_line(release: Release, headline: dict | None, band: dict | None = None) -> dict:
    """Первая строка экрана «Оценка»: EV против рыночного V*.

    Первой строкой — стоимость бизнеса точки (V0 λ-смеси слоёв) против V* —
    стоимости активов, при которой ТА ЖЕ функция «EV → цена» даёт рыночную
    цену (`fair_value.center_ev`), — и P(ниже рынка) из полосы. Рядом — V0
    каждого слоя против своего V* (`fair_value.ev_comparison`) и справочно
    сравнение «капитализация + D»: оно приравнивает опцион акционера к
    внутренней стоимости и при страйке K = D·(1 + k) с отображением не
    согласовано. Цена 1 % EV — для печатаемого центра
    (`center_ev.rub_per_1pct_ev`), а не рыночный рычаг «капитализация + D»
    (`rub_per_ev_percent`).

    EBITDA — знаменатель ядра, тот же, что у `ev_comparison` выпуска.

    С полосой (полная сборка) строка — для печатаемой МЕДИАНЫ
    (`model.uncertainty.center_ev_median`): V0 — медиана λ-смеси V0 прогонов,
    V* — при котором медиана прогонов равна рынку; слои — то же при λ = 1 и
    λ = 0, поле `target` — «median». Без полосы (быстрая сборка) — точка.
    """
    from model.uncertainty import center_ev_median

    fv = release.fair_value
    center = fv.center_ev
    if band:
        center = center_ev_median(release.book, band)
    ebitda = _ebitda_of_layer(release)
    layers = {}
    for name, lam in (("analytical", 1.0), ("macro_neutral", 0.0)):
        cmp = center_ev_median(release.book, band, lam) if band else fv.ev_comparison[name]
        layers[name] = dict(title=release.layers[name].title, v0=round(cmp.v0, 1),
                            v_star=round(cmp.v_star, 1), gap=round(cmp.gap_vs_v_star, 4))
    return dict(**({"target": "median"} if band else {}),
        v0=round(center.v0, 1), v_star=round(center.v_star, 1),
        gap=round(center.gap_vs_v_star, 4),
        rub_per_1pct_ev=round(center.rub_per_1pct_ev, 1),
        own_macro_confidence=fv.own_macro_confidence,
        ebitda_ltm=round(ebitda, 2),
        ev_ebitda=round(center.v0 / ebitda, 2) if ebitda else None,
        ev_ebitda_v_star=round(center.v_star / ebitda, 2) if ebitda else None,
        p_below_market=(headline or {}).get("p_below_market"),
        layers=layers,
        cap_plus_d=dict(ev=round(fv.ev_market_implied, 1), gap=round(fv.ev_gap, 4),
                        ev_ebitda=round(fv.ev_market_implied / ebitda, 2) if ebitda else None,
                        rub_per_1pct_ev=round(fv.rub_per_ev_percent, 1)),
        # Та же строка при каждом положении ползунка λ (`center_ev_by_lambda`).
        by_lambda=center_ev_by_lambda(release, band),
    )


def rates_view_block(fv) -> dict:
    """Строка «взгляд на ставки»: рыночные ставки X — свой взгляд Y (+Z ₽).

    Ось «низ — верх» — не интервал, а вклад собственного взгляда на ставки
    (`fair_value.rates_view`): низ — мир форвардов ОФЗ («рынок прав
    целиком»), верх — свой макро-взгляд; точка стоит между ними с весом λ.
    """
    view = fv.rates_view
    return dict(rub=round(view.rub, 1), v0=round(view.v0, 2),
                low=round(fv.low, 1), high=round(fv.high, 1),
                printed_low=fv.printed_low, printed_high=fv.printed_high,
                point=round(fv.central, 1), own_macro_confidence=fv.own_macro_confidence)


# Ось обратного DCF, чьё значение — число, а не доля: бета печатается «0,62»,
# а не «62 %». Остальные оси книги — доли (ставки, касса, σ) или сдвиги в
# пунктах (`kind: shift`). Это правило ВИДА ЧИСЛА на экране, не допущение.
PLAIN_NUMBER_PATHS = frozenset({"valuation.beta_u"})


def reverse_dcf_block(A: dict, rows: list[dict]) -> dict:
    """Что заложено в цену — по спецификации книги (`reverse_dcf`).

    Для каждой оси книги: значение ОДНОГО суждения, при котором печатаемая
    медиана равна рыночной цене выпуска (остальное — как в книге,
    `model.uncertainty.reverse_dcf_median`), точечное значение, с которого
    начат поиск (`point_value`), значение книги, диапазон книги и «внутри /
    вне». `value = None` — цена недостижима на отрезке поиска книги, и это
    ответ, а не пропуск.
    """
    out = []
    for row in rows:
        kind = row["kind"]
        # Единица оси — вид числа на экране: сдвиг — пункты, множитель — «×1,0»,
        # целевой рычаг — «1,0×» (то же правило, что у суждений, `judgement_unit`),
        # бета — числом, прочие значения — доли.
        unit = ("pp" if kind == "shift" else "mult" if kind == "scale"
                else "number" if row["paths"][0] in PLAIN_NUMBER_PATHS
                else "times" if judgement_unit(row["paths"][0]) == "times" else "pct")
        out.append(dict(
            name=row["name"], paths=list(row["paths"]), kind=kind, unit=unit,
            book_value=(0.0 if kind == "shift" else row["book_value"]),
            value=None if row["value"] is None else round(row["value"], 6),
            range=list(row["range"]), inside_range=bool(row["inside_range"]),
            point_value=(None if row.get("point_value") is None else round(row["point_value"], 6)),
        ))
    return dict(market=A["market"]["price"], valuation_date=A["meta"]["valuation_date"],
                target="median", draws=median_draws(A),
                criterion="медиана распределения центра по суждениям книги = рыночной цене",
                rows=out)


def _peer_facts() -> dict:
    from model.paths import FACTS_DIR

    path = FACTS_DIR / "peers.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def peer_entries(facts: dict) -> list[tuple[str, dict]]:
    """Аналоги файла фактов в одной форме: [(ключ, {title, price_series,
    shares_mln, net_debt, dividends_after_balance, ebitda_ltm, source, note})].

    Файл Ленты (`lenta-facts-peers-v1`, лист `evidence/book-1.0/facts/`):
    `peers` — список строк {name, ticker, shares_mln, net_debt,
    dividends_after_balance, ebitda_ltm, src, …}; ключ — тикер, живая цена —
    ряд `moex.price.<тикер>` слоя индикаторов (`indicators/sources.py`
    пишет котировки эмитента и аналогов под этими именами). Файл 850oa —
    словарь {ключ: {title, price_series, …, net_debt_source,
    ebitda_ltm_source}}.
    """
    raw = facts.get("peers", {})
    if isinstance(raw, dict):
        return [(key, dict(peer, source=f"{peer['net_debt_source']}; EBITDA: {peer['ebitda_ltm_source']}"))
                for key, peer in raw.items()]
    return [(p["ticker"], dict(title=p["name"], price_series=f"moex.price.{p['ticker']}",
                               shares_mln=p["shares_mln"], net_debt=p["net_debt"],
                               dividends_after_balance=p.get("dividends_after_balance", 0.0),
                               ebitda_ltm=p["ebitda_ltm"], source=p["src"], note=p.get("note", "")))
            for p in raw]


def peers_block(A: dict, store=None) -> dict:
    """Аналоги на ОДНОЙ базе с эмитентом (`data/facts/peers.json`).

    EV = капитализация по живой цене + ЧД до МСФО 16 на дату баланса +
    дивиденды, объявленные до неё и выплаченные после; EV/EBITDA — к EBITDA
    до МСФО 16 за 12 месяцев на ту же дату. Эмитент — той же функцией: цена
    выпуска, акции в обращении, ЧД и EBITDA LTM из фактов книги. Нет живой
    цены аналога — его мультипликатор не печатается (`ev_ebitda: None` с
    причиной), а не подменяется книжной константой: константы книги
    (`market.peers`) — справочно, отдельным полем, без источника и даты.
    """
    facts = _peer_facts()
    F = A["facts"]

    def row(key, title, price, price_date, shares, net_debt, dividends, ebitda, source, note=""):
        if price is None:
            return dict(key=key, title=title, price=None, price_date=None, ev=None,
                        ev_ebitda=None, net_debt=net_debt, ebitda_ltm=ebitda,
                        shares_mln=shares, dividends_after_balance=dividends,
                        source=source, note=note, missing="нет живой цены")
        cap = price * shares / 1000.0
        ev = cap + net_debt + dividends
        return dict(key=key, title=title, price=price, price_date=price_date,
                    shares_mln=shares, cap=round(cap, 1), net_debt=net_debt,
                    dividends_after_balance=dividends, ev=round(ev, 1),
                    ebitda_ltm=ebitda, ev_ebitda=round(ev / ebitda, 2),
                    source=source, note=note)

    rows = [row(issuer.TICKER, issuer.NAME, A["market"]["price"], A["meta"]["valuation_date"],
                F["shares_out_mln"], F["anchor"]["net_debt"], 0.0, F["anchor"]["ebitda_ltm"],
                "факты книги: facts.anchor.net_debt, facts.anchor.ebitda_ltm, facts.shares_out_mln")]
    if facts:
        try:
            from indicators.store import Store
            store = store or Store()
        except ImportError:                                   # pragma: no cover
            store = None
        for key, peer in peer_entries(facts):
            series = store.load(peer["price_series"]) if store is not None else None
            point = series.latest() if series else None
            price = float(point.value) if point and point.value else None
            rows.append(row(key, peer["title"], price, point.period if price else None,
                            peer["shares_mln"], peer["net_debt"],
                            peer.get("dividends_after_balance", 0.0), peer["ebitda_ltm"],
                            peer["source"], peer.get("note", "")))
    return dict(as_of=facts.get("as_of", A["meta"]["facts_date"]),
                basis=facts.get("basis", ""), subject_key=issuer.TICKER, rows=rows)


def _ebitda_of_layer(release: Release) -> float:
    """EBITDA за 12 месяцев на дату оценки — знаменатель ЯДРА, слой «свой взгляд».

    Восстанавливается из клеток (`ev / ev_ebitda_ltm`) с весами слоя: пока
    закрытых периодов нет, она у всех клеток одна — отчётная; после закрытия
    полугодия в ней сидит прогноз закрытого периода, свой у каждой клетки.
    """
    from model.grid import layer_weights

    weights = layer_weights(release.book, release.cells)["analytical"]
    mass = ebitda = 0.0
    for c in release.cells:
        p = weights.get((c.cell.world, c.cell.margin_regime, c.cell.capex), 0.0)
        if p and c.result.ev_ebitda_ltm:
            mass += p
            ebitda += p * c.result.ev / c.result.ev_ebitda_ltm
    return ebitda / mass if mass else 0.0


def _ev_comparison(release: Release) -> dict:
    """Первая строка экрана «Оценка»: уровень стоимости бизнеса против рынка.

    Решение владельца 23.09.2026: заголовок остаётся ценой акции, но первой
    строкой идёт утверждение об EV — там, где спор с рынком умеренный
    (−6 %), а не там, где рычаг раздувает его до «рынок в 1,7 раза дороже».

    Ничего не считается заново. EV модели и EV, заложенный в цену, — те же
    `ev_model`/`ev_market_implied`, что уже лежат в `fair_value`. EBITDA —
    ЗНАМЕНАТЕЛЬ САМОГО ЯДРА: клетка отдаёт `ev_ebitda_ltm = ev / EBITDA за
    12 месяцев на дату оценки` (та же база до МСФО 16, в которой книга даёт
    мультипликаторы X5 и Ленты на экране мультипликаторов), и знаменатель
    восстанавливается делением обратно. Пока закрытых периодов нет, он у всех
    клеток один — отчётные 179,66; после закрытия полугодия в нём сидит
    прогноз закрытого периода, свой у каждой клетки, и берётся его ожидание с
    весами того же слоя, чьё ожидание — `ev_model` (`_ebitda_of_layer`).

    Книга 1.4: это сравнение — СПРАВОЧНОЕ («капитализация + D»); первая строка
    экрана — EV против рыночного V* (`ev_first_line`).
    """
    fv = release.fair_value
    ebitda = _ebitda_of_layer(release)
    return dict(
        layer="analytical",
        ev_model=round(fv.ev_model, 1),
        ev_market_implied=round(fv.ev_market_implied, 1),
        gap=round(fv.ev_gap, 4),
        ebitda_ltm=round(ebitda, 2),
        ev_ebitda_model=round(fv.ev_model / ebitda, 2) if ebitda else None,
        ev_ebitda_market=round(fv.ev_market_implied / ebitda, 2) if ebitda else None,
    )


# Оси строки «Разброс точки по одному суждению при остальных в центре». Полоса
# A-V9 двигает все суждения сразу; эта строка — по одному, и отвечает на другой
# вопрос: сколько стоит каждое суждение в пределах его диапазона книги. Ключи —
# первый путь строки таблицы суждений (`key`), числа берутся из неё же.
#
# Состав — главные оси «Ленты» по вкладу в полосу (I1c, раздел 5): поддерживающий
# capex (A-K1), долгосрочный уровень маржи (A-C0 — общий фактор целей LT, а не
# цель одного режима), долгий реальный LFL (A-R2), ставка (β_u и ERP, A-V1/A-V2),
# дисконт за управление (A-V7 — суждение первого порядка при рычаге «Ленты», D5)
# и рост сети (темп открытий «у дома» A-R7 и сближение плотности «О'КЕЙ» A-R10).
HEADLINE_JUDGEMENT_AXES = (
    ("capex", "поддерживающий capex", ("capex.maintenance_pct.base",)),
    ("margin", "долгосрочный уровень маржи (цели LT всех режимов)",
     ("margin.regimes.stress.target.LT",)),
    ("lfl", "долгий реальный LFL (сдвиг s LT)", ("revenue.ticket_shift.bear.LT",)),
    ("rate", "ставка (β_u, ERP)", ("valuation.beta_u", "valuation.erp")),
    ("governance", "дисконт за управление", ("valuation.governance_discount",)),
    ("network", "рост сети (открытия «у дома», сближение «О'КЕЙ»)",
     ("revenue.segments.conv.space.low.gross_open",
      "revenue.segments.okey.lfl_offset.by_regime.floor.2027H1")),
)


def judgement_spread(judgements: list[dict]) -> dict | None:
    """Минимум и максимум точки по главным суждениям — из таблицы суждений.

    Выборка, а не расчёт: каждое число — `price_low` или `price_high` строки
    `judgements` того же выпуска, и у каждого конца названы ось, строка и
    граница допущения, которые его дают. Каждое суждение — ПО ОДНОМУ при
    остальных в центре книги (`note`). Строки нет в таблице — ключ уходит в
    `missing`, а не молча выпадает из разброса. Нет ни одной строки (быстрая
    сборка без суждений) — `None`.
    """
    by_key = {j["key"]: j for j in judgements}
    axes, missing = [], []
    for axis, title, keys in HEADLINE_JUDGEMENT_AXES:
        missing += [k for k in keys if k not in by_key]
        ends = [dict(price=j[f"price_{side}"], key=j["key"], label=j["label"],
                     unit=j.get("unit") or judgement_unit(j["key"]),
                     bound=side, value=j[f"{side}_label"])
                for j in (by_key[k] for k in keys if k in by_key)
                for side in ("low", "high")]
        if not ends:
            continue
        axes.append(dict(axis=axis, title=title, keys=list(keys),
                         low=min(ends, key=lambda e: e["price"]),
                         high=max(ends, key=lambda e: e["price"])))
    if not axes:
        return None
    low = min(axes, key=lambda a: a["low"]["price"])
    high = max(axes, key=lambda a: a["high"]["price"])
    return dict(low=low["low"]["price"], high=high["high"]["price"],
                low_axis=low["axis"], high_axis=high["axis"],
                axes=axes, missing=missing,
                note="по одному суждению при остальных в центре")


def next_report_neutral_block(release: Release, neutral: dict | None,
                              median_rows: list[dict] | None = None) -> dict:
    """Нейтральная маржа ближайшего отчёта и наклон «рубли точки на 0,1 п.п.».

    Нейтральная маржа — факт отчёта, при котором печатаемая медиана не
    меняется (`model.uncertainty.next_report_median`), `central` — печатаемая
    медиана, `target` — «median». Наклон — разность центра на крайних строках
    таблицы «что даст отчёт» (`median_rows`), делённая на их расстояние в
    десятых долях пункта. Витрина эти числа не считает, а печатает. Отказ
    расчёта медианы — поле `error` вместо чисел (наклон тогда — по строкам
    точки `release.next_report`), выпуск при этом выходит.
    """
    out: dict = {"target": "median"} if median_rows is not None else {}
    if neutral and neutral.get("margin") is not None:
        out.update(period=neutral["period"], margin=round(neutral["margin"], 5),
                   central=round(neutral["central"], 1))
    elif neutral and neutral.get("error"):
        out["error"] = neutral["error"]
    rows = release.next_report if median_rows is None else median_rows
    if len(rows) >= 2 and rows[-1]["margin"] != rows[0]["margin"]:
        first, last = rows[0], rows[-1]
        out["slope_rub_per_0p1pp"] = round(
            (last["central"] - first["central"]) / ((last["margin"] - first["margin"]) * 1000), 1)
        out["slope_between"] = [round(first["margin"], 4), round(last["margin"], 4)]
        out.setdefault("period", first["period"])
    return out


def _soft(block, *args):
    """Справочный блок выпуска: его сбой — `{"error": причина}`, а не остановка.

    Выпуск не имеет права не выйти из-за блока, которого ядро не читает
    (референс-класс — «справочно» в самой книге): при испорченном блоке книги
    (`n: 0`, пропавшее поле) витрина просто не рисует таблицу класса.
    """
    try:
        return block(*args)
    except Exception as exc:                                    # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"[:200]}


def _wilson(k: int, n: int) -> list[float]:
    """95 % интервал Уилсона для доли k из n — как в листе книги (episodes)."""
    z = NormalDist().inv_cdf(0.975)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def reference_class_block(A: dict, unconditional: dict) -> dict | None:
    """Референс-класс эпизодов маржи рядом с вероятностями режимов книги.

    Блок книги `reference_class_margin` (СПРАВОЧНО: движок его не читает; лист
    `evidence/book-1.5/misc/episodes`): эпизоды «пик + 4 года» по годовым
    отчётам, доли исходов по режимам, средняя долгосрочная маржа класса,
    средняя цель режима. Выпуск добавляет 95 % интервал Уилсона к каждой доле
    (так же, как лист) и ожидаемую долгосрочную маржу самой книги —
    Σ P(режим) × цель LT режима, чтобы сравнение «класс против книги» стояло
    на экране числами выпуска. Нет блока — None.
    """
    rc = A.get("reference_class_margin")
    if not rc:
        return None
    n = int(rc["n"])
    shares = rc.get("share") or {}
    regimes = A["margin"]["regimes"]
    e_book = math.fsum(p * regimes[r]["target"]["LT"]
                       for r, p in unconditional.items() if r in regimes)
    return dict(
        n=n, share=_round(shares, 4),
        share_ci95={r: _wilson(int(round(v * n)), n) for r, v in shares.items()},
        e_m_lt=rc.get("e_m_lt"), e_m_lt_book=round(e_book, 5),
        mean_target_by_regime=_round(rc.get("mean_target_by_regime") or {}, 4),
        note=str(rc.get("note", ""))[:600],
        source="книга, reference_class_margin (лист evidence/book-1.5/misc/episodes) — "
               "справочно, движок не читает")


def open_period(A: dict) -> str:
    """Первое НЕЗАКРЫТОЕ полугодие на дату оценки — с него начинается поток в EV.

    Закрытые периоды ядро не приводит: их деньги уже в долге на дату оценки.
    Мост «Деньги и долг» подписывает этим полугодием строку свободного потока;
    начало горизонта книги после закрытия первого полугодия (книга 1.3.1 — с
    01.01.2027) было бы там неверно (проверка потока S2, 23.09.2026).
    """
    P = periods(A["meta"]["first_period"], A["meta"]["last_period"])
    return P[time_position(A)[0]]


# ------------------------------------------------ блоки «Ленты» (D16): сеть, периметр,
# стратегия и гайденс, дивиденды, дисконт за управление, базы отчётности
#
# Всё — пересчёт книги, фактов (`data/facts/*.json`) и строк ядра по клеткам
# сетки; своих допущений здесь нет. Ожидаемые пути печатаются для ДВУХ смесей
# клеток (`MIX_TITLES`): вероятностей сетки и смеси печатаемого заголовка.

MIX_TITLES = {
    "grid": "ожидание по сетке: вероятности клеток (слой «свой макро-взгляд»)",
    "headline": ("смесь печатаемого заголовка: λ книги × «свой макро-взгляд» + (1 − λ) × "
                 "«рыночные ставки как есть» — те же веса клеток, из которых ядро печатает "
                 "точку (V0 смеси = V0 точки); медиана и полосы — распределение этой смеси "
                 "по суждениям книги (A-V9)"),
}
# Путь сети печатается по полугодиям первых NETWORK_YEARS лет горизонта (с первого
# прогнозного полугодия по 2П года «первый + NETWORK_YEARS») и строкой LT —
# последним полугодием горизонта книги. Длина окна — выбор экрана, не допущение.
NETWORK_YEARS = 4


def _facts_file(name: str) -> dict:
    """Файл фактов `data/facts/<name>` целиком; нет файла — {} (блок скажет «нет данных»)."""
    from model.paths import FACTS_DIR

    path = FACTS_DIR / name
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def cell_mixes(release, cell_weights: dict) -> dict[str, dict[str, float]]:
    """Веса клеток двух смесей: «grid» (слой `analytical` — вероятности сетки) и
    «headline» (λ·analytical + (1 − λ)·macro_neutral, λ книги)."""
    lam = release.fair_value.own_macro_confidence
    own = cell_weights["analytical"]["weights"]
    neutral = cell_weights["macro_neutral"]["weights"]
    headline = {}
    for key in sorted(set(own) | set(neutral)):
        weight = lam * own.get(key, 0.0) + (1 - lam) * neutral.get(key, 0.0)
        if weight > 0:
            headline[key] = weight
    return {"grid": dict(own), "headline": headline}


class _Mix:
    """Ожидание величины строк ядра по смеси клеток: Σ w·x / Σ w по клеткам, где x есть."""

    def __init__(self, release, weights: dict[str, float]):
        results = {c.cell.key: c.result for c in release.cells}
        self.cells = [(w, results[k], {r.period: r for r in results[k].rows})
                      for k, w in weights.items()]

    def of(self, value_of):
        num = den = 0.0
        for w, result, rows in self.cells:
            value = value_of(result, rows)
            if value is None:
                continue
            num += w * value
            den += w
        return num / den if den else None

    def row(self, period: str, value_of):
        return self.of(lambda _r, rows: (value_of(rows[period]) if period in rows else None))

    def segment(self, period: str, sid: str, field_name: str):
        def pick(row):
            step = row.segments.get(sid)
            return None if step is None else getattr(step, field_name)
        return self.row(period, pick)

    def year(self, year: int, value_of):
        """Годовая величина клетки (`CellResult.annual`) — ожидание по смеси."""
        def pick(result, _rows):
            for a in result.annual():
                if a["year"] == year:
                    return value_of(a)
            return None
        return self.of(pick)

    def mass(self, predicate) -> float:
        return math.fsum(w for w, result, rows in self.cells if predicate(result, rows))


def _r(value, digits: int):
    return None if value is None else round(value, digits) + 0.0


def _mix_meta(release, mixes: dict) -> dict:
    """Смеси: подпись, число клеток и V0 смеси (Σ w·EV клеток) — сверка с заголовком."""
    out = {}
    for name, weights in mixes.items():
        mix = _Mix(release, weights)
        out[name] = dict(title=MIX_TITLES[name], cells=len(weights),
                         v0=_r(mix.of(lambda res, _rows: res.ev), 2))
    out["headline"]["lambda"] = release.fair_value.own_macro_confidence
    return out


def _back_periods(anchor: str, n: int) -> list[str]:
    """n полугодий, кончая якорем, по возрастанию (метки когорт истории открытий)."""
    from indicators import periods as P

    return [P.shift(anchor, -i) for i in range(n - 1, -1, -1)]


def _network_anchor(A: dict) -> dict:
    """Сегменты на якоре — факты книги (`facts.segments`), без расчёта ядра."""
    F, R = A["facts"], A["revenue"]["segments"]
    anchor = F["anchor"]["period"]
    rows = []
    for sid, f in F["segments"].items():
        spec = R.get(sid) or {}
        revenue = {p: v for p, v in (f.get("revenue") or {}).items()}
        eff = {p: v for p, v in (f.get("eff_area_avg_hist") or {}).items()}
        area, stores = f.get("area_end"), f.get("stores_end")
        hist = list(f.get("new_area_gross_hist") or [])
        rows.append(dict(
            id=sid, name=spec.get("name", sid), mode=spec.get("mode"),
            revenue=revenue, revenue_basis=dict(f.get("revenue_basis") or {}),
            revenue_se=dict(f.get("revenue_se") or {}),
            area_end=area, stores_end=stores,
            avg_store_area_m2=(_r(area / stores * 1000.0, 0) if area and stores else None),
            eff_area_avg=eff,
            density={p: _r(revenue[p] / eff[p] * 1000.0, 1) for p in eff
                     if revenue.get(p) and eff.get(p)},
            opened_area=dict(zip(_back_periods(anchor, len(hist)), hist)) if hist else {},
            closed_area=dict(f.get("closed_area_hist") or {})))
    total = dict(revenue=dict(F["revenue"]),
                 area_end=_r(math.fsum(r["area_end"] for r in rows if r["area_end"]), 3),
                 stores_end=sum(r["stores_end"] for r in rows if r["stores_end"]))
    return dict(period=anchor, facts_date=A["meta"]["facts_date"], segments=rows, total=total)


def _network_path(A: dict, mix: _Mix, path: list[str], anchor_block: dict) -> list[dict]:
    """Ожидаемый путь сети по сегментам для одной смеси клеток."""
    from indicators import periods as P

    anchor = {s["id"]: s for s in anchor_block["segments"]}
    seg_ids = list(A["revenue"]["segments"])
    cache: dict[tuple[str, str], float | None] = {}

    def revenue_at(sid: str, period: str):
        if period in (anchor[sid]["revenue"] or {}):
            return anchor[sid]["revenue"][period]
        if (sid, period) not in cache:
            cache[(sid, period)] = mix.segment(period, sid, "revenue")
        return cache[(sid, period)]

    out = []
    for period in path:
        segments, opened_total, closed_total, stores_total = {}, 0.0, 0.0, 0.0
        for sid in seg_ids:
            base = anchor.get(sid) or {}
            revenue = revenue_at(sid, period)
            prior = revenue_at(sid, P.shift(period, -2))
            area = mix.segment(period, sid, "area_end")
            eff = mix.segment(period, sid, "effective_area_avg")
            opened = mix.segment(period, sid, "opened") or 0.0
            closed = mix.segment(period, sid, "closed") or 0.0
            size = (base["area_end"] / base["stores_end"]
                    if base.get("area_end") and base.get("stores_end") else None)
            stores = area / size if (area is not None and size) else None
            segments[sid] = dict(
                revenue=_r(revenue, 2), growth=_r(revenue / prior - 1, 4) if revenue and prior else None,
                area_end=_r(area, 1), eff_area_avg=_r(eff, 1),
                density=_r(revenue / eff * 1000.0, 1) if revenue and eff else None,
                stores_est=_r(stores, 0),
                opened_area=_r(opened, 2), closed_area=_r(closed, 2),
                opened_stores_est=_r(opened / size, 0) if size else None)
            opened_total += opened
            closed_total += closed
            stores_total += stores or 0.0
        total_revenue = mix.row(period, lambda r: r.revenue)
        out.append(dict(period=period, segments=segments, total=dict(
            revenue=_r(total_revenue, 2), area_end=_r(mix.row(period, lambda r: r.area_end), 1),
            opened_area=_r(opened_total, 2), closed_area=_r(closed_total, 2),
            stores_est=_r(stores_total, 0))))
    return out


def _network_block(A: dict, release, mixes: dict) -> dict:
    """Сеть по форматам (D16): факты на якоре и ожидаемый путь сетки и смеси заголовка.

    Путь — полугодия первых `NETWORK_YEARS` лет горизонта и строка LT (последнее
    полугодие книги). Ядро считает сеть по сегментам (`StepRow.segments`):
    выручка, площадь на конец, эффективная площадь, валовые открытия и закрытия
    (тыс. м²). Плотность — выручка полугодия на средний метр эффективной
    площади (тыс. ₽ за полугодие; та же база, что у сближения «О'КЕЙ», решение
    ведущего B15). Магазинов ядро не считает: `stores_est` — площадь пути,
    делённая на среднюю площадь магазина сегмента на якоре (площади нового
    магазина книга не задаёт), `opened_stores_est` — открытия в тех же единицах.
    Ожидание отношения — отношение ожиданий (плотность, рост): числа строки
    согласованы между собой.
    """
    P = periods(A["meta"]["first_period"], A["meta"]["last_period"])
    last_year = int(P[0][:4]) + NETWORK_YEARS
    path = [p for p in P if int(p[:4]) <= last_year]
    anchor_block = _network_anchor(A)
    expected = {}
    for name, weights in mixes.items():
        mix = _Mix(release, weights)
        rows = _network_path(A, mix, path + [P[-1]], anchor_block)
        expected[name] = dict(rows=rows[:-1], lt=rows[-1])
    return dict(
        units=dict(revenue="млрд ₽ за полугодие", area="тыс. м² на конец полугодия",
                   density="тыс. ₽ на м² эффективной площади за полугодие",
                   stores="магазинов", opened="тыс. м² за полугодие (валовые)"),
        periods=path, lt_period=P[-1], anchor=anchor_block, expected=expected,
        notes=dict(
            stores_est="расчёт выпуска: площадь пути / средняя площадь магазина сегмента на "
                       "якоре — ядро магазинов не считает, площадь нового магазина книга не задаёт",
            density="выручка полугодия / средняя эффективная площадь полугодия",
            expectation="ожидание отношения — отношение ожиданий (выручки, площади)"))


def _deal_row(deal: dict) -> dict:
    """Сделка из `data/facts/inorganic.json`: даты контроля, цена, принятый долг, гудвил."""
    debt = deal.get("assumed_debt")
    if isinstance(debt, dict):
        debt = debt.get("total")
    contribution = deal.get("contribution")
    pro_forma = deal.get("pro_forma")
    pro_forma_numbers = ({k: v for k, v in pro_forma.items() if isinstance(v, (int, float))
                          and not isinstance(v, bool)} if isinstance(pro_forma, dict) else None)
    return dict(
        id=deal.get("id"), name=deal.get("name"), control=deal.get("control"),
        format=deal.get("format"), consideration=deal.get("consideration"),
        paid=deal.get("paid"), assumed_debt=debt, assumed_leases=deal.get("assumed_leases"),
        goodwill=deal.get("goodwill"), net_assets=deal.get("net_assets"),
        related_party=deal.get("related_party"), related_note=deal.get("related_note"),
        contribution=(contribution if isinstance(contribution, dict) else None),
        contribution_note=(contribution if isinstance(contribution, str) else None),
        pro_forma=pro_forma_numbers,
        pro_forma_note=(pro_forma if isinstance(pro_forma, str) else None),
        src=deal.get("src"))


def _perimeter_block(A: dict, release, mixes: dict) -> dict:
    """Периметр и интеграция покупок (D16): сделки из фактов и пути ядра.

    Сделки — `data/facts/inorganic.json` (дата контроля, цена, принятый долг,
    гудвил, вклад с даты покупки, проформа). Интеграция: плотность «О'КЕЙ»
    против гипермаркетов «Ленты» на ОДНОЙ базе площади — эффективной (решение
    ведущего B15): на якоре — факты книги, дальше — ожидание смесей; убытки
    «Дом Ленты» — вклад с даты консолидации (прим. 5 МСФО 6М2026); интеграционный
    capex и пул запертых убытков приобретённых юрлиц — строки ядра.
    """
    from indicators import perimeter

    facts = _facts_file("inorganic.json")
    deals = [_deal_row(d) for d in facts.get("deals") or []]
    F, R = A["facts"], A["revenue"]["segments"]
    seg = F["segments"]
    P = periods(A["meta"]["first_period"], A["meta"]["last_period"])

    def ratio(okey_rev, okey_eff, hyper_rev, hyper_eff):
        if not (okey_rev and okey_eff and hyper_rev and hyper_eff):
            return None
        return (okey_rev / okey_eff) / (hyper_rev / hyper_eff)

    okey_f, hyper_f = seg.get("okey") or {}, seg.get("hyper") or {}
    anchor_ratio = {}
    for p in (okey_f.get("eff_area_avg_hist") or {}):
        value = ratio((okey_f.get("revenue") or {}).get(p), okey_f["eff_area_avg_hist"].get(p),
                      (hyper_f.get("revenue") or {}).get(p),
                      (hyper_f.get("eff_area_avg_hist") or {}).get(p))
        if value is not None:
            anchor_ratio[p] = round(value, 4)
    last_year = int(P[0][:4]) + NETWORK_YEARS
    path = [p for p in P if int(p[:4]) <= last_year] + [P[-1]]
    paths = {}
    for name, weights in mixes.items():
        mix = _Mix(release, weights)
        rows = []
        for p in path:
            value = ratio(mix.segment(p, "okey", "revenue"), mix.segment(p, "okey", "effective_area_avg"),
                          mix.segment(p, "hyper", "revenue"), mix.segment(p, "hyper", "effective_area_avg"))
            rows.append(dict(period=p, okey_to_hyper_density=_r(value, 4),
                             diy_revenue=_r(mix.segment(p, "diy", "revenue"), 2),
                             capex_integration=_r(mix.row(p, lambda r: r.capex_integration), 3),
                             acquired_nol_pool=_r(mix.row(p, lambda r: r.tax_loss_pool_acquired), 3)))
        paths[name] = rows
    okey = next((d for d in facts.get("deals") or [] if d.get("id") == "okey"), {}) or {}
    diy = next((d for d in facts.get("deals") or [] if d.get("id") == "obi_domlenta"), {}) or {}
    diy_c = diy.get("contribution") if isinstance(diy.get("contribution"), dict) else {}
    okey_pf = okey.get("pro_forma") if isinstance(okey.get("pro_forma"), dict) else {}
    nol = (A.get("tax") or {}).get("acquired_nol") or {}
    return dict(
        deals=deals,
        breaks_halves={k: v for k, v in (facts.get("perimeter_breaks_halves") or {}).items()
                       if k != "note"},
        breaks_journal=[b.as_dict() for b in perimeter.breaks()],
        goodwill=facts.get("goodwill_bridge_1H2026"),
        acquisition_cash_1h2026=facts.get("acquisition_cash_1H2026"),
        integration=dict(
            okey=dict(
                name=(R.get("okey") or {}).get("name"),
                density_basis="выручка полугодия на средний метр эффективной площади; отношение "
                              "«О'КЕЙ» к гипермаркетам «Ленты» (решение ведущего B15)",
                density_ratio_anchor=anchor_ratio,
                density_press_2025=okey.get("density_k_rub_per_m2"),
                assumed_debt=(okey.get("assumed_debt") or {}).get("total")
                if isinstance(okey.get("assumed_debt"), dict) else okey.get("assumed_debt"),
                assumed_leases=okey.get("assumed_leases"),
                revenue_1h2026_full=okey_pf.get("revenue_1H2026_full"),
                pbt_1h2026_full=okey_pf.get("pbt_1H2026_full"),
                working_capital_at_acquisition=okey.get("working_capital_at_acq"),
                acquired_nwc_path=(A.get("nwc") or {}).get("acquired_path"),
                stores_end=okey_f.get("stores_end"), area_end=okey_f.get("area_end")),
            domlenta=dict(
                name=(R.get("diy") or {}).get("name"),
                period=diy_c.get("period"), revenue=diy_c.get("revenue"), pbt=diy_c.get("pbt"),
                pbt_margin=(_r(diy_c["pbt"] / diy_c["revenue"], 4)
                            if isinstance(diy_c.get("pbt"), (int, float)) and diy_c.get("revenue")
                            else None),
                stores_end=(seg.get("diy") or {}).get("stores_end"),
                area_end=(seg.get("diy") or {}).get("area_end"),
                density_path_book=(R.get("diy") or {}).get("density_path"),
                src=diy.get("src")),
            integration_capex_book=(A.get("capex") or {}).get("integration_capex"),
            acquired_nol=dict(amount=nol.get("amount"), usable_from=nol.get("usable_from"),
                              haircut=(nol.get("discount_rule") or {}).get("haircut"),
                              method=(nol.get("discount_rule") or {}).get("method")),
            expected=paths),
        notes=dict(
            integration_capex="млрд ₽ за полугодие, входит в capex (`capex.integration_capex`)",
            acquired_nol_pool="запертые убытки приобретённых юрлиц на конец полугодия, млрд ₽ "
                              "(`tax.acquired_nol`)"))


def _reported_half(base: dict, period: str) -> dict | None:
    """Отчётное полугодие IAS 17 из `accounting_base.json`: выручка, EBITDA, денежный capex."""
    from model.checks import CAPEX_CASH_LINES

    row = ((base.get("halves") or {}).get(period) or {}).get("ias17") or {}
    pl, cf = row.get("pl") or {}, row.get("cf") or {}
    revenue = (pl.get("revenue") or {}).get("v")
    ebitda = (pl.get("ebitda") or {}).get("v")
    if revenue is None or ebitda is None:
        return None
    capex = -math.fsum((cf.get(name) or {}).get("v") or 0.0 for name in CAPEX_CASH_LINES)
    return dict(revenue=revenue, ebitda=ebitda, capex=capex)


def _year_view(A: dict, base: dict, mix: _Mix, year: int) -> dict:
    """Год целиком: отчётные полугодия (IAS 17, отчётный периметр) + ожидание смеси
    по прогнозным. Выручка, EBITDA, маржа, capex/выручка, ЧД/EBITDA конца года."""
    revenue = ebitda = capex = 0.0
    parts = []
    for half in (1, 2):
        period = f"{year}H{half}"
        rev = mix.row(period, lambda r: r.revenue)
        if rev is not None:
            revenue += rev
            ebitda += mix.row(period, lambda r: r.ebitda)
            capex += mix.row(period, lambda r: r.capex)
            parts.append("model")
            continue
        reported = _reported_half(base, period)
        if reported is None:
            return {}
        revenue += reported["revenue"]
        ebitda += reported["ebitda"]
        capex += reported["capex"]
        parts.append("reported")
    leverage = mix.row(f"{year}H2", lambda r: r.leverage)
    return dict(year=year, revenue=_r(revenue, 1), ebitda=_r(ebitda, 2),
                margin=_r(ebitda / revenue, 5) if revenue else None,
                capex_pct=_r(capex / revenue, 5) if revenue else None,
                leverage_end=_r(leverage, 3), halves=parts)


def guidance_state(A: dict, release) -> dict | None:
    """Гайденс года (`facts.guidance`, решение ведущего C20) против модели.

    1П года — отчёт (`facts.reported`, периметр отчёта, как у гайденса); 2П —
    ожидание сетки. Требуемая маржа 2П — (g·(R₁ + R₂) − E₁)/R₂ при выручке 2П
    модели (`indicators.quarterly.guidance_half_margin`). Маржа года каждой
    клетки — правило гейта (`model.checks.guidance_year_parts`): доля сетки ниже
    гайденса — масса гейта `guidance_gap`. Гайденса нет или год вне горизонта —
    None.
    """
    from indicators.quarterly import guidance_half_margin
    from model.checks import guidance_year_parts

    spec = (A.get("facts") or {}).get("guidance")
    if not spec:
        return None
    year = str(spec["period"])
    reported = A["facts"].get("reported") or {}
    h1, h2 = f"{year}H1", f"{year}H2"
    h1_revenue = (reported.get("revenue") or {}).get(h1)
    h1_ebitda = (reported.get("ebitda_pre16") or {}).get(h1)
    grid = [(c.probability, c.result) for c in release.cells]
    total = math.fsum(p for p, _ in grid) or 1.0
    rows2 = [(p, next((r for r in res.rows if r.period == h2), None)) for p, res in grid]
    if h1_revenue is None or h1_ebitda is None or any(r is None for _, r in rows2):
        return None
    h2_revenue = math.fsum(p * r.revenue for p, r in rows2) / total
    h2_ebitda = math.fsum(p * r.ebitda for p, r in rows2) / total
    floor = float(spec["ebitda_margin_min"])
    below = 0.0
    for p, res in grid:
        parts = guidance_year_parts(A, res)
        if parts is not None and parts[1] / parts[0] < floor:
            below += p
    anchor = _facts_file("anchor.json").get(f"guidance_{year}") or {}
    fy_revenue, fy_ebitda = h1_revenue + h2_revenue, h1_ebitda + h2_ebitda
    gate = next((g for g in release.gate_summary if g.key == "guidance_gap"), None)
    mix = _Mix(release, {c.cell.key: c.probability for c in release.cells})
    return dict(
        year=year, fy_margin_min=floor, source=spec.get("source", ""),
        h1=dict(period=h1, revenue=round(h1_revenue, 3), ebitda=round(h1_ebitda, 3),
                margin=round(h1_ebitda / h1_revenue, 5), basis="отчёт (периметр отчёта)"),
        h2=dict(half=h2, revenue_model=round(h2_revenue, 2), ebitda_model=round(h2_ebitda, 3),
                margin_model=round(h2_ebitda / h2_revenue, 5),
                margin_required=round(guidance_half_margin(
                    fy_margin=floor, h1_revenue=h1_revenue, h1_ebitda=h1_ebitda,
                    h2_revenue=h2_revenue), 5)),
        fy_margin_model=round(fy_ebitda / fy_revenue, 5),
        gap_pp=round((fy_ebitda / fy_revenue - floor) * 100, 3),
        share_of_grid_below=round(below / total, 4),
        nd_ebitda_end=dict(guidance=anchor.get("nd_ebitda_end"),
                           model=_r(mix.row(h2, lambda r: r.leverage), 3),
                           source=anchor.get("src")),
        gate=(None if gate is None else dict(
            key=gate.key, cells=gate.cells, mass=round(gate.mass, 4), explained=gate.explained,
            stale=gate.stale, expiring=bool(gate.expiring), advisory=gate.advisory,
            valid_until=(gate.valid_until.isoformat() if hasattr(gate.valid_until, "isoformat")
                         else gate.valid_until))))


def _target_check(target: dict, value) -> bool | None:
    """Выполнена ли цель стратегии величиной `value` (None — сравнивать нечего).

    `min`/`level` — не ниже цели, `max` — не выше, `range` — внутри диапазона
    (где именно — `_target_position`)."""
    if value is None:
        return None
    kind = target.get("kind")
    if kind in ("min", "level"):
        return value >= target["value"]
    if kind == "max":
        return value <= target["value"]
    if kind == "range":
        return target["low"] <= value <= target["high"]
    return None


def _target_position(target: dict, value) -> str | None:
    """Для диапазона — «ниже» / «внутри» / «выше» (ниже 1,0× ЧД/EBITDA — запас, а
    не провал); для прочих — None."""
    if value is None or target.get("kind") != "range":
        return None
    return ("below" if value < target["low"] else "above" if value > target["high"]
            else "inside")


def _strategy_block(A: dict, release, mixes: dict) -> dict:
    """Стратегия-2028 и гайденс 2026 против пути модели (D16).

    Цели — `data/facts/strategy.json` (первичка: презентация стратегии, с. 32–33;
    IAS 17). Модель — год целиком: отчётные полугодия + ожидание смеси по
    прогнозным (`_year_view`). История выполнения — отчётные годы окна цели из
    `accounting_base.json` (маржа, capex/выручка, ЧД/EBITDA на конец года);
    валовых открытий по форматам в фактах нет — у целей открытий истории нет.
    Гайденс года — `guidance_state` и состояние гейта `guidance_gap`.
    """
    facts = _facts_file("strategy.json")
    base = _facts_file("accounting_base.json")
    targets = facts.get("targets") or []
    P = periods(A["meta"]["first_period"], A["meta"]["last_period"])
    first_year = int(P[0][:4])
    years = sorted({y for t in targets for y in t.get("years") or [] if y >= first_year})
    anchor = {s["id"]: s for s in _network_anchor(A)["segments"]}
    model = {}
    for name, weights in mixes.items():
        mix = _Mix(release, weights)
        rows = []
        for year in years:
            row = _year_view(A, base, mix, year)
            if not row:
                continue
            openings = {}
            for sid in {t.get("segment") for t in targets if t.get("segment")}:
                size = (anchor[sid]["area_end"] / anchor[sid]["stores_end"]
                        if sid in anchor and anchor[sid]["area_end"] and anchor[sid]["stores_end"]
                        else None)
                area, halves = 0.0, 0
                for half in (1, 2):
                    value = mix.segment(f"{year}H{half}", sid, "opened")
                    if value is not None:
                        area, halves = area + value, halves + 1
                # Открытия отчётного полугодия в фактах по форматам не раскрыты
                # валовыми магазинами — год с отчётным полугодием неполон
                # (`model_halves` < 2).
                openings[sid] = dict(opened_area=_r(area, 2), model_halves=halves,
                                     stores_est=_r(area / size, 0) if size else None)
            row["openings"] = openings
            rows.append(row)
        model[name] = rows
    history = []
    for target in targets:
        for year in target.get("years") or []:
            actual = _history_value(base, target, year)
            if actual is not None:
                history.append(dict(target=target["id"], year=year, value=_r(actual, 5),
                                    met=_target_check(target, actual),
                                    position=_target_position(target, actual)))
    comparisons = []
    for target in targets:
        for name, rows in model.items():
            for row in rows:
                if row["year"] not in (target.get("years") or []):
                    continue
                value = _model_value(target, row)
                comparisons.append(dict(target=target["id"], mix=name, year=row["year"],
                                        value=value, met=_target_check(target, value),
                                        position=_target_position(target, value),
                                        includes_reported="reported" in row["halves"]))
    return dict(
        source=facts.get("source"), basis=facts.get("basis"), targets=targets,
        years=years, model=model, comparisons=comparisons, history=history,
        guidance=guidance_state(A, release),
        notes=dict(
            model="год целиком: отчётные полугодия IAS 17 + ожидание смеси по прогнозным",
            openings="валовые открытия ядра, тыс. м²; магазины — по средней площади магазина "
                     "сегмента на якоре (площади нового магазина книга не задаёт)",
            revenue="цель выручки включает будущие сделки; модель их не содержит (D3)"))


def _history_value(base: dict, target: dict, year: int):
    """Отчётное значение цели за год (IAS 17, `accounting_base.json`); нет — None."""
    from model.checks import CAPEX_CASH_LINES

    row = ((base.get("fy") or {}).get(f"FY{year}") or {}).get("ias17") or {}
    pl, cf = row.get("pl") or {}, row.get("cf") or {}
    revenue = (pl.get("revenue") or {}).get("v")
    kind = target["id"]
    if kind.startswith("margin") and revenue:
        ebitda = (pl.get("ebitda") or {}).get("v")
        return None if ebitda is None else ebitda / revenue
    if kind == "capex_pct" and revenue:
        return -math.fsum((cf.get(n) or {}).get("v") or 0.0 for n in CAPEX_CASH_LINES) / revenue
    if kind == "nd_ebitda":
        debt = (((base.get("debt_sheet") or {}).get("ias17") or {}).get(f"{year}-12-31") or {})
        return (debt.get("nd_ebitda") or {}).get("v")
    return None


def _model_value(target: dict, row: dict):
    kind = target["id"]
    if kind.startswith("margin"):
        return row.get("margin")
    if kind == "capex_pct":
        return row.get("capex_pct")
    if kind == "nd_ebitda":
        return row.get("leverage_end")
    if kind.startswith("revenue"):
        return row.get("revenue")
    if kind.startswith("openings"):
        return ((row.get("openings") or {}).get(target.get("segment")) or {}).get("stores_est")
    return None


def _ladder_rung(ladder: list[dict], leverage: float) -> int | None:
    """Ступень лестницы по правилу ядра: первая с λ < max_leverage (null — без границы)."""
    for number, rung in enumerate(ladder):
        if rung.get("max_leverage") is None or leverage < rung["max_leverage"]:
            return number
    return None


def _ru(value: float) -> str:
    """Число для подписи — с десятичной запятой («1,5×», как на витрине)."""
    return f"{value:g}".replace(".", ",")


def _rung_title(ladder: list[dict], number: int) -> str:
    rung = ladder[number]
    low = ladder[number - 1]["max_leverage"] if number else None
    high = rung.get("max_leverage")
    span = (f"ниже {_ru(high)}×" if low is None
            else f"от {_ru(low)}× до {_ru(high)}×" if high is not None else f"от {_ru(low)}×")
    cap = ("без потолка (весь запас до целевого рычага и FCF)" if rung.get("payout_max") is None
           else f"не больше {_ru(rung['payout_max'] * 100)} % FCF")
    return f"ЧД/EBITDA {span}: {cap}"


def _dividends_block(A: dict, release, mixes: dict) -> dict:
    """Дивидендная лестница (D12, A-F6): ступени, текущая ступень, DPS и ЧД/EBITDA по годам.

    Ступени — `financing.dividend_ladder` книги (положение 2021 года; правило
    ядра «платить верх ступени»). Текущая ступень — по отчётному ЧД/EBITDA LTM
    якоря (база положения — IAS 17, отчётная EBITDA) и, рядом, по канону
    проформы. По годам — ожидание смеси: DPS = дивиденды года / акции в
    обращении, ЧД/EBITDA на конец года (`CellResult.annual`), доля смеси с
    выплатой; и те же ряды именованных сценариев.
    """
    FN, F = A["financing"], A["facts"]
    ladder = [dict(r) for r in FN.get("dividend_ladder") or []]
    shares = F["shares_out_mln"]
    anchor = F["anchor"]
    lev_reported = anchor["net_debt"] / anchor["ebitda_ltm_reported"]
    lev_canon = anchor["net_debt"] / anchor["ebitda_ltm"]
    rung_now = _ladder_rung(ladder, lev_reported) if ladder else None
    P = periods(A["meta"]["first_period"], A["meta"]["last_period"])
    years = sorted({int(p[:4]) for p in P})
    by_year = {}
    for name, weights in mixes.items():
        mix = _Mix(release, weights)
        rows = []
        for year in years:
            dividends = mix.year(year, lambda a: a["dividends"])
            rows.append(dict(
                year=year, dividends=_r(dividends, 2),
                dps=_r(dividends * 1000.0 / shares, 1) if dividends is not None else None,
                leverage_end=_r(mix.year(year, lambda a: a["leverage"]), 3),
                share_paying=round(mix.mass(lambda res, _rows, y=year: any(
                    a["year"] == y and a["dividends"] > 1e-9 for a in res.annual())), 4)))
        by_year[name] = rows
    scenarios = {name: [dict(year=a["year"], dividends=round(a["dividends"], 2),
                             dps=round(a["dividends"] * 1000.0 / shares, 1),
                             leverage_end=round(a["leverage"], 3)) for a in result.annual()]
                 for name, result in release.named.items()}
    return dict(
        ladder=[dict(rung=i, max_leverage=r.get("max_leverage"), payout_max=r.get("payout_max"),
                     title=_rung_title(ladder, i)) for i, r in enumerate(ladder)],
        leverage_target=FN.get("leverage_target"), dividends_from_year=FN.get("dividends_from_year"),
        basis="ЧД/EBITDA IAS 17 (положение о дивидендной политике 2021 г.; стратегия-2028, с. 34)",
        current=dict(as_of=A["meta"]["facts_date"], net_debt=anchor["net_debt"],
                     ebitda_ltm_reported=anchor["ebitda_ltm_reported"],
                     leverage_reported=round(lev_reported, 4),
                     rung=rung_now, rung_title=(_rung_title(ladder, rung_now)
                                                if rung_now is not None else None),
                     ebitda_ltm_canon=anchor["ebitda_ltm"], leverage_canon=round(lev_canon, 4),
                     rung_canon=_ladder_rung(ladder, lev_canon) if ladder else None),
        shares_mln=shares, by_year=by_year, scenarios=scenarios,
        notes=dict(dps="₽ на акцию в обращении; год выплаты — год строки ядра",
                   share_paying="доля смеси клеток, в которых за год выплачены дивиденды"))


def _governance_block(A: dict, judgements: list[dict] | None = None) -> dict:
    """Разложение дисконта за управление (D5) и строка «g на охвате 850oa» (E31).

    Каналы — `valuation.governance_components` книги со знаком и основанием;
    сумма подписанных каналов равна `valuation.governance_discount` (проверку
    держит `load_book`). «g на охвате 850oa» — сумма каналов, входящих в охват
    шкалы 850oa (`in_850oa_scope`; у «Ленты» без канала будущих сделок), —
    чтобы шкала g читалась одинаково у двух моделей. Цена ошибки g — строка
    таблицы суждений `valuation.governance_discount` (полная сборка).
    """
    from model.book import governance_components, governance_on_850oa_scope

    parts = governance_components(A) or []
    total = float(A["valuation"].get("governance_discount", 0.0))
    row = next((j for j in judgements or [] if j["key"] == "valuation.governance_discount"), None)
    return dict(
        discount=total,
        components=[dict(name=p["name"], value=p["value"], sign=p["sign"],
                         signed=p["sign"] * p["value"], basis=p.get("basis"),
                         in_850oa_scope=p["in_850oa_scope"]) for p in parts],
        sum_signed=round(math.fsum(p["sign"] * p["value"] for p in parts), 9) + 0.0,
        on_850oa_scope=governance_on_850oa_scope(A),
        on_850oa_scope_title="g на охвате 850oa (без каналов вне охвата шкалы 850oa)",
        out_of_850oa_scope=[p["name"] for p in parts if not p["in_850oa_scope"]],
        price_sensitivity=(None if row is None else dict(
            low=row["low_label"], high=row["high_label"],
            price_low=row["price_low"], price_high=row["price_high"])))


def _bases_block(A: dict) -> dict:
    """Сверка баз МСФО 16 ↔ до МСФО 16 для долга и EBITDA (D1) — из фактов.

    Модель считает на IAS 17: аренда — расход в EBITDA, обязательство по аренде
    в долг не входит. «Лента» раскрывает обе базы (`accounting_base.json`: лист
    Debt датабука, P&L обеих баз), выпуск печатает их рядом и разницу называет
    словами — обязательства по аренде и расход аренды, а не пересчитывает.
    """
    base = _facts_file("accounting_base.json")
    bridge = _facts_file("bridge_balance.json")
    sheet = base.get("debt_sheet") or {}
    ias, ifrs = sheet.get("ias17") or {}, sheet.get("ifrs16") or {}

    def v(block: dict, key: str):
        return (block.get(key) or {}).get("v")

    fy = base.get("fy") or {}

    def fy_ebitda(year: int, basis: str):
        row = ((fy.get(f"FY{year}") or {}).get(basis) or {}).get("pl") or {}
        return v(row, "ebitda")

    debt = []
    for day in sorted(set(ias) & set(ifrs)):
        a, b = ias[day], ifrs[day]
        year = int(day[:4])
        e17, e16 = (fy_ebitda(year, "ias17"), fy_ebitda(year, "ifrs16")) if day.endswith("12-31") \
            else (None, None)
        debt.append(dict(date=day,
                         ias17=dict(total_debt=v(a, "total_debt"), cash=v(a, "cash"),
                                    net_debt=v(a, "net_debt"), nd_ebitda=v(a, "nd_ebitda")),
                         ifrs16=dict(total_debt=v(b, "total_debt"),
                                     lease_liabilities=v(b, "lease_liabilities"),
                                     net_debt=v(b, "net_debt"),
                                     nd_ebitda=(_r(v(b, "net_debt") / e16, 3) if e16 else None)),
                         lease_gap=_r(v(b, "net_debt") - v(a, "net_debt"), 6)))
    ebitda = []
    for period in sorted(fy):
        row17 = (fy[period].get("ias17") or {}).get("pl") or {}
        row16 = (fy[period].get("ifrs16") or {}).get("pl") or {}
        if v(row16, "ebitda") is None:
            continue
        ebitda.append(dict(period=period, revenue=v(row17, "revenue"),
                           ias17=v(row17, "ebitda"), ifrs16=v(row16, "ebitda"),
                           lease_expense_ias17=v(row17, "lease_expense"),
                           gap=_r(v(row16, "ebitda") - v(row17, "ebitda"), 6)))
    F = A["facts"]
    anchor_day = A["meta"]["facts_date"]
    halves = base.get("halves") or {}

    def half_ebitda(period: str, basis: str):
        return v(((halves.get(period) or {}).get(basis) or {}).get("pl") or {}, "ebitda")

    anchor_period = F["anchor"]["period"]
    year = int(anchor_period[:4])
    ltm16 = None
    if anchor_period.endswith("H1"):
        parts = (fy_ebitda(year - 1, "ifrs16"), half_ebitda(f"{year - 1}H1", "ifrs16"),
                 half_ebitda(anchor_period, "ifrs16"))
        if all(p is not None for p in parts):
            ltm16 = parts[0] - parts[1] + parts[2]
    lease = (bridge.get("lease_liabilities") or {}).get("total")
    # ЧД/EBITDA МСФО 16 на дату якоря — на EBITDA LTM той же базы (в листе Debt
    # датабука отношение по МСФО 16 не раскрыто).
    for row in debt:
        if row["date"] == anchor_day and row["ifrs16"]["nd_ebitda"] is None and ltm16:
            row["ifrs16"]["nd_ebitda"] = _r(row["ifrs16"]["net_debt"] / ltm16, 3)
    return dict(
        model_basis="IAS 17 (до МСФО 16): аренда — расход в EBITDA, обязательство по аренде в "
                    "долг не входит (D1)",
        anchor=dict(date=anchor_day, net_debt_ias17=F["anchor"]["net_debt"],
                    lease_liabilities=lease,
                    net_debt_ifrs16=v(ifrs.get(anchor_day) or {}, "net_debt"),
                    ebitda_ltm_ias17_reported=F["anchor"]["ebitda_ltm_reported"],
                    ebitda_ltm_ias17_pro_forma=F["anchor"]["ebitda_ltm"],
                    ebitda_ltm_ifrs16=_r(ltm16, 6)),
        debt=debt, ebitda=ebitda,
        source=dict(file="accounting_base.json", document=(base.get("source_doc") or {}).get("id"),
                    lines="Debt (IAS 17 и IFRS 16), PL обеих баз; аренда — bridge_balance.json"))


def release_gates(release: Release, slow: dict | None) -> tuple[list, list]:
    """Находки и сводка гейтов выпуска с гейтами, которых нет в `run_release`.

    `limited_liability` (совещательный, `model.checks.check_limited_liability`)
    считается по прогонам полосы — её строят медленные блоки, а не
    `run_release`, поэтому гейт добавляется здесь (без полосы его нет).
    `security_change` (совещательный, `model.checks.check_security_card`) —
    по карточке акции из живых входов (`release.live.security`).
    """
    findings, summary = list(release.findings), list(release.gate_summary)
    extra = check_limited_liability(release.book, slow["uncertainty"]) if slow else []
    # Карточка акции сменилась после даты книги (D15): совещательный гейт
    # «пересмотреть g» — по наблюдению живых входов, без расчёта ядра.
    extra += check_security_card(release.live)
    if extra:
        findings += extra
        summary = summarize_gates(
            findings, {c.cell.key: c.probability for c in release.cells} | {RELEASE_LABEL: 1.0})
    return findings, summary


def _book_only_live(A: dict) -> dict:
    """Блок `live` выпуска на книжных входах (`--book-only`, тесты): те же поля,
    что у живого, с одной причиной деградации — витрина рисует плашку, а не пустоту."""
    from model.live import LiveReport

    report = LiveReport(degraded=["выпуск собран на книжных входах"])
    report.book_date = A["meta"]["valuation_date"]
    report.price_reference = float(A["market"]["price"])
    report.price_reference_date = A["meta"]["valuation_date"]
    return report.as_dict()


def build_payload(release: Release | None = None, *, with_slow: bool = True) -> dict:
    """Собирает весь payload. `with_slow=False` пропускает полосу
    неопределённости (печатаемый заголовок), обратный DCF и таблицу суждений —
    они считают сетку сотни и тысячи раз (`slow_blocks`)."""
    release = release or run_release()
    A = release.book
    fv = release.fair_value
    periods_closed = time_position(A)[0]
    cell_weights = layer_cell_weights(release)
    slow = slow_blocks(A) if with_slow else None
    headline = headline_payload(A, fv, slow["uncertainty"]) if slow else None
    findings, gate_summary = release_gates(release, slow)
    mixes = cell_mixes(release, cell_weights)
    peers = peers_block(A)

    payload = dict(
        schema=SCHEMA,
        meta=dict(
            generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            valuation_date=A["meta"]["valuation_date"],
            facts_date=A["meta"]["facts_date"],
            book_version=str(A["meta"]["version"]),
            # Коммит КОДА. Книга и факты свои версии называли, код — нет, и
            # правка ядра печаталась как «прочее» (аудит третьей итерации, A5).
            engine_commit=engine_commit(),
            horizon=[A["meta"]["first_period"], A["meta"]["last_period"]],
            basis="до МСФО 16",
            # Где стоит дата оценки на прогнозной сетке. Пока книга свежая,
            # закрытых периодов ноль; как только первый прогнозный период
            # кончился, а миры книги не обновили, это надо ВИДЕТЬ: оценка
            # считается на старых мирах, и панель обязана об этом сказать.
            periods_closed=periods_closed,
            open_period=open_period(A),
            book_first_period_closed=bool(periods_closed),
            shares_mln=A["facts"]["shares_out_mln"],
            governance_discount=A["valuation"]["governance_discount"],
            # Компания и единица периода — из книги (`meta.company`,
            # `meta.period_unit`): витрина не держит литералов эмитента, а
            # подписи «полугодие/квартал» берёт из выпуска (DESIGN §1:
            # модель — `half`, нау-каст — `nowcast.period_unit`).
            company=dict(A["meta"]["company"]),
            period_unit=A["meta"]["period_unit"],
            first_period=A["meta"]["first_period"],
        ),
        market=dict(
            price=A["market"]["price"],
            price_date=A["market"].get("price_date"),
            price_convention=A["market"].get("price_convention"),
            cap=round(A["market"]["price"] * A["facts"]["shares_out_mln"] / 1000, 1),
            # Аналоги — только живые на одной базе плюс строка эмитента на
            # проформенной EBITDA LTM (D16); константы книги сняты.
            peers_same_base=peers,
            # Агрегаты целей и сам список домов (книга: `sellside_summary` и
            # `sellside_targets`, решение ведущего E32) — одним блоком витрины.
            sellside=(dict(A["market"]["sellside_summary"], houses=A["market"]["sellside_targets"])
                      if A["market"].get("sellside_summary") else None),
        ),
        fair_value=dict(
            method=fv.method,
            # Печатаемый заголовок — медиана распределения центра
            # по суждениям книги и полосы 80 % / 50 % (`headline`); первая
            # строка — EV против рыночного V* (`ev_first_line`); строка
            # «взгляд на ставки» (`rates_view`). Поля ниже
            # (`low`/`central`/`high`) — ТОЧКА при центральных значениях всех
            # суждений и её диапазон по λ.
            headline=headline,
            ev_first_line=ev_first_line(release, headline, slow["uncertainty"] if slow else None),
            rates_view=rates_view_block(fv),
            low=round(fv.low, 0), central=round(fv.central, 0), high=round(fv.high, 0),
            # Пороги скачка заголовка книги (`valuation.headline.jump_guard`).
            jump_guard=jump_guard(A),
            printed_low=fv.printed_low, printed_central=fv.printed_central,
            printed_high=fv.printed_high,
            own_macro_confidence=fv.own_macro_confidence,
            grid_median=round(fv.grid_median, 0),
            modal_world=fv.modal_world,
            modal_world_price=round(fv.modal_world_price, 0),
            market=fv.market,
            market_percentile=round(fv.market_percentile, 3),
            p_equity_nonpositive=round(fv.p_equity_nonpositive, 3),
            by_world={w: round(p, 0) for w, p in release.world_prices().items()},
            ev_model=round(fv.ev_model, 1),
            ev_market_implied=round(fv.ev_market_implied, 1),
            ev_gap=round(fv.ev_gap, 4),
            rub_per_ev_percent=round(fv.rub_per_ev_percent, 1),
            # Перцентили СЕТКИ — для графика распределения исходов. Диапазон
            # сам по себе распределение не покрывает: это две оценки вдоль
            # оси «чей макро-взгляд», и внутри них лежит около 12 % массы.
            p10=round(release.layers["analytical"].p10, 0),
            p90=round(release.layers["analytical"].p90, 0),
            ev_comparison=_ev_comparison(release),
        ),
        layers={name: dict(title=layer.title,
                           # Веса клеток слоя — для сверки с контрольной
                           # моделью (B3(1)). `cell_weights_gap` — насколько
                           # пересобранный из этих весов слой расходится с
                           # ядром; `validate` требует ноль.
                           cell_weights=_round(cell_weights[name]["weights"], 8),
                           cell_weights_gap=cell_weights[name]["gap"],
                           v0=round(layer.v0, 1), claims=round(layer.claims, 1),
                           # Внутренняя стоимость без пола и заголовок слоя
                           # методом книги (D4: max(V0 − D, 0)·(1 − g)/акции).
                           # «Старый метод» 850oa (среднее цен клеток с полом)
                           # и вменённые потери кредиторов сняты вместе со
                           # структурным путём.
                           intrinsic=round(layer.intrinsic, 0),
                           headline=round(layer.headline, 0),
                           p10=round(layer.p10, 0), p25=round(layer.p25, 0),
                           p50=round(layer.p50, 0), p75=round(layer.p75, 0),
                           p90=round(layer.p90, 0),
                           p_equity_nonpositive=round(layer.p_equity_nonpositive, 3),
                           p_above_market=round(layer.p_above_market, 3))
                for name, layer in release.layers.items()},
        scenarios=[_scenario(name, result, release.named_weights[name], A)
                   for name, result in release.named.items()],
        grid=_grid_block(release.cells, credit_limit(A)),
        variance=dict(price=_round(release.variance_price, 4),
                      ev=_round(release.variance_ev, 4)),
        regime_prob=dict(
            unconditional=_round(release.regime_unconditional, 4),
            given_world=_round(release.regime_given_world, 4),
            prior=_round(A["joint"]["regime_given_world"], 4),
            titles=REGIME_TITLES,
            sigma_pp=A["joint"]["regime_update"]["sigma_pp"],
            observations=A["joint"]["regime_update"].get("observations") or {},
            reference_class=_soft(reference_class_block, A, release.regime_unconditional),
        ),
        next_report_value=[
            dict(margin=round(r["margin"], 4),
                 probabilities=_round(r["probabilities"], 4),
                 low=round(r["low"], 0), central=round(r["central"], 0),
                 high=round(r["high"], 0),
                 p_equity_nonpositive=round(r["p_equity_nonpositive"], 3))
            for r in release.next_report],
        worlds={name: dict(title=w["name"],
                           lt_inflation=w["lt"]["inflation"],
                           zero_curve=_round({str(k): v for k, v in w["zero_curve"].items()}, 5),
                           probability=A["joint"]["world_prob"][name],
                           probability_market=A["joint"]["world_prob_market_implied"][name])
                for name, w in A["worlds"].items()},
        debt=_debt_block(A),
        history=_history_block(),
        indicators=_indicators_block(),
        nowcast=_nowcast_block(A, release),
        calendar=_calendar_block(A),
        assumptions=[],
        checks=_checks_block(findings, peers),
        # Блоки «Ленты» (D16): сеть по форматам, периметр и интеграция покупок,
        # стратегия-2028 и гайденс, дивидендная лестница, разложение дисконта за
        # управление, сверка баз МСФО 16 ↔ до МСФО 16.
        network=_network_block(A, release, mixes),
        perimeter=_perimeter_block(A, release, mixes),
        strategy=_strategy_block(A, release, mixes),
        dividends=_dividends_block(A, release, mixes),
        governance=_governance_block(A),
        bases=_bases_block(A),
        mixes=_mix_meta(release, mixes),
        inputs=inputs_snapshot(A),
        changes={},
        live=(release.live.as_dict() if release.live is not None
              else _book_only_live(A)),
        gates=[dict(key=g.key, cells=g.cells, mass=round(g.mass, 4),
                    explained=g.explained, stale=g.stale,
                    # `pending` — гейт сработал, а текст объяснения ещё пишет
                    # аудитор; `mass_expected`/`mass_off` — сверка массы с
                    # объяснением на ЖИВЫХ входах, а не только в тесте.
                    pending=g.pending, mass_expected=g.expected_mass,
                    mass_off=g.mass_mismatch,
                    # Текст автора книги — целиком: витрина раскрывает его по
                    # щелчку, а обрезка по числу знаков рвала его на полуслове.
                    explanation=g.explanation,
                    # Срок объяснения (действует включительно) и «истекает в
                    # ближайшие 30 дней» — то, о чём такт предупреждает
                    # тревогой, теперь видно и на экране.
                    valid_until=(g.valid_until.isoformat()
                                 if hasattr(g.valid_until, "isoformat")
                                 else (str(g.valid_until) if g.valid_until else None)),
                    expiring=bool(g.expiring),
                    # Фактическая причина срабатывания гейта уровня выпуска
                    # (`book_update`: сдвиг кривой или возраст книги). Без неё
                    # плашка печатала запасную фразу о кривой, когда причиной
                    # был возраст (аудит 24.09.2026, G1-exam §1.3 п. 5).
                    message=g.message[:600])
               for g in gate_summary],
    )

    if slow:
        payload["reverse_dcf"] = reverse_dcf_block(A, slow["reverse_dcf"])
        payload["judgements"] = judgement_rows(slow["judgements"])
        payload["assumptions"] = _assumptions_block(A, payload["judgements"])
        payload["governance"] = _governance_block(A, payload["judgements"])
        payload["next_report_neutral"] = next_report_neutral_block(
            release, slow.get("neutral"), slow.get("next_report"))
        # Книга 1.5: медиана (и её ось ставок) при каждом факте маржи — рядом
        # с точкой строки (`next_report_median`).
        for row, med in zip(payload["next_report_value"], slow.get("next_report") or []):
            row.update(median=round(med["central"], 0), median_low=round(med["low"], 0),
                       median_high=round(med["high"], 0))
        if headline:
            link_contributions(headline["contributions"], payload["judgements"])
    else:
        payload["reverse_dcf"], payload["judgements"] = {}, []
        payload["next_report_neutral"] = {}
    payload["fair_value"]["judgement_spread"] = judgement_spread(payload["judgements"])

    # Хэш считается БЕЗ `generated_at` и БЕЗ блока `changes` — и порядок здесь
    # не случаен.
    #
    # `generated_at`: иначе повторный прогон того же дня на тех же данных даёт
    # другой хэш, публикация кладёт в бакет новый объект, а журнал получает
    # дубль записи. Момент сборки — метаданные о прогоне, а не о содержании.
    #
    # `changes`: блок ссылается на ПРОШЛЫЙ выпуск, то есть содержит хэш чужого
    # содержания. Пока он входил в хэш, три сборки на одинаковых входах давали
    # три разных релиза (аудит второй итерации, D1): каждая следующая
    # отличалась от предыдущей ровно тем, что ссылалась на неё. Поэтому
    # `changes` заполняется ПОСЛЕ подсчёта хэша, сравнением с последним
    # выпуском ДРУГОГО содержания.
    payload["meta"]["payload_sha256"] = content_digest(payload)
    payload["changes"] = _changes_block(A, release,
                                        own_digest=payload["meta"]["payload_sha256"])
    payload["meta"]["bytes"] = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    return payload


# Поля, которые в содержание выпуска не входят: момент сборки, собственный
# хэш, размер и блок «что изменилось» (он про ПРОШЛЫЙ выпуск, не про этот).
NOT_CONTENT_META = ("generated_at", "payload_sha256", "bytes")


def content_digest(payload: dict) -> str:
    """sha256 СОДЕРЖАНИЯ выпуска. Одни входы — один хэш, сколько ни пересобирай."""
    body = json.dumps(
        {k: v for k, v in payload.items() if k not in ("meta", "changes")}
        | {"meta": {k: v for k, v in payload["meta"].items() if k not in NOT_CONTENT_META}},
        ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


# Контракт с фронтом. Тест test_payload::test_frontend_reads_only_declared_blocks
# следит, что здесь перечислено ВСЁ, что читает web/app.js: иначе исчезнувший
# блок пройдёт валидацию и упадёт уже в браузере у владельца.
REQUIRED_TOP_LEVEL = (
    "schema", "meta", "market", "fair_value", "layers", "scenarios", "grid",
    "variance", "regime_prob", "next_report_value", "next_report_neutral",
    "reverse_dcf", "judgements",
    "nowcast", "indicators", "debt", "history", "checks", "changes", "worlds",
    "live", "gates", "inputs", "calendar", "assumptions",
    # Блоки «Ленты» (D16) и смеси клеток, по которым они печатают ожидания.
    "network", "perimeter", "strategy", "dividends", "governance", "bases", "mixes",
)

# Поля, без которых блок — не тот блок (путь блока через точку → поля). Проверка
# терпима к НОВЫМ полям и нетерпима к пропавшим: витрина читает поле по имени, а
# `undefined` в JS рисуется пустотой, а не ошибкой. Блоки полной сборки
# (`reverse_dcf`, `judgements`, `assumptions`, `next_report_neutral`,
# `fair_value.headline`) в быстрой сборке пусты по построению — их сверяют
# проверки полосы и диагностик медианы. Полный список полей для витрины —
# отчёт этапа P4b (`reports/P4b-release.md`).
REQUIRED_FIELDS = {
    "meta": ("generated_at", "valuation_date", "facts_date", "book_version", "engine_commit",
             "horizon", "basis", "periods_closed", "open_period", "book_first_period_closed",
             "shares_mln", "governance_discount", "company", "period_unit", "first_period",
             "payload_sha256", "bytes"),
    "meta.company": ("name", "ticker"),
    "market": ("price", "price_date", "cap", "peers_same_base", "sellside"),
    "market.peers_same_base": ("as_of", "basis", "subject_key", "rows"),
    "fair_value": ("method", "headline", "ev_first_line", "rates_view", "low", "central", "high",
                   "jump_guard", "printed_low", "printed_central", "printed_high",
                   "own_macro_confidence", "market", "p_equity_nonpositive", "by_world",
                   "ev_model", "ev_market_implied", "ev_gap", "rub_per_ev_percent",
                   "ev_comparison", "judgement_spread"),
    "fair_value.ev_first_line": ("v0", "v_star", "gap", "rub_per_1pct_ev", "ebitda_ltm",
                                 "ev_ebitda", "ev_ebitda_v_star", "layers", "cap_plus_d",
                                 "by_lambda"),
    "debt": ("net_debt_reported", "cash_reported", "undrawn_facilities", "floating_share_book",
             "cash_yield_k", "recent_events", "register") + REGISTER_FIELDS,
    "nowcast": ("period_unit", "target", "target_title", "target_titles", "target_quarter",
                "margin", "implied_half", "guidance", "admission", "retro", "interest",
                "journal", "scoreboard", "naive"),
    "nowcast.margin": ("period", "value", "std_error", "expectation", "expectation_se",
                       "deviation", "half", "half_value", "half_std_error", "revenue",
                       "equation", "version", "connected_to_price", "components"),
    "nowcast.implied_half": ("half", "quarter", "second_quarter", "quarter_expectation",
                             "second_quarter_expectation", "quarter_offset", "fact", "table",
                             "rule", "second_quarter_reports"),
    "network": ("units", "periods", "lt_period", "anchor", "expected", "notes"),
    "network.anchor": ("period", "facts_date", "segments", "total"),
    "network.expected": ("grid", "headline"),
    "perimeter": ("deals", "breaks_halves", "breaks_journal", "goodwill",
                  "acquisition_cash_1h2026", "integration", "notes"),
    "perimeter.integration": ("okey", "domlenta", "integration_capex_book", "acquired_nol",
                              "expected"),
    "strategy": ("source", "basis", "targets", "years", "model", "comparisons", "history",
                 "guidance", "notes"),
    "dividends": ("ladder", "leverage_target", "dividends_from_year", "basis", "current",
                  "shares_mln", "by_year", "scenarios", "notes"),
    "dividends.current": ("as_of", "net_debt", "ebitda_ltm_reported", "leverage_reported",
                          "rung", "rung_title", "ebitda_ltm_canon", "leverage_canon",
                          "rung_canon"),
    "governance": ("discount", "components", "sum_signed", "on_850oa_scope",
                   "on_850oa_scope_title", "out_of_850oa_scope", "price_sensitivity"),
    "bases": ("model_basis", "anchor", "debt", "ebitda", "source"),
    "bases.anchor": ("date", "net_debt_ias17", "lease_liabilities", "net_debt_ifrs16",
                     "ebitda_ltm_ias17_reported", "ebitda_ltm_ias17_pro_forma",
                     "ebitda_ltm_ifrs16"),
    "mixes": ("grid", "headline"),
    "mixes.headline": ("title", "cells", "v0", "lambda"),
    "checks": ("invariants_broken", "gates", "corridors", "control_model"),
    "live": ("applied", "degraded", "degraded_flag", "book_date", "book_age_days",
             "price_reference", "price_reference_date", "security", "observed_curve",
             "observed_curve_date"),
}

# Поля структурного отображения 850oa (колл Мертона, страйк K′, калибровка σ,
# «старый метод») и блоки только «Магнита» — в выпуске «Ленты» их нет (D4, D16;
# P2 §5.1). Вернувшееся поле — отказ сборки: витрина снова нарисовала бы шаг
# «Отображение» или плашку σ, которых у модели нет.
FORBIDDEN_FIELDS = {
    "fair_value": ("sigma_live", "sigma_ev", "horizon_years", "strike", "credit_put"),
    "layers.*": ("structural", "strike", "strike_effective", "credit_put", "credit_adjustment",
                 "implied_creditor_loss", "implied_creditor_loss_old", "old_method",
                 "mean_without_floor", "mean"),
    "scenarios[]": ("price_option", "price_structural"),
    "grid[]": ("price_structural",),
    "market": ("peers",),
    "market.peers_same_base": ("book_constants", "book_constants_note"),
    "debt": ("registry_check",),
    "nowcast.margin": ("modal_target", "modal_regime", "modal_title"),
    "checks": ("explained",),
}


def _node(payload: dict, dotted: str):
    node = payload
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _field_problems(payload: dict) -> list[str]:
    """Обязательные поля блоков есть, поля снятого структурного пути — нет."""
    problems = []
    for dotted, fields in REQUIRED_FIELDS.items():
        node = _node(payload, dotted)
        if node is None:
            if dotted.split(".")[0] in payload and "." in dotted:
                problems.append(f"{dotted}: нет подблока")
            continue
        if not isinstance(node, dict):
            problems.append(f"{dotted}: не словарь ({type(node).__name__})")
            continue
        missing = [f for f in fields if f not in node]
        if missing:
            problems.append(f"{dotted}: нет полей {missing}")
    for dotted, fields in FORBIDDEN_FIELDS.items():
        if dotted.endswith("[]"):
            nodes = [n for n in payload.get(dotted[:-2]) or [] if isinstance(n, dict)]
        elif dotted.endswith(".*"):
            nodes = [n for n in (_node(payload, dotted[:-2]) or {}).values() if isinstance(n, dict)]
        else:
            node = _node(payload, dotted)
            nodes = [node] if isinstance(node, dict) else []
        found = sorted({f for n in nodes for f in fields if f in n})
        if found:
            problems.append(f"{dotted}: поля снятого структурного пути или «Магнита» {found}")
    return problems


# Гигиена ВЫПУСКА (DESIGN §1): выпуск публичен через `/api/model`, и почта,
# телефон, IP-адрес или путь файловой системы внутри него — утечка. Сборщики и
# ядро пишут в выпуск тексты (заметки рядов, причины деградации, объяснения);
# любой из них может однажды принести адрес. Отказ — код 1 сборки: прежний
# выпуск остаётся на витрине. Правила — те же, что у теста дерева
# (`tests/test_public_hygiene.py`), и разрешения те же: петля, документационные
# сети RFC 5737, зарезервированные домены RFC 2606.
_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
HYGIENE_RULES = {
    "почта": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"),
    "телефон": re.compile(r"\+7[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}(?!\d)|"
                          r"(?<![\d.])8[\s-]?\(\d{3}\)[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}(?!\d)"),
    "IPv4": re.compile(rf"(?<![\w.]){_OCTET}\.{_OCTET}\.{_OCTET}\.{_OCTET}(?![\w.])"),
    "путь": re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]|(?<![\w.:/])/(?:home|root|srv|Users)/|"
                       r"(?<![\w.:/])/(?:var/lib|usr/local/etc)/"),
}
HYGIENE_ALLOW = {
    "IPv4": re.compile(r"127\.0\.0\.1|0\.0\.0\.0|(?:192\.0\.2|198\.51\.100|203\.0\.113)\.\d+"),
    "почта": re.compile(r"[^@\s]+@(?:[\w-]+\.)*(?:example\.(?:com|org|net)|[\w-]+\.(?:test|invalid|example))"),
}


def _strings(node, path: str = "$"):
    """(путь, строка) каждого ключа и строкового значения выпуска."""
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str):
                yield f"{path}.{key}(ключ)", key
            yield from _strings(value, f"{path}.{key}")
    elif isinstance(node, (list, tuple)):
        for i, value in enumerate(node):
            yield from _strings(value, f"{path}[{i}]")
    elif isinstance(node, str):
        yield path, node


def hygiene_problems(payload: dict) -> list[str]:
    """Почты, телефоны, IPv4 и пути файловой системы в строках выпуска — отказ.

    Совпадение печатается ПУТЁМ в выпуске и видом, без самого текста: строка
    отказа уходит в журнал такта, и повторять в нём утёкший адрес незачем.
    """
    found = []
    for path, text in _strings(payload):
        for rule, pattern in HYGIENE_RULES.items():
            allow = HYGIENE_ALLOW.get(rule)
            for hit in pattern.finditer(text):
                if allow is not None and allow.fullmatch(hit.group(0)):
                    continue
                found.append(f"{path}: {rule}")
                break
    if not found:
        return []
    return [f"гигиена выпуска: {len(found)} совпадений (почта, телефон, IP или путь) — "
            + "; ".join(found[:5])]


def _lenta_problems(payload: dict) -> list[str]:
    """Блоки «Ленты» согласованы между собой и с заголовком — по построению.

    * смеси клеток: V0 «grid» — V0 слоя «свой макро-взгляд», V0 «headline» —
      λ-смесь V0 слоёв (V0 точки); допуски — половины печатаемых знаков;
    * сеть: сумма сегментов — выручка и площадь группы строки;
    * дисконт за управление: сумма подписанных каналов — g книги;
    * базы: ЧД МСФО 16 − ЧД IAS 17 = обязательства по аренде на каждую дату;
    * дивиденды: ступень на якоре — по правилу ядра, DPS — дивиденды на акцию.
    """
    problems = []
    layers_ = payload.get("layers") or {}
    mixes = payload.get("mixes") or {}
    own = (layers_.get("analytical") or {}).get("v0")
    neutral = (layers_.get("macro_neutral") or {}).get("v0")
    slack = _half(1) + _half(2) + 1e-9
    if mixes and own is not None and neutral is not None:
        lam = mixes["headline"]["lambda"]
        if abs(mixes["grid"]["v0"] - own) > slack:
            problems.append(f"смесь grid: V0 {mixes['grid']['v0']} ≠ V0 слоя {own}")
        want = lam * own + (1 - lam) * neutral
        if abs(mixes["headline"]["v0"] - want) > slack:
            problems.append(f"смесь заголовка: V0 {mixes['headline']['v0']} ≠ λ-смесь слоёв {want:.2f}")
    network = payload.get("network") or {}
    for name, block in (network.get("expected") or {}).items():
        for row in (block.get("rows") or []) + [block.get("lt") or {}]:
            segments = (row.get("segments") or {}).values()
            revenue = [s["revenue"] for s in segments if s.get("revenue") is not None]
            total = (row.get("total") or {}).get("revenue")
            if total is not None and abs(math.fsum(revenue) - total) > _half(2) * (len(revenue) + 1) + 1e-9:
                problems.append(f"сеть {name} {row.get('period')}: сумма сегментов "
                                f"{math.fsum(revenue):.2f} ≠ выручка группы {total}")
            area = [s["area_end"] for s in segments if s.get("area_end") is not None]
            total_area = (row.get("total") or {}).get("area_end")
            if total_area is not None and abs(math.fsum(area) - total_area) > _half(1) * (len(area) + 1) + 1e-9:
                problems.append(f"сеть {name} {row.get('period')}: площадь сегментов ≠ площадь группы")
    governance = payload.get("governance") or {}
    if governance.get("components") and abs(governance["sum_signed"] - governance["discount"]) > _half(9) + 1e-12:
        problems.append("дисконт за управление: сумма подписанных каналов ≠ g книги")
    for row in (payload.get("bases") or {}).get("debt") or []:
        lease = (row.get("ifrs16") or {}).get("lease_liabilities")
        if lease is not None and row.get("lease_gap") is not None and abs(row["lease_gap"] - lease) > _half(5):
            problems.append(f"базы {row['date']}: ЧД МСФО 16 − ЧД IAS 17 = {row['lease_gap']} ≠ "
                            f"аренда {lease}")
    dividends = payload.get("dividends") or {}
    current = dividends.get("current") or {}
    ladder = dividends.get("ladder") or []
    if ladder and current:
        want = _ladder_rung(ladder, current["leverage_reported"])
        if current.get("rung") != want:
            problems.append(f"дивиденды: ступень на якоре {current.get('rung')} ≠ правило ядра {want}")
    shares = dividends.get("shares_mln")
    for name, rows in (dividends.get("by_year") or {}).items():
        for row in rows:
            if shares and row.get("dividends") is not None and row.get("dps") is not None:
                if abs(row["dps"] - row["dividends"] * 1000.0 / shares) > _half(1) + _half(2) * 1000.0 / shares + 1e-9:
                    problems.append(f"дивиденды {name} {row['year']}: DPS ≠ дивиденды / акции")
                    break
    return problems


# Коридор печатаемого заголовка — медианы, полосы 80 % и точки. Верхняя граница
# не «на всякий случай»: 5 000 ₽ — втрое дороже рынка «Ленты» на дату книги
# (1 619,5 ₽) и в полтора раза выше верха полосы 80 % первого прогона книги 1.0;
# отдельные клетки сетки выше неё бывают (мир N × «частичная»), напечатанное
# распределение — нет. Всё, что выше, — не оценка, а сломанные единицы.
HEADLINE_CEILING = 5_000.0
# Сдвиг заголовка к прошлому выпуску, требующий письменного объяснения, —
# пороги книги `valuation.headline.jump_guard` {median_pct, v0_pct} (доли
# прошлой медианы и прошлого V0): у рычага Ленты V0 сдвигается слабее цены
# иначе, чем у Магнита, и пороги — суждение книги, а не константа кода.
# Выпуск несёт их в `fair_value.jump_guard`, проверка читает оттуда.


def jump_limits(payload: dict | None = None) -> tuple[float, float]:
    """(median_pct, v0_pct): из `fair_value.jump_guard` выпуска, без него — из книги."""
    guard = ((payload or {}).get("fair_value") or {}).get("jump_guard")
    if not guard:
        guard = jump_guard(book())
    return float(guard["median_pct"]), float(guard["v0_pct"])
# Допуск записки по умолчанию: насколько напечатанный центр может отличаться
# от названного в ней. Пять процентов — это ±47 ₽ при центре 936: автор
# правки знает её эффект с точностью до считанных рублей (он его посчитал
# на копии), а лишний десяток набегает из-за живых входов такта.
NOTE_TOLERANCE_PCT = 5.0


def _read_release_notes(path=None) -> list[dict]:
    """Сырое содержимое `release_notes.yaml`. Нет файла — пусто."""
    from pathlib import Path as _Path

    import yaml

    from model.paths import BOOK_DIR

    path = _Path(path) if path else BOOK_DIR / "release_notes.yaml"
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    notes = data.get("notes") or []
    return [entry for entry in notes if isinstance(entry, dict)]


def note_tolerance(entry: dict) -> float:
    """Допуск записки в долях: `tolerance_pct` или `NOTE_TOLERANCE_PCT`."""
    value = entry.get("tolerance_pct", NOTE_TOLERANCE_PCT)
    try:
        return abs(float(value)) / 100.0
    except (TypeError, ValueError):
        return NOTE_TOLERANCE_PCT / 100.0


def release_notes_problems(path=None, *, max_shift: float | None = None) -> list[str]:
    """Записка без `valid_until` или без `expected_central` — ОШИБКА СБОРКИ.

    Про срок — аудит третьей итерации, D4; про ожидаемый центр — аудит
    четвёртой, пункт 4 раздела «Ответы аудитора».

    Записка разрешает скачок печатаемого заголовка. Без срока она разрешает
    его НАВСЕГДА: правка выкладывается один раз, а разрешение остаётся
    открытым, и следующий скачок — уже настоящая порча входов — пройдёт молча
    под её прикрытием. Просроченная записка считается отсутствующей, и это
    задумано; бессрочная не должна существовать вовсе.

    Почему ошибка сборки, а не молчаливое игнорирование. Игнорировать —
    значит оставить в книге запись, которая выглядит действующей и не
    действует: автор скачка увидит записку, сборка упадёт на скачке, и искать
    он будет не там. Падение на самой записке называет причину сразу.

    Проверка идёт в `validate()` БЕЗУСЛОВНО, а не только когда есть скачок:
    иначе бессрочная записка лежала бы в книге годами и обнаружилась бы ровно
    в тот такт, когда нужна была бы исправной.

    `expected_central` по той же причине. Записка «так и задумано» разрешает
    ЛЮБОЙ скачок: она сообщает, что автор чего-то ждал, но не что именно.
    Правка, двигающая центр на +30 ₽, и порча входов, роняющая его вдесятеро,
    проходят под ней одинаково. Названное число превращает записку из
    разрешения в предсказание, которое такт может проверить.
    """
    problems = []
    notes = _read_release_notes(path)
    if notes and max_shift is None:
        max_shift = jump_limits()[0]
    for index, entry in enumerate(notes, start=1):
        where = (f"записка {index} в data/assumptions/release_notes.yaml "
                 f"(дата {entry.get('date', '—')})")
        central = entry.get("expected_central")
        if central is None:
            problems.append(
                f"{where} без `expected_central`: разрешение на ЛЮБОЙ скачок. "
                "Назовите центр в рублях, которого вы ждёте после правки, — "
                "тогда сборка проверит предсказание, а не поверит на слово")
        elif not isinstance(central, (int, float)) or isinstance(central, bool) \
                or not (0 < float(central) <= HEADLINE_CEILING):
            problems.append(
                f"{where}: `expected_central` = {central!r} — не цена в рублях "
                f"в коридоре 0…{HEADLINE_CEILING:.0f}")
        tolerance = entry.get("tolerance_pct", NOTE_TOLERANCE_PCT)
        try:
            if not (0 < float(tolerance) <= 100 * max_shift):
                raise ValueError
        except (TypeError, ValueError):
            problems.append(
                f"{where}: `tolerance_pct` = {tolerance!r} — нужен процент от 0 "
                f"до {100 * max_shift:.0f} (шире порога записка не "
                "нужна: такой скачок и так не требует объяснения)")
        if not entry.get("valid_until"):
            problems.append(
                f"записка {index} в data/assumptions/release_notes.yaml "
                f"(дата {entry.get('date', '—')}) без `valid_until`: "
                "бессрочное разрешение на скачок заголовка. Поставьте срок — "
                "правка выкладывается один раз, разрешение не должно "
                "оставаться открытым навсегда")
            continue
        try:
            import datetime as _dt

            _dt.date.fromisoformat(str(entry["valid_until"]))
        except ValueError:
            problems.append(
                f"записка {index} в data/assumptions/release_notes.yaml: "
                f"`valid_until` = {entry['valid_until']!r} — не дата ГГГГ-ММ-ДД")
    return problems


def load_release_notes(path=None) -> list[dict]:
    """ДЕЙСТВУЮЩИЕ записки о задуманных скачках заголовка.

    Формат и смысл — как у `gate_explanations.yaml`: скачок разрешён, не
    замеченный скачок — нет. Просроченная записка считается отсутствующей.

    Записка без срока не разрешает ничего: сборка на ней падает
    (`release_notes_problems`), но и здесь она отбрасывается — сторож не
    должен зависеть от того, в каком порядке сработали проверки.
    """
    import datetime as _dt

    today = _dt.date.today()
    notes = []
    for entry in _read_release_notes(path):
        until = entry.get("valid_until")
        if not until:
            continue
        try:
            if _dt.date.fromisoformat(str(until)) < today:
                continue
        except ValueError:
            continue
        notes.append(entry)
    return notes


def _headline_problems(payload: dict, *, notes_path=None) -> list[str]:
    """Заголовок: конечность, коридор и объяснимость скачка.

    Аудит второй итерации, вердикт 3: перед публикацией у заголовка
    проверялся только порядок `low ≤ central ≤ high`. Поэтому сборка
    возвращала успех при заголовке «0–0–0», при цене акции 15,6 ₽ и при
    кривой в процентах вместо долей (диапазон 0–350 ₽ без единого флага).
    Порядок сохраняется и в нуле, и в мусоре. С 26.09.2026 порядок слоёв и
    не требуется: низ и верх — два слоя ставок, и книга допускает их переворот.
    """
    import math

    problems: list[str] = []
    fv = payload.get("fair_value") or {}
    if not fv:
        return ["нет блока fair_value"]

    values = {}
    for name in ("low", "central", "high"):
        value = fv.get(name)
        if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            problems.append(f"заголовок: {name} = {value!r} — не конечное число")
        else:
            values[name] = float(value)
    if len(values) < 3:
        return problems

    low, central, high = values["low"], values["central"], values["high"]
    # Низ и верх — не упорядоченные границы, а два слоя ставок: «рыночные
    # ставки как есть» может оказаться ДОРОЖЕ своего взгляда (книга это
    # допускает; при инфляции мира M на 0,5 п.п. выше книги 1.5 — 1 735,6 /
    # 1 709,4 / 1 683,2). Проверяются коридор обоих слоёв и то, что точка
    # лежит между ними, а не порядок «низ ≤ верх» (аудит 26.09.2026, 5.1).
    bottom, top = min(low, high), max(low, high)
    if not (0 <= bottom and top <= HEADLINE_CEILING and bottom <= central <= top):
        problems.append(
            f"точка вне коридора 0…{HEADLINE_CEILING:.0f} ₽ или не между слоями: "
            f"{low:.1f} / {central:.1f} / {high:.1f}")
    if top <= 0:
        problems.append("заголовок весь в нуле: оба слоя диапазона не положительны")

    # Книга 1.4: ПЕЧАТАЕМЫЙ заголовок — медиана распределения центра и полоса
    # 80 %. Порядок и коридор — у них тоже: P10 ≤ медиана ≤ P90 в 0…5 000 ₽.
    # Быстрая сборка полосы не считает (`headline` = None) — тогда проверка
    # стоит только на точке, как до 1.4.
    headline = fv.get("headline")
    printed_now = None
    if headline:
        try:
            p10, p90 = (float(x) for x in headline["band80"])
            median = float(headline["median"])
            printed_now = float(headline["printed_median"])
        except (KeyError, TypeError, ValueError):
            problems.append("заголовок: в блоке headline нет медианы или полосы 80 %")
        else:
            if not all(math.isfinite(x) for x in (p10, median, p90, printed_now)):
                problems.append("заголовок: медиана или полоса — не конечные числа")
            elif not (0 <= p10 <= median <= p90 <= HEADLINE_CEILING):
                problems.append(
                    f"заголовок (медиана и полоса 80 %) вне коридора 0…{HEADLINE_CEILING:.0f} ₽ "
                    f"или не по порядку: {p10:.1f} / {median:.1f} / {p90:.1f}")

    previous = previous_release(different_from=payload["meta"].get("payload_sha256"))
    if not previous:
        return problems

    was_fv = previous.get("fair_value") or {}
    was_central = float(was_fv.get("central") or 0.0)
    was_v0 = float(((previous.get("layers") or {}).get("analytical") or {}).get("v0") or 0.0)
    now_v0 = float(((payload.get("layers") or {}).get("analytical") or {}).get("v0") or 0.0)
    # Скачок ЗАГОЛОВКА меряется по тому, что читатель видит крупно: печатаемой
    # медиане (книга 1.4). У прошлого выпуска без полосы (до 1.4 или быстрая
    # сборка) заголовком была печатаемая точка по λ.
    was_head = was_fv.get("headline") or {}
    was_printed = float(was_head.get("printed_median") or was_fv.get("printed_central") or 0.0)

    max_central_shift, max_v0_shift = jump_limits(payload)
    jumps = []
    if printed_now is not None:
        if was_printed > 0 and abs(printed_now / was_printed - 1) > max_central_shift:
            jumps.append(f"заголовок {was_printed:.0f} → {printed_now:.0f} ₽ "
                         f"({printed_now / was_printed - 1:+.0%} при пределе "
                         f"±{max_central_shift:.0%})")
    elif was_central > 0 and abs(central / was_central - 1) > max_central_shift:
        jumps.append(f"центр {was_central:.0f} → {central:.0f} ₽ "
                     f"({central / was_central - 1:+.0%} при пределе "
                     f"±{max_central_shift:.0%})")
    if was_v0 > 0 and abs(now_v0 / was_v0 - 1) > max_v0_shift:
        jumps.append(f"V0 {was_v0:.1f} → {now_v0:.1f} млрд ₽ "
                     f"({now_v0 / was_v0 - 1:+.1%} при пределе ±{max_v0_shift:.0%})")
    if not jumps:
        return problems

    # Скачок разрешён ровно тремя вещами: новой версией книги, новыми фактами
    # или письменной запиской. Всё остальное — либо порча входов, либо правка
    # ядра, о которой никто не написал.
    was_meta, meta = previous.get("meta") or {}, payload.get("meta") or {}
    reasons = []
    if was_meta.get("book_version") != meta.get("book_version"):
        reasons.append(f"версия книги {was_meta.get('book_version')} → {meta.get('book_version')}")
    if was_meta.get("facts_date") != meta.get("facts_date"):
        reasons.append(f"дата фактов {was_meta.get('facts_date')} → {meta.get('facts_date')}")
    # Записка разрешает скачок только тогда, когда НАЗЫВАЕТ его результат:
    # «центр станет 936 ₽» — предсказание, которое такт проверяет; «так и
    # задумано» — подпись под чем угодно, в том числе под порчей входов.
    # Записка, чей центр разошёлся с посчитанным, — сама по себе дефект: она
    # обещала одно, вышло другое, — поэтому расхождение попадает в problems
    # даже когда скачок объясняют книга или новые факты.
    #
    # Сравнивается ТОЧНЫЙ центр — точка при центральных значениях суждений
    # (`fair_value.central`, 1 262 на книге 1.4), а не печатаемое число: так
    # записан формат записки (шапка `release_notes.yaml`). С книги 1.4 сам
    # скачок меряется иначе — по печатаемой МЕДИАНЕ (выше), и меры у скачка и
    # у записки разные: записка — проверяемое предсказание точного числа того
    # же выпуска, а медиана считается из той же книги на тех же входах, так
    # что правка, сдвинувшая медиану, сдвигает и точку. Выпуск без полосы
    # (быстрая сборка, книги до 1.4) мерит скачок той же точкой. Панель
    # печатает округлённое, поэтому отказ называет ОБА числа — иначе владелец
    # сверяет обещанное с тем, что видит на экране, и не понимает, откуда
    # взялось третье.
    for note in load_release_notes(notes_path):
        expected = note.get("expected_central")
        if not isinstance(expected, (int, float)) or isinstance(expected, bool) \
                or float(expected) <= 0:
            continue          # такую записку уже отвергла release_notes_problems
        tolerance = note_tolerance(note)
        if abs(central / float(expected) - 1) <= tolerance:
            reasons.append(f"записка: {str(note.get('reason', ''))[:160]}")
        else:
            printed = fv.get("printed_central")
            shown = (f" (панель печатает {float(printed):.0f} ₽)"
                     if isinstance(printed, (int, float)) and not isinstance(printed, bool)
                     else "")
            problems.append(
                f"записка от {note.get('date', '—')} обещала центр "
                f"{float(expected):.0f} ₽ ±{tolerance:.0%}, а выпуск считает "
                f"{central:.0f} ₽{shown} ({central / float(expected) - 1:+.0%}). "
                "Сверяется ТОЧНЫЙ центр. Скачок не тот, который разрешали: "
                "проверьте входы, а не записку")
    if reasons:
        return problems

    problems.append(
        "скачок заголовка без объяснения: " + "; ".join(jumps)
        + ". Если он задуман — версия книги, новые факты или запись в "
          "data/assumptions/release_notes.yaml; если нет — это порча входов")
    return problems


def _layer_weight_problems(payload: dict) -> list[str]:
    """Веса клеток слоя — вероятности, и пересобранный слой обязан совпасть.

    Две проверки, и вторая важнее первой. Сумма весов, равная единице, — это
    арифметика. А `cell_weights_gap` отвечает на вопрос, из-за которого весь
    пункт и появился: ТЕ ЖЕ ЛИ это веса, которыми считало ядро. Слой
    пересобирается из опубликованных весов и сравнивается с ядром по всем
    шестнадцати числам (`layer_cell_weights`); ненулевой разрыв означает, что
    выпуск печатает одни веса, а оценку считал по другим, и публиковать такой
    выпуск нельзя.
    """
    problems = []
    for name, layer in (payload.get("layers") or {}).items():
        weights = layer.get("cell_weights")
        if not weights:
            problems.append(f"слой {name}: нет весов клеток (cell_weights)")
            continue
        total = sum(weights.values())
        if abs(total - 1.0) > 1e-6:
            problems.append(f"слой {name}: веса клеток дают {total:.9f} вместо 1")
        gap = layer.get("cell_weights_gap")
        if not isinstance(gap, (int, float)) or gap > LAYER_WEIGHTS_TOLERANCE:
            problems.append(
                f"слой {name}: опубликованные веса не воспроизводят слой ядра "
                f"(худшее относительное расхождение {gap}) — значит, оценка "
                "считалась по другим весам")
    return problems + _layer_cells_problems(payload)


def _cell_key(cell: dict) -> str:
    return f"{cell['world']}|{cell['regime']}|{cell['capex']}"


def _layer_cells_problems(payload: dict) -> list[str]:
    """Веса слоёв — на клетках сетки: у каждого слоя только клетки сетки с
    положительным весом; «свой макро-взгляд» и «рыночные веса миров» — вся
    сетка, «рыночные ставки как есть» — все клетки одного мира; веса слоя
    «свой макро-взгляд» — вероятности клеток сетки (до округления печати)."""
    problems = []
    grid = {_cell_key(c): c["probability"] for c in payload.get("grid") or []}
    layers_ = payload.get("layers") or {}
    for name, layer in layers_.items():
        weights = layer.get("cell_weights") or {}
        outside = sorted(set(weights) - set(grid))
        if outside:
            problems.append(f"слой {name}: веса клеток вне сетки: {outside[:3]}")
        if any(not v > 0 for v in weights.values()):
            problems.append(f"слой {name}: неположительный вес клетки")
    for name in ("analytical", "market_implied"):
        weights = (layers_.get(name) or {}).get("cell_weights") or {}
        if weights and set(weights) != set(grid):
            problems.append(f"слой {name}: веса не на всех {len(grid)} клетках сетки")
    neutral = (layers_.get("macro_neutral") or {}).get("cell_weights") or {}
    worlds = {key.split("|")[0] for key in neutral}
    if neutral and (len(worlds) != 1
                    or set(neutral) != {k for k in grid if k.split("|")[0] in worlds}):
        problems.append("слой macro_neutral: веса не на клетках одного мира")
    own = (layers_.get("analytical") or {}).get("cell_weights") or {}
    # Вероятность клетки печатается с пятью знаками, вес — с восемью.
    off = [k for k, v in own.items() if k in grid and abs(v - grid[k]) > _half(5) + _half(8)]
    if off:
        problems.append(f"слой analytical: веса не равны вероятностям сетки: {off[:3]}")
    return problems


# Проверки выпуска, которые держатся построением: блоки выпуска согласованы
# между собой и с ядром. Суточный такт полный выпуск в тестах не собирает
# (метка `ci_only`), поэтому то, что прежде проверяли тесты полного выпуска,
# проверяет сборка — на тех числах, что уйдут в R2. Допуски — половины
# печатаемых знаков, чтобы число на границе шага не давало ложного отказа.


def _half(digits: int) -> float:
    """Половина последнего знака числа, округлённого до `digits` знаков."""
    return 10.0 ** -digits / 2


# Центр прогона из опубликованных низа и верха (до рубля; в полосе — до 0,1)
# против центра ядра (до 0,01).
DRAW_CENTRE_SLACK = _half(0) + _half(1) + _half(2)


def _non_finite(node, path: str = "$", out: list | None = None) -> list[str]:
    """Пути нечисловых значений (NaN, ±Infinity): `json.dumps` пишет их без
    кавычек, и `JSON.parse` витрины падает на всём выпуске."""
    out = [] if out is None else out
    if isinstance(node, dict):
        for key, value in node.items():
            _non_finite(value, f"{path}.{key}", out)
    elif isinstance(node, (list, tuple)):
        for i, value in enumerate(node):
            _non_finite(value, f"{path}[{i}]", out)
    elif isinstance(node, float) and not math.isfinite(node):
        out.append(f"{path} = {node}")
    return out


def _grid_problems(payload: dict) -> list[str]:
    """Сетка — все сочетания миров, режимов и уровней capex по разу; вероятности
    клеток и веса сценариев дают единицу до округления печати."""
    problems = []
    grid = payload.get("grid") or []
    keys = [_cell_key(c) for c in grid]
    full = {f"{w}|{r}|{k}" for w in payload.get("worlds") or {}
            for r in REGIME_TITLES for k in CAPEX_TITLES}
    if len(set(keys)) != len(keys) or set(keys) != full:
        problems.append(f"сетка: {len(keys)} клеток, а не {len(full)} разных "
                        "(миры × режимы × уровни capex)")
    total = math.fsum(c["probability"] for c in grid)
    if abs(total - 1.0) > _half(5) * len(grid) + 1e-12:
        problems.append(f"сетка: вероятности клеток дают {total:.6f} вместо 1")
    scenarios = payload.get("scenarios") or []
    weights = math.fsum(s["weight"] for s in scenarios)
    if abs(weights - 1.0) > _half(4) * len(scenarios) + 1e-12:
        problems.append(f"сценарии: веса дают {weights:.6f} вместо 1")
    return problems


def _first_line_problems(fv: dict) -> list[str]:
    """Первая строка «EV против V*»: «кап. + D» и EBITDA — числа `fair_value`,
    мультипликаторы — деление на ту же EBITDA."""
    first = fv.get("ev_first_line") or {}
    problems = []
    cap = first.get("cap_plus_d") or {}
    if (cap.get("ev"), cap.get("rub_per_1pct_ev")) != (fv.get("ev_market_implied"),
                                                       fv.get("rub_per_ev_percent")):
        problems.append("первая строка: «капитализация + D» не та, что в fair_value")
    ebitda = first.get("ebitda_ltm")
    if ebitda != (fv.get("ev_comparison") or {}).get("ebitda_ltm"):
        problems.append("первая строка: EBITDA не та, что у сравнения EV")
    if ebitda:
        for value, multiple in ((first["v0"], first["ev_ebitda"]),
                                (first["v_star"], first["ev_ebitda_v_star"])):
            # Мультипликатор — до 0,01, EV — до 0,1, EBITDA — до 0,01.
            slack = _half(2) + (_half(1) + abs(value) * _half(2) / abs(ebitda)) / abs(ebitda)
            if abs(multiple - value / ebitda) > slack + 1e-9:
                problems.append(f"первая строка: EV/EBITDA {multiple} ≠ {value} / {ebitda}")
    return problems


def _band_problems(payload: dict) -> list[str]:
    """Полоса заголовка: прогоны, таблицы при каждом λ ползунка, вклады осей.

    Строки при λ вне книги воспроизводятся из опубликованных прогонов точно;
    перцентиль рынка при λ книги считан ядром по неокруглённым центрам и
    сверяется интервалом округления прогонов (`DRAW_CENTRE_SLACK`).
    """
    fv = payload["fair_value"]
    head, first = fv["headline"], fv.get("ev_first_line") or {}
    problems = []
    lows, highs, n = head["low_draws"], head["high_draws"], head["draws"]
    if not (n == len(lows) == len(highs) and n > 0):
        return [f"полоса: {n} прогонов, а низов и верхов {len(lows)} и {len(highs)}"]
    market, lam_book = head["market"], head["own_macro_confidence"]
    grid = lambda_grid(lam_book)
    for name, block, fields in (
            ("заголовок", head, ("p_below_market", "market_percentile", "mean")),
            ("первая строка", first, ("v0", "v_star", "gap", "rub_per_1pct_ev"))):
        rows = block.get("by_lambda") or []
        if [r["lambda"] for r in rows] != [round(lam, 4) for lam in grid]:
            problems.append(f"{name}: сетка λ таблицы не та, что у ползунка")
            continue
        at_book = [r for r in rows if r["release"]]
        if (len(at_book) != 1 or not math.isclose(at_book[0]["lambda"], lam_book, abs_tol=_half(4))
                or any(at_book[0][f] != block[f] for f in fields)):
            problems.append(f"{name}: строка при λ книги — не числа выпуска")
    for lam, row in zip(grid, head.get("by_lambda") or []):
        if row["release"]:
            continue
        centres = [lo + lam * (hi - lo) for lo, hi in zip(lows, highs)]
        if (row["p_below_market"] != round(sum(1 for c in centres if c < market) / n, 4)
                or row["market_percentile"] != round(sum(1 for c in centres if c <= market) / n, 4)
                or abs(row["mean"] - math.fsum(centres) / n) > _half(1) + 1e-9):
            problems.append(f"заголовок: строка λ = {row['lambda']} не воспроизводится из прогонов")
            break
    centres = [lo + lam_book * (hi - lo) for lo, hi in zip(lows, highs)]
    below = sum(1 for c in centres if c <= market - DRAW_CENTRE_SLACK - 1e-9) / n
    above = sum(1 for c in centres if c <= market + DRAW_CENTRE_SLACK + 1e-9) / n
    if not below - _half(4) <= head["market_percentile"] <= above + _half(4):
        problems.append(f"заголовок: перцентиль рынка {head['market_percentile']} не из прогонов "
                        f"({below:.4f}–{above:.4f})")
    if first.get("p_below_market") != head["p_below_market"]:
        problems.append("первая строка: P(ниже рынка) не та, что у заголовка")
    ends = first.get("by_lambda") or []
    for row, name in ((ends[0], "macro_neutral"), (ends[-1], "analytical")) if ends else ():
        layer = first["layers"][name]
        # Одна и та же функция ядра при λ = 0 и λ = 1: равны до округления печати.
        if (abs(row["v0"] - layer["v0"]) > _half(1) + 1e-9
                or abs(row["v_star"] - layer["v_star"]) > _half(1) + 1e-9
                or abs(row["gap"] - layer["gap"]) > _half(4) + 1e-12):
            problems.append(f"первая строка: EV при λ = {row['lambda']} — не слой {name}")
    keys = {j["key"] for j in payload.get("judgements") or []}
    for c in head.get("contributions") or []:
        if not c.get("paths") or (keys and c.get("judgement_key") not in keys | {None}):
            problems.append(f"вклад оси {c.get('axis')}: нет путей или ключ не из таблицы суждений")
    return problems


def _contribution_problems(head: dict, release=None) -> list[str]:
    """Вклады осей в полосу (A-V9): доля — квадрат ранговой корреляции оси с
    центром на сумму квадратов, строки по убыванию доли; с `release` — ровно
    оси полосы книги. Доли и корреляции печатаются с 4 знаками — допуски от печати."""
    rows = head["contributions"]
    problems = []
    if release is not None:
        want = sorted(a["name"] for a in release.book["valuation"]["uncertainty"]["axes"])
        if sorted(c["axis"] for c in rows) != want:
            problems.append(f"вклады: {len(rows)} осей, а у полосы книги {len(want)}")
    if not rows:
        return problems + ["вклады: строк нет"]
    shares, corrs = [c["share"] for c in rows], [c["rank_corr"] for c in rows]
    if shares != sorted(shares, reverse=True):
        problems.append("вклады: строки не по убыванию доли")
    total = math.fsum(r * r for r in corrs)
    if total == 0:
        return problems
    if abs(math.fsum(shares) - 1) > len(rows) * _half(4) + 1e-9:
        problems.append(f"вклады: сумма долей {math.fsum(shares):.4f}, а не 1")
    # Граница первого порядка: доля r_i²/Σr² от корреляций, округлённых до
    # 4 знаков, плюс округление самой доли; 1 % — на члены второго порядка.
    spread = math.fsum(abs(r) for r in corrs)
    for c, share, r in zip(rows, shares, corrs):
        f = r * r / total
        slack = _half(4) * (1 + 1.01 * 2 * (abs(r) + f * spread) / total) + 1e-12
        if abs(share - f) > slack:
            problems.append(f"вклад оси {c['axis']}: доля {share} не квадрат корреляции {r} "
                            f"на сумму квадратов ({f:.4f})")
    return problems


def _median_blocks_problems(payload: dict, release=None) -> list[str]:
    """Диагностики печатаемой медианы (книга 1.5): «что в цене», первая строка и
    «что даст отчёт» решены для медианы; нейтральная маржа — при печатаемой
    медиане, наклон — по крайним строкам медианы. С `release` — оси и число
    прогонов пересчёта медианы против книги."""
    fv = payload["fair_value"]
    head, first = fv["headline"], fv.get("ev_first_line") or {}
    reverse = payload.get("reverse_dcf") or {}
    problems = []
    if reverse.get("target") != "median" or first.get("target") != "median":
        problems.append("обратный DCF или первая строка — не для медианы")
    # Поиск медианы стартует с точечного решения: без него строка — не того расчёта.
    if not str(reverse.get("criterion", "")).startswith("медиана") or any(
            "point_value" not in row for row in reverse.get("rows") or []):
        problems.append("обратный DCF медианы: критерий не медиана или у оси нет точечного старта")
    if release is not None:
        axes, n = len(release.book.get("reverse_dcf") or []), median_draws(release.book)
        if (len(reverse.get("rows") or []), reverse.get("draws")) != (axes, n):
            problems.append(f"обратный DCF медианы: {len(reverse.get('rows') or [])} осей на "
                            f"{reverse.get('draws')} прогонах при {axes} осях и {n} прогонах книги")
    # V0 первой строки — медиана λ-смеси V0 прогонов (печать 0,1 и 0,01).
    if abs(first["v0"] - head["v0"]["v0_lambda"]["0.50"]) > _half(1) + _half(2) + 1e-9:
        problems.append("первая строка: V0 не медиана λ-смеси V0 прогонов")
    neutral = payload.get("next_report_neutral") or {}
    if "error" in neutral:
        return problems
    rows = payload.get("next_report_value") or []
    if neutral.get("target") != "median" or any(
            not {"median", "median_low", "median_high"} <= set(r) for r in rows):
        return problems + ["«что даст отчёт»: строк медианы нет"]
    if "margin" in neutral and neutral.get("central") != head["median"]:
        problems.append(f"нейтральная маржа: медиана {neutral.get('central')} "
                        f"не печатаемая {head['median']}")
    if len(rows) >= 2 and rows[-1]["margin"] != rows[0]["margin"]:
        span = (rows[-1]["margin"] - rows[0]["margin"]) * 1000
        slope = (rows[-1]["median"] - rows[0]["median"]) / span
        # Медианы строк печатаются до рубля, наклон — до 0,1.
        slack = 2 * _half(0) / abs(span) + _half(1) + 1e-9
        if (neutral.get("slope_between") != [rows[0]["margin"], rows[-1]["margin"]]
                or abs(neutral.get("slope_rub_per_0p1pp", math.inf) - slope) > slack):
            problems.append("нейтральная маржа: наклон не по крайним строкам медианы")
    return problems


def _judgement_problems(payload: dict, release=None) -> list[str]:
    """Таблица суждений, допущения и разброс по одному суждению: все строки
    `sensitivities` книги, единица по правилу выпуска, допущения с источником и
    значением по убыванию цены ошибки, концы разброса — выборка из таблицы."""
    judgements = payload.get("judgements") or []
    if not judgements:
        return []
    problems = []
    if release is not None and len(judgements) != len(release.book.get("sensitivities") or []):
        problems.append(f"суждения: {len(judgements)} строк при "
                        f"{len(release.book.get('sensitivities') or [])} строках книги")
    assumptions = payload.get("assumptions") or []
    for row in judgements + assumptions:
        if row.get("unit") not in JUDGEMENT_UNITS or row.get("unit") != judgement_unit(row["key"]):
            problems.append(f"суждение {row['key']}: единица {row.get('unit')!r} не по правилу")
    if any(not row.get("source") or row.get("value") is None for row in assumptions):
        problems.append("допущения: строка без источника или значения")
    spreads = [row["spread"] for row in assumptions]
    if spreads != sorted(spreads, reverse=True):
        problems.append("допущения: порядок не по цене ошибки")
    spread = payload["fair_value"].get("judgement_spread") or {}
    rows = {j["key"]: j for j in judgements}
    if spread.get("missing"):
        problems.append(f"разброс по одному суждению: нет строк {spread['missing']}")
    everything = []
    for axis in spread.get("axes") or []:
        prices = [rows[k][f"price_{side}"] for k in axis["keys"] if k in rows
                  for side in ("low", "high")]
        ends = (axis["low"], axis["high"])
        # Строк оси нет в таблице — отказ с причиной, а не падение самой проверки.
        if not prices or any(end["key"] not in rows for end in ends):
            problems.append(f"разброс по одному суждению: строк оси {axis['axis']} нет в таблице суждений")
            continue
        if (axis["low"]["price"], axis["high"]["price"]) != (min(prices), max(prices)) or any(
                end["price"] != rows[end["key"]][f"price_{end['bound']}"]
                or end["unit"] != judgement_unit(end["key"]) for end in ends):
            problems.append(f"разброс по одному суждению: концы оси {axis['axis']} не из таблицы")
        everything += prices
    if everything and (spread.get("low"), spread.get("high")) != (min(everything), max(everything)):
        problems.append("разброс по одному суждению: общие концы — не минимум и максимум осей")
    return problems


def _printed_problems(payload: dict, release) -> list[str]:
    """Печатаемые числа — округление шагом книги НЕОКРУГЛЁННЫХ чисел ядра.

    Округлённое до рубля число на границе шага (1 525,4 → 1 525 → 1 500 при
    ядре 1 550) дало бы ложный отказ, поэтому сверка — с ядром: точка по
    `release.fair_value`, медиана и полосы — по квантилям полосы, которую
    сборка держит в памяти (`slow_blocks`)."""
    A, core = release.book, release.fair_value
    step = float(A["valuation"]["headline"].get("print_step", 50.0))
    fv = payload["fair_value"]
    problems = []
    for name in ("low", "central", "high"):
        exact = getattr(core, name)
        if (fv[name], fv[f"printed_{name}"]) != (round(exact, 0), round(exact / step) * step):
            problems.append(f"точка: {name} {fv[name]} / {fv[f'printed_{name}']} — не ядро "
                            f"{exact:.2f} с шагом {step:g}")
    head = fv.get("headline")
    band = (_SLOW_BLOCKS.get(book_digest(A)) or {}).get("uncertainty")
    if not head or not band:
        return problems
    q = band["central"]

    def rnd(x: float) -> float:
        return int(round(x / step)) * step

    want = dict(median=(round(q["0.50"], 1), rnd(q["0.50"])),
                band80=([round(q["0.10"], 1), round(q["0.90"], 1)], [rnd(q["0.10"]), rnd(q["0.90"])]),
                band50=([round(q["0.25"], 1), round(q["0.75"], 1)], [rnd(q["0.25"]), rnd(q["0.75"])]),
                point=(round(core.central, 1), rnd(core.central)))
    for name, (exact, printed) in want.items():
        if (head[name], head[f"printed_{name}"]) != (exact, printed):
            problems.append(f"заголовок: {name} {head[name]} / {head[f'printed_{name}']} — не "
                            f"квантили полосы ядра с шагом {step:g}")
    return problems


def _checked(title: str, check, *args) -> list[str]:
    """Проверка блока, который мог прийти не той формы: пропавшее поле — строка
    контракта, а не падение сборки на `KeyError`."""
    try:
        return check(*args)
    except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
        return [f"{title}: блок не той формы ({type(exc).__name__}: {exc})"[:300]]


def validate(payload: dict, *, notes_path=None, release=None) -> list[str]:
    """Проверка контракта: терпима к НОВЫМ полям, нетерпима к пропавшим.

    С `release` (сборка передаёт выпуск ядра) — ещё и печать от неокруглённых
    чисел ядра, число строк суждений и осей обратного DCF медианы против книги.
    """
    problems = []
    if payload.get("schema") != SCHEMA:
        problems.append(f"schema: {payload.get('schema')} вместо {SCHEMA}")
    for key in REQUIRED_TOP_LEVEL:
        if key not in payload:
            problems.append(f"нет обязательного блока: {key}")
    size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    if size > 500_000:
        problems.append(f"размер {size / 1000:.0f} КБ больше 500 КБ")
    bad = _non_finite(payload)
    if bad:
        problems.append(f"нечисловые значения в выпуске ({len(bad)}): " + "; ".join(bad[:5]))
    # Гигиена выпуска — первой из содержательных проверок: выпуск публичен.
    problems += hygiene_problems(payload)
    problems += _checked("поля блоков", _field_problems, payload)
    problems += _checked("блоки «Ленты»", _lenta_problems, payload)
    # Записки проверяются ВСЕГДА, а не только при скачке заголовка: бессрочная
    # записка иначе дождалась бы такта, в котором она нужна исправной.
    problems += release_notes_problems(notes_path, max_shift=jump_limits(payload)[0])
    problems += _checked("веса слоёв", _layer_weight_problems, payload)
    problems += _headline_problems(payload, notes_path=notes_path)
    for scenario in payload.get("scenarios", []):
        if scenario["price_published"] < 0:
            problems.append(f"{scenario['name']}: отрицательная публикуемая цена")
    problems += _checked("сетка", _grid_problems, payload)
    fv = payload.get("fair_value") or {}
    if fv:
        problems += _checked("первая строка", _first_line_problems, fv)
    if fv.get("headline"):
        problems += _checked("полоса", _band_problems, payload)
        problems += _checked("вклады в полосу", _contribution_problems, fv["headline"], release)
        problems += _checked("диагностики медианы", _median_blocks_problems, payload, release)
    problems += _checked("суждения", _judgement_problems, payload, release)
    if release is not None and fv:
        problems += _checked("печать", _printed_problems, payload, release)
    return problems
