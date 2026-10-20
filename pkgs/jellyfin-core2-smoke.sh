#!/usr/bin/env bash
# Boot Jellyfin on throwaway dirs and push it through the code paths that have
# hit SIGILL on old CPUs: SQLite (setup + library scan), ffmpeg/ffprobe on
# 10-bit HEVC like most of the real library, and Skia (thumbnail resize).
#
#   JELLYFIN=/path/to/bin/jellyfin FFMPEG=/path/to/ffmpeg jellyfin-core2-smoke.sh
set -euo pipefail

: "${JELLYFIN:?}" "${FFMPEG:?}"
port=${PORT:-18096}
base=http://127.0.0.1:$port

work=$(mktemp -d)
export HOME=$work/home
movie="$work/media/Movies/Smoke Test (2020)"
mkdir -p "$HOME" "$movie" "$work"/{data,config,cache,log}

cat > "$work/config/network.xml" <<EOF
<?xml version="1.0" encoding="utf-8"?>
<NetworkConfiguration xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema">
  <InternalHttpPort>$port</InternalHttpPort>
  <EnableIPv6>false</EnableIPv6>
</NetworkConfiguration>
EOF

echo "smoke: encoding a 10-bit HEVC test clip"
"$FFMPEG" -nostdin -loglevel error \
  -f lavfi -i testsrc2=size=640x360:rate=24 -f lavfi -i sine=frequency=440 -t 3 \
  -c:v libx265 -preset ultrafast -pix_fmt yuv420p10le -x265-params log-level=error \
  -c:a aac "$movie/Smoke Test (2020).mkv"

echo "smoke: transcoding it to H.264 the way a client fallback would"
"$FFMPEG" -nostdin -loglevel error -i "$movie/Smoke Test (2020).mkv" \
  -vf scale=320:-2,format=yuv420p -c:v libx264 -preset veryfast -c:a aac -f null -

"$JELLYFIN" --datadir "$work/data" --configdir "$work/config" --cachedir "$work/cache" \
  --logdir "$work/log" --nowebclient > "$work/server.log" 2>&1 &
pid=$!

stop_server() {
  kill "$pid" 2>/dev/null || return 0
  for _ in $(seq 30); do kill -0 "$pid" 2>/dev/null || return 0; sleep 1; done
  kill -9 "$pid" 2>/dev/null || true
}
trap stop_server EXIT

fail() {
  echo "smoke: FAILED: $*" >&2
  if ! kill -0 "$pid" 2>/dev/null; then
    status=0
    wait "$pid" || status=$?
    case $status in
      132) why=" (SIGILL: illegal instruction)" ;;
      134) why=" (SIGABRT: .NET aborts after a fault in native code, e.g. SIGILL; see coredumpctl)" ;;
      *) why= ;;
    esac
    echo "smoke: jellyfin exited with status $status$why" >&2
  fi
  tail -n 40 "$work/server.log" >&2
  exit 1
}

token=
api() {
  local auth='MediaBrowser Client="core2-smoke", Device="nix", DeviceId="core2-smoke", Version="1"'
  if [ -n "$token" ]; then auth="$auth, Token=\"$token\""; fi
  curl -sf --max-time 60 -H "Authorization: $auth" -H 'Content-Type: application/json' "$@"
}

# until <seconds> <description> <command...>: retry until it succeeds, the server dies or time runs out
until_ok() {
  local secs=$1 what=$2
  shift 2
  for _ in $(seq "$secs"); do
    if "$@" > /dev/null 2>&1; then return 0; fi
    kill -0 "$pid" 2>/dev/null || fail "server died while waiting for: $what"
    sleep 1
  done
  fail "timed out after ${secs}s waiting for: $what"
}

# During first-boot migrations a placeholder page answers instead of the API
ready() { api "$base/System/Info/Public" | jq -e '.StartupWizardCompleted == false'; }
echo "smoke: waiting for the server to start"
until_ok 600 "startup" ready

echo "smoke: running the setup wizard"
api -X POST "$base/Startup/Configuration" \
  -d '{"UICulture":"en-US","MetadataCountryCode":"US","PreferredMetadataLanguage":"en"}' || fail "POST /Startup/Configuration"
api "$base/Startup/User" > /dev/null || fail "GET /Startup/User"
api -X POST "$base/Startup/User" -d '{"Name":"smoke","Password":"smoke"}' || fail "POST /Startup/User"
api -X POST "$base/Startup/Complete" || fail "POST /Startup/Complete"
token=$(api -X POST "$base/Users/AuthenticateByName" -d '{"Username":"smoke","Pw":"smoke"}' | jq -r .AccessToken) ||
  fail "login"

echo "smoke: adding a movie library and scanning it"
api -X POST "$base/Library/VirtualFolders?name=Movies&collectionType=movies" \
  -d "$(jq -n --arg p "$work/media/Movies" '{LibraryOptions: {PathInfos: [{Path: $p}]}}')" || fail "add library"

# The scan that adding a library starts can race the path registration and find
# nothing, so keep asking for scans until the movie shows up
polls=0
scanned() {
  if [ $((polls++ % 60)) = 0 ]; then api -X POST "$base/Library/Refresh"; fi
  api "$base/Items?Recursive=true&IncludeItemTypes=Movie&Fields=MediaStreams" |
    jq -e '.Items[0].MediaStreams | any(.Codec == "hevc" and .BitDepth == 10)'
}
until_ok 600 "library scan with ffprobe stream info" scanned
id=$(api "$base/Items?Recursive=true&IncludeItemTypes=Movie" | jq -r '.Items[0].Id')

echo "smoke: fetching a resized thumbnail (ffmpeg frame grab + Skia)"
thumb() {
  api -o "$work/thumb.jpg" "$base/Items/$id/Images/Primary?maxWidth=160&format=Jpg" &&
    [ "$(head -c 2 "$work/thumb.jpg" | od -An -tx1 | tr -d ' ')" = ffd8 ]
}
until_ok 600 "primary image" thumb

kill -0 "$pid" 2>/dev/null || fail "server died at the end"
echo "smoke: OK, Jellyfin works on this CPU"
