{ config, pkgs, ... }:

{
  age.secrets.searxng-secret = {
    file = ../secrets/searxng-secret.age;
    user = "searx";
    group = "searx";
  };

  services.searx = {
    enable = true;
    package = pkgs.searxng;

    settings = {
      server = {
        port = 8888;
        bind_address = "127.0.0.1";
        secret_key = "placeholder";
      };
      ui = {
        static_path = "${pkgs.searxng}/share/static";
        templates_path = "${pkgs.searxng}/share/templates";
        theme = "simple";
      };
      search = {
        safe_search = 0;
        autocomplete = "google";
      };

      engines = [
        { name = "google"; engine = "google"; }
        { name = "duckduckgo"; engine = "duckduckgo"; }
        { name = "wikipedia"; engine = "wikipedia"; }
      ];
    };
  };

  systemd.services.searx.serviceConfig.Environment = [
    "SEARX_SECRET_KEY__FILE=${config.age.secrets.searxng-key.path}"
  ];

  services.nginx.virtualHosts."search.sslavos.com" = {
    locations."/" = {
      proxyPass = "http://127.0.0.1:8888";
      proxyWebsockets = true;
    };
  };

  services.cloudflared.tunnels."113fd93b-5514-4d9e-86d2-7eb0c6d7ea9e".ingress."search.sslavos.com" = {
    service = "http://localhost:80";
  };
}
