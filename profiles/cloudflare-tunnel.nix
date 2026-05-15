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
