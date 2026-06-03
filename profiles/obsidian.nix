{ config, pkgs, ... }:

{
  # 1. Secret Management for CouchDB
  age.secrets.couchdb-secret = {
    file = ../secrets/couchdb-secret.age;
  };

  # 2. OCI Container for CouchDB
  virtualisation.oci-containers.containers = {
    obsidian-couchdb = {
      image = "couchdb:3";
      ports = [ "5984:5984" ];
      environment = {
        COUCHDB_USER = "admin";
      };
      environmentFiles = [
        config.age.secrets.couchdb-secret.path
      ];
      volumes = [
        "/var/lib/obsidian-couchdb/data:/opt/couchdb/data"
        "/var/lib/obsidian-couchdb/local.d:/opt/couchdb/etc/local.d"
      ];
    };
  };

  # 3. Caddy Reverse Proxy
  services.caddy.virtualHosts."http://sync.joaoroxo.com" = {
    extraConfig = ''
      reverse_proxy 127.0.0.1:5984
    '';
  };

  # 4. Cloudflare Tunnel Ingress
  services.cloudflared.tunnels."8d582240-9666-4ad0-ae5d-6215bd6dcad3".ingress."sync.joaoroxo.com" = {
    service = "http://localhost:80";
  };
}
