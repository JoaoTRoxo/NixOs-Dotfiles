#!/usr/bin/env python3
"""Pulse's laptop agent: reports to the dashboard on aurea what only the
laptop knows. That's its git repos (uncommitted and unpushed work), GitHub
(through the laptop's `gh` login, so the server needs no token), and the
laptop itself (battery, disks, failed units, its pull of aurea's backup).

When the server can't write the morning brief itself (no API key), the agent
writes it with Claude Code (`claude -p`, the laptop's own login) from the
prompt the server hands it, and sends it back.

Runs from a systemd user timer every 15 minutes (pulse-agent-install.sh).
Config: ~/.config/pulse/agent.json, all optional:
  {"server": "http://100.124.208.1:8484", "repos": ["~", "~/TIC"],
   "claude": "claude", "claude_model": null}

  pulse-agent           collect, send, and write the brief if it's due
  pulse-agent --print   collect and print, send nothing
  pulse-agent --brief   write the brief now, even if there is one today
"""

import datetime as dt
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

CONFIG = os.path.expanduser("~/.config/pulse/agent.json")
DEFAULTS = {"server": "http://100.124.208.1:8484", "repos": ["~", "~/TIC"],
            "claude": "claude", "claude_model": None}


def run(*command, cwd=None, timeout=60, check=True):
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                          timeout=timeout, check=check).stdout


# Git repos

def remote_name(url):
    """git@github.com:owner/name.git or https://github.com/owner/name →
    owner/name (GitHub only)."""
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?/?$", url or "")
    return m[1] if m else None


def repo_state(path):
    status = run("git", "status", "--porcelain=v2", "--branch",
                 "--untracked-files=normal", cwd=path)
    state = {"name": os.path.basename(path), "path": path, "branch": None,
             "upstream": None, "ahead": 0, "behind": 0, "changed": 0,
             "untracked": 0}
    for line in status.splitlines():
        if line.startswith("# branch.head "):
            head = line.split(" ", 2)[2]
            state["branch"] = None if head == "(detached)" else head
        elif line.startswith("# branch.upstream "):
            state["upstream"] = line.split(" ", 2)[2]
        elif line.startswith("# branch.ab "):
            ahead, behind = line.split()[2:4]
            state["ahead"], state["behind"] = int(ahead), -int(behind)
        elif line.startswith("? "):
            state["untracked"] += 1
            state["changed"] += 1
        elif line[:2] in ("1 ", "2 ", "u "):
            state["changed"] += 1
    last = run("git", "log", "-1", "--format=%ct%x00%s", cwd=path,
               check=False).strip()
    if last:
        stamp, _, subject = last.partition("\0")
        state["last_commit"] = {"at": int(stamp), "subject": subject[:120]}
    if not state["upstream"]:
        # A branch never pushed: count its commits not on any remote branch
        local = run("git", "rev-list", "--count", "HEAD", "--not",
                    "--remotes", cwd=path, check=False).strip()
        if local.isdigit() and run("git", "remote", cwd=path).strip():
            state["ahead"] = int(local)
    stash = run("git", "stash", "list", cwd=path, check=False)
    state["stashes"] = len(stash.splitlines())
    url = run("git", "remote", "get-url", "origin", cwd=path,
              check=False).strip()
    state["remote"] = remote_name(url)
    fetched = os.path.join(path, ".git", "FETCH_HEAD")
    if os.path.exists(fetched):
        state["fetched"] = int(os.stat(fetched).st_mtime)
    return state


def repos(roots):
    """The repos directly inside each root (not the roots themselves: the
    home directory is a repo too)."""
    found = []
    for root in roots:
        root = os.path.expanduser(root)
        try:
            names = sorted(os.listdir(root))
        except OSError:
            continue
        for name in names:
            path = os.path.join(root, name)
            if name.startswith(".") or not os.path.isdir(
                    os.path.join(path, ".git")):
                continue
            try:
                found.append(repo_state(path))
            except (subprocess.SubprocessError, OSError) as e:
                found.append({"name": name, "path": path,
                              "error": str(e)[:200]})
    return found


# GitHub, shared with the server (which uses a token instead of gh)

