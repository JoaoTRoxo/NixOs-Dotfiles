{ config, lib, pkgs, ... }:

# Native Jellyfin 12.x instead of the linuxserver container that was stuck on
# 10.10.7. pkgs/jellyfin-core2.nix is what makes it run on this CPU.
let
  jellyfin = pkgs.callPackage ../pkgs/jellyfin-core2.nix { };
  state = "/var/lib/jellyfin";
in
{
  services.jellyfin = {
    enable = true;
    package = jellyfin;
    # The container ran as aurea (PUID 1000 / PGID 100); keep the same owner
    user = "aurea";
    group = "users";
    # Host side of the container's /config layout
    configDir = state;
    dataDir = "${state}/data";
    cacheDir = "${state}/cache";
    logDir = "${state}/log";
    # LAN clients and discovery, which the container's published 8096 used to allow
    openFirewall = true;
  };

  # The database stores the container's paths (/config/data/root, /data/movies, ...)
  # and Jellyfin derives item IDs from paths, so show it the same layout
  # instead of rewriting them.
  systemd.services.jellyfin.serviceConfig = {
    BindPaths = [
      "${state}:/config"
      "/home/aurea/data:/data"
    ];
    WorkingDirectory = lib.mkForce "/config/data";
    ExecStart = lib.mkForce "${lib.getExe jellyfin} --datadir /config/data --configdir /config --cachedir /config/cache --logdir /config/log";
  };
  # Empty mount points for the binds above
  systemd.tmpfiles.settings.jellyfinMountPoints = {
    "/config".d.mode = "0755";
    "/data".d.mode = "0755";
  };

  # Each rebuild boots the new Jellyfin on this CPU first (in the build
  # sandbox); an update that can't run here fails nixos-rebuild instead.
  system.checks = [ jellyfin.tests.core2-smoke ];

  # One-time copy of the 10.10.7 container's state. /var/lib/jellyfin-docker is
  # left untouched as the way back (12.x migrates its copy in place on first start).
  # Safe to delete this unit once you're happy with 12.x.
  systemd.services.jellyfin-import-docker = {
    description = "Copy Jellyfin state from the old 10.10.7 container";
    requiredBy = [ "jellyfin.service" ];
    before = [ "jellyfin.service" ];
    unitConfig.ConditionPathExists = "!${state}/.imported-from-docker";
    path = [ pkgs.rsync ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
    script = ''
      tmp=${state}.importing
      rm -rf "$tmp"
      # Rate-limited: this disk's SATA link has failed under big write bursts
      rsync -a --bwlimit=20m /var/lib/jellyfin-docker/ "$tmp/"

      # Plugins built for 10.10 don't load on 12.x and can break the migration.
      # Their settings stay in data/plugins/configurations: reinstall from the catalog.
      mkdir -p "$tmp/plugins-10.10"
      find "$tmp/data/plugins" -mindepth 1 -maxdepth 1 -type d ! -name configurations \
        -exec mv -t "$tmp/plugins-10.10" {} +

      # Whatever is there (an abandoned native install from May + tmpfiles dirs) goes aside
      if [ -e ${state} ]; then
        mv -T ${state} "${state}.before-import-$(date +%Y%m%d-%H%M%S)"
      fi
      mv -T "$tmp" ${state}
      chmod 700 ${state}
      touch ${state}/.imported-from-docker
    '';
  };

  services.caddy.virtualHosts."http://stream.joaoroxo.com".extraConfig = ''
    reverse_proxy 127.0.0.1:8096
  '';

  services.cloudflared.tunnels."8d582240-9666-4ad0-ae5d-6215bd6dcad3".ingress."stream.joaoroxo.com" = {
    service = "http://localhost:80";
  };
}
