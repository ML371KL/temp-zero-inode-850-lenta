"""Проверки: инварианты и гейты правдоподобия.

Старая система держала 10,9 тыс. строк с хэшами, 142 SHA-256 в публичном
пакете и 1 185 407 «аудиторских сравнений» — и ни одной проверки
правдоподобия: поиск `EV/EBITDA` по исходникам был пуст, сравнение с рынком
в оценочной цепочке отключено (`market_cap=None`). Зелёный CI стоял перед
каждым из семи сбоев 20.09.

Разделение жёсткое.

**Инвариант** — арифметика. Нарушение означает поломку и блокирует выпуск.

**Гейт правдоподобия** — экономика. Срабатывание НЕ блокирует и НЕ включает
автоподгонку: оно требует письменного объяснения в выпуске. Модель имеет право
не соглашаться с рынком, но не имеет права не замечать, что не соглашается.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Severity(str, Enum):
    INVARIANT = "инвариант"
    GATE = "гейт"


@dataclass(frozen=True)
class Finding:
    key: str
    severity: Severity
    message: str
    label: str = ""
    observed: float | str | None = None
    expected: str = ""

    @property
    def blocking(self) -> bool:
        return self.severity is Severity.INVARIANT


# Коридоры гейтов `margin_range`, `capex_range` и `ev_ebitda` — ДАННЫЕ гейтов, а
# не литералы кода (CONSISTENCY E20): поле `corridor` их записей в
# `data/assumptions/gate_explanations.yaml` (`gate_corridors`). Коридор — суждение
# о компании (у «Ленты» маржа 4,5–8,0 %, capex 1,5–5,5 %, EV/EBITDA 2,5–4,5×, в
# мире N 3,5–6,0×), и его правка — правка данных с объяснением рядом, а не кода.
CORRIDOR_GATES = ("margin_range", "capex_range", "ev_ebitda")
# EV/EBITDA зависит от мира: при ставках мира N бизнес обязан стоить дороже.
# Стресс и полный возврат выходят за коридор по построению — их не проверяем.
EV_EBITDA_REGIMES = ("floor", "partial")
ZERO_OPENINGS_MAX_YEARS = 2.0
TERMINAL_SHARE_RANGE = (0.10, 0.55)
REAL_RATE_RANGE = (0.02, 0.13)

# Гейтов структурного отображения 850oa (`creditor_loss`, флаг перекалибровки
# σ `sigma_calibration`) здесь нет: заголовок — внутренняя стоимость (D4), σ и
# калибровки нет. Существенность ограниченной ответственности сторожит
# совещательный гейт `limited_liability` (`check_limited_liability`).

# Метка находки, относящейся не к клетке, а ко всему выпуску: гейты слоя и
# гейты согласованности миров. Масса такой находки — единица: она про весь
# напечатанный результат, а не про его часть.
RELEASE_LABEL = "выпуск целиком"

# Сдвиг кривой для проверки монотонности по стоимости денег, п.п.
RATE_SHIFT_FOR_MONOTONICITY = 0.02

# Гейты, письменное объяснение которых ЕЩЁ НЕ СОГЛАСОВАНО аудитором.
#
# СПИСОК ПУСТ, и это не заготовка на будущее, а состояние дела: три ключа,
# которые здесь стояли третью итерацию (`creditor_loss`, `margin_range`,
# `credit_lines`), получили тексты аудитора 22.09.2026 — они лежат в
# `data/assumptions/gate_explanations.yaml` и с этой минуты работают как все
# прочие: объяснённый гейт не блокирует, НЕОБЪЯСНЁННЫЙ РОНЯЕТ СБОРКУ.
#
# Почему механизм не удалён вместе с ключами. Аудит третьей итерации, B4:
# пока список был непустым, любой гейт можно было внести сюда и снять его
# текст, не уронив ни одного теста, — то есть выключить проверку правкой
# одной строки. Пустой литерал держит это на виду: тест
# `test_pending_gates_is_empty_by_literal` требует пустоты, и вернуть ключ
# нельзя молча. Механизм ожидания остаётся законной дверью на случай
# следующего нового гейта, но открывать её придётся видимой правкой.
PENDING_GATES: frozenset[str] = frozenset()

# Срок, до которого гейт имеет право ждать текста аудитора.
#
# РЕШЕНИЕ ПО СОВЕТУ АУДИТА (B4, «на будущее — PENDING_UNTIL с датой»). Оно
# нужно, и именно при пустом списке его и надо вводить. Пустота держится
# литералом и тестом, но тест охраняет только ПУСТОТУ: как только появится
# следующий новый гейт, ключ вернётся сюда законно — и вернётся без срока,
# потому что срок в прежнем механизме просто не предусмотрен. Дверь без срока
# — это дверь, которую забывают закрыть: ровно так три ключа простояли в
# списке всю третью итерацию.
#
# Поэтому дверь теперь ДАТИРОВАНА, и датирована принудительно: ключ в
# `PENDING_GATES` без записи здесь (или с прошедшей датой) — нарушенный
# инвариант, то есть сборка не выходит. Обойти это можно только удалив
# проверку, а не забыв про неё. Пока список пуст, запись тоже пуста, и
# механизм ничего не стоит.
PENDING_UNTIL: dict[str, str] = {}

# Гейты, которые не блокируют сборку НИКОГДА — по построению, а не в ожидании
# текста. Такой гейт адресован не расчёту, а человеку: «книгу пора
# обновлять» — это работа аудитора, и останавливать из-за неё ежедневный
# выпуск бессмысленно. Плашку на витрине он даёт, публикацию — нет.
# `limited_liability` — доля прогонов полосы с V0 < порог·D выше порога книги:
# внутренняя стоимость перестаёт быть честным отображением, нужна новая версия
# книги со сменой метода (D4).
# `guidance_gap` — гайденс компании на год не достигается в клетке (решение
# ведущего C20): расхождение книги с гайденсом записано до события и
# проверяется отчётом, а не правкой книги; публикацию не останавливает.
# `security_change` — карточка акции на бирже (уровень листинга, акций в выпуске)
# сменилась после даты книги (D15): повод пересмотреть дисконт за управление
# новой версией книги, а не число выпуска.
ADVISORY_GATES = frozenset({"book_update", "limited_liability", "guidance_gap",
                            "security_change"})

# Сдвиг кривой, после которого книга считается требующей обновления: 50 б.п.
# на среднем и длинном конце. Короткий конец живёт ключевой ставкой и шумит,
# поэтому в правило не входит.
BOOK_CURVE_SHIFT_MAX = 0.005
BOOK_UPDATE_NODES = ("5", "10")
# Возраст книги, после которого её пора пересматривать независимо от кривой.
# 45 дней — это полтора месяца: за такой срок выходит решение ЦБ по ставке и
# недельная статистика Росстата набирает значимый объём.
BOOK_AGE_MAX_DAYS = 45

# Срок объяснения гейта (`valid_until`) — «будильник»: текст, написанный под
# одну картину, обязан перечитываться. Объяснение действует ВКЛЮЧИТЕЛЬНО по
# `valid_until` и считается отсутствующим со СЛЕДУЮЩЕГО дня.
#
# До 24.09.2026 правило было записано дважды и по-разному: тест требовал
# `until > today`, сборка считала просроченным `until < today`. В день срока
# суточный такт падал на тестах — на день раньше даты в файле и с чужой
# причиной в журнале («тест упал», а не «объяснение просрочено»; независимый
# аудит, G1-exam §1.3 п. 3). Теперь правило одно — `explanation_is_stale`, — и
# его зовут и сборка, и тест.
#
# За `EXPLANATION_WARN_DAYS` дней до срока — СОВЕЩАТЕЛЬНАЯ тревога: выпуск
# публикуется, а такт кончается кодом тревоги с названной причиной (Telegram).
# Без неё будильник звонил сразу остановкой витрины: первое, что владелец
# узнавал о сроке, — несвежий выпуск.
EXPLANATION_WARN_DAYS = 30

# Коридор сверки массы гейта с объяснением: от 0,4 × ожидаемой − 0,02 до
# 1,5 × ожидаемой + 0,02. При `expected_mass_range: [lo, hi]` тот же допуск
# ставится вокруг ГРАНИЦ диапазона: 0,4 × lo − 0,02 … 1,5 × hi + 0,02.
MASS_TOLERANCE_LOW = (0.4, 0.02)
MASS_TOLERANCE_HIGH = (1.5, 0.02)


def explanation_is_stale(until, today) -> bool:
    """Просрочено ли объяснение: действует включительно по `valid_until`.

    Одно правило на сборку (`load_gate_explanations`) и на тест
    (`test_explanations_are_not_stale`). Объяснение без срока не просрочено
    никогда — поэтому тест отдельно требует, чтобы срок был у каждого.
    """
    return until is not None and until < today


def explanation_expires_soon(until, today, warn_days: int = EXPLANATION_WARN_DAYS) -> bool:
    """Ещё действует, но истекает в ближайшие `warn_days` дней (срок включительно)."""
    return until is not None and 0 <= (until - today).days <= warn_days


def check_pending_gates_are_dated(today=None) -> list[Finding]:
    """Ключ, ждущий текста аудитора, обязан ждать ДО НАЗВАННОЙ ДАТЫ.

    Это сторож самого механизма ожидания, а не экономики, поэтому нарушение —
    инвариант: сборка не выходит. Без него `PENDING_GATES` остаётся списком,
    в который достаточно дописать имя гейта, чтобы его объяснение перестало
    требоваться навсегда (аудит третьей итерации, B4).

    Два нарушения. Ключ без даты — дверь без срока. Ключ с прошедшей датой —
    дверь, срок которой истёк: текст так и не написан, и дальше выпуск идти
    не должен.
    """
    import datetime as _dt

    today = today or _dt.date.today()
    out: list[Finding] = []
    for key in sorted(PENDING_GATES):
        raw = PENDING_UNTIL.get(key)
        if not raw:
            out.append(Finding(
                key="pending_gate_undated", severity=Severity.INVARIANT,
                message=(f"гейт {key} ждёт текста аудитора без срока: "
                         "добавьте дату в PENDING_UNTIL"),
                label=RELEASE_LABEL, observed=key))
            continue
        until = _dt.date.fromisoformat(raw) if isinstance(raw, str) else raw
        if until < today:
            out.append(Finding(
                key="pending_gate_expired", severity=Severity.INVARIANT,
                message=(f"гейт {key} ждал текста до {until.isoformat()}, и срок "
                         "истёк: объяснение так и не написано"),
                label=RELEASE_LABEL, observed=key, expected=until.isoformat()))
    # И обратное: запись о сроке для ключа, который уже никто не ждёт, —
    # забытое разрешение. Тоже инвариант, а не гейт: это дефект кода, а не
    # утверждение об экономике, и публиковать вокруг него нечего. Иначе
    # запись осталась бы висеть, и следующий раз ключ попал бы в ожидание «по
    # инерции» — с датой, написанной под прошлый повод.
    for key in sorted(set(PENDING_UNTIL) - set(PENDING_GATES)):
        out.append(Finding(
            key="pending_gate_stale_date", severity=Severity.INVARIANT,
            message=(f"срок ожидания для гейта {key} записан, а сам гейт "
                     "текста не ждёт — запись пора убрать"),
            label=RELEASE_LABEL, observed=key))
    return out


def check_invariants(result, label: str = "") -> list[Finding]:
    """Арифметика. Нарушение — поломка."""
    out: list[Finding] = []
    for r in result.rows:
        if r.revenue <= 0:
            out.append(Finding("nonpositive_revenue", Severity.INVARIANT,
                               f"{r.period}: выручка {r.revenue:.1f}", label, r.revenue, "> 0"))
        if r.area_end <= 0:
            out.append(Finding("nonpositive_area", Severity.INVARIANT,
                               f"{r.period}: площадь {r.area_end:.0f}", label, r.area_end, "> 0"))
        if abs((r.ebitda - r.da) - r.ebit) > 1e-9 * max(1.0, abs(r.ebit)):
            out.append(Finding("ebit_identity", Severity.INVARIANT,
                               f"{r.period}: EBIT ≠ EBITDA − D&A", label, r.ebit, ""))
        if r.tax_unlevered < -1e-9 or r.tax_actual < -1e-9:
            out.append(Finding("negative_tax", Severity.INVARIANT,
                               f"{r.period}: отрицательный налог", label, r.tax_actual, "≥ 0"))
        rebuilt = (r.ebitda - r.tax_unlevered - r.capex - r.nwc_change
                   - r.operating_cash_change + r.lease_adjustment
                   + r.disposal_proceeds)
        if abs(rebuilt - r.fcff) > 1e-9 * max(1.0, abs(r.fcff)):
            out.append(Finding("fcff_identity", Severity.INVARIANT,
                               f"{r.period}: FCFF не собирается из опубликованных строк",
                               label, r.fcff, f"{rebuilt:.6f}"))
        # Части capex: поддерживающий, ростовой, инфраструктурный и интеграционный
        # приобретённого периметра (`capex.integration_capex`).
        parts = r.capex_maintenance + r.capex_growth + r.capex_infra + r.capex_integration
        if abs(parts - r.capex) > 1e-9 * max(1.0, r.capex):
            out.append(Finding("capex_identity", Severity.INVARIANT,
                               f"{r.period}: capex не равен сумме частей", label, r.capex, ""))

    if result.price_floor < 0:
        out.append(Finding("negative_price", Severity.INVARIANT,
                           f"публикуемая цена {result.price_floor:.0f} ₽ отрицательна",
                           label, result.price_floor, "≥ 0"))
    if result.equity > 0 and result.price <= 0:
        out.append(Finding("price_sign", Severity.INVARIANT,
                           "положительный капитал даёт неположительную цену", label))
    return out


def check_gates(A: dict, result, label: str = "",
                market_price: float | None = None,
                market_cap: float | None = None,
                credit_limit: float | None = None,
                corridors: dict | None = None) -> list[Finding]:
    """Экономика. Срабатывание требует письменного объяснения, а не правки.

    `credit_limit` и `corridors` передаются сборкой один раз на все 36 клеток:
    лимит читает факты, коридоры — файл объяснений гейтов (`gate_corridors`).
    """
    out: list[Finding] = []
    cell = result.cell
    corridors = corridors if corridors is not None else gate_corridors()
    market_price = market_price if market_price is not None else A["market"]["price"]
    if market_cap is None:
        market_cap = market_price * A["facts"]["shares_out_mln"] / 1000

    if cell.margin_regime in EV_EBITDA_REGIMES:
        lo, hi = corridors["ev_ebitda"][cell.world]
        if not lo <= result.ev_ebitda_ltm <= hi:
            out.append(Finding(
                "ev_ebitda", Severity.GATE,
                f"EV/EBITDA {result.ev_ebitda_ltm:.2f}x вне коридора мира {cell.world} "
                f"({lo}-{hi}x); аналоги книги: "
                + ", ".join(f"{p['name']} ≈{p['ev_ebitda']}x" for p in A["market"]["peers"]),
                label, result.ev_ebitda_ltm, f"{lo}-{hi}x"))

    margins = [r.margin for r in result.rows]
    m_lo, m_hi = corridors["margin_range"]
    if min(margins) < m_lo or max(margins) > m_hi:
        out.append(Finding(
            "margin_range", Severity.GATE,
            f"маржа EBITDA {min(margins)*100:.2f}-{max(margins)*100:.2f} % "
            f"вне исторического коридора {m_lo*100:.1f}-{m_hi*100:.1f} %",
            label, f"{min(margins)*100:.2f}-{max(margins)*100:.2f}", ""))

    rates = [r.capex_pct for r in result.rows]
    c_lo, c_hi = corridors["capex_range"]
    if min(rates) < c_lo or max(rates) > c_hi:
        out.append(Finding(
            "capex_range", Severity.GATE,
            f"capex/выручка {min(rates)*100:.2f}-{max(rates)*100:.2f} % вне коридора "
            f"{c_lo*100:.1f}-{c_hi*100:.1f} %", label, "", ""))

    streak = longest = 0.0
    for r in result.rows:
        streak = 0.0 if r.capex_growth > 1e-9 else streak + 0.5
        longest = max(longest, streak)
    if longest > ZERO_OPENINGS_MAX_YEARS:
        out.append(Finding(
            "zero_openings", Severity.GATE,
            f"ноль открытий подряд {longest:.1f} года — при открытиях "
            f"{_last_openings(A):g} тыс. м² в последнем отчётном полугодии",
            label, longest, f"≤ {ZERO_OPENINGS_MAX_YEARS} лет"))

    t_lo, t_hi = TERMINAL_SHARE_RANGE
    if not t_lo <= result.terminal_share <= t_hi:
        out.append(Finding("terminal_share", Severity.GATE,
                           f"доля терминала в EV {result.terminal_share:.0%}",
                           label, result.terminal_share, f"{t_lo:.0%}-{t_hi:.0%}"))

    r_lo, r_hi = REAL_RATE_RANGE
    if not r_lo <= result.r_real <= r_hi:
        out.append(Finding(
            "real_rate", Severity.GATE,
            f"реальная ставка мира {cell.world} в терминале {result.r_real:.1%} "
            f"вне [{r_lo:.0%}; {r_hi:.0%}] — мир рассогласован",
            label, result.r_real, ""))

    if result.equity < 0 <= market_cap:
        out.append(Finding(
            "equity_sign", Severity.GATE,
            f"капитал по DCF отрицателен ({result.equity:.1f} млрд ₽) при рыночной "
            f"капитализации {market_cap:.1f}; публикуется ноль",
            label, result.equity, "≥ 0 либо объяснение"))

    covers = [r.interest_cover for r in result.rows]
    if min(covers) < 1.0:
        out.append(Finding(
            "interest_cover", Severity.GATE,
            f"покрытие процентов падает до {min(covers):.2f}x — ниже единицы: проценты "
            f"не покрываются EBITDA",
            label, min(covers), "≥ 1,0x"))

    # Лимит кредитных линий. До третьей итерации это был ФЛАГ КЛЕТКИ на
    # витрине и больше ничего: клетка, где долг перерастает раскрытые линии,
    # входила в среднее как есть, и заметить это можно было только глазами на
    # графике. Физически такая клетка означает допэмиссию или
    # реструктуризацию — и то и другое обнуляет долю нынешнего акционера
    # сильнее, чем считает модель, потому что ветки докапитализации в ней нет.
    #
    # Гейт идёт за суждением книги `valuation.distress.credit_limit_trigger`
    # (решение ведущего F1): лимит — либо ограничение пути долга (тогда он и
    # включает издержки неустойчивости, и требует объяснения), либо нет. У
    # «Ленты» номинальная сумма 30.06.2026 при выручке, растущей в 2,5 раза, —
    # не ограничение, неустойчивость ловит порог ЧД/EBITDA; путь против
    # номинального лимита остаётся справкой (`results.json` →
    # `checks.credit_lines`, флаг клетки на витрине), без объяснения к гейту,
    # который книга ограничением не считает.
    limit = credit_limit if credit_limit is not None else _credit_limit(A)
    if (limit and result.max_gross_debt > limit
            and _distress_rule(A)["credit_limit_trigger"]):
        out.append(Finding(
            "credit_lines", Severity.GATE,
            f"валовой долг доходит до {result.max_gross_debt:.0f} млрд ₽ при доступных "
            f"{limit:.0f} (долг на дату якоря плюс неиспользованные линии "
            f"{A['facts']['undrawn_credit_lines']:g}): ветки "
            f"докапитализации в модели нет, клетка считается как есть",
            label, result.max_gross_debt, f"≤ {limit:.0f}"))

    return out


def _last_openings(A: dict) -> float:
    """Валовые открытия последнего отчётного полугодия — сумма по сегментам сети."""
    return sum((f.get("new_area_gross_hist") or [0.0])[-1]
               for f in A["facts"]["segments"].values() if f.get("new_area_gross_hist"))


def guidance_year_parts(A: dict, result) -> tuple[float, float] | None:
    """(выручка, EBITDA) года гайденса в клетке — правило гейта `guidance_gap`.

    Полугодия года: закрытые на якоре — отчётные (`facts.reported`, периметр
    отчёта, как у гайденса компании), прогнозные — строки клетки. None — гайденса
    нет, год вне горизонта или целиком в прошлом (гайденс уже проверен отчётом).
    Одна функция на гейт и на блок выпуска `strategy` (`model/payload.py`).
    """
    spec = (A.get("facts") or {}).get("guidance")
    if not spec:
        return None
    year = str(spec["period"])
    rows = {r.period: r for r in result.rows}
    reported = A["facts"].get("reported") or {}
    revenue = ebitda = 0.0
    forecast = False
    for p in (f"{year}H1", f"{year}H2"):
        if p in rows:
            revenue, ebitda, forecast = revenue + rows[p].revenue, ebitda + rows[p].ebitda, True
        elif p in (reported.get("revenue") or {}) and p in (reported.get("ebitda_pre16") or {}):
            revenue += reported["revenue"][p]
            ebitda += reported["ebitda_pre16"][p]
        else:
            return None
    return (revenue, ebitda) if forecast else None


def check_guidance(A: dict, result, label: str = "") -> list[Finding]:
    """Гейт `guidance_gap` (совещательный; решение ведущего C20).

    `facts.guidance {period: ГГГГ, ebitda_margin_min}` — гайденс компании по
    марже EBITDA за год. Маржа года в клетке = Σ EBITDA / Σ выручка его двух
    полугодий (`guidance_year_parts`): закрытые на якоре — отчётные
    (`facts.reported`, периметр отчёта), прогнозные — строки клетки. Год целиком
    в прошлом (оба полугодия отчётные) или вне горизонта — гейта нет: гайденс
    уже проверен отчётом. Ниже гайденса — находка клетки; масса гейта — доля
    сетки, где гайденс не достигается.
    """
    parts = guidance_year_parts(A, result)
    if parts is None:
        return []
    spec = A["facts"]["guidance"]
    year = str(spec["period"])
    revenue, ebitda = parts
    margin, floor = ebitda / revenue, float(spec["ebitda_margin_min"])
    if margin >= floor:
        return []
    return [Finding(
        "guidance_gap", Severity.GATE,
        f"маржа EBITDA {year} года в клетке {margin * 100:.2f} % против гайденса компании "
        f"не ниже {floor * 100:.1f} % (1П — отчёт, 2П — клетка)",
        label, margin, f"≥ {floor:.3f}")]


def _credit_limit(A: dict) -> float:
    """Потолок валового долга. Импорт отложенный: `model.financing` читает
    факты с диска, и тянуть его при импорте проверок незачем."""
    from model.financing import credit_limit as _limit

    return _limit(A)


def _distress_rule(A: dict) -> dict:
    """`valuation.distress` книги (`model.book.distress_rule`); импорт отложенный,
    как у лимита."""
    from model.book import distress_rule

    return distress_rule(A)


def check_limited_liability(A: dict, band: dict | None) -> list[Finding]:
    """Гейт `limited_liability` (совещательный, уровня выпуска).

    Полоса (`model.uncertainty.uncertainty`) считает долю прогонов, в которых V0
    хотя бы одного слоя ниже `valuation.headline.limited_liability.v0_to_d_min`
    × D; доля выше `max_share` — гейт срабатывает: ограниченная ответственность
    акционера существенна, внутренняя стоимость её не видит, метод меняет только
    новая версия книги. Выпуск не останавливается. Нет полосы или блока — пусто.
    """
    info = (band or {}).get("limited_liability")
    if not info or not info["fired"]:
        return []
    return [Finding(
        "limited_liability", Severity.GATE,
        f"в {info['hits']} из {info['draws']} прогонов полосы ({info['share']:.1%}) V0 слоя ниже "
        f"{info['v0_to_d_min']:.2f}·D — больше порога книги {info['max_share']:.1%}: "
        "ограниченная ответственность акционера существенна, внутренняя стоимость её не "
        "учитывает; нужна новая версия книги со сменой метода (D4)",
        RELEASE_LABEL, info["share"], f"доля ≤ {info['max_share']:.4f}")]


def check_book_is_current(A: dict, live) -> list[Finding]:
    """Не пора ли обновлять книгу: сдвиг кривой и возраст калибровки.

    Оценка считается на мирах книги как они записаны (A2, п. 5), поэтому
    рынок может уехать, а напечатанное число — нет. Единственная защита от
    этого — заметить расхождение и сказать о нём: гейт даёт тревогу в прогоне
    и плашку на витрине, но публикацию НЕ останавливает. Останавливать
    нечего: прежний выпуск от этого не станет свежее, а обновить миры может
    только аудитор.
    """
    out: list[Finding] = []
    if live is None:
        return out
    shift = getattr(live, "curve_shift", None) or {}
    moved = {node: value for node, value in shift.items()
             if node in BOOK_UPDATE_NODES and abs(value) >= BOOK_CURVE_SHIFT_MAX}
    age = int(getattr(live, "book_age_days", 0) or 0)
    reasons = []
    if moved:
        reasons.append("кривая ушла на " + ", ".join(
            f"{value * 10000:+.0f} б.п. на {node} годах" for node, value in sorted(moved.items())))
    if age > BOOK_AGE_MAX_DAYS:
        # Дата — по-русски: строка уходит на плашку витрины как есть.
        stamp = str(getattr(live, "book_date", "") or "")
        try:
            import datetime as _dt

            stamp = _dt.date.fromisoformat(stamp[:10]).strftime("%d.%m.%Y")
        except ValueError:
            pass
        reasons.append(f"откалибрована {stamp} — {age} дней назад "
                       f"(пересматривать каждые {BOOK_AGE_MAX_DAYS} дней)")
    if reasons:
        out.append(Finding(
            "book_update", Severity.GATE,
            "книга требует обновления: " + "; ".join(reasons)
            + ". Оценка считается на книжных мирах, поэтому расхождение с рынком "
              "в напечатанное число не попадает — его должен внести аудитор",
            RELEASE_LABEL, age, f"сдвиг < {BOOK_CURVE_SHIFT_MAX * 10000:.0f} б.п., "
                                f"возраст ≤ {BOOK_AGE_MAX_DAYS} дн"))
    return out


SECURITY_FIELD_TITLES = {"LISTLEVEL": "уровень листинга", "ISSUESIZE": "акций в выпуске"}


def check_security_card(live) -> list[Finding]:
    """Гейт `security_change` (совещательный, уровня выпуска; D15).

    Карточка акции (`live.security`, сборщик `moex_security`) сменилась ПОСЛЕ
    даты книги: уровень листинга или объём выпуска — это каналы (в) и (г)
    дисконта за управление (`valuation.governance_components`), и суждение книги
    писалось до смены. Гейт горит, пока новая версия книги (с датой после смены)
    не пересмотрит g; публикацию не останавливает. Смена до даты книги — уже в
    книге (3-й уровень с 12.08.2026 у книги 18.09.2026), гейта нет.
    """
    if live is None:
        return []
    card = getattr(live, "security", None) or {}
    book_date = str(getattr(live, "book_date", "") or "")
    fresh = [c for c in card.get("changes") or [] if str(c.get("since", "")) > book_date]
    if not fresh:
        return []
    parts = [f"{SECURITY_FIELD_TITLES.get(c.get('field'), c.get('field'))} "
             f"{c.get('before'):g} → {c.get('after'):g} с {c.get('since')}" for c in fresh]
    return [Finding(
        "security_change", Severity.GATE,
        "карточка акции на бирже сменилась после даты книги (" + "; ".join(parts)
        + "): пересмотреть дисконт за управление (ликвидность и листинг, права "
          "миноритария) новой версией книги; число выпуска не меняется",
        RELEASE_LABEL, len(fresh), f"смен после {book_date or 'даты книги'} — 0")]


def check_rate_monotonicity(price_low_rate: float, price_high_rate: float) -> list[Finding]:
    """EV не должен расти при росте ставки. Рост — признак того, что мир
    рассогласован: инфляция взята из одного мира, а дисконт из другого."""
    if price_high_rate > price_low_rate + 1e-9:
        return [Finding("ev_grows_with_rates", Severity.GATE,
                        f"цена растёт при росте ставки: {price_low_rate:.0f} → {price_high_rate:.0f} ₽",
                        "", price_high_rate, f"< {price_low_rate:.0f}")]
    return []


def check_worlds_are_monotone_in_rates(A: dict, *, shift: float = RATE_SHIFT_FOR_MONOTONICITY
                                       ) -> list[Finding]:
    """Та же монотонность, но КАК ГЕЙТ СБОРКИ, по всем трём миром.

    До третьей итерации `check_rate_monotonicity` вызывалась только из теста и
    в сборке не участвовала — то есть проверяла книгу на момент написания
    теста, а не тот выпуск, который уходит на витрину. Здесь кривая каждого
    мира сдвигается вверх на 2 п.п. при неизменной инфляции: так проверяется
    именно согласованность дисконта с потоком, а не чувствительность к
    инфляции. Дороже от подорожавших денег бизнес стать не может.
    """
    import copy as _copy

    from model.book import Cell
    from model.core import run_cell

    out: list[Finding] = []
    for world in A["joint"]["world_prob"]:
        priors = A["joint"]["regime_given_world"][world]
        regime = max(priors, key=priors.get)
        cell = Cell.build(A, world, regime, "base")
        base = run_cell(A, cell).ev
        trial = _copy.deepcopy(A)
        curve = trial["worlds"][world]["zero_curve"]
        trial["worlds"][world]["zero_curve"] = {k: v + shift for k, v in curve.items()}
        raised = run_cell(trial, Cell.build(trial, world, regime, "base")).ev
        if raised > base + 1e-9:
            out.append(Finding(
                "ev_grows_with_rates", Severity.GATE,
                f"мир {world}: при кривой выше на {shift * 100:.0f} п.п. стоимость бизнеса "
                f"РАСТЁТ ({base:.1f} → {raised:.1f} млрд ₽) — дисконт рассогласован с потоком",
                RELEASE_LABEL, raised, f"≤ {base:.1f}"))
    return out


def report(findings: list[Finding]) -> str:
    if not findings:
        return "проверки пройдены: нарушений нет"
    lines = []
    for f in findings:
        mark = "БЛОКИРУЕТ" if f.blocking else "объяснить"
        where = f" [{f.label}]" if f.label else ""
        lines.append(f"[{mark}]{where} {f.key}: {f.message}")
    return "\n".join(lines)


# ------------------------------------------- агрегация гейтов по сетке


@dataclass(frozen=True)
class GateSummary:
    """Сводка по одному ключу гейта: где сработал и какой массой."""

    key: str
    cells: int
    mass: float
    labels: list[str]
    explained: bool
    explanation: str = ""
    expected_mass: float | None = None
    stale: bool = False
    pending: bool = False
    """Гейт включён, но его текст ещё не согласован аудитором (PENDING_GATES).

    С 22.09.2026 `PENDING_GATES` пуст, то есть поле всегда False: тексты трёх
    ожидавших гейтов вписаны. Поле оставлено потому, что просроченное
    объяснение обязано блокировать даже ожидающий гейт, — правило проверяется
    тестом и не должно исчезнуть вместе с временной развязкой."""
    advisory: bool = False
    """Гейт адресован человеку и не блокирует сборку по построению."""
    expected_mass_range: tuple[float, float] | None = None
    """Диапазон ожидаемой массы `[lo, hi]` из объяснения — для гейтов, чья
    масса законно ДРЕЙФУЕТ. Пример — `terminal_share`: с перекатом даты
    оценки закрытые полугодия выпадают из PV, доля терминала растёт, и масса
    срабатывания идёт 0,012 → 0,049 (08.10.2026) → 0,076 (01.01.2027) при
    неизменной экономике. Коридор от одной точки давал бы тревогу каждый
    будний такт с 08.10.2026 (аудит, G1-exam §1.3 п. 1)."""
    valid_until: object = None
    """Срок объяснения (дата) — для печати в журнале такта."""
    expiring: bool = False
    """Объяснение ещё действует, но истекает в ближайшие
    `EXPLANATION_WARN_DAYS` дней: совещательная тревога, не блокировка."""
    message: str = ""
    """Текст находки уровня ВЫПУСКА — то, что сборка знает о причине.

    Для гейтов по клеткам пуст: там причина у каждой клетки своя, а общее
    слово — объяснение. Для гейтов выпуска (`book_update`, `limited_liability`,
    монотонность миров) это единственное место, где названа ФАКТИЧЕСКАЯ
    причина срабатывания. Витрина печатала на плашке `book_update` запасную
    фразу о кривой, хотя причиной был возраст книги (аудит, G1-exam §1.3 п. 5)."""

    @property
    def blocking(self) -> bool:
        """Необъяснённый гейт блокирует выпуск так же, как нарушенный инвариант.

        Первая итерация считала гейты только на трёх именованных сценариях, а
        поле `explained` было литералом `[]`. Гейт, который нигде не объясняют
        и который ничего не останавливает, — это не проверка, а украшение.

        Исключение одно и оно временное: гейт из списка `PENDING_GATES`, у
        которого объяснения ещё НЕТ, виден, но не блокирует. Как только текст
        появится, ветка перестанет работать сама собой — и, что важнее,
        просроченный текст (`stale`) блокирует даже такой гейт: «ожидает
        согласования» и «согласовано когда-то давно» — разные состояния.
        """
        if self.advisory:
            return False
        if self.pending and not self.explained:
            return False
        return not self.explained or self.stale

    @property
    def mass_corridor(self) -> tuple[float, float] | None:
        """Коридор, в котором масса согласуется с объяснением. None — сверки нет.

        Диапазон `expected_mass_range`, если он задан, иначе точка
        `expected_mass`; допуск вокруг границ прежний (`MASS_TOLERANCE_*`).
        """
        if self.expected_mass_range is not None:
            low, high = self.expected_mass_range
        elif self.expected_mass is not None:
            low = high = self.expected_mass
        else:
            return None
        return (low * MASS_TOLERANCE_LOW[0] - MASS_TOLERANCE_LOW[1],
                high * MASS_TOLERANCE_HIGH[0] + MASS_TOLERANCE_HIGH[1])

    @property
    def mass_mismatch(self) -> bool:
        """Фактическая масса разошлась с ожидаемой из объяснения.

        Сверять это только тестом на книжных входах недостаточно: сборка идёт
        на живых, и именно там масса уезжает. Коридор двусторонний — гейт,
        который ПЕРЕСТАЛ срабатывать, тоже означает, что объяснение описывает
        не то, что происходит.
        """
        corridor = self.mass_corridor
        if corridor is None:
            return False
        return not (corridor[0] <= self.mass <= corridor[1])


def _parse_until(key: str, raw):
    """`valid_until` → дата. Мусор — ошибка сборки, а не «срока нет»."""
    import datetime as _dt

    if raw is None:
        return None
    if isinstance(raw, _dt.datetime):
        return raw.date()
    if isinstance(raw, _dt.date):
        return raw
    try:
        return _dt.date.fromisoformat(str(raw))
    except ValueError:
        raise ValueError(f"gate_explanations.yaml, {key}: valid_until = {raw!r} — "
                         "не дата ГГГГ-ММ-ДД") from None


def _parse_mass_range(key: str, raw) -> tuple[float, float] | None:
    """`expected_mass_range: [lo, hi]` → пара долей. Необязательное поле.

    Неверный формат — ОШИБКА СБОРКИ, а не молчаливый откат к точке: диапазон
    вписывают ровно тогда, когда точка уже даёт ложные тревоги, и тихо
    проигнорированный диапазон вернул бы их обратно.
    """
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        raise ValueError(f"gate_explanations.yaml, {key}: expected_mass_range = {raw!r} — "
                         "ожидается [нижняя, верхняя] в долях вероятностной массы")
    try:
        low, high = float(raw[0]), float(raw[1])
    except (TypeError, ValueError):
        raise ValueError(f"gate_explanations.yaml, {key}: expected_mass_range = {raw!r} — "
                         "границы не числа") from None
    if not 0.0 <= low <= high <= 1.0:
        raise ValueError(f"gate_explanations.yaml, {key}: expected_mass_range = {raw!r} — "
                         "нужно 0 ≤ нижняя ≤ верхняя ≤ 1")
    return (low, high)


def _parse_corridor(key: str, raw):
    """`corridor` записи гейта: [низ, верх] или {мир: [низ, верх]} (EV/EBITDA)."""
    def pair(value, where):
        if (not isinstance(value, (list, tuple)) or len(value) != 2
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)
                or not value[0] < value[1]):
            raise ValueError(f"gate_explanations.yaml, {key}: corridor{where} = {value!r} — "
                             "ожидается [низ, верх], низ < верх")
        return (float(value[0]), float(value[1]))

    if raw is None:
        return None
    if isinstance(raw, dict):
        return {str(w): pair(v, f".{w}") for w, v in raw.items()}
    return pair(raw, "")


def gate_corridors(path=None) -> dict:
    """Коридоры гейтов `CORRIDOR_GATES` из файла объяснений (CONSISTENCY E20).

    Нет файла или поля `corridor` у гейта с коридором — ошибка сборки: без
    коридора гейт молча не проверял бы ничего. У `ev_ebitda` — по мирам.
    """
    import datetime as _dt

    explanations = load_gate_explanations(path, today=_dt.date.min)
    out = {}
    for key in CORRIDOR_GATES:
        corridor = (explanations.get(key) or {}).get("corridor")
        if corridor is None:
            raise ValueError(f"gate_explanations.yaml: у гейта {key} нет коридора (поле corridor) — "
                             "коридоры гейтов — данные, не литералы кода")
        if key == "ev_ebitda" and not isinstance(corridor, dict):
            raise ValueError("gate_explanations.yaml, ev_ebitda: corridor — по мирам {N: […], H: […], M: […]}")
        if key != "ev_ebitda" and isinstance(corridor, dict):
            raise ValueError(f"gate_explanations.yaml, {key}: corridor — [низ, верх]")
        out[key] = corridor
    return out


# Основания коридоров на истории «Ленты». Сами коридоры — данные (поле `corridor`
# записей `gate_explanations.yaml`, E20); здесь — из какой истории они выведены,
# за какие годы коридор обязан её покрывать и что остаётся снаружи. Числа истории
# не переписываются сюда: их считает `corridor_history` из фактов
# (`data/facts/accounting_base.json`, IAS 17), и выпуск печатает их рядом с
# коридором (`checks.corridors`), а тест `test_the_corridors_cover_the_lenta_history`
# сверяет покрытие окна. Годы окна — параметр проверки, не правило модели.
CORRIDOR_BASIS = {
    "margin_range": (
        "Маржа EBITDA IAS 17 каждого полугодия пути клетки. Коридор — по годовой марже "
        "«Ленты» на нынешнем наборе форматов (с 2022 г.: гипермаркеты, супермаркеты, у дома, "
        "дрогери) с запасом вниз на стресс регулирования наценок и вверх на полную "
        "сходимость приобретённых сетей (лист «Маржа»); годы одних гипермаркетов (до 2022 г.) "
        "лежат над верхом коридора: их возврат модель обязана объяснить, а не напечатать молча. "
        "Отдельные полугодия выходят за годовой коридор (сезонность, разовые статьи) — "
        "поэтому окно сверки годовое."),
    "capex_range": (
        "Денежный capex IAS 17 (ОС + НМА + права аренды) к выручке каждого полугодия пути "
        "клетки. Коридор покрывает всю годовую историю с 2018 г.: «голодные» 2020–2023 гг. "
        "снизу и пик открытий сверху; потолок — цель стратегии-2028 «не более 5,5 % без M&A» "
        "(data/facts/strategy.json)."),
    "ev_ebitda": (
        "EV/EBITDA LTM клеток «дно» и «частичная сходимость», коридор по мирам ставок (мир N "
        "выше: ниже реальная ставка). Основание — история «Ленты» на конец года по ценам MOEX "
        "и аналоги на одной базе IAS 17 (лист «Оценка»); цен истории в фактах выпуска нет, "
        "поэтому рядом печатается нынешний мультипликатор «Ленты» на той же базе "
        "(`market.peers_same_base`)."),
}
# Первый год годовой истории, которую коридор обязан покрывать целиком (см.
# `CORRIDOR_BASIS`); у EV/EBITDA истории в фактах нет.
CORRIDOR_HISTORY_FROM = {"margin_range": "FY2022", "capex_range": "FY2018"}
# Строки денежного capex IAS 17 в ДДС листа фактов (определение `capex_cash`).
CAPEX_CASH_LINES = ("capex_ppe", "capex_intangibles", "capex_leasehold_rights")


def _history_ratio(block: dict, kind: str) -> float | None:
    """Маржа EBITDA или capex/выручка одной строки `accounting_base.json` (IAS 17)."""
    pl, cf = block.get("pl") or {}, block.get("cf") or {}
    revenue = (pl.get("revenue") or {}).get("v")
    if not revenue:
        return None
    if kind == "margin_range":
        ebitda = (pl.get("ebitda") or {}).get("v")
        return None if ebitda is None else ebitda / revenue
    lines = [(cf.get(name) or {}).get("v") for name in CAPEX_CASH_LINES]
    if all(v is None for v in lines):
        return None
    return -sum(v or 0.0 for v in lines) / revenue


def corridor_history(facts_dir=None) -> dict:
    """История «Ленты» против коридоров гейтов — из фактов, не из кода.

    {гейт: {annual: {FYгггг: доля}, halves_range: [мин, макс], window_from,
    annual_outside: [годы вне коридора], window_outside: [годы окна вне
    коридора]}} для `margin_range` и `capex_range`. Нет файла фактов — {}.
    """
    import json
    from pathlib import Path as _Path

    from model.paths import FACTS_DIR

    path = _Path(facts_dir or FACTS_DIR) / "accounting_base.json"
    if not path.exists():
        return {}
    base = json.loads(path.read_text(encoding="utf-8"))
    corridors = gate_corridors()
    out = {}
    for key in ("margin_range", "capex_range"):
        annual = {}
        for period, row in sorted((base.get("fy") or {}).items()):
            value = _history_ratio((row or {}).get("ias17") or {}, key)
            if value is not None:
                annual[period] = value
        halves = [v for v in (_history_ratio((row or {}).get("ias17") or {}, key)
                              for row in (base.get("halves") or {}).values()) if v is not None]
        low, high = corridors[key]
        outside = [p for p, v in annual.items() if not low <= v <= high]
        start = CORRIDOR_HISTORY_FROM[key]
        out[key] = dict(
            annual=annual,
            halves_range=[min(halves), max(halves)] if halves else None,
            window_from=start,
            annual_outside=outside,
            window_outside=[p for p in outside if p >= start])
    return out


def load_gate_explanations(path=None, *, today=None) -> dict:
    """Читает письменные объяснения гейтов. Отсутствие файла — не молчание.

    Формат записи (`data/assumptions/gate_explanations.yaml`):

        ключ_гейта:
          explanation: текст для человека
          expected_mass: 0.18                # точка — как раньше
          expected_mass_range: [0.01, 0.08]  # необязательно: масса законно дрейфует
          valid_until: 2027-06-30            # действует ВКЛЮЧИТЕЛЬНО по эту дату
          corridor: [0.045, 0.080]           # коридор гейта (margin_range, capex_range; у ev_ebitda — по мирам)

    `today` — день, на который читается срок; по умолчанию сегодняшний. Явный
    день нужен тестам механизма: иначе они проверяли бы не механизм, а то,
    не истёк ли срок в файле на день прогона.
    """
    import datetime as _dt
    from pathlib import Path as _Path

    import yaml

    from model.paths import BOOK_DIR

    path = _Path(path) if path else BOOK_DIR / "gate_explanations.yaml"
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    today = today or _dt.date.today()
    out = {}
    for key, spec in raw.items():
        until = _parse_until(key, spec.get("valid_until"))
        stale = explanation_is_stale(until, today)
        out[key] = dict(explanation=str(spec.get("explanation", "")).strip(),
                        expected_mass=spec.get("expected_mass"),
                        expected_mass_range=_parse_mass_range(
                            key, spec.get("expected_mass_range")),
                        valid_until=until,
                        stale=stale,
                        expiring=not stale and explanation_expires_soon(until, today),
                        corridor=_parse_corridor(key, spec.get("corridor")))
    return out


def summarize_gates(findings: list["Finding"], mass_by_label: dict[str, float],
                    explanations: dict | None = None, *, today=None) -> list[GateSummary]:
    """Сводит гейты по ключу, взвешивая вероятностью клеток.

    Считать гейты по числу клеток недостаточно: три клетки с суммарной
    вероятностью 2 % и три с вероятностью 40 % — разные новости.
    """
    explanations = (explanations if explanations is not None
                    else load_gate_explanations(today=today))
    grouped: dict[str, list[Finding]] = {}
    for f in findings:
        if f.severity is Severity.GATE:
            grouped.setdefault(f.key, []).append(f)

    out = []
    for key, items in sorted(grouped.items()):
        labels = sorted({f.label for f in items if f.label})
        mass = sum(mass_by_label.get(f.label, 0.0) for f in items)
        spec = explanations.get(key) or {}
        # Текст причины — только у находок уровня выпуска и без повторов.
        messages = list(dict.fromkeys(f.message for f in items if f.label == RELEASE_LABEL))
        out.append(GateSummary(
            key=key, cells=len(items), mass=mass, labels=labels,
            explained=bool(spec.get("explanation")),
            explanation=spec.get("explanation", ""),
            expected_mass=spec.get("expected_mass"),
            stale=bool(spec.get("stale")),
            pending=key in PENDING_GATES,
            advisory=key in ADVISORY_GATES,
            expected_mass_range=spec.get("expected_mass_range"),
            valid_until=spec.get("valid_until"),
            expiring=bool(spec.get("expiring")),
            message="; ".join(messages),
        ))
    return out


def explanation_deadline_alarms(explanations: dict, firing, *, today) -> list[str]:
    """Совещательные тревоги по срокам объяснений — строки для журнала такта.

    Два случая, оба НЕ останавливают выпуск (код тревоги `ALARM_EXIT` у
    `ops/build_release.py`):

    * срок истекает в ближайшие `EXPLANATION_WARN_DAYS` дней — продлить или
      переписать, пока витрина работает;
    * срок уже прошёл у гейта, который сейчас НЕ срабатывает: сборку это не
      блокирует (объяснять нечего), но запись в файле мёртвая, и тест
      `test_explanations_are_not_stale` в такте её не пропустит;
    * срок прошёл у СОВЕЩАТЕЛЬНОГО гейта, который срабатывает (`guidance_gap`
      книги «Ленты» — объяснение до отчёта за 3 кв.): такой гейт сборку не
      блокирует никогда, и без этой строки его просроченный текст молчал бы.

    Просроченное объяснение срабатывающего БЛОКИРУЮЩЕГО гейта сюда не входит:
    оно блокирует сборку (`GateSummary.blocking`), и об этом говорит строка
    «ГЕЙТ: …».
    """
    firing = set(firing)
    out = []
    for key in sorted(explanations):
        spec = explanations[key] or {}
        until = spec.get("valid_until")
        if until is None:
            continue
        state = "гейт сейчас срабатывает" if key in firing else "гейт сейчас не срабатывает"
        if key in firing and key in ADVISORY_GATES:
            state += ", совещательный — публикацию не блокирует"
        if spec.get("stale"):
            if key not in firing or key in ADVISORY_GATES:
                out.append(f"объяснение гейта {key} просрочено: срок был {until:%d.%m.%Y} ({state}): "
                           "продлить, переписать или удалить запись в "
                           "data/assumptions/gate_explanations.yaml")
        elif spec.get("expiring"):
            days = (until - today).days
            left = "сегодня последний день" if days == 0 else f"осталось {days} дн"
            after = ("со следующего дня после срока — тревога каждый такт" if key in ADVISORY_GATES
                     else "со следующего дня после срока сборка встанет")
            out.append(f"объяснение гейта {key} истекает {until:%d.%m.%Y}: продлить или "
                       f"переписать в data/assumptions/gate_explanations.yaml ({left}, "
                       f"{state}; {after})")
    return out
