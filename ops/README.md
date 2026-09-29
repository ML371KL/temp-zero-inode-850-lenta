# Эксплуатация «Лента 850»

Конвейер модели справедливой стоимости «Ленты» на общем VPS панелей владельца:
сбор источников, нау-каст, сборка выпуска, публикация в ветку `release` этого
репозитория, сверка через боевую дверь `https://tzi-850-lenta.pages.dev/api/model`.
Каркас — панель Магнита 850oa; отличия от неё названы по месту.

Здесь — всё, что нужно для работы с конвейером, без инфраструктурных
подробностей сервера (адрес, имена ключей, доступы): они в справочнике владельца
вне репозитория. Репозиторий публичный, и правило гигиены
(`tests/test_public_hygiene.py`) это проверяет.

## 1. Что где

| Что | Где |
|---|---|
| Код на сервере | `/srv/dash/repo-850-lenta` — клон `main`, `--single-branch`, анонимный HTTPS |
| Окружение Python | `/srv/dash/repo-850-lenta/.venv` (Python 3.12, как в CI); отметка зависимостей `.venv/.requirements.sha256` |
| Настройки | `/usr/local/etc/lenta-850/env` (`root:dash 640`), образец — `ops/env.example`; секретов в нём нет |
| Состояние | `/var/lib/lenta-850` (`StateDirectory=lenta-850`, владелец `dash`) |
| Юниты | `lenta850-{collect,daily,rebuild}.{service,timer}` из `ops/systemd/`, журнал `SyslogIdentifier=lenta850` |
| Репозиторий данных | `ML371KL/temp-zero-inode-850-lenta-data` (публичный, отдельный от кода): ветка `release` по умолчанию — только `README.md`, `.nojekyll`, `latest.json`, `releases/<sha256>.json`; `.github/` в ней не бывает. Deploy-ключ сервера пишет только сюда: к репозиторию кода у него доступа нет (в личном репозитории правила веток deploy-ключ с правом записи обходит — проверено 29.09.2026, поэтому граница — репозиторий, а не правило) |
| Раздача данных | GitHub Pages репозитория данных: `https://ml371kl.github.io/temp-zero-inode-850-lenta-data/latest.json` |
| Дверь браузера | Pages Function `functions/api/model.js` на `tzi-850-lenta.pages.dev`; запасной источник — `raw.githubusercontent.com/…/release/latest.json` |
| Витрина | статика `web/`, выкладка с ноутбука (раздел 8) |
| Тревоги | общий мост `/usr/local/sbin/dash-alert` (`ExecStopPost`) и сторож `dash-watch` — оба из служебного репозитория панелей, не отсюда |

Внутри `/var/lib/lenta-850`:

| Путь | Что |
|---|---|
| `indicators/` | сырой архив ответов, ряды с винтажами, журнал нау-каста, отчёт сборщиков |
| `release/latest.json`, `release/<sha>.json` | локальные выпуски сборки; от `latest.json` мерится скачок заголовка |
| `release-repo/` | одноразовый клон ветки `release` (создаётся первой публикацией; каждый прогон — `fetch` + `reset --hard origin/release`) |
| `release.pushed` | выпуск отправлен в ветку, сверка ещё не прошла (хэш, `published_at`, коммит кода) |
| `release.commit` | коммит кода выпуска, ПОДТВЕРЖДЁННОГО сверкой; по нему пересборка решает, есть ли что пересобирать |
| `rebuild.tried` | коммит, на котором пересборка уже запускалась (упавшая не повторяется каждые 15 минут) |
| `future.checked` | «день код» прогона «в будущем» (повтор утреннего юнита его не гоняет заново) |
| `run.lock` | один замок на все такты и на откат |
| `logs/<дата UTC>.log` | общий журнал тактов, 45 дней (journald ротируется по объёму всей машины) |

## 2. Канал данных

