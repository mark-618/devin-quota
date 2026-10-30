#!/usr/bin/env python3
"""Devin weekly quota poller + dashboard server.

Usage:
  quota.py poll          fetch quota once, append to history.jsonl, print one-liner
  quota.py serve [port]  serve dashboard at http://localhost:8377 (default), auto-poll every REFRESH_SECS

Data source: GET https://api.devin.ai/{org}/billing/quota/usage (same call the
Devin webapp's settings/usage page makes). Auth token read from
~/.local/share/devin/credentials.toml.
"""
import json, os, re, sys, time, urllib.request, datetime, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# snapshots live outside the package so pip installs can't clobber data;
# override with DEVIN_QUOTA_HOME
DATA_DIR = Path(os.environ.get("DEVIN_QUOTA_HOME",
                              Path.home() / ".local/share/devin-quota"))
HISTORY = DATA_DIR / "history.jsonl"
CREDS = Path.home() / ".local/share/devin/credentials.toml"
API = "https://api.devin.ai"
POLL_ACTIVE = 60    # seconds between polls while quota is moving
POLL_IDLE = 300     # back off to 5min when idle (integer-% moves slowly)


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
    DATA_DIR.mkdir(parents=True, exist_ok=True)
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
    ok = [h for h in history if h.get("quota", {}).get("weekly_percentage") is not None]
    latest = ok[-1] if ok else {}
    q = latest.get("quota", {})
    pct = q.get("weekly_percentage")
    reset_ts = iso_to_ts(q.get("weekly_reset_at", ""))
    week_start = reset_ts - 7 * 86400 if reset_ts else None
    pts = [(h["ts"], h["quota"]["weekly_percentage"]) for h in history
           if h.get("quota", {}).get("weekly_percentage") is not None
           and (week_start is None or h["ts"] > week_start)]
    out = {"pct": pct, "reset_ts": reset_ts, "samples": len(pts)}
    # hot rate: measured slope over the collected sample window — the upper
    # bound "if you keep burning at the recently observed pace" forecast
    if len(pts) >= 2:
        (ta, pa), (tb, pb) = pts[0], pts[-1]
        dh = (tb - ta) / 3600
        if dh >= 1.0 and pb != pa:
            hot = (pb - pa) / dh
            out["hot_rate_per_day"] = hot * 24
            out["window_hours"] = dh
            if hot > 0:
                out["hot_exhaust_ts"] = time.time() + (100 - pct) / hot * 3600
                if reset_ts:
                    out["hot_projected_pct_at_reset"] = pct + hot * (reset_ts - time.time()) / 3600
    # week-to-date average rate: pct is cumulative since reset, so pct/elapsed
    # is the true mean slope of the underlying curve — same convention as
    # opencode/ccusage "Averaging X% a day" and the forecast line.
    if week_start and pct is not None and time.time() > week_start:
        elapsed_h = (time.time() - week_start) / 3600
        if elapsed_h > 0.2:
            rate = pct / elapsed_h
            out["rate_per_hour"] = rate
            out["rate_per_day"] = rate * 24
            out["rate_source"] = "week_avg"
            out["pace_projection_pct"] = rate * 168
            if rate > 0:
                out["exhaust_ts"] = time.time() + (100 - pct) / rate * 3600
                if reset_ts:
                    out["projected_pct_at_reset"] = pct + rate * (reset_ts - time.time()) / 3600
    return out


SESSDB = Path.home() / ".local/share/devin/cli/sessions.db"
_tok_cache = {"at": 0.0, "data": {"rows": [], "titles": {}}}


