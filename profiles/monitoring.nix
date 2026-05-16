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
        domain = "monitor.joaoroxo.com";
        root_url = "https://monitor.joaoroxo.com/";
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
      job_name = "aurea";
      static_configs = [{ targets = [ "127.0.0.1:9100" ]; }];
    }];
  };

  services.caddy.virtualHosts."http://monitor.joaoroxo.com" = {
    extraConfig = ''
      reverse_proxy 127.0.0.1:3004
      '';

  };

  services.cloudflared.tunnels."8d582240-9666-4ad0-ae5d-6215bd6dcad3".ingress."monitor.joaoroxo.com" = {
    service = "http://localhost:80";
  };
}
