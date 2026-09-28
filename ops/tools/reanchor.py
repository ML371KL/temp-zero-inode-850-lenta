# -*- coding: utf-8 -*-
"""Перезаякоривание книги допущений на новый отчёт: книга-кандидат и «что сдвинулось».

ЗАЧЕМ ФУНКЦИЯ, А НЕ РУЧНАЯ ПРАВКА. У книги-источника (850oa) ручная процедура
перезаякоривания на сухом прогоне — факты, равные ожиданию модели, — давала
заметный скачок центра ЧИСТОЙ МЕХАНИКИ (июньский излишек оборотного капитала
входил в стартовый уровень при годовом якоре), а ещё несколько мест расходились
и проходили все тесты молча: эффективная площадь, индексы цен capex и июньского
излишка, пул убытков, дата строк моста, забытый `demo_period`. Инструмент
делает все механические шаги одной функцией, и тест `tests/test_reanchor.py`
держит инвариант «перезаякоривание на ожидаемом пути = перекат»: факты, равные
ожиданию модели, не двигают ни V0, ни печатаемую точку, ни заголовок-медиану
(клетка на своём пути — EV до 0,1 %, требования до 2 млн ₽).

СЕГМЕНТЫ (DESIGN D3, ядро P2 §2.1). Сеть Ленты — сегменты по строкам
отчётности: у каждого сегмента с площадью (`yoy`, `level`) свои площадь,
когорты открытий, эффективная площадь и выручка, у сегмента `revenue` — только
выручка. Отчёт полугодия перезаякоривает каждый сегмент по правилу ядра
(`model.core._SegmentState`); маржа, D&A, capex, долг, касса, оборотный капитал
и мост — группы.

Как запускать (из корня репозитория):
  python -B ops/tools/reanchor.py --facts FACTS.json --out DIR
      книга — data/assumptions/assumptions.yaml (или --book ФАЙЛ);
      дата оценки кандидата — --valuation-date, иначе `published` из фактов,
      иначе первый день нового прогнозного полугодия;
      версия — --version, иначе «<версия>+<полугодие>»;
      --band 2000 — раскладка и печатаемого ЗАГОЛОВКА (медиана полосы, 10/90 %,
      P(центр < рынка)) на четырёх книгах; без него — только точка при
      центральных значениях книги
  python -B ops/tools/reanchor.py --expected FACTS.json
      записать синтетический отчёт «факты = ожидание модели» по первому
      прогнозному полугодию книги (сухой прогон процедуры и образец формата;
      НЕ шаблон для настоящих чисел — в нём нет ни одного факта)

Что пишет — ТОЛЬКО в --out: assumptions.yaml и assumptions.json кандидата (без
комментариев — машинный файл; в канон изменения переносятся правкой
`assumptions_template.yaml` по списку «что сдвинулось»), REANCHOR.md —
производные величины, сеть по сегментам, список изменений, раскладка скачка
заголовка («выпуклость ожидаемого факта» + «механика» + «сюрприз факта») и
ручные шаги, которые инструмент не делает. В data/assumptions/ не пишет.

ФАЙЛ ФАКТОВ (JSON; деньги — млрд ₽ ДО МСФО 16, площадь — тыс. м², доли — доли
единицы). Незнакомый ключ — отказ, в том числе внутри сегмента.
  schema              "lenta-850/reanchor-facts/1" (необязательно)
  period              закрываемое полугодие, РОВНО первое прогнозное книги
                      («2026H2»); перезаякоривание идёт по одному полугодию
  published           дата публикации отчёта (необязательно; дата оценки)
  source, note        текст (необязательно)
  segments            {id сегмента: блок} — РОВНО сегменты книги
                      (`revenue.segments`); блок:
      revenue             {полугодие: выручка сегмента}: обязательно само
                          `period`; прежние полугодия — если отчёт их пересчитал
      revenue_basis       необязательно: {полугодие: reported|pro_forma|estimate}
                          для полугодий из `revenue` (по умолчанию reported)
      revenue_se          необязательно: {полугодие: стандартная ошибка, млрд ₽}
                          для полугодий из `revenue`; у reported — 0 (по
                          умолчанию), у расчёта и проформы — обязательна, если
                          книга ведёт `revenue_se`
      area_end            торговая площадь на конец полугодия     } только у
      opened_gross        валовые открытия полугодия (когорта)    } сегментов
      effective_area_avg  необязательно: средняя эффективная      } с площадью
                          площадь; без неё — правило ядра из      } (yoy,
                          площади и открытий (закрытия = площадь₀ } level)
                          + открытия − площадь₁)                  }
      stores_end          необязательно: магазинов на конец (витрина)
  revenue             необязательно: {полугодие: выручка группы} — сверка
                      `period` с суммой сегментов; прежние полугодия —
                      пересчёт строки группы (проформа)
  ebitda_pre16        {полугодие: EBITDA группы до МСФО 16}: обязательно `period`
  margin_pre16        необязательная сверка: маржа = EBITDA / выручка группы
                      (ловит маржу после МСФО 16 и годовую вместо полугодовой)
  da_pre16            амортизация полугодия до МСФО 16 — без обесценения и его
                      восстановления (нетто, по примечаниям МСФО)
  capex               денежный capex полугодия
  net_debt, cash      отчётный чистый долг до МСФО 16 и денежные средства на
                      конец полугодия
  nwc_to_revenue      оборотный капитал группы (без строк моста, с
                      приобретённым периметром) на дату / выручка LTM на дату;
                      на 30.06 — С июньским излишком: инструмент разложит сам
  tax_loss_pool       пул убытков группы на конец полугодия; без него —
  net_interest        чистые проценты полугодия, и пул катится правилом ядра
                      (ускоренная налоговая амортизация — по capex отчёта)
  tax_loss_pool_acquired  необязательно: пул запертых убытков приобретённых
                      юрлиц (`tax.acquired_nol`) на дату; без него — правило
                      ядра (до `usable_from` копит `locked_addback`, в нём
                      переходит в пул группы)
  ebitda_ltm          необязательная сверка суммы двух последних полугодий
  cpi_half            необязательно: ИПЦ полугодия (средний г/г, как траектории
                      миров) — переиндексация цен capex; без него — ИПЦ миров
                      книги при аналитических весах (с предупреждением)
  bridge              {id строки bridge.items: сумма} на дату отчёта; строки с
                      наращением (`accrete_rate_half`) обязательны; строка,
                      выплаченная к концу полугодия (`settle_period` ≤ period),
                      — 0 или без ключа; остальные — если изменились
  bridge_as_of        дата, на которую названы суммы моста; по умолчанию —
                      первый день после полугодия (остаток на конец дня
                      31.12 = на начало 01.01: линейка ядра считает день на его
                      начало, P2 §9 п. 3)
  reported            необязательно: {revenue|ebitda_pre16: {полугодие: число}}
                      — отчётный периметр для сверки (`facts.reported`); без
                      него отчётное полугодие = проформа (периметр не менялся)
  shares_out_mln, undrawn_credit_lines   если изменились
  dividends_paid      дивиденды, выплаченные за полугодие (0 — не было):
                      обязательно у книги с годовым правилом лестницы
                      (`financing.dividend_timing`), иначе — отказ (ключ
                      ничего не значит); нужен для FCFE года (см. ниже)
  fcfe_ytd            необязательно, только при годовом правиле: FCFE
                      финансового года по закрытое полугодие включительно
                      (определение ядра) — ставится как есть вместо расчёта

ЧТО ДЕЛАЕТ С КНИГОЙ:
  meta          first_period → следующее полугодие; facts_date → конец
                полугодия; valuation_date; bridge_as_of; version; curve_as_of —
                дата кривых книги (миры и кривые не пересобираются)
  facts         выручка и EBITDA группы (окно той же длины), `reported`, факты
                якоря (D&A, capex, чистый долг, касса, EBITDA LTM — проформа и
                отчётная, выручка LTM),
                акции, линии; по сегментам — выручка, её основание и ошибка
                (окно), площадь, магазины, когорты (сдвиг окна), эффективная
                площадь (см. ниже)
  nwc           старт ОК из факта: `anchor_level` — уровень ОК на дату (млрд ₽,
                если книга задаёт старт балансом), иначе `nwc_pct_start` = факт
                за вычетом июньского излишка (на 30.06); ОК приобретённого
                периметра (`nwc.acquired_path`, доля выручки поверх `nwc_pct`) —
                часть траектории, с которой сверяется факт; траектории nwc_pct —
                по решению --nwc-path (см. ниже); seasonal_june_excess ×
                LTM₁/LTM₀ — излишек индексирован выручкой LTM ЯКОРЯ, и без
                переиндексации набор 1П навсегда меньше
  цены якоря   growth_capex_per_m2 каждого сегмента с площадью,
                infra_capex_per_net_m2 и плотность сегментов `level`
                (`density_path`) × (1 + ИПЦ за полугодие): они — в ценах
                ЯКОРЯ (индекс ИПЦ ядра стартует с единицы), и без
                переиндексации рост и замещение сети навсегда дешевле, а
                выручка сегмента `level` навсегда меньше на полугодие
                инфляции; те же оси чувствительности, полосы и обратного DCF —
                тем же множителем; база физической доли capex и линейная D&A —
                через якорь
  tax           nol_start и tax.acquired_nol.amount — пулы на дату отчёта;
                прибавки запертых юрлиц за закрытые полугодия
                (`discount_rule.locked_addback`) снимаются
  bridge        строки моста на дату отчёта с `as_of` = bridge_as_of (строки
                без факта — со своей прежней датой); строка, выплаченная к
                концу полугодия, снимается вместе с осями на неё: выплата уже
                в чистом долге
  дивиденды     при годовом правиле лестницы — facts.anchor.fcfe_ytd: FCFE
                года нового якоря (якорь на 1П — FCFE полугодия, на 2П — плюс
                fcfe_ytd старого якоря); FCFE полугодия в определении ядра =
                ЧД якоря − ЧД отчёта + dividends_paid − прирост операционной
                кассы (operating_cash_pct × ΔLTM: ядро считает его оттоком
                FCFF, отчётный ЧД — нет)
  наблюдения    факт маржи → joint.regime_update.observations (см. ниже)
  demo_period   → первое прогнозное полугодие; demo_values — тем же шагом,
                сдвинутые на рост ожидаемой маржи (с округлением до шага)
  шоки нау-каста за закрытые полугодия удаляются

ЭФФЕКТИВНАЯ ПЛОЩАДЬ — ИНДЕКС, И ОН НЕ ДОЛЖЕН ПРЫГАТЬ. Без перенесённого уровня
ядро на якоре восстанавливает его как «физическая − незрелая», а за полугодие
закрытия худших точек (продуктивность сегмента < 1) подняли индекс над этим
уровнем на δ = (1 − продуктивность) × закрытия. Выручка сегмента `yoy`
считается отношением эффективных площадей год к году, и сброс стоил бы части
выручки навсегда. Поэтому инструмент переносит уровень конца полугодия по
каждому сегменту (`facts.segments.<id>.eff_area_end`), и ряд непрерывен точно.

История эффективной площади сегмента, выведенная правилом сети назад
(`facts.segments.<id>.closed_area_hist`, `model.core.effective_history`), на
новом якоре записывается числами (`eff_area_avg_hist`: старый якорь — как его
видело ядро, закрытое полугодие — шаг ядра на фактах), а ключ закрытий
снимается: вывод назад от перенесённого уровня разошёлся бы с шагом, по
которому шла прокатанная книга (у когорт, дозревающих до d, правило вывода
считает плотными на одну когорту больше, чем шаг вперёд). Оси d и
продуктивности закрытых двигают такую историю только вперёд — строка в
предупреждениях.

ПЛОТНОСТЬ СЕГМЕНТА `level` — СУЖДЕНИЕ, А НЕ ФАКТ. Выручка сегмента `level` =
эффективная площадь × плотность (в ценах полугодия якоря) × индекс ИПЦ мира
× доля полугодия: выручка закрытого полугодия в следующие не переходит.
Инструмент переиндексирует плотность в цены нового якоря и показывает
плотность, которую дал факт (выручка / (эффективная площадь × индекс ИПЦ
полугодия / 1000 × доля)), рядом с плотностью книги; расхождение больше 1 % —
строка ручных шагов (пересмотреть `density_path`, автор книги).

ФАКТ МАРЖИ СЧИТАЕТСЯ ОДИН РАЗ. Маржа полугодия выводится из EBITDA и выручки
группы ФАЙЛА (не вводится отдельно) и попадает в два места с разным смыслом:
уровень — в историю `facts.ebitda_pre16` (из неё ядро берёт только EBITDA LTM
первого прогнозного полугодия), свидетельство о режиме — в
`joint.regime_update.observations` (правило A-P2u двигает вероятности режимов,
ядро берёт из него отклонение на якоре — AR(1)-хвост, см. ниже). Двойного
счёта нет, и инструмент закрывает три места, где он возник бы: (1)
апостериорные вероятности не вписываются в `regime_given_world` — таблица
остаётся априорной, её двигает правило; (2) цели режимов на закрытое
полугодие не трогаются — против них судится факт; (3) прогноз нау-каста за то
же полугодие и его шок заменяются фактом, а не остаются рядом с ним.

МАРЖА ЯКОРЯ НА ПРОФОРМЕ ОСТАЁТСЯ НАБЛЮДЕНИЕМ. `facts.anchor.margin_pro_forma`
с `margin_pro_forma_se` — наблюдение A-P2u за полугодие старого якоря с
ошибкой (у Ленты 1П2026 — периметр с приобретённой сетью за полгода). Новый
якорь — отчётное полугодие (факт, se = 0), поэтому ключи проформы уходят из
`facts.anchor`, а само наблюдение переносится в `joint.regime_update.
observations` как {value, se}: правдоподобие режимов у кандидата то же, что у
прокатанной книги (P2 §9 п. 7). Наблюдения закрытых полугодий до
закрываемого — проформы и факты — остаются как есть.

AR(1)-ХВОСТ ФАКТА НЕСЁТ ЯДРО. Факт в наблюдениях — на якоре кандидата, и ядро
задаёт им отклонение каждого режима на якоре (правило якоря, `model/core.py`):
дальше оно затухает ρ, как без перезаякоривания. Инструмент хвост не пишет и
показывает его диагностикой в REANCHOR.md.

ОБОРОТНЫЙ КАПИТАЛ: ФАКТ ПРОТИВ ТРАЕКТОРИИ. Факт становится стартовым уровнем
`nwc_pct_start` (или `anchor_level`), а траектории `nwc_pct` (+ `acquired_path`)
книга задаёт сама. Разрыв факта с тем, чего ждала траектория сетки на дату
отчёта, ядро превращает в РАЗОВЫЙ поток первого прогнозного полугодия. При разрыве больше 0,2 п.п. выручки LTM
инструмент отказывает без явного решения:
  --nwc-path hold   траектории как в книге: разрыв — разовый поток (высвобождение
                    или вложение) в первом полугодии;
  --nwc-path fact   все траектории `nwc_pct` сдвигаются на разрыв: уровень факта
                    держится, сценарии высвобождения идут от него.
Решение и оценка разового потока — в REANCHOR.md (раздел предупреждений).

Коды возврата: 0 — кандидат записан; 1 — негодные входы (файл фактов, книга,
каталог вывода); отказы объясняют, что именно не так.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from model.book import (BOOK_DIR, BOOK_YAML, FACT_SUM_TOL, REVENUE_BASES, BookError,  # noqa: E402
                        Cell, Segment, acquired_nol, all_cells, bridge_items, capex_network_rules,
                        capex_tax_premium, dividend_timing,
                        half_rate, load_book, path_value, period_index, previous_period,
                        segments, validate_book)
from model.core import (accreted, anchor_effective_end, effective_history,  # noqa: E402
                        margin_observations, margin_season, observation_weight, period_bounds,
                        run_cell)
from model.financing import credit_limit  # noqa: E402
from model.grid import (build_grid, fair_value, layer_variants, layers,  # noqa: E402
                        regime_unconditional)
from model.uncertainty import uncertainty  # noqa: E402

BOOK = BOOK_DIR
SCHEMA = "lenta-850/reanchor-facts/1"

REQUIRED = ("period", "segments", "ebitda_pre16", "da_pre16", "capex", "net_debt", "cash",
            "nwc_to_revenue", "bridge")
OPTIONAL = ("schema", "published", "source", "note", "revenue", "margin_pre16", "tax_loss_pool",
            "net_interest", "tax_loss_pool_acquired", "ebitda_ltm", "cpi_half", "bridge_as_of",
            "reported", "shares_out_mln", "undrawn_credit_lines", "dividends_paid", "fcfe_ytd")
# Ключи годового правила лестницы (`financing.dividend_timing`): без него — отказ.
ANNUAL_DIVIDEND_KEYS = ("dividends_paid", "fcfe_ytd")
# Ключи блока сегмента в файле фактов по режиму сегмента книги.
SEGMENT_REQUIRED = {"network": ("revenue", "area_end", "opened_gross"), "revenue": ("revenue",)}
SEGMENT_OPTIONAL = {"network": ("revenue_basis", "revenue_se", "effective_area_avg", "stores_end"),
                    "revenue": ("revenue_basis", "revenue_se")}
REPORTED_KEYS = ("revenue", "ebitda_pre16")

# Допуски СВЕРОК файла фактов — параметры проверки, не допущения модели.
MARGIN_CHECK = 0.0005      # явная маржа против EBITDA/выручки: 0,05 п.п. (округление отчёта)
LTM_CHECK = 0.05           # EBITDA LTM против суммы полугодий, млрд ₽
MARGIN_SANITY = 0.02       # маржа вне [мин. цели − 2 п.п.; макс. + 2 п.п.] — не та база (МСФО 16?)
AREA_TOL = 1e-9            # закрытия сегмента ниже −1e-9 × площадь₀ — отказ (не округление)

# Цена инфраструктуры — в ценах ЯКОРЯ (индекс ИПЦ ядра `inflation_index`
# стартует с единицы в первом прогнозном полугодии); цены открытий сегментов и
# плотность сегментов `level` — там же (`price_indexed`).
INFRA_PRICE = "capex.infra_capex_per_net_m2"
DENSITY_CHECK = 0.01       # плотность факта против книги у сегмента level: 1 % — ручной шаг
REVENUE_INDEXED = ("nwc.seasonal_june_excess",)
# Разрыв «факт оборотного капитала − траектория сетки на дату отчёта», после
# которого нужно явное решение --nwc-path — порог проверки, не допущение модели.
NWC_GAP = 0.002
NWC_PATHS = ("hold", "fact")
# Допуск механики на пути клетки (инвариант tests/test_reanchor.py, уровень 1) —
# для диагностики REANCHOR.md: EV — доля прокатанной + пол, требования — млрд ₽.
CELL_EV_TOLERANCE, CELL_EV_FLOOR, CELL_CLAIMS_TOLERANCE = 1e-3, 0.02, 2e-3


class FactsError(ValueError):
    """Файл фактов не годится для перезаякоривания — объяснение в тексте."""


@dataclass
class Reanchored:
    """Кандидат и всё, что о нём надо знать до переноса в канон."""

    book: dict
    period: str
    derived: dict
    changes: list[tuple[str, object, object]]
    warnings: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ полугодия


def next_period(p: str) -> str:
    year, half = int(p[:4]), int(p[5])
    return f"{year}H2" if half == 1 else f"{year + 1}H1"


def period_end(p: str) -> dt.date:
    return period_bounds(p)[1]


def anchor_of(A: dict) -> str:
    """Последнее отчётное полугодие книги — то, что перед первым прогнозным."""
    return previous_period(A["meta"]["first_period"])


def price_indexed(A: dict) -> tuple[str, ...]:
    """Пути величин в ценах якоря: цены открытий сегментов с площадью, цена
    инфраструктуры, плотность сегментов `level` (траектория)."""
    segs = segments(A)
    return (tuple(f"capex.segments.{s.id}.growth_capex_per_m2" for s in segs if s.network)
            + (INFRA_PRICE,)
            + tuple(f"revenue.segments.{s.id}.density_path" for s in segs if s.mode == "level"))


def _scaled(value, factor: float):
    """Число или траектория (все значения, кроме `LT_from`) × множитель."""
    if isinstance(value, dict):
        return {k: (v if k == "LT_from" else v * factor) for k, v in value.items()}
    return value * factor


# -------------------------------------------------------------- файл фактов


def read_facts(path: Path) -> dict:
    try:
        facts = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FactsError(f"файл фактов {path}: {exc}") from exc
    if not isinstance(facts, dict):
        raise FactsError(f"файл фактов {path}: ожидается объект JSON")
    return facts


def _is_number(value) -> bool:
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value))


def _number(block: dict, key: str, where: str = "факты") -> float:
    value = block[key]
    if not _is_number(value):
        raise FactsError(f"{where}: {key} = {value!r} — ожидается число")
    return float(value)


def _halves(block, key: str, period: str, where: str) -> dict:
    """Словарь полугодий с положительными числами, в нём `period`, позже — нет."""
    if not isinstance(block, dict) or period not in block:
        raise FactsError(f"{where}: {key} — словарь полугодий, и в нём обязано быть {period}")
    for p, v in block.items():
        try:
            later = period_index(p) > period_index(period)
        except (TypeError, ValueError, IndexError):
            raise FactsError(f"{where}: {key}[{p!r}] — ожидается полугодие «ГГГГH1»/«ГГГГH2»") from None
        if later:
            raise FactsError(f"{where}: {key}[{p}] — позже закрываемого полугодия {period}")
        if not _is_number(v) or v <= 0:
            raise FactsError(f"{where}: {key}[{p}] = {v!r} — ожидается положительное число")
    return block


def _check_segments(A: dict, facts: dict, period: str) -> None:
    block = facts["segments"]
    book = {s.id: s for s in segments(A)}
    if not isinstance(block, dict):
        raise FactsError("факты: segments — словарь {id сегмента: блок}")
    unknown, missing = sorted(set(block) - set(book)), [s for s in book if s not in block]
    if unknown:
        raise FactsError(f"факты: незнакомые сегменты {', '.join(unknown)} (сегменты книги: "
                         f"{', '.join(book)}); новый сегмент — решение автора книги")
    if missing:
        raise FactsError(f"факты: нет сегментов {', '.join(missing)} — отчёт перезаякоривает "
                         "каждый сегмент книги")
    area0 = {s.id: s.facts["area_end"] for s in book.values() if s.network}
    for sid, seg in book.items():
        fs, where = block[sid], f"факты: segments.{sid}"
        kind = "network" if seg.network else "revenue"
        if not isinstance(fs, dict):
            raise FactsError(f"{where} — ожидается блок сегмента")
        known = SEGMENT_REQUIRED[kind] + SEGMENT_OPTIONAL[kind]
        odd = sorted(set(fs) - set(known))
        if odd:
            raise FactsError(f"{where}: незнакомые ключи {', '.join(odd)} (у сегмента mode: "
                             f"{seg.mode} известны: {', '.join(known)})")
        lost = [k for k in SEGMENT_REQUIRED[kind] if k not in fs]
        if lost:
            raise FactsError(f"{where}: нет обязательных ключей {', '.join(lost)}")
        _halves(fs["revenue"], "revenue", period, where)
        basis = fs.get("revenue_basis", {})
        if not isinstance(basis, dict) or not set(basis) <= set(fs["revenue"]) \
                or not all(b in REVENUE_BASES for b in basis.values()):
            raise FactsError(f"{where}: revenue_basis — {{полугодие из revenue: "
                             f"{'|'.join(REVENUE_BASES)}}}, а не {basis!r}")
        se = fs.get("revenue_se", {})
        if not isinstance(se, dict) or not set(se) <= set(fs["revenue"]) \
                or not all(_is_number(v) and v >= 0 for v in se.values()):
            raise FactsError(f"{where}: revenue_se — {{полугодие из revenue: число ≥ 0}}, "
                             f"а не {se!r}")
        for p in fs["revenue"]:
            reported = basis.get(p, "reported") == "reported"
            if reported and se.get(p, 0.0) != 0.0:
                raise FactsError(f"{where}: revenue_se[{p}] = {se[p]!r} у отчёта (reported) — "
                                 "у отчёта ошибки нет")
            if not reported and p not in se and "revenue_se" in seg.facts:
                raise FactsError(f"{where}: revenue_se[{p}] обязателен — основа {basis[p]}, "
                                 "а книга ведёт ошибку базы (revenue_se)")
        if seg.network:
            opened, area = _number(fs, "opened_gross", where), _number(fs, "area_end", where)
            closed = area0[sid] + opened - area
            # Допуск — округление суммы в последнем знаке (ожидание модели —
            # среднее клеток), а не разрешение закрытиям уйти в минус.
            if opened < 0 or area <= 0 or closed < -AREA_TOL * area0[sid]:
                raise FactsError(
                    f"{where}: площадь {area0[sid]} + открытия {opened} − площадь {area} = "
                    f"закрытия {closed:.3f} < 0 — открытия валовые? площадь та же? Покупка "
                    "сети (площадь без открытий) — вне правила сети: решение автора книги")
            if "effective_area_avg" in fs and not _number(fs, "effective_area_avg", where) > 0:
                raise FactsError(f"{where}: effective_area_avg — ожидается положительное число")
            if "stores_end" in fs and (not _is_number(fs["stores_end"]) or fs["stores_end"] < 0):
                raise FactsError(f"{where}: stores_end = {fs['stores_end']!r} — ожидается число")
    group = facts.get("revenue")
    if group is not None:
        _halves(group, "revenue", period, "факты")
        total = sum(float(block[s]["revenue"][period]) for s in book)
        if abs(float(group[period]) - total) > FACT_SUM_TOL * abs(total):
            raise FactsError(f"факты: revenue[{period}] = {group[period]} ≠ сумме выручки "
                             f"сегментов {total!r} — строки отчёта и сегменты книги разошлись")


def check_facts(A: dict, facts: dict) -> None:
    """Громкий отказ на всём, что ядро прочло бы молча не так."""
    unknown = sorted(set(facts) - set(REQUIRED) - set(OPTIONAL))
    if unknown:
        raise FactsError(f"факты: незнакомые ключи {', '.join(unknown)} "
                         f"(известны: {', '.join(REQUIRED + OPTIONAL)})")
    missing = [k for k in REQUIRED if k not in facts]
    if missing:
        raise FactsError(f"факты: нет обязательных ключей {', '.join(missing)}")
    if facts.get("schema", SCHEMA) != SCHEMA:
        raise FactsError(f"факты: schema {facts['schema']!r}, ожидается {SCHEMA!r}")
    period, first = facts["period"], A["meta"]["first_period"]
    if period != first:
        raise FactsError(
            f"факты за {period}, а первое прогнозное полугодие книги — {first}. "
            "Перезаякоривание идёт по одному полугодию: сначала отчёт за "
            f"{first}, потом следующий.")
    _halves(facts["ebitda_pre16"], "ebitda_pre16", period, "факты")
    for key in ("da_pre16", "capex", "net_debt", "cash", "nwc_to_revenue"):
        _number(facts, key)
    for key in ("margin_pre16", "tax_loss_pool", "net_interest", "tax_loss_pool_acquired",
                "ebitda_ltm", "cpi_half", "shares_out_mln", "undrawn_credit_lines",
                *ANNUAL_DIVIDEND_KEYS):
        if key in facts:
            _number(facts, key)
    stray = [k for k in ANNUAL_DIVIDEND_KEYS if k in facts]
    if dividend_timing(A) is None and stray:
        raise FactsError(f"факты: {', '.join(stray)} — только у книги с годовым правилом "
                         "лестницы (financing.dividend_timing); правило полугодия их не читает")
    if dividend_timing(A) is not None and not stray:
        raise FactsError("факты: нужен dividends_paid (дивиденды, выплаченные за полугодие; 0 — "
                         "если не было) или fcfe_ytd — годовое правило лестницы считает FCFE года")
    if "tax_loss_pool" not in facts and "net_interest" not in facts:
        raise FactsError("факты: нужен tax_loss_pool (пул убытков на дату отчёта) или "
                         "net_interest (чистые проценты полугодия — пул покатится правилом книги)")
    if "tax_loss_pool_acquired" in facts and not acquired_nol(A):
        raise FactsError("факты: tax_loss_pool_acquired, а у книги нет tax.acquired_nol")
    _check_segments(A, facts, period)
    reported = facts.get("reported")
    if reported is not None:
        if not isinstance(reported, dict) or not set(reported) <= set(REPORTED_KEYS):
            raise FactsError(f"факты: reported — {{{'|'.join(REPORTED_KEYS)}: {{полугодие: число}}}}")
        for key, block in reported.items():
            _halves(block, key, period, "факты: reported")
    if "bridge_as_of" in facts:
        try:
            day = dt.date.fromisoformat(facts["bridge_as_of"])
        except (TypeError, ValueError):
            raise FactsError(f"факты: bridge_as_of = {facts['bridge_as_of']!r} — ожидается дата "
                             "«ГГГГ-ММ-ДД»") from None
        if day < period_end(period):
            raise FactsError(f"факты: bridge_as_of {day} раньше конца полугодия {period}: мост — "
                             "на дату отчёта")
    bridge = facts["bridge"]
    if not isinstance(bridge, dict):
        raise FactsError("факты: bridge — словарь строк моста {id: сумма}")
    items = {item.id: item for item in bridge_items(A)}
    unknown = sorted(set(bridge) - set(items))
    if unknown:
        raise FactsError(f"факты: незнакомые строки моста {', '.join(unknown)} "
                         f"(известны: {', '.join(items)})")
    for key, value in bridge.items():
        if not _is_number(value):
            raise FactsError(f"факты: bridge.{key} = {value!r} — ожидается число")
    for item in items.values():
        if item.accrete_rate_half is not None and item.id not in bridge:
            raise FactsError(f"факты: bridge.{item.id} обязателен — строка наращивается "
                             "(обязательство на дату отчёта, 0 после выплаты)")
        if (item.settle_period is not None and bridge.get(item.id)
                and period_index(item.settle_period) <= period_index(period)):
            raise FactsError(
                f"факты: строка {item.id} выплачивается в {item.settle_period} — к концу "
                f"{period} она выплачена, а сумма {bridge[item.id]} ≠ 0: "
                "выплата уже в чистом долге, строка посчитала бы её дважды")


# ------------------------------------------------------ правила книги в шаге


def effective_area_step(A: dict, seg: Segment, opened: float, area_end: float) -> dict:
    """Эффективная площадь сегмента за закрываемое полугодие — ПРАВИЛО ЯДРА на фактах.

    То же, что шаг сегмента в `model.core.run_cell` (созревание когорт,
    плотность новой площади d, закрытия худших точек с продуктивностью
    сегмента), но на фактических площади и открытиях: закрытия — из тождества
    «площадь₀ + открытия − закрытия = площадь₁». Тест
    `test_the_tool_rules_are_the_core_rules` сверяет его с ядром на путях клеток.

    Возвращает среднюю за полугодие, уровень конца по правилу, уровень, который
    ядро восстановило бы на новом якоре («физическая − незрелая»), их разность
    δ, закрытия, когорты нового якоря и число последних исторических когорт,
    дозревающих до d (`facts.segments.<id>.new_area_dense_cohorts`).
    """
    maturity = A["revenue"]["maturity_curve"]
    cohorts = list(seg.facts.get("new_area_gross_hist") or [])
    area0 = seg.facts["area_end"]
    density, dense = seg.new_space_density, seg.dense_history_cohorts

    def immature(cs: list[float], n_dense: int) -> float:
        return sum(n * (1 - maturity[min(a, len(maturity) - 1)]
                        * (density if a < n_dense else 1.0))
                   for a, n in enumerate(reversed(cs)))

    start = anchor_effective_end(seg, maturity)
    closed = area0 + opened - area_end
    maturing = sum(cohorts[-a] * (maturity[a] - maturity[a - 1])
                   * (density if a <= dense else 1.0)
                   for a in range(1, min(len(maturity), len(cohorts) + 1)))
    end = (start - closed * seg.closed_productivity + opened * maturity[0] * density
           + maturing)
    # Окно когорт: не короче, чем дозревает когорта (длина кривой − 1), —
    # иначе дозревание когорты, открытой до якоря, пропало бы из пути.
    keep = max(len(cohorts), len(maturity) - 1)
    rotated = (cohorts + [opened])[-keep:]
    dense_new = min(len(rotated), dense + 1) if density != 1.0 else min(dense, len(rotated))
    reset = area_end - immature(rotated, dense_new)
    return dict(start=start, end=end, average=(start + end) / 2.0, reset=reset,
                delta=end - reset, closed=closed, cohorts=rotated, dense=dense_new)


def book_effective_hist(A: dict, seg: Segment) -> dict:
    """Средняя эффективная площадь двух полугодий до прогноза — как её видит ядро.

    Записанная история книги (`eff_area_avg_hist`), а у сегмента с закрытиями
    истории (`closed_area_hist`) — вывод правила сети назад
    (`model.core.effective_history`), как в `model.core._SegmentState`.
    """
    maturity = A["revenue"]["maturity_curve"]
    out = dict(seg.facts.get("eff_area_avg_hist") or {})
    derived = effective_history(seg, maturity, anchor_effective_end(seg, maturity), A)
    if derived is not None:
        out.update(derived)
    return out


def level_density(A: dict, seg: Segment, period: str, revenue: float, effective_avg: float,
                  cpi_factor: float) -> float:
    """Плотность сегмента `level` в закрываемом полугодии, которую дал ФАКТ выручки.

    Правило ядра: выручка = эффективная площадь × плотность / 1000 × индекс ИПЦ
    × доля полугодия (`model.core.run_cell`; индекс в первом прогнозном
    полугодии — 1 + ИПЦ за полугодие); отсюда плотность = выручка ×
    1000 / (эффективная площадь × индекс × доля). На пути клетки это ровно
    плотность книги (тест `test_the_tool_rules_are_the_core_rules`).
    """
    share = seg.spec["h1_share"] if period.endswith("H1") else 1.0 - seg.spec["h1_share"]
    return revenue * 1000.0 / (effective_avg * cpi_factor * share)


def acquired_pool_by_rule(A: dict, period: str, pool_acquired: float | None = None) -> float:
    """Пул запертых убытков приобретённых юрлиц на конец полугодия — правило ядра.

    `frozen_until_usable_from` (`model.book.acquired_nol`): до `usable_from` пул
    копит убыток запертых юрлиц полугодия (`locked_addback`), в `usable_from` и
    позже — переходит в пул группы (остаток — ноль).
    """
    locked = acquired_nol(A)
    if not locked:
        return 0.0
    acquired = locked["amount"] if pool_acquired is None else pool_acquired
    if period_index(period) < period_index(locked["usable_from"]):
        return acquired + locked["locked_addback"].get(period, 0.0)
    return 0.0


def tax_loss_pools_by_rule(A: dict, period: str, revenue: float, ebitda: float, da: float,
                           capex: float, net_interest: float, pool: float | None = None,
                           pool_acquired: float | None = None) -> tuple[float, float]:
    """Пул убытков группы и пул запертых убытков приобретённых юрлиц на конец полугодия.

    Правило ядра (`model.core.run_cell`) на фактах: база = EBIT + постоянная
    прибавка × выручка − премия × (capex − D&A) (ускоренная налоговая
    амортизация, `tax.capex_tax_premium_share`); корзина с долгом — α × база −
    проценты; запертые убытки (`tax.acquired_nol`) до `usable_from` прибавляют к
    ней убыток запертых юрлиц и копятся в их пуле, в `usable_from` пул
    переходит в пул группы с долей (1 − haircut); убыток корзины пополняет пул
    группы, прибыль гасит его не больше чем на предел зачёта (`tax.nol_limit`
    до `nol_full_from_year`). Сверяется с полями ядра `StepRow.tax_loss_pool`,
    `tax_loss_pool_acquired` тем же тестом, что и площадь.
    """
    TX = A["tax"]
    locked = acquired_nol(A)
    pool = TX["nol_start"] if pool is None else pool
    acquired = (locked["amount"] if locked else 0.0) if pool_acquired is None else pool_acquired
    base = (ebitda - da) + path_value(TX["permanent_addback_pct"], period) * revenue
    premium = capex_tax_premium(A)
    if premium:
        base -= premium * (capex - da)
    corner = TX["alpha"] * base - net_interest
    if locked:
        if period_index(period) < period_index(locked["usable_from"]):
            addback = locked["locked_addback"].get(period, 0.0)
            corner += addback
            acquired += addback
        else:
            pool += (1.0 - locked["haircut"]) * acquired
            acquired = 0.0
    if corner < 0:
        return pool - corner, acquired
    limit = TX["nol_limit"] if int(period[:4]) < TX["nol_full_from_year"] else 1.0
    return pool - min(pool, limit * corner), acquired


def book_cpi(A: dict, period: str) -> float:
    """ИПЦ полугодия по мирам книги при аналитических весах — если факта нет."""
    J = A["joint"]
    return sum(w * path_value(A["worlds"][name]["cpi"], period)
               for name, w in J["world_prob"].items())


# -------------------------------------------------------------- перезаякоривание


def _get(tree: dict, dotted: str):
    """Значение по пути книги (селектор `[id]` — строка списка, `model.book.get_path`)."""
    from model.book import get_path

    return get_path(tree, dotted)


def _set(tree: dict, dotted: str, value) -> None:
    from model.book import path_parent

    node, last = path_parent(tree, dotted)
    node[last] = value


def _axis_lists(A: dict):
    """Три списка осей: (где, оси, числовые поля). Чувствительности, полоса, обратный DCF."""
    return (
        ("sensitivities", A.get("sensitivities") or [], ("low", "high", "shift_low", "shift_high")),
        ("valuation.uncertainty.axes", A["valuation"]["uncertainty"].get("axes") or [],
         ("low", "high", "shift_low", "shift_high")),
        ("reverse_dcf", A.get("reverse_dcf") or [], ("search", "range")),
    )


def _axis_paths(axis: dict) -> list[str]:
    return list(axis.get("paths") or [axis.get("path")])


def _scale_axes(A: dict, group: tuple[str, ...], factor: float) -> None:
    """Оси по параметрам `group` — тем же множителем, что и сами параметры.

    Диапазон суждения задан в тех же единицах, что и параметр; переиндексация
    параметра без его диапазона сдвинула бы суждение, а не единицы. Ось,
    которая двигает переиндексируемый параметр ВМЕСТЕ с другим (у того другой
    множитель или никакого), одним множителем не переводится — отказ: молча
    пропущенная ось осталась бы в ценах старого якоря. Ось-множитель (`scale`)
    безразмерна и не меняется.
    """
    def inside(path) -> bool:
        # путь самой величины или узла её траектории (`...density_path.LT`)
        return any(path == g or str(path).startswith(g + ".") for g in group)

    for where, axes, keys in _axis_lists(A):
        for axis in axes:
            paths = _axis_paths(axis)
            hit = [p for p in paths if inside(p)]
            if not hit or axis.get("kind") == "scale":
                continue            # множитель (`scale`) от единиц цены не зависит
            if len(hit) < len(paths):
                raise FactsError(
                    f"{where} «{axis.get('name')}»: ось двигает {', '.join(map(str, paths))} — "
                    f"переиндексируемые ({', '.join(map(str, hit))}) "
                    "вместе с другими; одним множителем её не перевести: разделить ось "
                    "или переиндексировать руками (автор книги)")
            for key in keys:
                value = axis.get(key)
                if _is_number(value):
                    axis[key] = value * factor
                elif isinstance(value, list) and value and all(_is_number(v) for v in value):
                    axis[key] = [v * factor for v in value]


def _drop_settled_rows(X: dict, settled: list[str]) -> list[str]:
    """Снять выплаченные строки моста и оси, которые двигают только их.

    После закрытия полугодия расчёта строка из моста снимается: выплата уже
    в якоре чистого долга (P2 §2.2). Ось на снятую строку считала бы то, чего
    нет; ось, которая двигает снятую строку вместе с другими, — решение автора
    книги (отказ).
    """
    if not settled:
        return []
    prefixes = tuple(f"bridge.items[{rid}]" for rid in settled)
    notes = []
    X["bridge"]["items"] = [row for row in X["bridge"]["items"] if row["id"] not in settled]
    for where, axes, _ in _axis_lists(X):
        keep = []
        for axis in axes:
            paths = _axis_paths(axis)
            hit = [p for p in paths if str(p).startswith(prefixes)]
            if not hit:
                keep.append(axis)
            elif len(hit) < len(paths):
                raise FactsError(f"{where} «{axis.get('name')}»: ось двигает выплаченную строку "
                                 f"моста ({', '.join(hit)}) вместе с другими — решение автора книги")
            else:
                notes.append(f"ось «{axis.get('name')}» ({where}) снята: строка моста выплачена")
        axes[:] = keep
    return [f"строки моста {', '.join(settled)} выплачены к концу полугодия — сняты с моста "
            "(выплата в чистом долге отчёта)"] + notes


def margin_tail_shock(A: dict, period: str, margin: float, se: float = 0.0) -> float:
    """Диагностика: AR(1)-хвост наблюдения маржи в следующем полугодии, средний по режимам.

    ρ × Σ P(r) × w × (наблюдение − цель_r − сезонность), w = σ²/(σ² + se²) —
    вес наблюдения против априорного N(0, σ²) (факт: se = 0, w = 1), при
    безусловных вероятностях режимов книги `A`. Несёт хвост ядро — правилом
    якоря (`model/core.py`), по каждому режиму; здесь — строка REANCHOR.md и
    ожидание маржи для `_demo_values`.
    """
    probs = regime_unconditional(A)
    rho = A["margin"]["deviation_persistence"]
    weight = observation_weight(A, se)
    return rho * weight * sum(probs[r] * (margin - path_value(spec["target"], period)
                                          - margin_season(A, period))
                              for r, spec in A["margin"]["regimes"].items())


def _demo_values(A_old: dict, A_new: dict, old_period: str, new_period: str) -> list[float]:
    """Сетка `demo_values` тем же шагом, сдвинутая на рост ожидаемой маржи.

    Ожидание первого прогнозного полугодия книги — как среднее ядра: Σ P(режим)
    × (цель + сезонность) при безусловных вероятностях режимов (у кандидата —
    с новым фактом), шок нау-каста и хвост наблюдения якоря
    (`margin_tail_shock`). Сдвиг округляется до шага сетки, чтобы таблица
    осталась «круглой»; число значений то же.
    """
    old = sorted(float(v) for v in (A_old["joint"]["regime_update"].get("demo_values") or []))
    if len(old) < 2:
        return old

    def expected(A: dict, p: str) -> float:
        probs = regime_unconditional(A)
        shock = float((A["margin"].get("nowcast_margin_shocks_pp") or {}).get(p, 0.0))
        anchor = previous_period(p)
        seen = margin_observations(A).get(anchor)
        tail = margin_tail_shock(A, anchor, seen[0], seen[1]) if seen else 0.0
        return shock + tail + sum(probs[r] * (path_value(spec["target"], p) + margin_season(A, p))
                                  for r, spec in A["margin"]["regimes"].items())

    step = min(b - a for a, b in zip(old, old[1:]))
    shift = expected(A_new, new_period) - expected(A_old, old_period)
    shift = round(shift / step) * step if step > 0 else 0.0
    return [round(v + shift, 6) for v in old]


def nwc_trajectory(A: dict, period: str | None = None) -> float:
    """ОК, которого ждёт траектория сетки в полугодии, — доля выручки LTM без
    июньского излишка: путь `nwc_pct` (`joint.by_world`) + ОК приобретённого
    периметра (`nwc.acquired_path`, доля выручки поверх `nwc_pct`).

    Разные пути миров с разным уровнем в этом полугодии — отказ: одним разрывом
    их не описать (решение автора книги).
    """
    period, N = period or A["meta"]["first_period"], A["nwc"]
    used = sorted({link["nwc"] for link in A["joint"]["by_world"].values()})
    levels = {k: path_value(N["nwc_pct"][k], period) for k in used}
    if max(levels.values()) - min(levels.values()) > 1e-12:
        raise FactsError(f"пути оборотного капитала сетки ({', '.join(used)}) на {period} "
                         f"разные ({levels}): разрыв факта с траекторией неоднозначен — "
                         "решает автор книги")
    acquired = path_value(N["acquired_path"], period) if "acquired_path" in N else 0.0
    return next(iter(levels.values())) + acquired


def nwc_gap(A: dict, start: float) -> float:
    """Факт оборотного капитала (доля выручки LTM без июньского излишка) минус
    траектория сетки на дату отчёта (`nwc_trajectory` закрываемого полугодия —
    первого прогнозного книги `A`): столько модель и ждала на дату отчёта."""
    return start - nwc_trajectory(A)


def _one_off(amount: float) -> str:
    """Разрыв × выручка LTM: факт выше траектории — высвобождение (приток), ниже — вложение."""
    return f"{'высвобождение' if amount > 0 else 'вложение'} ≈{abs(amount):.1f} млрд ₽"


def _shift_path(spec, delta: float):
    if isinstance(spec, dict):
        return {k: (v if k == "LT_from" else v + delta) for k, v in spec.items()}
    return spec + delta


def _window(old: dict, new: dict, need: tuple[str, ...], where: str) -> dict:
    """Окно полугодий той же длины: прежние значения, поверх — новые; последние по времени."""
    merged = {**old, **new}
    keep = sorted(merged, key=period_index)[-len(old):]
    out = {p: merged[p] for p in keep}
    for p in need:
        if p not in out:
            raise FactsError(f"факты: {where} без {p} — нужен для базы нового якоря")
    return out


def reanchor(A: dict, facts: dict, *, valuation_date: str | None = None,
             version: str | None = None, nwc_path: str | None = None) -> Reanchored:
    """Книга-кандидат, перезаякоренная на отчёт `facts` (формат — в шапке модуля).

    `nwc_path` — решение о разрыве факта оборотного капитала с траекторией
    (`hold` | `fact`, шапка модуля); без решения разрыв больше `NWC_GAP` — отказ.
    """
    if nwc_path is not None and nwc_path not in NWC_PATHS:
        raise FactsError(f"--nwc-path {nwc_path!r}: известны {', '.join(NWC_PATHS)}")
    validate_book(A)
    check_facts(A, facts)
    period = facts["period"]
    first_new = next_period(period)
    old_anchor = anchor_of(A)
    facts_date = period_end(period)
    boundary = facts_date + dt.timedelta(days=1)
    X = copy.deepcopy(A)
    F, F_old, M = X["facts"], A["facts"], X["meta"]
    warnings: list[str] = []
    derived: dict = {}

    # --- meta
    if period_index(period) >= period_index(M["last_period"]):
        raise FactsError(f"полугодие {period} — последнее прогнозное книги; горизонт кончился")
    M["first_period"] = first_new
    M["facts_date"] = facts_date.isoformat()
    vdate = valuation_date or facts.get("published") or boundary.isoformat()
    if dt.date.fromisoformat(vdate) <= facts_date:
        raise FactsError(f"дата оценки {vdate} не позже даты фактов {facts_date}")
    M.setdefault("curve_as_of", M["valuation_date"])   # миры и кривые — на старой дате
    M["valuation_date"] = vdate
    M["bridge_as_of"] = facts.get("bridge_as_of") or boundary.isoformat()
    M["version"] = version or f"{A['meta']['version']}+{period}"

    # --- сегменты: выручка (окно), площадь, когорты, эффективная площадь, индекс цен
    segs = segments(A)
    FS = facts["segments"]
    per_segment: dict[str, dict] = {}
    for seg in segs:
        fs, old, new = FS[seg.id], F_old["segments"][seg.id], F["segments"][seg.id]
        given = {p: float(v) for p, v in fs["revenue"].items()}
        new["revenue"] = _window(old["revenue"], given, (old_anchor, period),
                                 f"segments.{seg.id}.revenue")
        basis_given = {p: (fs.get("revenue_basis") or {}).get(p, "reported") for p in given}
        if "revenue_basis" in old or "revenue_basis" in fs:
            basis = {**(old.get("revenue_basis") or {}), **basis_given}
            new["revenue_basis"] = {p: basis[p] for p in new["revenue"] if p in basis}
        if "revenue_se" in old:
            se = {**old["revenue_se"],
                  **{p: float((fs.get("revenue_se") or {}).get(p, 0.0)) for p in given}}
            new["revenue_se"] = {p: se.get(p) for p in new["revenue"]}
        if "stores_end" in fs:
            new["stores_end"] = fs["stores_end"]
        elif old.get("stores_end") is not None:
            warnings.append(f"сегмент {seg.id}: stores_end не в файле фактов — оставлено "
                            f"{old['stores_end']} прошлого якоря (витрина)")
        info: dict = {"revenue": given[period]}
        if seg.network:
            opened, area_end = _number(fs, "opened_gross"), _number(fs, "area_end")
            step = effective_area_step(A, seg, opened, area_end)
            if "effective_area_avg" in fs:
                average = _number(fs, "effective_area_avg")
                end = 2.0 * average - step["start"]
                warnings.append(f"сегмент {seg.id}: эффективная площадь {period} — из файла "
                                f"фактов ({average:.2f}); правило книги дало бы {step['average']:.2f}")
                step.update(average=average, end=end, delta=end - step["reset"])
            new["area_end"] = area_end
            new["new_area_gross_hist"] = step["cohorts"]
            if step["dense"] or "new_area_dense_cohorts" in old:
                new["new_area_dense_cohorts"] = step["dense"]
            new["eff_area_end"] = step["end"]
            if seg.mode == "yoy" or "eff_area_avg_hist" in old:
                history = book_effective_hist(A, seg)
                new["eff_area_avg_hist"] = {old_anchor: history[old_anchor],
                                            period: step["average"]}
            if "closed_area_hist" in old:
                new.pop("closed_area_hist")
                warnings.append(f"сегмент {seg.id}: история эффективной площади нового якоря "
                                "записана числами (eff_area_avg_hist), closed_area_hist снят — "
                                "оси d и продуктивности закрытых двигают её только вперёд")
            info.update(area_end=area_end, opened=opened, closed=step["closed"],
                        effective_area_avg=step["average"], effective_area_delta=step["delta"])
        per_segment[seg.id] = info
    derived["segments"] = per_segment

    # --- выручка и EBITDA группы: окно той же длины, отчёт может пересчитать прошлое
    group_given = {p: float(v) for p, v in (facts.get("revenue") or {}).items()}
    restated = {p for fs in FS.values() for p in fs["revenue"]} - {period} - set(group_given)
    for p in sorted(restated, key=period_index):
        values = [F["segments"][s.id]["revenue"].get(p) for s in segs]
        if all(p in FS[s.id]["revenue"] for s in segs):
            group_given[p] = sum(values)
        elif p in F_old["revenue"] and all(v is not None for v in values) and \
                abs(sum(values) - F_old["revenue"][p]) > FACT_SUM_TOL * abs(F_old["revenue"][p]):
            names = ", ".join(s.id for s in segs if p in FS[s.id]["revenue"])
            raise FactsError(f"факты: пересчёт {p} у сегментов {names} без строки группы — "
                             f"сумма сегментов {sum(values)!r} ≠ выручке группы книги "
                             f"{F_old['revenue'][p]!r}: дайте revenue[{p}] или пересчёт всех "
                             "сегментов")
    group_given[period] = sum(F["segments"][s.id]["revenue"][period] for s in segs)
    F["revenue"] = _window(F_old["revenue"], group_given, (old_anchor, period), "revenue")
    F["ebitda_pre16"] = _window(F_old["ebitda_pre16"],
                                {p: float(v) for p, v in facts["ebitda_pre16"].items()},
                                (old_anchor, period), "ebitda_pre16")
    revenue, ebitda = F["revenue"][period], F["ebitda_pre16"][period]
    margin = ebitda / revenue
    derived["margin"] = margin
    if "margin_pre16" in facts and abs(_number(facts, "margin_pre16") - margin) > MARGIN_CHECK:
        raise FactsError(
            f"факты: margin_pre16 {facts['margin_pre16']:.4f} ≠ EBITDA/выручка "
            f"{ebitda}/{revenue} = {margin:.4f}: маржа после МСФО 16? годовая вместо "
            "полугодовой? Правило A-P2u судит полугодовую маржу ДО МСФО 16")
    targets = [path_value(spec["target"], period) + margin_season(A, period)
               for spec in A["margin"]["regimes"].values()]
    if not min(targets) - MARGIN_SANITY <= margin <= max(targets) + MARGIN_SANITY:
        raise FactsError(
            f"факты: маржа {period} {margin:.4f} вне [{min(targets) - MARGIN_SANITY:.4f}; "
            f"{max(targets) + MARGIN_SANITY:.4f}] вокруг целей режимов — EBITDA не до МСФО 16?")
    ltm_old = F_old["revenue"][previous_period(old_anchor)] + F_old["revenue"][old_anchor]
    ltm_new = F["revenue"][old_anchor] + revenue
    derived.update(revenue_ltm_old=ltm_old, revenue_ltm_new=ltm_new)
    if "reported" in F_old:
        stated = facts.get("reported") or {}
        for key in REPORTED_KEYS:
            if key in F_old["reported"]:
                given = {p: float(v) for p, v in (stated.get(key) or {}).items()}
                given.setdefault(period, F[key][period])
                F["reported"][key] = _window(F_old["reported"][key], given, (period,),
                                             f"reported.{key}")
        if not stated:
            warnings.append(f"facts.reported за {period} = факты файла (отчётный периметр = "
                            "периметр модели); иначе — ключ reported файла фактов")

    # --- факты якоря
    FA, FA_old = F["anchor"], F_old["anchor"]
    FA["period"] = period
    FA["ebitda_ltm"] = F["ebitda_pre16"][old_anchor] + ebitda
    if "ebitda_ltm" in facts and abs(_number(facts, "ebitda_ltm") - FA["ebitda_ltm"]) > LTM_CHECK:
        raise FactsError(f"факты: ebitda_ltm {facts['ebitda_ltm']} ≠ сумме полугодий "
                         f"{FA['ebitda_ltm']:.3f}")
    if "revenue_ltm" in FA_old:
        FA["revenue_ltm"] = ltm_new
    if "ebitda_ltm_reported" in FA_old:
        # отчётная EBITDA LTM рядом с проформой (решение ведущего 12) — из окна reported
        reported = F["reported"]["ebitda_pre16"]
        FA["ebitda_ltm_reported"] = reported[old_anchor] + reported[period]
    pro_forma = None
    if "margin_pro_forma" in FA_old:
        pro_forma = (float(FA_old["margin_pro_forma"]), float(FA_old["margin_pro_forma_se"]))
        FA.pop("margin_pro_forma")
        FA.pop("margin_pro_forma_se")
        derived["pro_forma"] = dict(period=old_anchor, value=pro_forma[0], se=pro_forma[1])
    FA["da_pre16"] = _number(facts, "da_pre16")
    FA["capex"] = _number(facts, "capex")
    FA["net_debt"] = _number(facts, "net_debt")
    FA["cash"] = _number(facts, "cash")
    if "shares_out_mln" in facts:
        F["shares_out_mln"] = _number(facts, "shares_out_mln")
    if "undrawn_credit_lines" in facts:
        F["undrawn_credit_lines"] = _number(facts, "undrawn_credit_lines")

    # --- годовое правило лестницы: FCFE года нового якоря по якорь включительно
    if dividend_timing(A) is not None:
        carry = fcfe_ytd_after(A, facts, ltm_old, ltm_new)
        if carry is None:
            FA.pop("fcfe_ytd", None)
        else:
            FA["fcfe_ytd"] = carry
        derived["fcfe_ytd"] = carry

    # --- пулы убытков
    locked = acquired_nol(A)
    rule = None
    if "net_interest" in facts:
        rule = tax_loss_pools_by_rule(A, period, revenue, ebitda, FA["da_pre16"], FA["capex"],
                                      _number(facts, "net_interest"))
        derived["tax_loss_pool_by_rule"] = rule[0]
    X["tax"]["nol_start"] = _number(facts, "tax_loss_pool") if "tax_loss_pool" in facts else rule[0]
    if locked:
        by_rule = acquired_pool_by_rule(A, period)
        derived["tax_loss_pool_acquired_by_rule"] = by_rule
        LN = X["tax"]["acquired_nol"]
        LN["amount"] = (_number(facts, "tax_loss_pool_acquired")
                        if "tax_loss_pool_acquired" in facts else by_rule)
        # Прибавки запертых юрлиц за закрытые полугодия уже в пулах на дату отчёта
        # (и вне горизонта кандидата — отказ книги).
        addback = LN["discount_rule"].get("locked_addback") or {}
        LN["discount_rule"]["locked_addback"] = {
            p: v for p, v in addback.items() if period_index(p) > period_index(period)}

    # --- оборотный капитал: уровень на дату и индекс июньского излишка
    factor_w2 = ltm_new / ltm_old
    for dotted in REVENUE_INDEXED:
        _set(X, dotted, _get(A, dotted) * factor_w2)
    _scale_axes(X, REVENUE_INDEXED, factor_w2)
    june = X["nwc"]["seasonal_june_excess"]
    level = _number(facts, "nwc_to_revenue") * ltm_new
    start = (level - (june if period.endswith("H1") else 0.0)) / ltm_new
    if "anchor_level" in X["nwc"]:
        X["nwc"]["anchor_level"] = level
    else:
        X["nwc"]["nwc_pct_start"] = start
    derived.update(revenue_index_factor=factor_w2, nwc_level=level, nwc_start=start)
    gap = nwc_gap(A, start)
    derived.update(nwc_gap=gap, nwc_one_off=gap * ltm_new, nwc_path=nwc_path)
    if abs(gap) > NWC_GAP and nwc_path is None:
        raise FactsError(
            f"оборотный капитал: факт {start:.4%} выручки LTM (без июньского излишка) против "
            f"траектории книги {start - gap:.4%} на {period} — разрыв "
            f"{gap * 100:+.2f} п.п. (порог {NWC_GAP * 100:.1f}); без решения он станет разовым "
            f"потоком в {first_new}: {_one_off(gap * ltm_new)}. Нужно явное решение: "
            "--nwc-path hold (траектории книги, разовый поток) или --nwc-path fact "
            "(траектории сдвигаются на разрыв)")
    if nwc_path == "fact" and gap:
        X["nwc"]["nwc_pct"] = {k: _shift_path(v, gap) for k, v in X["nwc"]["nwc_pct"].items()}

    # --- цены capex: из цен старого якоря в цены нового
    cpi = _number(facts, "cpi_half") if "cpi_half" in facts else book_cpi(A, period)
    if "cpi_half" not in facts:
        warnings.append(f"ИПЦ {period} для переиндексации цен capex — из миров книги "
                        f"({cpi:.4f}); внесите факт Росстата ключом cpi_half")
    factor_k = 1.0 + half_rate(cpi)
    prices = price_indexed(A)
    for dotted in prices:
        _set(X, dotted, _scaled(_get(A, dotted), factor_k))
    _scale_axes(X, prices, factor_k)
    derived.update(cpi_half=cpi, price_index_factor=factor_k)
    for seg in segs:
        if seg.mode == "level":
            info = per_segment[seg.id]
            info["density_fact"] = level_density(A, seg, period, info["revenue"],
                                                 info["effective_area_avg"], factor_k)
            info["density_book"] = path_value(seg.spec["density_path"], period)
    carry_capex_rules(A, X, factor_k, ltm_new)

    # --- мост: суммы на дату отчёта, выплаченные строки сняты
    rows = {row["id"]: row for row in X["bridge"]["items"]}
    settled = [item.id for item in bridge_items(A) if item.settle_period is not None
               and period_index(item.settle_period) <= period_index(period)]
    for key, value in facts["bridge"].items():
        rows[key]["amount"] = float(value)
        rows[key]["as_of"] = M["bridge_as_of"]   # названа на дату моста кандидата
    unchanged = [k for k in rows if k not in facts["bridge"] and k not in settled]
    if unchanged:
        warnings.append("строки моста без факта (оставлены как в книге, со своей датой as_of): "
                        + ", ".join(unchanged))
    for item in bridge_items(A):
        if (item.settle_period is not None and item.id not in settled
                and not rows[item.id]["amount"]):
            warnings.append(f"строка моста {item.id} = 0, а выплата по книге — в "
                            f"{item.settle_period}: проверить")
    warnings += _drop_settled_rows(X, settled)

    # --- наблюдения: факт маржи — один раз; проформа старого якоря — наблюдением
    update = X["joint"].setdefault("regime_update", {})
    observations = dict(update.get("observations") or {})
    if pro_forma is not None:
        observations[old_anchor] = ({"value": pro_forma[0], "se": pro_forma[1]} if pro_forma[1]
                                    else pro_forma[0])
        warnings.append(f"маржа якоря {old_anchor} на проформе ({pro_forma[0]:.5f}, se "
                        f"{pro_forma[1]:.5f}) перенесена из facts.anchor в наблюдения A-P2u")
    replaced = observations.get(period)
    if isinstance(replaced, dict):
        warnings.append(f"прогноз нау-каста за {period} ({replaced}) заменён фактом")
    elif replaced is not None and abs(float(replaced) - margin) > 1e-12:
        warnings.append(f"факт маржи {period} в книге ({replaced}) заменён фактом файла ({margin:.6f})")
    observations[period] = margin
    update["observations"] = {p: observations[p] for p in sorted(observations, key=period_index)}
    shocks = X["margin"].get("nowcast_margin_shocks_pp") or {}
    X["margin"]["nowcast_margin_shocks_pp"] = {
        p: v for p, v in shocks.items() if period_index(p) > period_index(period)}
    derived["margin_tail_shock"] = margin_tail_shock(X, period, margin)

    # --- «что даст ближайший отчёт»
    update["demo_period"] = first_new
    update["demo_values"] = _demo_values(A, X, update_period(A), first_new)

    validate_book(X)
    warnings += manual_steps(A, X, derived)
    return Reanchored(book=X, period=period, derived=derived,
                      changes=diff(A, X), warnings=warnings)


def fcfe_ytd_after(A: dict, facts: dict, ltm_old: float, ltm_new: float) -> float | None:
    """`facts.anchor.fcfe_ytd` нового якоря — FCFE финансового года по закрытое
    полугодие включительно (годовое правило лестницы, `model.book.dividend_timing`).

    FCFE закрытого полугодия в определении ядра (ЧД(p−1) − ЧД до дивидендов по
    модельному ЧД): ЧД старого якоря − отчётный ЧД + выплаченные за полугодие
    дивиденды − прирост операционной кассы operating_cash_pct × (LTM₁ − LTM₀) —
    ядро вычитает его из FCFF, а в отчётном ЧД он остаётся кассой. Якорь на 1П
    открывает год — FCFE полугодия; на 2П — плюс FCFE 1П (`fcfe_ytd` старого
    якоря). `fcfe_ytd` файла фактов — как есть. Нет FCFE 1П у старого якоря:
    если выплата по году нового якоря в горизонте выплат — отказ, иначе None.
    """
    if "fcfe_ytd" in facts:
        return _number(facts, "fcfe_ytd")
    period = facts["period"]
    half = (A["facts"]["anchor"]["net_debt"] - _number(facts, "net_debt")
            + _number(facts, "dividends_paid")
            - A["financing"]["operating_cash_pct"] * (ltm_new - ltm_old))
    if period.endswith("H1"):
        return half
    before = A["facts"]["anchor"].get("fcfe_ytd")
    if before is None:
        if int(period[:4]) + 1 >= A["financing"]["dividends_from_year"]:
            raise FactsError(
                f"годовое правило лестницы: у книги нет facts.anchor.fcfe_ytd (FCFE "
                f"1П{period[:4]}), а выплата по {period[:4]} г. в горизонте выплат — дайте "
                "fcfe_ytd файлом фактов (FCFE года по закрытое полугодие)")
        return None
    return float(before) + half


def carry_capex_rules(A: dict, X: dict, factor_k: float, ltm_new: float) -> None:
    """Правила capex продолжаются через якорь без скачка.

    Физическая доля поддерживающего capex меряется интенсивностью x = площадь
    группы (сумма сегментов с площадью) × индекс цен / выручка LTM к базе x₀
    (`capex.maintenance_area_base`). Без ключа база — x первого прогнозного
    полугодия, то есть закрываемого; инструмент записывает её явно в ценах
    НОВОГО якоря (индекс ядра снова стартует с единицы, поэтому база делится
    на множитель цен полугодия).

    Линейная D&A помнит базу якоря и когорты capex (`facts.da_straight_line`):
    на новом якоре база списывается дальше, а не заново. База сверяется с
    фактом: D&A закрываемого полугодия по правилу = D&A отчёта.
    """
    rules = capex_network_rules(A)
    F_old, F = A["facts"], X["facts"]
    if rules.maintenance_area_share:
        network = [s.id for s in segments(A) if s.network]
        area0 = sum(F_old["segments"][sid]["area_end"] for sid in network)
        area1 = sum(F["segments"][sid]["area_end"] for sid in network)
        base = (rules.maintenance_area_base if rules.maintenance_area_base is not None
                else (area0 + area1) / 2.0 * factor_k / ltm_new)
        X["capex"]["maintenance_area_base"] = base / factor_k
    half_life = 2 * A["capex"]["asset_life_years"]
    legacy, halves, vintages = rules.da_state or (F_old["anchor"]["da_pre16"], 0, ())
    vintages, halves = list(vintages) + [F_old["anchor"]["capex"]], halves + 1
    booked = sum(v * min(1.0, max(0.0, half_life - age))
                 for age, v in enumerate(reversed(vintages))) / half_life
    remaining = max(0.0, 1 - halves / half_life)
    if remaining:
        legacy = max(0.0, (F["anchor"]["da_pre16"] - booked) / remaining)
    F["da_straight_line"] = {"legacy": legacy, "legacy_halves": halves,
                             "vintages": vintages[-math.ceil(half_life):]}


def update_period(A: dict) -> str:
    """Период «что даст отчёт» старой книги (для сдвига сетки demo_values)."""
    return A["joint"]["regime_update"].get("demo_period") or A["meta"]["first_period"]


def manual_steps(A: dict, X: dict, derived: dict | None = None) -> list[str]:
    """Что инструмент сознательно НЕ делает — это суждения или другие потоки."""
    out = []
    gap = (derived or {}).get("nwc_gap", 0.0)
    if abs(gap) > NWC_GAP:
        decision = derived.get("nwc_path")
        out.append(
            f"**ОБОРОТНЫЙ КАПИТАЛ: РАЗРЫВ ФАКТА С ТРАЕКТОРИЕЙ {gap * 100:+.2f} п.п.** "
            f"(факт {derived['nwc_start']:.4%}, траектория книги "
            f"{derived['nwc_start'] - gap:.4%}; порог {NWC_GAP * 100:.1f} п.п.). Решение "
            f"--nwc-path {decision}: "
            + ("траектории книги оставлены — разрыв идёт РАЗОВЫМ потоком в "
               f"{X['meta']['first_period']}: {_one_off(derived['nwc_one_off'])}"
               if decision == "hold" else
               "все траектории nwc_pct сдвинуты на разрыв — уровень факта держится")
            + ". Подтвердить в журнале версии книги (автор книги)")
    out.append(f"миры и кривые — на {A['meta']['valuation_date']}: пересборка "
               "ops/tools/refresh_worlds.py отдельно")
    for sid, info in ((derived or {}).get("segments") or {}).items():
        if "density_fact" in info and \
                abs(info["density_fact"] / info["density_book"] - 1) > DENSITY_CHECK:
            out.append(f"сегмент {sid}: плотность факта {info['density_fact']:.2f} против книги "
                       f"{info['density_book']:.2f} тыс. ₽/м² (в ценах старого якоря) — "
                       "density_path — суждение книги: пересмотреть (автор книги)")
    moved = ("tax.nol_start", "tax.acquired_nol.amount")
    for _, axes, _ in _axis_lists(X):
        for axis in axes:
            path = axis.get("path") or (axis.get("paths") or [None])[0]
            if not (path in moved or str(path).startswith("bridge.items[")):
                continue
            was, now = _get(A, path), _get(X, path)
            if was != now:
                out.append(f"ось «{axis['name']}» [{axis.get('low')}; {axis.get('high')}] задана "
                           f"вокруг значения старой книги ({was}); новое — {now:.3f}: "
                           "пересмотреть диапазон")
    out.append("объяснения гейтов (data/assumptions/gate_explanations.yaml): массы и сроки — "
               "на новой книге")
    out.append("таблицы книги: python -B -m model.book_results → data/assumptions/results.json "
               "и run_output.txt; текст книги и docs/ — по списку «что сдвинулось» (перенос в "
               "канон — автор книги)")
    return out


# --------------------------------------------------------------------- дифф


def diff(a, b, prefix: str = "") -> list[tuple[str, object, object]]:
    """Все листья, которые изменились: (путь, было, стало).

    Списки строк с `id` (мост) сравниваются по `id`: снятая строка — одна
    запись «было → нет», а не сдвиг всех следующих.
    """
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for key in list(a) + [k for k in b if k not in a]:
            path = f"{prefix}.{key}" if prefix else str(key)
            if key not in b:
                out.append((path, a[key], None))
            elif key not in a:
                out.append((path, None, b[key]))
            else:
                out += diff(a[key], b[key], path)
        return out
    if (isinstance(a, list) and isinstance(b, list) and a and b
            and all(isinstance(x, dict) and "id" in x for x in a + b)):
        return diff({x["id"]: x for x in a}, {x["id"]: x for x in b}, prefix)
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b) \
            and all(isinstance(x, dict) for x in a):
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            out += diff(x, y, f"{prefix}[{x.get('name', i)}]")
        return out
    return [] if a == b else [(prefix, a, b)]


# ------------------------------------------------ ожидание модели и перекат


def rolled(A: dict, valuation_date: str, observations: dict | None = None) -> dict:
    """Та же книга, прокатанная к дате оценки (без перезаякоривания).

    Строки моста наращиваются от своей даты `as_of`: прокатка её не трогает.
    """
    X = copy.deepcopy(A)
    X["meta"]["valuation_date"] = valuation_date
    X["meta"].setdefault("curve_as_of", A["meta"]["valuation_date"])   # кривые — книжные
    if observations:
        update = X["joint"].setdefault("regime_update", {})
        update["observations"] = {**(update.get("observations") or {}), **observations}
    return X


def _cell_report(A: dict, cell: Cell, result) -> dict:
    """Отчёт за первое прогнозное полугодие, как его дал бы путь клетки (плоский словарь)."""
    F, FN, N = A["facts"], A["financing"], A["nwc"]
    period, anchor = A["meta"]["first_period"], anchor_of(A)
    row = result.rows[0]
    assert row.period == period
    ltm0 = F["revenue"][previous_period(anchor)] + F["revenue"][anchor]
    ltm1 = row.revenue + F["revenue"][anchor]
    acquired = path_value(N["acquired_path"], period) if "acquired_path" in N else 0.0
    nwc = ((path_value(N["nwc_pct"][cell.nwc], period) + acquired) * ltm1
           + (N["seasonal_june_excess"] * ltm1 / ltm0 if period.endswith("H1") else 0.0))
    out = dict(
        ebitda=row.ebitda, da=row.da, capex=row.capex,
        # отчётный чистый долг: модельный минус прирост операционной кассы —
        # прирост вычтен из FCFF как отток, но деньги остались в кассе
        net_debt=row.net_debt - FN["operating_cash_pct"] * (ltm1 - ltm0),
        cash=row.cash, pool=row.tax_loss_pool, pool_acquired=row.tax_loss_pool_acquired,
        dividends=row.dividends,
        nwc=nwc / ltm1, cpi=path_value(A["worlds"][cell.world]["cpi"], period))
    for sid, step in row.segments.items():
        out[f"segments.{sid}.revenue"] = step.revenue
        if step.area_end is not None:
            out[f"segments.{sid}.area_end"] = step.area_end
            out[f"segments.{sid}.opened_gross"] = step.opened
    return out


def expected_report(A: dict, *, layer: str = "analytical", cell: Cell | None = None) -> dict:
    """Синтетический отчёт за первое прогнозное полугодие = ожидание модели.

    `cell` — путь одной клетки (точная проверка механики, без неравенства
    Йенсена); иначе — среднее по клеткам слоя `layer` с его вероятностями
    (`analytical` — «свой макро-взгляд», `macro_neutral` — рыночные ставки).
    Строки моста с наращением — суммы книги, наращённые правилом ядра до
    границы полугодия (0 после выплаты); остальные — как в книге.

    Неиспользованные линии — остаток лимита книги: лимит «валовой долг
    отчётной даты + неиспользованные линии» у ядра один на весь путь (гейт
    `credit_lines`, триггер издержек неустойчивости), поэтому по ожиданию
    модели на следующую отчётную дату линий остаётся «лимит минус ожидаемый
    валовой долг». Иначе погашения из кассы сжимали бы лимит кандидата, и
    клетка у порога перескакивала бы через триггер при неизменной экономике.
    Настоящий отчёт называет линии сам.
    """
    period, anchor = A["meta"]["first_period"], anchor_of(A)
    if cell is not None:
        pairs = [(cell, 1.0, run_cell(A, cell))]
    else:
        variants = layer_variants(A, build_grid(A))
        pairs = [(c.cell, c.probability, c.result) for c in variants[layer]]
    total = sum(w for _, w, _ in pairs)
    avg: dict[str, float] = {}
    for c, w, result in pairs:
        for key, value in _cell_report(A, c, result).items():
            avg[key] = avg.get(key, 0.0) + w / total * value
    boundary = period_end(period) + dt.timedelta(days=1)
    bridge = {}
    for item in bridge_items(A):
        if item.settle_period is not None and period_index(item.settle_period) <= period_index(period):
            bridge[item.id] = 0.0
        else:
            bridge[item.id] = accreted(A, item, boundary)
    report_segments = {}
    for seg in segments(A):
        block = {"revenue": {period: avg[f"segments.{seg.id}.revenue"]}}
        if seg.network:
            block.update(area_end=avg[f"segments.{seg.id}.area_end"],
                         opened_gross=avg[f"segments.{seg.id}.opened_gross"])
        report_segments[seg.id] = block
    report = {
        "schema": SCHEMA, "period": period,
        "note": (f"синтетический отчёт: ожидание модели книги {A['meta']['version']} "
                 f"({'клетка ' + cell.key if cell else 'слой ' + layer}) — сухой прогон, не факты"),
        "segments": report_segments,
        "ebitda_pre16": {period: avg["ebitda"]},
        "da_pre16": avg["da"], "capex": avg["capex"], "net_debt": avg["net_debt"],
        "cash": avg["cash"], "tax_loss_pool": avg["pool"], "nwc_to_revenue": avg["nwc"],
        "cpi_half": avg["cpi"],
        "ebitda_ltm": A["facts"]["ebitda_pre16"][anchor] + avg["ebitda"],
        "bridge": bridge,
        "undrawn_credit_lines": credit_limit(A) - (avg["net_debt"] + avg["cash"]),
    }
    if acquired_nol(A):
        report["tax_loss_pool_acquired"] = avg["pool_acquired"]
    if dividend_timing(A) is not None:
        report["dividends_paid"] = avg["dividends"]
    return report


# ---------------------------------------------------------------- заголовок


def headline(A: dict) -> dict:
    """Печатаемая точка по λ, границы, V0 и D слоёв, цена 1 % EV центра."""
    cells = build_grid(A)
    lm = layers(A, cells)
    fv = fair_value(A, cells, lm)
    return dict(low=fv.low, central=fv.central, high=fv.high,
                v0={k: v.v0 for k, v in lm.items()}, claims={k: v.claims for k, v in lm.items()},
                rub_per_1pct_ev=fv.center_ev.rub_per_1pct_ev if fv.center_ev else None)


def cell_mechanics(A: dict, valuation_date: str) -> dict:
    """Механика на путях клеток: каждая клетка перезаякорена на отчёт СВОЕГО пути.

    Строка «механика» раскладки сравнивает кандидата на усреднённом отчёте с
    прокатанной книгой, и в ней, кроме механики, — неравенство Йенсена: отчёт —
    одно число на все клетки, а прокатанная книга ведёт каждую своим путём (у
    книги, где сеть или спрос закрываемого полугодия различаются по мирам и
    режимам, — заметно). Здесь Йенсена нет: EV и требования каждой клетки
    против прокатанной, худшая — в долях допуска инварианта (> 1 — механика
    сломана).
    """
    worst, text = 0.0, ""
    cells = all_cells(A)
    base = rolled(A, valuation_date)
    for cell in cells:
        X = reanchor(A, expected_report(A, cell=cell), valuation_date=valuation_date).book
        was = run_cell(base, cell)
        now = run_cell(X, Cell.build(X, cell.world, cell.margin_regime, cell.capex))
        allowed = CELL_EV_TOLERANCE * abs(was.ev) + CELL_EV_FLOOR
        ratio = max(abs(now.ev - was.ev) / allowed,
                    abs(now.claims - was.claims) / CELL_CLAIMS_TOLERANCE)
        if ratio >= worst:
            worst, text = ratio, (f"{cell.key}: EV {was.ev:.3f} → {now.ev:.3f}, требования "
                                  f"{was.claims:.4f} → {now.claims:.4f}")
    return dict(cells=len(cells), worst=worst, text=text)


def attribution(A: dict, cand: Reanchored, band_draws: int | None = None) -> dict:
    """Скачок «прокатанная книга → кандидат» по трём причинам.

    выпуклость — прокатанная книга + ожидаемый факт маржи как наблюдение
                 минус прокатанная без наблюдения: реакция правила A-P2u на
                 факт, РАВНЫЙ ожиданию (не новость, а выпуклость);
    механика   — кандидат на синтетическом отчёте «факты = ожидание» минус
                 прокатанная книга с тем же наблюдением: должна быть ≈ 0
                 (тест `tests/test_reanchor.py`);
    сюрприз    — кандидат на фактах минус кандидат на ожидании: то, что
                 отчёт сообщил нового.

    Всегда — точка при центральных значениях книги (`headline`). ЗАГОЛОВОК —
    медиана полосы и её 10/90 %; они считаются только при `band_draws`: полоса
    на каждой из четырёх книг с одними точками гиперкуба.
    """
    vdate = cand.book["meta"]["valuation_date"]
    expected = expected_report(A)
    mech = reanchor(A, expected, valuation_date=vdate, version=cand.book["meta"]["version"],
                    nwc_path=cand.derived.get("nwc_path"))
    books = dict(rolled=rolled(A, vdate),
                 rolled_expected_fact=rolled(A, vdate, {cand.period: mech.derived["margin"]}),
                 mechanics=mech.book, candidate=cand.book)
    out = dict(valuation_date=vdate, expected_margin=mech.derived["margin"], band_draws=band_draws,
               cell_mechanics=cell_mechanics(A, vdate))
    for key, B in books.items():
        out[key] = headline(B)
        if band_draws:
            un = uncertainty(copy.deepcopy(B), draws=band_draws)
            out[key]["band"] = {**{q: un["central"][q] for q in ("0.10", "0.50", "0.90")},
                                "p_below": un["p_central_below_market"]}
    return out


# ------------------------------------------------------------------- отчёт


def _fmt(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{k}: {_fmt(v)}" for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(_fmt(v) for v in value) + "]"
    return str(value)


def _segment_lines(cand: Reanchored) -> list[str]:
    lines = ["", "## Сеть по сегментам", "",
             "| сегмент | выручка | площадь | открытия | закрытия | эфф. площадь (средняя) | "
             "δ сброса | плотность факт / книга |", "|---|---|---|---|---|---|---|---|"]
    for sid, s in cand.derived["segments"].items():
        cells = [f"{s['revenue']:.3f}"]
        for key, fmt in (("area_end", ".2f"), ("opened", ".2f"), ("closed", ".2f"),
                         ("effective_area_avg", ".2f"), ("effective_area_delta", ".3f")):
            # ноль округления суммы (−1e-14) печатается нулём, а не «−0.00»
            cells.append(format(s[key] if abs(s[key]) > 1e-9 else 0.0, fmt) if key in s else "—")
        cells.append(f"{s['density_fact']:.2f} / {s['density_book']:.2f}"
                     if "density_fact" in s else "—")
        lines.append(f"| {sid} | " + " | ".join(cells) + " |")
    lines += ["", "Уровень индекса эффективной площади перенесён по каждому сегменту "
              "(`eff_area_end`): δ — на сколько сдвинулся бы ряд при сбросе к «физическая − "
              "незрелая». Плотность сегмента `level` (`density_path`, тыс. ₽/м² в год, в "
              "ценах старого якоря) — суждение книги; переиндексирована в цены нового якоря "
              "вместе с ценами capex."]
    return lines


def report_text(A: dict, cand: Reanchored, attr: dict | None) -> str:
    X, d = cand.book, cand.derived
    lines = [f"# Перезаякоривание книги {A['meta']['version']} на отчёт {cand.period}", "",
             f"Кандидат: версия {X['meta']['version']}, первое прогнозное полугодие "
             f"{X['meta']['first_period']}, факты на {X['meta']['facts_date']}, дата оценки "
             f"{X['meta']['valuation_date']}, мост на {X['meta']['bridge_as_of']}.", "",
             "## Производные величины", "",
             f"- маржа {cand.period} до МСФО 16 = EBITDA / выручка группы = {d['margin']:.5f} "
             "(в `joint.regime_update.observations`)",
             f"- выручка LTM: {d['revenue_ltm_old']:.3f} → {d['revenue_ltm_new']:.3f} "
             f"(множитель июньского излишка {d['revenue_index_factor']:.6f})",
             f"- ИПЦ полугодия {d['cpi_half']:.4f} → множитель цен якоря (открытия сегментов, "
             f"инфраструктура, плотность сегментов level) {d['price_index_factor']:.6f}"]
    lines.append(f"- ОК на дату {d['nwc_level']:.3f} млрд ₽; без июньского излишка — "
                 f"{d['nwc_start']:.4%} выручки LTM; разрыв с траекторией сетки (`nwc_pct` + "
                 f"`acquired_path`) {d['nwc_gap'] * 100:+.3f} п.п.")
    if "tax_loss_pool_by_rule" in d:
        lines.append(f"- пул убытков по правилу книги: {d['tax_loss_pool_by_rule']:.3f}")
    if "tax_loss_pool_acquired_by_rule" in d:
        lines.append(f"- запертые убытки приобретённых юрлиц по правилу (прибавки до "
                     f"присоединения, переход в пул группы): "
                     f"{d['tax_loss_pool_acquired_by_rule']:.3f}")
    if "pro_forma" in d:
        pf = d["pro_forma"]
        lines.append(f"- маржа якоря {pf['period']} на проформе {pf['value']:.5f} (se "
                     f"{pf['se']:.5f}) — из `facts.anchor` в наблюдения A-P2u")
    lines.append(f"- AR(1)-хвост факта маржи в {X['meta']['first_period']}: "
                 f"{d['margin_tail_shock'] * 100:+.3f} п.п. (средний по режимам) — "
                 "переносит ядро (правило якоря)")
    lines += _segment_lines(cand)
    lines += ["", "## Что сдвинулось", "", "| путь | было | стало |", "|---|---|---|"]
    lines += [f"| `{p}` | {_fmt(a)} | {_fmt(b)} |" for p, a, b in cand.changes]
    if attr:
        steps = (("прокатанная книга (без факта)", "rolled", None),
                 (f"+ ожидаемый факт {attr['expected_margin']:.4%} как наблюдение (выпуклость)",
                  "rolled_expected_fact", "rolled"),
                 ("перезаякоривание на ожидании (механика, ≈0)", "mechanics", "rolled_expected_fact"),
                 ("кандидат на фактах (сюрприз факта)", "candidate", "mechanics"))
        lines += ["", f"## Точка при центральных значениях книги на {attr['valuation_date']} (ядро)", "",
                  "Низ, центр по λ и верх при значениях суждений книги (`fair_value`); "
                  "ЗАГОЛОВОК — медиана полосы, она в следующей таблице.", "",
                  "| шаг | низ | центр | верх | V0 свой | V0 рыночные ставки |", "|---|---|---|---|---|---|"]

        def row(name: str, h: dict, ref: dict | None) -> str:
            cells = [h["low"], h["central"], h["high"], h["v0"]["analytical"], h["v0"]["macro_neutral"]]
            if ref is None:
                return f"| {name} | " + " | ".join(f"{x:.1f}" for x in cells) + " |"
            base = [ref["low"], ref["central"], ref["high"], ref["v0"]["analytical"],
                    ref["v0"]["macro_neutral"]]
            return f"| {name} | " + " | ".join(f"{x:.1f} ({x - y:+.1f})" for x, y in zip(cells, base)) + " |"

        lines += [row(name, attr[key], attr[ref] if ref else None) for name, key, ref in steps]
        cm = attr.get("cell_mechanics")
        if cm:
            lines += ["", f"Механика на путях клеток (каждая из {cm['cells']} клеток "
                      f"перезаякорена на отчёт своего пути): худшая — {cm['text']}, "
                      f"{cm['worst']:.2f} допуска (EV 0,1 % + 0,02 млрд ₽, требования 2 млн ₽). "
                      "Больше 1 — клетка разошлась с перекатом: ошибка механики или порог "
                      "издержек неустойчивости у клетки на грани (целевой рычаг и ступени "
                      "дивидендов ядро меряет по модельному чистому долгу, а перезаякоривание "
                      "начинает его с отчётного — сдвиг на прирост операционной кассы за "
                      "полугодие). Остаток строки «механика» сверх этого — неравенство Йенсена: "
                      "отчёт — одно число на все клетки."]
        lines += ["", "## Заголовок — медиана полосы", ""]
        if attr.get("band_draws"):

            def band_row(name: str, h: dict, ref: dict | None) -> str:
                b = h["band"]
                cells = [b["0.10"], b["0.50"], b["0.90"]]
                if ref is None:
                    return (f"| {name} | " + " | ".join(f"{x:.1f}" for x in cells)
                            + f" | {b['p_below']:.1%} |")
                r = ref["band"]
                return (f"| {name} | " + " | ".join(f"{x:.1f} ({x - y:+.1f})" for x, y in
                                                     zip(cells, [r["0.10"], r["0.50"], r["0.90"]]))
                        + f" | {b['p_below']:.1%} ({(b['p_below'] - r['p_below']) * 100:+.1f} п.п.) |")

            lines += [f"{attr['band_draws']} прогонов на книгу, одни точки гиперкуба (сравнение "
                      "парное). Полоса 80 % — от 10 % до 90 %.", "",
                      "| шаг | 10 % | медиана | 90 % | P(центр < рынка) |", "|---|---|---|---|---|"]
            lines += [band_row(name, attr[key], attr[ref] if ref else None) for name, key, ref in steps]
        else:
            lines.append("Не считалась: раскладка печатаемого заголовка — ключом `--band 2000` "
                         "(четыре полосы). Инвариант заголовка на ожидании держит тест "
                         "`test_reanchoring_on_the_expected_path_keeps_the_printed_headline`.")
    lines += ["", "## Предупреждения и ручные шаги", ""] + [f"- {w}" for w in cand.warnings]
    lines += ["", "Механика ≈ 0 — это инвариант tests/test_reanchor.py: клетка на своём пути "
              "сохраняет EV до 0,1 % и требования до 2 млн ₽; слой — точку до четверти цены "
              "1 % EV и V0 до ±1 % (до 0,1 %, когда сеть и спрос закрываемого полугодия у "
              "клеток одни); заголовок (медиана и полоса 80 %) — до цены 1 % EV. Остаток "
              "«механики» в таблице — неравенство Йенсена (отчёт — одно число, а прокатанная "
              "книга ведёт каждую клетку своим путём: сценарии роста сети по мирам, спрос по "
              "режимам) и пороги (лимит линий и порог ЧД/EBITDA — через дивиденды, которые "
              "ядро меряет по модельному чистому долгу; ступени лестницы).", ""]
    return "\n".join(lines)


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def write_candidate(out: Path, A: dict, cand: Reanchored, attr: dict | None) -> None:
    if _inside(out, BOOK):
        raise FactsError(f"--out {out} внутри канона книги {BOOK}: кандидат в канон "
                         "переносит автор книги")
    out.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(cand.book, allow_unicode=True, sort_keys=False, width=100000)
    if yaml.safe_load(text) != cand.book:
        raise FactsError("кандидат не переживает YAML туда-обратно — отказ записи")
    (out / "assumptions.yaml").write_text(text, encoding="utf-8")
    (out / "assumptions.json").write_text(json.dumps(cand.book, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
    (out / "REANCHOR.md").write_text(report_text(A, cand, attr), encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--book", type=Path, default=BOOK_YAML)
    ap.add_argument("--facts", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--valuation-date")
    ap.add_argument("--version")
    ap.add_argument("--expected", type=Path,
                    help="записать синтетический отчёт «факты = ожидание модели» и выйти")
    ap.add_argument("--nwc-path", choices=NWC_PATHS,
                    help="решение о разрыве факта оборотного капитала с траекторией книги "
                         "больше 0,2 п.п.: hold — разовый поток, fact — траектории сдвигаются "
                         "(без решения при разрыве — отказ)")
    ap.add_argument("--no-attribution", action="store_true")
    ap.add_argument("--band", type=int, metavar="ПРОГОНОВ",
                    help="раскладка печатаемого заголовка (медиана полосы) на ПРОГОНОВ "
                         "прогонах каждой из четырёх книг; для отчёта — 2000")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        A = load_book(args.book)
        if args.expected:
            # Сухой прогон пишет, например, в var/reanchor/, которого в свежей
            # выкладке нет (var/ в git пуст): каталог создаётся, как у --out.
            # В канон книги синтетический отчёт не пишется — так же, как кандидат.
            if _inside(args.expected, BOOK):
                raise FactsError(f"--expected {args.expected} внутри канона книги {BOOK}")
            args.expected.parent.mkdir(parents=True, exist_ok=True)
            args.expected.write_text(json.dumps(expected_report(A),
                                                ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"синтетический отчёт за {A['meta']['first_period']} → {args.expected}")
            return 0
        if not args.facts or not args.out:
            ap.error("нужны --facts и --out (или --expected)")
        cand = reanchor(A, read_facts(args.facts), valuation_date=args.valuation_date,
                        version=args.version, nwc_path=args.nwc_path)
        attr = None if args.no_attribution else attribution(A, cand, band_draws=args.band)
        write_candidate(args.out, A, cand, attr)
    except (FactsError, BookError) as exc:
        print(f"reanchor: {exc}", file=sys.stderr)
        return 1
    print(report_text(A, cand, attr))
    return 0


if __name__ == "__main__":
    sys.exit(main())
