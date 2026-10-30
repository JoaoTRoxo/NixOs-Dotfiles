# Pulse

The daily dashboard: one page that says how everything is going, what needs
attention, and a morning brief written by Claude.

**Open it:** <http://100.124.208.1:8484> from any device on the tailnet
(laptop, iPhone with Tailscale on). It isn't on the LAN or the internet.

## What it shows

| Card | From |
|---|---|
| Morning brief, questions | Claude, from everything below (`ai.py`) |
| Needs attention | fixed rules over everything below (`attention.py`) |
| Calendar | Radicale's copy of the iCloud calendars (CalDAV, expanded) |
| Projects | GitHub: repos, CI on the default branch, PRs, milestones, notifications; plus each repo's local state on the laptop |
| CPU, memory, temperature, network, disks | Prometheus (node and smartctl exporters), 24 h |
| Services, jobs, alerts, tailnet | `systemctl`, server-alerts' open problems, the public addresses through Cloudflare, `tailscale status` |
| Market-data recorder | jrxd's files (bytes per stream and day, still writing?) and `jrxd list` (messages and gaps, every 30 min) |
| Coming up, new, downloads, requests | Sonarr, Radarr, Seerr (and Jellyfin's sessions with a key) |
| Laptop | the laptop agent |

## How it fits together

```
aurea: pulse.service (server.py)          laptop: pulse-agent (agent.py)
  sources.py   collectors, own schedules    every 15 min, systemd user timer
  attention.py rules -> what needs you      git repos, GitHub via `gh`,
  ai.py        brief + questions            battery/disks/backup pull
  pulse.db     laptop report, briefs,  <--  POST /api/push/laptop
               last snapshot of each day    writes the brief with `claude -p`
  web/         the page                <--  when aurea has no API key
```

The brief is written once a day after 06:00. With a Claude API key on aurea,
aurea writes it (and the page can answer questions). Without one, the laptop
agent writes it with Claude Code on the laptop's own login, from the prompt
aurea hands it (`GET /api/brief/request`), and sends it back.

Claude Code can't run on aurea: its binary needs CPU instructions the Q6600
doesn't have. So aurea calls the API directly.

## Setting things up

- Server: `profiles/pulse.nix`, deployed with the rest of aurea.
- Laptop: `pkgs/pulse/pulse-agent-install.sh`. Check it with
  `pulse-agent --print` (collects without sending), and write a brief now
  with `pulse-agent --brief`.
- Optional secrets, picked up once their file exists
  (`cd secrets && agenix -e pulse-anthropic.age`, then rebuild):
  - `pulse-anthropic.age`: a Claude API key (console.anthropic.com). aurea
    writes the brief and answers questions with `claude-opus-5-5`. A brief
    is roughly 8k tokens in and a few thousand out (thinking included),
    around $0.05-0.10.
  - `pulse-github.age`: a GitHub token with read access. aurea then reads
    GitHub itself, even while the laptop is off.
  - `pulse-jellyfin.age`: a Jellyfin API key (Dashboard > API Keys), for
    who's watching what.

## Working on it

Tests (stand-ins on localhost, nothing real touched):

    python3 pkgs/pulse/test_pulse.py
    uv run --with anthropic python3 pkgs/pulse/test_pulse.py   # + the API

To run a copy on aurea next to the real one, point `PULSE_CONFIG` at a
config with another `listen` port, `STATE_DIRECTORY` at a scratch directory,
and `CREDENTIALS_DIRECTORY` at a directory of credential files (sonarr and
radarr keys from `/var/lib/*/config.xml`, a link to Seerr's
`settings.json`), then run `python3 pkgs/pulse/server.py`.
