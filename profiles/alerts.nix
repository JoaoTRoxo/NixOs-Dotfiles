{ config, lib, pkgs, ... }:

# Telegram alerts (@AureaServerBot) for the kind of thing that failed silently
# before: the calendar sync was broken for 6 weeks, a 5-day outage went
# unnoticed and the containers restart-looped ~25k times. A problem is reported
# once when it starts and once when it clears; if Telegram can't be reached,
# it's retried on the next run.
let
  state = "/var/lib/server-alerts";

  # Disks to watch, as name=device. By id, because /dev/sdX names move when a
  # disk is added: on 2026-10-28 the second disk took sda.
  disks = lib.concatStringsSep " " (lib.mapAttrsToList (name: device: "${name}=${device}") {
    system = "/dev/disk/by-id/ata-SAMSUNG_HD103SJ_S246J9EB918624";
    recordings = "/dev/disk/by-id/ata-SAMSUNG_HD501LJ_S0MUJ1MP911549";
  });

  notify = pkgs.writeShellApplication {
    name = "telegram-notify";
    runtimeInputs = [ pkgs.curl ];
    text = ''
      # telegram-notify <message...>
      # shellcheck disable=SC1091
      . ${config.age.secrets.telegram-alerts.path}
      msg="$*"
      # The token goes in via a curl config on stdin, so it never shows up in `ps`
      printf 'url = "https://api.telegram.org/bot%s/sendMessage"\n' "$TELEGRAM_BOT_TOKEN" |
        curl -sS --fail --max-time 30 --retry 3 -K - \
          --data-urlencode "chat_id=$TELEGRAM_CHAT_ID" \
          --data-urlencode "text=''${msg:0:4000}" > /dev/null
    '';
  };

  check = pkgs.writeShellApplication {
    name = "server-alerts-check";
    runtimeInputs = with pkgs; [ notify smartmontools jq gawk gnugrep coreutils systemd openssh ];
    text = ''
      mkdir -p ${state}/active ${state}/restarts
      now=$(date +%s)
      last=$(cat ${state}/last-run 2>/dev/null || echo $((now - 300)))
      seen=()

      # problem <key> <message>: report once while the condition holds
      problem() {
        seen+=("$1")
        if [ ! -e "${state}/active/$1" ]; then
          telegram-notify "$2" && printf '%s' "$2" > "${state}/active/$1"
        fi
        return 0
      }

      # Failed units, with the end of their log
      while read -r unit; do
        [ -n "$unit" ] || continue
        problem "failed-$unit" "❌ aurea: $unit failed
      $(journalctl -u "$unit" -n 12 -o cat --no-pager | cut -c1-200)"
      done < <(systemctl list-units --state=failed --plain --no-legend | awk '{print $1}')

      # Restart loops (a Restart=always service that keeps crashing never shows as "failed")
      id="" n=""
      while IFS= read -r line; do
        case $line in
          Id=*) id=''${line#Id=} ;;
          NRestarts=*) n=''${line#NRestarts=} ;;
          "")
            if [ -n "$id" ] && [ -n "$n" ]; then
              prev=$(cat "${state}/restarts/$id" 2>/dev/null || echo "$n")
              echo "$n" > "${state}/restarts/$id"
              if [ "$n" -ge $((prev + 3)) ]; then
                problem "looping-$id" "🔁 aurea: $id restarted $((n - prev)) times in the last few minutes"
              fi
            fi
            id="" n=""
            ;;
        esac
      done < <(systemctl show --property=Id,NRestarts '*.service'; echo)

      # Disk space
      use=$(df --output=pcent / | tail -n 1 | tr -dc 0-9)
      if [ "$use" -ge 90 ]; then
        problem disk-full "💾 aurea: / is $use% full ($(df -h --output=avail / | tail -n 1 | tr -d ' ') free)"
      fi

      # SMART, per disk: overall health, and any growth in the counters that matter.
      # The counters go by id, because drives name them differently: the
      # recordings disk calls 197 Total_Pending_Sectors, not Current_Pending_Sector
      for disk in ${disks}; do
        name=''${disk%%=*} device=''${disk#*=}
        if [ ! -e "$device" ]; then
          problem "disk-missing-$name" "🧲 aurea: the $name disk is missing ($device)"
          continue
        fi
        smart=$(smartctl -j -H -A "$device" || true)
        if [ "$(jq -r '.smart_status.passed' <<< "$smart")" = false ]; then
          problem "smart-failing-$name" "🧲 aurea: the $name disk's SMART health check FAILED. Back up and replace it."
        fi
        # 5 reallocated, 197 pending, 198 offline uncorrectable, 199 SATA CRC errors
        for id in 5 197 198 199; do
          read -r attr value < <(jq -r --argjson i "$id" '.ata_smart_attributes.table[]? | select(.id == $i) | "\(.name) \(.raw.value)"' <<< "$smart") || continue
          before=$(cat "${state}/smart-$name-$attr" 2>/dev/null || echo "$value")
          if [ "$value" -gt "$before" ]; then
            hint="the drive is growing bad sectors"
            if [ "$id" = 199 ]; then hint="transmission errors: SATA cable or port"; fi
            telegram-notify "🧲 aurea: $name disk $attr went $before → $value ($hint)" || continue
          fi
          echo "$value" > "${state}/smart-$name-$attr"
        done
      done

      # SATA link / I/O errors in the kernel log since the last run
      errors=$(journalctl -k -q --no-pager --since "@$last" | grep -cE 'ICRC ABRT|exception Emask|I/O error' || true)
      if [ "$errors" -gt 0 ]; then
        telegram-notify "⚠️ aurea: $errors disk/SATA errors in the kernel log in the last few minutes" || now=$last
      fi

      # Backups: the server's nightly snapshot, and the laptop actually pulling it
      complete=/var/backup/aurea/.complete
      if [ ! -e $complete ] || [ $((now - $(stat -c %Y $complete))) -gt $((36 * 3600)) ]; then
        problem backup-snapshot-stale "🗄 aurea: the nightly backup snapshot hasn't completed in over 36 h"
      fi
      fp=$(ssh-keygen -lf - <<< "${config.alerts.backupKey}" | awk '{print $2}')
      # (not grep -q: exiting early kills journalctl with SIGPIPE, which pipefail counts as failure)
      if ! journalctl -q --no-pager --since "-5 days" -t sshd-session -t sshd | grep -F "$fp" > /dev/null; then
        problem backup-not-pulled "💻 aurea: the laptop hasn't pulled a backup in 5 days"
      fi

      # Notes left by units that couldn't reach Telegram at the time (e.g. the NIC watchdog)
      for f in ${state}/pending/*; do
        [ -e "$f" ] || continue
        telegram-notify "$(cat "$f")" && rm -f "$f"
      done

      # Clear what's no longer a problem
      for f in ${state}/active/*; do
        [ -e "$f" ] || continue
        key=''${f##*/}
        if [[ " ''${seen[*]} " != *" $key "* ]]; then
          telegram-notify "✅ resolved: $(head -n 1 "$f")" && rm -f "$f"
        fi
      done

      echo "$now" > ${state}/last-run
    '';
  };
