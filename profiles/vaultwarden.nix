{ config, lib, ... }:

{
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
      DOMAIN = "https://vault.joaoroxo.com";
      SIGNUPS_ALLOWED = false;

      ROCKET_ADDRESS = "127.0.0.1";
      ROCKET_PORT = 8222;

      WEBSOCKET_ENABLED = true;
      WEBSOCKET_ADDRESS = "127.0.0.1";
      WEBSOCKET_PORT = 3012;
    };
  };

  services.caddy.virtualHosts."vault.joaoroxo.com" = {
    locations."/" = {
      proxyPass = "http://127.0.0.1:8222/";
      proxyWebsockets = true;
    };
    locations."/notifications/hub" = {
      proxyPass = "http://127.0.0.1:3012";
      proxyWebsockets = true;
    };
  };

  services.cloudflared.tunnels."8d582240-9666-4ad0-ae5d-6215bd6dcad3".ingress."vault.joaoroxo.com" = {
    service = "http://localhost:80";
  };

}
