import os
import json
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(os.environ.get("PORT", "10000"))


def extract_video(url):
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
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
            page.wait_for_timeout(8000)
            try:
                vids = page.eval_on_selector_all(
                    "video", "els => els.map(e => e.currentSrc || e.src).filter(Boolean)")
                for v in vids:
                    if v not in found:
                        found.append(v)
            except Exception:
                pass
            browser.close()
        return {"videos": found}
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
            url = qs.get("url", [""])[0]
            if not url or not url.startswith("http"):
                return self._send(400, {"error": "missing url param"})
            try:
                res = extract_video(url)
                if "error" in res:
                    return self._send(200, {"url": url, "videos": [], "count": 0, "warn": res["error"]})
                return self._send(200, {"url": url, "videos": res.get("videos", []), "count": len(res.get("videos", []))})
            except Exception as e:
                return self._send(500, {"error": str(e)})
        return self._send(404, {"error": "not_found"})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"Extractor escuchando en 0.0.0.0:{PORT}", flush=True)
    HTTPServer(("0.0.0.0", PORT), handler).serve_forever()
