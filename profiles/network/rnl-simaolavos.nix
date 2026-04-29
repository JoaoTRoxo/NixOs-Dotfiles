{inputs, pkgs, config, lib, ...}:
{
  networking = {
    hostName = "rnl-simaolavos";

    networkmanager.enable = true;

    interfaces.enp4s0 = {
      wakeOnLan = {
        enable = true;
        policy = ["magic"];
      };
      ipv4 = {
        addresses = [{
          address = "193.136.164.195";
          prefixLength = 27;
        }];
      };

      ipv6 = {
        addresses = [{
          address = "2001:690:2100:82::195";
          prefixLength = 64;
        }];
      };



    };
    firewall = {
      enable = true;
      allowedUDPPorts = [ 9 ];
    };
    nat = {
      enable = true;
      internalInterfaces = [ "virbr0" ];
      externalInterface = "enp4s0";

    };

    defaultGateway = "193.136.164.222";
    nameservers = [ "1.1.1.1" ];
  };

}
