# Python2 Minify Comprehension Inventory

## Scope
- Modules listed in `doc/flatten_manifest.txt` (all roles/transports).
- AST scan for list/dict/set comprehensions and generator expressions.
- `sfb/stagers/dns_stager_template.py` requires placeholder substitution
  (`{{RESOLVER_SNIPPET}}` -> `pass`) to parse; the location below is from that
  parse with line numbers preserved.

## Locations
- sfb/cli.py:189 listcomp before: return [data[i:i + chunk_size] for i in range(0, len(data), chunk_size)]; after: chunks = []; for i in range(0, len(data), chunk_size): chunks.append(data[i:i + chunk_size]); return chunks
- sfb/cli.py:480 listcomp before: offsets = [header_len + off for off in offsets]; after: new_offsets = []; for off in offsets: new_offsets.append(header_len + off); offsets = new_offsets
- sfb/cli.py:503 genexp before: offsets_text = ', '.join(str(offset) for offset in offsets); after: parts = []; for offset in offsets: parts.append(str(offset)); offsets_text = ', '.join(parts)
- sfb/cli.py:1437 dictcomp before: config_kwargs = {k: v for k, v in config_kwargs.items() if v is not None}; after: cleaned = {}; for k, v in config_kwargs.items(): if v is not None: cleaned[k] = v; config_kwargs = cleaned
- sfb/logging_util.py:462 listcomp before: existing = set([row[1] for row in cursor.fetchall()]); after: existing = set(); for row in cursor.fetchall(): existing.add(row[1])
- sfb/reliability/fast_retransmit.py:42 listcomp before: stale = [seq for seq in self._counts if seq not in valid]; after: stale = []; for seq in self._counts: if seq not in valid: stale.append(seq)
- sfb/reliability/send_window.py:308 listcomp before: return [ (seq, ...) for _, seq, pkt in retransmits ] (multiline); after: items = []; for _, seq, pkt in retransmits: items.append((seq, ...)); return items
- sfb/reliability/send_window.py:437 listcomp before: return [seq for _, seq in candidates]; after: seqs = []; for _, seq in candidates: seqs.append(seq); return seqs
- sfb/stagers/dns_stager.py:339 genexp before: return ' '.join('\"%s\"' % part for part in parts); after: quoted = []; for part in parts: quoted.append('\"%s\"' % part); return ' '.join(quoted)
- sfb/stagers/dns_stager_template.py:226 genexp before: data = b''.join(chunks[index] for index in range(1, count + 1)); after: parts = []; for index in range(1, count + 1): parts.append(chunks[index]); data = b''.join(parts)
- sfb/transport/dns/dns_codec.py:92 listcomp before: labels = [label for label in name.split('.') if label]; after: labels = []; for label in name.split('.'): if label: labels.append(label)
- sfb/transport/dns/dns_codec.py:207 listcomp before: labels = [label for label in name.split('.') if label]; after: labels = []; for label in name.split('.'): if label: labels.append(label)
- sfb/transport/dns/dns_codec.py:225 genexp before: total_len = sum(len(label) for label in labels) + (len(labels) - 1); after: total_len = 0; for label in labels: total_len += len(label); total_len += (len(labels) - 1)
- sfb/transport/dns/dns_codec.py:418 listcomp before: base_labels = [label for label in base_domain.split('.') if label]; after: base_labels = []; for label in base_domain.split('.'): if label: base_labels.append(label)
- sfb/transport/proxy_helpers.py:234 genexp before: if extra and any(byte not in (13, 10) for byte in bytearray(extra)): after: if extra: has_non_crlf = False; for byte in bytearray(extra): if byte not in (13, 10): has_non_crlf = True; break; if has_non_crlf: ...
- sfb/transport/proxy_helpers.py:272 genexp before: if any(ch.isspace() for ch in value): after: has_space = False; for ch in value: if ch.isspace(): has_space = True; break; if has_space: ...
- sfb/transport/tls_handshake/tls_handshake_config.py:175 listcomp before: tokens = [token.strip() for token in value.split(',')]; after: tokens = []; for token in value.split(','): tokens.append(token.strip())
- sfb/transport/tls_handshake/tls_handshake_config.py:176 genexp before: if not tokens or any(not token for token in tokens): after: has_empty = False; for token in tokens: if not token: has_empty = True; break; if not tokens or has_empty:
- sfb/transport/tls_handshake_bump/tls_handshake_bump_codec.py:411 genexp before: total_len = sum(len(label) for label in labels) + (len(labels) - 1); after: total_len = 0; for label in labels: total_len += len(label); total_len += (len(labels) - 1)
- sfb/transport/tls_handshake_bump/tls_handshake_bump_config.py:177 genexp before: if any(ch.isspace() for ch in value): after: has_space = False; for ch in value: if ch.isspace(): has_space = True; break; if has_space: ...
- sfb/tunnel/base_tunnel.py:706 listcomp before: 'rtt_samples_ms': [round(sample, 3) for sample in rtt_samples]; after: rounded = []; for sample in rtt_samples: rounded.append(round(sample, 3)); 'rtt_samples_ms': rounded
