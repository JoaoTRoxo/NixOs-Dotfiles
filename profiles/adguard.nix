{ config, pkgs, ... }:
{

  services.adguardhome = {
    enable = true;
    host = "0.0.0.0";
    port = 3000;
  };

  networking.firewall = {
    allowedTCPPorts = [ 53 3000 80 ];
    allowedUDPPorts = [ 53 ]; 
  };

  services.resolved.enable = false;
}