```
сборка (ops/build_release.py) → $LENTA_STATE_DIR/release/latest.json
  → ops/publish.py: клон ветки release репозитория данных, releases/<sha256>.json + latest.json одним коммитом, push
  → GitHub Pages репозитория данных (сборка pages-build-deployment, обычно 1–3 минуты)
  → functions/api/model.js: Pages (минутная метка, cacheTtl 60) → raw (запасной) → копия края
  → браузер и сверка ops/publish.py --verify
```

- **Адресация содержимым.** `releases/<sha256>.json` пишется один раз; хэш
  считается без `meta.generated_at`, `meta.payload_sha256`, `meta.bytes`,
  `meta.published_at` и блока `changes` — три сборки на одних входах дают один
  файл. `latest.json` — указатель.
- **`meta.published_at`** — момент публикации (UTC, ISO, секунды), вне хэша.
  Вписывается при каждой публикации и каждом откате. Из него дверь ставит
  `Last-Modified` (по нему живёт сторож) и сильный `ETag` `"<sha12>.<секунды>"`.
- **Коммит — только при разнице** (`git diff --cached --quiet`): повтор после
  упавшего push не упирается в «nothing to commit». Push — три попытки с растущей
  паузой, каждая с чистого клона. `gc.autoDetach=false`: systemd убил бы фоновый
  `git gc` и оставил `*.lock`.
- **Автор коммитов ветки** — `LENTA_GIT_NAME`/`LENTA_GIT_EMAIL`, только
  noreply-адрес GitHub; пусто или другой адрес — отказ (код 1). Все адреса всей
  истории сверяет CI со списком `ops/commit-emails.allow`.
- **Сверка** читает дверь, как браузер, до совпадения `payload_sha256` И
  `published_at` (по одному хэшу пересборка на тех же входах «сверялась» бы
  мгновенно). Окно — `LENTA_VERIFY_TIMEOUT` (600 с), пауза 30 с. Итоги:
  - совпало, `x-data-source: pages` — `release.commit` = коммит кода выпуска, отметка `release.pushed` снимается, код 0;
  - совпало через `raw` или `edge-cache` — выпуск засчитан, но GitHub Pages не отдаёт ветку: код 3 (тревога);
  - не дождались — код 3, `release.pushed` остаётся, `release.commit` не пишется; сверку дожимает пересборка (одна попытка раз в 15 минут).
- **Прореживание** — только после подтверждённой сверки: 90 дней все выпуски,
  старше — только понедельничные. Дата — из содержания выпуска
  (`published_at`, иначе `generated_at`), не из времени файла. Из дерева ветки
  файлы уходят, история остаётся в git.
- **Дверь**: 200 с годным выпуском; 503 `not published yet` — оба источника
  ответили 404 (ветку ещё не публиковали); 503 `upstream unavailable` — прочие
  отказы без копии края; JSON-404 на чужие пути под `/api/`; 405 на методы кроме
  GET/HEAD; 304 на `If-None-Match`. Поведение проверяет
  `tests/functions/model.test.mjs` (Node, подменённые `fetch` и `caches`).

## 3. Такты

| Юнит | Когда (UTC) | Что делает |
|---|---|---|
| `lenta850-collect` | каждый день 04:20 (±5 мин), `Persistent` | обновление кода → зависимости → сбор источников → по понедельникам тесты такта «в будущем» (+60 дней) → ротация сырого архива → объём → здоровье (тревога) |
| `lenta850-daily` | будни 17:25 (±5 мин), `Persistent` | обновление кода → зависимости → сбор с повтором невосполнимых → нау-каст → здоровье (напоминание) → тесты такта → сборка → публикация → сверка → прореживание → объём |
| `lenta850-rebuild` | `*:07,22,37,52` | тихая проверка: дожим сверки прошлого такта; если код в `CODE_DIRS` (`model`, `indicators`, `ops`, `data/assumptions`, `data/facts`, `requirements.txt`, без `*.md`) изменился с `release.commit` — тесты такта → сборка → публикация → сверка → прореживание, без сбора |

- **Обновление кода** — `git fetch` + `reset --hard origin/main` и `exec`
  нового `run.sh` один раз за такт (иначе bash доигрывал бы прежний файл).
  GitHub не читается — такт идёт на текущем коде (утренний срез дороже).
