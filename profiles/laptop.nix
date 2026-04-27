{ config, pkgs, inputs, ...}:

{
  services.pulseaudio.enable = false;
  security.rtkit.enable = true;
  services.pipewire = {
    enable = true;
    alsa.enable = true;
    alsa.support32Bit = true;
    pulse.enable = true;
  };

  programs.hyprland.enable = true;
  services.upower.enable = true;

  environment.systemPackages = with pkgs; [
    vesktop
    inputs.noctalia.packages.${pkgs.stdenv.hostPlatform.system}.default
    hyprland
    waybar
    upower
    wireguard-tools
  ];
}
