"""Seerr's requests on Telegram, through the alerts' bot (@AureaServerBot).

Each new request is sent to the alerts chat. One that's waiting for approval
comes with buttons to approve or decline it, and a show's with one to choose
which of its seasons to approve (asked for 6, approve the first).

Seerr is polled, so nothing needs setting up in it, and Telegram long-polled,
so nothing is exposed to the internet. Only the buttons in the alerts chat
act. Decided in Seerr instead, a request's buttons go away with the outcome.

Credentials (systemd's LoadCredential): "telegram", the alerts' secret
(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID), and "seerr-settings", Seerr's
settings.json, for its API key. Environment: SEERR_URL, SEERR_PUBLIC_URL (for
links), STATE_DIRECTORY, and TELEGRAM_API (for testing).
"""

import html
import json
import os
import time
import urllib.error
import urllib.request

# A request's status in Seerr
PENDING, APPROVED, DECLINED, FAILED, COMPLETED = 1, 2, 3, 4, 5
# How often Seerr is looked at, in seconds
EVERY = 30


def call(url, body=None, headers=None, method=None, timeout=30):
    """Send a request and return its JSON reply."""
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(request, timeout=timeout) as reply:
        raw = reply.read()
    return json.loads(raw) if raw else None


def problem(error):
    """What went wrong, from the reply's JSON when there is one (never the
    address, which has the bot's token in it)."""
    if isinstance(error, urllib.error.HTTPError):
        try:
            body = json.loads(error.read())
            return body.get("message") or body.get("description") or ""
        except ValueError:
            return f"error {error.code}"
    return str(error)


def credential(name):
    return os.path.join(os.environ["CREDENTIALS_DIRECTORY"], name)


def read_env_file(path):
    """KEY=value lines, as the alerts' secret has them (shell-sourced, so
    maybe with export and quotes)."""
    values = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                key, _, value = line.removeprefix("export ").partition("=")
                values[key.strip()] = value.strip().strip("'\"")
    return values


def ranges(numbers):
    """[1, 2, 3, 5] → "1–3, 5"."""
    parts, numbers = [], sorted(numbers)
    start = previous = None
    for n in numbers + [None]:
        if previous is not None and n == previous + 1:
            previous = n
            continue
        if start is not None:
            parts.append(
                str(start) if start == previous else f"{start}–{previous}")
        start = previous = n
    return ", ".join(parts)


def season_numbers(request):
    return sorted(s["seasonNumber"] for s in request.get("seasons") or [])


class Seerr:
    def __init__(self, url, api_key):
        self.url = url.rstrip("/") + "/api/v1"
        self.headers = {"X-Api-Key": api_key}

    def send(self, path, body=None, method=None):
        return call(self.url + path, body, self.headers, method)

    def latest(self):
        """The 20 most recent requests, newest first."""
        page = self.send("/request?take=20&sort=added&filter=all")
        return page["results"]

    def request(self, request_id):
        """The request, or None if it's been removed."""
        try:
            return self.send(f"/request/{request_id}")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise

    def details(self, request):
        """(name, year, poster path, number of seasons) of what's asked for"""
        media = request["media"]
        kind = "movie" if request["type"] == "movie" else "tv"
        found = self.send(f"/{kind}/{media['tmdbId']}")
        date = found.get("releaseDate") or found.get("firstAirDate") or ""
        name = found.get("title") or found.get("name") or "?"
        return name, date[:4], found.get("posterPath"), found.get(
            "numberOfSeasons")

    def decide(self, request, approve):
        status = "approve" if approve else "decline"
        self.send(f"/request/{request['id']}/{status}", method="POST")

    def keep_seasons(self, request, seasons):
        """Narrow a pending show's request to `seasons`. Seerr sets what
        it's sent, so the request's server, profile and folder go back as
        they are. One left to Seerr's defaults is null, which Seerr refuses:
        left out, it stays null."""
        body = {"mediaType": "tv", "seasons": sorted(seasons)}
        for key in ("is4k", "serverId", "profileId", "rootFolder",
                    "languageProfileId", "tags"):
            if request.get(key) is not None:
                body[key] = request[key]
        self.send(f"/request/{request['id']}", body, method="PUT")


class Telegram:
    def __init__(self, api, token):
        self.url = f"{api}/bot{token}/"

    def __call__(self, method, http_timeout=30, **params):
        return call(self.url + method, params, timeout=http_timeout)["result"]


def outcome(request):
    """How a request that isn't waiting anymore ended, in a line."""
    if request is None:
        return "🗑 Removed in Seerr"
    by = (request.get("modifiedBy") or {}).get("displayName")
    by = f" by {html.escape(by)}" if by else ""
    return {
        APPROVED: f"✅ Approved{by}",
        DECLINED: f"❌ Declined{by}",
        FAILED: "⚠️ Approved, but sending it to Radarr/Sonarr failed",
        COMPLETED: "✅ Available",
    }.get(request["status"], "")


