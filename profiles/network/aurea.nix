{pkgs, config, ...}:

{
  networking = {
    hostName = "aurea";
    networkmanager.enable = true;
    # Configured by the static setup below (network-addresses-enp1s0); keep NM's
    # auto "Wired connection" DHCP profiles off it, e.g. after the watchdog reloads the driver
    networkmanager.unmanaged = [ "enp1s0" ];

    interfaces.enp1s0 = {
      ipv4.addresses = [{
        address = "192.168.100.5";
        prefixLength = 24;
      }];
    };

    defaultGateway = "192.168.100.1";
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
      if ping -q -c 3 -W 2 -I enp1s0 192.168.100.1 > /dev/null 2>&1; then
        if [ -e $count ]; then echo "gateway reachable again"; rm -f $count; fi
        exit 0
      fi

      failures=$(( $(cat $count 2>/dev/null || echo 0) + 1 ))
      echo $failures > $count
      echo "gateway unreachable ($failures min, carrier=$(cat /sys/class/net/enp1s0/carrier 2>/dev/null || echo none))"

      case $failures in
        3)
          echo "bouncing enp1s0"
          ip link set enp1s0 down; sleep 2; ip link set enp1s0 up; sleep 5
          systemctl restart network-addresses-enp1s0.service
          ;;
        6)
          echo "reloading the atl1 driver"
          modprobe -r atl1; sleep 2; modprobe atl1; sleep 10
          systemctl restart network-addresses-enp1s0.service
          ;;
        *)
          if [ $failures -ge 15 ] && [ "$(cut -d. -f1 /proc/uptime)" -gt 3600 ]; then
            echo "still offline, rebooting"
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
