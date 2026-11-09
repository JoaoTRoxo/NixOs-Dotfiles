"""Subtitles requested through Seerr, fetched by Bazarr, with nobody approving.

Jelly's Request Subtitles opens an issue about subtitles in Seerr, whose
message ends with Bazarr's code for the language: "Requesting subtitles in
Portuguese [pt]." This goes through the open ones and has Bazarr search for
whatever each covers (a show, a season, an episode or a movie) that doesn't
have that language yet. Once it's all there, the issue is resolved. Issues
written by hand have no code, and are left for a person.

Providers run out for the day (OpenSubtitles' downloads, say), so what isn't
found is tried again a day later, for as long as tries keep finding some.
After TRIES in a row that find nothing, the issue gets a comment saying what's
missing, and stays open.

Bazarr can't search for anything without a language profile (it reads the
profile's options, and fails with none), and most of the library has none:
with one, it would fetch all that profile's languages for the whole show by
itself. So what has none borrows the first profile for the search, and gives
it back.

Seerr is polled, so nothing needs setting up in it. Credentials (systemd's
LoadCredential): "seerr-settings", Seerr's settings.json, and "bazarr-config",
Bazarr's config.yaml, for their API keys. Environment: SEERR_URL, BAZARR_URL
and STATE_DIRECTORY.
"""

import http.client
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

# An issue about subtitles in Seerr ("IssueType")
SUBTITLES_ISSUE = 3
# What Jelly writes (see subtitles_issue in Jelly's src/seerr/mod.rs)
REQUEST = re.compile(r"Requesting subtitles in (.+) \[([a-z]{2})\]\.")
# How often Seerr is looked at, in seconds
EVERY = 60
# How long until another try at what wasn't found
RETRY_AFTER = 24 * 60 * 60
# Tries in a row that find nothing before giving up
TRIES = 3
# How long one search may take, and how often Bazarr's queue is looked at
SEARCH_TIMEOUT = 600
POLL = 3
# As many missing episodes as a comment names
NAMED = 10


