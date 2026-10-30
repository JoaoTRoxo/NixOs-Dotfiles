"""What Pulse collects on aurea itself: the machine (from Prometheus), its
units and jobs, the jrxd recordings, the media apps, the calendar and the
tailnet. Each collector takes the config and returns a dict for the
snapshot, or raises; server.py runs them on their own schedules.
"""

import base64
import datetime as dt
import glob
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

USER_AGENT = "pulse (aurea dashboard)"


def fetch(url, headers=None, body=None, method=None, timeout=20, raw=False):
    """GET (or send `body`) and return the reply's JSON, or its text."""
    data = body if isinstance(body, (bytes, type(None))) else json.dumps(
        body).encode()
    request = urllib.request.Request(url, data=data, method=method, headers={
        "User-Agent": USER_AGENT, "Accept": "application/json",
        **({"Content-Type": "application/json"} if data else {}),
        **(headers or {})})
    with urllib.request.urlopen(request, timeout=timeout) as reply:
        text = reply.read().decode()
    return text if raw else json.loads(text) if text else None


def credential(name):
    """A credential's contents from systemd's LoadCredential, or None."""
    directory = os.environ.get("CREDENTIALS_DIRECTORY")
    if not name or not directory:
        return None
    try:
        with open(os.path.join(directory, name)) as f:
            return f.read().strip()
    except FileNotFoundError:
        return None


def run(*command, timeout=60):
    return subprocess.run(command, capture_output=True, text=True,
                          timeout=timeout, check=True).stdout


def iso(seconds):
    return dt.datetime.fromtimestamp(seconds, dt.timezone.utc).isoformat()


# The machine, from Prometheus

class Prometheus:
    def __init__(self, url):
        self.url = url.rstrip("/") + "/api/v1/"

    def query(self, expr):
        """{labels: value} for an instant query (labels as a sorted tuple)."""
        reply = fetch(self.url + "query?" + urllib.parse.urlencode(
            {"query": expr}))
        return {tuple(sorted(r["metric"].items())): float(r["value"][1])
                for r in reply["data"]["result"]}

    def one(self, expr):
        values = list(self.query(expr).values())
        return values[0] if values else None

    def series(self, expr, hours=24, step=900):
        """[[unix time, value], ...] of a single-series range query."""
        end = time.time()
        reply = fetch(self.url + "query_range?" + urllib.parse.urlencode({
            "query": expr, "start": end - hours * 3600, "end": end,
            "step": step}))
        result = reply["data"]["result"]
        if not result:
            return []
        return [[int(t), round(float(v), 4)] for t, v in result[0]["values"]
                if v not in ("NaN", "+Inf", "-Inf")]


# Network traffic, without loopback, containers' veths and the tailnet
NIC = 'device!~"lo|veth.*|docker.*|br-.*|tailscale.*"'


