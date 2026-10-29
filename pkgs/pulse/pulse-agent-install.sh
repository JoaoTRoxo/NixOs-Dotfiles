#!/bin/sh
# Installs Pulse's laptop agent (agent.py) as ~/.local/bin/pulse-agent, run
# every 15 minutes by a systemd user timer. Run it again after agent.py
# changes. The units live in the shared /home, so Arch and Gentoo both run
# it. Undo: systemctl --user disable --now pulse-agent.timer
set -eu
here=$(cd "$(dirname "$0")" && pwd)
units=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user

install -Dm755 "$here/agent.py" "$HOME/.local/bin/pulse-agent"
mkdir -p "$units"
cat > "$units/pulse-agent.service" <<'EOF'
[Unit]
Description=Report the laptop's repos and GitHub to Pulse on aurea
After=network-online.target

[Service]
Type=oneshot
ExecStart=%h/.local/bin/pulse-agent
# gh, git, and Claude Code for the brief (in /opt/bin on Gentoo)
Environment=PATH=%h/.local/bin:/opt/bin:/usr/local/bin:/usr/bin:/bin
TimeoutStartSec=15min
Nice=10
EOF
cat > "$units/pulse-agent.timer" <<'EOF'
[Unit]
Description=Report the laptop to Pulse every 15 minutes

[Timer]
OnStartupSec=1min
OnUnitActiveSec=15min

[Install]
WantedBy=timers.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now pulse-agent.timer
echo "pulse-agent installed; it reports every 15 minutes (journalctl --user -u pulse-agent)"
