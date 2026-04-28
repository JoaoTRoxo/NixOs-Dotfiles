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
  services.nginx.virtualHosts."music.sslavos.com" = {
    locations."/" = {
      proxyPass = "http://127.0.0.1:4533/";
      proxyWebsockets = true;
    };
  };

  services.cloudflared.tunnels."113fd93b-5514-4d9e-86d2-7eb0c6d7ea9e".ingress."music.sslavos.com" = {
    service = "http://localhost:80";
  };
}
