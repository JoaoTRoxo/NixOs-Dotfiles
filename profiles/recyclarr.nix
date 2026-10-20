{ config, lib, pkgs, ... }:

# Keeps Sonarr/Radarr on the TRaSH guides' quality profiles, release scoring and
# size limits (https://trash-guides.info), synced daily. 1080p only: the disk is
# 1 TB, and Seerr used to request everything with "Any" (60 GB 4K remuxes).
# Existing shows and movies keep their old profiles, so nothing gets re-downloaded;
# new requests from Seerr use these.
{
  age.secrets.sonarr-api-key.file = ../secrets/sonarr-api-key.age;
  age.secrets.radarr-api-key.file = ../secrets/radarr-api-key.age;

  # Recyclarr 8.7.2's sync crashes in its live progress display when stdout isn't a
  # terminal ("Offset and length were out of bounds" in Spectre.Console), which it
  # never is under systemd. Give it a pseudo-terminal. (script runs the command
  # with $SHELL, and the recyclarr user's login shell is nologin.)
  systemd.services.recyclarr = {
    environment.NO_COLOR = "1";
    environment.SHELL = pkgs.runtimeShell;
    serviceConfig.ExecStart = lib.mkForce
      "${pkgs.util-linux}/bin/script -qec '${lib.getExe config.services.recyclarr.package} sync --config /var/lib/recyclarr/config.yml --log info' /dev/null";
  };

  services.recyclarr = {
    enable = true;
    configuration = {
      radarr.movies = {
        base_url = "http://127.0.0.1:7878";
        api_key._secret = config.age.secrets.radarr-api-key.path;
        quality_definition = {
          type = "movie";
          # The guide's 50.8 MB/min (6 GB for a 2 h film) rejects most x265 encodes
          qualities = [ { name = "Bluray-1080p"; min = 15; } ];
        };
        quality_profiles = [
          {
            trash_id = "d1d67249d3890e49bc12e275d989a7e9"; # HD Bluray + WEB
            reset_unmatched_scores.enabled = true;
          }
        ];
        custom_format_groups = {
          add = [
            { trash_id = "a3ac6af01d78e4f21fcb75f601ac96df"; } # [Unwanted] Unwanted Formats
          ];
          # Synced by default otherwise; it penalizes x265 at 1080p, and most of this library is x265
          skip = [ "f8bf8eab4617f12dfdbd16303d8da245" ]; # [Optional] Golden Rule HD
        };
      };

      sonarr.series = {
        base_url = "http://127.0.0.1:8989";
        api_key._secret = config.age.secrets.sonarr-api-key.path;
        # Sonarr v4 has one set of size limits per instance. The guide's "anime" set
        # is its "series" set with a 5 MB/min minimum everywhere; "series" would reject
        # small x265 encodes (Blu-ray 1080p min 50.4 MB/min = 1.2 GB per 24 min episode).
        quality_definition.type = "anime";
        quality_profiles = [
          {
            trash_id = "72dae194fc92bf828f32cde7744e51a1"; # WEB-1080p
            reset_unmatched_scores.enabled = true;
          }
          {
            # TRaSH's anime release ranking without the Remux tier and upgrade
            # churn: take the first good 1080p release (Blu-ray encode or WEB)
            # and keep it. Score >= 0 still rejects what TRaSH marks as bad
            # (LQ groups, raws).
            trash_id = "20e0fc959f1f1704bed501f23bdae76f"; # [Anime] Remux-1080p
            name = "Anime 1080p";
            min_format_score = 0;
            reset_unmatched_scores.enabled = true;
            upgrade = {
              allowed = false;
              until_quality = "Bluray-1080p";
            };
            qualities = [
              { name = "Bluray-1080p"; }
              { name = "WEB 1080p"; qualities = [ "WEBDL-1080p" "WEBRip-1080p" "HDTV-1080p" ]; }
              { name = "Bluray-720p"; }
              { name = "WEB 720p"; qualities = [ "WEBDL-720p" "WEBRip-720p" "HDTV-720p" ]; }
            ];
          }
        ];
        custom_format_groups = {
          add = [
            { trash_id = "85fae4a2294965b75710ef2989c850eb"; } # [Streaming Services] HD/UHD boost
            { trash_id = "59c3af66780d08332fdc64e68297098f"; } # [Unwanted] Unwanted Formats
          ];
          skip = [ "158188097a58d7687dee647e04af0da3" ]; # [Optional] Golden Rule HD, as for Radarr
        };
      };
    };
  };
}
