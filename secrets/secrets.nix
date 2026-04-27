let
	rnl-simaolavos = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIA3jJ34aDHRG8KLbSU/4mhfUblVte4vjBPpcOocWKEkE root@RNL-SimaoLavos";
	zeno = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAfXG5G95rmqd9G9sJCujtZTJehQqW8z83JzBcaKDPTc root@nixos";
	hosts = [ rnl-simaolavos zeno ];
in {
    "vault-tunnel.age".publicKeys = [ zeno rnl-simaolavos ];
    "vw-secrets.age".publicKeys = [ zeno rnl-simaolavos ];
    "nextcloud-secret.age".publicKeys = [ zeno rnl-simaolavos ];
    "grafana-secret-key.age".publicKeys = [ zeno rnl-simaolavos ];
    "grafana-secret.age".publicKeys = [ zeno rnl-simaolavos ];
}