def machine(cfg):
    p = Prometheus(cfg["prometheus"])
    cpu = '1 - avg(rate(node_cpu_seconds_total{mode="idle"}[5m]))'
    mem = "1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes"
    temp = cfg.get("temperature", "max(node_hwmon_temp_celsius)")
    rx = f"sum(rate(node_network_receive_bytes_total{{{NIC}}}[5m]))"
    tx = f"sum(rate(node_network_transmit_bytes_total{{{NIC}}}[5m]))"
    data = {
        "uptime": p.one("time() - node_boot_time_seconds"),
        "cores": p.one('count(node_cpu_seconds_total{mode="idle"})'),
        "load": [p.one("node_load1"), p.one("node_load5"),
                 p.one("node_load15")],
        "cpu": {"now": p.one(cpu), "series": p.series(cpu)},
        "memory": {"now": p.one(mem), "series": p.series(mem),
                   "total": p.one("node_memory_MemTotal_bytes"),
                   "swap": p.one("1 - node_memory_SwapFree_bytes"
                                 " / node_memory_SwapTotal_bytes")},
        "temperature": {"now": p.one(temp), "series": p.series(temp)},
        "network": {"rx": p.one(rx), "tx": p.one(tx),
                    "series": p.series(rx)},
        "disks": [],
    }
    for mount in cfg.get("mounts", []):
        sel = f'mountpoint="{mount["path"]}"'
        size = p.one(f"node_filesystem_size_bytes{{{sel}}}")
        if size is None:
            data["disks"].append({**mount, "mounted": False})
            continue
        used = (f"1 - node_filesystem_avail_bytes{{{sel}}}"
                f" / node_filesystem_size_bytes{{{sel}}}")
        free = p.one(f"node_filesystem_avail_bytes{{{sel}}}")
        free_day_ago = p.one(
            f"node_filesystem_avail_bytes{{{sel}}} offset 1d")
        data["disks"].append({
            **mount, "mounted": True, "size": size, "free": free,
            "used": p.one(used), "series": p.series(used),
            # Bytes used up in the last 24 hours (negative: freed)
            "grew": None if free_day_ago is None else free_day_ago - free})
    data["smart"] = smart(p, cfg.get("disks", {}))
    return data


def smart(p, disks):
    """Per disk (named in the config by id, since sdX names move): health,
    temperature and the counters that matter."""
    by_device = {os.path.basename(os.path.realpath(path)): name
                 for name, path in disks.items()}
    out = {}
    for labels, value in p.query("smartctl_device_smart_status").items():
        device = dict(labels)["device"]
        out[device] = {"name": by_device.get(device, device),
                       "device": device, "passed": value == 1}
    temps = p.query('smartctl_device_temperature'
                    '{temperature_type="current"}')
    for labels, value in temps.items():
        out.setdefault(dict(labels)["device"], {})["temperature"] = value
    # 5 reallocated, 197 pending, 198 offline uncorrectable, 199 SATA CRC
    counters = p.query('smartctl_device_attribute{attribute_value_type="raw",'
                       'attribute_id=~"5|197|198|199"}')
    for labels, value in counters.items():
        labels = dict(labels)
        disk = out.setdefault(labels["device"], {})
        disk.setdefault("counters", {})[labels["attribute_id"]] = {
            "name": labels["attribute_name"], "value": int(value)}
    return sorted(out.values(), key=lambda d: d.get("name", ""))


# Units, jobs and the server's own alerts

def systemctl_show(units, properties):
    """[{property: value}] for each unit, in order, timestamps as unix
    seconds."""
    if not units:
        return []
    text = run("systemctl", "show", "--timestamp=unix",
               "--property=" + ",".join(properties), *units)
    blocks = []
    for block in text.strip().split("\n\n"):
        values = {}
        for line in block.splitlines():
            key, _, value = line.partition("=")
            if value.startswith("@") and value[1:].isdigit():
                value = int(value[1:])
            values[key] = value
        blocks.append(values)
    return blocks


UNIT = ["Id", "Description", "ActiveState", "SubState", "Result",
        "NRestarts", "ActiveEnterTimestamp", "StateChangeTimestamp"]
JOB = ["Id", "ActiveState", "Result", "ExecMainStartTimestamp",
       "ExecMainExitTimestamp", "ExecMainStatus"]
TIMER = ["Id", "LastTriggerUSec", "NextElapseUSecRealtime"]


