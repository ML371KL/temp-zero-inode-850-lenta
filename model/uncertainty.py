"""Полоса неопределённости печатаемого центра и обратный DCF (книга 1.4, A-V9).

ЧТО ПЕЧАТАЕТСЯ С КНИГИ 1.4. Заголовок — не точка «все суждения в центре
книги», а МЕДИАНА распределения центра по суждениям книги в их диапазонах,
рядом — полосы 80 % (P10–P90) и 50 % (P25–P75) и P(центр ниже рынка). Точка
«все суждения в центре» печатается тоже, но второй строкой: из-за
асимметричных диапазонов (capex вверх шире, маржа вниз шире) она не
совпадает с медианой — на книге 1.4 это 1 262 против 961 ₽.

КАК СЧИТАЕТСЯ (определение — книга, A-V9; числа книги на 2 000 прогонах —
`results.json`, регрессия `tests/test_book_results.py`).

* Каждая ось — суждение книги с диапазоном [low; high] (`valuation.uncertainty.axes`).
  Точка оси s ∈ [−1; 1]: s = 0 — значение книги, ±1 — концы диапазона, между
  ними линейно по половинам (`axis_overrides`).
* s распределена треугольно с модой 0 (`tri_s`), поэтому МЕДИАНА каждого
  суждения — значение книги («центр суждения = медиана»).
* Оси независимы, выборка — латинский гиперкуб. ПОРЯДОК СЛУЧАЙНЫХ ЧИСЕЛ —
  часть определения: `random.Random(seed)`; для каждой оси по порядку —
  перестановка страт `rng.shuffle(list(range(n)))`; затем для каждого прогона
  i и каждой оси j: u = (perm_j[i] + rng.random()) / n. Другой порядок даёт
  другую выборку и другие квантили — не ошибку «в методе», но расхождение с
  таблицами книги, которое регрессия до 1e-12 обязана поймать.
* Каждый прогон — ВСЯ сетка тем же движком и тем же отображением V0 → цена,
  что печатаемое число (`model.engine.with_overrides` → `build_grid` →
  `layers` → `fair_value`).

Единицы: цена — ₽/акцию, V0 — млрд ₽.
"""

from __future__ import annotations

import atexit
import copy
import math
import os
import pickle
import random
import sys
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from multiprocessing import get_context, parent_process

from model.book import (limited_liability_rule, median_draws, median_refine, reverse_bounds,
                        validate_book)
from model.engine import with_overrides
from model.grid import (CENTER_EV_SEARCH, NEUTRAL_MARGIN_SEARCH, PERCENTILES, CenterEV,
                        build_grid, fair_value, layers, own_macro_confidence, report_period,
                        solve_increasing, with_observation)


# ------------------------------------------------------------------- оси


def get_path(A: dict, dotted: str):
    """Значение книги по пути вида `tax.permanent_addback_pct.2028` или
    `bridge.items[put].amount` (`model.book.get_path`)."""
    from model.book import get_path as _get_path

    return _get_path(A, dotted)


def axis_spec(ax: dict) -> dict:
    """Ось суждения в единой форме {name, paths, kind, low, high}.

    Старые строки `sensitivities` (`path` + `low`/`high` или
    `shift_low`/`shift_high`) приводятся к ней без изменения смысла; новые
    могут назвать несколько путей (`paths`) — сдвиг или значение применяются к
    каждому.
    """
    paths = ax.get("paths") or [ax["path"]]
    if "shift_low" in ax:
        return dict(name=ax["name"], paths=paths, kind="shift", low=ax["shift_low"], high=ax["shift_high"])
    kind = ax.get("kind") or ("dict" if isinstance(ax["low"], dict) else "value")
    return dict(name=ax["name"], paths=paths, kind=kind, low=ax["low"], high=ax["high"])


def axis_value(kind: str, v):
    """Подмена одного пути для значения оси `v` её вида: сдвиг — `{"__shift__": v}`,
    множитель — `{"__scale__": v}`, значение и выбор — само `v`."""
    if kind == "shift":
        return {"__shift__": v}
    if kind == "scale":
        return {"__scale__": v}
    return v


def axis_book_value(A: dict, kind: str, path: str) -> float:
    """Значение оси в точке книги: сдвиг — 0, множитель — 1, значение — число книги."""
    if kind == "shift":
        return 0.0
    if kind == "scale":
        return 1.0
    return float(get_path(A, path))


