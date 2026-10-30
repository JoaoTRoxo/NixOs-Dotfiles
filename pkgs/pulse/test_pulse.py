"""Tests for Pulse, against stand-ins on localhost (nothing real is
touched): python3 pkgs/pulse/test_pulse.py

The Claude API test needs the anthropic package and is skipped without it:
uv run --with anthropic python3 pkgs/pulse/test_pulse.py
"""

import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

# No __pycache__ next to the code in the repo
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agent  # noqa: E402
import ai  # noqa: E402
import attention  # noqa: E402
import server  # noqa: E402
import sources  # noqa: E402

LISBON = ZoneInfo("Europe/Lisbon")

JRXD_LIST = """\
binance-spot:BTCUSDT depth-sbe
  2026-10-29T02:34:32.487258958Z → 2026-10-29T16:03:07.040926524Z  \
2007811 messages, 15 snapshots  121.8 MB  stopped (shutdown)  \
binance-spot/BTCUSDT/depth-sbe/2026-10-29/023432.jrxraw.zst
  2026-10-29T16:03:14.313666181Z → 2026-10-29T16:22:12.742131408Z  \
52791 messages, 1 snapshot  4.6 MB  open or interrupted  \
binance-spot/BTCUSDT/depth-sbe/2026-10-29/160314.jrxraw.zst
  2 gaps, 9.052 s missing
    gap 2026-10-29T00:00:00.000000000Z → 2026-10-29T00:00:01.000000000Z  \
1.000 s  starting
    gap 2026-10-29T16:03:07.040926524Z → 2026-10-29T16:03:14.313666181Z  \
8.052 s  shutdown
hyperliquid-mainnet:BTC trades
  2026-10-29T02:34:32.485726466Z → 2026-10-29T16:03:07.876427994Z  \
61327 messages  8.4 MB  rotated at midnight  \
hyperliquid-mainnet/BTC/trades/2026-10-29/023432.jrxraw.zst
  3 gaps, 2.5 min missing
    gap a → b  1.744 s  read failed: IO error: Connection reset by peer
    gap a → b  1.769 s  closed by venue: 1000 Expired
    gap a → b  1.775 s  closed by venue: 1000 Expired
"""


class JrxdList(unittest.TestCase):
    def test_streams(self):
        streams = sources.parse_jrxd_list(JRXD_LIST)
        depth = streams["binance-spot:BTCUSDT depth-sbe"]
        self.assertEqual(depth["files"], 2)
        self.assertEqual(depth["messages"], 2007811 + 52791)
        self.assertEqual(depth["snapshots"], 16)
        self.assertEqual(depth["gaps"], 2)
        self.assertAlmostEqual(depth["missing"], 9.052)
        self.assertTrue(depth["open"])
        self.assertEqual(depth["reasons"], {"starting": 1, "shutdown": 1})
        trades = streams["hyperliquid-mainnet:BTC trades"]
        self.assertFalse(trades["open"])
        self.assertAlmostEqual(trades["missing"], 150)
        self.assertEqual(trades["reasons"],
                         {"read failed": 1, "closed by venue": 2})

    def test_nothing_recorded(self):
        self.assertEqual(sources.parse_jrxd_list(
            "no capture files under /srv/jrx/raw\n"), {})


ICS = """BEGIN:VCALENDAR\r
BEGIN:VEVENT\r
UID:1\r
DTSTART;TZID=Europe/Lisbon:20261030T100000\r
DTEND;TZID=Europe/Lisbon:20261030T113000\r
SUMMARY:Análise Matemática\\, aula\r
LOCATION:Pavilhão \r
 Central\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:2\r
DTSTART:20261030T150000Z\r
SUMMARY:Utc thing\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:3\r
DTSTART;VALUE=DATE:20261031\r
SUMMARY:Holiday\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:4\r
DTSTART:20261030T150000Z\r
STATUS:CANCELLED\r
SUMMARY:Gone\r
END:VEVENT\r
END:VCALENDAR\r
"""