def units(cfg):
    services = cfg.get("services", [])
    shown = systemctl_show([s["unit"] for s in services], UNIT)
    data = {"services": [], "jobs": [], "failed": [], "alerts": []}
    for service, state in zip(services, shown):
        data["services"].append({
            **service, "active": state.get("ActiveState"),
            "sub": state.get("SubState"),
            "restarts": int(state.get("NRestarts") or 0),
            "since": state.get("ActiveEnterTimestamp") or None})

    jobs = cfg.get("jobs", [])
    names = [j["unit"] for j in jobs]
    runs = systemctl_show([n + ".service" for n in names], JOB)
    timers = systemctl_show([n + ".timer" for n in names], TIMER)
    for job, last, timer in zip(jobs, runs, timers):
        started = last.get("ExecMainStartTimestamp") or None
        data["jobs"].append({
            **job,
            # systemd only knows the result of runs since boot; before that
            # there's just when the timer last fired
            "result": last.get("Result") if started else None,
            "running": last.get("ActiveState") == "activating",
            "started": started,
            "finished": last.get("ExecMainExitTimestamp") or None,
            "triggered": timer.get("LastTriggerUSec") or None,
            "status": last.get("ExecMainStatus"),
            "next": timer.get("NextElapseUSecRealtime") or None})

    failed = run("systemctl", "list-units", "--state=failed", "--plain",
                 "--no-legend", "--all")
    data["failed"] = [line.split()[0] for line in failed.splitlines()
                      if line.strip()]

    # Problems the server-alerts job has reported and that haven't cleared
    for path in sorted(glob.glob(cfg.get(
            "alerts_dir", "/var/lib/server-alerts/active") + "/*")):
        try:
            with open(path) as f:
                message = f.read().strip()
            data["alerts"].append({"key": os.path.basename(path),
                                   "message": message,
                                   "since": int(os.stat(path).st_mtime)})
        except OSError:
            pass
    return data


def reach(cfg):
    """The public addresses, through Cloudflare's tunnel and Caddy. Anything
    below 500 means the service answered (401 and redirects included)."""
    def check(endpoint):
        start = time.monotonic()
        try:
            request = urllib.request.Request(
                endpoint["url"], headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=15) as reply:
                code = reply.status
        except urllib.error.HTTPError as e:
            code = e.code
        except Exception as e:
            return {**endpoint, "ok": False, "error": str(e)[:200]}
        return {**endpoint, "ok": code < 500, "code": code,
                "ms": round((time.monotonic() - start) * 1000)}

    with ThreadPoolExecutor(8) as pool:
        return {"endpoints": list(pool.map(check, cfg.get("endpoints", [])))}


def tailnet(cfg):
    status = json.loads(run("tailscale", "status", "--json"))
    me = status.get("Self", {})
    peers = []
    for peer in (status.get("Peer") or {}).values():
        seen = peer.get("LastSeen") or ""
        peers.append({"name": peer.get("HostName"), "os": peer.get("OS"),
                      "online": bool(peer.get("Online")),
                      "seen": None if seen.startswith("0001") else seen})
    peers.sort(key=lambda p: (not p["online"], p["seen"] or ""))
    return {"name": me.get("HostName"), "dns": me.get("DNSName"),
            "online": status.get("BackendState") == "Running",
            "peers": peers}


# The jrxd recordings

def recordings(cfg):
    """Each stream's files from the files themselves, which is cheap: bytes
    recorded per UTC day, and whether it's still writing."""
    root = cfg["jrxd"]["dir"]
    now = time.time()
    today = dt.datetime.now(dt.timezone.utc).date()
    # The first UTC day with recordings, so a short history isn't mistaken
    # for missing days
    first = min((os.path.basename(d) for d in glob.glob(
        os.path.join(root, "*", "*", "*", "*-*-*"))), default=None)
    days = [(today - dt.timedelta(days=n)).isoformat() for n in range(7)]
    streams, per_day = [], {d: 0 for d in days}
    for stream_dir in sorted(glob.glob(os.path.join(root, "*", "*", "*"))):
        venue, instrument, stream = stream_dir.split(os.sep)[-3:]
        latest, bytes_by_day = 0, {}
        for day in days:
            for path in glob.glob(os.path.join(stream_dir, day, "*")):
                st = os.stat(path)
                latest = max(latest, st.st_mtime)
                bytes_by_day[day] = bytes_by_day.get(day, 0) + st.st_size
                per_day[day] += st.st_size
        streams.append({
            "id": f"{venue}:{instrument} {stream}",
            "venue": venue, "instrument": instrument, "stream": stream,
            "today": bytes_by_day.get(days[0], 0),
            "yesterday": bytes_by_day.get(days[1], 0),
            "last_write": int(latest) or None,
            "idle": None if not latest else round(now - latest)})
    unit = systemctl_show([cfg["jrxd"].get("unit", "jrxd.service")], UNIT)
    state = unit[0] if unit else {}
    return {"streams": streams, "first_day": first,
            "days": [[d, per_day[d]] for d in days if not first or d >= first],
            "active": state.get("ActiveState"),
            "since": state.get("ActiveEnterTimestamp") or None,
            "restarts": int(state.get("NRestarts") or 0)}


