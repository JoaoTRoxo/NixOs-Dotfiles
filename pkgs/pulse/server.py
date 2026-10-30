"""Pulse: aurea's daily dashboard, at http://aurea:8484 on the tailnet.

It collects how everything is going (sources.py) on a schedule per source,
keeps the laptop's reports (agent.py pushes them), turns it all into a list
of what needs attention (attention.py), and has Claude write a morning brief
and answer questions about it (ai.py). The page is web/.

Config: the JSON file in $PULSE_CONFIG (written by profiles/pulse.nix).
Credentials come from systemd's LoadCredential ($CREDENTIALS_DIRECTORY):
sonarr, radarr, seerr-settings, radicale, and optionally jellyfin (an API
key), github (a token; otherwise the laptop reports GitHub) and anthropic
(an API key; otherwise the laptop writes the brief). State (history, the
briefs, the laptop's last report) is in $STATE_DIRECTORY/pulse.db.

Endpoints: GET /api/snapshot; POST /api/push/laptop (the agent's report);
GET /api/brief/request + POST /api/brief (the agent writing the brief);
POST /api/brief/generate; POST /api/ask (server-sent events); POST
/api/refresh.
"""

import datetime as dt
import json
import os
import sqlite3
import threading
import time
import traceback
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

import ai
import attention
import sources

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/app.css": ("app.css", "text/css; charset=utf-8"),
          "/icon.svg": ("icon.svg", "image/svg+xml")}


def log(*parts):
    print(time.strftime("%H:%M:%S"), *parts, flush=True)


class Store:
    """SQLite: the laptop's last report, one brief per day, and the last
    snapshot of each day (for "since yesterday")."""

    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock, self.db:
            self.db.executescript("""
                create table if not exists pushes (
                    source text primary key, received real, body text);
                create table if not exists briefs (
                    day text primary key, created real, source text,
                    model text, body text);
                create table if not exists days (
                    day text primary key, saved real, body text);
            """)

    def put(self, sql, *args):
        with self.lock, self.db:
            self.db.execute(sql, args)

    def get(self, sql, *args):
        with self.lock:
            return self.db.execute(sql, args).fetchone()

    def push(self, source, body):
        self.put("insert or replace into pushes values (?, ?, ?)", source,
                 time.time(), json.dumps(body))

    def pushed(self, source):
        row = self.get("select received, body from pushes where source = ?",
                       source)
        return (row[0], json.loads(row[1])) if row else (None, None)

    def brief(self, day):
        row = self.get("select created, source, model, body from briefs "
                       "where day = ?", day)
        if not row:
            return None
        return {"day": day, "created": row[0], "source": row[1],
                "model": row[2], "text": row[3]}

    def save_brief(self, day, source, model, text):
        self.put("insert or replace into briefs values (?, ?, ?, ?, ?)",
                 day, time.time(), source, model, text)

    def save_day(self, day, body):
        self.put("insert or replace into days values (?, ?, ?)", day,
                 time.time(), json.dumps(body))
        cutoff = (dt.date.fromisoformat(day) - dt.timedelta(days=120))
        self.put("delete from days where day < ?", cutoff.isoformat())

    def day(self, day):
        row = self.get("select body from days where day = ?", day)
        return json.loads(row[0]) if row else None


class Collector(threading.Thread):
    """Runs one source every `every` seconds (sooner when woken)."""

    def __init__(self, name, function, every, cfg):
        super().__init__(name=name, daemon=True)
        self.function, self.every, self.cfg = function, every, cfg
        self.state = {"ok": False, "data": None, "error": None,
                      "updated": None, "pending": True}
        self.wake = threading.Event()

    def run(self):
        while True:
            start = time.monotonic()
            try:
                data = self.function(self.cfg)
                self.state = {"ok": True, "data": data, "error": None,
                              "updated": time.time()}
            except Exception as e:
                log(self.name, "failed:", sources.describe(e))
                self.state = {**self.state, "ok": False, "pending": False,
                              "error": sources.describe(e),
                              "failed": time.time()}
            self.state["took"] = round(time.monotonic() - start, 2)
            self.wake.wait(self.every)
            self.wake.clear()