in
{
  options.alerts.backupKey = lib.mkOption {
    type = lib.types.str;
    description = "Public key the laptop pulls backups with (to check it still does).";
  };

  config = {
    age.secrets.telegram-alerts.file = ../secrets/telegram-alerts.age;

    environment.systemPackages = [ notify ];

    systemd.services.server-alerts = {
      description = "Check for problems and report them on Telegram";
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      serviceConfig = {
        Type = "oneshot";
        ExecStart = lib.getExe check;
        StateDirectory = "server-alerts";
      };
    };
    systemd.timers.server-alerts = {
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnBootSec = "3m";
        OnUnitActiveSec = "5m";
      };
    };

    # Say when it boots, and whether the previous boot ended cleanly
    systemd.services.server-alerts-boot = {
      description = "Report the boot on Telegram";
      wantedBy = [ "multi-user.target" ];
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      path = [ notify pkgs.getent ];
      serviceConfig = {
        Type = "oneshot";
        StateDirectory = "server-alerts";
      };
      script = ''
        # Only on a real boot, not when nixos-rebuild switch starts this unit
        if [ "$(cut -d. -f1 /proc/uptime)" -gt 900 ]; then exit 0; fi
        # Wait for DNS: the NIC can take a while to come up
        for _ in $(seq 60); do getent hosts api.telegram.org > /dev/null && break; sleep 5; done
        if [ -e ${state}/watchdog-reboot ]; then
          how="the network watchdog rebooted it ($(cat ${state}/watchdog-reboot))"
          rm -f ${state}/watchdog-reboot
        elif journalctl -b -1 -n 30 -o cat --no-pager 2>/dev/null | grep "Journal stopped" > /dev/null; then
          how="the previous shutdown was clean"
        else
          how="the previous boot ended WITHOUT a clean shutdown (power loss, crash or forced power-off)"
        fi
        telegram-notify "🔌 aurea booted: $how"
      '';
    };

    # Weekly all-clear: if these stop arriving, the alerts themselves are broken
    systemd.services.server-alerts-weekly = {
      description = "Weekly status on Telegram";
      path = [ notify pkgs.smartmontools pkgs.jq ];
      serviceConfig.Type = "oneshot";
      script = ''
        crc=""
        for disk in ${disks}; do
          crc="$crc ''${disk%%=*} $(smartctl -j -A "''${disk#*=}" | jq -r '.ata_smart_attributes.table[]? | select(.id == 199) | .raw.value')"
        done
        failed=$(systemctl list-units --state=failed --plain --no-legend | wc -l)
        telegram-notify "📊 aurea weekly
      up $(( $(cut -d. -f1 /proc/uptime) / 86400 )) days, $failed failed units
      disk $(df -h --output=pcent / | tail -n 1 | tr -d ' ') used, $(df -h --output=avail / | tail -n 1 | tr -d ' ') free
      SATA CRC errors (total):$crc
      last backup snapshot: $(cat /var/backup/aurea/.complete 2>/dev/null || echo never)"
      '';
    };
    systemd.timers.server-alerts-weekly = {
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnCalendar = "Sun 11:00";
        Persistent = true;
      };
    };
  };
}