SIZE = {"B": 1, "kB": 1e3, "MB": 1e6, "GB": 1e9}
DURATION = {"s": 1, "min": 60, "h": 3600}
FILE_LINE = re.compile(
    r"^  (\S+) → (\S+)  (\d+) messages(?:, (\d+) snapshots?)?"
    r"  ([\d.]+) (B|kB|MB|GB)  (.+?)  (\S+)$")
GAPS_LINE = re.compile(r"^  (\d+) gaps?, ([\d.]+) (s|min|h) missing$")
GAP_LINE = re.compile(r"^    gap (\S+) → (\S+)  ([\d.]+) (s|min|h)  (.*)$")


def parse_jrxd_list(text):
    """`jrxd list`'s report → {stream id: messages, gaps, missing seconds,
    the gaps' reasons and whether its last file is still open}."""
    streams, current = {}, None
    for line in text.splitlines():
        if line.startswith("no capture files") or ": unreadable: " in line:
            continue
        if not line.startswith(" ") and " " in line:
            instrument, stream = line.rsplit(" ", 1)
            current = streams[f"{instrument} {stream}"] = {
                "files": 0, "messages": 0, "snapshots": 0, "gaps": 0,
                "missing": 0.0, "reasons": {}, "open": False, "ended": None}
        elif current is None:
            continue
        elif m := FILE_LINE.match(line):
            current["files"] += 1
            current["messages"] += int(m[3])
            current["snapshots"] += int(m[4] or 0)
            current["open"] = m[7] == "open or interrupted"
            current["ended"] = m[2]
        elif m := GAPS_LINE.match(line):
            current["gaps"] = int(m[1])
            current["missing"] = round(float(m[2]) * DURATION[m[3]], 3)
        elif m := GAP_LINE.match(line):
            # "read failed: IO error: ..." → "read failed"
            reason = m[5].split(":")[0]
            current["reasons"][reason] = current["reasons"].get(reason, 0) + 1
    return streams


class RecordingGaps:
    """jrxd's own accounting of messages and gaps. It decodes every file
    (about a minute of CPU per day on the Q6600), so today's is redone on a
    long interval, at the lowest CPU and disk priority (jrxd writing comes
    first), and a finished day is read once."""

    def __init__(self):
        self.finished = {}

    def __call__(self, cfg):
        root = cfg["jrxd"]["dir"]
        now = dt.datetime.now(dt.timezone.utc)
        today = now.date().isoformat()
        yesterday = (now.date() - dt.timedelta(days=1)).isoformat()

        def listing(day):
            return parse_jrxd_list(run(
                "ionice", "-c", "3", "nice", "-n", "19", "jrxd", "list",
                root, day, timeout=900))

        # A day's files are all closed a little after midnight UTC
        if yesterday not in self.finished and now.hour >= 1:
            self.finished = {yesterday: listing(yesterday)}
        return {"today": {"day": today, "streams": listing(today)},
                "yesterday": {"day": yesterday,
                              "streams": self.finished.get(yesterday)}}


# The media apps

