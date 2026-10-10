# curlite-bench

Reproducible comparison of blocking Rust HTTP clients for short-lived
processes. Every client gets the same 15-line CLI (`GET <url> [-n count]`)
and the same release profile; `bench-server` is a dependency-free keep-alive
server so that the numbers measure clients, not the network.

```sh
python3 -I bench.py                              # builds, then: size, crates, cold start, RSS, per-request cost
python3 -I bench.py --https https://example.com/ # plus one HTTPS GET per process: CPU time and RSS
python3 -I bench.py --build-time                 # plus clean build time per client (slow)
```

Clients are built one `cargo build -p` at a time on purpose: a workspace-wide
build unifies dependency features, and one client's TLS backend would end up
in another's binary.

Clients: curlite, ureq (rustls and native-tls), minreq, attohttpc, reqwest
blocking (rustls and native-tls), isahc. Add one by copying a directory under
`clients/`.

## Results

x86_64 laptop, Linux, 12 CPUs, 2026-10-10. curlite here links a libcurl that
`curl-sys` built from source (no `libcurl4-openssl-dev` on this machine) against
the system OpenSSL 3; on a machine with the distro libcurl the binary carries no
HTTP or TLS code at all. `runs=20`, 300 in-process requests, HTTPS against
example.com (CPU time, so network latency is excluded). minreq and attohttpc
open a new connection per request, hence their per-request cost.

| client | binary KiB | crates | cold start ms | max RSS KiB | per request µs | 8 MiB download RSS KiB | HTTPS CPU ms | HTTPS RSS KiB |
|---|---|---|---|---|---|---|---|---|
| minreq | 1318 | 11 | 3.8 | 2304 | 1659 | 10232 | 9 | 3428 |
| ureq-rustls | 1876 | 22 | 4.0 | 2680 | 24 | 11032 | 10 | 4192 |
| attohttpc | 4033 | 48 | 4.0 | 3388 | 1305 | 11052 | 35 | 5034 |
| reqwest-rustls | 2495 | 85 | 4.6 | 3534 | 34 | 11868 | 12 | 4346 |
| ureq-native | 827 | 30 | 4.7 | 4162 | 36 | 12444 | 49 | 10516 |
| curlite | 1096 | 20 | 6.3 | 6334 | 33 | 14436 | 21 | 8296 |
| isahc | 1485 | 73 | 7.0 | 6764 | 51 | 14768 | 33 | 9374 |
| reqwest-native | 1238 | 85 | 31.0 | 9196 | 40 | 17668 | 43 | 9656 |

What the numbers say so far: the TLS library decides, not the HTTP client.
Every OpenSSL-based client (curlite, isahc, ureq-native, reqwest-native) pays
for loading libssl/libcrypto and OpenSSL 3's initialisation; curlite is the
cheapest of them on HTTPS because it lets OpenSSL look CA certificates up by
hash instead of parsing the bundle. rustls with compiled-in roots is cheaper
on x86. Results on a small ARM board with the distro libcurl follow.

### Where the per-request time goes

callgrind on the curlite client against the distro libcurl, 3000 requests on
one keep-alive connection (2026-10-11): about 94 000 instructions per
request, of which three quarters are inside libcurl itself (easy handle
reset, the twenty setopts, URL parsing, connection cache lookup and the
proxy environment check, which calls `getenv` several times per request).
malloc and free take another 13 %. curlite's own code is below 5 %. The gap
to ureq (22 against 15 µs on this laptop) is libcurl's per-request setup,
not something the wrapper can remove.

## Results on a small ARM board

Orange Pi Zero 2W (Allwinner H618, 4x Cortex-A53 at 1.4 GHz, 4 GB), Armbian
Debian 13, `performance` governor, 2026-10-10. Same columns as above plus the
summed PSS of 10 concurrent processes (what shared pages really cost). Rows
without a crate count are extra builds of the curlite client:
`curlite-lean-musl-ring` is `build-lean.sh` (static musl, no PIE, rustls on
ring), `curlite-lean-musl-awslc` the same with `build-lean.sh fetch-curlite-rustls`
(aws-lc without jitter entropy), `curlite-nopie-ring` ring on glibc without
PIE, `curlite-awslc-nojit` the `rustls` feature on glibc built with
`AWS_LC_SYS_NO_JITTER_ENTROPY=1`.

