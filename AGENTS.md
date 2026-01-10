- Compatibility: Python 2.7 + 3; ASCII-only code/scripts (Unicode allowed in
  .md); Windows + Linux support; ICMP transport stays Linux-only.
- Libraries/mentions: standard library only; never mention claude/anthropic or
  use emojis.
- Protocol: asymmetric MTU negotiation; keepalive pongs suppressed when any
  channel has pending data; follow doc/architecture/ASYMMETRY.md (Alice initiates, RTT
  retransmit, packet-count timeouts; Bob polls/opportunistic retransmit,
  wall-clock timeouts; Bob throughput bounded by Alice polling).
- Invocation/tests: use python3; DNS direct tests use port 5353; authoritative
  DNS uses port 53; never run tests/e2e/ (user only).
- Git workflow: always commit + push after code/doc changes; never git add .
  or git add -A; stage explicit paths; commit only touched files; ignore
  unrelated changes.
- Breaking changes: prefer clean breaks over compatibility shims; update all
  call sites in the same change.
- Reviews: answer your own questions when possible; otherwise propose best
  options grounded in facts; ignore tests unless explicitly asked; ignore
  doc/completed_plans and doc/abandoned_plans.
- Plans: drafting a <plan>.md must list affected components; evaluating a plan
  requires full code review of all affected components; executing a plan adds
  execution notes and moves it to doc/completed_plans with YYYYMMDD_ prefix;
  do not modify code under ./tests.
- Logs/db: default logs logs/server_log.db + logs/client_log.db; inspect .db
  with sqlite3; if a bug is listed in doc/bugs, summarize new findings in that
  bug's .md.
- Coding: minimize code and complexity while maximizing performance,
  readability, logging, and correctness; optimize for the least code and
  complexity with the highest performance while maintaining readability,
  logging, and correctness.
- Coding: avoid list/dict/set comprehensions and generator expressions in sfb/
  (flat build) so Python 2 minified builds remain safe; use explicit loops.
- Avoid over-preserving API compatibility when a breaking change would be
  cleaner/better long term; update call sites instead.
