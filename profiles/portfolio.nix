{ config, pkgs, ... }:

{
  services.caddy.virtualHosts."http://joaoroxo.com".extraConfig = ''
    root * /var/www/joaoroxo
    file_server
    try_files {path} {path}/ /index.html
  '';

  services.cloudflared.tunnels."8d582240-9666-4ad0-ae5d-6215bd6dcad3".ingress."joaoroxo.com" = {
    service = "http://localhost:80";
  };
}