| client | binary KiB | crates | cold start ms | max RSS KiB | per request µs | 8 MiB download RSS KiB | PSS of 10 procs KiB | PSS per proc KiB | HTTPS CPU ms | HTTPS RSS KiB |
|---|---|---|---|---|---|---|---|---|---|---|
| curlite-lean-musl-ring | 2054 | - | **9.4** | **1096** | 185 | 9288 | **1972** | **197** | 28 | **1996** |
| curlite-lean-musl-awslc | 3334 | - | 9.6 | 1400 | 180 | 9592 | 2261 | 226 | **18** | 2808 |
| minreq | 1156 | 11 | 10.1 | 2200 | 6726 | 10260 | 2964 | 296 | 20 | 2788 |
| ureq-rustls | 1604 | 22 | 10.5 | 2404 | 106 | 10724 | 5675 | 567 | 21 | 3748 |
| attohttpc | 3012 | 48 | 10.9 | 2468 | 6810 | 10652 | 3748 | 374 | 480 | 4260 |
| curlite-nopie-ring | 1926 | - | 11.1 | 2600 | 170 | 10792 | 3290 | 329 | 28 | 3664 |
| curlite-rustls-ring | 2118 | 36 | 11.4 | 2724 | 172 | 10916 | 3915 | 391 | 31 | 3796 |
| curlite-awslc-nojit | 3334 | - | 11.8 | 3024 | 173 | 11036 | 4419 | 441 | 21 | 4820 |
| reqwest-rustls | 2180 | 85 | 11.9 | 3108 | 205 | 11644 | 5385 | 538 | 22 | 3734 |
| curlite-rustls | 3334 | 35 | 11.9 | 2980 | 172 | 11044 | 4583 | 458 | 488 | 4986 |
| curlite-static-ssl | 6172 | 20 | 16.8 | 5456 | 172 | 13604 | 12341 | 1234 | 48 | 7508 |
| ureq-native | 836 | 30 | 18.1 | 4452 | 104 | 12772 | 12294 | 1229 | 223 | 12128 |
| curlite-static | 1094 | 20 | 23.4 | 6928 | 180 | 15076 | 13701 | 1370 | 55 | 9808 |
| isahc | 773 | 73 | 44.8 | 12208 | 270 | 19720 | 25638 | 2563 | 140 | 15872 |
| curlite (distro libcurl) | 452 | 20 | 45.6 | 11984 | 222 | 19028 | 24036 | 2403 | 75 | 14680 |
| reqwest-native | 1156 | 85 | 183.2 | 10704 | 202 | 19152 | 37732 | 3773 | 192 | 11344 |

What the board says:

- **Cold start and memory are decided by the TLS library and the linking**,
  not by the HTTP client. The distro libcurl (34 shared objects) and anything
  on OpenSSL 3 start at 17 to 46 ms and 5 to 12 MiB. A static musl binary on
  rustls starts in 9.4 ms with 1.1 MiB, an empty C program in 7 ms and 0.7 MiB.
- **aws-lc's CPU jitter entropy costs 480 ms per HTTPS process** on this
  core. It hits attohttpc and the plain `curlite-rustls` build alike; the
  `AWS_LC_SYS_NO_JITTER_ENTROPY=1` row shows the same build without it.
- **OpenSSL via native-tls parses the whole CA bundle per process**: 223 ms
  for ureq-native, 192 ms for reqwest-native. curlite on OpenSSL lets OpenSSL
  look certificates up by hash instead (48 to 75 ms).
- **Shared pages matter with many processes**: 10 curlite musl processes
  cost 2 MiB together, 10 ureq processes 5.5 MiB.
- **Per request, libcurl is slower than ureq on this core** (170 vs 106 µs
  over a keep-alive connection); minreq and attohttpc open a connection per
  request. The HTTPS handshake is the floor for everyone at about 20 ms,
  ring's ARM handshake is a third slower than aws-lc's.
