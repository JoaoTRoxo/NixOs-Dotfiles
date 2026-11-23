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
    # Keep background ffmpeg jobs (intro/credits blackdetect, chapter extraction)
    # from pegging all 4 cores on the Core 2 Quad Q6600.
    CPUQuota = "100%";
  };
  # Empty mount points for the binds above
  systemd.tmpfiles.settings.jellyfinMountPoints = {
    "/config".d.mode = "0755";
    "/data".d.mode = "0755";
  };

  # Each rebuild boots the new Jellyfin on this CPU first (in the build
  # sandbox); an update that can't run here fails nixos-rebuild instead.
  system.checks = [ jellyfin.tests.core2-smoke ];

  services.caddy.virtualHosts."http://stream.joaoroxo.com".extraConfig = ''
    reverse_proxy 127.0.0.1:8096
  '';

  services.cloudflared.tunnels."8d582240-9666-4ad0-ae5d-6215bd6dcad3".ingress."stream.joaoroxo.com" = {
    service = "http://localhost:80";
  };
}
