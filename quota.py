#!/usr/bin/env python3
"""Devin weekly quota poller + dashboard server.

Usage:
  quota.py poll          fetch quota once, append to history.jsonl, print one-liner
  quota.py serve [port]  serve dashboard at http://localhost:8377 (default), auto-poll every REFRESH_SECS

Data source: GET https://api.devin.ai/{org}/billing/quota/usage (same call the
Devin webapp's settings/usage page makes). Auth token read from
~/.local/share/devin/credentials.toml.
"""
import json, re, sys, time, urllib.request, datetime, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HISTORY = ROOT / "history.jsonl"
CREDS = Path.home() / ".local/share/devin/credentials.toml"
API = "https://api.devin.ai"
POLL_INTERVAL = 60  # seconds; server refreshes when data older than this


def token():
    m = re.search(r'windsurf_api_key = "(.+)"', CREDS.read_text())
    return m.group(1)


def org_id():
    m = re.search(r'"org_id":\s*"([^"]+)"', (Path.home() / ".config/devin/config.json").read_text())
    return m.group(1)


def api_get(path):
    req = urllib.request.Request(API + path, headers={"Authorization": "Bearer " + token()})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def fetch_snapshot():
    org = org_id()
    out = {"ts": time.time()}
    try:
        q = api_get(f"/{org}/billing/quota/usage")
        out["quota"] = q
    except Exception as e:
        out["quota_error"] = str(e)
    try:
        out["stats"] = api_get(f"/{org}/billing/usage/stats")
    except Exception as e:
        out["stats_error"] = str(e)
    try:
        out["limits"] = api_get(f"/{org}/billing/usage/limits")
    except Exception:
        pass
    return out


def poll(verbose=True):
    snap = fetch_snapshot()
    q = snap.get("quota", {})
    with HISTORY.open("a") as f:
        f.write(json.dumps(snap) + "\n")
    if verbose:
        wk = q.get("weekly_percentage", "?")
        reset = q.get("weekly_reset_at", "?")
        print(f"{time.strftime('%H:%M:%S')}  weekly={wk}%  daily={q.get('daily_percentage','?')}%  reset={reset}")
    return snap


def load_history():
    if not HISTORY.exists():
        return []
    rows = []
    for line in HISTORY.read_text().splitlines():
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    return rows


def iso_to_ts(s):
    try:
        return datetime.datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def predict(history):
    """Return prediction dict based on weekly_pct history within the current week window."""
    latest = history[-1] if history else {}
    q = latest.get("quota", {})
    pct = q.get("weekly_percentage")
    reset_ts = iso_to_ts(q.get("weekly_reset_at", ""))
    week_start = reset_ts - 7 * 86400 if reset_ts else None
    pts = [(h["ts"], h["quota"]["weekly_percentage"]) for h in history
           if h.get("quota", {}).get("weekly_percentage") is not None
           and (week_start is None or h["ts"] > week_start - 86400)]
    out = {"pct": pct, "reset_ts": reset_ts, "samples": len(pts)}
    if len(pts) >= 2 and pct is not None:
        (t0, p0), (t1, p1) = pts[0], pts[-1]
        span_h = (t1 - t0) / 3600
        rate = (p1 - p0) / span_h if span_h > 0.05 else None  # %/hour over the whole window
        out["window_hours"] = span_h
        out["rate_per_hour"] = rate
        out["rate_per_day"] = rate * 24 if rate is not None else None
        if rate and rate > 0:
            out["exhaust_ts"] = time.time() + (100 - pct) / rate
            if reset_ts:
                out["projected_pct_at_reset"] = pct + rate * (reset_ts - time.time())
        # recent slope: last 2 samples
        (ta, pa), (tb, pb) = pts[-2], pts[-1]
        dh = (tb - ta) / 3600
        if dh > 0:
            out["recent_rate_per_hour"] = (pb - pa) / dh
    # naive elapsed-based projection: pct/elapsed * 7d
    if week_start and pct is not None and time.time() > week_start:
        elapsed_frac = (time.time() - week_start) / (7 * 86400)
        if elapsed_frac > 0.005:
            out["pace_projection_pct"] = pct / elapsed_frac
    return out


def state(force=False):
    hist = load_history()
    stale = not hist or (time.time() - hist[-1]["ts"]) > POLL_INTERVAL
    if force or stale:
        poll(verbose=False)
        hist = load_history()
    return {"latest": hist[-1] if hist else {}, "history": hist, "prediction": predict(hist)}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/api/state"):
            force = "refresh=1" in self.path
            body = json.dumps(state(force=force)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
        elif self.path == "/" or self.path.startswith("/index"):
            body = (ROOT / "dashboard.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()


def serve(port=8377):
    poll(verbose=True)  # seed
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"dashboard: http://localhost:{port}  (polls quota every {POLL_INTERVAL}s on refresh)")
    srv.serve_forever()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "poll":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 1
        for _ in range(n):
            poll()
            if _ < n - 1:
                time.sleep(POLL_INTERVAL)
    elif len(sys.argv) > 1 and sys.argv[1] == "serve":
        serve(int(sys.argv[2]) if len(sys.argv) > 2 else 8377)
    else:
        print(__doc__)
