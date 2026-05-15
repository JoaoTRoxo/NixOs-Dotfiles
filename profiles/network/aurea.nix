{pkgs, config, ...}:

{
  networking = {
    hostName = "aurea";
    networkmanager.enable = true;

    interfaces.enp1s0 = {
      ipv4.addresses = [{
        address = "192.168.100.5";
        prefixLength = 24;
      }];
    };

    defaultGateway = "192.168.100.1";
    nameservers = [ "1.1.1.1" "8.8.8.8" ];
  };
}
