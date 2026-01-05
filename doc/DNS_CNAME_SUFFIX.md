# DNS CNAME Suffix Rationale

This document explains why DNS transport responses use a CNAME target suffix
of the form `<cname_label>.<base_domain>` (default `0.<base_domain>`).

## Background

The DNS transport encodes Bob's response data into the CNAME target name. In
authoritative mode, Alice often queries through a recursive resolver. Most
resolvers automatically chase CNAME targets by issuing a follow-up A query.

If the CNAME target were just:

```
<data_labels>.<base_domain>
```

then the resolver's follow-up A query would be indistinguishable from a real
tunnel query (which is also an A query under `<base_domain>`). Bob would treat
that follow-up as tunnel data and feed it into the tunnel, which is incorrect.

## Why the `0.` label exists

We add a short, non-base32 label before the base domain (configurable as
`dns_cname_label`, default `0`):

```
<data_labels>.0.<base_domain>
```

This gives Bob a reliable way to distinguish CNAME follow-up queries from real
tunnel queries:

- Tunnel queries: `<nonce>.<data_labels>.<base_domain>`
- CNAME follow-ups: `<data_labels>.0.<base_domain>`

Because the label must include non-base32 characters, it cannot appear in
tunnel data labels. This prevents collisions and makes the detection
unambiguous.

## Why not just use `<base_domain>` alone

Using only `<base_domain>` as the suffix is sufficient for parsing, but it does
not provide a discriminator for follow-up queries. The `0.` label is needed to
keep resolver follow-ups from being misclassified as tunnel data.

## Direct mode note

In direct mode (Alice queries Bob directly), resolver follow-up behavior does
not occur. The `0.` label still remains in place to keep the authoritative mode
behavior correct and consistent.
