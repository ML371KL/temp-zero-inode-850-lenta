"""Чтение первички для листа nwc-tax (оборотный капитал, операционная касса, налог).

Все файлы берутся из каталога LENTA_PRIMARY_DIR (по умолчанию — reference/primary папки передачи
относительно этого файла: ../../../../reference/primary). Путей C:\\Users в коде нет.
Каждый прочитанный файл сверяется с sha256 из MANIFEST.md (если строка найдена).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import openpyxl
import pypdf

HERE = Path(__file__).resolve().parent
DEFAULT_PRIMARY = HERE.parents[3] / "reference" / "primary"
PRIMARY = Path(os.environ.get("LENTA_PRIMARY_DIR", str(DEFAULT_PRIMARY)))

DATABOOK = "Lenta_Q22026_DATABOOK.xlsx"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


_MANIFEST: dict[str, str] | None = None


def manifest() -> dict[str, str]:
    global _MANIFEST
    if _MANIFEST is None:
        _MANIFEST = {}
        mf = PRIMARY / "MANIFEST.md"
        if mf.exists():
            for line in mf.read_text(encoding="utf-8").splitlines():
                cells = [c.strip() for c in line.split("|")]
                if len(cells) > 5:
                    name = cells[1]
                    for c in cells:
                        if re.fullmatch(r"[0-9a-f]{64}", c):
                            _MANIFEST[name] = c
                            break
    return _MANIFEST


USED: dict[str, dict] = {}


def use(rel: str) -> Path:
    p = PRIMARY / rel
    if rel not in USED:
        digest = sha256(p)
        expected = manifest().get(rel)
        USED[rel] = {"sha256": digest, "manifest": expected,
                     "match": (expected == digest) if expected else None}
    return p


# ---------------------------------------------------------------- датабук
class Databook:
    """Лист датабука: колонки по (блок, период), строки по подписи. Блоки: 'IAS 17', 'IFRS 16'."""

    def __init__(self, sheet: str):
        wb = openpyxl.load_workbook(use(DATABOOK), read_only=True, data_only=True)
        ws = wb[sheet]
        self.rows = [list(r) for r in ws.iter_rows(values_only=True)]
        self.cols: dict[tuple[str, str], int] = {}
        block = None
        hdr_block = hdr_per = None
        for i, r in enumerate(self.rows[:12]):
            vals = [str(v).strip() if v is not None else "" for v in r]
            if any(v in ("IAS 17", "IFRS 16") for v in vals):
                hdr_block = vals
                hdr_per = [str(v).strip() if v is not None else "" for v in self.rows[i + 1]]
                break
        if hdr_block is None:
            raise ValueError(f"нет шапки блоков в листе {sheet}")
        for j, v in enumerate(hdr_block):
            if v in ("IAS 17", "IFRS 16"):
                block = v
            per = hdr_per[j] if j < len(hdr_per) else ""
            per = per.replace("Н", "H")  # кириллическая Н в «1Н 2023»
            if block and per:
                self.cols.setdefault((block, per), j)

    def row(self, label: str, occurrence: int = 1) -> list:
        n = 0
        for r in self.rows:
            if r and r[0] is not None and str(r[0]).strip().lower().startswith(label.lower()):
                n += 1
                if n == occurrence:
                    return r
        raise KeyError(label)

    def get(self, label: str, block: str, period: str, occurrence: int = 1) -> float | None:
        r = self.row(label, occurrence)
        j = self.cols[(block, period)]
        v = r[j]
        if v in (None, "", "-", "–"):
            return None
        return float(v)


# ---------------------------------------------------------------- PDF МСФО
_PDF_CACHE: dict[str, list[str]] = {}


def pdf_pages(rel: str) -> list[str]:
    if rel not in _PDF_CACHE:
        r = pypdf.PdfReader(str(use(rel)))
        _PDF_CACHE[rel] = [(p.extract_text() or "") for p in r.pages]
    return _PDF_CACHE[rel]


# Русские отчёты: разряды через одиночный пробел, колонки — через двойной; английские: разряды через запятую,
# колонки — через пробел. Шаблон выбирается по строке.
_DASH = r"[–—‒−](?=\s|$)"
_NUM_SPACE = re.compile(r"\(?-?\d{1,3}(?:[ \u00a0]\d{3})+\)?|\(?\d{1,3}\)?(?![\d])|" + _DASH)
_NUM_COMMA = re.compile(r"\(?-?\d{1,3}(?:,\d{3})+\)?|\(?\d{1,3}\)?(?![\d,])|" + _DASH)


class _NumFinder:
    @staticmethod
    def findall(ln: str) -> list[str]:
        pat = _NUM_COMMA if re.search(r"\d,\d{3}", ln) else _NUM_SPACE
        return pat.findall(ln)


_NUM = _NumFinder()


def _to_num(tok: str) -> float:
    if tok in ("–", "—", "‒", "−"):
        return 0.0
    neg = tok.startswith("(") or tok.startswith("-")
    digits = re.sub(r"[^\d]", "", tok)
    v = float(digits)
    return -v if neg else v


def numbers_after(text: str, label: str, min_abs: float = 1000.0, max_lines: int = 3) -> list[float]:
    """Числа строки с подписью `label` (или следующих строк, если подпись перенесена).

    Удаляет ссылки «(Прим. N)» и годы «2025 г.». Возвращает числа ≥ min_abs по модулю или нули-прочерки.
    """
    idx = text.find(label)
    if idx < 0:
        raise KeyError(label)
    tail = text[idx + len(label):]
    lines = tail.split("\n")
    out: list[float] = []
    for ln in lines[:max_lines]:
        ln = re.sub(r"\(\s*Прим\.?\s*[\d, ]+\)|Прим\.?\s*\d+|\(Note \d+\)", " ", ln)
        ln = re.sub(r"\b(19|20)\d{2}\s*(г\.|года|году)?", " ", ln)
        toks = [_to_num(t) for t in _NUM.findall(ln)]
        toks = [t for t in toks if abs(t) >= min_abs or t == 0.0]
        if toks:
            out = toks
            break
    if not out:
        raise ValueError(f"нет чисел после «{label}»")
    return out


def page_with(rel: str, *labels: str) -> tuple[int, str]:
    for i, t in enumerate(pdf_pages(rel)):
        if all(l in t for l in labels):
            return i + 1, t
    raise KeyError(f"{rel}: нет страницы с {labels}")


# ---------------------------------------------------------------- ГИР БО (РСБУ)
def rsbu_okey() -> dict[str, dict[str, float]]:
    rel = "rsbu_bo_nalog/bo_nalog_OOO_OKEY_7826087713_bfo_2021-2024.json"
    d = json.loads(use(rel).read_text(encoding="utf-8"))
    out: dict[str, dict[str, float]] = {}
    for rec in d:
        c = rec["typeCorrections"][0]["correction"]
        row: dict[str, float] = {}
        for part in ("balance", "financialResult"):
            for k, v in (c.get(part) or {}).items():
                if k.startswith("current") and isinstance(v, (int, float)):
                    row[k.replace("current", "")] = float(v)
        out[rec["period"]] = row
    return out
