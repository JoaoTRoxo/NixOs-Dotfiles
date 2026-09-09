{ config, ... }:

{
  age.secrets.radicale-users = {
    file = ../secrets/radicale-users.age;
    owner = "radicale";
    group = "radicale";
  };

  services.radicale = {
    enable = true;

    settings = {
      server = {
        hosts = [ "127.0.0.1:5232" ];
      };

      auth = {
        type = "htpasswd";
        htpasswd_filename = config.age.secrets.radicale-users.path;
        htpasswd_encryption = "autodetect";
      };

      storage = {
        filesystem_folder = "/var/lib/radicale/collections";
      };
    };
  };

  services.caddy.virtualHosts."http://calendar.joaoroxo.com" = {
    extraConfig = ''
      reverse_proxy 127.0.0.1:5232
    '';
  };

  services.cloudflared.tunnels
    ."8d582240-9666-4ad0-ae5d-6215bd6dcad3"
    .ingress
    ."calendar.joaoroxo.com" = {
      service = "http://localhost:80";
    };
}
