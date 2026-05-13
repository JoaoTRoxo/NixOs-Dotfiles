let
        roxo = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBvbx6tMWqkVWBiWxZ1WpUn2kT3ZPT+4xUQgNV4K98Gx roxo@archlinux";
	aurea = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHgprombM+WE1zGUnBGQr86EOtd2dKSJyT+by/FWEC6w aurea@aurea";
	hosts = [ aurea roxo ];
in {
    "vault-tunnel.age".publicKeys = [ aurea roxo ];
    "vw-secrets.age".publicKeys = [ aurea roxo ];
    "nextcloud-secret.age".publicKeys = [ aurea roxo ];
    "grafana-secret-key.age".publicKeys = [ aurea roxo ];
    "grafana-secret.age".publicKeys = [ aurea roxo ];
    "searxng-secret.age".publicKeys = [ aurea roxo ];
    }
