#!/usr/bin/env bash
# Workspace-only Debian 13 audio libraries; no sudo/system package changes.
set -euo pipefail
. /etc/os-release
test "$ID" = debian
test "$VERSION_ID" = 13
test "$(dpkg --print-architecture)" = amd64
audio_root="${1:-/workspace/jarvis-audio}"
mkdir -p "$audio_root"
audio_root="$(cd "$audio_root" && pwd)"
mkdir -p "$audio_root/apt/lists/partial" "$audio_root/apt/archives/partial" "$audio_root/apt/parts"
cat > "$audio_root/apt/sources.list" <<'EOF'
deb [signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] https://deb.debian.org/debian trixie main
EOF
cat > "$audio_root/apt/config" <<EOF
Dir::Etc::parts "$audio_root/apt/parts";
Dir::Etc::main "$audio_root/apt/empty";
Dir::Etc::sourcelist "$audio_root/apt/sources.list";
Dir::Etc::sourceparts "$audio_root/apt/parts";
Dir::State::lists "$audio_root/apt/lists";
Dir::Cache::archives "$audio_root/apt/archives";
APT::Sandbox::User "$(id -un)";
EOF
touch "$audio_root/apt/empty"
# Isolated APT state; signed InRelease/index and archive hash checks remain enabled.
APT_CONFIG="$audio_root/apt/config" /usr/bin/apt-get update
(
    cd "$audio_root/apt/archives"
    APT_CONFIG="$audio_root/apt/config" /usr/bin/apt-get download \
        espeak-ng libespeak-ng1 espeak-ng-data libpcaudio0 libsonic0 \
        libportaudio2 portaudio19-dev libportaudiocpp0 libasound2t64 libjack-jackd2-0
)
mkdir -p "$audio_root/sysroot"
for archive in "$audio_root"/apt/archives/*.deb; do
    dpkg-deb -x "$archive" "$audio_root/sysroot"
    dpkg-deb -f "$archive" Package Version
done
LD_LIBRARY_PATH="$audio_root/sysroot/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
ESPEAK_DATA_PATH="$audio_root/sysroot/usr/lib/x86_64-linux-gnu" \
"$audio_root/sysroot/usr/bin/espeak-ng" --version
# Caller sets PATH/LD_LIBRARY_PATH/LIBRARY_PATH/ESPEAK_DATA_PATH before use.
# LIBRARY_PATH lets ctypes locate PortAudio via the local development symlink;
# LD_LIBRARY_PATH loads the verified libraries without modifying system ldconfig.
# This script does not claim that a microphone or playback device exists.
