{ config, pkgs, ...}:

{

  services.displayManager.sddm.enable = true;
  services.desktopManager.plasma6.enable = true;

  services.printing.enable = true;

  services.pulseaudio.enable = false;
  security.rtkit.enable = true;
  services.pipewire = {
    enable = true;
    alsa.enable = true;
    alsa.support32Bit = true;
    pulse.enable = true;
  };

  virtualisation.docker.enable = true;

  users.users.simaolavos.extraGroups = [ "docker" ];


  programs.firefox.enable = true;

  programs.bash = {
    interactiveShellInit = "eval \"$(atuin init bash)\"";
  };


  virtualisation = {
    libvirtd = {
      enable = true;
      onBoot = "ignore";
      qemu = {
        runAsRoot = false;
      };
    };
  };

  environment.systemPackages = with pkgs; [
    fastfetch
    wakeonlan
    spotify
    blesh
    alacritty
    tealdeer
    dropbox
    zed-editor
    atuin
    docker
    virt-manager
  ];
}