class Arr:
    """Sonarr's or Radarr's v3 API."""

    def __init__(self, url, key):
        self.url, self.headers = url.rstrip("/") + "/api/v3", {
            "X-Api-Key": key}

    def get(self, path, **params):
        query = urllib.parse.urlencode(params)
        return fetch(f"{self.url}{path}?{query}", self.headers)


def queue_item(record, title):
    size, left = record.get("size") or 0, record.get("sizeleft") or 0
    messages = [m for s in record.get("statusMessages") or []
                for m in s.get("messages") or []]
    return {"title": title, "status": record.get("status"),
            "state": record.get("trackedDownloadState"),
            "health": record.get("trackedDownloadStatus"),
            "progress": round(1 - left / size, 3) if size else None,
            "eta": record.get("timeleft"),
            "problem": (messages or [record.get("errorMessage")])[0]}


def episode_code(e):
    return f"S{e['seasonNumber']:02}E{e['episodeNumber']:02}"


def sonarr(cfg):
    key = credential(cfg["sonarr"].get("key_credential", "sonarr"))
    if not key:
        return None
    s = Arr(cfg["sonarr"]["url"], key)
    today = dt.date.today()
    calendar = s.get("/calendar", start=today - dt.timedelta(days=1),
                     end=today + dt.timedelta(days=8), includeSeries="true")
    history = s.get("/history/since", date=today - dt.timedelta(days=2),
                    eventType="downloadFolderImported",
                    includeSeries="true", includeEpisode="true")
    queue = s.get("/queue", pageSize=20, includeSeries="true",
                  includeEpisode="true")
    return {
        "upcoming": [{
            "show": e["series"]["title"], "code": episode_code(e),
            "title": e.get("title"), "airs": e.get("airDateUtc"),
            "downloaded": e.get("hasFile", False)}
            for e in calendar if e.get("monitored", True)],
        "imported": [{
            "show": h["series"]["title"], "code": episode_code(h["episode"]),
            "title": h["episode"].get("title"), "at": h["date"],
            "quality": h["quality"]["quality"]["name"]}
            for h in history if "series" in h and "episode" in h][-20:],
        "queue": [queue_item(r, f"{r.get('series', {}).get('title', '?')} "
                                f"{episode_code(r['episode'])}"
                             if "episode" in r else r.get("title"))
                  for r in queue.get("records", [])],
        "missing": s.get("/wanted/missing", pageSize=1)["totalRecords"],
        "health": [{"level": h.get("type"), "message": h.get("message")}
                   for h in s.get("/health")],
    }


def radarr(cfg):
    key = credential(cfg["radarr"].get("key_credential", "radarr"))
    if not key:
        return None
    r = Arr(cfg["radarr"]["url"], key)
    today = dt.date.today()
    calendar = r.get("/calendar", start=today - dt.timedelta(days=1),
                     end=today + dt.timedelta(days=45))
    history = r.get("/history/since", date=today - dt.timedelta(days=2),
                    eventType="downloadFolderImported", includeMovie="true")
    queue = r.get("/queue", pageSize=20, includeMovie="true")
    upcoming = []
    for movie in calendar:
        if movie.get("hasFile") or not movie.get("monitored", True):
            continue
        release = movie.get("digitalRelease") or movie.get("physicalRelease")
        if release and release[:10] >= today.isoformat():
            upcoming.append({"title": movie["title"],
                             "year": movie.get("year"), "release": release})
    return {
        "upcoming": sorted(upcoming, key=lambda m: m["release"]),
        "imported": [{"title": h["movie"]["title"],
                      "year": h["movie"].get("year"), "at": h["date"],
                      "quality": h["quality"]["quality"]["name"]}
                     for h in history if "movie" in h][-20:],
        "queue": [queue_item(q, q.get("movie", {}).get("title")
                             or q.get("title"))
                  for q in queue.get("records", [])],
        "health": [{"level": h.get("type"), "message": h.get("message")}
                   for h in r.get("/health")],
    }


