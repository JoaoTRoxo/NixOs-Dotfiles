"""Tests for seerr-telegram.py, against stand-ins for Seerr and Telegram on
localhost: python3 pkgs/seerr-telegram-test.py"""

import importlib.util
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# No __pycache__ next to the bot in the repo
sys.dont_write_bytecode = True
here = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "bot", os.path.join(here, "seerr-telegram.py"))
bot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bot)

ADMIN = {"id": 1, "displayName": "aurea"}
# What Seerr's schema takes in a request's update (never null)
UPDATE = {"is4k": (bool, "boolean"), "serverId": (int, "number"),
          "profileId": (int, "number"), "languageProfileId": (int, "number"),
          "rootFolder": (str, "string")}


def request(rid, kind, status, seasons=(), tmdb=100, **fields):
    """A request, left to Seerr's default server, profile and folder unless
    `fields` say otherwise."""
    return {
        "id": rid, "type": kind, "status": status, "is4k": False,
        "serverId": None, "profileId": None, "rootFolder": None,
        "languageProfileId": None, "tags": None, "modifiedBy": None,
        "media": {"tmdbId": tmdb},
        "seasons": [{"seasonNumber": n} for n in seasons],
        "requestedBy": {"id": 5, "displayName": "Nando <3"},
        **fields,
    }


class Fake(BaseHTTPRequestHandler):
    """Seerr under /api/v1, Telegram under /bot<token>/"""

    def log_message(self, *args):
        pass

    def reply(self, code, body=None):
        raw = json.dumps(body).encode() if body is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(raw)

    def body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"null")

    def do_GET(self):
        self.seerr("GET", None)

    def do_PUT(self):
        self.seerr("PUT", self.body())

    def do_POST(self):
        if self.path.startswith("/bottoken/"):
            return self.telegram(self.path[len("/bottoken/"):], self.body())
        self.seerr("POST", None)

    def seerr(self, method, body):
        world = self.server.world
        if self.headers.get("X-Api-Key") != "key":
            return self.reply(401, {"message": "no"})
        path = self.path.removeprefix("/api/v1")
        parts = path.split("?")[0].strip("/").split("/")
        requests = world["requests"]
        if parts == ["request"]:
            newest = sorted(requests.values(), key=lambda r: -r["id"])
            return self.reply(200, {"results": newest[:20]})
        if parts[0] in ("movie", "tv"):
            tmdb = int(parts[1])
            found = {"posterPath": "/p.jpg" if tmdb != 404 else None}
            if parts[0] == "movie":
                found.update(title="Dune & Co", releaseDate="2024-02-27")
            else:
                found.update(name="The Bear", firstAirDate="2022-06-23",
                             numberOfSeasons=6)
            return self.reply(200, found)
        r = requests.get(int(parts[1]))
        if r is None:
            return self.reply(404, {"message": "Request not found"})
        if method == "GET":
            return self.reply(200, r)
        if r["status"] != bot.PENDING:
            return self.reply(409, {"message": "Only pending requests"})
        if method == "PUT":
            for key, (kind, name) in UPDATE.items():
                if key in body and not isinstance(body[key], kind):
                    return self.reply(400, {
                        "message": f"request/body/{key} must be {name}"})
            world["put"].append(body)
            r["seasons"] = [{"seasonNumber": n} for n in body["seasons"]]
            return self.reply(200, r)
        r["status"] = bot.APPROVED if parts[2] == "approve" else bot.DECLINED
        r["modifiedBy"] = ADMIN
        return self.reply(200, r)

    def telegram(self, method, params):
        world = self.server.world
        world["calls"].append((method, params))
        if method == "sendPhoto" and world.get("photo_fails"):
            return self.reply(400, {"ok": False, "description": "bad photo"})
        if method == "getUpdates":
            updates, world["updates"] = world["updates"], []
            return self.reply(200, {"ok": True, "result": updates})
        world["message"] += 1
        return self.reply(
            200, {"ok": True, "result": {"message_id": world["message"]}})


