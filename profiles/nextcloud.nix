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

      hostName = "cloud.sslavos.com";
      https = true;

      database.createLocally = true;
      configureRedis = true;

      config = {
        dbtype = "pgsql";
        adminuser = "admin";
        adminpassFile = config.age.secrets.nextcloud-secret.path;
      };

    };


    services.cloudflared.tunnels."113fd93b-5514-4d9e-86d2-7eb0c6d7ea9e".ingress."cloud.sslavos.com" = {
      service = "http://localhost:80";
    };
}
