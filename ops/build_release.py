"""Сборка выпуска: payload → файл в `$LENTA_STATE_DIR/release/` (иначе `var/release/`).

    python ops/build_release.py                # собрать выпуск
    python ops/build_release.py --check        # только проверить контракт (CI)

Публикует собранное `ops/publish.py` — в ветку `release` (GitHub Pages).
Адресация содержимым: `releases/<sha256>.json` неизменяем, `latest.json` —
указатель. Откат делается перезаписью указателя, а не восстановлением данных.

Коды выхода: 0 — собрано; 1 — НЕ собрано (нарушен контракт, инвариант или
гейт без действующего объяснения), на витрине остаётся прежний выпуск; 3
(`ALARM_EXIT`) — собрано и записано, но прогон требует внимания: масса гейта
разошлась с ожидаемой ИЛИ срок объяснения гейта на исходе (меньше
`EXPLANATION_WARN_DAYS` дней). Такт идёт дальше и публикует, а тревога уходит
кодом юнита в самом конце (`ops/run.sh`); строки «ТРЕВОГА:» называют причину.
С `--check` (проверка контракта в CI) тревоги такта печатаются, но код — 0.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from model.checks import explanation_deadline_alarms, load_gate_explanations  # noqa: E402
from model.engine import run_release  # noqa: E402
from model.payload import build_payload, release_gates, slow_blocks, validate  # noqa: E402

def pct(share: float) -> str:
    """Доля в процентах с одним знаком — тем же путём, что на витрине: выпуск
    пишет массу гейта округлённой до 4 знаков (`model/payload.py`), витрина
    печатает её `fmt.pct` (web/app.js: Math.round(доля·100·10)/10). Прямое
    `:.1%` от неокруглённой массы (0,29749… у EV/EBITDA книги 1.5) давало в
    журнале 29,7 %, а витрина и текст объяснения — 29,8 %: одно число
    выглядело двумя."""
    return f"{math.floor(round(share, 4) * 100 * 10 + 0.5) / 10:.1f}%"


# На проде выпуски ложатся в StateDirectory: там ротация и бюджет в 1 ГБ,
# а рабочее дерево остаётся кодом, который можно сбросить `git reset --hard`.
OUT = (Path(os.environ["LENTA_STATE_DIR"]) / "release"
       if os.environ.get("LENTA_STATE_DIR") else ROOT / "var" / "release")

# «Выпуск собран и записан, но прогон требует внимания». Отличается и от 0, и
# от 1: единица здесь означает «выпуска нет» и под `set -Eeuo` останавливает
# такт, а расхождение массы гейта останавливать его не должно — оценка верна,
# неверно объяснение. Тревога уходит кодом ЮНИТА в самом конце такта
# (`ops/run.sh`), потому что мост `dash-alert` срабатывает только по коду.
# Аудит третьей итерации, D1: расхождение печаталось в журнал при коде 0.
ALARM_EXIT = 3


def main() -> int:
    parser = argparse.ArgumentParser(description="Собрать выпуск дашборда")
    parser.add_argument("--check", action="store_true", help="только проверка контракта")
    parser.add_argument("--fast", action="store_true", help="без обратного DCF и суждений")
    parser.add_argument("--book-only", action="store_true",
                        help="считать на книге, не подставляя собранные входы")
    args = parser.parse_args()

    # Живые входы: цена, дата оценки и кривая рыночного мира из собранных
    # рядов. Без этого выпуск обновлял бы только дату сборки.
    release = run_release(live=not args.book_only)
    payload = build_payload(release, with_slow=not args.fast)
    # Гейты ВЫПУСКА — те, что ушли в `payload["gates"]`: к гейтам ядра
    # (`run_release`) выпуск добавляет гейты полосы (`limited_liability`) и
    # живых входов (`security_change`, карточка акции). Медленные блоки уже
    # посчитаны `build_payload` и лежат в памяти процесса — здесь не пересчёт.
    _findings, gate_summary = release_gates(
        release, None if args.fast else slow_blocks(release.book))

    # --- три причины НЕ публиковать. Все три возвращают ненулевой код.
    #
    # В первой итерации сборка возвращала 0 при 756 нарушенных инвариантах:
    # проверки считались, печатались и игнорировались. Выпуск с поломанной
    # арифметикой хуже отсутствующего — отсутствующий виден по дате.
    problems = validate(payload, release=release)
    for problem in problems:
        print(f"КОНТРАКТ: {problem}", file=sys.stderr)

    broken = release.blocking
    for finding in broken[:10]:
        print(f"ИНВАРИАНТ: {finding.label}: {finding.message}", file=sys.stderr)
    if len(broken) > 10:
        print(f"  ...и ещё {len(broken) - 10}", file=sys.stderr)

    unexplained = [gate for gate in gate_summary if gate.blocking]
    for gate in unexplained:
        reason = (f"объяснение просрочено (действовало по {gate.valid_until:%d.%m.%Y} "
                  "включительно)" if gate.stale else "нет объяснения")
        print(f"ГЕЙТ: {gate.key} — {reason}; клеток {gate.cells}, "
              f"вероятность {pct(gate.mass)}. Дописать в "
              f"data/assumptions/gate_explanations.yaml", file=sys.stderr)

    if problems or broken or unexplained:
        print(f"выпуск НЕ собран: контракт {len(problems)}, инварианты {len(broken)}, "
              f"необъяснённых гейтов {len(unexplained)}", file=sys.stderr)
        return 1

    # Гейты печатаются все, с их состоянием. «Ждёт согласования» — не то же,
    # что «объяснён»: такой гейт сработал по существу, текст пишет аудитор, и
    # видно это должно быть в каждом прогоне, а не один раз в отчёте.
    mass_off = [gate.key for gate in gate_summary if gate.mass_mismatch]
    for gate in gate_summary:
        # Совещательный гейт (`book_update`, `guidance_gap`, `limited_liability`,
        # `security_change`) текста не ждёт: он адресован человеку, и его
        # причину называет `message` находки.
        state = ("объяснён" if gate.explained
                 else "совещательный, публикацию не блокирует: " + gate.message[:300]
                 if gate.advisory
                 else "ЖДЁТ ТЕКСТА АУДИТОРА (сборку не блокирует)")
        if gate.explained and gate.valid_until is not None:
            state += f" до {gate.valid_until:%d.%m.%Y} включительно"
        print(f"  гейт {gate.key}: клеток {gate.cells}, вероятность {pct(gate.mass)} — {state}")
        if gate.mass_mismatch:
            # Сверка ожидаемой массы на ЖИВЫХ входах, а не только тестом на
            # книжных: объяснение, написанное под прежнюю картину, перестаёт
            # описывать происходящее молча.
            low, high = gate.mass_corridor
            expected = (f"{pct(gate.expected_mass_range[0])}–{pct(gate.expected_mass_range[1])}"
                        if gate.expected_mass_range else pct(gate.expected_mass))
            print(f"ТРЕВОГА: гейт {gate.key} сработал массой {pct(gate.mass)} при ожидаемой "
                  f"{expected} (коридор {pct(max(low, 0.0))}–{pct(high)}) — объяснение "
                  "описывает не то, что происходит", file=sys.stderr)

    # Сроки объяснений — БУДИЛЬНИК С ПРЕДУПРЕЖДЕНИЕМ. Просроченное объяснение
    # срабатывающего гейта блокирует сборку выше; здесь — то, что заблокирует
    # её скоро, и мёртвые записи. Без этой строки первое, что владелец узнавал
    # о сроке, была остановка витрины (аудит, G1-exam §1.3 п. 4).
    deadline = explanation_deadline_alarms(
        load_gate_explanations(), {gate.key for gate in gate_summary},
        today=date.today())
    for line in deadline:
        print(f"ТРЕВОГА: {line}", file=sys.stderr)

    live = payload.get("live") or {}
    for note in live.get("degraded", []):
        print(f"  ДЕГРАДАЦИЯ: {note}")
    for key, value in (live.get("applied") or {}).items():
        print(f"  живой вход {key}: {value}")

    digest = payload["meta"]["payload_sha256"]
    size = payload["meta"]["bytes"]
    print(f"выпуск {digest[:12]} · {size / 1024:.0f} КБ · "
          f"диапазон {payload['fair_value']['low']:.0f}–{payload['fair_value']['high']:.0f} ₽, "
          f"центр {payload['fair_value']['central']:.0f}")

    if not args.check:
        OUT.mkdir(parents=True, exist_ok=True)
        # Строгий JSON: `JSON.parse` витрины не знает NaN и Infinity.
        body = json.dumps(payload, ensure_ascii=False, indent=1, allow_nan=False)
        (OUT / f"{digest}.json").write_text(body, encoding="utf-8", newline="\n")
        (OUT / "latest.json").write_text(body, encoding="utf-8", newline="\n")
        print(f"записано: {OUT / 'latest.json'}")

    # Код — ПОСЛЕ работы: выпуск записан, публикация такта пойдёт своим ходом,
    # и только в конце `run.sh` превратит эту тревогу в код юнита 1.
    reasons = []
    if mass_off:
        reasons.append("масса гейтов " + ", ".join(mass_off) + " разошлась с ожидаемой")
    if deadline:
        reasons.append(f"сроки объяснений гейтов: {len(deadline)} (строки «ТРЕВОГА:» выше)")
    if reasons and args.check:
        # `--check` — проверка КОНТРАКТА (шаг CI «Контракт выпуска»), а не
        # такт: выпуск не пишется, и тревоги такта кодом не поднимаются. Иначе
        # CI краснел бы за 30 дней до каждого срока объяснения и при дрейфе
        # массы гейта на дате прогона — то есть по календарю, а не по коду.
        print("тревоги такта (при сборке был бы код 3): " + "; ".join(reasons)
              + "; контракт цел", file=sys.stderr)
        return 0
    if reasons:
        print("ТРЕВОГА (код 3): " + "; ".join(reasons)
              + "; выпуск записан, тревога уйдёт кодом такта", file=sys.stderr)
        return ALARM_EXIT
    return 0


if __name__ == "__main__":
    sys.exit(main())
