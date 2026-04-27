{ config, pkgs, ... }:

let
  privateFile = ./private.nix;

  private = if builtins.pathExists privateFile
  then import privateFile
  else { tunnelId = "00000000-0000-0000-0000-000000000000"; };
in {
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
    tunnels."${private.tunnelId}" = {
        credentialsFile = config.age.secrets.vault-tunnel.path;
        default = "http_status:404";
    };
  };
}
