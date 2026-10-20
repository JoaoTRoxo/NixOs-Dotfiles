{ config, pkgs, ... }:
{

  services.adguardhome = {
    enable = true;
    settings.http.address = "0.0.0.0:3000";
  };

  networking.firewall = {
    allowedTCPPorts = [ 53 3000 ];
    allowedUDPPorts = [ 53 ]; 
  };

  services.resolved.enable = false;
}
