{ config, lib, pkgs, inputs, ... }:

# Pulse, the daily dashboard: http://100.124.208.1:8484 on the tailnet (the
# firewall keeps it off the LAN, and it isn't behind the Cloudflare tunnel).
# It shows how aurea, its services and jobs, jrxd's recordings, the media
# stack, the calendar and the projects on GitHub are doing, what needs
# attention, and a morning brief written by Claude. The code is pkgs/pulse
# (README.md there); its tests: python3 pkgs/pulse/test_pulse.py
#
# The laptop reports its git repos and GitHub (pkgs/pulse/agent.py, set up
# with pkgs/pulse/pulse-agent-install.sh), and writes the brief with Claude
# Code while aurea has no API key.
#
# Optional secrets, used as soon as their file exists (agenix -e <name>):
#   pulse-anthropic.age  a Claude API key: aurea writes the brief itself,
#                        and the page can answer questions
#   pulse-github.age     a GitHub token (read access): GitHub without the
#                        laptop
#   pulse-jellyfin.age   a Jellyfin API key: who's watching what
let
  port = 8484;
  tailscaleIp = "100.124.208.1";

  optionalSecrets = lib.filterAttrs (_: file: builtins.pathExists file) {
    anthropic = ../secrets/pulse-anthropic.age;
    github = ../secrets/pulse-github.age;
    jellyfin = ../secrets/pulse-jellyfin.age;
  };

  python = pkgs.python3.withPackages (ps: [ ps.anthropic ]);
  # The Rust Pulse (JoaoTRoxo/pulse), running beside this one for now
  pulseRust = pkgs.callPackage "${inputs.pulse}/packaging/nix/pulse.nix" { };
  pulse = pkgs.runCommand "pulse" {
    nativeBuildInputs = [ pkgs.makeWrapper pkgs.python3Packages.flake8 ];
  } ''
    mkdir -p $out/share $out/bin
    cp -r ${../pkgs/pulse} $out/share/pulse
    flake8 $out/share/pulse
    makeWrapper ${python}/bin/python3 $out/bin/pulse \
      --add-flags $out/share/pulse/server.py
  '';

  web = name: url: { inherit name url; };
  tailnet = p: "http://${tailscaleIp}:${toString p}";

  settings = {
    name = "João";
    listen = "0.0.0.0:${toString port}";
    # What the page may be opened as (anything else is refused, so another
    # site can't read it by pointing a domain of its own at aurea)
    hosts = [ tailscaleIp "aurea" "localhost" "127.0.0.1" ];
    timezone = config.time.timeZone;
    prometheus = "http://127.0.0.1:${toString config.services.prometheus.port}";
    temperature = ''max(node_hwmon_temp_celsius{chip=~"platform_coretemp.*"})'';
    mounts = [
      { path = "/"; name = "System disk"; }
      { path = "/srv/jrx"; name = "Recordings disk"; }
    ];
    # By id, as in alerts.nix: sdX names move
    disks = {
      system = "/dev/disk/by-id/ata-SAMSUNG_HD103SJ_S246J9EB918624";
      recordings = "/dev/disk/by-id/ata-SAMSUNG_HD501LJ_S0MUJ1MP911549";
    };

    services =
      map (s: s // { group = "Media"; }) [
        (web "Jellyfin" "https://stream.joaoroxo.com" // { unit = "jellyfin.service"; })
        (web "Seerr" "https://requests.joaoroxo.com" // { unit = "docker-jellyseerr.service"; })
        (web "Sonarr" (tailnet 8989) // { unit = "docker-sonarr.service"; })
        (web "Radarr" (tailnet 7878) // { unit = "docker-radarr.service"; })
        (web "Prowlarr" (tailnet 9696) // { unit = "docker-prowlarr.service"; })
        (web "qBittorrent" (tailnet 8080) // { unit = "docker-qbittorrent.service"; })
        (web "Bazarr" (tailnet 6767) // { unit = "docker-bazarr.service"; })
        { name = "Byparr"; unit = "docker-byparr.service"; }
        { name = "Seerr on Telegram"; unit = "seerr-telegram.service"; }
      ]
      ++ map (s: s // { group = "Apps"; }) [
        { name = "jrxd recorder"; unit = "jrxd.service"; }
        (web "Gitea" "https://git.joaoroxo.com" // { unit = "gitea.service"; })
        (web "Grafana" "https://monitor.joaoroxo.com" // { unit = "grafana.service"; })
        (web "Radicale" "https://calendar.joaoroxo.com" // { unit = "radicale.service"; })
        { name = "Obsidian sync"; unit = "docker-obsidian-couchdb.service"; }
      ]
      ++ map (s: s // { group = "Infrastructure"; }) [
        { name = "Cloudflare tunnel"; unit = "cloudflared-tunnel-8d582240-9666-4ad0-ae5d-6215bd6dcad3.service"; }
        { name = "Caddy"; unit = "caddy.service"; }
        { name = "Tailscale"; unit = "tailscaled.service"; }
        (web "AdGuard Home" (tailnet 3000) // { unit = "adguardhome.service"; })
        (web "Prometheus" (tailnet config.services.prometheus.port) // { unit = "prometheus.service"; })
        { name = "Docker"; unit = "docker.service"; }
        { name = "smartd"; unit = "smartd.service"; }
        { name = "chrony"; unit = "chronyd.service"; }
      ];

    # Timers: the last run's result, and a warning when one stops running
    jobs = [
      { unit = "backup-snapshot"; name = "Nightly backup snapshot"; max_age_hours = 36; }
      { unit = "server-alerts"; name = "Alert checks"; max_age_hours = 1; }
      { unit = "vdirsyncer-sync"; name = "iCloud calendar sync"; max_age_hours = 1; }
      { unit = "recyclarr"; name = "Recyclarr"; max_age_hours = 36; }
      { unit = "oci-containers-update"; name = "Container image updates"; max_age_hours = 36; }
      { unit = "nix-gc"; name = "Nix garbage collection"; max_age_hours = 192; }
      { unit = "fstrim"; name = "fstrim"; max_age_hours = 192; }
    ];

    # Through Cloudflare and Caddy, as anyone outside sees them
    endpoints = [
      (web "stream.joaoroxo.com" "https://stream.joaoroxo.com/health")
      (web "requests.joaoroxo.com" "https://requests.joaoroxo.com/")
      (web "git.joaoroxo.com" "https://git.joaoroxo.com/")
      (web "monitor.joaoroxo.com" "https://monitor.joaoroxo.com/api/health")
      (web "calendar.joaoroxo.com" "https://calendar.joaoroxo.com/")
      (web "sync.joaoroxo.com" "https://sync.joaoroxo.com/")
    ];

    jrxd = { dir = config.services.jrxd.dataDir; unit = "jrxd.service"; };
    sonarr = { url = "http://127.0.0.1:8989"; };
    radarr = { url = "http://127.0.0.1:7878"; };
    seerr = { url = "http://127.0.0.1:5055"; public_url = "https://requests.joaoroxo.com"; };
    jellyfin = { url = "http://127.0.0.1:8096"; public_url = "https://stream.joaoroxo.com"; };
    # Radicale's copy of the iCloud calendars (vdirsyncer-bridge.nix), read
    # as the bridge's user
    calendar = {
      url = "http://127.0.0.1:5232/roxo/";
      user = "roxo";
      calendars = {
        home.label = "Home";
        work.label = "Work";
        app-tecnico-classes.label = "Classes";
        app-tecnico-evaluations = { label = "Evaluations"; kind = "evaluation"; };
      };
    };
    ai = { model = "claude-opus-5-5"; effort = "medium"; brief_after = 6; };
  };
in
{
  age.secrets = lib.mapAttrs' (name: file:
    lib.nameValuePair "pulse-${name}" { inherit file; }) optionalSecrets;

  systemd.services.pulse = {
    description = "Pulse, the daily dashboard";
    wantedBy = [ "multi-user.target" ];
    after = [ "network-online.target" "prometheus.service" ];
    wants = [ "network-online.target" ];
    # systemctl, tailscale status, and jrxd list (under nice and ionice)
    path = [ config.systemd.package pkgs.tailscale pkgs.coreutils
             pkgs.util-linux config.services.jrxd.package ];
    environment = {
      PULSE_CONFIG = pkgs.writeText "pulse.json" (builtins.toJSON settings);
      PYTHONUNBUFFERED = "1";
      PYTHONDONTWRITEBYTECODE = "1";
    };
    serviceConfig = {
      ExecStart = lib.getExe' pulse "pulse";
      DynamicUser = true;
      StateDirectory = "pulse";
      # jrxd's recordings are 0750 jrxd:jrxd
      SupplementaryGroups = [ "jrxd" ];
      LoadCredential = [
        "sonarr:${config.age.secrets.sonarr-api-key.path}"
        "radarr:${config.age.secrets.radarr-api-key.path}"
        "seerr-settings:/var/lib/jellyseerr/settings.json"
        "radicale:${config.age.secrets.radicale-bridge-pass.path}"
      ] ++ lib.mapAttrsToList (name: _:
        "${name}:${config.age.secrets."pulse-${name}".path}") optionalSecrets;
      # It shares the Q6600 with Jellyfin and jrxd
      Nice = 5;
      IOSchedulingPriority = 7;
      ProtectHome = true;
      PrivateTmp = true;
      NoNewPrivileges = true;
      Restart = "always";
      RestartSec = 10;
    };
  };

  # The Rust Pulse, beside the Python one until their snapshots match
  # (packaging/compare-snapshots.sh in JoaoTRoxo/pulse); then it takes over.
  # The same settings and credentials, on 127.0.0.1:8485, with its own state.
  # Keep its source out of garbage collection, as jrx's: root can't fetch
  # the private repo again
  system.extraDependencies = [ inputs.pulse ];
  systemd.services.pulse-shadow = {
    description = "Pulse in Rust, beside the Python one";
    inherit (config.systemd.services.pulse) wantedBy after wants path;
    environment.PULSE_CONFIG = pkgs.writeText "pulse-shadow.json"
      (builtins.toJSON (settings // { listen = "127.0.0.1:8485"; }));
    serviceConfig = config.systemd.services.pulse.serviceConfig // {
      ExecStart = lib.getExe pulseRust;
      StateDirectory = "pulse-shadow";
    };
  };
}
