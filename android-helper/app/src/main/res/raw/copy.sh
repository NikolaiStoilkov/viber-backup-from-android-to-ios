# Run as root by CopyService (`su -c sh copy.sh`). Copies Viber's data into STAGE,
# writes checksums and makes the copy readable by `adb pull`.
#
# It only READS from Viber's folders. The only thing it deletes is STAGE (our own copy).
#
# Output lines parsed by CopyService:
#   PID|<pid>                             this shell, so Cancel can stop it
#   FOUND|<dest>|<source>|<files>|<kb>    a source folder that exists
#   TOTAL|<n>                             number of files to copy
#   FILE|<i>                              i files copied so far
#   WARN|<text>                           a file could not be copied
#   CHECKSUM / NOSHA / DONE
set -u
echo "PID|$$"
STAGE=/data/local/tmp/vibertransfer_staging
PKG=com.viber.voip

rm -rf "$STAGE" || exit 10
mkdir -p "$STAGE" || exit 11
LIST="$STAGE/.filelist"
INV="$STAGE/inventory.json"
: > "$LIST"
printf '{"package":"%s","created_by":"Viber Transfer Helper","sources":[' "$PKG" > "$INV"
SEP=''

# add_tree <source dir> <destination relative to STAGE>
add_tree() {
  [ -d "$1" ] || return 0
  n=$(find "$1" -type f | wc -l | tr -d ' ')
  kb=$(du -sk "$1" | cut -f1)
  echo "FOUND|$2|$1|$n|$kb"
  printf '%s{"name":"%s","path":"%s","files":%s,"kb":%s}' "$SEP" "$2" "$1" "$n" "$kb" >> "$INV"
  SEP=','
  find "$1" -type f | while IFS= read -r f; do echo "$2|$1|$f"; done >> "$LIST"
}

# 1. Viber's private app data: everything except caches and the native-lib link.
for d in /data/data/$PKG/*; do
  b=$(basename "$d")
  case "$b" in cache|code_cache|lib) continue ;; esac
  add_tree "$d" "internal/$b"
done

# 2. Viber's folders on shared storage (attachments). /data/media/0 is the real
#    location of /sdcard for user 0; reading it directly avoids the FUSE layer that
#    hides other apps' Android/data folders on Android 11+.
add_tree /data/media/0/Android/data/$PKG/files external/Android_data_files
add_tree /data/media/0/Android/media/$PKG external/Android_media
add_tree /data/media/0/Viber external/Viber
echo ']}' >> "$INV"

echo "TOTAL|$(wc -l < "$LIST" | tr -d ' ')"
i=0
while IFS='|' read -r dest src f; do
  out="$STAGE/$dest${f#$src}"
  mkdir -p "$(dirname "$out")"
  cp -p "$f" "$out" 2>/dev/null || echo "WARN|could not copy $f"
  i=$((i + 1))
  echo "FILE|$i"
done < "$LIST"
rm -f "$LIST"

echo "CHECKSUM"
command -v sha256sum >/dev/null || { echo "NOSHA"; exit 20; }
cd "$STAGE" || exit 12
find . -type f ! -name checksums.sha256 -exec sha256sum {} + > checksums.sha256 || exit 21

# Hand the copy to the adb shell user (uid 2000) so the Mac can pull it.
chown -R 2000:2000 "$STAGE"
chmod -R u+rwX,go+rX "$STAGE"
restorecon -R "$STAGE" 2>/dev/null
echo "DONE"
exit 0
