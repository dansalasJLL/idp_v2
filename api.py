"""
api.py — read-only HTTP API over the portfolio store, for Power BI's Web connector.

The folder/CSV path (portfolio.py) is the zero-setup option. This is the
"graduates to a server" path: when the tool runs on a host, Power BI can pull
from URLs instead of a shared folder, using Get Data -> Web.

Endpoints (all GET, JSON):
  /health                 -> {"status": "ok", ...}
  /manifest               -> schema + refresh metadata
  /contracts              -> [ {contract row}, ... ]      (dimension)
  /obligations            -> [ {obligation row}, ... ]    (fact)
  /summary                -> portfolio rollup

Deliberately stdlib-only (http.server) so it adds no dependency and can run
anywhere the tool runs. It's READ-ONLY — it never mutates the store — so it is
safe to expose to the Power BI service. Put a reverse proxy / auth in front of
it for production (documented in POWERBI.md); for a first internal rollout,
bind it to the internal network only.

Run:
    python api.py --port 8600 --store portfolio_store
Then in Power BI: Get Data -> Web -> http://<host>:8600/obligations
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from portfolio import PortfolioStore


def make_handler(store: PortfolioStore):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, payload, code=200):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            # Power BI tolerates missing CORS, but set it permissive for browser previews.
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            path = urlparse(self.path).path.rstrip("/") or "/"
            routes = {
                "/": lambda: {"service": "idp-portfolio-api",
                              "endpoints": ["/health", "/manifest", "/contracts",
                                            "/obligations", "/summary"]},
                "/health": lambda: {"status": "ok", **store.summary()},
                "/contracts": store.contracts,
                "/obligations": store.obligations,
                "/summary": store.summary,
                "/manifest": lambda: json.load(open(store.manifest_path))
                if __import__("os").path.exists(store.manifest_path) else {"error": "no data yet"},
            }
            handler = routes.get(path)
            if handler is None:
                self._send({"error": "not found", "path": path}, code=404)
                return
            try:
                self._send(handler())
            except Exception as e:  # never leak a stack trace to the client
                self._send({"error": str(e)}, code=500)

        def log_message(self, *args):
            pass  # quiet by default

    return Handler


def serve(port: int = 8600, store_dir: str = "portfolio_store"):
    store = PortfolioStore(root=store_dir)
    httpd = ThreadingHTTPServer(("0.0.0.0", port), make_handler(store))
    print(f"[api] serving portfolio from '{store_dir}' on http://0.0.0.0:{port}")
    print(f"[api] Power BI: Get Data -> Web -> http://<host>:{port}/obligations")
    httpd.serve_forever()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8600)
    ap.add_argument("--store", default="portfolio_store")
    ap.add_argument("--selftest", action="store_true", help="run an in-process test and exit")
    args = ap.parse_args()

    if args.selftest:
        import tempfile, shutil, threading, time, urllib.request

        print("=== api.py self-test ===\n")
        tmp = tempfile.mkdtemp()
        store = PortfolioStore(root=tmp)
        store.record_run({
            "document_name": "API Test.pdf", "page_count": 10, "total": 1,
            "needs_review": 0, "by_risk": {"High": [1], "Medium": [], "Low": []},
            "run_stats": {"total_cost_usd": 0.01, "input_tokens": 100, "output_tokens": 50,
                          "cache_hit_rate": 0.0, "wall_clock_s": 1.0, "model": "m"},
            "obligations": [{"obligation_id": "1-1", "risk_level": "High", "risk_type": "Financial",
                             "priority": "High", "category": "Insurance", "responsible_party": "JLL",
                             "description": "x", "penalty": "USD $20,000", "trigger_type": "Recurring",
                             "confidence": 0.9, "needs_review": False,
                             "mitigation": {"summary": "s", "actions": ["a"], "source": "rules"}}],
        })
        httpd = ThreadingHTTPServer(("127.0.0.1", 8611), make_handler(store))
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        time.sleep(0.3)
        try:
            for ep, check in [
                ("/health", lambda d: d["status"] == "ok"),
                ("/contracts", lambda d: len(d) == 1),
                ("/obligations", lambda d: len(d) == 1 and d[0]["risk_level"] == "High"),
                ("/summary", lambda d: d["high_risk"] == 1),
                ("/manifest", lambda d: d["join_key"] == "contract_run_id"),
            ]:
                with urllib.request.urlopen(f"http://127.0.0.1:8611{ep}") as r:
                    data = json.loads(r.read())
                assert check(data), f"{ep} check failed: {data}"
                print(f"GET {ep}: OK")
            with urllib.request.urlopen("http://127.0.0.1:8611/nope") as r:
                pass
        except urllib.error.HTTPError as e:
            assert e.code == 404
            print("GET /nope -> 404: OK")
        finally:
            httpd.shutdown()
            shutil.rmtree(tmp)
        print("\nAll api self-tests passed.")
    else:
        serve(port=args.port, store_dir=args.store)