class Pulse:
    def __init__(self, cfg, store):
        self.cfg, self.store = cfg, store
        self.zone = ZoneInfo(cfg.get("timezone", "Europe/Lisbon"))
        every = {"machine": 60, "units": 60, "reach": 300, "tailnet": 300,
                 "recordings": 60, "gaps": 1800, "media": 300,
                 "calendar": 600, "github": 900, **cfg.get("every", {})}
        functions = {"machine": sources.machine, "units": sources.units,
                     "reach": sources.reach, "tailnet": sources.tailnet}
        if "jrxd" in cfg:
            functions["recordings"] = sources.recordings
            functions["gaps"] = sources.RecordingGaps()
        if any(k in cfg for k in ("sonarr", "radarr", "seerr", "jellyfin")):
            functions["media"] = sources.media
        if "calendar" in cfg:
            functions["calendar"] = sources.calendar
        token = sources.credential("github")
        if token:
            functions["github"] = lambda cfg: github_with_token(token)
        self.collectors = {name: Collector(name, f, every[name], cfg)
                           for name, f in functions.items()}
        key = sources.credential(cfg.get("ai", {}).get("key_credential",
                                                       "anthropic"))
        self.claude = None
        if key:
            settings = cfg.get("ai", {})
            self.claude = ai.Claude(key, settings.get("model",
                                                      "claude-opus-5-5"),
                                    settings.get("effort", "medium"))
        self.conversations = {}
        self.writing = threading.Lock()

    def start(self):
        for collector in self.collectors.values():
            collector.start()
        threading.Thread(target=self.keep, daemon=True).start()

    def now(self):
        return dt.datetime.now(self.zone)

    def sections(self):
        out = {name: dict(c.state) for name, c in self.collectors.items()}
        received, report = self.store.pushed("laptop")
        report = report or {}
        errors = report.get("errors", {})
        for name in ("laptop", "repos", "github"):
            if name == "github" and "github" in self.collectors:
                continue
            out[name] = {"ok": name in report, "data": report.get(name),
                         "error": errors.get(name), "updated": received,
                         "via": "laptop"}
            if not received:
                out[name]["error"] = None
        return out

    def snapshot(self):
        sections = self.sections()
        now = self.now()
        yesterday = self.store.day((now.date() - dt.timedelta(days=1))
                                   .isoformat())
        found = attention.items(
            sections, (yesterday or {}).get("sections"), time.time(),
            self.cfg)
        return {
            "generated": time.time(), "now": now.isoformat(),
            "name": self.cfg.get("name"), "status": attention.overall(found),
            "attention": found, "sections": sections,
            "brief": self.store.brief(now.date().isoformat()),
            "ai": {"server": bool(self.claude),
                   "model": self.claude.model if self.claude else None},
            "links": self.cfg.get("links", []),
        }

    def keep(self):
        """Every 10 minutes: save today's snapshot for tomorrow's
        comparison, and write the brief when it's due."""
        time.sleep(90)  # let the collectors finish their first round
        while True:
            try:
                snap = self.snapshot()
                self.store.save_day(self.now().date().isoformat(),
                                    ai.compact(snap))
                if self.claude and self.brief_due()[0]:
                    self.write_brief()
            except Exception:
                traceback.print_exc()
            time.sleep(600)

    def brief_due(self, force=False):
        now = self.now()
        if not force and self.store.brief(now.date().isoformat()):
            return False, "there's already a brief today"
        if not force and now.hour < self.cfg.get("ai", {}).get(
                "brief_after", 6):
            return False, "too early"
        return True, None

    def brief_prompt(self):
        now = self.now()
        yesterday = self.store.day((now.date() - dt.timedelta(days=1))
                                   .isoformat())
        return ai.brief_prompt(self.snapshot(), yesterday, self.cfg, now)

    def write_brief(self):
        with self.writing:
            system, user = self.brief_prompt()
            text, model = self.claude.brief(system, user)
            self.store.save_brief(self.now().date().isoformat(),
                                  "aurea (Claude API)", model, text)
            log("brief written by", model)
            return self.store.brief(self.now().date().isoformat())

    def conversation(self, cid):
        """The system prompt a conversation started with: the snapshot
        doesn't change under it, so the API's cache keeps working."""
        now = time.time()
        for old in [k for k, (t, _) in self.conversations.items()
                    if now - t > 7200]:
            del self.conversations[old]
        if cid not in self.conversations:
            yesterday = self.store.day((self.now().date()
                                        - dt.timedelta(days=1)).isoformat())
            self.conversations[cid] = (now, ai.ask_system(
                self.snapshot(), yesterday, self.cfg, self.now()))
        return self.conversations[cid][1]


def github_with_token(token):
    import agent

    def graphql(query, variables):
        reply = sources.fetch("https://api.github.com/graphql",
                              {"Authorization": f"bearer {token}"},
                              {"query": query, "variables": variables})
        if reply.get("errors"):
            raise RuntimeError("; ".join(e.get("message", "?")
                                         for e in reply["errors"]))
        return reply["data"]

    def notifications():
        return sources.fetch("https://api.github.com/notifications"
                             "?per_page=50",
                             {"Authorization": f"bearer {token}"})

    return agent.github(graphql, notifications)


