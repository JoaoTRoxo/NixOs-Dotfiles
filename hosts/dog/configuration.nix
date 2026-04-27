{ config, pkgs, ... }:

{
  imports =
    [
    ../../profiles/hardware/dog-hw.nix
	  ../../profiles/core.nix
	  ./home.nix
    ../../profiles/network/dog.nix
    ../../profiles/laptop.nix
    ];

  # Bootloader.
  boot.loader.systemd-boot.enable = true;
  boot.loader.efi.canTouchEfiVariables = true;

  system.stateVersion = "25.11";
}
