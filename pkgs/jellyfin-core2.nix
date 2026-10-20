# Jellyfin from nixpkgs, adjusted to run on this Core 2 Quad (no SSE4.1/SSE4.2/POPCNT):
#  - the bundled libe_sqlite3.so (SSE4.1) is replaced by ./e_sqlite3.nix;
#  - the build fails if Jellyfin moves to .NET 11+, whose JIT requires x86-64-v2;
#  - passthru.tests.core2-smoke boots the server, scans a 10-bit HEVC clip and
#    serves a resized thumbnail. profiles/jellyfin.nix puts it in system.checks,
#    so it runs on this CPU during nixos-rebuild: an update that needs newer
#    instructions fails the build instead of crashing the running server.
{
  lib,
  runCommand,
  callPackage,
  jellyfin,
  jellyfin-ffmpeg,
  binutils,
  curl,
  jq,
}:

let
  e_sqlite3 = callPackage ./e_sqlite3.nix { };

  package =
    runCommand "jellyfin-core2-${jellyfin.version}"
      {
        inherit (jellyfin) version;
        nativeBuildInputs = [ binutils ];
        passthru = {
          inherit e_sqlite3;
          unpatched = jellyfin;
          tests.core2-smoke = smoke;
        };
        meta = jellyfin.meta // {
          mainProgram = "jellyfin";
        };
      }
      ''
        cp -r ${jellyfin} $out
        chmod -R u+w $out
        install -m555 ${e_sqlite3}/lib/libe_sqlite3.so $out/lib/jellyfin/libe_sqlite3.so

        # The apphost finds its .dll/.so files next to its real path, so it must be our copy
        substituteInPlace $out/bin/jellyfin \
          --replace-fail '${jellyfin}/lib/jellyfin/jellyfin' "$out/lib/jellyfin/jellyfin"
        if grep -rq '${jellyfin}' $out; then
          echo "error: $out still references ${jellyfin}" >&2
          exit 1
        fi

        dotnet=$(sed -n "s/^export DOTNET_ROOT='\(.*\)'$/\1/p" $out/bin/jellyfin)
        for runtime in "$dotnet"/shared/Microsoft.NETCore.App/*; do
          version=''${runtime##*/}
          if [ "''${version%%.*}" -ge 11 ]; then
            echo "error: Jellyfin ${jellyfin.version} runs on .NET $version." >&2
            echo ".NET 11 requires x86-64-v2 (SSE4.2, POPCNT) and won't start on a Core 2." >&2
            echo "Stay on a nixpkgs whose Jellyfin targets .NET 10 (e.g. pin the flake input)." >&2
            exit 1
          fi
        done

        bash ${./core2-isa-check.sh} --strict $out/lib/jellyfin/libe_sqlite3.so
        echo "Other bundled native libraries (AVX here is behind runtime CPU dispatch):"
        bash ${./core2-isa-check.sh} $out/lib/jellyfin/*.so
      '';

  smoke =
    runCommand "jellyfin-core2-${jellyfin.version}-smoke"
      {
        nativeBuildInputs = [
          curl
          jq
        ];
      }
      ''
        export JELLYFIN=${lib.getExe package}
        export FFMPEG=${lib.getBin jellyfin-ffmpeg}/bin/ffmpeg
        bash ${./jellyfin-core2-smoke.sh}
        touch $out
      '';
in
package
