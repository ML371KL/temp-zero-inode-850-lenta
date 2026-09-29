/**
 * Единственная дверь браузера к данным модели: GET/HEAD /api/model.
 *
 * Канал (DESIGN §2): конвейер кладёт выпуск в ветку `release` публичного
 * репозитория (`ops/publish.py`), GitHub Pages раздаёт её как статику, а эта
 * функция читает `latest.json` на своём origin — фронт ходит только сюда.
 *
 * ИСТОЧНИКИ по порядку:
 *   1. GitHub Pages ветки release — основной. Запрос несёт минутную метку
 *      `?m=<floor(now/60000)>`, которую строит СЕРВЕР (query клиента не
 *      передаётся): CDN GitHub кэширует ответ на 10 минут, и без метки выпуск
 *      доезжал бы до витрины с опозданием до 10 минут. `cf.cacheTtl` 60 —
 *      край держит ответ не дольше минуты, и в пределах минуты все зрители
 *      получают один подзапрос.
 *   2. raw.githubusercontent.com той же ветки — запасной: сборка Pages ему не
 *      нужна (Pages из ветки собирается workflow `pages-build-deployment`, и в
 *      инциденты Actions она встаёт в очередь). У raw свой кэш ≈5 минут и
 *      лимиты на анонимные запросы — поэтому он только запасной.
 *   3. Последняя годная копия в Cache API края — если оба источника отказали.
 *      Устаревшее лучше пустого экрана, а возраст виден по Last-Modified.
 *
 * Ответ источника принимается только после СТРОГОГО разбора: JSON (NaN и
 * Infinity `JSON.parse` не пропускает), объект со `schema` = "lenta-v1",
 * `meta.payload_sha256` (64 hex) и разбираемым `meta.published_at`. HTML
 * вместо данных, битый JSON или чужая схема — отказ источника, а не 200.
 *
 * ЗАГОЛОВКИ 200:
 *   Last-Modified   из `meta.published_at` (момент публикации; его обновляет и
 *                   откат) — по нему сторож `dash-watch` решает, жива ли панель;
 *   ETag            сильный, "<sha12>.<published_at в секундах>": тот же
 *                   выпуск, опубликованный заново, — другой ETag, иначе браузер
 *                   получил бы 304 на вчерашнее время публикации;
 *   Cache-Control   public, max-age=60;
 *   x-data-source   pages | raw | edge-cache — сверка `ops/publish.py --verify`
 *                   поднимает тревогу, если витрина живёт не на Pages.
 *
 * 503, а не 404, если данных нет: 404 — «такого не бывает», 503 — «ещё нет».
 * Сторож различает эти состояния. Причина в теле: «not published yet» — оба
 * источника ответили 404 (ветку ещё не публиковали), «upstream unavailable» —
 * всё прочее. Чужие пути под /api/ отсекает `functions/_middleware.js`.
 */

const SCHEMA = "lenta-v1";
const PRIMARY = "https://ml371kl.github.io/temp-zero-inode-850-lenta-data/latest.json";
const FALLBACK = "https://raw.githubusercontent.com/ML371KL/temp-zero-inode-850-lenta-data/release/latest.json";
const CACHE_SECONDS = 60;
// Сколько край держит последнюю годную копию. Cache API соблюдает
// Cache-Control сохранённого ответа: с max-age=60 копия умирала бы через минуту
// и не спасала бы ни от чего. Вытеснить её край может и раньше — это запас, а
// не обязательство.
const LAST_GOOD_SECONDS = 30 * 24 * 3600;
// Ключ копии — на СВОЁМ хосте: Cache API принимает только абсолютные адреса,
// а чужой хост в ключе — это чужое пространство имён.
const LAST_GOOD_PATH = "/__edge-cache/api/model/latest.json";
const UPSTREAM_TIMEOUT_MS = 8000;
// Только ASCII: заголовки HTTP кодируются latin-1.
const USER_AGENT = "tzi-850-lenta-edge/1.0";
const SHA_RE = /^[0-9a-f]{64}$/;

