# -*- coding: utf-8 -*-
"""Календарь событий «Ленты» (data/calendar.json книги 1.0) из первички и входов листа.

Что делает:
  1. sha256 используемых файлов первички против MANIFEST.md (каталог — env LENTA_PRIMARY_DIR,
     по умолчанию <H>/reference/primary от места скрипта);
  2. история релизов: каждая строка inputs/calendar_inputs.yaml → report_history находится в индексе пресс-релизов
     lentagroup.ru (lentagroup_news/_index_news_2016-2026.json) по дате и признаку в заголовке; лаг = дата − конец периода;
  3. окна будущих отчётов по правилу report_rule (лаги последних двух лет того же типа): [конец + min − 2; конец + max + 3],
     центр = конец + ⌊среднее⌋, выходной → предыдущая пятница;
  4. пут-оферты облигаций «О'КЕЙ» — из ISS bondization (offers: тип «Оферта», дата ≥ as_of), объём и купон — оттуда же;
  5. прочие события — из входов (у каждого source и reliability); проверки: порядок, точность, окно содержит when,
     у фактов эмитента prior_for = null и есть revenue, у остальных событий эмитента revenue нет;
  6. пишет calendar.json (по умолчанию <book-draft>/calendar.json; env LENTA_CALENDAR_OUT) и calendar_out.txt рядом.
Формат — как data/calendar.json книги-источника (schema/purpose/as_of/precision_note/events с id, when, precision, title,
gives, prior_for, note) плюс необязательные поля window, when_rule, source, reliability и список watch (загрузчик
indicators/calendar.py их не читает; точность window/year — расширение, см. README листа).
Запуск: python -B build_calendar.py   (Python 3.12, PyYAML). Код 0 — все проверки прошли.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import statistics
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
BOOK_DRAFT = HERE.parents[2]
H = BOOK_DRAFT.parent
PRIMARY = Path(os.environ.get("LENTA_PRIMARY_DIR", H / "reference" / "primary"))
OUT = Path(os.environ.get("LENTA_CALENDAR_OUT", BOOK_DRAFT / "calendar.json"))
INPUTS = HERE / "inputs" / "calendar_inputs.yaml"
ISSUER_PREFIX = "lenta."
PRECISIONS = ("day", "month", "window", "year")
WHEN_RULES = ("exact", "central", "deadline", "earliest")

NEWS_INDEX = "lentagroup_news/_index_news_2016-2026.json"

checks: list[dict] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    checks.append({"name": name, "ok": bool(ok), "detail": detail})


def manifest() -> dict[str, str]:
    out = {}
    for line in (PRIMARY / "MANIFEST.md").read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.split("|")]
        if len(cells) > 5 and re.fullmatch(r"[0-9a-f]{64}", cells[4] or ""):
            out[cells[1]] = cells[4]
    return out


def verified(rel: str, man: dict[str, str]) -> Path:
    p = PRIMARY / rel
    got = hashlib.sha256(p.read_bytes()).hexdigest()
    check(f"sha256 {rel} = MANIFEST", man.get(rel) == got, got)
    return p


def period_end(period: str) -> dt.date:
    y, tail = int(period[:4]), period[4:]
    return {"Q1": dt.date(y, 3, 31), "Q2": dt.date(y, 6, 30), "Q3": dt.date(y, 9, 30), "Q4": dt.date(y, 12, 31),
            "FY": dt.date(y, 12, 31)}[tail]


def weekend_to_friday(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=d.weekday() - 4) if d.weekday() >= 5 else d


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    inp = yaml.safe_load(INPUTS.read_text(encoding="utf-8"))
    as_of = dt.date.fromisoformat(inp["as_of"])
    man = manifest()

    # --- 2. история релизов из индекса пресс-релизов
    index = json.loads(verified(NEWS_INDEX, man).read_text(encoding="utf-8"))
    by_date: dict[str, list[str]] = {}
    for it in index:
        by_date.setdefault(it["date"], []).append(it["title"])
    history = []
    for h in inp["report_history"]:
        titles = by_date.get(h["date"], [])
        hit = [t for t in titles if h["title_has"] in t]
        check(f"релиз {h['period']} ({h['kind']}) {h['date']} есть в индексе", len(hit) == 1, "; ".join(titles)[:200])
        lag = (dt.date.fromisoformat(h["date"]) - period_end(h["period"])).days
        history.append(dict(h, lag=lag, title=hit[0] if hit else None))

    rule = inp["report_rule"]
    events = []
    report_windows = []
    for r in inp["reports"]:
        end = period_end(r["period"])
        same = [x for x in history if x["kind"] == r["kind"] and (r["kind"] != "q" or x["period"][4:] == r["period"][4:])]
        same = sorted(same, key=lambda x: x["date"])[-rule["history_years"]:]
        lags = [x["lag"] for x in same]
        lo = end + dt.timedelta(days=min(lags) - rule["window_before_days"])
        hi = end + dt.timedelta(days=max(lags) + rule["window_after_days"])
        central_raw = end + dt.timedelta(days=int(statistics.mean(lags) // 1))
        central = weekend_to_friday(central_raw)
        report_windows.append({"id": r["id"], "period_end": end.isoformat(), "lags_used": {x["period"]: x["lag"] for x in same},
                               "window": [lo.isoformat(), hi.isoformat()], "central_raw": central_raw.isoformat(),
                               "central": central.isoformat(), "weekday": central.strftime("%a")})
        events.append({
            "id": r["id"], "when": central.isoformat(), "precision": "window", "when_rule": "central",
            "window": {"from": lo.isoformat(), "to": hi.isoformat()},
            "title": r["title"], "gives": r["gives"], "prior_for": None,
            "note": r["note"] + (f" Окно — по лагам релизов того же типа за {', '.join(x['period'] for x in same)} "
                                 f"({', '.join(str(x['lag']) for x in same)} дн. от конца периода); точную дату компания "
                                 f"ставит в календарь IR (сборщик раз в неделю, тогда precision: day)."),
            "source": f"расчёт: {NEWS_INDEX} (релизы " + ", ".join(f"{x['date']}" for x in same) + "); правило report_rule листа",
            "reliability": "B"})

    # --- 4. пут-оферты облигаций «О'КЕЙ»
    for b in inp["bond_puts"]:
        bz = json.loads(verified(f"moex_iss_okey_bonds/{b['isin']}_bondization.json", man).read_text(encoding="utf-8"))
        ds = json.loads(verified(f"moex_iss_okey_bonds/{b['isin']}_description.json", man).read_text(encoding="utf-8"))
        desc = {row[0]: row[2] for row in ds["description"]["data"]}
        offers = [dict(zip(bz["offers"]["columns"], row)) for row in bz["offers"]["data"]]
        fut = [o for o in offers if o["offertype"] == "Оферта" and o["offerdate"] >= as_of.isoformat()]
        check(f"оферта {desc['SHORTNAME']}: одна будущая в ISS", len(fut) == 1, str([o["offerdate"] for o in fut]))
        o = fut[0]
        check(f"оферта {desc['SHORTNAME']} = BUYBACKDATE карточки", o["offerdate"] == desc.get("BUYBACKDATE"), f"{o['offerdate']} / {desc.get('BUYBACKDATE')}")
        vol = o["issuevalue"] / 1e9
        events.append({
            "id": b["id"], "when": o["offerdate"], "precision": "day", "when_rule": "exact",
            "title": f"Пут-оферта {desc['SHORTNAME']} ({b['isin']}): {vol:.1f} млрд ₽ по {o['price']:.0f} % номинала".replace(".", ","),
            "gives": ["refinancing", "coupon_reset"], "prior_for": f"financing.rate_baskets[{b['basket']}]",
            "note": (f"Облигации ООО «О'КЕЙ» (эмитент {desc.get('EMITTER_ID')}), купон {str(desc.get('COUPONPERCENT')).replace('.', ',')} % до оферты, "
                     f"погашение {desc.get('MATDATE')}; поручительства «Ленты» не найдено, рейтинги отозваны. В модели — корзина "
                     f"ставок до 2027H1, после оферты — ставки нового долга; денежного события сверх долга нет (рефинансирование "
                     f"внутри ЧД). Новый купон эмитент объявляет до периода предъявления."),
            "source": f"MOEX ISS bondization {b['isin']} (offers, тип «Оферта»), карточка бумаги; MANIFEST sha256",
            "reliability": "A"})

    # --- 5. прочие события
    for e in inp["events"]:
        ev = {k: e.get(k) for k in ("id", "when", "precision", "when_rule", "window", "title", "gives", "prior_for", "note", "source", "reliability")}
        if ev["window"] is None:
            ev.pop("window")
        events.append(ev)

    events.sort(key=lambda x: (x["when"], x["id"]))

    # --- проверки
    ids = [e["id"] for e in events]
    check("id уникальны", len(ids) == len(set(ids)), str([i for i in ids if ids.count(i) > 1]))
    check("события упорядочены по дате", [e["when"] for e in events] == sorted(e["when"] for e in events))
    for e in events:
        d = dt.date.fromisoformat(e["when"])
        check(f"{e['id']}: точность из списка", e["precision"] in PRECISIONS, e["precision"])
        check(f"{e['id']}: when_rule из списка", e.get("when_rule") in WHEN_RULES, str(e.get("when_rule")))
        check(f"{e['id']}: есть note и source", bool(e.get("note")) and bool(e.get("source")))
        if e["precision"] == "day":
            check(f"{e['id']}: у day нет окна и rule exact", "window" not in e and e["when_rule"] == "exact")
        else:
            w = e.get("window") or {}
            ok = bool(w) and dt.date.fromisoformat(w["from"]) <= d <= dt.date.fromisoformat(w["to"])
            check(f"{e['id']}: окно содержит when", ok, str(w))
            if e["when_rule"] == "deadline":
                check(f"{e['id']}: срок = конец окна", w and e["when"] == w["to"])
            if e["when_rule"] == "earliest":
                check(f"{e['id']}: «не раньше» = начало окна", w and e["when"] == w["from"])
        is_issuer = e["id"].startswith(ISSUER_PREFIX)
        is_fact = e["prior_for"] is None and "revenue" in e["gives"]
        if is_issuer and e["id"] in {r["id"] for r in inp["reports"]}:
            check(f"{e['id']}: факт эмитента (prior_for null, revenue)", is_fact)
        elif "revenue" in e["gives"]:
            check(f"{e['id']}: revenue даёт только отчёт эмитента", False)
        if e["id"].startswith(("x5.",)):
            check(f"{e['id']}: аналог — справочно, без приора", e["prior_for"] is None and e["note"].startswith("Справочно:"))
    q3 = next(e for e in events if e["id"] == "lenta.q3_2026")
    check("3 кв. 2026: окно 26.10–03.11.2026, центр 29.10.2026 (D2)",
          q3["window"] == {"from": "2026-10-26", "to": "2026-11-03"} and q3["when"] == "2026-10-29", str(q3["window"]) + q3["when"])
    next_fact = next((e for e in events if e["when"] >= as_of.isoformat() and e["id"].startswith(ISSUER_PREFIX)
                      and e["prior_for"] is None and "revenue" in e["gives"]), None)
    check("ближайший факт эмитента на as_of — 3 кв. 2026", next_fact is not None and next_fact["id"] == "lenta.q3_2026")

    cal = {
        "schema": "lenta-calendar-v1",
        "purpose": ("Календарь событий, которые могут ДВИНУТЬ оценку «Ленты» до выхода её отчёта, и сами отчёты. Смысл слоя "
                    "индикаторов — опережение относительно отчёта компании, а не относительно цены; ценны события, которые "
                    "приходят раньше него. Даты отчётов — окна по лагам прошлых лет, пока компания не объявила дату."),
        "as_of": as_of.isoformat(),
        "precision_note": ("«day» — дата объявлена компанией, биржей (оферта) или установлена законом. «month» — известен месяц. "
                           "«window» — окно window.from–window.to; when — центральная оценка (when_rule: central) или срок "
                           "(deadline). «year» — известен только год; when — «не раньше» (earliest). Выдуманная точная дата "
                           "хуже честного окна: обратный отсчёт до неё врёт с точностью до дня."),
        "events": events,
        "watch": inp["watch"],
    }
    OUT.write_text(json.dumps(cal, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")

    L = ["КАЛЕНДАРЬ «ЛЕНТЫ» — build_calendar.py", f"as_of {as_of}; первичка: {PRIMARY.name}/; вывод: {OUT.name}", ""]
    L.append("1. История релизов (лаг, дней от конца периода)")
    for h in history:
        L.append(f"   {h['period']:<7} {h['kind']:<7} {h['date']}  лаг {h['lag']:>3}  {(h['title'] or '—')[:90]}")
    L.append("")
    L.append("2. Окна будущих отчётов")
    for w in report_windows:
        L.append(f"   {w['id']:<18} конец {w['period_end']}; лаги {w['lags_used']} → окно {w['window'][0]} … {w['window'][1]}, "
                 f"центр {w['central']} ({w['weekday']}; до переноса выходного {w['central_raw']})")
    L.append("")
    L.append("3. События")
    for e in events:
        win = f" [{e['window']['from']} … {e['window']['to']}]" if "window" in e else ""
        L.append(f"   {e['when']} {e['precision']:<6}{win} {e['id']} — {e['title'][:80]} ({e['reliability']})")
    L.append("")
    L.append("4. Проверки")
    for c in checks:
        if not c["ok"]:
            L.append(f"   [НЕТ] {c['name']} — {c['detail'][:200]}")
    L.append(f"   прошло {sum(c['ok'] for c in checks)} из {len(checks)}")
    ok = all(c["ok"] for c in checks)
    L.append("ИТОГ: " + ("все проверки прошли" if ok else "ЕСТЬ ПРОВАЛЫ"))
    txt = "\n".join(L) + "\n"
    (HERE / "calendar_out.txt").write_text(txt, encoding="utf-8", newline="\n")
    (HERE / "calendar_out.json").write_text(json.dumps({"history": history, "report_windows": report_windows, "checks": checks},
                                                       ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(txt)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
