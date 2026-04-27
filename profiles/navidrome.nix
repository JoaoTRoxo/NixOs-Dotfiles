{ config, ... }:
let
  privateFile = ./private.nix;

  private = if builtins.pathExists privateFile
  then import privateFile
  else { tunnelId = "00000000-0000-0000-0000-000000000000"; };

in {
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

  services.cloudflared.tunnels."${private.tunnelId}".ingress."music.sslavos.com" = {
    service = "http://localhost:80";
  };
}
