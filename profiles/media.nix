{ config, pkgs, ... }:

# The automated torrent/media stack. Jellyfin itself runs natively (jellyfin.nix);
# image updates are handled by the daily timer in docker.nix.
let
  env = {
    PUID = "1000"; # aurea
    PGID = "100";  # users
    TZ = "Europe/Lisbon";
  };
in
{
  services.caddy.virtualHosts."http://requests.joaoroxo.com" = {
    extraConfig = ''
      reverse_proxy 127.0.0.1:5055
      '';
  };

  services.cloudflared.tunnels."8d582240-9666-4ad0-ae5d-6215bd6dcad3".ingress."requests.joaoroxo.com" = {
    service = "http://localhost:80";
  };

  virtualisation.oci-containers.containers = {

    qbittorrent = {
      image = "lscr.io/linuxserver/qbittorrent:latest";
      # We now expose the WebUI and P2P ports directly to the host
      ports = [
        "8080:8080"      # Web UI
        "6881:6881"      # BitTorrent TCP
        "6881:6881/udp"  # BitTorrent UDP
      ];
      environment = env // { WEBUI_PORT = "8080"; };
      volumes = [
        "/home/aurea/data:/data"
        "/var/lib/qbittorrent:/config"
      ];
    };

    sonarr = {
      image = "lscr.io/linuxserver/sonarr:latest";
      ports = [ "8989:8989" ];
      environment = env;
      volumes = [
        "/home/aurea/data:/data"
        "/var/lib/sonarr:/config"
      ];
    };

    radarr = {
      image = "lscr.io/linuxserver/radarr:latest";
      ports = [ "7878:7878" ];
      environment = env;
      volumes = [
        "/home/aurea/data:/data"
        "/var/lib/radarr:/config"
      ];
    };

    jellyseerr = {
      image = "ghcr.io/seerr-team/seerr:latest"; # active, well-maintained image
      ports = [ "5055:5055" ];
      environment = env;
      volumes = [
        "/var/lib/jellyseerr:/app/config" # Persistent state folder
      ];
    };

    prowlarr = {
      image = "lscr.io/linuxserver/prowlarr:latest";
      ports = [ "9696:9696" ];
      environment = env;
      volumes = [ "/var/lib/prowlarr:/config" ];
    };

    bazarr = {
      image = "lscr.io/linuxserver/bazarr:latest";
      ports = [ "6767:6767" ];
      environment = env;
      volumes = [
        "/home/aurea/data:/data"
        "/var/lib/bazarr:/config"
      ];
    };

    byparr = {
      image = "ghcr.io/flaresolverr/flaresolverr:latest";
      ports = [ "8191:8191" ];
      environment = {
        LOG_LEVEL = "info";
        TZ = "Europe/Lisbon";
      };
    };

  };
}