def load_tokens():
    """Per-message input-token sizes (context tokens the model read at that
    turn) from the local Devin CLI db — measured facts, read-only. Cached on a
    TTL (not mtime: the db is written constantly by live sessions) so /api/state
    stays cheap."""
    import sqlite3
    if time.time() - _tok_cache["at"] < 60 and _tok_cache["at"]:
        return _tok_cache["data"]
    try:
        db = sqlite3.connect(f"file:{SESSDB}?mode=ro", uri=True)
        rows = db.execute(
            "SELECT session_id, created_at, "
            "json_extract(metadata,'$.num_tokens_preceding') "
            "FROM message_nodes WHERE created_at>? "
            "AND json_extract(metadata,'$.num_tokens_preceding') IS NOT NULL "
            "ORDER BY created_at", (time.time() - 8 * 86400,)).fetchall()
        meta = {r[0]: (r[1], r[2])
                for r in db.execute("SELECT id,title,model FROM sessions")}
        db.close()
        # Quota is billed per token; SWE-2 is free on Desktop/CLI through
        # 2026-11-30 (SWE-1.x still bills, just discounted).
        billed = [r for r in rows
                  if not (meta.get(r[0]) or (None, ""))[1].startswith("swe-2")]
        _tok_cache.update(at=time.time(), data={
            "rows": rows, "billed": billed,
            "titles": {k: v[0] for k, v in meta.items()},
            "models": {k: v[1] for k, v in meta.items()}})
    except Exception:
        pass
    return _tok_cache["data"]


_POLL_LOCK = threading.Lock()
_LAST_REQ = 0.0  # last /api/state request time — "someone is watching"


def _poll_bg():
    try:
        poll(verbose=False)
    finally:
        _POLL_LOCK.release()


def _last_ok_ts(hist):
    return max((h["ts"] for h in hist
                if h.get("quota", {}).get("weekly_percentage") is not None), default=0)


def _scheduler():
    # keeps history.jsonl growing even when nobody has the page open:
    # 60s cadence while a dashboard tab fetched recently, else 5min
    while True:
        time.sleep(30)
        try:
            interval = POLL_ACTIVE if time.time() - _LAST_REQ < 600 else POLL_IDLE
            if time.time() - _last_ok_ts(load_history()) > interval \
                    and _POLL_LOCK.acquire(blocking=False):
                threading.Thread(target=_poll_bg, daemon=True).start()
        except Exception:
            pass


def state(force=False):
    hist = load_history()
    # failed polls (e.g. network not up yet after reboot) have quota=None —
    # skip them for "latest"/staleness so the page never renders a NaN state
    ok = [h for h in hist if h.get("quota", {}).get("weekly_percentage") is not None]
    global _LAST_REQ
    _LAST_REQ = time.time()
    # adaptive: poll fast while % changed within the last hour, slow when flat
    pcts = [h["quota"]["weekly_percentage"] for h in ok
            if h["ts"] > time.time() - 3600]
    interval = POLL_ACTIVE if len(set(pcts)) > 1 else POLL_IDLE
    stale = not ok or (time.time() - ok[-1]["ts"]) > interval
    if (force or stale) and _POLL_LOCK.acquire(blocking=False):
        # poll in background so the HTTP response is instant; next fetch picks it up
        threading.Thread(target=_poll_bg, daemon=True).start()
    # slim wire format: history rows carry only ts + weekly pct (raw snapshots
    # stay in history.jsonl on disk); keeps /api/state small enough for mobile
    slim = [{"ts": h["ts"], "p": h["quota"]["weekly_percentage"]} for h in ok]
    return {"latest": ok[-1] if ok else {}, "history": slim,
            "prediction": predict(hist), "tokens": load_tokens()}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        try:
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
        except (BrokenPipeError, ConnectionResetError):
            pass  # client (e.g. Safari refresh) aborted mid-write


def serve(port=8377):
    poll(verbose=True)  # seed
    threading.Thread(target=_scheduler, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"dashboard: http://localhost:{port}  (adaptive poll {POLL_ACTIVE}s active / {POLL_IDLE}s idle)")
    srv.serve_forever()


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "poll":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 1
        for _ in range(n):
            poll()
            if _ < n - 1:
                time.sleep(POLL_ACTIVE)
    elif len(sys.argv) > 1 and sys.argv[1] == "serve":
        serve(int(sys.argv[2]) if len(sys.argv) > 2 else 8377)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
