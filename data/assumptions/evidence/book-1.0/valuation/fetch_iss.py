"""Сверочные выгрузки MOEX ISS для листа «Оценка» (запускается только с --fetch; иначе печатает, что лежит в inputs/).

Что берётся (все ответы сохраняются как есть, sha256 печатается):
  inputs/iss_LENT_hist_2026-09-01_2026-09-25.json — история LENT (TQBR) с LEGALCLOSEPRICE: проверка цены книги 1 619,5;
  inputs/iss_LENT_description.json               — карточка бумаги (LISTLEVEL, ISSUESIZE);
  inputs/iss_LENT_indices.json                   — индексы, в которые входила бумага (IMOEX до 17.09.2026).
Ряды для бет и событий (LENT, LNTA, MCFTR, IMOEX, X5, MGNT за всю историю) не перекачиваются: берутся сырые ответы
ISS того же дня (28.09.2026) из research/_work05 (LENTA_RESEARCH_DIR), sha256 — в выводе beta.py и events.py.
"""
from __future__ import annotations

import sys
import time
import urllib.request

from common import INPUTS, sha256_file

URLS = {
    "iss_LENT_hist_2026-09-01_2026-09-25.json":
        "https://iss.moex.com/iss/history/engines/stock/markets/shares/boards/TQBR/securities/LENT.json"
        "?from=2026-09-01&till=2026-09-25&iss.meta=off"
        "&history.columns=TRADEDATE,SECID,BOARDID,CLOSE,LEGALCLOSEPRICE,WAPRICE,VOLUME,VALUE",
    "iss_LENT_description.json": "https://iss.moex.com/iss/securities/LENT.json?iss.only=description&iss.meta=off",
    "iss_LENT_indices.json": "https://iss.moex.com/iss/securities/LENT/indices.json?iss.meta=off",
}


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    for i in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            print("повтор", type(e).__name__, file=sys.stderr)
            time.sleep(2)
    raise SystemExit("не скачано: " + url)


def main():
    INPUTS.mkdir(parents=True, exist_ok=True)
    if "--fetch" in sys.argv:
        for name, url in URLS.items():
            (INPUTS / name).write_bytes(fetch(url))
            time.sleep(1.1)
    for name in URLS:
        p = INPUTS / name
        print(name, sha256_file(p) if p.exists() else "НЕТ ФАЙЛА (запустить с --fetch)")


if __name__ == "__main__":
    main()
