{ config, pkgs, ... }:

{
  virtualisation.docker.enable = true;
  virtualisation.oci-containers.backend = "docker";
  networking.firewall.trustedInterfaces = [ "docker0" ];


  networking.firewall.allowedTCPPorts = [ 8096 8080 ];



  # 2. Caddy Reverse Proxy
  services.caddy.virtualHosts."http://stream.joaoroxo.com" = {
    extraConfig = ''
      reverse_proxy 127.0.0.1:8096
      '';

  };


  # 3. Cloudflare Tunnel Ingress
  services.cloudflared.tunnels."8d582240-9666-4ad0-ae5d-6215bd6dcad3".ingress."stream.joaoroxo.com" = {
    service = "http://localhost:80";
  };




  # 4. The Automated Torrent/Media Stack (Docker/OCI)
  virtualisation.oci-containers.containers = {

    jellyfin = {
      image = "lscr.io/linuxserver/jellyfin:latest";
      ports = [ "8096:8096" ];
      environment = {
        PUID = "1000"; # Matches aurea
        PGID = "100";  # Matches users group
      };
      volumes = [
        "/var/lib/jellyfin-docker:/config" # Safe state directory for metadata
        "/home/aurea/data:/data"           # Direct access to your media pool
      ];
    };

    qbittorrent = {
      image = "lscr.io/linuxserver/qbittorrent:latest";
      # We now expose the WebUI and P2P ports directly to the host
      ports = [ 
        "8080:8080"      # Web UI
        "6881:6881"      # BitTorrent TCP
        "6881:6881/udp"  # BitTorrent UDP
      ];
      environment = {
        PUID = "1000"; # aurea user
        PGID = "100";  # users group
        WEBUI_PORT = "8080";
      };
      volumes = [
        "/home/aurea/data:/data" 
        "/var/lib/qbittorrent:/config"
      ];
    };

    sonarr = {
      image = "lscr.io/linuxserver/sonarr:latest";
      ports = [ "8989:8989" ];
      environment = { PUID = "1000"; PGID = "100"; };
      volumes = [
        "/home/aurea/data:/data"
        "/var/lib/sonarr:/config"
      ];
    };

    jellyseerr = {
      image = "ghcr.io/seerr-team/seerr:latest"; # active, well-maintained image
      ports = [ "5055:5055" ];
      environment = {
        PUID = "1000";
        PGID = "100";
        TZ = "Europe/Lisbon"; # Matches your core timezone
      };
      volumes = [
        "/var/lib/jellyseerr:/app/config" # Persistent state folder
      ];
    };

    prowlarr = {
      image = "lscr.io/linuxserver/prowlarr:latest";
      ports = [ "9696:9696" ];
      environment = { PUID = "1000"; PGID = "100"; };
      volumes = [ "/var/lib/prowlarr:/config" ];
    };

    bazarr = {
      image = "lscr.io/linuxserver/bazarr:latest";
      ports = [ "6767:6767" ];
      environment = { PUID = "1000"; PGID = "100"; };
      volumes = [
        "/home/aurea/data:/data"
        "/var/lib/bazarr:/config"
      ];
    };

    flaresolverr = {
      image = "ghcr.io/flaresolverr/flaresolverr:latest";
      ports = [ "8191:8191" ];
      environment = {
        LOG_LEVEL = "info";
        TZ = "Europe/Lisbon";
      };
    };

  };
}
