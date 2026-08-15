import os
import json
import urllib.parse
import re as _re
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(os.environ.get("PORT", "10000"))

# Patrones de URL que suelen ser media cruda (mp4/m3u8/ts/webm) incluso si la
# extension no es canonica (los hosts ofuscados la esconden en query params).
_MEDIA_RE = _re.compile(
    r'(https?://[^\s"\'<>]+?\.(?:mp4|m3u8|ts|webm|m4v|mov)(?:[?&#][^\s"\'<>]*)?)', _re.I)
# En el HTML los players ponen la fuente en strings tipo file:"...", hls:"...",
# source:"...", "url":"..." o data-src. Captamos cualquier URL con esos vecinos.
_SRC_RE = _re.compile(
    r'(?:file|hls|src|source|url|data-src|data-file|video|stream|playlist)'
    r'\s*[:=]\s*["\']?(https?://[^\s"\']+?)["\']?', _re.I)
# URLs "sueltas" (los players ofuscados parten la URL en strings JS
# concatenados, ej. "https://w"+"mixdrop.co/get/x.m3u8"). Captamos https://...
# hasta espacio y luego saneamos/filtramos por palabras clave de media.
_LOOSE_RE = _re.compile(r'https?://\S+', _re.I)
_MEDIA_KEYS = ("mp4", "m3u8", "ts?", "webm", "m4v", "mixdrop", "voe", "byse",
               "hexload", "savefiles", "stream", "hls", "playlist", "get/",
               "file/", "media", "video", "cdn", "source", "manifest")


