# Dynamic EDNS Size Negotiation Plan

Status: draft

## Summary
Make DNS EDNS sizing dynamic and negotiation-aware. Bob will honor the EDNS
buffer size advertised in each query (clamped by config), and Alice will adapt
the EDNS size used for queries based on observed successes and failures.

## Goals
- Ensure Bob never replies with packets larger than the resolver’s advertised
  EDNS UDP size for that query; if a resolver advertises a size below
  DNS_STANDARD_SIZE, treat it as invalid and fall back to standard DNS sizing
  without EDNS.
- Allow Alice to converge on the largest EDNS size that is reliable for the
  current resolver/path.
- Keep behavior deterministic, logged, and bounded by configuration.

## Non-Goals
- Implement TCP fallback for truncated DNS responses.
- Change DNS query/response types or payload encodings.
- Add or run automated tests.

## Affected Components
- `sfb/transport/dns/dns_codec.py`
- `sfb/transport/dns/dns_server.py`
- `sfb/transport/dns/dns_client.py`
- `sfb/transport/mtu_limits.py`
- `sfb/config.py`

## Plan
1. Add EDNS parsing helper in `sfb/transport/dns/dns_codec.py`.
   - Add `parse_query_edns(data, offset, arcount)` that inspects the additional
     records for an OPT record and returns `(edns_present, udp_size, reason)`.
   - Parsing rules:
     - Iterate `arcount` records starting at `offset`.
     - For each record, use `skip_name` to move past NAME. If `skip_name`
       raises `ValueError`, return `(False, None, 'invalid_name')`.
     - Ensure at least 10 bytes remain for TYPE/CLASS/TTL/RDLENGTH; if not,
       return `(False, None, 'truncated_header')`.
     - Parse `rtype, rclass, ttl, rdlength` with `'>HHIH'`.
     - Ensure `offset + rdlength <= len(data)`; if not, return
       `(False, None, 'truncated_rdata')`.
     - If `rtype != QTYPE_OPT`, skip `rdlength` bytes and continue.
     - If `rtype == QTYPE_OPT`:
       - Extract `version = (ttl >> 16) & 0xFF`.
       - If `version != 0`, return `(False, None, 'unsupported_version')`.
       - Treat `rclass` as the advertised UDP size and return
         `(True, rclass, None)`.
     - If no OPT record is found, return `(False, None, None)`.

2. Make Bob’s response size per-query in `sfb/transport/dns/dns_server.py`.
   - Update `_parse_query` to return `(query_id, qname, qtype, edns_present,
     edns_size, additional_offset)` where `additional_offset` is the offset at
     the start of the additional section.
   - After parsing the first question, skip any remaining questions
     (`qdcount - 1`) and any answer/authority records (`ancount`, `nscount`) to
     compute `additional_offset`. If any skip fails, treat the query as
     invalid (and do not attempt EDNS parsing).
   - Call `codec.parse_query_edns` with `additional_offset` and `arcount` to
     obtain EDNS details. If `edns_present` is False, treat the query as
     non-EDNS.
   - Compute the per-query response size:
     - If `edns_present` is False, `response_edns_size = DNS_STANDARD_SIZE`.
     - If `edns_present` is True and `edns_size < DNS_STANDARD_SIZE`, treat the
       OPT as invalid (`edns_present = False`) and use
       `response_edns_size = DNS_STANDARD_SIZE`.
     - If `edns_present` is True and `edns_size >= DNS_STANDARD_SIZE`,
       `response_edns_size = min(self._edns_size, edns_size)`.
   - Build per-query OPT data:
     - If `edns_present` is True and `response_edns_size > DNS_STANDARD_SIZE`,
       build `opt_record = codec.build_opt_record(response_edns_size)`, set
       `opt_arcount = 1`, and `opt_record_len = len(opt_record)`.
     - If `edns_present` is False (or was dropped due to sub-standard size),
       use `opt_record = b''`, `opt_arcount = 0`, `opt_record_len = 0`.
   - Thread these per-query values through response paths:
     - Update `DnsResponder` to store `opt_record`, `opt_arcount`,
       `opt_record_len`, and `response_edns_size`.
     - Update `_response_payload_cap` to accept `response_edns_size` and
       `opt_record_len` as parameters and use them in
       `calc_cname_response_payload_cap`.
     - Update `_send_response`, `_send_empty_response`, and
       `_send_cname_followup` to accept `opt_record` and `opt_arcount` and use
       them when building headers and appending the additional section.
   - Add a debug log when EDNS is clamped:
     - Event: `dns.edns_clamp` with fields `advertised`, `configured`,
       `effective`, and `reason` (e.g., `unsupported_version`,
       `below_standard`, or `clamped`).

