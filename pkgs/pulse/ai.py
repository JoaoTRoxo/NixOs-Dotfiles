"""The AI parts of Pulse: the morning brief, and questions about the
dashboard. Both give Claude the snapshot (without the charts' series) and
yesterday's, and ask it to say only what the data shows.

With an API key ("anthropic" credential) the server calls the Claude API
itself; without one, the laptop agent writes the brief with Claude Code
from the same prompt (brief_prompt) and sends it back.
"""

import datetime as dt
import json

BETAS = ["server-side-fallback-2026-07-01"]

ABOUT = """\
Pulse is {name}'s personal dashboard. It collects, every few minutes:
- aurea, their NixOS homeserver (a 2008 Core 2 Quad Q6600 with 8 GB RAM, a \
1 TB system disk and a 500 GB disk for market-data recordings): CPU, memory, \
disks, temperature, SMART, systemd units and timers, the server's own \
Telegram alerts, and whether the public *.joaoroxo.com addresses answer \
through the Cloudflare tunnel.
- jrxd, the recorder from their JRX project that captures public Binance \
and Hyperliquid BTC market data around the clock (bytes per stream, and \
jrxd's own count of messages and gaps per UTC day).
- The media stack: Jellyfin, Sonarr (episodes), Radarr (movies), Seerr \
(requests from family and friends).
- Their calendar (synced from iCloud; "evaluations" are university exams \
and tests at Instituto Superior Técnico).
- Their GitHub (repos, CI, pull requests, notifications) and the git repos \
on their laptop (uncommitted and unpushed work), reported by the laptop.

Times in the data are ISO 8601 or unix seconds; sizes are bytes; ratios \
(cpu, memory, disk "used") are 0-1. "attention" is the dashboard's own list \
of problems, from fixed rules. "yesterday" is the last snapshot of \
yesterday, for comparison; it may be missing."""

BRIEF = """\
{about}

Write the morning brief: what {name} should know today, read in under a \
minute.

- Start with one line that sums up the day (e.g. "All quiet: nothing \
needs you." or "Two things need you: …").
- Then the few things that need attention or a decision, most important \
first, each with the concrete detail (numbers, names) and what to do if \
it's obvious. Fold related items into one.
- Then a short "Today" with the calendar, and anything new since yesterday \
worth knowing (episodes or movies that arrived, progress on projects, the \
recorder's yesterday).
- Leave out what is normal and unchanged. Don't list everything that's fine.
- Use only what the data shows; never guess causes as facts. If a source \
couldn't be read, say so in a few words.
- Markdown: short paragraphs and bullet lists, a few **bold** words, no \
headings above ###, no tables. Under 250 words. English."""

ASK = """\
{about}

{name} is asking about what's on the dashboard. Answer from the data below, \
concisely, with the numbers. If the data doesn't say, tell them so and \
where they could look (a command on aurea, a log, an app). Markdown, no \
headings above ###.

Now: {now}

<snapshot>
{snapshot}
</snapshot>

<yesterday>
{yesterday}
</yesterday>"""


def trimmed(value, depth=0):
    """The snapshot without the charts' series and with long lists cut,
    so it costs fewer tokens and stays readable."""
    if isinstance(value, dict):
        return {k: trimmed(v, depth + 1) for k, v in value.items()
                if k not in ("series", "took") and v not in (None, [], {})}
    if isinstance(value, list):
        return [trimmed(v, depth + 1) for v in value[:25]]
    if isinstance(value, float):
        return round(value, 3)
    return value


def compact(snapshot):
    """What's kept of a snapshot for yesterday's comparison and the
    prompts: each section's data (or error), the attention list."""
    sections = {}
    for name, section in snapshot.get("sections", {}).items():
        if not section:
            continue
        if section.get("ok"):
            sections[name] = trimmed(section.get("data"))
        else:
            sections[name] = {"error": section.get("error")}
        if section.get("updated"):
            sections.setdefault("_updated", {})[name] = int(
                section["updated"])
    return {"at": snapshot.get("generated"), "sections": sections,
            "attention": snapshot.get("attention", [])}


def context(snapshot, yesterday, cfg, now):
    name = cfg.get("name", "the user")
    return {
        "about": ABOUT.format(name=name), "name": name,
        "now": now.strftime("%A %d %B %Y, %H:%M %Z"),
        "snapshot": json.dumps(compact(snapshot), ensure_ascii=False,
                               separators=(",", ":")),
        "yesterday": json.dumps(yesterday, ensure_ascii=False,
                                separators=(",", ":"))
        if yesterday else "(none)"}


def brief_prompt(snapshot, yesterday, cfg, now):
    """(system, user) for the morning brief."""
    c = context(snapshot, yesterday, cfg, now)
    user = (f"Now: {c['now']}\n\n<snapshot>\n{c['snapshot']}\n</snapshot>"
            f"\n\n<yesterday>\n{c['yesterday']}\n</yesterday>\n\n"
            "Write today's brief.")
    return BRIEF.format(**c), user


def ask_system(snapshot, yesterday, cfg, now):
    return ASK.format(**context(snapshot, yesterday, cfg, now))


class Claude:
    """The Claude API, through the official SDK (imported only when there's
    a key, so the rest of Pulse runs without it)."""

    def __init__(self, key, model, effort):
        import anthropic
        self.anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=key, timeout=300.0)
        self.model, self.effort = model, effort

    def options(self, system):
        return dict(
            model=self.model, max_tokens=16000, betas=BETAS, system=system,
            output_config={"effort": self.effort},
            # On a safety decline, the API reruns the request on the model
            # Anthropic recommends for that kind of decline
            extra_body={"fallbacks": "default"})

    def brief(self, system, user):
        """(text, model that wrote it)"""
        response = self.client.beta.messages.create(
            **self.options(system),
            messages=[{"role": "user", "content": user}])
        if response.stop_reason == "refusal":
            raise RuntimeError("Claude declined to write the brief")
        text = "".join(b.text for b in response.content if b.type == "text")
        return text.strip(), response.model

    def ask(self, system, messages):
        """The answer's text as it's written. The system prompt (with the
        snapshot) stays the same for a conversation, so it's cached."""
        with self.client.beta.messages.stream(
                **self.options(system), messages=messages,
                cache_control={"type": "ephemeral"}) as stream:
            for text in stream.text_stream:
                yield text
            final = stream.get_final_message()
        if final.stop_reason == "refusal":
            yield "\n\n*(Claude declined to answer this.)*"
        elif final.stop_reason == "max_tokens":
            yield "\n\n*(cut off: the answer hit the length limit)*"


def today(cfg):
    from zoneinfo import ZoneInfo
    return dt.datetime.now(ZoneInfo(cfg.get("timezone", "Europe/Lisbon")))
