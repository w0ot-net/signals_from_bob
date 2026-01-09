# signals_from_bob
multi network tunnel tool, supporting tcp, udp, icmp, dns, tls_handshake

## Compatibility
This project must remain compatible with Python 2.7 and Python 3.
This project is IPv4-only; IPv6 addresses and sockets are not supported.

## Text Encoding
Use ASCII only for code and scripts. Non-ASCII is allowed in .md files.

## Development
Enable the pre-commit hook to enforce ASCII-only files:
```
git config core.hooksPath .githooks
```

## Single-file Bundle
Generate a single-file bundle from the manifest:
```
python3 scripts/flatten.py --manifest doc/flatten_manifest.txt --output sfb_flat.py
```
Add `--minify` to use `python-minifier` (external dependency) and rename locals:
```
python3 scripts/flatten.py --manifest doc/flatten_manifest.txt --output sfb_flat.py --minify
```
Use `--minify-globals` to allow renaming module-level globals (unsafe across modules).
Run it the same way as the normal entrypoint:
```
python3 sfb_flat.py --role client --transport dns --domain t.example.com
```
Set `SFB_FLAT_ROOT` to control the temporary on-disk root used for `__file__`
paths in the flattened bundle.

## Profiling
Use `--cprofile` to write a cProfile output file.
Examples:
```
python3 -m sfb.cli --cprofile --role client --transport dns --domain t.example.com
python3 -m sfb.cli --cprofile /tmp/sfb_run.prof --role client --transport dns --domain t.example.com
```
Default output: `/tmp/sfb_<role>_<transport>_<YYYYMMDD_HHMMSS>_<pid>.prof`
(falls back to `tempfile.gettempdir()` when `/tmp` is unavailable).
Note: Thread profiling uses the standard library `profile` module to capture
threads (cProfile cannot be active in multiple threads at once) and is slower
than cProfile.
