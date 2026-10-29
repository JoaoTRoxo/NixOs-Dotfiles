{

  description = "My Nix configurations";

  inputs = {
    nixpkgs.url = "nixpkgs/nixos-unstable";

    home-manager = {
      url = "github:nix-community/home-manager";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    agenix = {
      url = "github:ryantm/agenix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # JRX, for its recorder jrxd (profiles/jrxd.nix). Not a flake: its NixOS
    # module and package are plain files in the repo. Private, so over SSH
    jrx = {
      url = "git+ssh://git@github.com/JoaoTRoxo/jrx.git?ref=main";
      flake = false;
    };

  };

    outputs = { self, nixpkgs, ...}@inputs:
    let
      baseModules = [
        inputs.agenix.nixosModules.default
        inputs.home-manager.nixosModules.default
      ];
    in {
      nixosConfigurations = {
        aurea = nixpkgs.lib.nixosSystem {
          system = "x86_64-linux";
          specialArgs = { inherit inputs; };
          modules = baseModules ++ [./hosts/aurea/configuration.nix];
        };

      };
    };
  }
