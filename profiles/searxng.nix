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
        base_url = "https://search.joaoroxo.com/";
        port = 8888;
        bind_address = "127.0.0.1";
        secret_key = "placeholder-nixos-build";
      };
      ui = {
        theme = "simple";
      };
      engines = [
        { name = "startpage"; engine = "startpage"; shortcut = "sp"; }
        { name = "google"; engine = "google"; shortcut = "go"; }
        { name = "duckduckgo"; engine = "duckduckgo"; shortcut = "ddg"; }
      ];
    };
  };

  # Injetar a chave real
  systemd.services.searx.serviceConfig.Environment = [
    "SEARX_SECRET_KEY__FILE=${config.age.secrets.searxng-secret.path}"
  ];

  # caddy (Cloudflare tratta do SSL)
  services.caddy.virtualHosts."search.joaoroxo.com" = {
    locations."/" = {
      proxyPass = "http://127.0.0.1:8888";
      proxyWebsockets = true;
    };
  };

  services.cloudflared.tunnels."8d582240-9666-4ad0-ae5d-6215bd6dcad3".ingress."search.joaoroxo.com" = {
    service = "http://localhost:80";
  };
}
