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

  users.users.simaolavos = {
    isNormalUser = true;
    description = "Simão Lavos";
    extraGroups = [ "networkmanager" "wheel" "libvirtd" ];
    packages = with pkgs; [
      kdePackages.kate
      thunderbird
    ];
    openssh.authorizedKeys.keys = [
      "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINdDq2tDLBbOuSWGX6oT03uciK7u5HouhaE7DkVlFt/J simaolavos@dog"
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


    services.xserver = {
      enable = true;
      xkb = {
        layout = "pt";
        variant = "";
        options = "caps:escape";
      };
    };


    programs.bash = {
      blesh.enable = true;
    };

    services.openssh =  {
      enable = true;
      settings.PasswordAuthentication = false;
      settings.KbdInteractiveAuthentication = false;
    };

    nix.settings.experimental-features = [ "nix-command" "flakes" ];

    nix.gc = {
      automatic = true;
      dates = "weekly";
      options = "--delete-older-than 14d";
    };

    hardware.bluetooth.enable = true;

}
