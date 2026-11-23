{ lib, pkgs, ... }:

# The subtitles viewers ask for in Jelly, fetched without anyone approving
# them: Jelly's request is an issue in Seerr, and this has Bazarr search for
# it, then resolves the issue. What can't be found stays open, with a comment
# saying what's missing. The worker is pkgs/seerr-subtitles/ (Go, stdlib-only,
# x86-64-v1 safe); tests: (cd pkgs/seerr-subtitles && go test -v -race ./...)
let
  worker = pkgs.callPackage ../pkgs/seerr-subtitles/package.nix { };
in
{
  systemd.services.seerr-subtitles = {
    description = "Subtitles requested through Seerr, fetched by Bazarr";
    wantedBy = [ "multi-user.target" ];
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    environment = {
      SEERR_URL = "http://127.0.0.1:5055";
      BAZARR_URL = "http://127.0.0.1:6767";
      GOMAXPROCS = "1";
      GOGC = "25";
      GOMEMLIMIT = "12MiB";
    };
    serviceConfig = {
      ExecStart = lib.getExe worker;
      # Seerr's and Bazarr's API keys from their own settings (so there's no
      # copy of them to keep in step)
      LoadCredential = [
        "seerr-settings:/var/lib/jellyseerr/settings.json"
        "bazarr-config:/var/lib/bazarr/config/config.yaml"
      ];
      DynamicUser = true;
      StateDirectory = "seerr-subtitles";
      MemoryMax = "32M";
      # Seerr or Bazarr being away is waited out inside; this is for crashes
      Restart = "always";
      RestartSec = 30;

      # Hardening
      NoNewPrivileges = true;
      PrivateTmp = true;
      ProtectSystem = "strict";
      ProtectHome = true;
      ProtectKernelTunables = true;
      ProtectKernelModules = true;
      ProtectControlGroups = true;
      RestrictNamespaces = true;
      LockPersonality = true;
      MemoryDenyWriteExecute = true;
      RestrictAddressFamilies = [ "AF_INET" "AF_INET6" ];
      SystemCallArchitectures = "native";
    };
  };
}
