{ config, pkgs, ... }:

{
  imports =
    
    [
      ../../profiles/monitoring.nix
      ../../profiles/docker.nix
      ../../profiles/media.nix
      ../../profiles/jellyfin.nix
      ../../profiles/network/aurea.nix
      ../../profiles/gitea.nix
      ../../profiles/hardware/aurea-hw.nix
      ../../profiles/cloudflare-tunnel.nix
      ../../profiles/adguard.nix
      ../../profiles/backup.nix
      ../../profiles/alerts.nix
      ../../profiles/seerr-telegram.nix
      ../../profiles/recyclarr.nix
      ../../profiles/core.nix
      ../../profiles/tailscale.nix
      ../../profiles/obsidian.nix
      ../../profiles/radicale.nix
      ../../profiles/vdirsyncer-bridge.nix
      ../../profiles/jrxd.nix
      ../../profiles/pulse.nix
    ];

  boot.loader.grub.enable = true;
  # By id, not /dev/sda: names move when a disk is added, and on 2026-10-28 the
  # second disk took sda, which would have put GRUB on it
  boot.loader.grub.device = "/dev/disk/by-id/ata-SAMSUNG_HD103SJ_S246J9EB918624";
  boot.loader.grub.useOSProber = false;

  #Don't shutdown with lid close
  services.logind.settings = {
    Login = {
      HandleLidSwitch = "ignore";
      HandleLidSwitchExternalPower = "ignore";
      HandleLidSwitchDocked = "ignore";
      # A short press shuts down cleanly; with "ignore" the only way off was
      # holding it (a hard power-off, as on 2026-10-20)
      HandlePowerKey = "poweroff";
    };
  };

  #Turn off the screen after some time
  boot.kernelParams = [ "consoleblank=300" ];

  users.users.root.openssh.authorizedKeys.keys = [ "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBvbx6tMWqkVWBiWxZ1WpUn2kT3ZPT+4xUQgNV4K98Gx roxo@archlinux" ];


  system.stateVersion = "25.05";

}
