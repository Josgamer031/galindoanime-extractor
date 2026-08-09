import os
import json
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(os.environ.get("PORT", "10000"))


def extract_video(url, wait_ms=15000):
    """Abre el embed con Chromium headless y captura la URL de video cruda.

    El import de playwright es LAZY (dentro de la funcion) para que el
    servidor HTTP arranque siempre, aunque playwright no este disponible;
    asi el endpoint responde y podemos Diagnosticar el error de forma remota.
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception as imp_err:
        return {"error": "playwright_import_failed: " + str(imp_err)}
    try:
        import re as _re
        found = []
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
                    ct = resp.headers.get("content-type", "").lower()
                    u = resp.url
                    if "video" in ct or u.lower().endswith((".mp4", ".m3u8", ".ts", ".webm")):
                        if u not in found:
                            found.append(u)
                except Exception:
                    pass

            page.on("response", on_response)
            try:
                page.goto(url, wait_until="networkidle", timeout=45000)
            except Exception:
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                except Exception:
                    pass
            # Esperar a que cargue el player (el video aparece tras JS/ads)
            page.wait_for_timeout(wait_ms)
            # <video> en el DOM (currentSrc/src)
            try:
                vids = page.eval_on_selector_all(
                    "video", "els => els.map(e => e.currentSrc || e.src).filter(Boolean)")
                for v in vids:
                    if v not in found:
                        found.append(v)
            except Exception:
                pass
            # fallback: buscar URLs de video directas en el HTML final
            try:
                html = page.content()
                for m in _re.finditer(r'(https?://[^\s"\'<>]+?\.(?:mp4|m3u8|ts|webm))', html, _re.I):
                    u = m.group(1)
                    if u not in found:
                        found.append(u)
            except Exception:
                pass
            browser.close()
        return {"videos": found}
    except Exception as e:
        return {"error": str(e)}


def extract_many(urls, wait_ms=15000):
    """Prueba VARIOS embeds en PARALELO (threads) y devuelve el primer
    video crudo que cualquiera resuelva. Asi el /episode no espera N veces
    secuencialmente: la latencia es la de UN solo embed (el mas rapido)."""
    import threading
    results = {}
    lock = threading.Lock()

    def worker(idx, u):
        r = extract_video(u, wait_ms=wait_ms)
        with lock:
            results[idx] = r

    threads = []
    for i, u in enumerate(urls[:6]):  # maximo 6 en paralelo
        t = threading.Thread(target=worker, args=(i, u), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join(timeout=wait_ms / 1000 + 60)
    # devolver el primer video encontrado, en orden de los embeds
    for i in sorted(results.keys()):
        r = results[i]
        if isinstance(r, dict) and r.get("videos"):
            return {"videos": r["videos"]}
    # si ninguno resolvio, devolver el primer error encontrado (o vacio)
    for i in sorted(results.keys()):
        r = results[i]
        if isinstance(r, dict) and "error" in r:
            return {"error": r["error"]}
    return {"videos": []}


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
            # soporta multiples embeds separados por coma (urls=a,b,c)
            flat = []
            for u in urls:
                for part in u.split(","):
                    part = part.strip()
                    if part:
                        flat.append(part)
            if not flat or not flat[0].startswith("http"):
                return self._send(400, {"error": "missing url param"})
            try:
                wait = int(qs.get("wait", ["15000"])[0])
            except Exception:
                wait = 15000
            try:
                res = extract_many(flat, wait_ms=wait)
                if "error" in res:
                    return self._send(200, {"url": flat[0], "videos": [], "count": 0, "warn": res["error"]})
                return self._send(200, {"url": flat[0], "videos": res.get("videos", []), "count": len(res.get("videos", []))})
            except Exception as e:
                return self._send(500, {"error": str(e)})
        return self._send(404, {"error": "not_found"})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"Extractor escuchando en 0.0.0.0:{PORT}", flush=True)
    HTTPServer(("0.0.0.0", PORT), handler).serve_forever()
