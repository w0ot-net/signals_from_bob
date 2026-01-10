# DNS Stager One-Liner Minify Plan

Status: draft

## Summary
Shrink the generated `linux_dns_stager.txt` and `windows_dns_stager.txt`
one-liners by removing comments, unused imports, and long identifiers. The
one-liners should stay Python 2/3 compatible, ASCII-only, and preserve
current behavior.

## Goals
- Eliminate comments and blank lines from the rendered payload.
- Minify identifiers inside the template so local variables are short.
- Avoid importing modules that are unused by a given platform.
- Keep the output as a single command line per platform.

## Non-Goals
- Changes to the DNS stager protocol or chunking logic.
- Adding external dependencies for minification.
- Tests or e2e validation.

## Affected Components
- `sfb/stagers/dns_stager_template.py`
- `sfb/stagers/dns_stager.py`
- `linux_dns_stager.txt` (generated)
- `windows_dns_stager.txt` (generated)

## Plan
1. Minify the template source.
   - Remove all comments and non-essential blank lines.
   - Replace verbose identifiers with short names (single-letter where safe).
   - Drop unused helpers (e.g., `text_type`) and avoid decoding payload to
     text before `exec` (use `compile(payload, ...)` on bytes instead).
   - Remove unused imports (e.g., `re`, `subprocess`, `random`, `time`) from
     the shared template where possible.

2. Split platform-specific imports from the template.
   - Add a new `{{EXTRA_IMPORTS}}` placeholder in the template.
   - For Linux: empty (no `subprocess`, no `re`).
   - For Windows: include only what is required for resolver discovery.

3. Simplify resolver snippets to avoid regex.
   - Replace regex parsing in the Windows resolver snippet with direct
     `split`/`startswith` parsing to remove the `re` import.
   - Keep the Linux resolver snippet unchanged but shorten variable names.

4. Keep the DNS ID logic minimal.
   - Replace `random.randint` with a monotonic counter stored in a small
     mutable (e.g., list) to drop the `random` import.
   - Ensure the ID still changes between queries to reduce stale responses.

5. Ensure one-liners are fully compact.
   - Render the minified template and wrap it as `python -c "exec(...)"`.
   - Verify that the output is a single line with no embedded comments.

## Testing
- Do not run tests here.