def axis_overrides(A: dict, ax: dict, s: float) -> dict:
    """Подмены книги для точки оси s ∈ [−1; 1].

    s = 0 — значение книги, s = −1 / +1 — концы диапазона (low / high), между
    ними линейно по половинам. Сдвиг (`shift`) применяется ко всем числам
    траектории (`{"__shift__": d}`, кроме `LT_from`); множитель (`scale`) —
    ×(1 + |s|·(конец − 1)) ко всем числам (`{"__scale__": f}`); числовой
    словарь весов (`dict`) интерполируется поэлементно и нормируется на единицу;
    нечисловой словарь (выбор сценария) и строковое значение (`choice`) берутся
    концом со стороны s (в центре — книга).
    """
    ax = axis_spec(ax)
    out = {}
    for path in ax["paths"]:
        if ax["kind"] == "shift":
            out[path] = {"__shift__": s * ax["high"] if s >= 0 else -s * ax["low"]}
        elif ax["kind"] == "scale":
            end = float(ax["high"] if s >= 0 else ax["low"])
            out[path] = {"__scale__": 1.0 + abs(s) * (end - 1.0)}
        elif ax["kind"] == "choice":
            out[path] = copy.deepcopy(ax["high"] if s > 0 else ax["low"] if s < 0
                                      else get_path(A, path))
        elif ax["kind"] == "dict":
            base, end = get_path(A, path), (ax["high"] if s >= 0 else ax["low"])
            w = abs(s)
            if w >= 1.0:
                out[path] = copy.deepcopy(end)
            elif all(isinstance(v, (int, float)) for v in base.values()):
                mix = {k: base[k] + w * (end[k] - base[k]) for k in base}
                tot = sum(mix.values())
                out[path] = {k: v / tot for k, v in mix.items()}
            else:
                out[path] = copy.deepcopy(end if w >= 1.0 else base)
        else:
            base = float(get_path(A, path))
            end = float(ax["high"] if s >= 0 else ax["low"])
            out[path] = base + abs(s) * (end - base)
    return out


# ------------------------------------------------------- статистика


def tri_s(u: float) -> float:
    """Квантиль симметричного треугольного распределения на [−1; 1] с модой 0."""
    # «u < 1/2» записано как 2u < 1: удвоение точное, сравнение то же.
    return math.sqrt(2.0 * u) - 1.0 if 2.0 * u < 1.0 else 1.0 - math.sqrt(2.0 * (1.0 - u))


def quantile(sorted_vals: list[float], q: float) -> float:
    """Квантиль с линейной интерполяцией (тип 7): h = (n − 1)·q."""
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    h = (n - 1) * q
    lo = int(math.floor(h))
    hi = min(lo + 1, n - 1)
    return sorted_vals[lo] + (h - lo) * (sorted_vals[hi] - sorted_vals[lo])


def ranks(values: list[float]) -> list[float]:
    """Ранги 1..n; при равенстве — по порядку появления."""
    order = sorted(range(len(values)), key=lambda i: (values[i], i))
    out = [0.0] * len(values)
    for pos, i in enumerate(order):
        out[i] = float(pos + 1)
    return out


def pearson(x: list[float], y: list[float]) -> float:
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sxx, syy = sum((a - mx) ** 2 for a in x), sum((b - my) ** 2 for b in y)
    return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else 0.0


def axis_contributions(names: list[str], points: list[list[float]], central: list[float]) -> list[dict]:
    """Вклады осей в полосу: ранговая корреляция точки оси с центром прогона и
    доля её квадрата в сумме квадратов, по убыванию доли. Отдельной функцией —
    чтобы такт сверял её с таблицами книги по прогонам файла, без полосы."""
    rc = ranks(central)
    rho = [pearson(ranks([s[j] for s in points]), rc) for j in range(len(names))]
    tot = sum(x * x for x in rho) or 1.0
    return sorted([dict(axis=a, rank_corr=r_, share=r_ * r_ / tot) for a, r_ in zip(names, rho)],
                  key=lambda x: -x["share"])


# -------------------------------------------------------------- прогон


def evaluate(A: dict, overrides: dict | None = None):
    """Полный расчёт ядром на книге с подменами: (книга, клетки, слои, результат).

    Та же цепочка, что у выпуска: подмены → 36 клеток → три слоя → печатаемый
    результат. Своих формул здесь нет.
    """
    trial = with_overrides(A, overrides) if overrides else A
    cells = build_grid(trial)
    layer_map = layers(trial, cells)
    return trial, cells, layer_map, fair_value(trial, cells, layer_map)


def draw_points(n: int, n_axes: int, seed: int) -> list[list[float]]:
    """Точки осей s[i][j] латинского гиперкуба — в ТОМ порядке случайных чисел,
    что задан книгой (см. шапку модуля). Вынесено отдельно, чтобы порядок можно
    было проверить без сетки."""
    rng = random.Random(int(seed))
    perms = []
    for _ in range(n_axes):
        p = list(range(n))
        rng.shuffle(p)
        perms.append(p)
    return [[tri_s((perms[j][i] + rng.random()) / n) for j in range(n_axes)] for i in range(n)]


