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

    noctalia = {
      url = "github:noctalia-dev/noctalia-shell";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.noctalia-qs.follows = "noctalia-qs";
    };

    noctalia-qs = {
      url = "github:noctalia-dev/noctalia-qs";
      inputs.nixpkgs.follows = "nixpkgs";
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
        rnl-simaolavos = nixpkgs.lib.nixosSystem {
          system = "x86_64-linux";
          specialArgs = { inherit inputs; };
          modules = baseModules ++ [./hosts/rnl-simaolavos/configuration.nix];
        };
        dog = nixpkgs.lib.nixosSystem {
          system = "x86_64-linux";
          specialArgs = { inherit inputs; };
          modules = baseModules ++ [./hosts/dog/configuration.nix ./profiles/noctalia.nix];
        };
        zeno = nixpkgs.lib.nixosSystem {
          system = "x86_64-linux";
          specialArgs = { inherit inputs; };
          modules = baseModules ++ [./hosts/zeno/configuration.nix];
        };

      };
    };
  }