class Calendar(unittest.TestCase):
    def test_events(self):
        events = sources.parse_events(ICS, LISBON)
        self.assertEqual([e["title"] for e in events],
                         ["Análise Matemática, aula", "Utc thing",
                          "Holiday"])
        lesson, utc, holiday = events
        self.assertEqual(lesson["location"], "Pavilhão Central")
        self.assertEqual(lesson["start"].isoformat(),
                         "2026-10-30T10:00:00+00:00")
        self.assertEqual(lesson["end"] - lesson["start"],
                         dt.timedelta(minutes=90))
        self.assertEqual(utc["start"].tzinfo, dt.timezone.utc)
        self.assertEqual(utc["end"], utc["start"])
        self.assertTrue(holiday["all_day"])
        self.assertEqual(holiday["end"] - holiday["start"],
                         dt.timedelta(days=1))

    def test_caldav(self):
        """A REPORT per calendar, as the bridge's user, read back from the
        multistatus."""
        seen = []
        today = dt.datetime.now(LISBON).replace(hour=9, minute=0, second=0,
                                                microsecond=0)
        stamp = today.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        ics = ("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:x\nDTSTART:" + stamp +
               "\nSUMMARY:Exam & co\nEND:VEVENT\nEND:VCALENDAR\n")
        reply = f"""<?xml version="1.0"?>
<multistatus xmlns="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
<response><href>/roxo/x/1.ics</href><propstat><prop>
<C:calendar-data>{ics.replace("&", "&amp;")}</C:calendar-data>
</prop></propstat></response></multistatus>"""

        class Radicale(BaseHTTPRequestHandler):
            def do_REPORT(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                seen.append((self.path, self.headers["Authorization"],
                             self.headers["Depth"], body))
                data = reply.encode()
                self.send_response(207)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        with stand_in(Radicale) as url, credentials(radicale="secret"):
            cfg = {"timezone": "Europe/Lisbon", "calendar": {
                "url": url + "/roxo/", "user": "roxo", "calendars": {
                    "exams": {"label": "Evaluations",
                              "kind": "evaluation"}}}}
            data = sources.calendar(cfg)
        path, auth, depth, body = seen[0]
        self.assertEqual(path, "/roxo/exams/")
        self.assertEqual(auth, "Basic cm94bzpzZWNyZXQ=")
        self.assertEqual(depth, "1")
        self.assertIn(b"<C:expand", body)
        [event] = data["events"]
        self.assertEqual(event["title"], "Exam & co")
        self.assertEqual(event["kind"], "evaluation")
        self.assertEqual(event["start"], today.isoformat())


class Systemctl(unittest.TestCase):
    def test_show(self):
        text = ("Id=a.service\nActiveState=active\n"
                "ActiveEnterTimestamp=@1793244603\n\n"
                "Id=b.service\nActiveState=failed\nActiveEnterTimestamp=\n")
        original = sources.run
        sources.run = lambda *a, **k: text
        try:
            a, b = sources.systemctl_show(["a.service", "b.service"],
                                          ["Id"])
        finally:
            sources.run = original
        self.assertEqual(a["ActiveEnterTimestamp"], 1793244603)
        self.assertEqual(b["ActiveState"], "failed")
        self.assertEqual(b["ActiveEnterTimestamp"], "")


def ok(data, **extra):
    return {"ok": True, "data": data, "error": None, "updated": NOW,
            **extra}


NOW = 1793300000.0


def machine(used=0.5, crc=15, mounted=True):
    return {"disks": [{"name": "System disk", "path": "/",
                       "mounted": mounted, "used": used, "free": 50e9}],
            "memory": {"now": 0.3}, "temperature": {"now": 50},
            "smart": [{"name": "system", "device": "sdb", "passed": True,
                       "counters": {"199": {"name": "UDMA_CRC_Error_Count",
                                            "value": crc}}}]}


class Attention(unittest.TestCase):
    def titles(self, sections, yesterday=None, level=None):
        found = attention.items(sections, yesterday, NOW, {})
        return [i["title"] for i in found
                if level is None or i["level"] == level]

    def test_quiet(self):
        self.assertEqual(self.titles({"machine": ok(machine())}), [])
        self.assertEqual(attention.overall([]), "ok")

    def test_disks(self):
        self.assertEqual(self.titles({"machine": ok(machine(0.95))}, None,
                                     "bad"), ["System disk is 95% full"])
        self.assertEqual(self.titles({"machine": ok(machine(0.88))}, None,
                                     "warn"), ["System disk is 88% full"])
        self.assertEqual(self.titles({"machine": ok(machine(
            mounted=False))}), ["System disk isn't mounted"])

    def test_smart_counters_only_when_they_grow(self):
        yesterday = {"machine": machine(crc=15)}
        self.assertEqual(self.titles({"machine": ok(machine(crc=15))},
                                     yesterday), [])
        self.assertEqual(
            self.titles({"machine": ok(machine(crc=17))}, yesterday),
            ["system disk: UDMA_CRC_Error_Count went 15 → 17"])

    def test_failed_unit_reported_once(self):
        units = {"services": [], "jobs": [], "failed": ["x.service"],
                 "alerts": [{"key": "failed-x.service", "since": NOW,
                             "message": "❌ aurea: x.service failed\nlog"}]}
        self.assertEqual(self.titles({"units": ok(units)}),
                         ["aurea: x.service failed"])

    def test_jobs(self):
        units = {"services": [], "failed": [], "alerts": [], "jobs": [
            {"name": "Backup", "unit": "b", "result": "exit-code",
             "running": False, "finished": NOW - 60, "max_age_hours": 36},
            {"name": "Sync", "unit": "s", "result": None, "running": False,
             "triggered": NOW - 7200, "max_age_hours": 1}]}
        self.assertEqual(self.titles({"units": ok(units)}),
                         ["Backup failed its last run",
                          "Sync hasn't run in 2 h"])

    def test_recorder(self):
        rec = {"active": "active", "streams": [
            {"id": "a", "idle": 3}, {"id": "b", "idle": 900}]}
        gaps = {"today": {"streams": {"a": {"missing": 500.0}}}}
        self.assertEqual(
            self.titles({"recordings": ok(rec), "gaps": ok(gaps)}),
            ["1 stream stopped writing",
             "Recording gaps today: 1 stream missed over 120 s"])

    def test_projects(self):
        github = {"repos": [{"name": "me/x", "ci": "FAILURE",
                             "branch": "main", "url": "u", "prs": [
                                 {"bot": True}, {"bot": False}]}],
                  "my_prs": [], "review_requests": [], "assigned": []}
        repos = [{"name": "x", "ahead": 2, "changed": 0}]
        self.assertEqual(
            self.titles({"github": ok(github), "repos": ok(repos)}),
            ["CI is failing on me/x (main)",
             "1 pull request from bots waiting",
             "Unpushed commits in 1 repo"])

    def test_evaluations(self):
        tomorrow = (dt.datetime.fromtimestamp(NOW) + dt.timedelta(days=1))
        events = [{"kind": "evaluation", "title": "Exam",
                   "label": "Evaluations", "start": tomorrow.isoformat()},
                  {"kind": None, "title": "Lunch", "label": "Home",
                   "start": tomorrow.isoformat()}]
        found = attention.items({"calendar": ok({"events": events})}, None,
                                NOW, {})
        self.assertEqual([(i["level"], i["title"]) for i in found],
                         [("warn", "Exam tomorrow")])

    def test_stale_laptop_and_errors(self):
        sections = {"laptop": ok({"failed": []}, updated=NOW - 20 * 3600),
                    "media": {"ok": False, "error": "HTTP 500"}}
        self.assertEqual(self.titles(sections),
                         ["Couldn't read media",
                          "The laptop last reported 20 h ago"])


class Agent(unittest.TestCase):
    def test_remote_name(self):
        for url in ("git@github.com:JoaoTRoxo/jrx.git",
                    "https://github.com/JoaoTRoxo/jrx",
                    "https://github.com/JoaoTRoxo/jrx.git/"):
            self.assertEqual(agent.remote_name(url), "JoaoTRoxo/jrx")
        self.assertIsNone(agent.remote_name("git@gitea.local:a/b.git"))

    def test_repos(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "proj")
            os.mkdir(path)

            def git(*args):
                subprocess.run(["git", "-c", "user.name=t",
                                "-c", "user.email=t@t", *args], cwd=path,
                               check=True, capture_output=True)

            def write(name, text):
                with open(os.path.join(path, name), "w") as f:
                    f.write(text)
            git("init", "-q", "-b", "main")
            write("a", "1")
            git("add", "a")
            git("commit", "-qm", "first")
            write("a", "2")
            write("b", "new")
            os.mkdir(os.path.join(root, "not-a-repo"))
            [state] = agent.repos([root])
        self.assertEqual(state["name"], "proj")
        self.assertEqual(state["branch"], "main")
        self.assertEqual((state["changed"], state["untracked"]), (2, 1))
        self.assertEqual(state["last_commit"]["subject"], "first")
        self.assertIsNone(state["remote"])
        self.assertEqual(state["ahead"], 0)

    def test_github(self):
        def repo(name, owner, pushed, archived=False):
            return {"nameWithOwner": name, "url": "u", "isPrivate": False,
                    "isArchived": archived, "isFork": False,
                    "pushedAt": pushed, "owner": {"login": owner},
                    "description": "", "primaryLanguage": {"name": "Rust"},
                    "defaultBranchRef": {"name": "main", "target": {
                        "committedDate": pushed, "messageHeadline": "x",
                        "statusCheckRollup": {"state": "SUCCESS"}}},
                    "pullRequests": {"totalCount": 1, "nodes": [pr(name)]},
                    "issues": {"totalCount": 2},
                    "milestones": {"nodes": [{
                        "title": "P0", "url": "m", "dueOn": None,
                        "progressPercentage": 50.0,
                        "open": {"totalCount": 1},
                        "closed": {"totalCount": 1}}]},
                    "latestRelease": None}

        def pr(name):
            return {"number": 1, "title": "bump", "url": "p",
                    "isDraft": False, "createdAt": "2026-10-01T00:00:00Z",
                    "updatedAt": "2026-10-02T00:00:00Z",
                    "reviewDecision": None,
                    "author": {"login": "dependabot", "__typename": "Bot"},
                    "repository": {"nameWithOwner": name},
                    "commits": {"nodes": [{"commit": {
                        "statusCheckRollup": None}}]}}

        recent = "2026-10-28T00:00:00Z"
        old = "2025-01-01T00:00:00Z"
        viewer = {
            "login": "me", "organizations": {"nodes": [{"login": "club"}]},
            "repositories": {"nodes": [repo("me/a", "me", recent),
                                       repo("me/gone", "me", recent, True)]},
            "repositoriesContributedTo": {"nodes": [
                repo("club/b", "club", old),
                repo("friend/c", "friend", recent),
                repo("friend/d", "friend", old)]}}
        data = {"viewer": viewer, "mine": {"nodes": [pr("club/b")]},
                "review": {"nodes": []}, "assigned": {"nodes": []}}
        out = agent.github(lambda q, v: data, lambda: [], now=NOW)
        self.assertEqual([r["name"] for r in out["repos"]],
                         ["me/a", "club/b", "friend/c"])
        a = out["repos"][0]
        self.assertEqual((a["ci"], a["issues"], a["prs"][0]["bot"]),
                         ("SUCCESS", 2, True))
        self.assertEqual(a["milestones"][0]["progress"], 50.0)
        self.assertEqual(out["notifications"]["unread"], 0)


class Compact(unittest.TestCase):
    def test_drops_series(self):
        snap = {"generated": NOW, "attention": [], "sections": {
            "machine": ok({"cpu": {"now": 0.123456, "series": [[1, 2]]}}),
            "media": {"ok": False, "error": "down", "updated": None}}}
        out = ai.compact(snap)
        self.assertEqual(out["sections"]["machine"], {"cpu": {"now": 0.123}})
        self.assertEqual(out["sections"]["media"], {"error": "down"})

    def test_prompt(self):
        system, user = ai.brief_prompt({"sections": {}}, None,
                                       {"name": "João"},
                                       dt.datetime(2026, 10, 30, 7,
                                                   tzinfo=LISBON))
        self.assertIn("João", system)
        self.assertIn("Friday 30 October 2026", user)
        self.assertIn("<yesterday>\n(none)", user)


class Server(unittest.TestCase):
    """The HTTP API, with the collectors left unstarted."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        cfg = {"name": "João", "hosts": ["127.0.0.1"],
               "timezone": "Europe/Lisbon",
               "ai": {"brief_after": 0}}
        pulse = server.Pulse(cfg, server.Store(os.path.join(
            cls.dir.name, "pulse.db")))
        server.Handler.pulse = pulse
        cls.pulse = pulse
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.url = f"http://127.0.0.1:{cls.httpd.server_port}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.pulse.store.db.close()
        cls.dir.cleanup()

    def call(self, path, body=None, headers=None):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.url + path, data=data, headers={
            **({"Content-Type": "application/json"} if data else {}),
            **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=10) as reply:
                return reply.status, json.loads(reply.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read())

    def test_page(self):
        with urllib.request.urlopen(self.url + "/", timeout=10) as reply:
            self.assertIn(b"<title>Pulse</title>", reply.read())

    def test_other_hosts_refused(self):
        self.assertEqual(self.call("/api/snapshot",
                                   headers={"Host": "evil.example"})[0], 403)

    def test_cross_site_posts_refused(self):
        self.assertEqual(self.call("/api/push/laptop", {}, {
            "Origin": "https://evil.example"})[0], 403)
        request = urllib.request.Request(
            self.url + "/api/push/laptop", data=b"{}",
            headers={"Content-Type": "text/plain"})
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(request, timeout=10)
        self.assertEqual(e.exception.code, 403)
        e.exception.close()

    def test_laptop_report_and_brief(self):
        report = {"laptop": {"host": "archlinux", "failed": []},
                  "repos": [{"name": "x", "ahead": 1, "changed": 0}],
                  "github": {"repos": [], "my_prs": [],
                             "review_requests": [], "assigned": []},
                  "errors": {}}
        self.assertEqual(self.call("/api/push/laptop", report)[0], 200)
        code, snap = self.call("/api/snapshot")
        self.assertEqual(code, 200)
        self.assertEqual(snap["sections"]["laptop"]["data"]["host"],
                         "archlinux")
        self.assertEqual(snap["sections"]["github"]["via"], "laptop")
        self.assertIn("Unpushed commits in 1 repo",
                      [i["title"] for i in snap["attention"]])

        code, wanted = self.call("/api/brief/request?force=1")
        self.assertTrue(wanted["needed"])
        self.assertIn("archlinux", wanted["prompt"])
        self.assertEqual(self.call("/api/brief", {
            "day": wanted["day"], "text": " All quiet. ",
            "model": "m", "source": "laptop"})[0], 200)
        brief = self.call("/api/snapshot")[1]["brief"]
        self.assertEqual((brief["text"], brief["source"]),
                         ("All quiet.", "laptop"))
        self.assertFalse(self.call("/api/brief/request")[1]["needed"])

    def test_asking_needs_a_key(self):
        code, reply = self.call("/api/ask", {"messages": [
            {"role": "user", "content": "how's the disk?"}]})
        self.assertEqual(code, 409)


try:
    import anthropic  # noqa: F401
    HAVE_SDK = True
except ImportError:
    HAVE_SDK = False


@unittest.skipUnless(HAVE_SDK, "needs the anthropic package")
class ClaudeApi(unittest.TestCase):
    """What's sent to the Messages API, against a stand-in for it."""

    def test_brief_and_ask(self):
        seen = []

        class Api(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(
                    int(self.headers["Content-Length"])))
                seen.append((self.path, dict(self.headers), body))
                message = {"id": "msg_1", "type": "message",
                           "role": "assistant", "model": body["model"],
                           "stop_reason": None, "stop_sequence": None,
                           "usage": {"input_tokens": 9, "output_tokens": 1}}
                if not body.get("stream"):
                    data = json.dumps({**message, "stop_reason": "end_turn",
                                       "content": [{"type": "text",
                                                    "text": "Quiet day."}]})
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data.encode())
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for event in (
                        {"type": "message_start",
                         "message": {**message, "content": []}},
                        {"type": "content_block_start", "index": 0,
                         "content_block": {"type": "text", "text": ""}},
                        {"type": "content_block_delta", "index": 0,
                         "delta": {"type": "text_delta", "text": "75% "}},
                        {"type": "content_block_delta", "index": 0,
                         "delta": {"type": "text_delta", "text": "full."}},
                        {"type": "content_block_stop", "index": 0},
                        {"type": "message_delta",
                         "delta": {"stop_reason": "end_turn",
                                   "stop_sequence": None},
                         "usage": {"output_tokens": 3}},
                        {"type": "message_stop"}):
                    self.wfile.write(f"event: {event['type']}\ndata: "
                                     f"{json.dumps(event)}\n\n".encode())
                    self.wfile.flush()

            def log_message(self, *args):
                pass

        with stand_in(Api) as url:
            claude = ai.Claude("sk-test", "claude-opus-5-5", "medium")
            claude.client = claude.client.with_options(base_url=url)
            text, model = claude.brief("system", "user")
            answer = "".join(claude.ask("system", [
                {"role": "user", "content": "disk?"}]))
        self.assertEqual((text, model), ("Quiet day.", "claude-opus-5-5"))
        self.assertEqual(answer, "75% full.")
        for path, headers, body in seen:
            self.assertTrue(path.startswith("/v1/messages"))
            headers = {k.lower(): v for k, v in headers.items()}
            self.assertEqual(headers["x-api-key"], "sk-test")
            self.assertIn("server-side-fallback-2026-07-01",
                          headers["anthropic-beta"])
            self.assertEqual(body["fallbacks"], "default")
            self.assertEqual(body["output_config"], {"effort": "medium"})
            self.assertEqual(body["model"], "claude-opus-5-5")
        self.assertNotIn("cache_control", seen[0][2])
        self.assertEqual(seen[1][2]["cache_control"], {"type": "ephemeral"})


class stand_in:
    """A local HTTP server for a handler class, as a context manager that
    gives its URL."""

    def __init__(self, handler):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)

    def __enter__(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.httpd.server_port}"

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


class credentials:
    """$CREDENTIALS_DIRECTORY with the given files, for the duration."""

    def __init__(self, **files):
        self.files = files

    def __enter__(self):
        self.dir = tempfile.TemporaryDirectory()
        for name, value in self.files.items():
            with open(os.path.join(self.dir.name, name), "w") as f:
                f.write(value + "\n")
        self.old = os.environ.get("CREDENTIALS_DIRECTORY")
        os.environ["CREDENTIALS_DIRECTORY"] = self.dir.name

    def __exit__(self, *exc):
        if self.old is None:
            del os.environ["CREDENTIALS_DIRECTORY"]
        else:
            os.environ["CREDENTIALS_DIRECTORY"] = self.old
        self.dir.cleanup()


if __name__ == "__main__":
    time.tzset()
    unittest.main()
