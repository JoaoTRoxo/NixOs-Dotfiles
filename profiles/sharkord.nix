{ config, pkgs, ... }:

{
  virtualisation.oci-containers.containers."sharkord" = {
    image = "sharkord/sharkord:latest";
    ports = [
      "4991:4991/tcp"
      "40000:40000/tcp" 
      "40000:40000/udp"
    ];
    volumes = [
      "/var/lib/sharkord:/home/bun/.config/sharkord"
    ];
  };

  systemd.tmpfiles.rules = [
    "d /var/lib/sharkord 0755 1000 1000 -"
  ];
}