- **Зависимости** — по отметке `sha256(requirements.txt)` в venv, каждый такт до
  работы; отметка пишется только после удачного `pip install`, неудача — провал
  такта (новый код на старых пакетах число не считает).
- **Тесты такта** — белый список по метке `tact` (`pytest.ini`), выражение
  `TACT_TESTS` в `run.sh`; ноль выбранных — провал. Цель — ≤ 3 минут на сервере;
  CI печатает `--durations` этого набора.
- **Замок** один; плановый такт ждёт его до `LOCK_WAIT_SECONDS` (= потолок
  пересборки), пересборка на занятом замке молча выходит с 0.

## 4. Коды и тревоги

| Код юнита | Смысл | Повтор systemd |
|---|---|---|
| 0 | прошло | — |
| 1 | ПРОВАЛ шага, выпуска нет (в журнале «ПРОВАЛ на шаге «…» (код N)») | `daily`, `collect` — через 3 мин, до 3 раз за 30 мин |
| 8 | ТРЕВОГА: работа выполнена, выпуск (если такт его делает) на витрине, последняя строка называет причины | `daily` — нет (`RestartPreventExitStatus=8`); `collect` — да (повтор добирает невосполнимый источник) |
| 64 | неверный режим или проба тревоги с источником не из такта | нет |
| 75 | замок держит другой такт дольше `LOCK_WAIT_SECONDS` | да |
| 78 | нет env-файла или в нём нет `LENTA_REPO_DIR`/`LENTA_STATE_DIR` | нет |

Внутри такта шаги отвечают кодом 3 «сделано, но требует внимания»
(невосполнимый источник, тревога сборки, сверка не дождалась выпуска, красное
«будущее», отказ прореживания); причины копятся и уходят одним кодом 8 в конце.
Любой другой ненулевой код шага превращается в 1. Пересборка на коммите, где она
уже падала, выходит с 1 без работы (мост шлёт смену состояния — «упало» пришло
один раз).

Мост `dash-alert` зовётся из `ExecStopPost` всегда и шлёт смену состояния:
«упало» и «снова проходит». Имя тревог — «Лента 850 · …»: панель регистрируется
строкой в `PANELS` у `dash-watch` (адрес двери, порог свежести 74 ч по
`Last-Modified`: разрыв пятница → понедельник — 72 ч) и веткой `case` в
`dash-alert` — по процедуре служебного репозитория панелей. Как мост называет
код 8, проверить там при регистрации.

**Проба доставки тревоги** (утренний такт, источник объявляется отказавшим без
обращения к нему):

```bash
sudo -u dash env ENV_FILE=/usr/local/etc/lenta-850/env \
     LENTA_SIMULATE_FAILURE=<невосполнимый источник утреннего такта> \
     bash /srv/dash/repo-850-lenta/ops/run.sh collect
echo $?    # 8, последняя строка — «ТРЕВОГА (код 8): невосполнимый источник не собран …»
```

Имя не из такта — код 64: «проба прошла, тревоги нет» было бы тихим нулём.

## 5. Установка на сервер (по шагам)

Пользователь `dash` и общие скрипты `dash-alert`/`dash-watch` уже есть (шаблон
панелей владельца). Порядок обязателен: первый выпуск в ветке → GitHub Pages →
витрина → таймеры, иначе боевой адрес отвечает 503.

1. **Код и окружение.**
   ```bash
   sudo -u dash git clone --single-branch --branch main \
        https://github.com/ML371KL/temp-zero-inode-850-lenta.git /srv/dash/repo-850-lenta
   sudo -u dash python3.12 -m venv /srv/dash/repo-850-lenta/.venv
   ```
   Пакеты поставит первый такт (отметка `.venv/.requirements.sha256`).
