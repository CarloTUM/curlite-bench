#!/bin/bash
# build-lean.sh [client]: the leanest build of a client for this machine's
# architecture: static musl, no PIE, rustls on ring (client default
# fetch-curlite-ring-min). Output under target-lean/.
set -eu
client=${1:-fetch-curlite-ring-min}
arch=$(uname -m)
target=$arch-unknown-linux-musl
rustup target list --installed | grep -q "$target" || rustup target add "$target"
cc_var="CC_${target//-/_}"
export "$cc_var=musl-gcc"
export AWS_LC_SYS_NO_JITTER_ENTROPY=1
export RUSTFLAGS="-C target-feature=+crt-static -C relocation-model=static"
CARGO_TARGET_DIR=target-lean cargo build --release --target "$target" -p "$client"
echo "target-lean/$target/release/$client"