def uncertainty(A: dict, draws: int | None = None) -> dict:
    """Полоса неопределённости печатаемого центра (A-V9).

    Возвращает основу блока `uncertainty` в `results.json`: квантили центра,
    низа и верха диапазона, V0 обоих слоёв и их λ-смеси; среднее центра;
    P(центр < рынка); вклады осей (доля квадрата ранговой корреляции с
    центром); отсортированные центры и низ/верх каждого прогона в порядке
    прогонов (центр при любом λ = низ + λ·(верх − низ) — витрина пересчитывает
    полосу ползунком без нового прогона).
    """
    # Книга могла прийти не через `load_book` (живые входы, подмены): ключи и
    # слова `kind` осей проверяются и здесь — 2 000 сеток на опечатке не нужны.
    validate_book(A)
    U = A["valuation"]["uncertainty"]
    n = int(draws or U["draws"])
    axes = [axis_spec(a) for a in U["axes"]]
    lam = own_macro_confidence(A)
    px = A["market"]["price"]
    rows = band_rows(A, n)
    qlist = U.get("quantiles", list(PERCENTILES))

    def qs(vals):
        v = sorted(vals)
        return {f"{q:.2f}": quantile(v, q) for q in qlist}

    central = [r["central"] for r in rows]
    out = dict(draws=n, seed=int(U["seed"]), market=px, own_macro_confidence=lam,
                central=qs(central), low=qs([r["low"] for r in rows]), high=qs([r["high"] for r in rows]),
                v0_own=qs([r["v0_own"] for r in rows]), v0_market=qs([r["v0_market"] for r in rows]),
                v0_lambda=qs([r["v0_market"] + lam * (r["v0_own"] - r["v0_market"]) for r in rows]),
                mean_central=sum(central) / n, p_central_below_market=sum(1 for c in central if c < px) / n,
                contributions=axis_contributions([a["name"] for a in axes], [r["s"] for r in rows], central),
                central_sorted=[round(c, 2) for c in sorted(central)],
                low_draws=[round(r["low"], 1) for r in rows], high_draws=[round(r["high"], 1) for r in rows],
                # V0, страйк, σ и срок обоих слоёв и дисконт за управление каждого
                # прогона: «EV против V*» медианы считается отображением, без
                # новых сеток (`center_ev_median`).
                layer_draws=[r["mapping"] for r in rows])
    limited = limited_liability_share(A, rows)
    if limited is not None:
        out["limited_liability"] = limited
    return out


def limited_liability_share(A: dict, rows: list[dict]) -> dict | None:
    """Доля прогонов полосы, где ограниченная ответственность акционера существенна.

    Прогон считается, если V0 хотя бы одного из двух слоёв диапазона ниже
    `v0_to_d_min`·D своего слоя (`valuation.headline.limited_liability`). Доля
    выше `max_share` — совещательный гейт `limited_liability`
    (`model.checks.check_limited_liability`): внутренняя стоимость max(V0 − D, 0)
    игнорирует опцион акционера, и там, где V0 близко к D, это уже не мелочь —
    нужна новая версия книги со сменой метода. Нет блока в книге — None.
    """
    rule = limited_liability_rule(A)
    if rule is None or not rows:
        return None
    k = rule["v0_to_d_min"]
    hits = sum(1 for r in rows
               if r["v0_own"] < k * r["d"] or r["v0_market"] < k * r.get("d_market", r["d"]))
    share = hits / len(rows)
    return dict(v0_to_d_min=k, max_share=rule["max_share"], draws=len(rows), hits=hits,
                share=share, fired=share > rule["max_share"])


def band_rows(A: dict, n: int) -> list[dict]:
    """Прогоны полосы A-V9 на n точках: оси книги, сетка, слои, результат.

    Точки — `draw_points` с seed книги, поэтому любые две полосы с одним n —
    ОБЩИЕ случайные числа: одна и та же выборка суждений на разных книгах.
    Точки считает этот процесс; прогоны — пул процессов (`parallel_rows`),
    если `workers()` > 1 и прогонов не меньше PARALLEL_MIN_DRAWS, иначе здесь же.
    """
    U = A["valuation"]["uncertainty"]
    axes = [axis_spec(a) for a in U["axes"]]
    points = draw_points(n, len(axes), U["seed"])
    # В рабочем процессе пула (или скрипте, запущенном заново при spawn) — без вложенного пула.
    k = workers() if n >= PARALLEL_MIN_DRAWS and parent_process() is None else 1
    rows = parallel_rows(A, axes, points, k) if k > 1 else None
    return rows if rows is not None else band_chunk(A, axes, points)


