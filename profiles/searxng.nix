{ config, pkgs, ... }:

{
  age.secrets.searxng-secret = {
    file = ../secrets/searxng-secret.age;
    owner = "searx";
    group = "searx";
  };

  services.searx = {
    enable = true;
    package = pkgs.searxng;
    
    settings = {
      server = {
        base_url = "https://search.sslavos.com/";
        port = 8888;
        bind_address = "127.0.0.1";
        secret_key = "placeholder-nixos-build";
      };
      ui = {
        theme = "simple";
      };
      engines = [
        { name = "google"; engine = "google"; shortcut = "go"; }
        { name = "duckduckgo"; engine = "duckduckgo"; shortcut = "ddg"; }
      ];
    };
  };

  # Injetar a chave real
  systemd.services.searx.serviceConfig.Environment = [
    "SEARX_SECRET_KEY__FILE=${config.age.secrets.searxng-secret.path}"
  ];

  # Nginx (Cloudflare tratta do SSL)
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
