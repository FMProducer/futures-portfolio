"""Offline-safe connectivity probe for Binance under the local TUN/sing-box setup.

Public read-only endpoints ONLY. No API keys, no auth, no orders, no state writes.
Purpose: prove that a Python process (same pattern as the bot: requests, no
env-proxy, no env-proxy) can reach fapi.binance.com / fstream.binance.com.

Exit codes:
  0 = both REST pings OK
  1 = at least one failure (details printed)
"""
import os
import socket
import sys
import time

# Force explicit no-proxy: requests would otherwise honor HTTP_PROXY/HTTPS_PROXY env
import requests

HOSTS = {
    "fapi.binance.com": [
        ("GET", "https://fapi.binance.com/fapi/v1/ping", "fapi ping"),
        ("GET", "https://fapi.binance.com/fapi/v1/time", "fapi server time"),
    ],
    "api1.binance.com": [
        ("GET", "https://api1.binance.com/api/v3/ping", "spot ping (api1)"),
    ],
}


def banner(s: str) -> None:
    print(f"\n=== {s} ===", flush=True)


def show_dns(host: str) -> None:
    ips = sorted({a[4][0] for a in socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM)})
    print(f"DNS {host} -> {', '.join(ips)}")


def probe(method: str, url: str, label: str) -> bool:
    s = requests.Session()
    s.trust_env = False  # ignore HTTP_PROXY/HTTPS_PROXY like the bot's connector
    t0 = time.monotonic()
    try:
        r = s.request(method, url, timeout=10, proxies={"http": None, "https": None},
                      headers={"User-Agent": "futures-portfolio-probe/1.0"})
        dt = time.monotonic() - t0
        ok = r.status_code in (200, 204, 418)  # ping returns 200/204; 418 still = reachable
        print(f"[{'OK ' if ok else 'ERR'}] {label}: {url} -> HTTP {r.status_code} in {dt*1000:.0f} ms")
        if url.endswith("/time") and r.status_code == 200:
            print(f"      serverTime = {r.json().get('serverTime')}")
        return ok
    except requests.exceptions.SSLError as e:
        print(f"[ERR] {label}: TLS handshake FAILED: {e}")
        return False
    except Exception as e:
        print(f"[ERR] {label}: {type(e).__name__}: {e}")
        return False


def main() -> int:
    banner("Local socket view")
    try:
        # Which local NIC will the system route an outbound conn through?
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 53))  # no packets sent (UDP connect only for route lookup)
        print(f"Outbound local address = {s.getsockname()[0]}")
        s.close()
    except Exception as e:
        print(f"route lookup failed: {e}")

    all_ok = True
    for host in HOSTS:
        banner(f"Connectivity to {host}")
        show_dns(host)
        for method, url, label in HOSTS[host]:
            all_ok = all_ok and probe(method, url, label)

    banner("Result")
    print("PASS — all endpoints reachable from Python" if all_ok else "FAIL — see errors above")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
