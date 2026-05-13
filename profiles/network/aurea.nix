{inputs, pkgs, config, ...}:
{
  networking = {
    hostName = "zeno";

    networkmanager.enable = true;

    interfaces.enp4s0 = {
      ipv4 = {
        addresses = [{
          address = "192.168.1.88";
          prefixLength = 24;
        }];
      };

    };

    defaultGateway = "192.168.1.1";
    nameservers = [ "1.1.1.1" "8.8.8.8" ];
  };
}
