#!/usr/bin/env bash
# Explicit operator install; no weights or dependencies enter the JARVIS checkout.
set -euo pipefail
audio_root="${1:-$HOME/jarvis-audio/whisper.cpp}"
comparison="${2:-tiny.en}"
case "$comparison" in tiny.en|base.en) ;; *) echo 'Expected tiny.en or base.en' >&2; exit 1;; esac
expected_commit=4979e04f5dcaccb36057e059bbaed8a2f5288315
if [ ! -e "$audio_root" ]; then
    mkdir -p "$(dirname "$audio_root")"
    git clone --branch v1.8.2 --depth 1 https://github.com/ggml-org/whisper.cpp.git "$audio_root"
fi
test "$(git -C "$audio_root" rev-parse HEAD)" = "$expected_commit"
# Preserve operator changes: refuse to build an altered tracked source tree.
git -C "$audio_root" diff --quiet
git -C "$audio_root" diff --cached --quiet
cmake -S "$audio_root" -B "$audio_root/build" -DCMAKE_BUILD_TYPE=Release -DWHISPER_CURL=OFF -DWHISPER_BUILD_TESTS=OFF
cmake --build "$audio_root/build" --config Release -j 2
verify_model() {
    local model_name="$1" expected_hash="$2" model_file="$audio_root/models/ggml-$1.bin"
    if [ ! -f "$model_file" ]; then
        local download_file
        download_file="$(mktemp "$model_file.download.XXXXXX")"
        trap 'rm -f "$download_file"' EXIT
        curl --fail --location --retry 2 --output "$download_file" "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-$model_name.bin"
        python3 - "$download_file" "$expected_hash" <<'PY'
import hashlib, sys
with open(sys.argv[1], 'rb') as f:
    actual = hashlib.file_digest(f, 'sha256').hexdigest()
if actual != sys.argv[2]:
    raise SystemExit('Model SHA-256 mismatch; artifact rejected')
PY
        mv "$download_file" "$model_file"
        trap - EXIT
    fi
    python3 - "$model_file" "$expected_hash" <<'PY'
import hashlib, sys
with open(sys.argv[1], 'rb') as f:
    actual = hashlib.file_digest(f, 'sha256').hexdigest()
if actual != sys.argv[2]:
    raise SystemExit('Existing model SHA-256 mismatch; left unchanged')
print(sys.argv[1], actual)
PY
}
verify_model tiny.en 921e4cf8686fdd993dcd081a5da5b6c365bfde1162e72b08d75ac75289920b1f
if [ "$comparison" = base.en ]; then
    verify_model base.en a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002
fi
