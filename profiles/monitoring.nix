{ config, pkgs, ... }:

{
  age.secrets.grafana-secret = {
    file = ../secrets/grafana-secret.age;
    owner = "grafana";
  };

  age.secrets.grafana-secret-key = {
    file = ../secrets/grafana-secret-key.age;
    owner = "grafana";
    group = "grafana";
  };

  services.grafana = {
    enable = true;
    settings = {
      server = {
        http_addr = "127.0.0.1";
        http_port = 3004;
        domain = "monitor.sslavos.com";
        root_url = "https://monitor.sslavos.com/";
      };
      security = {
        admin_user = "admin";
        secret_key = "placeholder";
      };
      users.allow_sign_up = false;
    };
  };

  systemd.services.grafana.serviceConfig.Environment = [
    "GF_SECURITY_ADMIN_PASSWORD__FILE=${config.age.secrets.grafana-secret.path}"
    "GF_SECURITY_SECRET_KEY__FILE=${config.age.secrets.grafana-secret-key.path}"
  ];

  services.prometheus = {
    enable = true;
    port = 9090;
    exporters.node = {
      enable = true;
      enabledCollectors = [ "systemd" ];
      port = 9100;
    };
    scrapeConfigs = [{
      job_name = "zeno";
      static_configs = [{ targets = [ "127.0.0.1:9100" ]; }];
    }];
  };

  services.nginx.virtualHosts."monitor.sslavos.com" = {
    locations."/" = {
      proxyPass = "http://127.0.0.1:3004/";
      proxyWebsockets = true;

    extraConfig = ''
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
      '';
    };
  };

  services.cloudflared.tunnels."113fd93b-5514-4d9e-86d2-7eb0c6d7ea9e".ingress."monitor.sslavos.com" = {
    service = "http://localhost:80";
  };
}