def band_chunk(A: dict, axes: list[dict], points: list[list[float]]) -> list[dict]:
    """Прогоны полосы в точках `points` по порядку — одна функция для
    последовательного расчёта и для рабочего процесса пула."""
    rows = []
    for s in points:
        ov = {}
        for ax, sj in zip(axes, s):
            ov.update(axis_overrides(A, ax, sj))
        trial, _, layer_map, fv = evaluate(A, ov)
        own, market = layer_map["analytical"], layer_map["macro_neutral"]
        rows.append(dict(s=s, low=fv.low, central=fv.central, high=fv.high,
                         v0_own=own.v0, v0_market=market.v0, d=own.claims, d_market=market.claims,
                         mapping=draw_mapping(trial, own, market)))
    return rows


def draw_mapping(trial: dict, own, market) -> dict:
    """Отображение V0 → цена обоих слоёв прогона — для «EV против V*» медианы
    без новых сеток (`center_ev_median`): {own: [V0, D], market: [V0, D],
    governance} (g — ось полосы, своё у каждого прогона)."""
    governance = trial["valuation"].get("governance_discount", 0.0)
    return dict(own=[own.v0, own.claims], market=[market.v0, market.claims],
                governance=governance)


# ------------------------------------------------- параллельный расчёт полосы
#
# Прогоны полосы независимы: каждый — функция книги и своей точки. Пул считает
# их кусками в порядке точек, куски склеиваются в том же порядке — числа те же
# бит в бит (тот же код, тот же Python, те же входы). Рабочий — чистый
# интерпретатор (`spawn` на любой ОС) с кодом с диска: подмены в памяти этого
# процесса (monkeypatch, мутанты) ему не видны — набор тестов поэтому считает
# последовательно (`tests/conftest.py`).

WORKERS_ENV = "LENTA_WORKERS"
MAX_WORKERS = 7
"""Потолок `auto` (ядер − 1): на большой машине пул не берёт все ядра и память."""
PARALLEL_MIN_DRAWS = 32
"""Меньше прогонов — последовательно: запуск и пересылка дороже выигрыша."""
CHUNKS_PER_WORKER = 4
"""Кусков на рабочего: ядра неравные (P и E у ноутбука), мелкие куски выравнивают нагрузку."""

_POOL: dict = dict(executor=None, workers=0, failed=False)


def workers() -> int:
    """Число рабочих процессов полосы: `LENTA_WORKERS` (целое ≥ 1 или `auto`;
    по умолчанию auto = max(1, min(ядер − 1, MAX_WORKERS))). 1 — последовательно."""
    raw = os.environ.get(WORKERS_ENV, "").strip().lower()
    if raw in ("", "auto"):
        return max(1, min((os.cpu_count() or 1) - 1, MAX_WORKERS))
    if not raw.isdigit() or int(raw) < 1:
        raise ValueError(f"{WORKERS_ENV}={raw!r}: нужно целое ≥ 1 или auto")
    return min(int(raw), 61)          # 61 — предел числа процессов пула на Windows


def _executor(k: int) -> ProcessPoolExecutor:
    """Пул на k процессов — ленивый, один на процесс (другое k — новый пул)."""
    if _POOL["executor"] is None or _POOL["workers"] != k:
        close_pool()
        _POOL.update(executor=ProcessPoolExecutor(max_workers=k, mp_context=get_context("spawn")),
                     workers=k)
    return _POOL["executor"]


def close_pool() -> None:
    """Закрывает пул полосы (и при выходе из процесса — `atexit`)."""
    executor, _POOL["executor"], _POOL["workers"] = _POOL["executor"], None, 0
    if executor is not None:
        executor.shutdown(wait=True, cancel_futures=True)


atexit.register(close_pool)


def _chunk_of(blob: bytes, points: list[list[float]]) -> list[dict]:
    """Кусок прогонов в рабочем процессе: книга и оси приходят одним pickle."""
    A, axes = pickle.loads(blob)
    return band_chunk(A, axes, points)


