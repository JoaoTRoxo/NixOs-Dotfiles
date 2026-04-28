{ config, pkgs, ... }:

{
  imports =
    [
      ../../profiles/hardware/rnl-simaolavos-hw.nix
      ../../profiles/core.nix
      ../../profiles/network/rnl-simaolavos.nix
      ../../profiles/desktop.nix
    ];

  boot.loader.systemd-boot.enable = true;
  boot.loader.efi.canTouchEfiVariables = true;

  system.stateVersion = "25.05";

}
