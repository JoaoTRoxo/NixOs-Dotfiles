{ config, lib, pkgs, ... }:

let
  # Radicale calendar name = config name -> iCloud calendar UUID
  calendars = {
    home = "2B0A9076-A6C5-4301-90F5-0398D6615784";
    work = "A5827FAB-C12A-4619-B91D-738F2EF23EF2";
    app-tecnico-classes = "28ADC051-3F0F-497E-9849-16EB5A94229C";
    app-tecnico-evaluations = "92E42D16-0E3D-4BB2-833F-18045FA515B7";
  };
  # ["config_name", "icloud_uuid", "radicale_name"]; JSON on one line, since
  # vdirsyncer 0.20+ rejects multi-line lists
  collections = builtins.toJSON (lib.mapAttrsToList (name: uuid: [ name uuid name ]) calendars);
  status = "/var/lib/vdirsyncer/status";
in
{
  age.secrets.icloud-app-pass = {
    file = ../secrets/icloud-app-pass.age;
    owner = "vdirsyncer";
    group = "vdirsyncer";
  };

  age.secrets.radicale-bridge-pass = {
    file = ../secrets/radicale-bridge-pass.age;
    owner = "vdirsyncer";
    group = "vdirsyncer";
  };

  users.users.vdirsyncer = {
    isSystemUser = true;
    group = "vdirsyncer";
    description = "iCloud <-> Radicale bridge";
  };
  users.groups.vdirsyncer = {};

  environment.systemPackages = [ pkgs.vdirsyncer ];

  # No secrets in /nix/store here — only `password.fetch` commands
  # pointing at /run/agenix/*.
  environment.etc."vdirsyncer/config".text = ''
    [general]
    status_path = "${status}/"

    [pair icloud_to_radicale]
    a = "icloud"
    b = "radicale"
    # Explicit mapping = no interactive `discover` needed.
    collections = ${collections}
    conflict_resolution = "a wins"
    metadata = ["displayname", "color"]

    [storage icloud]
    type = "caldav"
    url = "https://caldav.icloud.com/"
    username = "joaotomas.roxo@gmail.com"
    password.fetch = ["command", "cat", "${config.age.secrets.icloud-app-pass.path}"]
    # Uncomment for 1-way iCloud -> Radicale only:
    # read_only = true

    [storage radicale]
    type = "caldav"
    # Localhost: same machine, skip Caddy/Cloudflare hop.
    url = "http://127.0.0.1:5232/roxo/"
    username = "roxo"
    password.fetch = ["command", "cat", "${config.age.secrets.radicale-bridge-pass.path}"]
  '';

  systemd.services.vdirsyncer-sync = {
    description = "vdirsyncer iCloud <-> Radicale sync";
    after = [ "network-online.target" "radicale.service" ];
    wants = [ "network-online.target" ];
    path = [ pkgs.curl ];
    script = ''
      mkdir -p ${status}

      # One-time: the sync status is from 2026-09-09, before the Radicale
      # calendars were deleted (and some recreated) in the web UI. Syncing
      # against it could push those deletions to iCloud, so start over with a
      # fresh merge, which never deletes anything.
      if [ ! -e /var/lib/vdirsyncer/.status-reset-2026-10 ]; then
        mv -T ${status} ${status}.2026-09-09
        mkdir -p ${status}
        touch /var/lib/vdirsyncer/.status-reset-2026-10
      fi

      # vdirsyncer 0.20+ has no `implicit = "create"`. A calendar missing on
      # Radicale makes every collection fail (404 for it, "Server disconnected"
      # for the rest), so recreate it and drop its status so it refills from iCloud.
      pass=$(cat ${config.age.secrets.radicale-bridge-pass.path})
      pass=''${pass//\\/\\\\}
      pass=''${pass//\"/\\\"}
      for c in ${toString (lib.attrNames calendars)}; do
        url=http://127.0.0.1:5232/roxo/$c/
        code=$(printf 'user = "roxo:%s"\n' "$pass" |
          curl -s -K - -o /dev/null -w '%{http_code}' -X PROPFIND -H 'Depth: 0' "$url")
        if [ "$code" = 404 ]; then
          echo "Radicale calendar $c is missing, recreating it"
          printf 'user = "roxo:%s"\n' "$pass" | curl -sf -K - -X MKCALENDAR "$url"
          rm -f ${status}/icloud_to_radicale/$c.*
        fi
      done

      # A fresh status (first run, or after the reset above) needs one discover
      # first; with every calendar already present on both sides it won't prompt.
      if [ ! -e ${status}/icloud_to_radicale.collections ]; then
        ${pkgs.vdirsyncer}/bin/vdirsyncer -c /etc/vdirsyncer/config discover icloud_to_radicale < /dev/null
      fi

      ${pkgs.vdirsyncer}/bin/vdirsyncer -c /etc/vdirsyncer/config metasync
      ${pkgs.vdirsyncer}/bin/vdirsyncer -c /etc/vdirsyncer/config sync
    '';
    serviceConfig = {
      Type = "oneshot";
      User = "vdirsyncer";
      Group = "vdirsyncer";
      StateDirectory = "vdirsyncer";
    };
  };

  systemd.timers.vdirsyncer-sync = {
    description = "vdirsyncer iCloud <-> Radicale timer";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "5m";
      OnUnitActiveSec = "5m";
    };
  };
}
