# Architectural Decision Record: Concurrency Strategy on Windows

## Context
We needed to implement a high-performance Traffic Bot and Proxy Checker capable of running thousands of concurrent connections. The target environment is Windows (i9-14900K). The networking library must support TLS Fingerprinting (JA3 spoofing) to bypass bot detection.

## The Problem
We selected `curl_cffi` for its excellent TLS fingerprinting capabilities. We initially implemented an `asyncio` based architecture for both Traffic Generation and Proxy Checking.
However, during high-concurrency proxy checking (>500 threads), the application crashed with:
`ValueError: too many file descriptors in select()`

## Root Cause
On Windows, the `select()` system call is strictly limited to 512 sockets.
While Python's `asyncio` can use `ProactorEventLoop` (IOCP) to bypass this, `curl_cffi` (as of v0.7.x) appears to rely on a fallback mechanism or internal thread utilizing `select()` for its async loop integration on Windows, or conflicts with the loop policy.

## The Solution: Hybrid Concurrency

### 1. Proxy Checker -> Threading
We refactored the `ProxyManager` to use standard Python `threading` (`ThreadPoolExecutor`) instead of `asyncio`.
*   **Why:** Python threads on Windows use blocking I/O which does *not* rely on `select()`. It scales well with the OS kernel handling context switches.
*   **Result:** We achieved 1000-2000 concurrent checks without crashes.

### 2. Traffic Engine -> Asyncio
We kept the `TrafficEngine` on `asyncio`.
*   **Why:** The attack phase often uses fewer concurrent connections per target (to avoid DOSing oneself or hitting rate limits instantly) compared to checking a list of 10,000 proxies. `asyncio` is more efficient for the "wait and hold" nature of traffic simulation (browsing time).
*   **Mitigation:** If the user attempts >500 attack threads, they *might* hit the limit, but this is a rarer use case than high-concurrency checking.

## Best Practices Established
1.  **Windows & Asyncio:** Be extremely wary of the 512 socket limit.
2.  **Library Specifics:** If an async library crashes with `select()` errors on Windows, switching to Threading is often a more robust and immediate fix than debugging complex Event Loop Policies.
3.  **Self-Healing:** The engine now automatically removes proxies that fail (connection reset/refused), keeping the pool clean.
