/**
 * Общий фильтр всех запросов к функциям сайта. Делает две вещи, которые нельзя
 * сделать ни в статике, ни внутри самих функций.
 *
 * 1. ДЕРЖИТ ГРАНИЦУ `/api/`. Cloudflare Pages на путь, для которого не нашлось
 * статики, отдаёт **200 и HTML главной страницы**. Значит `/api//model`,
 * `/api/../model`, `/api/%2e%2e/model` и `/api/model/` в маршрутизацию функций
 * не попадут вовсе и уйдут к статике — а программа, склеившая базу с ключом и
 * получившая лишний слеш, прочитает «успех» и разберёт вёрстку как JSON (так
 * соседняя панель владельца заплатила дважды за двое суток). Здесь путь под
 * `/api/` либо канонический (`model`), либо получает JSON-404. Никакой
 * нормализации «догадайся, что имел в виду клиент».
 *
 * 2. СТАВИТ ЗАГОЛОВКИ БЕЗОПАСНОСТИ на ответы `/api/*`. `web/_headers` Pages
 * накладывает только на статику, а ответы функций остались бы без `nosniff`.
 * Статика получает ту же политику из `web/_headers`: функции вызываются только
 * для `/api/*` (`web/_routes.json`), и обе копии политики посимвольно сверяет
 * `tests/test_edge.py::test_csp_is_the_same_in_headers_and_middleware`.
 */

// Хэш инлайн-скрипта темы из web/index.html (он ставит тему ДО первой отрисовки,
// иначе ночью моргает светлая). Считается по содержимому <script> с переводами
// строк LF. Пересчитывать при ЛЮБОЙ правке того скрипта — здесь и в
// web/_headers; расхождение ловит test_theme_script_hash_matches_csp и печатает
// готовую строку на замену, а test_csp_is_the_same_in_headers_and_middleware —
// расхождение двух копий.
const THEME_SCRIPT_HASH = "sha256-FuGfJdvXEr5tGmg6MtrIr5idnIkivcwPhVk/lN1ZVOs=";

const CSP = [
  "default-src 'self'",
  `script-src 'self' '${THEME_SCRIPT_HASH}'`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  "connect-src 'self'",
  "font-src 'self'",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
  "frame-ancestors 'none'",
].join("; ");

// Имена функций под /api/. Список обязан совпадать с файлами functions/api/*.js
// (тест test_api_allowlist_matches_functions).
const ALLOWED_API = new Set(["model"]);

export async function onRequest({ request, next }) {
  const url = new URL(request.url);

  if (url.pathname.startsWith("/api")) {
    const rest = url.pathname.slice(4);
    const name = rest.startsWith("/") ? rest.slice(1) : rest;
    if (!ALLOWED_API.has(name)) {
      return harden(new Response(
        JSON.stringify({ error: "not found", requested: name.slice(0, 64) }),
        { status: 404, headers: { "content-type": "application/json; charset=utf-8" } }));
    }
  }

  return harden(await next());
}

function harden(response) {
  const out = new Response(response.body, response);
  out.headers.set("content-security-policy", CSP);
  out.headers.set("x-content-type-options", "nosniff");
  out.headers.set("referrer-policy", "no-referrer");
  out.headers.set("permissions-policy", "geolocation=(), camera=(), microphone=()");
  return out;
}
