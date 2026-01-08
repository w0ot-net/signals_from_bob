# DNS Base32 Label Chunking Plan

Status: draft

## Goal

Remove duplicated base32 label chunking loops in the DNS codec by introducing
_b32_labels(data, label_max_len) and reusing it in query and CNAME encoding
without changing behavior.

## Affected Components

- sfb/transport/dns/dns_codec.py

## Design Notes

- _b32_labels should base32_encode the payload, split into label_max_len-sized
  chunks, and return a list (possibly empty).
- Normalize label_max_len in _b32_labels so callers do not repeat the same
  validation when they only need chunking.
- encode_query_name should keep the nonce label first and preserve base domain
  suffix handling.
- encode_cname_target should preserve suffix label handling and name-length
  validation.

## Implementation Steps

1. Add _b32_labels(data, label_max_len) near the existing base32 helpers and
   include a short docstring describing its behavior and inputs.
2. Update encode_query_name to build data labels via _b32_labels and remove the
   duplicated chunking loop.
3. Update encode_cname_target to build data labels via _b32_labels and remove
   the duplicated chunking loop.
4. Ensure any redundant local normalization or temporary variables are removed
   once _b32_labels is in use.

## Validation

- Compare label lists for empty, single-label, and multi-label payloads to
  confirm the output matches the previous behavior.
- Confirm label and name length validation still runs on assembled labels.
