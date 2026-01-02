# DNS Transport Performance Fix

## Problem
Performance issues were observed in the DNS transport hot paths. The client kept per-query packet copies that were never used, and the server repeatedly recomputed constant values (EDNS OPT records, recv buffer size, CNAME suffix lowercasing, and SOA records) for each packet. These extra allocations and repeated work added avoidable CPU and memory overhead under load.

## Fix
We removed the unused per-query packet storage in the DNS client and cached constant DNS records and derived values in the DNS server and client. This reduces per-packet allocations and repeated string processing without changing behavior.

## Reference
- Commit: 39a3fbc (Optimize DNS transport caching)
