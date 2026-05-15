{ config, pkgs, ... }:

{
  imports =
    
    [
      ../../profiles/network/aurea.nix
      ../../profiles/gitea.nix
      ../../profiles/hardware/aurea-hw.nix
      ../../profiles/cloudflare-tunnel.nix
      ../../profiles/portfolio.nix
      ../../profiles/adguard.nix
      ../../profiles/core.nix
      ../../profiles/tailscale.nix
    ];

  boot.loader.grub.enable = true;
  boot.loader.grub.device = "/dev/sda";
  boot.loader.grub.useOSProber = true;

  #Don't shutdown with lid close
  services.logind.settings = {
    Login = {
      HandleLidSwitch = "ignore";
      HandleLidSwitchExternalPower = "ignore";
      HandleLidSwitchDocked = "ignore";
      HandlePowerKey = "ignore";
    };
  };

  #Turn off the screen after some time
  boot.kernelParams = [ "consoleblank=300" ];

  users.users.root.openssh.authorizedKeys.keys = [ "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBvbx6tMWqkVWBiWxZ1WpUn2kT3ZPT+4xUQgNV4K98Gx roxo@archlinux" ];


  system.stateVersion = "25.05";

}
