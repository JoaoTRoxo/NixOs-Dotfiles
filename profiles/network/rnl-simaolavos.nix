{inputs, pkgs, config, lib, ...}:

let

  privateFile = ../private.nix;

  private = if builtins.pathExists privateFile
            then import privateFile
            else { rnl-ipv4 = "0.0.0.0"; rnl-ipv6 = "0.0.0.0"; rnl-gateway = "0.0.0.0"; };
in {
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
          address = private.rnl-ipv4;
          prefixLength = 27;
        }];
      };

      ipv6 = {
        addresses = [{
          address = private.rnl-ipv6;
          prefixLength = 64;
        }];
      };

    };
    firewall = {
      enable = true;
      allowedUDPPorts = [ 9 ];
    };

    defaultGateway = private.rnl-gateway;
    nameservers = [ "1.1.1.1" ];
  };
}