def seerr(cfg):
    settings = credential(cfg["seerr"].get("settings_credential",
                                           "seerr-settings"))
    if not settings:
        return None
    base = cfg["seerr"]["url"].rstrip("/") + "/api/v1"
    headers = {"X-Api-Key": json.loads(settings)["main"]["apiKey"]}
    counts = fetch(base + "/request/count", headers)
    pending = fetch(base + "/request?filter=pending&take=10&sort=added",
                    headers)["results"]
    waiting = []
    for request in pending:
        kind = "movie" if request["type"] == "movie" else "tv"
        try:
            found = fetch(f"{base}/{kind}/{request['media']['tmdbId']}",
                          headers)
            title = found.get("title") or found.get("name")
        except Exception:
            title = None
        waiting.append({
            "title": title or "?", "kind": kind,
            "by": (request.get("requestedBy") or {}).get("displayName"),
            "at": request.get("createdAt"),
            "seasons": [s["seasonNumber"]
                        for s in request.get("seasons") or []]})
    return {"counts": counts, "pending": waiting,
            "url": cfg["seerr"].get("public_url")}


def jellyfin(cfg):
    url = cfg["jellyfin"]["url"].rstrip("/")
    info = fetch(url + "/System/Info/Public")
    data = {"version": info.get("Version"), "name": info.get("ServerName"),
            "url": cfg["jellyfin"].get("public_url"), "playing": None}
    key = credential(cfg["jellyfin"].get("key_credential", "jellyfin"))
    if key:
        headers = {"Authorization": f'MediaBrowser Token="{key}"'}
        sessions = fetch(url + "/Sessions?activeWithinSeconds=900", headers)
        data["playing"] = []
        for s in sessions:
            item = s.get("NowPlayingItem")
            if not item:
                continue
            name = item.get("Name")
            if item.get("SeriesName"):
                name = f"{item['SeriesName']}: {name}"
            ticks = (s.get("PlayState") or {}).get("PositionTicks") or 0
            data["playing"].append({
                "title": name, "user": s.get("UserName"),
                "client": s.get("Client"), "device": s.get("DeviceName"),
                "method": (s.get("PlayState") or {}).get("PlayMethod"),
                "paused": (s.get("PlayState") or {}).get("IsPaused"),
                "progress": round(ticks / item["RunTimeTicks"], 3)
                if item.get("RunTimeTicks") else None})
    return data


def media(cfg):
    """All of the media apps at once; one being away doesn't hide the
    others."""
    parts = {"sonarr": sonarr, "radarr": radarr, "seerr": seerr,
             "jellyfin": jellyfin}
    data, errors = {}, {}
    with ThreadPoolExecutor(len(parts)) as pool:
        futures = {name: pool.submit(f, cfg) for name, f in parts.items()
                   if name in cfg}
        for name, future in futures.items():
            try:
                data[name] = future.result()
            except Exception as e:
                data[name], errors[name] = None, describe(e)
    data["errors"] = errors
    return data


def describe(error):
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP {error.code} from {error.url.split('?')[0]}"
    if isinstance(error, subprocess.CalledProcessError):
        return (error.stderr or str(error)).strip()[-300:]
    return f"{type(error).__name__}: {error}"[:300]


# The calendar, from Radicale over CalDAV

CALDAV = """<?xml version="1.0" encoding="utf-8"?>
<C:calendar-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:prop><C:calendar-data><C:expand start="{start}" end="{end}"/>
  </C:calendar-data></D:prop>
  <C:filter><C:comp-filter name="VCALENDAR"><C:comp-filter name="VEVENT">
    <C:time-range start="{start}" end="{end}"/>
  </C:comp-filter></C:comp-filter></C:filter>
</C:calendar-query>"""
NS = {"D": "DAV:", "C": "urn:ietf:params:xml:ns:caldav"}


