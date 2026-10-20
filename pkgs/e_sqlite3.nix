# SQLite built the way SQLitePCLRaw builds the `libe_sqlite3.so` that Jellyfin
# bundles, but for the plain x86-64 baseline. The upstream prebuilt (Jellyfin
# >= 10.11) contains SSE4.1 instructions (blendvpd) and dies with SIGILL on CPUs
# without them, like this Core 2 Quad: https://github.com/jellyfin/jellyfin/issues/15148
{
  lib,
  stdenv,
  sqlite,
  unzip,
  tcl,
  binutils,
}:

let
  # Same options as the prebuilt (read from its sqlite_compileoption_get list),
  # plus the usual glibc HAVE_* so sqlite3_sleep() doesn't round up to seconds.
  flags = [
    "-DSQLITE_DEFAULT_FOREIGN_KEYS=1"
    "-DSQLITE_DQS=0"
    "-DSQLITE_ENABLE_COLUMN_METADATA"
    "-DSQLITE_ENABLE_FTS3"
    "-DSQLITE_ENABLE_FTS3_PARENTHESIS"
    "-DSQLITE_ENABLE_FTS4"
    "-DSQLITE_ENABLE_FTS5"
    "-DSQLITE_ENABLE_GEOPOLY"
    "-DSQLITE_ENABLE_MATH_FUNCTIONS"
    "-DSQLITE_ENABLE_PREUPDATE_HOOK"
    "-DSQLITE_ENABLE_RTREE"
    "-DSQLITE_ENABLE_SESSION"
    "-DSQLITE_ENABLE_SNAPSHOT"
    "-DSQLITE_LIKE_DOESNT_MATCH_BLOBS"
    "-DSQLITE_MAX_VARIABLE_NUMBER=32766"
    "-DSQLITE_THREADSAFE=1"
    "-DHAVE_USLEEP=1"
    "-DHAVE_FDATASYNC=1"
    "-DHAVE_LOCALTIME_R=1"
    "-DHAVE_GMTIME_R=1"
  ];
in
stdenv.mkDerivation {
  pname = "e_sqlite3";
  inherit (sqlite) version src;

  nativeBuildInputs = [
    unzip
    tcl
    binutils
  ];

  configureFlags = [ "--disable-tcl" ];

  buildPhase = ''
    runHook preBuild
    make sqlite3.c

    # e_sqlite3 exports the encryption API as "not supported" stubs (return SQLITE_ERROR)
    cat > key_stubs.c <<'EOF'
    int sqlite3_key(void *db, const void *key, int n) { return 1; }
    int sqlite3_key_v2(void *db, const char *name, const void *key, int n) { return 1; }
    int sqlite3_rekey(void *db, const void *key, int n) { return 1; }
    int sqlite3_rekey_v2(void *db, const char *name, const void *key, int n) { return 1; }
    EOF

    $CC -O2 -fPIC -shared -march=x86-64 -mtune=generic ${toString flags} \
      -Wl,-soname,libe_sqlite3.so -o libe_sqlite3.so sqlite3.c key_stubs.c -lm
    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall
    install -Dm555 libe_sqlite3.so $out/lib/libe_sqlite3.so
    runHook postInstall
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    bash ${./core2-isa-check.sh} --strict $out/lib/libe_sqlite3.so
  '';

  meta = {
    inherit (sqlite.meta) homepage license;
    description = "SQLite as SQLitePCLRaw's e_sqlite3, built for baseline x86-64";
    platforms = [ "x86_64-linux" ];
  };
}
