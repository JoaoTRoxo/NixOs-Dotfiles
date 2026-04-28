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

  services.cloudflared.tunnels."113fd93b-5514-4d9e-86d2-7eb0c6d7ea9e".ingress."vault.sslavos.com" = {
    service = "http://localhost:80";
  };

}
