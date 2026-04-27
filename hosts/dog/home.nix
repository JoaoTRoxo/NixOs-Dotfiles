{ inputs, pkgs, ... }:
{
  home-manager = {
    useGlobalPkgs = true;
    useUserPackages = true;
    extraSpecialArgs = { inherit inputs; };

    users.simaolavos = { pkgs, ... }: {
      home.stateVersion = "26.05";
      home.enableNixpkgsReleaseCheck = false;

      imports = [
        ../home/vim.nix
      ];
    };
  };

}