2. **Настройки.**
   ```bash
   sudo install -d -o root -g dash -m 750 /usr/local/etc/lenta-850
   sudo install -o root -g dash -m 640 /srv/dash/repo-850-lenta/ops/env.example /usr/local/etc/lenta-850/env
   sudoedit /usr/local/etc/lenta-850/env      # проверить значения; секретов там нет
   ```
   Ключевые: `LENTA_REPO_DIR`, `LENTA_STATE_DIR` (без них — код 78),
   `LENTA_RELEASE_REMOTE` (только ssh-алиас `gh-850-lenta:…`), `LENTA_GIT_NAME` и
   `LENTA_GIT_EMAIL` (noreply-адрес из `ops/commit-emails.allow`), `LENTA_PUBLIC_URL`
   (боевая дверь, её читает сверка).
3. **Ключ записи в репозиторий данных.** Отдельный deploy-ключ ed25519 в
   `/srv/dash/.ssh/` (имя файла — в справочнике владельца), открытая часть — в
   **репозиторий данных** `temp-zero-inode-850-lenta-data`: Settings → Deploy keys →
   Allow write access. В репозиторий кода ключ не добавляется никогда: код сервер
   берёт анонимно, а ключ с правом записи в личном репозитории обходит правила
   веток. В `/srv/dash/.ssh/config` — алиас:
   ```
   Host gh-850-lenta
       HostName github.com
       User git
       IdentityFile /srv/dash/.ssh/<ключ Ленты>
       IdentitiesOnly yes
   ```
   Ключ хоста GitHub — заранее в `/srv/dash/.ssh/known_hosts`: под
   `ProtectSystem=strict` каталог только для чтения, и ssh в юните не сможет
   его дописать (`BatchMode=yes` — вопроса не будет, будет отказ). Проверка:
   `sudo -u dash ssh -T gh-850-lenta` — «successfully authenticated».
4. **Правила (rulesets).** Репозиторий кода: `main` — запрет force-push, удаления и
   обновлений с обходом только для владельца (защита от случайной перезаписи;
   deploy-ключей у репозитория кода нет). Репозиторий данных: `release` — запрет
   удаления и force-push (публикация только дописывает коммиты).
5. **Репозиторий данных** — один раз: публичный `temp-zero-inode-850-lenta-data`,
   ветка `release` по умолчанию (осиротевшая: `README.md` и `.nojekyll`). Если его
   нет — создать и выложить заготовку:
   ```bash
   sudo -u dash bash -c 'set -a; . /usr/local/etc/lenta-850/env; set +a;
        cd /srv/dash/repo-850-lenta && .venv/bin/python ops/publish.py init'
   ```
   Состояние (`/var/lib/lenta-850`) создаёт systemd при первом запуске юнита;
   для ручных команд до него — `sudo install -d -o dash -g dash -m 750 /var/lib/lenta-850`.
6. **Юниты** (маску раскрывает root: у `claude` нет доступа в `/srv/dash`).
   ```bash
   sudo sh -c 'install -o root -g root -m 644 /srv/dash/repo-850-lenta/ops/systemd/* /etc/systemd/system/'
   sudo systemctl daemon-reload
   ```
7. **Первый выпуск руками** (видно все шаги): `sudo systemctl start lenta850-daily.service`
   и `journalctl -u lenta850-daily -f`. Такт кончится кодом 8 «боевая дверь не
   подтвердила»: Pages ещё не включён, а витрина не выложена — это ожидаемо.
8. **GitHub Pages репозитория данных**: Settings → Pages → Deploy from a branch →
   `release`, `/ (root)`. Через 1–3 минуты `https://ml371kl.github.io/temp-zero-inode-850-lenta-data/latest.json`
   отдаёт выпуск. Actions в репозитории данных не выключать: на них держится сборка Pages.
9. **Витрина** — с ноутбука (раздел 8). Затем сверка дожимается сама
   (`lenta850-rebuild`) или руками: `.venv/bin/python ops/publish.py --verify`.
10. **Таймеры**: `sudo systemctl enable --now lenta850-collect.timer lenta850-daily.timer lenta850-rebuild.timer`.
11. **Сторож и мост** — регистрация по процедуре служебного репозитория
    (раздел 4), затем проба доставки тревоги.