class Handler(BaseHTTPRequestHandler):
    pulse = None
    server_version = "pulse"

    def log_message(self, fmt, *args):
        if not self.path.startswith("/api/snapshot"):
            log(self.address_string(), fmt % args)

    def allowed(self):
        """Only the names it's served under, so a page elsewhere can't read
        it by pointing its own domain at aurea (DNS rebinding); and no
        cross-site posts (they'd spend the API key)."""
        hosts = self.pulse.cfg.get("hosts")
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        if hosts and host not in hosts:
            return False
        if self.command == "POST":
            origin = self.headers.get("Origin")
            if origin and urllib.parse.urlsplit(origin).hostname != host:
                return False
            if not (self.headers.get("Content-Type") or "").startswith(
                    "application/json"):
                return False
        return True

    def send(self, code, body, kind="application/json"):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > 4_000_000:
            raise ValueError("too big")
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        if not self.allowed():
            return self.send(403, {"error": "forbidden"})
        url = urllib.parse.urlsplit(self.path)
        if url.path in STATIC:
            name, kind = STATIC[url.path]
            with open(os.path.join(WEB, name), "rb") as f:
                return self.send(200, f.read(), kind)
        if url.path == "/api/snapshot":
            return self.send(200, self.pulse.snapshot())
        if url.path == "/api/brief/request":
            force = "force=1" in url.query
            if self.pulse.claude and not force:
                return self.send(200, {"needed": False, "reason":
                                       "aurea writes it with its API key"})
            due, reason = self.pulse.brief_due(force)
            if not due:
                return self.send(200, {"needed": False, "reason": reason})
            system, user = self.pulse.brief_prompt()
            return self.send(200, {"needed": True, "system": system,
                                   "prompt": user,
                                   "day": self.pulse.now().date()
                                   .isoformat()})
        if url.path == "/healthz":
            return self.send(200, {"ok": True})
        self.send(404, {"error": "not found"})

    def do_POST(self):
        if not self.allowed():
            return self.send(403, {"error": "forbidden"})
        try:
            body = self.body()
        except ValueError:
            return self.send(400, {"error": "bad JSON"})
        path = urllib.parse.urlsplit(self.path).path
        p = self.pulse
        if path == "/api/push/laptop":
            p.store.push("laptop", body)
            return self.send(200, {"ok": True})
        if path == "/api/brief":
            if not (body.get("text") or "").strip():
                return self.send(400, {"error": "no text"})
            p.store.save_brief(body.get("day") or p.now().date().isoformat(),
                               body.get("source", "laptop"),
                               body.get("model"), body["text"].strip())
            return self.send(200, {"ok": True})
        if path == "/api/refresh":
            for collector in p.collectors.values():
                if collector.name != "gaps":
                    collector.wake.set()
            return self.send(200, {"ok": True})
        if path == "/api/brief/generate":
            if not p.claude:
                return self.send(409, {"error": "no API key on aurea; the "
                                       "laptop agent writes the brief"})
            try:
                return self.send(200, p.write_brief())
            except Exception as e:
                return self.send(502, {"error": sources.describe(e)})
        if path == "/api/ask":
            return self.ask(body)
        self.send(404, {"error": "not found"})

    def ask(self, body):
        p = self.pulse
        if not p.claude:
            return self.send(409, {"error": "Questions need an API key on "
                                   "aurea (see profiles/pulse.nix)."})
        messages = [m for m in body.get("messages", [])
                    if m.get("role") in ("user", "assistant")
                    and isinstance(m.get("content"), str)][-20:]
        if not messages or messages[-1]["role"] != "user":
            return self.send(400, {"error": "no question"})
        cid = str(body.get("conversation") or uuid.uuid4())
        system = p.conversation(cid)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        def event(data):
            self.wfile.write(f"data: {json.dumps(data)}\n\n".encode())
            self.wfile.flush()

        try:
            for text in p.claude.ask(system, messages):
                event({"text": text})
            event({"done": True})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            log("ask failed:", sources.describe(e))
            try:
                event({"error": sources.describe(e)})
            except OSError:
                pass


def main():
    with open(os.environ.get("PULSE_CONFIG", "config.json")) as f:
        cfg = json.load(f)
    state = os.environ.get("STATE_DIRECTORY", ".")
    pulse = Pulse(cfg, Store(os.path.join(state, "pulse.db")))
    pulse.start()
    Handler.pulse = pulse
    host, _, port = cfg.get("listen", "127.0.0.1:8484").rpartition(":")
    server = ThreadingHTTPServer((host or "127.0.0.1", int(port)), Handler)
    server.daemon_threads = True
    log(f"pulse on http://{host}:{port}/",
        "with" if pulse.claude else "without", "an API key")
    server.serve_forever()


if __name__ == "__main__":
    main()
