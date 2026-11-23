{ lib, buildGoModule }:

buildGoModule {
  pname = "seerr-telegram";
  version = "0.1.0";

  src = lib.cleanSource ./.;

  # Zero external Go dependencies.
  vendorHash = null;

  env = {
    CGO_ENABLED = "0";
    # Core 2 Quad Q6600 (Kentsfield) is x86-64-v1 only.
    GOAMD64 = "v1";
  };
  ldflags = [ "-s" "-w" ];
  tags = [ "netgo" ];

  meta = {
    description = "Seerr requests on Telegram with inline approval and season picker buttons";
    mainProgram = "seerr-telegram";
    platforms = lib.platforms.linux;
  };
}
