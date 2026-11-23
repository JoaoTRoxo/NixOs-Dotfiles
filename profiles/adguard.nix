{ config, pkgs, ... }:
{

  services.adguardhome = {
    enable = true;
    settings.http.address = "0.0.0.0:3000";
  };

  networking.firewall = {
    # Only DNS on the LAN; the web UI on 3000 stays reachable over Tailscale (trusted interface).
    allowedTCPPorts = [ 53 ];
    allowedUDPPorts = [ 53 ];
  };

  services.resolved.enable = false;
}
