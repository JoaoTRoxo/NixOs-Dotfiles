{ config, lib, pkgs, ... }:

let
  containers = config.virtualisation.oci-containers.containers;
  autoUpdated = lib.filter (name: lib.hasSuffix ":latest" containers.${name}.image) (lib.attrNames containers);

  # Published container ports nobody on the LAN needs: the web UIs are used over
  # Tailscale or through Caddy, and the containers reach each other via docker0.
  # (8096 Jellyfin and 6881 BitTorrent stay open.)
  lanBlockedPorts = [ 5055 5984 6767 7878 8080 8191 8989 9696 ];
in
{
  virtualisation.docker.enable = true;
  virtualisation.oci-containers.backend = "docker";
  networking.firewall.trustedInterfaces = [ "docker0" ];

  # Docker's port publishing has its own iptables rules that bypass the NixOS
  # firewall, so the ports above were open to the whole LAN. DOCKER-USER is the
  # chain Docker leaves to us; --ctdir ORIGINAL keeps replies to containers' own
  # outgoing connections working.
  networking.firewall.extraCommands = ''
    iptables -N DOCKER-USER 2>/dev/null || true
    iptables -F DOCKER-USER
    ${lib.concatMapStrings (port: ''
      iptables -A DOCKER-USER -i enp1s0 -p tcp -m conntrack --ctdir ORIGINAL --ctorigdstport ${toString port} -j DROP
    '') lanBlockedPorts}
  '';
  networking.firewall.extraStopCommands = ''
    iptables -F DOCKER-USER 2>/dev/null || true
  '';

  # Containers used to have `pull = "always"`, so without DNS they couldn't even
  # start: during the 2026-10-15..20 network outage they restart-looped ~25k
  # times each. Now they start from the local image and this pulls :latest daily.
  systemd.services.oci-containers-update = {
    description = "Pull newer :latest container images and restart what changed";
    after = [ "network-online.target" "docker.service" ];
    wants = [ "network-online.target" ];
    path = [ config.virtualisation.docker.package ];
    serviceConfig.Type = "oneshot";
    script = lib.concatMapStrings (name: ''
      running=$(docker inspect --format '{{.Image}}' ${name} 2>/dev/null || true)
      if docker pull --quiet ${containers.${name}.image} > /dev/null; then
        if [ "$running" != "$(docker image inspect --format '{{.Id}}' ${containers.${name}.image})" ]; then
          echo "${name}: new image, restarting"
          systemctl restart docker-${name}.service
        fi
      else
        echo "${name}: pull failed, keeping the current image" >&2
      fi
    '') autoUpdated + ''
      docker image prune --force > /dev/null
    '';
  };

  systemd.timers.oci-containers-update = {
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = "05:00";
      RandomizedDelaySec = "30m";
      Persistent = true;
    };
  };
}
