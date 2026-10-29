"""What needs looking at, from a snapshot's sections: each rule turns state
into items of "bad" (broken now), "warn" (will be, or needs a decision) or
"info" (worth knowing today). Yesterday's snapshot lets counters that only
matter when they grow (SMART) be compared.
"""

import datetime as dt

LEVELS = {"bad": 0, "warn": 1, "info": 2}
AREAS = {"machine": "Server", "units": "Services", "reach": "Services",
         "recordings": "Recorder", "gaps": "Recorder", "media": "Media",
         "calendar": "Calendar", "github": "Projects", "repos": "Projects",
         "laptop": "Laptop", "tailnet": "Server"}


def ago(seconds):
    seconds = max(0, int(seconds))
    if seconds < 90:
        return f"{seconds} s"
    if seconds < 5400:
        return f"{round(seconds / 60)} min"
    if seconds < 36 * 3600:
        return f"{round(seconds / 3600)} h"
    return f"{round(seconds / 86400)} days"


def plural(n, word, words=None):
    return f"{n} {word if n == 1 else words or word + 's'}"


def parse_time(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


class Rules:
    def __init__(self, sections, yesterday, now, cfg):
        self.s = sections
        self.yesterday = yesterday or {}
        self.now = now
        self.cfg = cfg
        self.out = []

    def add(self, level, area, title, detail=None, url=None):
        self.out.append({"level": level, "area": area, "title": title,
                         "detail": detail, "url": url})

    def data(self, name):
        section = self.s.get(name) or {}
        return section.get("data") if section.get("ok") else None

    def run(self):
        for name, section in self.s.items():
            if section and not section.get("ok") and section.get("error"):
                self.add("warn", AREAS.get(name, name.title()),
                         f"Couldn't read {name}", section["error"])
        for rule in (self.machine, self.units, self.reach, self.recorder,
                     self.media, self.calendar, self.github, self.repos,
                     self.laptop):
            rule()
        return sorted(self.out, key=lambda i: LEVELS[i["level"]])

    def machine(self):
        m = self.data("machine")
        if not m:
            return
        limits = self.cfg.get("limits", {})
        for disk in m["disks"]:
            if not disk.get("mounted"):
                self.add("bad", "Server", f"{disk['name']} isn't mounted",
                         disk["path"])
                continue
            used = disk.get("used") or 0
            free = f"{disk['free'] / 1e9:.0f} GB free"
            if used >= limits.get("disk_bad", 0.93):
                self.add("bad", "Server",
                         f"{disk['name']} is {used:.0%} full", free)
            elif used >= limits.get("disk_warn", 0.85):
                self.add("warn", "Server",
                         f"{disk['name']} is {used:.0%} full", free)
        memory = (m.get("memory") or {}).get("now")
        if memory and memory >= limits.get("memory_warn", 0.92):
            self.add("warn", "Server", f"Memory is {memory:.0%} used")
        temp = (m.get("temperature") or {}).get("now")
        if temp and temp >= limits.get("temperature_warn", 80):
            self.add("warn", "Server", f"CPU at {temp:.0f} °C")

        before = {d.get("device"): d for d in
                  ((self.yesterday.get("machine") or {}).get("smart") or [])}
        for disk in m.get("smart", []):
            name = disk.get("name", disk.get("device"))
            if disk.get("passed") is False:
                self.add("bad", "Server",
                         f"The {name} disk FAILED its SMART check",
                         "Back it up and replace it.")
            old = (before.get(disk.get("device")) or {}).get("counters", {})
            for key, counter in (disk.get("counters") or {}).items():
                was = (old.get(key) or {}).get("value")
                if was is not None and counter["value"] > was:
                    hint = ("SATA cable or port" if key == "199"
                            else "the drive is growing bad sectors")
                    self.add("warn", "Server",
                             f"{name} disk: {counter['name']} went "
                             f"{was} → {counter['value']}", hint)

    def units(self):
        u = self.data("units")
        if not u:
            return
        for alert in u["alerts"]:
            title, _, detail = alert["message"].partition("\n")
            self.add("bad", "Services", title.removeprefix("❌ "),
                     detail.strip()[:300] or None)
        alerted = {a["key"] for a in u["alerts"]}
        for unit in u["failed"]:
            if f"failed-{unit}" not in alerted:
                self.add("bad", "Services", f"{unit} has failed")
        for service in u["services"]:
            if service["active"] not in ("active", "reloading") \
                    and f"{service['unit']}" not in u["failed"]:
                self.add("bad", "Services", f"{service['name']} is "
                         f"{service['active']} ({service['sub']})")
        for job in u["jobs"]:
            if job["running"]:
                continue
            if job["result"] not in ("success", None):
                self.add("bad", "Services",
                         f"{job['name']} failed its last run",
                         f"{job['unit']}.service: {job['result']}")
            limit = job.get("max_age_hours")
            last = (job.get("finished") or job.get("started")
                    or job.get("triggered"))
            if limit and last and self.now - last > limit * 3600:
                self.add("warn", "Services",
                         f"{job['name']} hasn't run in {ago(self.now - last)}")

    def reach(self):
        r = self.data("reach")
        for endpoint in (r or {}).get("endpoints", []):
            if not endpoint["ok"]:
                why = (f"HTTP {endpoint['code']}" if endpoint.get("code")
                       else endpoint.get("error"))
                self.add("bad", "Services",
                         f"{endpoint['name']} is unreachable from the "
                         "internet", why, endpoint["url"])

    def recorder(self):
        r = self.data("recordings")
        if not r:
            return
        if r["active"] != "active":
            self.add("bad", "Recorder", f"jrxd is {r['active']}",
                     "No market data is being recorded.")
            return
        stalled = [s["id"] for s in r["streams"]
                   if s["idle"] is None or s["idle"] > self.cfg.get(
                       "limits", {}).get("stream_idle", 300)]
        if stalled:
            self.add("bad", "Recorder",
                     f"{plural(len(stalled), 'stream')} stopped writing",
                     ", ".join(stalled))
        g = self.data("gaps")
        limit = self.cfg.get("limits", {}).get("missing_seconds", 120)
        for label, key in (("today", "today"), ("yesterday", "yesterday")):
            streams = ((g or {}).get(key) or {}).get("streams") or {}
            missing = {sid: s["missing"] for sid, s in streams.items()
                       if s["missing"] > limit}
            if missing:
                worst = max(missing, key=missing.get)
                self.add("warn", "Recorder",
                         f"Recording gaps {label}: "
                         f"{plural(len(missing), 'stream')} missed over "
                         f"{limit} s",
                         f"worst {worst}: {ago(missing[worst])} missing")

    def media(self):
        m = self.data("media")
        if not m:
            return
        for app in ("sonarr", "radarr"):
            part = m.get(app) or {}
            for h in part.get("health", []):
                if h["level"] in ("warning", "error"):
                    self.add("warn", "Media", f"{app.title()}: {h['message']}")
            for item in part.get("queue", []):
                if item["health"] in ("warning", "error") or \
                        item["state"] in ("importBlocked", "failedPending"):
                    self.add("warn", "Media",
                             f"Download stuck: {item['title']}",
                             item.get("problem"))
        seerr = m.get("seerr") or {}
        if seerr.get("pending"):
            names = ", ".join(p["title"] for p in seerr["pending"][:4])
            self.add("info", "Media",
                     f"{plural(len(seerr['pending']), 'request')} waiting "
                     "for approval", names, seerr.get("url"))
        for name, error in (m.get("errors") or {}).items():
            self.add("warn", "Media", f"Couldn't reach {name.title()}",
                     error)

    def calendar(self):
        c = self.data("calendar")
        if not c:
            return
        today = dt.datetime.fromtimestamp(self.now).date()
        for event in c["events"]:
            if event.get("kind") != "evaluation":
                continue
            day = dt.date.fromisoformat(event["start"][:10])
            days = (day - today).days
            if days < 0:
                continue
            when = {0: "today", 1: "tomorrow"}.get(days, f"in {days} days")
            self.add("warn" if days <= 2 else "info", "Calendar",
                     f"{event['title']} {when}",
                     f"{event['label']} · {day:%a %d %b}")

    def github(self):
        g = self.data("github")
        if not g:
            return
        for repo in g.get("repos", []):
            if repo.get("ci") in ("FAILURE", "ERROR"):
                self.add("bad", "Projects",
                         f"CI is failing on {repo['name']} "
                         f"({repo.get('branch')})",
                         repo.get("head"), repo.get("url"))
        for pr in g.get("review_requests", []):
            self.add("warn", "Projects", f"Review requested: {pr['title']}",
                     f"{pr['repo']}#{pr['number']}", pr["url"])
        for pr in g.get("my_prs", []):
            if pr.get("ci") in ("FAILURE", "ERROR"):
                self.add("warn", "Projects",
                         f"Checks failing on your PR: {pr['title']}",
                         f"{pr['repo']}#{pr['number']}", pr["url"])
            elif 7 < (self.now - parse_time(pr["updated"])) / 86400 < 90:
                self.add("info", "Projects",
                         f"Your PR has been quiet for "
                         f"{ago(self.now - parse_time(pr['updated']))}: "
                         f"{pr['title']}", f"{pr['repo']}#{pr['number']}",
                         pr["url"])
        bots = {}
        for repo in g.get("repos", []):
            for pr in repo.get("prs", []):
                if pr.get("bot"):
                    bots.setdefault(repo["name"], []).append(pr)
        if bots:
            count = sum(len(v) for v in bots.values())
            where = ", ".join(f"{name.split('/')[-1]} ({len(prs)})"
                              for name, prs in bots.items())
            self.add("info", "Projects",
                     f"{plural(count, 'pull request')} from bots waiting",
                     where)

    def repos(self):
        r = self.data("repos")
        if not r:
            return
        unpushed = [x for x in r if x.get("ahead")]
        dirty = [x for x in r if x.get("changed")]
        if unpushed:
            self.add("info", "Projects",
                     f"Unpushed commits in {plural(len(unpushed), 'repo')}",
                     ", ".join(f"{x['name']} ({x['ahead']})"
                               for x in unpushed))
        if dirty:
            self.add("info", "Projects",
                     f"Uncommitted changes in {plural(len(dirty), 'repo')}",
                     ", ".join(f"{x['name']} ({x['changed']})"
                               for x in dirty))

    def laptop(self):
        section = self.s.get("laptop") or {}
        if not section.get("updated"):
            return
        age = self.now - section["updated"]
        if age > 12 * 3600:
            self.add("info", "Laptop",
                     f"The laptop last reported {ago(age)} ago",
                     "Projects and GitHub are as of then.")
        lap = self.data("laptop")
        if not lap:
            return
        for unit in lap.get("failed", []):
            self.add("warn", "Laptop", f"{unit} failed on the laptop")
        backup = lap.get("backup")
        if backup:
            if backup.get("result") not in ("success", None):
                self.add("bad", "Laptop",
                         "The laptop's backup of aurea failed",
                         f"aurea-backup.service: {backup['result']}")
            last = backup.get("finished")
            if last and self.now - last > 3 * 86400:
                self.add("warn", "Laptop",
                         f"No backup of aurea pulled in "
                         f"{ago(self.now - last)}")


def items(sections, yesterday, now, cfg):
    return Rules(sections, yesterday, now, cfg).run()


def overall(found):
    for level in ("bad", "warn"):
        if any(i["level"] == level for i in found):
            return level
    return "ok"
