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

  virtualisation.libvirtd.enable = true;

  boot.extraModprobeConfig = ''
    options kvm_amd nested=1
    options kvm ignore_msrs=1
  '';

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
