{ inputs, ... }:

# Treino, the workout log: the Upper/Lower program, set-by-set logging with
# a rest timer, history and stats. The code is in JoaoTRoxo/treino; this is
# its configuration. To update it: nix flake update treino, then rebuild.
#
#   http://100.124.208.1:8585     on the tailnet, no login
#   https://treino.joaoroxo.com   anywhere, through the tunnel; asks for the
#                                 password, which is set from the tailnet
#                                 (the app's Settings)
#
# The public name needs a DNS record in Cloudflare: treino CNAME
# 8d582240-9666-4ad0-ae5d-6215bd6dcad3.cfargotunnel.com, proxied.
#
# Data: /var/lib/private/treino/treino.db (in the nightly backup).
let
  port = 8585;
  tailscaleIp = "100.124.208.1";
  publicHost = "treino.joaoroxo.com";
in
{
  imports = [ "${inputs.treino}/packaging/nix/treino-service.nix" ];

  # Keep the source out of garbage collection: root can't fetch the private
  # repo again (as pulse's and jrx's)
  system.extraDependencies = [ inputs.treino ];

  services.treino = {
    enable = true;
    settings = {
      # The firewall keeps it off the LAN: only the tailnet (a trusted
      # interface) and Caddy reach it
      listen = "0.0.0.0:${toString port}";
      hosts = [ tailscaleIp "aurea" "localhost" "127.0.0.1" publicHost ];
      # Straight from these: no login
      trusted_networks = [ "100.64.0.0/10" "fd7a:115c:a1e0::/48" ];
      # Caddy, i.e. the internet: login needed
      proxies = [ "127.0.0.1" "::1" ];
    };
  };

  services.caddy.virtualHosts."http://${publicHost}".extraConfig = ''
    reverse_proxy 127.0.0.1:${toString port}
  '';

  services.cloudflared.tunnels."8d582240-9666-4ad0-ae5d-6215bd6dcad3".ingress.${publicHost} = {
    service = "http://localhost:80";
  };
}
