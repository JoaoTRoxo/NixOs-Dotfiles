{ config, pkgs, ... }:

{
  users.users.cloudflared = {
    group = "cloudflared";
    isSystemUser = true;
  };
  users.groups.cloudflared = {};

  age.secrets.vault-tunnel = {
    file = ../secrets/vault-tunnel.age;
    owner = "cloudflared";
    group = "cloudflared";
  };

  services.caddy = {
    enable = true;
    # cloudflared connects from localhost and sends the visitor's address in
    # X-Forwarded-For; trust it so Jellyfin & co. see real client IPs instead of 127.0.0.1
    globalConfig = ''
      servers {
        trusted_proxies static 127.0.0.1/32 ::1/128
      }
    '';
  };

  environment.systemPackages = [ pkgs.cloudflared ];

  services.cloudflared = {
    enable = true;
    tunnels."8d582240-9666-4ad0-ae5d-6215bd6dcad3" = {
        credentialsFile = config.age.secrets.vault-tunnel.path;
        default = "http_status:404";
    };
  };
}