GITHUB_QUERY = """
fragment Checks on Commit { statusCheckRollup { state } }
fragment PR on PullRequest {
  number title url isDraft createdAt updatedAt reviewDecision
  author { login __typename }
  repository { nameWithOwner }
  commits(last: 1) { nodes { commit { ...Checks } } }
}
fragment Repo on Repository {
  nameWithOwner url isPrivate isArchived isFork pushedAt description
  owner { login }
  primaryLanguage { name }
  defaultBranchRef { name target { ... on Commit {
    committedDate messageHeadline ...Checks } } }
  pullRequests(states: OPEN, first: 10,
               orderBy: {field: UPDATED_AT, direction: DESC}) {
    totalCount nodes { ...PR } }
  issues(states: OPEN) { totalCount }
  milestones(states: OPEN, first: 5,
             orderBy: {field: DUE_DATE, direction: ASC}) {
    nodes { title url dueOn progressPercentage
      open: issues(states: OPEN) { totalCount }
      closed: issues(states: CLOSED) { totalCount } } }
  latestRelease { tagName publishedAt }
}
query($repos: Int!) {
  viewer {
    login
    organizations(first: 20) { nodes { login } }
    repositories(first: $repos, ownerAffiliations: [OWNER],
                 orderBy: {field: PUSHED_AT, direction: DESC}) {
      nodes { ...Repo } }
    repositoriesContributedTo(first: 10, includeUserRepositories: false,
        contributionTypes: [COMMIT, PULL_REQUEST, ISSUE],
        orderBy: {field: PUSHED_AT, direction: DESC}) {
      nodes { ...Repo } }
  }
  mine: search(query: "is:pr is:open author:@me archived:false",
               type: ISSUE, first: 20) { nodes { ...PR } }
  review: search(query: "is:pr is:open review-requested:@me archived:false",
                 type: ISSUE, first: 20) { nodes { ...PR } }
  assigned: search(query: "is:issue is:open assignee:@me archived:false",
                   type: ISSUE, first: 20) {
    nodes { ... on Issue { number title url updatedAt
      repository { nameWithOwner } } } }
}
"""


def checks(commit):
    return ((commit or {}).get("statusCheckRollup") or {}).get("state")


def pull_request(pr):
    commits = (pr.get("commits") or {}).get("nodes") or [{}]
    author = pr.get("author") or {}
    return {"number": pr["number"], "title": pr["title"], "url": pr["url"],
            "repo": pr["repository"]["nameWithOwner"],
            "draft": pr["isDraft"], "created": pr["createdAt"],
            "updated": pr["updatedAt"], "review": pr.get("reviewDecision"),
            "author": author.get("login"),
            "bot": author.get("__typename") == "Bot",
            "ci": checks(commits[0].get("commit"))}


def repository(r):
    branch = r.get("defaultBranchRef") or {}
    head = branch.get("target") or {}
    return {
        "name": r["nameWithOwner"], "url": r["url"],
        "private": r["isPrivate"], "pushed": r["pushedAt"],
        "description": r.get("description") or None,
        "language": (r.get("primaryLanguage") or {}).get("name"),
        "branch": branch.get("name"), "ci": checks(head),
        "head": head.get("messageHeadline"),
        "committed": head.get("committedDate"),
        "prs": [pull_request(p) for p in r["pullRequests"]["nodes"]],
        "open_prs": r["pullRequests"]["totalCount"],
        "issues": r["issues"]["totalCount"],
        "milestones": [{
            "title": m["title"], "url": m["url"], "due": m.get("dueOn"),
            "progress": m.get("progressPercentage"),
            "open": m["open"]["totalCount"],
            "closed": m["closed"]["totalCount"]}
            for m in r["milestones"]["nodes"]],
        "release": (r.get("latestRelease") or {}).get("tagName")}


def github(graphql, notifications=None, count=12, now=None):
    """GitHub as Pulse shows it. `graphql(query, variables)` returns the
    reply's data; `notifications()` the unread notifications (REST)."""
    now = now or time.time()
    data = graphql(GITHUB_QUERY, {"repos": count})
    viewer = data["viewer"]
    orgs = {o["login"] for o in viewer["organizations"]["nodes"]}
    repos = [r for r in viewer["repositories"]["nodes"]
             if not r["isArchived"]]
    # Other people's repos only while there's activity, org repos always
    for r in viewer["repositoriesContributedTo"]["nodes"]:
        recent = now - iso_seconds(r["pushedAt"]) < 14 * 86400
        if not r["isArchived"] and (r["owner"]["login"] in orgs or recent):
            repos.append(r)
    out = {"login": viewer["login"],
           "repos": [repository(r) for r in repos],
           "my_prs": [pull_request(p) for p in data["mine"]["nodes"] if p],
           "review_requests": [pull_request(p)
                               for p in data["review"]["nodes"] if p],
           "assigned": [{"number": i["number"], "title": i["title"],
                         "url": i["url"], "updated": i["updatedAt"],
                         "repo": i["repository"]["nameWithOwner"]}
                        for i in data["assigned"]["nodes"] if i]}
    if notifications:
        unread = notifications()
        out["notifications"] = {"unread": len(unread), "latest": [{
            "reason": n.get("reason"), "repo": n["repository"]["full_name"],
            "title": n["subject"]["title"], "type": n["subject"]["type"],
            "updated": n.get("updated_at")} for n in unread[:10]]}
    return out


def iso_seconds(value):
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def gh_graphql(query, variables):
    args = ["gh", "api", "graphql", "-f", "query=" + query]
    for key, value in variables.items():
        args += ["-F", f"{key}={value}"]
    reply = json.loads(run(*args, timeout=60))
    if reply.get("errors"):
        raise RuntimeError("; ".join(e.get("message", "?")
                                     for e in reply["errors"]))
    return reply["data"]


