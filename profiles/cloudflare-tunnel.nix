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

  services.nginx = {
    enable = true;
  };

  environment.systemPackages = [ pkgs.cloudflared ];

  services.cloudflared = {
    enable = true;
    tunnels."113fd93b-5514-4d9e-86d2-7eb0c6d7ea9e" = {
        credentialsFile = config.age.secrets.vault-tunnel.path;
        default = "http_status:404";
    };
  };
}