def call(url, data=None, headers=None, method=None, timeout=60):
    """Send a request and return its JSON reply."""
    request = urllib.request.Request(
        url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(request, timeout=timeout) as reply:
        raw = reply.read()
    return json.loads(raw) if raw else None


def problem(error):
    """What went wrong, without the query (or anything else in the call)."""
    if isinstance(error, urllib.error.HTTPError):
        return f"error {error.code} from {error.url.split('?')[0]}"
    return f"{type(error).__name__}: {error}"


def credential(name):
    return os.path.join(os.environ["CREDENTIALS_DIRECTORY"], name)


def bazarr_key(path):
    """auth.apikey in Bazarr's config.yaml, which has others (Sonarr's, say)
    in other sections."""
    section = None
    with open(path) as f:
        for line in f:
            if not line[:1].isspace():
                section = line.strip().rstrip(":")
            elif section == "auth" and line.strip().startswith("apikey:"):
                return line.split(":", 1)[1].strip().strip("'\"")
    raise ValueError("no auth.apikey in Bazarr's config")


class Seerr:
    def __init__(self, url, api_key):
        self.url = url.rstrip("/") + "/api/v1"
        self.headers = {"X-Api-Key": api_key,
                        "Content-Type": "application/json"}

    def send(self, path, body=None, method=None):
        data = None if body is None else json.dumps(body).encode()
        return call(self.url + path, data, self.headers, method)

    def open_subtitle_issues(self):
        """Oldest first."""
        page = self.send("/issue?take=100&skip=0&sort=added&filter=open")
        return [issue for issue in reversed(page["results"])
                if issue["issueType"] == SUBTITLES_ISSUE]

    def comment(self, issue_id, message):
        self.send(f"/issue/{issue_id}/comment", {"message": message})

    def resolve(self, issue_id):
        self.send(f"/issue/{issue_id}/resolved", method="POST")


class Bazarr:
    """An item is ("tv", Sonarr's id for the show) or ("movie", Radarr's)."""

    def __init__(self, url, api_key):
        self.url = url.rstrip("/") + "/api"
        self.headers = {"X-API-KEY": api_key}

    def send(self, path, query=None, method=None):
        url = f"{self.url}{path}?{urllib.parse.urlencode(query or {})}"
        return call(url, headers=self.headers, method=method)

    def videos(self, item):
        """Its episodes, or the movie."""
        kind, number = item
        if kind == "tv":
            return self.send("/episodes", {"seriesid[]": number})["data"]
        return self.send("/movies", {"radarrid[]": number})["data"]

    def has_profile(self, item):
        kind, number = item
        found = (self.send("/series", {"seriesid[]": number})
                 if kind == "tv" else
                 self.send("/movies", {"radarrid[]": number}))["data"]
        return bool(found and found[0].get("profileId"))

    def first_profile(self):
        return self.send("/system/languages/profiles")[0]["profileId"]

    def set_profile(self, item, profile):
        """`profile`: its id, or "null" for none."""
        kind, number = item
        if kind == "tv":
            query = {"seriesid": number, "profileid": profile}
            self.send("/series", query, "POST")
        else:
            query = {"radarrid": number, "profileid": profile}
            self.send("/movies", query, "POST")

    def search(self, item, video, code):
        """Queue a search for `video`'s subtitles in `code`."""
        kind, number = item
        query = {"language": code, "forced": "False", "hi": "False"}
        if kind == "tv":
            query |= {"seriesid": number,
                      "episodeid": video["sonarrEpisodeId"]}
            self.send("/episodes/subtitles", query, "PATCH")
        else:
            query |= {"radarrid": number}
            self.send("/movies/subtitles", query, "PATCH")

    def searching(self):
        """Whether searches are waiting or running. The call that asks for
        one returns at once, without saying how it went."""
        return any(job["status"] in ("pending", "running")
                   and job["job_name"].startswith("Searching")
                   for job in self.send("/system/jobs")["data"])

    def wait(self):
        deadline = time.monotonic() + SEARCH_TIMEOUT
        while self.searching() and time.monotonic() < deadline:
            time.sleep(POLL)


def wanted(issue):
    """The language an issue asks for (its name and Bazarr's code), if it's a
    request from Jelly."""
    comments = issue.get("comments") or []
    match = comments and REQUEST.fullmatch(comments[0]["message"].strip())
    return match.groups() if match else None


def covered(video, season, episode):
    """Whether a request for `season` and `episode` (0: all) covers `video`.
    A movie has neither, and is only ever asked for whole."""
    return ((not season or video.get("season") == season)
            and (not episode or video.get("episode") == episode))


def has(video, code):
    return any(s["code2"] == code and not s["forced"]
               for s in video["subtitles"])


def named(videos):
    """The episodes in `videos`, for a comment; nothing for a movie."""
    names = [f"S{v['season']}E{v['episode']}"
             for v in sorted(videos, key=lambda v: (v.get("season", 0),
                                                    v.get("episode", 0)))
             if "season" in v]
    if not names:
        return ""
    more = f" and {len(names) - NAMED} more" if len(names) > NAMED else ""
    return f" for {', '.join(names[:NAMED])}{more}"


class Worker:
    def __init__(self, seerr, bazarr, state_path):
        self.seerr = seerr
        self.bazarr = bazarr
        self.state_path = state_path
        self.now = time.time
        try:
            with open(state_path) as f:
                self.state = json.load(f)
        except FileNotFoundError:
            self.state = {"issues": {}, "borrowed": []}

    def save(self):
        with open(self.state_path + ".new", "w") as f:
            json.dump(self.state, f)
        os.replace(self.state_path + ".new", self.state_path)

    def give_back(self):
        """Back to no profile, for what borrowed one: right after a search,
        and first thing next time if that didn't get to happen."""
        for item in list(self.state["borrowed"]):
            self.bazarr.set_profile(tuple(item), "null")
            self.state["borrowed"].remove(item)
            self.save()

    def search(self, item, videos, code):
        """One at a time: Bazarr's queue then says when each is done."""
        if not self.bazarr.has_profile(item):
            # Noted first, in case this never gets to give it back
            self.state["borrowed"].append(list(item))
            self.save()
            self.bazarr.set_profile(item, self.bazarr.first_profile())
        try:
            for video in videos:
                self.bazarr.search(item, video, code)
                self.bazarr.wait()
        finally:
            self.give_back()

    def is_due(self, issue):
        record = self.state["issues"].get(str(issue["id"]), {})
        return (not record.get("given_up")
                and self.now() >= record.get("next", 0))

    def give_up(self, issue, message):
        self.seerr.comment(issue["id"], message)
        self.state["issues"][str(issue["id"])] = {"given_up": True}
        self.save()
        print(f"issue {issue['id']}: gave up: {message}", flush=True)

    def done(self, issue, message):
        self.seerr.comment(issue["id"], message)
        self.seerr.resolve(issue["id"])
        self.state["issues"].pop(str(issue["id"]), None)
        self.save()
        print(f"issue {issue['id']}: {message}", flush=True)

    def fulfil(self, issue, name, code):
        media = issue["media"]
        item = (media["mediaType"], media.get("externalServiceId"))
        part = (issue["problemSeason"], issue["problemEpisode"])
        if item[1] is None:
            self.give_up(issue, "Sonarr or Radarr doesn't have this.")
            return

        def videos():
            return [v for v in self.bazarr.videos(item) if covered(v, *part)]

        def without(found):
            return [v for v in found if not has(v, code)]

        found = videos()
        if not found:
            self.give_up(issue, "Bazarr doesn't have this.")
            return
        missing = without(found)
        if not missing:
            self.done(issue, f"These {name} subtitles were already here.")
            return
        print(f"issue {issue['id']}: searching {name} for {len(missing)} of "
              f"{item}", flush=True)
        self.search(item, missing, code)
        left = without(videos())
        if not left:
            self.done(issue, f"Found the {name} subtitles.")
            return
        record = self.state["issues"].setdefault(str(issue["id"]), {})
        record["idle"] = (0 if len(left) < len(missing)
                          else record.get("idle", 0) + 1)
        if record["idle"] >= TRIES:
            self.give_up(
                issue, f"Couldn't find {name} subtitles{named(left)}.")
            return
        record["next"] = self.now() + RETRY_AFTER
        self.save()

    def tick(self):
        self.give_back()
        issues = self.seerr.open_subtitle_issues()
        for issue in issues:
            language = wanted(issue)
            if language and self.is_due(issue):
                self.fulfil(issue, *language)
        still_open = {str(issue["id"]) for issue in issues}
        if set(self.state["issues"]) - still_open:
            self.state["issues"] = {
                key: record for key, record in self.state["issues"].items()
                if key in still_open}
            self.save()

    def run(self):
        while True:
            try:
                self.tick()
            except (OSError, ValueError, KeyError, IndexError,
                    http.client.HTTPException) as error:
                # Seerr or Bazarr being away is waited out
                print(f"trying again later: {problem(error)}", flush=True)
            time.sleep(EVERY)


def main():
    with open(credential("seerr-settings")) as f:
        seerr_key = json.load(f)["main"]["apiKey"]
    Worker(
        Seerr(os.environ.get("SEERR_URL", "http://127.0.0.1:5055"), seerr_key),
        Bazarr(os.environ.get("BAZARR_URL", "http://127.0.0.1:6767"),
               bazarr_key(credential("bazarr-config"))),
        os.path.join(os.environ["STATE_DIRECTORY"], "state.json"),
    ).run()


if __name__ == "__main__":
    main()
