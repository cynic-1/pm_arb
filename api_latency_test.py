#!/usr/bin/env python3
"""Measure network latency for Polymarket APIs or any supplied endpoints."""

from __future__ import annotations

import argparse
import statistics
import time
from dataclasses import dataclass
from typing import List

import requests

DEFAULT_ENDPOINTS: List[str] = [
    "https://gamma-api.polymarket.com/events",
    "https://gamma-api.polymarket.com/markets",
]


@dataclass
class Sample:
    """Stores a single latency measurement."""

    elapsed_ms: float
    ok: bool
    status_code: int | None
    error: str | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test API network latency.")
    parser.add_argument(
        "-e",
        "--endpoint",
        action="append",
        dest="endpoints",
        help="Endpoint URL to probe (repeatable). Defaults to common Polymarket APIs.",
    )
    parser.add_argument(
        "-n",
        "--iterations",
        type=int,
        default=5,
        help="Number of requests per endpoint (default: 5).",
    )
    parser.add_argument(
        "-t",
        "--timeout",
        type=float,
        default=5.0,
        help="Request timeout in seconds (default: 5).",
    )
    parser.add_argument(
        "-p",
        "--pause",
        type=float,
        default=0.2,
        help="Seconds to wait between requests to avoid rate limits (default: 0.2).",
    )
    return parser.parse_args()


def collect_samples(url: str, iterations: int, timeout: float, pause: float) -> List[Sample]:
    samples: List[Sample] = []
    for _ in range(iterations):
        start = time.perf_counter()
        status_code: int | None = None
        ok = False
        error: str | None = None
        try:
            response = requests.get(url, timeout=timeout)
            status_code = response.status_code
            ok = response.ok
        except requests.RequestException as exc:
            error = str(exc)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        samples.append(Sample(elapsed_ms=elapsed_ms, ok=ok, status_code=status_code, error=error))
        if pause > 0:
            time.sleep(pause)
    return samples


def format_summary(samples: List[Sample]) -> str:
    success_count = sum(1 for sample in samples if sample.ok)
    latencies = [sample.elapsed_ms for sample in samples if sample.ok]
    if not latencies:
        return "  No successful measurements."
    avg = statistics.mean(latencies)
    minimum = min(latencies)
    maximum = max(latencies)
    stdev = statistics.stdev(latencies) if len(latencies) > 1 else 0.0
    return (
        f"  Success {success_count}/{len(samples)} | "
        f"min {minimum:.2f} ms | avg {avg:.2f} ms | max {maximum:.2f} ms | std {stdev:.2f}"
    )


def print_report(url: str, samples: List[Sample]) -> None:
    print(f"\nEndpoint: {url}")
    print("Run  Status  Latency (ms)  Error")
    for idx, sample in enumerate(samples, start=1):
        status_display = str(sample.status_code) if sample.status_code is not None else "---"
        error_display = sample.error or ""
        print(f"{idx:>3}  {status_display:>6}  {sample.elapsed_ms:12.2f}  {error_display}")
    print(format_summary(samples))


def main() -> None:
    args = parse_args()
    endpoints = args.endpoints or DEFAULT_ENDPOINTS
    for url in endpoints:
        samples = collect_samples(url, args.iterations, args.timeout, args.pause)
        print_report(url, samples)


if __name__ == "__main__":
    main()