class Bot:
    def __init__(self, seerr, telegram, chat, public_url, state_path):
        self.seerr, self.telegram, self.chat = seerr, telegram, str(chat)
        self.public_url = public_url.rstrip("/") if public_url else None
        self.state_path = state_path
        try:
            with open(state_path) as f:
                self.state = json.load(f)
        except FileNotFoundError:
            self.state = None
        self.next_look = 0

    def save(self):
        with open(self.state_path + ".tmp", "w") as f:
            json.dump(self.state, f)
        os.replace(self.state_path + ".tmp", self.state_path)

    # ---- Seerr ----

    def look(self):
        """Send what's new, and update what was decided in Seerr."""
        requests = self.seerr.latest()
        if self.state is None:
            # The first time: what's waiting now, not the whole history
            self.state = {"offset": 0, "latest": 0, "sent": {}}
            waiting = [r for r in requests if r["status"] == PENDING]
            self.state["latest"] = max((r["id"] for r in requests), default=0)
            for request in sorted(waiting, key=lambda r: r["id"]):
                self.announce(request)
        for request in sorted(requests, key=lambda r: r["id"]):
            if request["id"] > self.state["latest"]:
                self.announce(request)
                self.state["latest"] = request["id"]
        by_id = {str(r["id"]): r for r in requests}
        for request_id in list(self.state["sent"]):
            request = by_id.get(request_id) or self.seerr.request(request_id)
            if request is None or request["status"] != PENDING:
                self.finish(request_id, outcome(request),
                            request and self.link(request))
        self.save()

    def describe(self, request):
        name, year, poster, total = self.seerr.details(request)
        who = request["requestedBy"].get("displayName") or "Someone"
        what = f"<b>{html.escape(name)}</b>" + (f" ({year})" if year else "")
        icon = "🎬" if request["type"] == "movie" else "📺"
        text = f"{icon} <b>{html.escape(who)}</b> requested {what}"
        if request["type"] == "tv":
            seasons = season_numbers(request)
            text += f"\nSeason{'s' if len(seasons) > 1 else ''} "
            # (TMDB's count can be behind what Seerr asks for)
            whole = total and total >= max(seasons, default=0)
            text += ranges(seasons) + (f" of {total}" if whole else "")
        if request.get("is4k"):
            text += "\nIn 4K"
        return text, poster

    def link(self, request):
        kind = "movie" if request["type"] == "movie" else "tv"
        if not self.public_url:
            return []
        return [[{"text": "Open in Seerr", "url":
                  f"{self.public_url}/{kind}/{request['media']['tmdbId']}"}]]

    def buttons(self, request):
        rid = request["id"]
        rows = [[{"text": "✅ Approve", "callback_data": f"a:{rid}"},
                 {"text": "❌ Decline", "callback_data": f"d:{rid}"}]]
        if len(season_numbers(request)) > 1:
            rows[0][0]["text"] = "✅ Approve all"
            # None chosen yet: a tap on a season chooses it
            rows.append([{"text": "✏️ Choose seasons",
                          "callback_data": f"p:{rid}:0"}])
        return {"inline_keyboard": rows + self.link(request)}

    def picker(self, request, chosen):
        """A button per season, ✅ for the chosen ones (a bit each in
        `chosen`, in season order), and one to approve those."""
        rid, seasons = request["id"], season_numbers(request)
        toggles = [
            {"text": ("✅ " if chosen >> i & 1 else "◻️ ") + f"S{n}",
             "callback_data": f"p:{rid}:{chosen ^ (1 << i):x}"}
            for i, n in enumerate(seasons)]
        rows = [toggles[i:i + 4] for i in range(0, len(toggles), 4)]
        count = bin(chosen).count("1")
        approve = (f"✅ Approve {count} season{'s' if count > 1 else ''}"
                   if count else "Choose a season")
        rows.append([{"text": approve, "callback_data": f"k:{rid}:{chosen:x}"},
                     {"text": "↩️ Back", "callback_data": f"b:{rid}"}])
        return {"inline_keyboard": rows}

    def announce(self, request):
        text, poster = self.describe(request)
        waiting = request["status"] == PENDING
        if not waiting:
            text += "\n" + (outcome(request) or "")
        markup = self.buttons(request) if waiting else {
            "inline_keyboard": self.link(request)}
        params = {"chat_id": self.chat, "parse_mode": "HTML",
                  "reply_markup": markup}
        sent = None
        if poster:
            try:
                sent = self.telegram(
                    "sendPhoto", caption=text, **params,
                    photo=f"https://image.tmdb.org/t/p/w500{poster}")
            except urllib.error.HTTPError as e:
                # Telegram couldn't fetch the poster, say
                print(f"sending the poster failed: {problem(e)}")
        if sent is None:
            poster = None
            sent = self.telegram("sendMessage", text=text, **params)
        if waiting:
            self.state["sent"][str(request["id"])] = {
                "message": sent["message_id"], "text": text,
                "photo": bool(poster)}

    def finish(self, request_id, line, link=None):
        """Replace a request's buttons with how it ended."""
        sent = self.state["sent"].pop(str(request_id), None)
        if sent is None:
            return
        text = sent["text"] + "\n\n" + line
        params = {"chat_id": self.chat, "message_id": sent["message"],
                  "parse_mode": "HTML",
                  "reply_markup": {"inline_keyboard": link or []}}
        try:
            if sent["photo"]:
                self.telegram("editMessageCaption", caption=text, **params)
            else:
                self.telegram("editMessageText", text=text, **params)
        except urllib.error.HTTPError as e:
            print(f"couldn't update request {request_id}: {problem(e)}")

    # ---- Telegram ----

    def listen(self):
        """Wait for button presses until it's time to look at Seerr."""
        wait = max(1, min(25, int(self.next_look - time.monotonic())))
        updates = self.telegram(
            "getUpdates", http_timeout=wait + 10, timeout=wait,
            offset=self.state["offset"], allowed_updates=["callback_query"])
        for update in updates:
            self.state["offset"] = update["update_id"] + 1
            if "callback_query" in update:
                self.pressed(update["callback_query"])
        if updates:
            self.save()

    def pressed(self, query):
        chat = str(query.get("message", {}).get("chat", {}).get("id"))
        if chat != self.chat:
            return self.answer(query, "This isn't your server's chat")
        action, request_id, *rest = query.get("data", "").split(":") + [""]
        if not request_id.isdigit():
            return self.answer(query)
        try:
            self.act(query, action, request_id, rest[0])
        except urllib.error.HTTPError as e:
            self.answer(query, f"Seerr said no: {problem(e)}", alert=True)
        except (OSError, ValueError, KeyError) as e:
            self.answer(query, f"Couldn't reach Seerr: {e}", alert=True)

    def act(self, query, action, request_id, chosen):
        self.remember(request_id, query["message"])
        request = self.seerr.request(request_id)
        if request is None or request["status"] != PENDING:
            self.finish(request_id, outcome(request))
            return self.answer(query, "It was decided in Seerr already")
        message = query["message"]["message_id"]
        if action in ("a", "d"):
            self.seerr.decide(request, approve=action == "a")
            self.finish(request_id, outcome(self.seerr.request(request_id)),
                        self.link(request))
            return self.answer(query, "Done")
        if action == "p":
            self.telegram("editMessageReplyMarkup", chat_id=self.chat,
                          message_id=message,
                          reply_markup=self.picker(request, int(chosen, 16)))
            return self.answer(query)
        if action == "b":
            self.telegram("editMessageReplyMarkup", chat_id=self.chat,
                          message_id=message,
                          reply_markup=self.buttons(request))
            return self.answer(query)
        if action == "k":
            seasons = season_numbers(request)
            keep = [n for i, n in enumerate(seasons)
                    if int(chosen, 16) >> i & 1]
            if not keep:
                return self.answer(query, "Choose a season first")
            if keep != seasons:
                self.seerr.keep_seasons(request, keep)
            self.seerr.decide(request, approve=True)
            line = outcome(self.seerr.request(request_id))
            plural = "s" if len(keep) > 1 else ""
            self.finish(request_id,
                        f"{line}: season{plural} {ranges(keep)}",
                        self.link(request))
            return self.answer(query, "Done")
        return self.answer(query)

    def remember(self, request_id, message):
        """Keep a request's message, if it was sent before this state (from
        an older run, say), to update it like the others."""
        if request_id in self.state["sent"]:
            return
        photo = "caption" in message
        text = html.escape(message.get("caption") or message.get("text", ""))
        self.state["sent"][request_id] = {
            "message": message["message_id"], "text": text, "photo": photo}

    def answer(self, query, text="", alert=False):
        try:
            self.telegram("answerCallbackQuery",
                          callback_query_id=query["id"], text=text,
                          show_alert=alert)
        except (OSError, ValueError) as e:
            print(f"couldn't answer a button: {problem(e)}")

    def run(self):
        while True:
            if time.monotonic() >= self.next_look:
                self.next_look = time.monotonic() + EVERY
                try:
                    self.look()
                except (OSError, ValueError, KeyError) as e:
                    print(f"couldn't look at Seerr: {problem(e)}")
            if self.state is None:
                time.sleep(EVERY)
                continue
            try:
                self.listen()
            except (OSError, ValueError, KeyError) as e:
                print(f"couldn't reach Telegram: {problem(e)}")
                time.sleep(10)


def main():
    telegram = read_env_file(credential("telegram"))
    with open(credential("seerr-settings")) as f:
        api_key = json.load(f)["main"]["apiKey"]
    Bot(
        Seerr(os.environ.get("SEERR_URL", "http://127.0.0.1:5055"), api_key),
        Telegram(os.environ.get("TELEGRAM_API", "https://api.telegram.org"),
                 telegram["TELEGRAM_BOT_TOKEN"]),
        telegram["TELEGRAM_CHAT_ID"],
        os.environ.get("SEERR_PUBLIC_URL"),
        os.path.join(os.environ["STATE_DIRECTORY"], "state.json"),
    ).run()


if __name__ == "__main__":
    main()
