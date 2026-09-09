{ config, pkgs, ... }:

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
    status_path = "/var/lib/vdirsyncer/status/"

    [pair icloud_to_radicale]
    a = "icloud"
    b = "radicale"
    # Explicit mapping = no interactive `discover` needed.
    # Format: ["config_name", "icloud_uuid", "radicale_name"]
    # NOTE: must stay on one line — vdirsyncer 0.20 fails on multiline lists.
    collections = [["home", "2B0A9076-A6C5-4301-90F5-0398D6615784", "home"], ["work", "A5827FAB-C12A-4619-B91D-738F2EF23EF2", "work"], ["app-tecnico-classes", "28ADC051-3F0F-497E-9849-16EB5A94229C", "app-tecnico-classes"], ["app-tecnico-evaluations", "92E42D16-0E3D-4BB2-833F-18045FA515B7", "app-tecnico-evaluations"]]
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
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    script = ''
      mkdir -p /var/lib/vdirsyncer/status
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