12. **Замер времени** — после первых тактов: время шагов из журнала → `ops/budgets.json`
    (`status: measured`, `measured_on`) → потолки юнитов и `LOCK_WAIT_SECONDS`
    (раздел 9) → юниты заново (раздел 7).

## 6. Откат

```bash
sudo -u dash env ENV_FILE=/usr/local/etc/lenta-850/env \
     bash /srv/dash/repo-850-lenta/ops/run.sh rollback <sha выпуска, от 12 знаков>
```

Под тем же замком, что и такты: `latest.json` перезаписывается выпуском
`releases/<sha>.json` с НОВЫМ `published_at` (со старым сторож на откате старше
74 ч объявил бы панель мёртвой), затем сверка. Список выпусков — `git log` и
`ls releases/` в `/var/lib/lenta-850/release-repo`. `release.commit` откат не
трогает. Откат — пауза на один такт, а не состояние: ближайший суточный такт
(или пересборка на новом коммите) соберёт новый выпуск. Если причина не
устранена — остановить таймеры:
`sudo systemctl stop lenta850-daily.timer lenta850-rebuild.timer`.

## 7. Выкладка кода и юнитов

Код попадает на сервер только через `main`: рабочая ветка → CI зелёный →
fast-forward `main` → ближайшая пересборка (`*:07,22,37,52`) публикует выпуск на
новом коде. Если меняются сами юниты: остановить таймер пересборки, поставить
юниты из обновлённой копии (`install … /etc/systemd/system/`),
`daemon-reload`, запустить таймер. Пересборка идёт под потолком УСТАНОВЛЕННОГО
юнита.

## 8. Витрина

```bash
npx wrangler@4.135.0 pages deploy web --project-name tzi-850-lenta --branch main
```

Метка `--branch` обязана совпадать с `production_branch` проекта (`main`),
иначе деплой молча уйдёт в preview. Функции — только `/api/*`
(`web/_routes.json`), CSP с хэшем инлайн-скрипта темы — в `web/_headers` и
`functions/_middleware.js` (одна политика, сверяет `tests/test_edge.py`); при
правке скрипта темы в `web/index.html` тест `test_theme_script_hash_matches_csp`
печатает готовую строку на замену. Локальный предпросмотр —
`python ops/tools/devserver.py`.

## 9. Потолки времени и бюджеты

- **Время**: `RuntimeMaxSec` юнитов и `LOCK_WAIT_SECONDS` считаются из
  `ops/budgets.json` (формула — в поле `about`); равенство сверяет
  `tests/test_ops_units.py`. До замера на сервере там оценка
  (`status: estimate`): сборка 600 с, тесты такта 180 с, сверка до 600 с,
  запас ×1,5. Потолок ниже настоящего времени убивает такт на середине.
- **Память**: `MemoryHigh=384M`, `MemoryMax=512M`, `Nice=10` — сервер общий.
- **Диск**: сырой архив индикаторов под потолком `indicators/store.py`;
  клон `release-repo` — десятки КБ на выпуск (одинаковые файлы — один blob).
- **GitHub**: Actions и Pages публичного репозитория бесплатны; Pages из ветки —
  мягкий лимит 10 сборок в час (пересборка публикует не чаще раза в 15 минут).
  Расписание CI GitHub выключает после 60 дней без активности — поэтому прогон
  «в будущем» идёт и на сервере.

## 10. Диагностика

```bash
systemctl list-timers 'lenta850-*'
journalctl -u lenta850-daily -n 200 --no-pager
tail -n 100 /var/lib/lenta-850/logs/$(date -u +%F).log
cat /var/lib/lenta-850/release.commit /var/lib/lenta-850/release.pushed 2>/dev/null
curl -sI https://tzi-850-lenta.pages.dev/api/model | grep -iE '^(HTTP|last-modified|etag|x-data-source)'
```

`x-data-source: raw` или `edge-cache` — GitHub Pages ветки не отдаёт: проверить
Settings → Pages и прогоны `pages-build-deployment` в Actions.
