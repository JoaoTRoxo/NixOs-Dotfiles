"""Tests for seerr-subtitles.py, against a stand-in for Seerr and Bazarr on
localhost: python3 pkgs/seerr-subtitles-test.py"""

import importlib.util
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# No __pycache__ next to the worker in the repo
sys.dont_write_bytecode = True
here = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "worker", os.path.join(here, "seerr-subtitles.py"))
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)
worker.POLL = 0

DAY = worker.RETRY_AFTER
SHOW = 16
MOVIE = 5
PT = "Requesting subtitles in Portuguese [pt]."


def subtitle(code, forced=False):
    return {"code2": code, "forced": forced, "hi": False, "path": "/x.srt"}


def episode(number, season=1, subtitles=()):
    return {"sonarrEpisodeId": season * 100 + number, "season": season,
            "episode": number, "subtitles": list(subtitles)}


def issue(iid, kind="tv", service=SHOW, season=0, number=0, message=PT,
          issue_type=3):
    return {
        "id": iid, "issueType": issue_type, "status": 1,
        "problemSeason": season, "problemEpisode": number,
        "media": {"mediaType": kind, "externalServiceId": service},
        "comments": [{"message": message}],
    }


class Fake(BaseHTTPRequestHandler):
    """Seerr under /api/v1, Bazarr under /api, as far as they're used."""

    def log_message(self, *args):
        pass

    def reply(self, code, body=None):
        raw = json.dumps(body).encode() if body is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_PATCH(self):
        self.route("PATCH")

    def route(self, method):
        url = urllib.parse.urlsplit(self.path)
        query = dict(urllib.parse.parse_qsl(url.query))
        if url.path.startswith("/api/v1/"):
            self.seerr(method, url.path.removeprefix("/api/v1/").split("/"))
        else:
            self.server.world["calls"].append((method, url.path, query))
            self.bazarr(method, url.path.removeprefix("/api/"), query)

    def seerr(self, method, parts):
        world = self.server.world
        if self.headers.get("X-Api-Key") != "seerr-key":
            return self.reply(403, {"message": "no"})
        if method == "GET":
            found = [i for i in world["issues"].values() if i["status"] == 1]
            return self.reply(200, {"results": sorted(
                found, key=lambda i: -i["id"])})
        iid = int(parts[1])
        length = int(self.headers.get("Content-Length") or 0)
        if parts[2] == "comment":
            message = json.loads(self.rfile.read(length))["message"]
            world["comments"].append((iid, message))
        else:
            world["issues"][iid]["status"] = 2
        return self.reply(200, {})

    def bazarr(self, method, path, query):
        world = self.server.world
        if self.headers.get("X-API-KEY") != "bazarr-key":
            return self.reply(401)
        if method == "PATCH":
            return self.queue(query)
        if method == "POST":
            table = "shows" if path == "series" else "movies"
            key = int(query["seriesid" if path == "series" else "radarrid"])
            profile = query["profileid"]
            world[table][key] = None if profile == "null" else int(profile)
            return self.reply(204)
        if path == "system/jobs":
            jobs = [dict(job) for job in world["jobs"]]
            self.reply(200, {"data": jobs})
            return self.run_jobs()
        if path == "system/languages/profiles":
            return self.reply(200, [{"profileId": 1}, {"profileId": 2}])
        if path == "episodes":
            return self.reply(200, {"data": world["episodes"]})
        if path == "series":
            profile = world["shows"][int(query["seriesid[]"])]
            return self.reply(200, {"data": [{"profileId": profile}]})
        key = int(query["radarrid[]"])
        return self.reply(200, {"data": [{
            "radarrId": key, "profileId": world["movies"][key],
            "subtitles": world["movie_subtitles"]}]})

    def queue(self, query):
        world = self.server.world
        if world.get("refuse"):
            return self.reply(500)
        world["searches"].append(query)
        world["jobs"].append({"job_name": "Searching subtitles",
                              "status": "pending", "query": query})
        return self.reply(204)

    def run_jobs(self):
        """Once they've been seen waiting, as Bazarr gets to them later."""
        world = self.server.world
        for job in world["jobs"]:
            if job["status"] == "pending":
                job["status"] = "completed"
                self.run(job["query"])

    def run(self, query):
        world = self.server.world
        code = query["language"]
        movie = "radarrid" in query
        profile = (world["movies"][int(query["radarrid"])] if movie
                   else world["shows"][int(query["seriesid"])])
        # generate_subtitles reads the profile's options: none, it fails
        if profile is None:
            world["without_profile"] += 1
            return
        if world["quota"] is not None:
            if world["quota"] <= 0:
                return
            world["quota"] -= 1
        if movie:
            if "movie" not in world["unavailable"]:
                world["movie_subtitles"].append(subtitle(code))
            return
        number = int(query["episodeid"])
        if number not in world["unavailable"]:
            video = next(e for e in world["episodes"]
                         if e["sonarrEpisodeId"] == number)
            video["subtitles"].append(subtitle(code))