def ical_time(prop, value, zone):
    """An iCalendar DTSTART/DTEND → (aware datetime, all day?)."""
    params = dict(p.split("=", 1) for p in prop.split(";")[1:] if "=" in p)
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        day = dt.datetime.strptime(value, "%Y%m%d")
        return day.replace(tzinfo=zone), True
    when = dt.datetime.strptime(value.rstrip("Z"), "%Y%m%dT%H%M%S")
    if value.endswith("Z"):
        return when.replace(tzinfo=dt.timezone.utc), False
    try:
        tz = ZoneInfo(params["TZID"]) if "TZID" in params else zone
    except Exception:
        tz = zone
    return when.replace(tzinfo=tz), False


def unescape(text):
    return (text.replace("\\n", "\n").replace("\\N", "\n")
            .replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\"))


def parse_events(ics, zone):
    """VEVENTs of an iCalendar text (already expanded by the server)."""
    lines = re.sub(r"\r?\n[ \t]", "", ics).splitlines()
    events, event = [], None
    for line in lines:
        if line == "BEGIN:VEVENT":
            event = {}
        elif line == "END:VEVENT" and event is not None:
            if "start" in event and event.get("status") != "CANCELLED":
                if "end" not in event:
                    event["end"] = event["start"] + (
                        dt.timedelta(days=1) if event["all_day"]
                        else dt.timedelta(0))
                events.append(event)
            event = None
        elif event is not None and ":" in line:
            prop, value = line.split(":", 1)
            name = prop.split(";")[0].upper()
            if name == "DTSTART":
                event["start"], event["all_day"] = ical_time(prop, value,
                                                             zone)
            elif name == "DTEND":
                event["end"], _ = ical_time(prop, value, zone)
            elif name == "SUMMARY":
                event["title"] = unescape(value)
            elif name == "LOCATION":
                event["location"] = unescape(value)
            elif name == "STATUS":
                event["status"] = value.upper()
    return events


def calendar(cfg):
    cal = cfg["calendar"]
    password = credential(cal.get("password_credential", "radicale"))
    if not password:
        raise RuntimeError("no Radicale password (the radicale credential)")
    zone = ZoneInfo(cfg.get("timezone", "Europe/Lisbon"))
    now = dt.datetime.now(zone)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + dt.timedelta(days=cal.get("days", 8))
    stamp = "%Y%m%dT%H%M%SZ"
    body = CALDAV.format(start=start.astimezone(dt.timezone.utc).strftime(
        stamp), end=end.astimezone(dt.timezone.utc).strftime(stamp))
    auth = base64.b64encode(f"{cal['user']}:{password}".encode()).decode()
    headers = {"Authorization": "Basic " + auth, "Depth": "1",
               "Content-Type": "application/xml; charset=utf-8",
               "Accept": "application/xml"}
    # Expanding a week of a calendar can take Radicale on aurea's Q6600
    # longer than fetch's usual 20 s
    timeout = cal.get("timeout", 120)
    events = []
    for name, about in cal["calendars"].items():
        url = cal["url"].rstrip("/") + f"/{name}/"
        reply = fetch(url, headers, body.encode(), "REPORT", timeout=timeout,
                      raw=True)
        for data in ET.fromstring(reply).iterfind(".//C:calendar-data", NS):
            for event in parse_events(data.text or "", zone):
                if event["end"] <= start or event["start"] >= end:
                    continue
                events.append({
                    "calendar": name, "label": about.get("label", name),
                    "kind": about.get("kind"),
                    "title": event.get("title", "(no title)"),
                    "location": event.get("location"),
                    "all_day": event["all_day"],
                    "start": event["start"].astimezone(zone).isoformat(),
                    "end": event["end"].astimezone(zone).isoformat()})
    events.sort(key=lambda e: (e["start"], not e["all_day"]))
    return {"events": events, "from": start.isoformat(),
            "to": end.isoformat()}
