{ config, pkgs, ... }:

{
  imports =
    [
      ../../profiles/network/zeno.nix
      ../../profiles/hardware/zeno-hw.nix
      ../../profiles/core.nix
      ../../profiles/vaultwarden.nix
      ../../profiles/cloudflare-tunnel.nix
      ../../profiles/navidrome.nix
      ../../profiles/tailscale.nix
      ../../profiles/nextcloud.nix
      ../../profiles/adguard.nix
      ../../profiles/monitoring.nix
      ../../profiles/searxng.nix
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

  users.users.root.openssh.authorizedKeys.keys = [ "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINdDq2tDLBbOuSWGX6oT03uciK7u5HouhaE7DkVlFt/J simaolavos@dog" ];


  system.stateVersion = "25.05";

}