class Test(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        self.world = self.server.world = {
            "issues": {}, "comments": [], "calls": [], "searches": [],
            "jobs": [], "shows": {SHOW: None}, "movies": {MOVIE: None},
            "episodes": [], "movie_subtitles": [], "unavailable": set(),
            "quota": None, "without_profile": 0}
        threading.Thread(target=self.server.serve_forever,
                         daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.dir = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.dir.name, "state.json")
        self.clock = 1000.0
        self.worker = self.start()

    def tearDown(self):
        self.server.shutdown()
        self.dir.cleanup()

    def start(self):
        made = worker.Worker(worker.Seerr(self.url, "seerr-key"),
                             worker.Bazarr(self.url, "bazarr-key"),
                             self.state)
        made.now = lambda: self.clock
        return made

    def ask(self, *args, **kwargs):
        made = issue(*args, **kwargs)
        self.world["issues"][made["id"]] = made
        return made

    def searched(self):
        return [int(q.get("episodeid", 0)) for q in self.world["searches"]]

    def resolved(self, iid):
        return self.world["issues"][iid]["status"] == 2

    def test_finds_an_episode(self):
        self.world["episodes"] = [episode(1), episode(2)]
        self.ask(1, season=1, number=2)
        self.worker.tick()
        self.assertEqual(self.searched(), [102])
        query = self.world["searches"][0]
        self.assertEqual(
            (query["seriesid"], query["language"], query["forced"],
             query["hi"]), (str(SHOW), "pt", "False", "False"))
        self.assertEqual(self.world["episodes"][1]["subtitles"],
                         [subtitle("pt")])
        self.assertEqual(self.world["episodes"][0]["subtitles"], [])
        self.assertEqual(self.world["comments"],
                         [(1, "Found the Portuguese subtitles.")])
        self.assertTrue(self.resolved(1))

    def test_a_whole_show_only_searches_whats_missing(self):
        self.world["episodes"] = [
            episode(1, subtitles=[subtitle("pt")]),
            # Only the signs: not what was asked for
            episode(2, subtitles=[subtitle("pt", forced=True)]),
            episode(3, subtitles=[subtitle("en")]),
            episode(1, season=2),
        ]
        self.ask(1)
        self.worker.tick()
        self.assertEqual(sorted(self.searched()), [102, 103, 201])
        self.assertTrue(self.resolved(1))

    def test_a_season_is_only_that_season(self):
        self.world["episodes"] = [episode(1), episode(1, season=2),
                                  episode(2, season=2)]
        self.ask(1, season=2)
        self.worker.tick()
        self.assertEqual(sorted(self.searched()), [201, 202])

    def test_finds_a_movie(self):
        self.ask(1, "movie", MOVIE)
        self.worker.tick()
        self.assertEqual(self.world["searches"][0]["radarrid"], str(MOVIE))
        self.assertEqual(self.world["movie_subtitles"], [subtitle("pt")])
        self.assertTrue(self.resolved(1))
        self.assertIsNone(self.world["movies"][MOVIE])

    def test_what_is_already_there_is_only_resolved(self):
        self.world["episodes"] = [episode(1, subtitles=[subtitle("pt")])]
        self.ask(1)
        self.worker.tick()
        self.assertEqual(self.world["searches"], [])
        self.assertEqual(self.world["shows"][SHOW], None)
        self.assertEqual(self.world["comments"], [
            (1, "These Portuguese subtitles were already here.")])
        self.assertTrue(self.resolved(1))

    def test_leaves_issues_written_by_hand(self):
        self.world["episodes"] = [episode(1)]
        self.ask(1, message="They're out of sync after the intro")
        self.ask(2, issue_type=2)
        self.worker.tick()
        self.assertEqual(self.world["searches"], [])
        self.assertEqual(self.world["comments"], [])
        self.assertFalse(self.resolved(1))
        self.assertFalse(self.resolved(2))

    def test_borrows_a_profile_only_for_the_search(self):
        self.world["episodes"] = [episode(1)]
        self.ask(1)
        self.worker.tick()
        self.assertEqual(self.world["without_profile"], 0)
        changes = [(c[0], c[1], c[2].get("profileid"))
                   for c in self.world["calls"]
                   if c[0] in ("POST", "PATCH")]
        self.assertEqual(changes, [("POST", "/api/series", "1"),
                                   ("PATCH", "/api/episodes/subtitles", None),
                                   ("POST", "/api/series", "null")])
        self.assertIsNone(self.world["shows"][SHOW])
        self.assertEqual(self.worker.state["borrowed"], [])

    def test_keeps_a_profile_of_its_own(self):
        self.world["shows"][SHOW] = 2
        self.world["episodes"] = [episode(1)]
        self.ask(1)
        self.worker.tick()
        self.assertEqual(self.world["shows"][SHOW], 2)
        self.assertNotIn("POST", [c[0] for c in self.world["calls"]])
        self.assertTrue(self.resolved(1))

    def test_gives_the_profile_back_if_the_search_breaks(self):
        self.world["episodes"] = [episode(1)]
        self.world["refuse"] = True
        self.ask(1)
        with self.assertRaises(urllib.error.HTTPError):
            self.worker.tick()
        self.assertIsNone(self.world["shows"][SHOW])
        self.assertEqual(self.worker.state["borrowed"], [])

    def test_gives_the_profile_back_after_a_crash(self):
        self.world["shows"][SHOW] = 1
        self.worker.state["borrowed"].append(["tv", SHOW])
        self.worker.save()
        self.start().tick()
        self.assertIsNone(self.world["shows"][SHOW])

    def test_other_jobs_arent_waited_for(self):
        self.world["jobs"].append({"job_name": "Syncing with Sonarr",
                                   "status": "running", "query": {}})
        self.world["episodes"] = [episode(1)]
        self.ask(1)
        self.worker.tick()
        self.assertTrue(self.resolved(1))

    def test_tries_again_each_day_then_gives_up(self):
        self.world["episodes"] = [episode(1)]
        self.world["unavailable"].add(101)
        self.ask(1)
        self.worker.tick()
        self.assertEqual(self.searched(), [101])
        self.assertEqual(self.world["comments"], [])
        # Not again the same day
        self.clock += DAY - 1
        self.worker.tick()
        self.assertEqual(self.searched(), [101])
        for _ in range(worker.TRIES - 1):
            self.clock += DAY
            self.worker.tick()
        self.assertEqual(self.searched(), [101] * worker.TRIES)
        self.assertEqual(self.world["comments"], [
            (1, "Couldn't find Portuguese subtitles for S1E1.")])
        self.assertFalse(self.resolved(1))
        # And that's the end of it
        self.clock += 10 * DAY
        self.worker.tick()
        self.assertEqual(self.searched(), [101] * worker.TRIES)
        self.assertEqual(len(self.world["comments"]), 1)

    def test_goes_on_while_it_finds_some(self):
        """More episodes than the provider's downloads for a day."""
        self.world["episodes"] = [episode(n) for n in range(1, 6)]
        self.ask(1)
        for _ in range(4):
            self.world["quota"] = 1
            self.worker.tick()
            self.clock += DAY
            self.assertFalse(self.resolved(1))
        self.world["quota"] = 1
        self.worker.tick()
        self.assertTrue(self.resolved(1))
        self.assertEqual(self.world["comments"],
                         [(1, "Found the Portuguese subtitles.")])

    def test_says_how_many_are_missing_when_there_are_many(self):
        self.world["episodes"] = [episode(n) for n in range(1, 14)]
        self.world["unavailable"] = {100 + n for n in range(1, 14)}
        self.ask(1)
        for _ in range(worker.TRIES):
            self.worker.tick()
            self.clock += DAY
        said = self.world["comments"][0][1]
        self.assertIn("S1E1, S1E2", said)
        self.assertTrue(said.endswith("S1E10 and 3 more."), said)

    def test_a_movie_that_isnt_found_says_so(self):
        self.world["unavailable"].add("movie")
        self.ask(1, "movie", MOVIE)
        for _ in range(worker.TRIES):
            self.worker.tick()
            self.clock += DAY
        self.assertEqual(self.world["comments"],
                         [(1, "Couldn't find Portuguese subtitles.")])

    def test_a_restart_remembers_the_day(self):
        self.world["episodes"] = [episode(1)]
        self.world["unavailable"].add(101)
        self.ask(1)
        self.worker.tick()
        self.start().tick()
        self.assertEqual(self.searched(), [101])

    def test_forgets_what_someone_else_closed(self):
        self.world["episodes"] = [episode(1)]
        self.world["unavailable"].add(101)
        self.ask(1)
        self.worker.tick()
        self.assertIn("1", self.worker.state["issues"])
        self.world["issues"][1]["status"] = 2
        self.worker.tick()
        self.assertEqual(self.worker.state["issues"], {})

    def test_what_isnt_in_the_library_says_so(self):
        self.ask(1, service=None)
        self.world["episodes"] = [episode(1)]
        self.ask(2, season=9, number=9)
        self.worker.tick()
        self.assertEqual(self.world["comments"], [
            (1, "Sonarr or Radarr doesn't have this."),
            (2, "Bazarr doesn't have this.")])
        self.assertEqual(self.world["searches"], [])

    def test_oldest_first(self):
        self.world["episodes"] = [episode(1), episode(2)]
        self.ask(7, season=1, number=2)
        self.ask(3, season=1, number=1)
        self.worker.tick()
        self.assertEqual(self.searched(), [101, 102])

    def test_reads_what_jelly_writes(self):
        self.assertEqual(
            worker.wanted(issue(1, message="Requesting subtitles in "
                                "Portuguese (Brazil) [pb].")),
            ("Portuguese (Brazil)", "pb"))
        self.assertIsNone(worker.wanted(issue(1, message="Portuguese please")))
        self.assertIsNone(worker.wanted(
            {"comments": [], "id": 1}))

    def test_finds_bazarrs_own_key(self):
        path = os.path.join(self.dir.name, "config.yaml")
        with open(path, "w") as f:
            f.write("sonarr:\n  apikey: not-this\n"
                    "auth:\n  type: null\n  apikey: 'the-key'\n"
                    "radarr:\n  apikey: nor-this\n")
        self.assertEqual(worker.bazarr_key(path), "the-key")


if __name__ == "__main__":
    unittest.main()
