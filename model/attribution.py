"""Разложение изменения стоимости между двумя выпусками.

Владельцу, открывшему панель, нужен ответ не «сколько стоит», а «почему
изменилось со вчера». Без разложения любое движение числа выглядит как шум
модели, и доверие к ней падает быстрее, чем растёт от точности.

**Почему разложение именно такое.** Между выпусками меняются шесть вещей, и
каждая — своей природы:

1. **Ставки.** Кривая ЦБ обновляется каждый торговый день. Это наблюдение,
   и его вклад считается точно: пересчётом на вчерашней кривой.
2. **Наблюдения.** Нау-каст маржи и вышедшие отчёты двигают вероятности
   режимов через правило A-P2u. Тоже считается точно.
3. **Перекат даты оценки.** Дата оценки равна дате цены и едет каждый день.
   При замороженной книге оценка от этого растёт примерно на 0,8 ₽ в день
   (≈ +70 ₽ за квартал, и это НОРМАЛЬНО до обновления книги — см. «обобщённое
   время» в задании). Считается точно: пересчётом на вчерашней дате.
4. **Код и методика.** Правка ядра меняет число, не тронув ни книгу, ни
   факты. Точно разложить нельзя, зато можно НАЗВАТЬ: рядом печатается, какой
   коммит на какой сменился.
5. **Допущения.** Новая версия книги. Тот же случай: вклад считается как
   остаток, а рядом печатается смена версии.
6. **Факты.** Вышел отчёт, обновилась база. Тот же случай.

Аудит третьей итерации, A5: шагов 3 и 4 не было, и +32 ₽ от приведения
терминала к книге напечатались как «прочее (взаимодействие шагов) · версии
совпадают — остаток расчёта». Формально верно (версии совпадали), по существу
бесполезно: владелец видел «прочее» там, где произошла названная правка кода.
Дрейф даты уходил туда же.

Порядок шагов ВАЖЕН: вклады не аддитивны, и «эффект ставок» зависит от
того, считается он до или после смены допущений. Порядок зафиксирован от
самого наблюдаемого к самому субъективному — ставки, наблюдения, перекат
даты, затем код/факты/допущения, — и остаток относится на последний шаг, а не
размазывается.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass


@dataclass(frozen=True)
class Step:
    key: str
    title: str
    delta_v0: float
    delta_price: float
    note: str = ""


TITLES = {
    "curve": "ставки: кривая бескупонной доходности",
    "observations": "наблюдения: нау-каст и вышедшие отчёты",
    "valuation_date": "перекат даты оценки",
    "engine": "изменение ядра/методики",
    "facts": "факты: обновление отчётной базы",
    "assumptions": "допущения: новая версия книги",
    "residual": "прочее (взаимодействие шагов)",
}


def inputs_snapshot(A: dict) -> dict:
    """То, что нужно сохранить в выпуске, чтобы следующий смог разложить сдвиг."""
    world = A["joint"]["macro_neutral_world"]
    return {
        "book_version": str(A["meta"]["version"]),
        "facts_date": A["meta"]["facts_date"],
        "valuation_date": A["meta"]["valuation_date"],
        "market_price": A["market"]["price"],
        "curve": {str(k): v for k, v in A["worlds"][world]["zero_curve"].items()},
        "observations": dict(A["joint"]["regime_update"].get("observations") or {}),
    }


def _apply(A: dict, snapshot: dict, *, with_date: bool = True) -> dict:
    """Возвращает книгу с входами из снимка: кривая, наблюдения и дата оценки.

    `with_date=False` оставляет СЕГОДНЯШНЮЮ дату оценки — так считается вклад
    переката даты: две книги, различающиеся ровно датой.
    """
    B = copy.deepcopy(A)
    world = B["joint"]["macro_neutral_world"]
    curve = snapshot.get("curve") or {}
    if curve:
        restored = {}
        for key in B["worlds"][world]["zero_curve"]:
            restored[key] = curve.get(str(key), B["worlds"][world]["zero_curve"][key])
        B["worlds"][world]["zero_curve"] = restored
    B["joint"].setdefault("regime_update", {})["observations"] = dict(
        snapshot.get("observations") or {})
    if with_date and snapshot.get("valuation_date"):
        B["meta"]["valuation_date"] = snapshot["valuation_date"]
    return B


def attribute(previous: dict, A: dict, *, run, engine_commit: str = "") -> list[Step]:
    """Раскладывает ΔV₀ и Δцены между прошлым выпуском и текущей книгой.

    `run(book) -> (v0, price)` — функция расчёта; передаётся снаружи, чтобы
    модуль не тянул за собой движок и оставался проверяемым на заглушке.
    `engine_commit` — коммит кода ТЕКУЩЕЙ сборки; прошлый берётся из
    `previous["meta"]["engine_commit"]`.
    """
    snapshot = previous.get("inputs") or {}
    if not snapshot:
        return []

    # Шаг 0: что было. Берём ТЕКУЩУЮ книгу и возвращаем ей вчерашние
    # наблюдаемые входы — кривую, наблюдения И ДАТУ ОЦЕНКИ. Разница с прошлым
    # выпуском по V₀ и будет вкладом кода, допущений и фактов вместе — их
    # поодиночке не разделить, но можно назвать, что именно сменилось.
    was_v0 = previous.get("layers", {}).get("macro_neutral", {}).get("v0")
    was_price = previous.get("fair_value", {}).get("central")
    if was_v0 is None or was_price is None:
        return []

    base = _apply(A, snapshot)
    base_v0, base_price = run(base)

    steps: list[Step] = []
    was_commit = str((previous.get("meta") or {}).get("engine_commit") or "")
    book_changed = snapshot.get("book_version") != str(A["meta"]["version"])
    facts_changed = snapshot.get("facts_date") != A["meta"]["facts_date"]
    # Код считается изменившимся только тогда, когда ОБА хэша известны:
    # пустой у прошлого выпуска означает «выпуск старше этой правки», а не
    # «код был другой».
    code_changed = bool(was_commit and engine_commit and was_commit != engine_commit)

    reasons = []
    if book_changed:
        reasons.append(f"книга {snapshot.get('book_version')} → {A['meta']['version']}")
    if facts_changed:
        reasons.append(f"факты {snapshot.get('facts_date')} → {A['meta']['facts_date']}")
    if code_changed:
        reasons.append(f"код {was_commit[:12]} → {engine_commit[:12]}")
    # Порядок ярлыка — от самого объемлющего к самому узкому: новая книга
    # объясняет и правку кода, которая её сопровождала, а «код» ставится
    # только когда книга и факты те же. Иначе один сдвиг назывался бы двумя
    # причинами, а сумма шагов перестала бы сходиться.
    label = ("assumptions" if book_changed else "facts" if facts_changed
             else "engine" if code_changed else "residual")
    steps.append(Step(
        key=label, title=TITLES[label],
        delta_v0=base_v0 - was_v0, delta_price=base_price - was_price,
        note="; ".join(reasons) or "книга, факты и код те же — остаток расчёта"))

    # Шаг «перекат даты оценки»: та же книга, те же наблюдаемые входы, СЕГОДНЯШНЯЯ
    # дата. При замороженной книге это и есть те ≈0,8 ₽ в день, которые раньше
    # уходили в «прочее».
    rolled = _apply(A, snapshot, with_date=False)
    roll_v0, roll_price = run(rolled)
    days = _days_between(snapshot.get("valuation_date"), A["meta"]["valuation_date"])
    steps.append(Step(
        key="valuation_date", title=TITLES["valuation_date"],
        delta_v0=roll_v0 - base_v0, delta_price=roll_price - base_price,
        note=(f"{snapshot.get('valuation_date')} → {A['meta']['valuation_date']}"
              + (f", {days} дн" if days else ""))))

    # Шаг «наблюдения»: возвращаем сегодняшние наблюдения при вчерашней кривой.
    with_observations = _apply(A, {**snapshot,
                                   "observations": A["joint"]["regime_update"]
                                   .get("observations") or {}},
                               with_date=False)
    obs_v0, obs_price = run(with_observations)
    steps.append(Step(key="observations", title=TITLES["observations"],
                      delta_v0=obs_v0 - roll_v0, delta_price=obs_price - roll_price))

    # Шаг «ставки»: полностью текущая книга.
    now_v0, now_price = run(A)
    steps.append(Step(key="curve", title=TITLES["curve"],
                      delta_v0=now_v0 - obs_v0, delta_price=now_price - obs_price))

    return steps


def _days_between(was: str | None, now: str) -> int:
    """Календарных дней между датами оценки. Ноль — дат нет или они мусор."""
    import datetime as _dt

    try:
        return (_dt.date.fromisoformat(str(now)[:10])
                - _dt.date.fromisoformat(str(was)[:10])).days
    except (TypeError, ValueError):
        return 0


def total(steps: list[Step]) -> tuple[float, float]:
    return (sum(s.delta_v0 for s in steps), sum(s.delta_price for s in steps))
