{ config, inputs, ... }:

# jrxd, JRX's recorder of public market data (ADR-033 in JoaoTRoxo/jrx), writes
# raw capture files to the second disk. To update it: nix flake update jrx,
# then rebuild. `jrxd list /srv/jrx/raw <YYYY-MM-DD>` shows a day's files and gaps
let
  dataDir = "/srv/jrx/raw";
in
{
  imports = [ "${inputs.jrx}/packaging/nix/jrxd-service.nix" ];

  # Keep the jrx source out of garbage collection: sudo nixos-rebuild evaluates
  # as root, which has no GitHub key and can't fetch the private repo again
  system.extraDependencies = [ inputs.jrx ];

  # The recording disk, SAMSUNG HD501LJ S0MUJ1MP911549, formatted 2026-10-29.
  # nofail: aurea still boots without it, and jrxd then just doesn't start
  fileSystems."/srv/jrx" = {
    device = "/dev/disk/by-uuid/bdd59dfb-e5e9-41c5-8c34-3f8a3460535d";
    fsType = "ext4";
    options = [ "nofail" "noatime" ];
  };

  # A Binance API key with no permissions: it unlocks public SBE market data only
  age.secrets.jrxd-binance-key.file = ../secrets/jrxd-binance-key.age;

  services.jrxd = {
    enable = true;
    inherit dataDir;
    binanceApiKeyFile = config.age.secrets.jrxd-binance-key.path;
    settings.instruments = [
      { id = "hyperliquid-mainnet:BTC"; streams = [ "trades" "bbo" "l2Book" "l2Book-fast" ]; }
      { id = "binance-spot:BTCUSDT"; streams = [ "trade" "bookTicker" "bestBidAsk-sbe" "depth-sbe" ]; }
      { id = "binance-usdm:BTCUSDT"; streams = [ "trade" "bookTicker" "depth@100ms" ]; }
    ];
  };

  # jrxd list and print on the command line, and the recordings readable by
  # the aurea user without sudo (dataDir is 0750 jrxd:jrxd)
  environment.systemPackages = [ config.services.jrxd.package ];
  users.users.aurea.extraGroups = [ "jrxd" ];

  # Receive times are only as good as the clock. Unlike timesyncd, chrony tells
  # the kernel how far off the clock may be, and jrxd records that
  services.chrony.enable = true;
}
