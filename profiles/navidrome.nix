{ config, ... }:
{
  services.navidrome = {
    enable = true;
    settings = {
      Address = "127.0.0.1";
      Port = 4533;
      MusicFolder = "/var/lib/navidrome/music";
    };
  };
  services.caddy.virtualHosts."music.joaoroxo.com" = {
    locations."/" = {
      proxyPass = "http://127.0.0.1:4533/";
      proxyWebsockets = true;
    };
  };

  services.cloudflared.tunnels."8d582240-9666-4ad0-ae5d-6215bd6dcad3".ingress."music.joaoroxo.com" = {
    service = "http://localhost:80";
  };
}
