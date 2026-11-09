{ pkgs, ... }:

# The subtitles viewers ask for in Jelly, fetched without anyone approving
# them: Jelly's request is an issue in Seerr, and this has Bazarr search for
# it, then resolves the issue. What can't be found stays open, with a comment
# saying what's missing. The worker is pkgs/seerr-subtitles.py; its tests:
# python3 pkgs/seerr-subtitles-test.py
let
  worker = pkgs.writers.writePython3Bin "seerr-subtitles" { }
    (builtins.readFile ../pkgs/seerr-subtitles.py);
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
      # Its messages in the journal as they happen
      PYTHONUNBUFFERED = "1";
    };
    serviceConfig = {
      ExecStart = "${worker}/bin/seerr-subtitles";
      # Seerr's and Bazarr's API keys from their own settings (so there's no
      # copy of them to keep in step)
      LoadCredential = [
        "seerr-settings:/var/lib/jellyseerr/settings.json"
        "bazarr-config:/var/lib/bazarr/config/config.yaml"
      ];
      DynamicUser = true;
      StateDirectory = "seerr-subtitles";
      # Seerr or Bazarr being away is waited out inside; this is for crashes
      Restart = "always";
      RestartSec = 30;
    };
  };
}
