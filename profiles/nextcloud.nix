{ config, pkgs, ... }:
{
  age.secrets.nextcloud-secret = {
    file = ../secrets/nextcloud-secret.age;
    owner = "nextcloud";
    group = "nextcloud";
  };

  services.nextcloud = {
      enable = true;

      package = pkgs.nextcloud33;

      hostName = "cloud.joaoroxo.com";
      https = true;

      database.createLocally = true;
      configureRedis = true;

      config = {
        dbtype = "pgsql";
        adminuser = "admin";
        adminpassFile = config.age.secrets.nextcloud-secret.path;
      };

    };


    services.cloudflared.tunnels."8d582240-9666-4ad0-ae5d-6215bd6dcad3".ingress."cloud.joaoroxo.com" = {
      service = "http://localhost:80";
    };
}
