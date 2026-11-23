{ config, lib, pkgs, ... }:

# Seerr's requests on Telegram, through its own dedicated bot (@aureaMedia_bot,
# separate from @AureaServerBot in alerts.nix): each new request is sent there, and one
# waiting for approval comes with buttons to approve or decline it, or to
# approve only some of a show's seasons. Decided in Seerr instead, its buttons
# go away. The bot is pkgs/seerr-telegram/ (Go, stdlib-only, x86-64-v1 safe);
# tests: (cd pkgs/seerr-telegram && go test -v -race ./...)
let
  bot = pkgs.callPackage ../pkgs/seerr-telegram/package.nix { };
in
{
  age.secrets.seerr-telegram.file = ../secrets/seerr-telegram.age;

  systemd.services.seerr-telegram = {
    description = "Seerr's requests on Telegram, with buttons to approve them";
    wantedBy = [ "multi-user.target" ];
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    environment = {
      SEERR_URL = "http://127.0.0.1:5055";
      SEERR_PUBLIC_URL = "https://requests.joaoroxo.com";
      GOMAXPROCS = "1";
      GOGC = "25";
      GOMEMLIMIT = "12MiB";
    };
    serviceConfig = {
      ExecStart = lib.getExe bot;
      # Dedicated Seerr Telegram bot secret and Seerr's API key from its own
      # settings (so there's no copy of it to keep in step)
      LoadCredential = [
        "telegram:${config.age.secrets.seerr-telegram.path}"
        "seerr-settings:/var/lib/jellyseerr/settings.json"
      ];
      DynamicUser = true;
      StateDirectory = "seerr-telegram";
      MemoryMax = "32M";
      # Seerr or Telegram being away is waited out inside; this is for crashes
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
