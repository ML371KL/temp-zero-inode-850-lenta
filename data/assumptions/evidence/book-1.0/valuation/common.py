"""Общие функции листа «Оценка» (раздел 09) книги 1.0 «Ленты».

Пути — только через переменные окружения (абсолютных путей пользователя в коде нет):
  LENTA_PRIMARY_DIR   — первичка (по умолчанию <handoff>/reference/primary);
  LENTA_RESEARCH_DIR  — сырые ответы ISS и таблицы research (по умолчанию <handoff>/research);
  MAGNIT_850OA_DIR    — репозиторий 850oa, только чтение (лист беты X5), по умолчанию <handoff>/../magnit-850oa;
  LENTA_BOOK_DRAFT    — каталог book-draft (соседние листы и факты), по умолчанию выводится от места файла.
Стандартная библиотека + openpyxl/pypdfium2 из venv book-draft. Сеть не нужна (кроме fetch_iss.py --fetch).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOOK_DRAFT = Path(os.environ.get("LENTA_BOOK_DRAFT", HERE.parents[2]))
HANDOFF = BOOK_DRAFT.parent
INPUTS = HERE / "inputs"
OUT = HERE / "out"


def primary_dir() -> Path:
    return Path(os.environ.get("LENTA_PRIMARY_DIR", HANDOFF / "reference" / "primary"))


def research_dir() -> Path:
    return Path(os.environ.get("LENTA_RESEARCH_DIR", HANDOFF / "research"))


def magnit_dir() -> Path:
    return Path(os.environ.get("MAGNIT_850OA_DIR", HANDOFF.parent / "magnit-850oa"))


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def rel(p: Path) -> str:
    """Путь относительно handoff для печати (без пользовательских путей)."""
    try:
        return str(Path(p).resolve().relative_to(HANDOFF.resolve())).replace("\\", "/")
    except ValueError:
        return Path(p).name


class Log:
    """Печать в консоль и в текстовый файл одновременно."""

    def __init__(self, path: Path):
        self.path = path
        self.lines: list[str] = []

    def __call__(self, *a):
        s = " ".join(str(x) for x in a)
        print(s)
        self.lines.append(s)

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("\n".join(self.lines) + "\n", encoding="utf-8")


def dump_json(obj, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=False) + "\n", encoding="utf-8")


# ------------------------------------------------------------------ ISS
def load_iss(path: Path, price_field: str = "LEGALCLOSEPRICE", fallback: str | None = "CLOSE",
             skip_zero_volume: bool = True) -> dict[dt.date, float]:
    """Сырой ответ ISS history ({columns, data} или {'history': {...}}) → {дата: цена}.
    Берётся price_field, при его отсутствии — fallback; дни без цены пропускаются.
    skip_zero_volume — для акций: день без сделок (VOLUME 0) не берётся (legal close там не цена сделки)."""
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    if "history" in d:
        d = d["history"]
    cols = d["columns"]
    it = cols.index("TRADEDATE")
    ip = cols.index(price_field) if price_field in cols else None
    ifb = cols.index(fallback) if (fallback and fallback in cols) else None
    iv = cols.index("VOLUME") if "VOLUME" in cols else None
    out: dict[dt.date, float] = {}
    for r in d["data"]:
        if skip_zero_volume and iv is not None and r[iv] is not None and r[iv] == 0:
            continue
        v = r[ip] if ip is not None else None
        if (v is None or v == 0) and ifb is not None:
            v = r[ifb]
        if v is None or v == 0:
            continue
        out[dt.date.fromisoformat(r[it])] = float(v)
    return out


def load_iss_rows(path: Path) -> list[dict]:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    if "history" in d:
        d = d["history"]
    cols = d["columns"]
    return [dict(zip(cols, r)) for r in d["data"]]


# ------------------------------------------------------------------ статистика (без numpy)
def mean(x):
    return sum(x) / len(x)


def ols(y, X):
    """МНК с константой: y = a + X·b. X — список столбцов. Возвращает (коэф., остатки, XtX^-1)."""
    n = len(y)
    cols = [[1.0] * n] + [list(c) for c in X]
    k = len(cols)
    xtx = [[sum(cols[i][t] * cols[j][t] for t in range(n)) for j in range(k)] for i in range(k)]
    xty = [sum(cols[i][t] * y[t] for t in range(n)) for i in range(k)]
    inv = mat_inv(xtx)
    b = [sum(inv[i][j] * xty[j] for j in range(k)) for i in range(k)]
    res = [y[t] - sum(b[i] * cols[i][t] for i in range(k)) for t in range(n)]
    return b, res, inv, cols


def mat_inv(a):
    n = len(a)
    m = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[p] = m[p], m[c]
        pv = m[c][c]
        m[c] = [v / pv for v in m[c]]
        for r in range(n):
            if r != c:
                f = m[r][c]
                m[r] = [m[r][j] - f * m[c][j] for j in range(2 * n)]
    return [row[n:] for row in m]


def regress(y, X, lags_nw: int):
    """МНК + ст. ошибки: обычные и Ньюи — Уэста (Бартлетт, lags_nw). Возвращает dict."""
    b, res, inv, cols = ols(y, X)
    n, k = len(y), len(cols)
    s2 = sum(e * e for e in res) / (n - k)
    se_ols = [math.sqrt(s2 * inv[i][i]) for i in range(k)]
    # HAC
    S = [[0.0] * k for _ in range(k)]
    for t in range(n):
        g = [cols[i][t] * res[t] for i in range(k)]
        for i in range(k):
            for j in range(k):
                S[i][j] += g[i] * g[j]
    for L in range(1, lags_nw + 1):
        w = 1 - L / (lags_nw + 1)
        for t in range(L, n):
            g1 = [cols[i][t] * res[t] for i in range(k)]
            g0 = [cols[i][t - L] * res[t - L] for i in range(k)]
            for i in range(k):
                for j in range(k):
                    S[i][j] += w * (g1[i] * g0[j] + g0[i] * g1[j])
    V = [[sum(inv[i][a] * S[a][c] * inv[c][j] for a in range(k) for c in range(k)) for j in range(k)] for i in range(k)]
    ybar = mean(y)
    sst = sum((v - ybar) ** 2 for v in y)
    r2 = 1 - sum(e * e for e in res) / sst if sst > 0 else float("nan")
    return {"b": b, "se_ols": se_ols, "V_hac": V, "se_hac": [math.sqrt(max(V[i][i], 0)) for i in range(k)], "r2": r2, "n": n}


def norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def quantile(xs, q):
    s = sorted(xs)
    pos = q * (len(s) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def tri_from_halves(u: float, low: float, mode: float, high: float) -> float:
    """Правило A-V9 850oa: s ~ треугольное на [−1; 1] с модой 0, значение линейно по половинам диапазона."""
    s = math.sqrt(2 * u) - 1 if u < 0.5 else 1 - math.sqrt(2 * (1 - u))
    return mode + s * (mode - low) if s < 0 else mode + s * (high - mode)
