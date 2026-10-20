{ inputs, outputs, pkgs, ...}:
{

  time.timeZone = "Europe/Lisbon";

  i18n.defaultLocale = "en_US.UTF-8";


  i18n.extraLocaleSettings = {
    LC_ADDRESS = "pt_PT.UTF-8";
    LC_IDENTIFICATION = "pt_PT.UTF-8";
    LC_MEASUREMENT = "pt_PT.UTF-8";
    LC_MONETARY = "pt_PT.UTF-8";
    LC_NAME = "pt_PT.UTF-8";
    LC_NUMERIC = "pt_PT.UTF-8";
    LC_PAPER = "pt_PT.UTF-8";
    LC_TELEPHONE = "pt_PT.UTF-8";
    LC_TIME = "pt_PT.UTF-8";
  };

  nixpkgs.config.allowUnfree = true;

  console.keyMap = "pt-latin1";

  users.users.aurea = {
    isNormalUser = true;
    description = "João Roxo";
    extraGroups = [ "networkmanager" "wheel" "libvirtd" ];
    openssh.authorizedKeys.keys = [
      "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBvbx6tMWqkVWBiWxZ1WpUn2kT3ZPT+4xUQgNV4K98Gx roxo@archlinux"
    ];
  };

  documentation = {
    enable = true;
    man.enable = true;
    dev.enable = true;
  };

  environment.systemPackages = with pkgs; [
    vim-full
    wget
    git
    unzip
    gnumake
    gcc
    man-pages
    inputs.agenix.packages."${pkgs.stdenv.hostPlatform.system}".default
    python3
    ];

    # Headless: no X/LightDM/Bluetooth (they used RAM on an 8 GB box for nothing).
    # The console keeps the pt layout via console.keyMap above.

    services.openssh =  {
      enable = true;
      settings = {
        PermitRootLogin = "without-password";
        PasswordAuthentication = false;
        KbdInteractiveAuthentication = false;
      };

    };

    nix.settings.experimental-features = [ "nix-command" "flakes" ];

    nix.gc = {
      automatic = true;
      dates = "weekly";
      options = "--delete-older-than 14d";
    };

    # The Oct 15-20 restart loop filled the journal to 3.9 GB
    services.journald.settings.Journal.SystemMaxUse = "1G";

}
