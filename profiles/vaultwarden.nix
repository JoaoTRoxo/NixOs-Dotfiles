{ config, lib, ... }:
let
  privateFile = ./private.nix;

  private = if builtins.pathExists privateFile
  then import privateFile
  else { tunnelId = "00000000-0000-0000-0000-000000000000"; };

in {
  age.secrets.vw-secrets = {
    file = ../secrets/vw-secrets.age;
    owner = "vaultwarden";
    group = "vaultwarden";
    mode = "0400";
  };

  services.vaultwarden = {
    enable = true;

    environmentFile = config.age.secrets.vw-secrets.path;

    config = {
      DOMAIN = "https://vault.sslavos.com";
      SIGNUPS_ALLOWED = false;

      ROCKET_ADDRESS = "127.0.0.1";
      ROCKET_PORT = 8222;

      WEBSOCKET_ENABLED = true;
      WEBSOCKET_ADDRESS = "127.0.0.1";
      WEBSOCKET_PORT = 3012;
    };
  };

  services.nginx.virtualHosts."vault.sslavos.com" = {
    locations."/" = {
      proxyPass = "http://127.0.0.1:8222/";
      proxyWebsockets = true;
    };
    locations."/notifications/hub" = {
      proxyPass = "http://127.0.0.1:3012";
      proxyWebsockets = true;
    };
  };

  services.cloudflared.tunnels."${private.tunnelId}".ingress."vault.sslavos.com" = {
    service = "http://localhost:80";
  };

}