def parallel_rows(A: dict, axes: list[dict], points: list[list[float]], k: int
                  ) -> list[dict] | None:
    """Прогоны пулом из k процессов кусками по порядку точек.

    None — пул недоступен (не стартовал, сломался, книга не пересылается):
    сообщение в stderr, и до конца процесса полоса считается последовательно.
    Ошибка самого расчёта в рабочем поднимается здесь, как при последовательном.
    """
    if _POOL["failed"]:
        return None
    size = -(-len(points) // (k * CHUNKS_PER_WORKER))
    chunks = [points[i:i + size] for i in range(0, len(points), size)]
    try:
        blob = pickle.dumps((A, axes), protocol=pickle.HIGHEST_PROTOCOL)
    except Exception as exc:                            # noqa: BLE001 — любая: не пересылается
        return _unavailable(exc)
    try:
        parts = list(_executor(k).map(_chunk_of, [blob] * len(chunks), chunks))
    except (BrokenProcessPool, MemoryError, OSError, NotImplementedError, pickle.PicklingError) as exc:
        # OSError самого расчёта тоже здесь — последовательный расчёт поднимет его снова.
        return _unavailable(exc)
    return [row for part in parts for row in part]


def _unavailable(exc: BaseException) -> None:
    _POOL["failed"] = True
    close_pool()
    print(f"параллельный расчёт недоступен: {type(exc).__name__}: {exc}, считаю последовательно",
          file=sys.stderr)
    return None


def headline_of(A: dict, fv, un: dict) -> dict:
    """ПЕЧАТАЕМЫЙ заголовок: медиана распределения центра и полосы.

    Шаг печати — `valuation.headline.print_step`. Точка «все суждения в
    центре книги» (`fv.central`) печатается рядом.
    """
    step = float(A["valuation"]["headline"].get("print_step", 50.0))
    rnd = lambda x: int(round(x / step)) * step
    c = un["central"]
    return dict(method="judgement_median", central=c["0.50"], band=[c["0.10"], c["0.90"]], inner=[c["0.25"], c["0.75"]],
                printed_central=rnd(c["0.50"]), printed_band=[rnd(c["0.10"]), rnd(c["0.90"])],
                printed_inner=[rnd(c["0.25"]), rnd(c["0.75"])],
                point_at_book_centres=fv.central, printed_point=rnd(fv.central),
                p_central_below_market=un["p_central_below_market"], market=un["market"], draws=un["draws"])


def reverse_dcf(A: dict) -> list[dict]:
    """Что заложено в рыночную цену: значение ОДНОГО суждения, при котором
    ТОЧКА при центральных значениях (не печатаемая медиана) равна рынку
    (остальное — как в книге), и лежит ли оно в диапазоне книги. Ось книги:
    {name, paths, kind: value|shift, search, range}. Для медианы —
    `reverse_dcf_median`; там это решение — старт поиска.

    Бисекция 50 шагов на отрезке `search`; если на концах отрезка центр по
    одну сторону от рынка — значение `None` («недостижимо в пределах поиска»),
    а не притворное решение.
    """
    # Как и у полосы: книга с подменами проверяется здесь же — незнакомый `kind`
    # иначе читался бы как замена значением, а не как сдвиг.
    validate_book(A)
    px, out = A["market"]["price"], []
    for ax in A.get("reverse_dcf", []) or []:
        kind = ax.get("kind", "value")

        def gap(v, ax=ax, kind=kind):
            ov = {p: axis_value(kind, v) for p in ax["paths"]}
            return evaluate(A, ov)[3].central - px

        lo, hi = ax["search"]
        flo, fhi = gap(lo), gap(hi)
        value = None
        if flo * fhi <= 0:
            for _ in range(50):
                mid = (lo + hi) / 2
                fm = gap(mid)
                if (fm <= 0) == (flo <= 0):
                    lo, flo = mid, fm
                else:
                    hi = mid
            value = (lo + hi) / 2
        book_value = None if kind == "shift" else axis_book_value(A, kind, ax["paths"][0])
        rlo, rhi = ax["range"]
        out.append(dict(name=ax["name"], paths=ax["paths"], kind=kind, book_value=book_value, value=value,
                        range=[rlo, rhi], inside_range=(value is not None and rlo <= value <= rhi)))
    return out


# ------------------------------------------ диагностики для медианы (книга 1.5)
#
# Книга 1.4 решала «что в цене», «EV против V*» и нейтральную маржу для ТОЧКИ
# при центральных значениях (1 262 ₽), а печатала медиану (961 ₽): эти числа
# отвечали на вопрос о другом числе (аудит 26.09.2026, 4.1). С
# `valuation.headline.diagnostics: median` они решаются для печатаемой медианы.
#
# ОБЩИЕ СЛУЧАЙНЫЕ ЧИСЛА. Каждый пересчёт медианы — полоса на
# `valuation.uncertainty.median_draws` прогонах с seed книги: выборка суждений
# одна и та же, меняется только подменённое суждение (или новое наблюдение).
# Сдвиг медианы на этих прогонах прибавляется к печатаемой медиане полосы
# (`MedianAnchor`): при значении книги число ровно печатаемое, а шум малой
# выборки уходит в разность.
#
# ПОИСК КОРНЯ. Медиана кусочно-гладкая по значению суждения; одна полоса —
# `median_draws` сеток, поэтому бисекция в 50 шагов недоступна. Старт —
# точечное решение, отодвинутое от книги в SEED_FACTOR раз (медиане нужно
# ≈2,1–3,1 сдвига точки на книге 1.4), затем секущая наружу до смены знака
# и обратная квадратичная интерполяция (иначе секущая) внутри отрезка;
# остановка — |медиана − цель| ≤ MEDIAN_TOLERANCE_RUB на `median_draws`
# прогонах. С `valuation.uncertainty.median_refine: 1` решение строки внутри
# диапазона книги уточняется одним шагом секущей на полной полосе
# (`refine_on_full_band`): допуск 5 ₽ — у оценки на малой выборке, а шум её
# сдвига бывает больше.
#
# ГРАНИЦЫ ОСИ. Подмена значения оси-значения полосы переносит её центр; с
# `valuation.uncertainty.reverse_bounds: follow_center` граница, которую
# центр пересёк, следует за ним (`follow_center`) — иначе обе половины
# треугольника шли бы от центра в одну сторону, и медиана суждения в прогонах
# не была бы подменённым значением.

SEED_FACTOR = 9 / 4
"""Во сколько раз старт поиска дальше от книги, чем точечное решение."""
MEDIAN_TOLERANCE_RUB = 5
"""Корень найден, когда медиана отличается от цели не больше чем на 5 ₽ —
десятая часть шага печати; шум медианы на 200 прогонах больше."""
MEDIAN_MAX_STEPS = 12
"""Предел пересчётов медианы на один корень (страховка от зацикливания)."""


def _median(values) -> float:
    return quantile(sorted(values), 1 / 2)


def band_medians(A: dict, n: int) -> dict:
    """Медианы центра, низа и верха полосы на n прогонах книги."""
    rows = band_rows(A, n)
    return {k: _median([r[k] for r in rows]) for k in ("central", "low", "high")}


class MedianAnchor:
    """Медиана при подмене = печатаемая медиана + её сдвиг на общих числах.

    `band` — полоса выпуска (печатаемая медиана), пересчёт — на
    `median_draws` прогонах. `evaluations` — сколько полос пересчитано
    (каждая — `median_draws` сеток), чтобы бюджет был виден.
    """

    def __init__(self, A: dict, band: dict):
        self.n = median_draws(A)
        self.base = band_medians(A, self.n)
        self.full = {k: band[k]["0.50"] for k in ("central", "low", "high")}
        self.evaluations = 1
        self.full_grids = 0             # сетки полных полос уточнения (`refine_on_full_band`)

    def at(self, book: dict) -> dict:
        self.evaluations += 1
        m = band_medians(book, self.n)
        return {k: self.full[k] + (m[k] - self.base[k]) for k in m}


def _bracketed(g, a: float, fa: float, b: float, fb: float, c, fc, steps: int
               ) -> tuple[float, int]:
    """Корень g на отрезке [a; b] (fa и fb разных знаков).

    Шаг — обратная квадратичная интерполяция по трём последним точкам (a, b
    и отброшенной c), если она попадает внутрь отрезка, иначе секущая по
    концам. Отрезок сжимается заменой конца того же знака. Не сошлось за
    `steps` пересчётов — оценка секущей по последнему отрезку.
    """
    used = 0
    for _ in range(steps):
        x = None
        if c is not None and len({fa, fb, fc}) == 3:
            x = (a * fb * fc / ((fa - fb) * (fa - fc)) + b * fa * fc / ((fb - fa) * (fb - fc))
                 + c * fa * fb / ((fc - fa) * (fc - fb)))
            if not min(a, b) < x < max(a, b):
                x = None
        if x is None:
            x = (a * fb - b * fa) / (fb - fa)
        fx = g(x)
        used += 1
        if abs(fx) <= MEDIAN_TOLERANCE_RUB:
            return x, used
        if (fx > 0) == (fa > 0):
            c, fc, a, fa = a, fa, x, fx
        else:
            c, fc, b, fb = b, fb, x, fx
    return (a * fb - b * fa) / (fb - fa), used


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def solve_outward(g, b: float, gb: float, x1: float, end: float) -> tuple[float | None, int]:
    """Корень g между b и `end`: старт x1, секущая наружу до смены знака, затем
    `_bracketed` внутри найденного отрезка.

    `gb` — значение в b (уже известно). Если g не меняет знак до самого `end`,
    корня на отрезке поиска нет — None, а не притворное решение.
    """
    if abs(gb) <= MEDIAN_TOLERANCE_RUB:
        return b, 0
    prev, xa, fa, x, used = None, b, gb, x1, 0
    direction = _sign(end - b)
    while used < MEDIAN_MAX_STEPS:
        fx = g(x)
        used += 1
        if abs(fx) <= MEDIAN_TOLERANCE_RUB:
            return x, used
        if (fx > 0) != (fa > 0):
            c, fc = prev if prev else (None, None)
            root, more = _bracketed(g, xa, fa, x, fx, c, fc, MEDIAN_MAX_STEPS - used)
            return root, used + more
        if x == end:
            return None, used
        nxt = x - fx * (x - xa) / (fx - fa) if fx != fa else math.inf
        if not math.isfinite(nxt) or (nxt - x) * direction <= 0:
            nxt = x + (x - xa)
        nxt = min(nxt, end) if direction > 0 else max(nxt, end)
        prev, xa, fa, x = (xa, fa), x, fx, nxt
    return None, used


def value_axis(A: dict, path: str) -> int | None:
    """Номер оси-значения полосы, которая подменяет `path`; None — такой оси нет."""
    for j, ax in enumerate(A["valuation"]["uncertainty"]["axes"]):
        spec = axis_spec(ax)
        if spec["kind"] == "value" and path in spec["paths"]:
            return j
    return None


def follow_center(B: dict, j: int, value: float) -> None:
    """Граница оси j полосы в книге `B` следует за центром: low′ = min(low, v),
    high′ = max(high, v). Центр внутри диапазона оставляет ось как есть."""
    ax = B["valuation"]["uncertainty"]["axes"][j]
    ax["low"], ax["high"] = min(ax["low"], value), max(ax["high"], value)


def secant_step(x: float, fx: float, b: float, fb: float) -> float:
    """Шаг секущей к корню f по точкам (b, fb) и (x, fx); fx = fb — шага нет."""
    return x - fx * (x - b) / (fx - fb) if fx != fb else x


def refine_on_full_band(trial, x: float, b: float, base_gap: float, px: float,
                        draws: int) -> tuple[float, float]:
    """Уточнение корня медианы одним шагом секущей на полных прогонах полосы.

    `trial(x)` — книга с подменой, `b` — значение книги (сдвиг 0) с невязкой
    полной полосы `base_gap` (печатаемая медиана − рынок). Возвращает
    (уточнённое значение, невязку полной медианы в x). Проверки уточнённого
    значения ещё одной полосой нет — это вторая полоса на строку.
    """
    gap = band_medians(trial(x), draws)["central"] - px
    return secant_step(x, gap, b, base_gap), gap


def reverse_dcf_median(A: dict, band: dict, point_rows: list[dict] | None = None,
                       anchor: "MedianAnchor | None" = None) -> list[dict]:
    """Обратный DCF для ПЕЧАТАЕМОЙ медианы (книга 1.5, `diagnostics: median`).

    Для каждой оси `reverse_dcf` книги — значение суждения, при котором
    медиана распределения центра (A-V9) равна рыночной цене; остальные
    суждения — как в книге и гуляют в своих диапазонах полосы. Значение
    подменяется в книге ДО полосы: у оси, которая есть и в полосе, прогоны
    распределены вокруг нового центра до концов диапазона книги.

    Направление поиска — из точечного решения (`point_rows`, иначе
    `reverse_dcf(A)`): куда сдвиг суждения двигает точку, туда и медиану.
    Если точка недостижима на отрезке, направление — по точке на его концах.
    Строка — как у `reverse_dcf`, плюс `point_value` и `evaluations`
    (пересчётов медианы на оси); с уточнением на полной полосе — ещё
    `search_value` (решение поиска) и `search_gap_full` (невязка полной
    медианы в нём, ₽; None — строка не уточнялась). `anchor` — общая с
    таблицей «что даст отчёт» база медианы, чтобы не считать её дважды.
    """
    validate_book(A)
    px = A["market"]["price"]
    anchored = anchor or MedianAnchor(A, band)
    median_gap = anchored.full["central"] - px
    point_rows = point_rows if point_rows is not None else reverse_dcf(A)
    point_gap = evaluate(A)[3].central - px
    follow, refine = reverse_bounds(A) == "follow_center", median_refine(A)
    out = []
    for ax, row in zip(A.get("reverse_dcf", []) or [], point_rows):
        kind = ax.get("kind", "value")
        j = value_axis(A, ax["paths"][0]) if follow and kind == "value" else None

        def overrides(v, ax=ax, kind=kind):
            return {p: axis_value(kind, v) for p in ax["paths"]}

        def trial(v, overrides=overrides, j=j):
            B = with_overrides(A, overrides(v))
            if j is not None:
                follow_center(B, j, v)
            return B

        def gap(v, trial=trial):
            return anchored.at(trial(v))["central"] - px

        lo, hi = ax["search"]
        b = axis_book_value(A, kind, ax["paths"][0])
        p = row["value"]
        if p is not None:
            sense = _sign(p - b) * _sign(-point_gap)
        else:
            sense = _sign(evaluate(A, overrides(hi))[3].central - evaluate(A, overrides(lo))[3].central)
        direction = sense * _sign(-median_gap)
        value, used = None, 0
        if median_gap == 0:
            value = b
        elif direction:
            end = hi if direction > 0 else lo
            x1 = end if p is None else b + direction * SEED_FACTOR * abs(p - b)
            x1 = min(x1, hi) if direction > 0 else max(x1, lo)
            value, used = solve_outward(gap, b, median_gap, x1, end)
        rlo, rhi = ax["range"]
        refined = {}
        if refine:
            refined = dict(search_value=value, search_gap_full=None)
            if value is not None and value != b and rlo <= value <= rhi:
                draws = int(band["draws"])
                value, refined["search_gap_full"] = refine_on_full_band(
                    trial, value, b, median_gap, px, draws)
                anchored.full_grids += draws
        out.append(dict(name=ax["name"], paths=ax["paths"], kind=kind, book_value=row["book_value"],
                        value=value, range=[rlo, rhi],
                        inside_range=(value is not None and rlo <= value <= rhi),
                        point_value=p, evaluations=used, **refined))
    return out


def center_ev_median(A: dict, band: dict, lam: float | None = None) -> CenterEV:
    """«EV против V*» для медианы (книга 1.5): только отображение, без сеток.

    Активы обоих слоёв КАЖДОГО прогона полосы умножаются на общий x; x* — тот,
    при котором МЕДИАНА центров прогонов `низ + λ·(верх − низ)` равна рынку.
    V0 — медиана λ-смеси V0 слоёв по прогонам, V* = V0·x*; цена 1 % EV —
    медиана центров при x = 1,01 минус при x = 1. λ = 1 и λ = 0 — то же
    сравнение для слоя «свой макро-взгляд» и «рыночные ставки как есть».
    """
    lam = own_macro_confidence(A) if lam is None else lam
    px, shares = band["market"], A["facts"]["shares_out_mln"]
    draws = band["layer_draws"]

    def median_at(x: float) -> float:
        centres = []
        for d in draws:
            # То же отображение, что у полосы (`model.mapping`): max(V0·x − D, 0)
            # ·(1 − g)/акции с дисконтом за управление прогона.
            unit = (1.0 - d["governance"]) * 1000.0 / shares
            v0, claims = d["market"]
            lo_ = max(v0 * x - claims, 0.0) * unit
            v0, claims = d["own"]
            hi_ = max(v0 * x - claims, 0.0) * unit
            centres.append(lo_ + lam * (hi_ - lo_))
        return _median(centres)

    x_star = solve_increasing(lambda x: median_at(x) - px, *CENTER_EV_SEARCH)
    v0 = _median([d["market"][0] + lam * (d["own"][0] - d["market"][0]) for d in draws])
    return CenterEV(v0=v0, v_star=v0 * x_star, gap_vs_v_star=1.0 / x_star - 1.0,
                    rub_per_1pct_ev=median_at(1.01) - median_at(1.0))


def next_report_median(A: dict, band: dict, values=None, period: str | None = None,
                       anchor: "MedianAnchor | None" = None) -> tuple[list[dict], dict]:
    """Таблица «что даст отчёт» и нейтральная маржа для медианы (книга 1.5).

    Каждая строка — медианы центра, низа и верха полосы на `median_draws`
    прогонах книги с ещё одним наблюдением маржи; сдвиг на общих случайных
    числах прибавлен к печатаемой медиане. Нейтральная маржа — факт, при
    котором медиана не меняется: отрезок — соседние строки таблицы со сменой
    знака (нет смены — до конца отрезка NEUTRAL_MARGIN_SEARCH), внутри —
    квадратичный шаг по трём строкам (`_bracketed`). Возвращает (строки,
    нейтральная).
    """
    period = report_period(A, period)
    values = values if values is not None else A["joint"]["regime_update"].get("demo_values", [])
    anchored = anchor or MedianAnchor(A, band)
    base = anchored.full["central"]
    rows = []
    for value in values:
        m = anchored.at(with_observation(A, period, value))
        rows.append(dict(period=period, margin=value, central=m["central"], low=m["low"], high=m["high"]))

    def gap(margin: float) -> float:
        return anchored.at(with_observation(A, period, margin))["central"] - base

    known = sorted((r["margin"], r["central"] - base) for r in rows)
    lo, hi = NEUTRAL_MARGIN_SEARCH
    if not known:
        known = [(lo, gap(lo))]
    near = [m for m, g in known if abs(g) <= MEDIAN_TOLERANCE_RUB]
    cross = next((i for i in range(len(known) - 1)
                  if (known[i][1] > 0) != (known[i + 1][1] > 0)), None)
    if near:
        margin = near[0]
    elif cross is not None:
        # Третья точка для квадратичного шага — соседняя строка таблицы.
        (a, fa), (b, fb) = known[cross], known[cross + 1]
        c, fc = (known[cross + 2] if cross + 2 < len(known)
                 else known[cross - 1] if cross else (None, None))
        margin = _bracketed(gap, a, fa, b, fb, c, fc, MEDIAN_MAX_STEPS)[0]
    elif known[0][1] > 0:
        margin = solve_outward(gap, known[0][0], known[0][1], min(lo, known[0][0]), min(lo, known[0][0]))[0]
    else:
        margin = solve_outward(gap, known[-1][0], known[-1][1], max(hi, known[-1][0]), max(hi, known[-1][0]))[0]
    return rows, dict(period=period, central=base, margin=margin, evaluations=anchored.evaluations)
