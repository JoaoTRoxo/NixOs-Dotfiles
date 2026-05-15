{ config, lib, ... }:

{

  services.gitea = {
    enable = true;

    settings = {
      server = {
        DOMAIN   = "git.joaoroxo.com";
        ROOT_URL = "https://git.joaoroxo.com/";
        HTTP_ADDRESS = "127.0.0.1";
        HTTP_PORT    = 3001;

        DISABLE_SSH      = false;
        START_SSH_SERVER = false;
        SSH_PORT         = 22;
      };

      service.REGISTER_EMAIL_CONFIRM = true;
      service.DISABLE_REGISTRATION = true;

    };
  };

  services.caddy.virtualHosts."git.joaoroxo.com" = {
    locations."/" = {
      proxyPass    = "http://127.0.0.1:3001/";
      proxyWebsockets = true;
    };
  };

  services.cloudflared.tunnels."8d582240-9666-4ad0-ae5d-6215bd6dcad3".ingress."git.joaoroxo.com" = {
    service = "http://localhost:80";
  };
}
