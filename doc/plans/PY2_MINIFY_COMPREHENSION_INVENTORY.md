# Python2 Minify Comprehension Inventory

## Scope
- Modules listed in `doc/flatten_manifest.txt` (all roles/transports).
- AST scan for list/dict/set comprehensions and generator expressions.
- `sfb/stagers/dns_stager_template.py` requires placeholder substitution
  (`{{RESOLVER_SNIPPET}}` -> `pass`) to parse; the location below is from that
  parse with line numbers preserved.

## Locations
- sfb/cli.py:189 listcomp return [data[i:i + chunk_size] for i in range(0, len(data), chunk_size)]
- sfb/cli.py:480 listcomp offsets = [header_len + off for off in offsets]
- sfb/cli.py:503 genexp offsets_text = ', '.join(str(offset) for offset in offsets)
- sfb/cli.py:1437 dictcomp config_kwargs = {k: v for k, v in config_kwargs.items() if v is not None}
- sfb/logging_util.py:462 listcomp existing = set([row[1] for row in cursor.fetchall()])
- sfb/reliability/fast_retransmit.py:42 listcomp stale = [seq for seq in self._counts if seq not in valid]
- sfb/reliability/send_window.py:308 listcomp return [ (seq, ...) for _, seq, pkt in retransmits ] (multiline)
- sfb/reliability/send_window.py:437 listcomp return [seq for _, seq in candidates]
- sfb/stagers/dns_stager.py:339 genexp return ' '.join('\"%s\"' % part for part in parts)
- sfb/stagers/dns_stager_template.py:226 genexp data = b''.join(chunks[index] for index in range(1, count + 1))
- sfb/transport/dns/dns_codec.py:92 listcomp labels = [label for label in name.split('.') if label]
- sfb/transport/dns/dns_codec.py:207 listcomp labels = [label for label in name.split('.') if label]
- sfb/transport/dns/dns_codec.py:225 genexp total_len = sum(len(label) for label in labels) + (len(labels) - 1)
- sfb/transport/dns/dns_codec.py:418 listcomp base_labels = [label for label in base_domain.split('.') if label]
- sfb/transport/proxy_helpers.py:234 genexp if extra and any(byte not in (13, 10) for byte in bytearray(extra)):
- sfb/transport/proxy_helpers.py:272 genexp if any(ch.isspace() for ch in value):
- sfb/transport/tls_handshake/tls_handshake_config.py:175 listcomp tokens = [token.strip() for token in value.split(',')]
- sfb/transport/tls_handshake/tls_handshake_config.py:176 genexp if not tokens or any(not token for token in tokens):
- sfb/transport/tls_handshake_bump/tls_handshake_bump_codec.py:411 genexp total_len = sum(len(label) for label in labels) + (len(labels) - 1)
- sfb/transport/tls_handshake_bump/tls_handshake_bump_config.py:177 genexp if any(ch.isspace() for ch in value):
- sfb/tunnel/base_tunnel.py:706 listcomp 'rtt_samples_ms': [round(sample, 3) for sample in rtt_samples]
