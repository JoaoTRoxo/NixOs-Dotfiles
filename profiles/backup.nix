{ config, lib, pkgs, ... }:

# Nightly consistent copy of the state worth keeping into /var/backup/aurea.
# The laptop (roxo@archlinux, ~/.local/bin/aurea-backup) pulls it over Tailscale
# with a key that can only read that directory, and keeps dated snapshots.
# Media isn't backed up: it can be downloaded again.
let
  staging = "/var/backup/aurea";
  sources = {
    gitea = "/var/lib/gitea";
    grafana = "/var/lib/grafana";
    radicale = "/var/lib/radicale";
    obsidian-couchdb = "/var/lib/obsidian-couchdb";
    adguardhome = "/var/lib/private/AdGuardHome";
    jellyfin = "/var/lib/jellyfin";
    jellyseerr = "/var/lib/jellyseerr";
    sonarr = "/var/lib/sonarr";
    radarr = "/var/lib/radarr";
    prowlarr = "/var/lib/prowlarr";
    bazarr = "/var/lib/bazarr";
    qbittorrent = "/var/lib/qbittorrent";
    nixos-config = "/home/aurea/NixOs-Dotfiles";
  };
  # Regenerated or re-downloaded on their own
  excludes = [ "cache/" "log/" "logs/" "transcodes/" "MediaCover/" "Backups/" "*-wal" "*-shm" "*-journal" ];
in
{
  systemd.services.backup-snapshot = {
    description = "Consistent copy of service state for the laptop to pull";
    path = with pkgs; [ rsync sqlite findutils ];
    serviceConfig = {
      Type = "oneshot";
      # Nice/idle IO plus --bwlimit: this disk's SATA link has failed under big write bursts
      Nice = 19;
      IOSchedulingClass = "idle";
    };
    script = ''
      install -d -m 700 ${staging}
      rm -f ${staging}/.complete

      ${lib.concatStrings (lib.mapAttrsToList (name: src: ''
        if [ -d ${src} ]; then
          rsync -a --delete --delete-excluded --bwlimit=20m ${lib.concatMapStringsSep " " (e: "--exclude='${e}'") excludes} ${src}/ ${staging}/${name}/
        fi
      '') sources)}

      # A plain copy of a live SQLite database can be torn; replace every one
      # with an online backup taken from the source.
      ${lib.concatStrings (lib.mapAttrsToList (name: src: ''
        find ${staging}/${name} -type f -size +0 2>/dev/null | while read -r copy; do
          if [ "$(head -c 15 "$copy")" = "SQLite format 3" ]; then
            sqlite3 "file:${src}/''${copy#${staging}/${name}/}?mode=ro" ".backup '$copy.tmp'" && mv "$copy.tmp" "$copy"
          fi
        done
      '') sources)}

      date --iso-8601=seconds > ${staging}/.complete
    '';
  };

  systemd.timers.backup-snapshot = {
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = "03:30";
      Persistent = true;
    };
  };

  # The laptop's backup key: read-only rsync of the staging directory, nothing else
  users.users.root.openssh.authorizedKeys.keys = [
    ''command="${pkgs.rrsync}/bin/rrsync -ro ${staging}",restrict ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEhcswCE3bAp2cKDa4vwnJQ2hKWIPJBQyvFibolGhpCC roxo@archlinux aurea-backup''
  ];
}
