{ config, inputs, ... }:

# jrxd, JRX's recorder of public market data (ADR-033 in JoaoTRoxo/jrx), writes
# raw capture files to the second disk. To update it: nix flake update jrx,
# then rebuild. `jrxd list /srv/jrx/raw <YYYY-MM-DD>` shows a day's files and gaps
let
  dataDir = "/srv/jrx/raw";
  hyperliquidStreams = [ "trades" "bbo" "l2Book" "l2Book-fast" ];
  # HIP-3 markets, the perps builders deploy on Hyperliquid: the top 20 by 24 h
  # volume on 2026-10-29 (83% of HIP-3 volume). A fixed list, so each market's
  # history stays unbroken: add markets as they matter, drop only delisted ones
  hip3Markets = [
    "xyz:SKHX" "xyz:GOOGL" "xyz:XYZ100" "xyz:CL" "xyz:SP500"
    "xyz:MU" "xyz:SOXL" "xyz:SKHY" "xyz:SNDK" "xyz:SILVER"
    "xyz:DRAM" "xyz:BRENTOIL" "xyz:MSFT" "xyz:SPCX" "xyz:SMSN"
    "xyz:INTC" "xyz:META" "xyz:NVDA" "xyz:MRVL" "xyz:AMD"
  ];
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
    # Hyperliquid instruments of a stream share one connection (jrx #201)
    settings.instruments = [
      { id = "hyperliquid-mainnet:BTC"; streams = hyperliquidStreams; }
      { id = "binance-spot:BTCUSDT"; streams = [ "trade" "bookTicker" "bestBidAsk-sbe" "depth-sbe" ]; }
      { id = "binance-usdm:BTCUSDT"; streams = [ "trade" "bookTicker" "depth@100ms" ]; }
    ] ++ map (market: { id = "hyperliquid-mainnet:${market}"; streams = hyperliquidStreams; }) hip3Markets;
  };

  # jrxd list and print on the command line, and the recordings readable by
  # the aurea user without sudo (dataDir is 0750 jrxd:jrxd)
  environment.systemPackages = [ config.services.jrxd.package ];
  users.users.aurea.extraGroups = [ "jrxd" ];

  # Receive times are only as good as the clock. Unlike timesyncd, chrony tells
  # the kernel how far off the clock may be, and jrxd records that. chrony
  # clears the kernel's unsynchronised flag only with rtcsync, which conflicts
  # with the module's RTC trimming; without it every jrxd clock record says
  # synced: false (jrx #197)
  services.chrony = {
    enable = true;
    enableRTCTrimming = false;
    extraConfig = "rtcsync";
  };
}
