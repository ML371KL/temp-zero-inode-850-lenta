# -*- coding: utf-8 -*-
"""Шаги книги «Ленты» при приёме версии миров ставок (их зовёт `ops/tools/adopt_worlds.py` по манифесту
`data/assumptions/worlds_dependents.yaml`).

    python -B ops/tools/worlds_book.py quick --out var/adopt/quick.json   # числа книги из таблиц → JSON отчёта
    python -B ops/tools/worlds_book.py bump                               # номер книги: младший разряд + 1
    python -B ops/tools/worlds_book.py walk [--median [--draws N]]         # прогулка «книга до приёма → книга»

`quick` — точка, низ, верх, миры, медиана и её печать из `results.json` (полоса книги, зерно книги): перед приёмом —
таблицы книги до приёма, после шага таблиц — новые (формат — `850-worlds/model-kit/README.md`).

`bump` (только при смене версии миров): `meta.version` шаблона, шапка шаблона (версия, дата, журнал), строка версии в
комментарии ключа, лист развёртки гейтов новой версии (`evidence/book-<новая>/gates/` — копия листа прежней). Дата
книги, цена, дата оценки и зерно — без изменений.

`walk` — стартовая книга — машинная книга коммита до приёма (`WORLDS_START_COMMIT`, его ставит команда приёма),
шаги — номер версии, мир M с датой кривых, миры N и H (подпись шага — `labels.walk_step` индекса источника); пишет
`evidence/book-<версия>/walk/steps.yaml` и `out/walk.json`, `out/walk.txt` (LF). Код 1, если хоть одна проверка
прогулки «НЕТ» (последний шаг ≠ прямой расчёт или книга после шагов ≠ конечной).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
BOOK = ROOT / "data" / "assumptions"
TEMPLATE = BOOK / "assumptions_template.yaml"
EVIDENCE = BOOK / "evidence"
MSK = dt.timezone(dt.timedelta(hours=3))


def book_version() -> str:
    return str(yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))["meta"]["version"])


def next_version(v: str) -> str:
    parts = v.split(".")
    return ".".join(parts[:-1] + [str(int(parts[-1]) + 1)])


def worlds_entry() -> tuple[str, dict]:
    v = os.environ.get("WORLDS_VERSION")
    src = os.environ.get("WORLDS_SOURCE_DIR")
    if not v or not src:
        sys.exit("нет WORLDS_VERSION / WORLDS_SOURCE_DIR: шаг зовёт команда приёма миров")
    index = json.loads((Path(src) / "versions.json").read_text(encoding="utf-8"))
    return v, index["versions"][v]


def ru(iso: str) -> str:
    return dt.date.fromisoformat(iso).strftime("%d.%m.%Y")


# ----------------------------------------------------------------------------------------------- quick
def quick(out: Path) -> int:
    r = json.loads((BOOK / "results.json").read_text(encoding="utf-8"))
    fv, h = r["fair_value"], r["headline"]
    nums = {"point": fv["central"], "median": h["central"], "low": fv["low"], "high": fv["high"],
            "print": h["printed_central"], "worlds": {w: r["by_world"][w]["headline"] for w in ("N", "H", "M")},
            "runs": h["draws"], "seed": r["uncertainty"]["seed"], "book_version": r["book_version"],
            "band": h["band"], "printed_band": h["printed_band"], "source": "data/assumptions/results.json"}
    out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(nums, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"книга {nums['book_version']}: медиана {nums['median']:.1f} (печать {nums['print']:.0f}), точка "
          f"{nums['low']:.1f} / {nums['point']:.1f} / {nums['high']:.1f} ₽")
    return 0


# ----------------------------------------------------------------------------------------------- bump
HEAD_RE = re.compile(r"^(# КНИГА ДОПУЩЕНИЙ LENT \(машинный файл\) — версия )(?P<v>[\d.]+) от (?P<d>[\d.]+) "
                     r"\(тег book-[\d.]+\); журнал версии — V[\d.]+-CHANGES\.md \((?P<rest>.*)\)$")


def bump() -> int:
    wv, entry = worlds_entry()
    text = TEMPLATE.read_text(encoding="utf-8")
    old = book_version()
    new = next_version(old)
    today = dt.datetime.now(MSK).date().strftime("%d.%m.%Y")
    lines = text.split("\n")
    m = HEAD_RE.match(lines[1])
    if not m or m.group("v") != old:
        sys.exit(f"шапка шаблона (строка 2) не по образцу — номер книги не пишется: {lines[1][:120]}")
    lines[1] = (f"{m.group(1)}{new} от {today} (тег book-{new}); журнал версии — V{new}-CHANGES.md "
                f"({old} от {m.group('d')} — V{old}-CHANGES.md, {m.group('rest')})")
    note = f"# Версия {new} меняет только миры ставок: версия {wv} общего источника ({entry['title']}); " \
           f"суждения, факты, цена и мост — книги {old} (V{new}-CHANGES.md)."
    last = max(i for i, ln in enumerate(lines[:40]) if ln.startswith("# Версия "))
    while last + 1 < len(lines) and lines[last + 1].startswith("# ") and not lines[last + 1].startswith("# ===="):
        last += 1
    lines.insert(last + 1, note)
    key = next(i for i, ln in enumerate(lines) if ln.startswith("  version: "))
    a, sep, comment = lines[key].partition("#")
    if f'"{old}"' not in a:
        sys.exit(f"meta.version шаблона не {old!r}: {lines[key][:80]}")
    lines[key] = (a.replace(f'"{old}"', f'"{new}"') + sep + comment.rstrip()
                  + f"; {new} ({today}) — миры ставок {wv} ({entry['labels']['walk_step']}), остальное — {old}")
    TEMPLATE.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    after = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    if str(after["meta"]["version"]) != new:
        sys.exit("после правки meta.version шаблона не читается новым номером")
    src, dst = EVIDENCE / f"book-{old}" / "gates", EVIDENCE / f"book-{new}" / "gates"
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src / "gate_sweep.py", dst / "gate_sweep.py")
    (EVIDENCE / f"book-{new}" / "README.md").write_text(
        f"# Листы книги {new}\n\n"
        f"Версия {new} меняет только миры ставок: версия {wv} общего источника миров семейства 850 "
        f"({entry['title']}; запись `{entry['record']['sha256'][:8]}…`, снимок {ru(entry['record']['curve_date'])}). "
        f"Листы пишет команда приёма `ops/tools/adopt_worlds.py --version {wv}` по манифесту "
        f"`data/assumptions/worlds_dependents.yaml`:\n\n"
        f"- `gates/` — развёртка масс гейтов по датам оценки (копия листа книги {old}); числа объяснений "
        f"`gate_explanations.yaml` (массы, коридоры дрейфа, составы клеток) — `ops/tools/gate_numbers.py` и шаблон "
        f"`gate_explanations.tpl` (шаг `gate-texts` команды);\n"
        f"- `walk/` — прогулка книги {old} → {new} (`ops/tools/worlds_book.py walk`).\n",
        encoding="utf-8", newline="\n")
    print(f"книга {old} → {new} ({today}); лист гейтов {dst.relative_to(ROOT).as_posix()}")
    return 0


# ----------------------------------------------------------------------------------------------- walk
def walk(median: bool, draws: int | None) -> int:
    wv, entry = worlds_entry()
    start_commit = os.environ.get("WORLDS_START_COMMIT")
    if not start_commit:
        sys.exit("нет WORLDS_START_COMMIT: стартовую книгу знает команда приёма миров")
    rel = "data/assumptions/assumptions.yaml"
    start = subprocess.run(["git", "-C", str(ROOT), "show", f"{start_commit}:{rel}"], capture_output=True,
                           check=True).stdout
    var = ROOT / "var" / "adopt"
    var.mkdir(parents=True, exist_ok=True)
    (var / "walk-start.yaml").write_bytes(start)
    A0, A1 = yaml.safe_load(start.decode("utf-8")), yaml.safe_load((BOOK / "assumptions.yaml").read_text(encoding="utf-8"))
    v0, v1 = str(A0["meta"]["version"]), str(A1["meta"]["version"])
    label = entry["labels"]["walk_step"]
    steps = []
    if v0 != v1:
        steps.append({"name": f"Версия {v1} (номер версии, чисел не меняет)", "from_end": ["meta.version"]})
    steps.append({"name": f"{label}, мир M: рынок и дата кривых {ru(str(A1['meta']['curve_as_of']))}",
                  "from_end": ["worlds.M", "meta.curve_as_of"]})
    steps.append({"name": f"{label}, миры N и H", "from_end": ["worlds.N", "worlds.H"]})
    g0, g1 = A0["valuation"]["governance_discount"], A1["valuation"]["governance_discount"]
    if g0 != g1:                                     # шаг манифеста governance: неподвижная точка g на медиане книги
        steps.append({"name": f"Дисконт за управление g (A-V7): неподвижная точка на медиане книги {v1} — "
                              f"{100 * g0:.3f} → {100 * g1:.3f} %".replace(".", ","),
                      "from_end": ["valuation.governance_discount", "valuation.governance_components"]})
    wdir = EVIDENCE / f"book-{v1}" / "walk"
    (wdir / "out").mkdir(parents=True, exist_ok=True)
    head = (f"# Прогулка книги {v0} → {v1}: правки по одной, накопительно (ops/tools/walk_book.py).\n"
            f"# Шаги пишет ops/tools/worlds_book.py walk (команда приёма миров {wv}): номер версии, мир M с датой кривых,\n"
            f"# миры N и H, дисконт за управление g (неподвижная точка на медиане); суждения, факты, цена и мост не меняются.\n")
    (wdir / "steps.yaml").write_text(head + yaml.safe_dump(steps, allow_unicode=True, sort_keys=False, width=1000),
                                     encoding="utf-8", newline="\n")
    cmd = [sys.executable, "-B", str(ROOT / "ops" / "tools" / "walk_book.py"), "--start", str(var / "walk-start.yaml"),
           "--end", str(BOOK / "assumptions.yaml"), "--steps", str(wdir / "steps.yaml"),
           "--json", str(wdir / "out" / "walk.json")]
    if median:
        cmd += ["--median"] + (["--draws", str(draws)] if draws else [])
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    sys.stdout.write(p.stdout)
    sys.stderr.write(p.stderr)
    (wdir / "out" / "walk.txt").write_text(p.stdout, encoding="utf-8", newline="\n")
    js = wdir / "out" / "walk.json"
    if js.exists():
        js.write_bytes(js.read_bytes().replace(b"\r\n", b"\n"))
    return p.returncode


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("quick")
    q.add_argument("--out", type=Path, default=Path("var/adopt/quick.json"))
    sub.add_parser("bump")
    w = sub.add_parser("walk")
    w.add_argument("--median", action="store_true")
    w.add_argument("--draws", type=int)
    a = ap.parse_args(argv)
    if a.cmd == "quick":
        return quick(a.out)
    if a.cmd == "bump":
        return bump()
    return walk(a.median, a.draws)


if __name__ == "__main__":
    sys.exit(main())
