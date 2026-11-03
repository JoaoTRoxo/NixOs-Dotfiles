{pkgs, config, ...}:

{
  networking = {
    hostName = "aurea";
    networkmanager.enable = true;
    # Configured by dhcpcd below; keep NM's auto "Wired connection" DHCP
    # profiles off it, e.g. after the watchdog reloads the driver
    networkmanager.unmanaged = [ "enp1s0" ];

    # DHCP, not a static address: the router was replaced on 2026-11-03 and
    # the old static 192.168.100.0/24 settings left the box without IPv4
    interfaces.enp1s0.useDHCP = true;
    dhcpcd = {
      wait = "background";
      # The kernel keeps handling IPv6 router advertisements, so the tunnel and
      # Tailscale still work if DHCP doesn't
      extraConfig = ''
        ipv4only
        noipv4ll
      '';
    };

    nameservers = [ "1.1.1.1" "8.8.8.8" ];
  };

  # The onboard Attansic L1 (atl1) can lose link and never get it back: on
  # 2026-10-15 it went down a second after boot and the box was offline for
  # five days. Each minute, ping the gateway; after 3 failed minutes bounce the
  # link, after 6 reload the driver, after 15 reboot (at most once an hour, so a
  # router that's simply off doesn't cause a reboot loop).
  systemd.services.nic-watchdog = {
    description = "Recover enp1s0 when the gateway stops answering";
    path = with pkgs; [ iputils iproute2 kmod ];
    serviceConfig.Type = "oneshot";
    script = ''
      count=/run/nic-watchdog.failures
      # Telegram can't be reached while offline: leave a note that profiles/alerts.nix sends later
      pending=/var/lib/server-alerts/pending
      gateway=$(ip -4 route show default dev enp1s0 | sed -n 's/.*via \([^ ]*\).*/\1/p' | head -n 1)
      if [ -n "$gateway" ] && ping -q -c 3 -W 2 -I enp1s0 "$gateway" > /dev/null 2>&1; then
        if [ -e $count ]; then
          failures=$(cat $count)
          echo "gateway reachable again after $failures min"
          if [ "$failures" -ge 2 ]; then
            did="it came back on its own"
            if [ "$failures" -ge 3 ]; then did="the watchdog bounced the link"; fi
            if [ "$failures" -ge 6 ]; then did="the watchdog bounced the link and reloaded the atl1 driver"; fi
            mkdir -p $pending
            echo "🌐 aurea's network was down for about $failures min; $did." > "$pending/network-$(date +%s)"
          fi
          rm -f $count
        fi
        exit 0
      fi

      failures=$(( $(cat $count 2>/dev/null || echo 0) + 1 ))
      echo $failures > $count
      echo "gateway unreachable ($failures min, carrier=$(cat /sys/class/net/enp1s0/carrier 2>/dev/null || echo none))"

      case $failures in
        3)
          echo "bouncing enp1s0"
          ip link set enp1s0 down; sleep 2; ip link set enp1s0 up; sleep 5
          systemctl restart dhcpcd.service
          ;;
        6)
          echo "reloading the atl1 driver"
          modprobe -r atl1; sleep 2; modprobe atl1; sleep 10
          systemctl restart dhcpcd.service
          ;;
        *)
          if [ $failures -ge 15 ] && [ "$(cut -d. -f1 /proc/uptime)" -gt 3600 ]; then
            echo "still offline, rebooting"
            mkdir -p /var/lib/server-alerts
            echo "gateway unreachable for $failures min, link bounce and driver reload didn't help" > /var/lib/server-alerts/watchdog-reboot
            systemctl reboot
          fi
          ;;
      esac
    '';
  };

  systemd.timers.nic-watchdog = {
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "5m";
      OnUnitActiveSec = "1m";
    };
  };
}