export async function onRequest(context) {
  const { request } = context;
  if (request.method !== "GET" && request.method !== "HEAD") {
    return json(405, { error: "method not allowed", method: request.method },
      { allow: "GET, HEAD" });
  }

  const minute = Math.floor(Date.now() / 60000);
  const pages = await fetchSource(`${PRIMARY}?m=${minute}`, "pages");
  const chosen = pages.ok ? pages : null;
  const raw = chosen ? null : await fetchSource(FALLBACK, "raw");
  const good = chosen || (raw && raw.ok ? raw : null);

  if (good) {
    const response = respond(request, good.text, good.release, good.label);
    remember(context, request, good.text);
    return response;
  }

  const copy = await lastGood(request);
  if (copy) {
    return respond(request, copy.text, copy.release, "edge-cache");
  }

  const missing = pages.status === 404 && raw && raw.status === 404;
  return json(503, {
    error: missing ? "not published yet" : "upstream unavailable",
    pages: pages.detail,
    raw: raw ? raw.detail : null,
  });
}

/** Подзапрос к источнику: { ok, label, status, detail, text?, release? }. */
async function fetchSource(url, label) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), UPSTREAM_TIMEOUT_MS);
  try {
    const response = await fetch(url, {
      headers: { accept: "application/json", "user-agent": USER_AGENT },
      cf: { cacheTtl: CACHE_SECONDS },
      signal: controller.signal,
    });
    if (!response.ok) {
      return { ok: false, label, status: response.status, detail: `http ${response.status}` };
    }
    const text = await response.text();
    const parsed = parseRelease(text);
    if (parsed.error) {
      return { ok: false, label, status: response.status, detail: parsed.error };
    }
    return { ok: true, label, status: response.status, text, release: parsed.release };
  } catch (error) {
    const name = error && error.name === "AbortError" ? "timeout" : String((error && error.name) || error);
    return { ok: false, label, status: 0, detail: name.slice(0, 80) };
  } finally {
    clearTimeout(timer);
  }
}

/** Строгий разбор выпуска. Возвращает { release, published } или { error }. */
function parseRelease(text) {
  let data;
  try {
    data = JSON.parse(text);
  } catch (error) {
    return { error: "bad json" };
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) return { error: "not an object" };
  if (data.schema !== SCHEMA) return { error: `schema ${JSON.stringify(data.schema)}`.slice(0, 80) };
  const meta = data.meta;
  if (!meta || typeof meta !== "object") return { error: "no meta" };
  if (typeof meta.payload_sha256 !== "string" || !SHA_RE.test(meta.payload_sha256)) {
    return { error: "no payload_sha256" };
  }
  const published = typeof meta.published_at === "string" ? Date.parse(meta.published_at) : NaN;
  if (!Number.isFinite(published)) return { error: "no published_at" };
  return { release: { sha: meta.payload_sha256, published } };
}

function respond(request, text, release, source) {
  const headers = new Headers({
    "content-type": "application/json; charset=utf-8",
    "cache-control": `public, max-age=${CACHE_SECONDS}`,
    "last-modified": new Date(release.published).toUTCString(),
    etag: `"${release.sha.slice(0, 12)}.${Math.floor(release.published / 1000)}"`,
    "x-data-source": source,
    "x-data-published": new Date(release.published).toISOString(),
    "x-content-type-options": "nosniff",
  });
  // Префикс `W/` появляется, когда край отдаёт ответ сжатым: слабый ETag не
  // равен сильному посимвольно, и без снятия префикса 304 не работал бы вовсе.
  const strip = (tag) => tag.trim().replace(/^W\//, "");
  const inm = request.headers.get("if-none-match");
  if (inm && inm.split(",").map(strip).includes(strip(headers.get("etag")))) {
    return new Response(null, { status: 304, headers });
  }
  if (request.method === "HEAD") return new Response(null, { status: 200, headers });
  return new Response(text, { status: 200, headers });
}

function lastGoodKey(request) {
  return new Request(new URL(LAST_GOOD_PATH, request.url).toString(), { method: "GET" });
}

/** Годную копию — в кэш края ФОНОМ: ответ не ждёт записи. */
function remember(context, request, text) {
  if (typeof caches === "undefined" || !caches.default) return;
  const copy = new Response(text, {
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": `public, max-age=${LAST_GOOD_SECONDS}`,
    },
  });
  const save = caches.default.put(lastGoodKey(request), copy).catch(() => {});
  if (typeof context.waitUntil === "function") context.waitUntil(save);
}

async function lastGood(request) {
  if (typeof caches === "undefined" || !caches.default) return null;
  try {
    const cached = await caches.default.match(lastGoodKey(request));
    if (!cached) return null;
    const text = await cached.text();
    const parsed = parseRelease(text);
    return parsed.error ? null : { text, release: parsed.release };
  } catch (error) {
    return null;
  }
}

function json(status, body, extra) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
      ...(extra || {}),
    },
  });
}