def gh_notifications():
    return json.loads(run("gh", "api", "notifications?per_page=50",
                          timeout=60))


# The laptop itself

def read(path, default=None):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def os_name():
    for line in (read("/etc/os-release") or "").splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip('"')
    return None


def user_unit(unit, properties):
    text = run("systemctl", "--user", "show", "--timestamp=unix",
               "--property=" + ",".join(properties), unit, check=False)
    values = {}
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if value.startswith("@") and value[1:].isdigit():
            value = int(value[1:])
        values[key] = value
    return values


def laptop():
    data = {"host": socket.gethostname(), "os": os_name(),
            "kernel": os.uname().release,
            "uptime": float((read("/proc/uptime") or "0").split()[0]),
            "load": list(os.getloadavg())}
    for battery in sorted(os.listdir("/sys/class/power_supply")):
        base = f"/sys/class/power_supply/{battery}"
        if read(f"{base}/type") == "Battery":
            data["battery"] = {"percent": int(read(f"{base}/capacity", 0)),
                               "status": read(f"{base}/status")}
            break
    data["disks"] = []
    seen = set()
    for mount in ("/", "/home"):
        device = os.stat(mount).st_dev
        if device in seen:
            continue
        seen.add(device)
        usage = shutil.disk_usage(mount)
        data["disks"].append({"path": mount, "size": usage.total,
                              "free": usage.free})
    failed = run("systemctl", "--user", "list-units", "--state=failed",
                 "--plain", "--no-legend", check=False)
    data["failed"] = [line.split()[0] for line in failed.splitlines()
                      if line.strip()]
    backup = user_unit("aurea-backup.service",
                       ["Result", "ExecMainStartTimestamp",
                        "ExecMainExitTimestamp", "LoadState"])
    if backup.get("LoadState") == "loaded":
        data["backup"] = {"result": backup.get("Result"),
                          "finished": backup.get("ExecMainExitTimestamp")
                          or None}
    return data


# Talking to the server

def call(server, path, body=None, timeout=30):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        server.rstrip("/") + path, data=data,
        headers={"Content-Type": "application/json",
                 "User-Agent": "pulse-agent"})
    with urllib.request.urlopen(request, timeout=timeout) as reply:
        return json.loads(reply.read() or b"null")


def collect(cfg):
    report, errors = {}, {}
    for name, part in (("laptop", laptop),
                       ("repos", lambda: repos(cfg["repos"])),
                       ("github", lambda: github(gh_graphql,
                                                 gh_notifications))):
        try:
            report[name] = part()
        except Exception as e:
            errors[name] = f"{type(e).__name__}: {e}"[:300]
    report["errors"] = errors
    return report


def find_claude(name):
    for candidate in (name, "/opt/bin/claude", "/usr/bin/claude",
                      os.path.expanduser("~/.local/bin/claude")):
        found = shutil.which(candidate)
        if found:
            return found
    return None


def write_brief(cfg, force=False):
    """Write the morning brief with Claude Code if the server wants one."""
    wanted = call(cfg["server"], "/api/brief/request" +
                  ("?force=1" if force else ""))
    if not wanted.get("needed"):
        return wanted.get("reason")
    claude = find_claude(cfg["claude"])
    if not claude:
        return "Claude Code isn't installed here"
    command = [claude, "-p", "--output-format", "json", "--tools", "",
               "--no-session-persistence", "--strict-mcp-config",
               "--system-prompt", wanted["system"]]
    if cfg.get("claude_model"):
        command += ["--model", cfg["claude_model"]]
    done = subprocess.run(command, input=wanted["prompt"],
                          capture_output=True, text=True, timeout=600)
    try:
        reply = json.loads(done.stdout)
    except ValueError:
        raise RuntimeError("claude -p: " + (done.stderr or done.stdout)
                           .strip()[-300:])
    if reply.get("is_error") or not reply.get("result"):
        raise RuntimeError("claude -p: " + str(reply.get("result"))[:300])
    models = list((reply.get("modelUsage") or {}).keys())
    call(cfg["server"], "/api/brief", {
        "day": wanted["day"], "text": reply["result"].strip(),
        "model": models[0] if models else None,
        "source": "laptop (Claude Code)"})
    return "written"


def main(argv):
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG) as f:
            cfg.update(json.load(f))
    except FileNotFoundError:
        pass
    if "--brief" in argv:
        print(write_brief(cfg, force=True))
        return
    report = collect(cfg)
    if "--print" in argv:
        json.dump(report, sys.stdout, indent=1)
        print()
        return
    try:
        call(cfg["server"], "/api/push/laptop", report)
    except OSError as e:
        # aurea being away (or the laptop off the tailnet) isn't the
        # agent failing: try again next time
        print(f"couldn't reach {cfg['server']}: {e}", file=sys.stderr)
        return
    for name, error in report["errors"].items():
        print(f"{name}: {error}", file=sys.stderr)
    try:
        outcome = write_brief(cfg)
        if outcome:
            print("brief:", outcome)
    except Exception as e:
        print(f"brief: {e}", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1:])