def extract_video(url, wait_ms=20000):
    """Abre el embed con Chromium headless y captura la URL de video cruda.

    Estrategia multi-capa (los hosts de peliculas ofuscan el video de formas
    distintas):
      1. Listener de TODAS las respuestas de red: capta .mp4/.m3u8/.ts/.webm por
         extension Y por content-type (mpegurl/hls/video). Las respuestas de
         sub-iframes cross-origin TAMBIEN se captan (Playwright escucha a nivel
         de red del browser, no del DOM).
      2. DOM: <video> currentSrc/src, <source> y players globales
         (jwplayer/plyr/videojs) que exponen la fuente en JS.
      3. HTML final: regex amplio para URLs media y para strings tipo
         file:"..."/hls:"..." que los players ofuscados dejan en el script.
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception as imp_err:
        return {"error": "playwright_import_failed: " + str(imp_err)}
    try:
        found = []
        def add(u):
            u = (u or "").strip()
            if u and u.startswith("http") and u not in found and "blob:" not in u:
                found.append(u)
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                      "--autoplay-policy=no-user-gesture-required"],
            )
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"),
                java_script_enabled=True,
                ignore_https_errors=True,
            )
            page = ctx.new_page()

            def on_response(resp):
                try:
                    ct = (resp.headers.get("content-type") or "").lower()
                    u = resp.url
                    if (("video" in ct) or ("mpegurl" in ct) or ("hls" in ct)
                            or u.lower().endswith((".mp4", ".m3u8", ".ts", ".webm", ".m4v"))):
                        add(u)
                    # Muchos hosts (mixdrop, voe, bysekoze) entregan la URL del
                    # video en el BODY de un XHR (JSON/JS), no en la URL ni en un
                    # <video> directo. Escaneamos el body de respuestas pequeñas
                    # (JSON/JS/text) buscando URLs de media ofuscadas.
                    elif ("json" in ct) or ("javascript" in ct) or ("text" in ct) or ("xml" in ct):
                        try:
                            body = resp.text()[:200000]
                            if body:
                                for m in _MEDIA_RE.finditer(body):
                                    add(m.group(1))
                                for m in _SRC_RE.finditer(body):
                                    add(m.group(1))
                        except Exception:
                            pass
                except Exception:
                    pass

            page.on("response", on_response)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
            # Dejar que el player resuelva la fuente (HLS via JS/MSE tarda)
            page.wait_for_timeout(wait_ms)
            # DOM: <video> + <source>
            try:
                vids = page.eval_on_selector_all(
                    "video, source",
                    "els => els.flatMap(e => [e.currentSrc||e.src||e.getAttribute('src')||'']"
                    ".filter(Boolean))")
                for v in vids:
                    add(v)
            except Exception:
                pass
            # Players globales que exponen la fuente
            try:
                js = (
                    "() => {"
                    "  var out = [];"
                    "  try { if (window.jwplayer) { var p = jwplayer(); if (p && p.getPlaylist) {"
                    "    p.getPlaylist().forEach(function(i){ if(i.file) out.push(i.file); }); } } } catch(e){}"
                    "  try { if (window.videojs) { document.querySelectorAll('video').forEach(function(v){"
                    "    var s = v.currentSrc||v.src; if(s) out.push(s); }); } } catch(e){}"
                    "  try { if (window.plyr) { document.querySelectorAll('video').forEach(function(v){"
                    "    var s = v.currentSrc||v.src; if(s) out.push(s); }); } } catch(e){}"
                    "  return out;"
                    "}"
                )
                for v in page.evaluate(js):
                    add(v)
            except Exception:
                pass
            # HTML final: URLs "sueltas" (players ofuscados las parten en JS).
            # Captamos https://... hasta espacio, saneamos comillas/parentesis
            # sobrantes y filtramos por palabras clave de media para descartar
            # basura (ej. "https://w"+"mixdrop.co/get/x.m3u8").
            try:
                html = page.content()
                for m in _LOOSE_RE.finditer(html):
                    raw = m.group(0)
                    raw = raw.strip().strip('"\'').strip().rstrip('\\\'");,')
                    if " " in raw:
                        raw = raw.split(" ")[0]
                    low = raw.lower()
                    if any(k in low for k in _MEDIA_KEYS) and raw.startswith("http"):
                        add(raw)
                for m in _SRC_RE.finditer(html):
                    add(m.group(1))
            except Exception:
                pass
            browser.close()
        return {"videos": found}
    except Exception as e:
        return {"error": str(e)}


def extract_many(urls, wait_ms=20000):
    """Prueba VARIOS embeds en PARALELO (threads) y devuelve el primer
    video crudo que cualquiera resuelva."""
    import threading
    results = {}
    lock = threading.Lock()

    def worker(idx, u):
        r = extract_video(u, wait_ms=wait_ms)
        with lock:
            results[idx] = r

    threads = []
    for i, u in enumerate(urls[:6]):
        t = threading.Thread(target=worker, args=(i, u), daemon=True)
        t.start()
        threads.append(t)
    import time as _t
    deadline = _t.time() + (wait_ms / 1000) + 45
    while _t.time() < deadline:
        for i in sorted(results.keys()):
            r = results.get(i)
            if isinstance(r, dict) and r.get("videos"):
                return {"videos": r["videos"]}
        if all(not t.is_alive() for t in threads):
            break
        _t.sleep(1)
    for t in threads:
        t.join(timeout=1)
    for i in sorted(results.keys()):
        r = results[i]
        if isinstance(r, dict) and r.get("videos"):
            return {"videos": r["videos"]}
    for i in sorted(results.keys()):
        r = results[i]
        if isinstance(r, dict) and "error" in r:
            return {"error": r["error"]}
    return {"videos": []}


def diag_video(url, wait_ms=20000):
    """Endpoint de diagnostico: capta TODAS las respuestas de red (url+ctype)
    y un snippet del HTML."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception as imp_err:
        return {"error": "playwright_import_failed: " + str(imp_err)}
    try:
        resp_log = []
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            )
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"),
                java_script_enabled=True,
            )
            page = ctx.new_page()

            def on_response(resp):
                try:
                    ct = resp.headers.get("content-type", "")
                    u = resp.url
                    if any(k in (ct.lower() + " " + u.lower()) for k in
                           ("mp4", "m3u8", "ts?", "webm", "mpegurl", "manifest",
                            "playlist", "video", "player", "embed", "stream",
                            "source", "file", ".mp4")):
                        resp_log.append({"u": u[:200], "ct": ct[:60]})
                except Exception:
                    pass

            page.on("response", on_response)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
            page.wait_for_timeout(wait_ms)
            html = ""
            try:
                html = page.content()
            except Exception:
                pass
            doms = []
            try:
                doms = page.eval_on_selector_all(
                    "video", "els => els.map(e => (e.currentSrc||e.src||'')).filter(Boolean)")
            except Exception:
                pass
            browser.close()
        return {
            "frames_count": len(resp_log),
            "responses": resp_log[:80],
            "dom_videos": doms[:10],
            "html_len": len(html),
            "html_snippet": html[:1500],
        }
    except Exception as e:
        return {"error": str(e)}


class handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path.rstrip("/") in ("/extract", ""):
            qs = urllib.parse.parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            urls = qs.get("url", [""])
            flat = []
            for u in urls:
                for part in u.split(","):
                    part = part.strip()
                    if part:
                        flat.append(part)
            if not flat or not flat[0].startswith("http"):
                return self._send(400, {"error": "missing url param"})
            try:
                wait = int(qs.get("wait", ["20000"])[0])
            except Exception:
                wait = 20000
            try:
                res = extract_many(flat, wait_ms=wait)
                if "error" in res:
                    return self._send(200, {"url": flat[0], "videos": [], "count": 0, "warn": res["error"]})
                return self._send(200, {"url": flat[0], "videos": res.get("videos", []), "count": len(res.get("videos", []))})
            except Exception as e:
                return self._send(500, {"error": str(e)})
        elif path.rstrip("/") == "/diag":
            qs = urllib.parse.parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            u = (qs.get("url", [""])[0] or "").strip()
            if not u.startswith("http"):
                return self._send(400, {"error": "missing url param"})
            try:
                wait = int(qs.get("wait", ["20000"])[0])
            except Exception:
                wait = 20000
            try:
                res = diag_video(u, wait_ms=wait)
                return self._send(200, res)
            except Exception as e:
                return self._send(500, {"error": str(e)})
        return self._send(404, {"error": "not_found"})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"Extractor escuchando en 0.0.0.0:{PORT}", flush=True)
    HTTPServer(("0.0.0.0", PORT), handler).serve_forever()