3. Add dynamic EDNS sizing on Alice in `sfb/transport/dns/dns_client.py`.
   - Treat `config.dns_edns_size` as a hard cap for dynamic sizing.
   - Add candidates and thresholds near the top of the module:
     - `EDNS_SIZE_CANDIDATES = (4096, 2048, 1232, 1024, 512)`
     - `EDNS_DOWNGRADE_FAILURES = 3`
     - `EDNS_UPGRADE_SUCCESSES = 50`
   - Add fields in `__init__`:
     - `self._edns_size_cap = config.dns_edns_size`
    - `self._edns_candidates = [s for s in EDNS_SIZE_CANDIDATES if s <= cap]`.
      If `cap > DNS_STANDARD_SIZE` and `cap` is not already in the list, append
      it and re-sort descending so non-standard caps (e.g., 3000) are honored.
      If `cap < DNS_STANDARD_SIZE`, treat EDNS as disabled (no OPT) and set
      candidates to `[DNS_STANDARD_SIZE]`; the cap applies to EDNS OPT only,
      and standard DNS baseline remains 512 bytes.
     - `self._edns_index = 0` (largest size in the candidate list)
     - Initialize `self._edns_size` from the candidate at `self._edns_index`
       (use `_set_edns_size` so OPT/caps are consistent) rather than assuming
       `config.dns_edns_size` is in the list.
     - `self._edns_failures = 0`, `self._edns_successes = 0`
     - Store the initial MTU limits from `resolve_mtu_limits` as
       `self._resolved_send_mtu` and `self._resolved_recv_mtu`.
   - Add helper `_set_edns_size(new_size, reason)`:
     - If `new_size == self._edns_size`, return early.
     - Update `self._edns_size` to `new_size`.
     - Reset `self._edns_failures` and `self._edns_successes` to `0` after a
       size change to avoid cascading adjustments on a single batch of events.
     - Rebuild `_opt_record`, `_opt_arcount`, `_opt_record_len`:
       - If `new_size > DNS_STANDARD_SIZE`, include OPT.
       - If `new_size <= DNS_STANDARD_SIZE`, omit OPT (no EDNS).
    - Recompute `_recv_bufsize` without shrinking in-flight:
       - Track `old_size = self._edns_size` before updating.
       - Set `recv_target = max(new_size, config.dns_recv_bufsize_min)`.
      - If `new_size < old_size` and there are pending queries, keep
        `_recv_bufsize = max(self._recv_bufsize, recv_target)`; allow shrink
        once pending is empty.
      - Add a `_maybe_shrink_recv_bufsize()` helper that shrinks
        `_recv_bufsize` to `recv_target` when there are no pending queries.
        Call it from `_on_prune` when pruning empties pending and after
        `_try_recv` clears the last pending entry.
     - Call `_init_response_caps()` to rebuild response caps and lookups, and
       store the returned cap in `self._max_response_packet_mtu`.
     - Recompute `self._recv_packet_mtu` as
       `min(self._resolved_recv_mtu, self._max_response_packet_mtu)` and log
       a `dns.edns_adjust` event with `old_size`, `new_size`, and `reason`.
   - Add helper `_note_edns_success()`:
     - Increment `self._edns_successes`, reset `self._edns_failures`.
     - If successes reach `EDNS_UPGRADE_SUCCESSES` and `self._edns_index > 0`,
       decrement index and call `_set_edns_size` with the next larger size.
   - Add helper `_note_edns_failure(reason)`:
     - Increment `self._edns_failures`, reset `self._edns_successes`.
     - If failures reach `EDNS_DOWNGRADE_FAILURES` and
       `self._edns_index < len(candidates) - 1`, increment index and call
       `_set_edns_size` with the next smaller size.
   - Track the EDNS size used per pending query (e.g., store it on
     `_PendingQuery`). When a response/prune is processed, only call
     `_note_edns_success()` or `_note_edns_failure(...)` if the pending entry’s
     size matches the current `self._edns_size` to avoid old-size responses
     perturbing the new size.
   - Wire the helpers into the response path:
     - On successful payload decode in `_try_recv`, call `_note_edns_success()`.
     - On error responses for a known pending query (where `error_response`
       becomes True), call `_note_edns_failure(reason)` before cleaning up.
     - In `_on_prune`, collapse stale entries into a single failure event
       (e.g., call `_note_edns_failure('timeout')` once if any stale entries
       match the current `self._edns_size`).

4. Clarify configuration and MTU behavior.
   - Update `sfb/config.py` comments to state that `dns_edns_size` is a cap and
     may be dynamically reduced at runtime.
   - Keep validation as `dns_edns_size <= DNS_EDNS_MAX_SIZE`; do not add a new
     config option unless the project requires a hard disable switch.
   - In `sfb/transport/mtu_limits.py`, leave the cap-based calculation intact
     (it remains the maximum possible). Dynamic sizing will override effective
     receive MTU inside `DnsClient`.

5. Manual validation (no tests).
   - Manually inspect that:
     - All response builders in `dns_server.py` use per-query OPT records and
       per-query MTU caps.
     - `DnsClient` updates `_opt_record` and `_init_response_caps` whenever
       EDNS size changes.
     - `dns.edns_adjust` and `dns.edns_clamp` logs are emitted only when sizes
       actually change or clamp.

## Testing
- Do not run tests here.