class Test(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        self.world = self.server.world = {
            "requests": {}, "calls": [], "updates": [], "put": [],
            "message": 0}
        threading.Thread(target=self.server.serve_forever,
                         daemon=True).start()
        url = f"http://127.0.0.1:{self.server.server_port}"
        self.dir = tempfile.TemporaryDirectory()
        self.bot = bot.Bot(bot.Seerr(url, "key"), bot.Telegram(url, "token"),
                           -42, "https://requests.example",
                           os.path.join(self.dir.name, "state.json"))
        self.update = 0

    def tearDown(self):
        self.server.shutdown()
        self.dir.cleanup()

    def add(self, *args, **kwargs):
        r = request(*args, **kwargs)
        self.world["requests"][r["id"]] = r
        return r

    def calls(self, method):
        return [p for m, p in self.world["calls"] if m == method]

    def press(self, data, chat=-42, message=1):
        self.update += 1
        self.world["updates"].append({"update_id": self.update,
                                      "callback_query": {
                                          "id": str(self.update),
                                          "data": data,
                                          "message": {
                                              "message_id": message,
                                              "chat": {"id": chat},
                                              "caption": "old"}}})
        self.world["calls"].clear()
        self.bot.listen()

    def test_first_look_sends_only_what_waits(self):
        self.add(1, "movie", bot.APPROVED)
        self.add(2, "tv", bot.PENDING, seasons=range(1, 7))
        self.bot.look()
        sent = self.calls("sendPhoto")
        self.assertEqual(len(sent), 1)
        self.assertIn("<b>Nando &lt;3</b> requested <b>The Bear</b> (2022)",
                      sent[0]["caption"])
        self.assertIn("Seasons 1–6 of 6", sent[0]["caption"])
        keys = sent[0]["reply_markup"]["inline_keyboard"]
        self.assertEqual([b["callback_data"] for b in keys[0]],
                         ["a:2", "d:2"])
        self.assertEqual(keys[1][0]["callback_data"], "p:2:0")
        self.assertEqual(keys[2][0]["url"], "https://requests.example/tv/100")
        # Nothing new: nothing sent
        self.world["calls"].clear()
        self.bot.look()
        self.assertEqual(self.calls("sendPhoto"), [])

    def test_new_request_declined(self):
        self.bot.look()
        self.add(3, "movie", bot.PENDING)
        self.bot.look()
        self.assertIn("Dune &amp; Co</b> (2024)",
                      self.calls("sendPhoto")[0]["caption"])
        self.press("d:3")
        self.assertEqual(self.world["requests"][3]["status"], bot.DECLINED)
        edit = self.calls("editMessageCaption")[0]
        self.assertTrue(edit["caption"].endswith("❌ Declined by aurea"))
        self.assertEqual(edit["reply_markup"]["inline_keyboard"][0][0]["url"],
                         "https://requests.example/movie/100")
        self.assertNotIn("3", self.bot.state["sent"])

    def test_choosing_seasons(self):
        self.bot.look()
        self.add(2, "tv", bot.PENDING, seasons=range(1, 7))
        self.bot.look()
        # The picker starts with none chosen
        self.press("p:2:0")
        picker = self.calls("editMessageReplyMarkup")[0]["reply_markup"]
        rows = picker["inline_keyboard"]
        self.assertEqual(rows[0][0], {"text": "◻️ S1",
                                      "callback_data": "p:2:1"})
        self.assertEqual(rows[-1][0]["text"], "Choose a season")
        self.press("p:2:1")
        rows = self.calls("editMessageReplyMarkup")[0]["reply_markup"][
            "inline_keyboard"]
        self.assertEqual(rows[0][0]["text"], "✅ S1")
        self.assertEqual(rows[0][1]["text"], "◻️ S2")
        self.assertEqual(rows[-1][0], {"text": "✅ Approve 1 season",
                                       "callback_data": "k:2:1"})
        self.press("k:2:1")
        # What Seerr left to its defaults stays out (it refuses null)
        self.assertEqual(self.world["put"], [{
            "mediaType": "tv", "seasons": [1], "is4k": False}])
        self.assertEqual(self.world["requests"][2]["status"], bot.APPROVED)
        self.assertTrue(self.calls("editMessageCaption")[0]["caption"]
                        .endswith("✅ Approved by aurea: season 1"))

    def test_chosen_seasons_keep_the_server(self):
        self.bot.look()
        self.add(2, "tv", bot.PENDING, seasons=[4, 5, 6], serverId=0,
                 profileId=7, rootFolder="/tv", languageProfileId=1,
                 tags=[3])
        self.bot.look()
        self.press("k:2:5")
        self.assertEqual(self.world["put"], [{
            "mediaType": "tv", "seasons": [4, 6], "is4k": False,
            "serverId": 0, "profileId": 7, "rootFolder": "/tv",
            "languageProfileId": 1, "tags": [3]}])
        self.assertTrue(self.calls("editMessageCaption")[0]["caption"]
                        .endswith("✅ Approved by aurea: seasons 4, 6"))

    def test_no_season_chosen(self):
        self.bot.look()
        self.add(2, "tv", bot.PENDING, seasons=[1, 2])
        self.bot.look()
        self.press("k:2:0")
        self.assertEqual(self.calls("answerCallbackQuery")[0]["text"],
                         "Choose a season first")
        self.assertEqual(self.world["requests"][2]["status"], bot.PENDING)

    def test_decided_in_seerr(self):
        self.bot.look()
        r = self.add(4, "movie", bot.PENDING)
        self.bot.look()
        r["status"], r["modifiedBy"] = bot.APPROVED, ADMIN
        self.world["calls"].clear()
        self.bot.look()
        self.assertTrue(self.calls("editMessageCaption")[0]["caption"]
                        .endswith("✅ Approved by aurea"))
        # A button left on it says so, and doesn't act
        self.press("d:4")
        self.assertEqual(r["status"], bot.APPROVED)
        self.assertEqual(self.calls("answerCallbackQuery")[0]["text"],
                         "It was decided in Seerr already")

    def test_other_chats_buttons_do_nothing(self):
        self.bot.look()
        self.add(5, "movie", bot.PENDING)
        self.bot.look()
        self.press("a:5", chat=99)
        self.assertEqual(self.world["requests"][5]["status"], bot.PENDING)

    def test_poster_failing(self):
        self.bot.look()
        self.world["photo_fails"] = True
        self.add(6, "movie", bot.PENDING)
        self.bot.look()
        self.assertEqual(len(self.calls("sendMessage")), 1)
        self.assertFalse(self.bot.state["sent"]["6"]["photo"])

    def test_a_restart_keeps_its_place(self):
        self.add(2, "tv", bot.PENDING, seasons=[1])
        self.bot.look()
        again = bot.Bot(self.bot.seerr, self.bot.telegram, -42, None,
                        self.bot.state_path)
        self.world["calls"].clear()
        again.look()
        self.assertEqual(self.calls("sendPhoto"), [])
        self.assertIn("2", again.state["sent"])


class Pieces(unittest.TestCase):
    def test_ranges(self):
        self.assertEqual(bot.ranges([1, 2, 3, 5, 7, 8]), "1–3, 5, 7–8")
        self.assertEqual(bot.ranges([4]), "4")

    def test_env_file(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write("export TELEGRAM_BOT_TOKEN='1:abc'\n# a comment\n"
                    'TELEGRAM_CHAT_ID="-42"\n')
        try:
            self.assertEqual(bot.read_env_file(f.name), {
                "TELEGRAM_BOT_TOKEN": "1:abc", "TELEGRAM_CHAT_ID": "-42"})
        finally:
            os.unlink(f.name)


if __name__ == "__main__":
    unittest.main()
