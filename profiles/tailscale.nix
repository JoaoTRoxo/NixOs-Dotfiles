{ config, pkgs, ... }:
{
  services.tailscale.enable = true;

  networking.firewall.checkReversePath = "loose";
  networking.firewall.trustedInterfaces = [ "tailscale0" ];
}
