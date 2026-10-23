{ config, pkgs, ... }:

# Seerr's requests on Telegram, in the alerts' chat (@AureaServerBot, see
# alerts.nix): each new one is sent there, and one waiting for approval comes
# with buttons to approve or decline it, or to approve only some of a show's
# seasons. Decided in Seerr instead, its buttons go away. The bot is
# pkgs/seerr-telegram.py; its tests: python3 pkgs/seerr-telegram-test.py
let
  bot = pkgs.writers.writePython3Bin "seerr-telegram" { }
    (builtins.readFile ../pkgs/seerr-telegram.py);
in
{
  systemd.services.seerr-telegram = {
    description = "Seerr's requests on Telegram, with buttons to approve them";
    wantedBy = [ "multi-user.target" ];
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    environment = {
      SEERR_URL = "http://127.0.0.1:5055";
      SEERR_PUBLIC_URL = "https://requests.joaoroxo.com";
      # Its messages in the journal as they happen
      PYTHONUNBUFFERED = "1";
    };
    serviceConfig = {
      ExecStart = "${bot}/bin/seerr-telegram";
      # The alerts' bot and chat, and Seerr's API key from its own settings
      # (so there's no copy of it to keep in step)
      LoadCredential = [
        "telegram:${config.age.secrets.telegram-alerts.path}"
        "seerr-settings:/var/lib/jellyseerr/settings.json"
      ];
      DynamicUser = true;
      StateDirectory = "seerr-telegram";
      # Seerr or Telegram being away is waited out inside; this is for crashes
      Restart = "always";
      RestartSec = 30;
    };
  };
}
