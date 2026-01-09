# Stdout Log Event Format Plan

Status: completed

## Summary
Simplify stdout logging so structured events print only the event name and the
JSON fields, without the pre-`|` prefix or the `event=`/`fields=` labels.

## Goals
- Stdout structured logs show `<event> <fields_json>` with no leading
  `INFO ... |` prefix.
- Remove `event=` and `fields=` labels on stdout while keeping the same data.
- Preserve SQLite logging formats and schemas.

## Non-Goals
- Change DB logging or file logging formats.
- Alter `log_event` payload contents.
- Add or run automated tests.

## Affected Components
- `sfb/logging_util.py`
- `sfb/cli.py`

## Plan
1. Extend `StructuredLogFormatter` in `sfb/logging_util.py`.
   - Add options to control message inclusion and label rendering, e.g.
     `include_message`, `label_event`, `label_fields`, defaulting to current
     behavior.
   - When an event or fields exist and `include_message` is False, omit the
     base formatted message and render only:
     - `event` (if present)
     - `_encode_fields(fields)` (if present)
     separated by a space.
   - If neither event nor fields exist, fall back to the base formatted
     message so unstructured logs still appear.

2. Update stdout formatter wiring in `sfb/cli.py`.
   - Use the new formatter options for stdout with
     `include_message=False`, `label_event=False`, `label_fields=False`.
   - Use a simple base format (`'%(message)s'`) so the fallback message does
     not include log level prefixes.
   - Keep the max line length limit unchanged.

3. Manual validation (no tests).
   - Run the CLI with stdout logging and confirm output format is:
     `event {"k": "v"}`.
   - Confirm unstructured logs still show their message.
   - Confirm SQLite logs remain unchanged.

## Testing
- Do not run tests here.

## Execution Notes
- Added formatter options to omit the base message and labels when emitting
  structured events.
- Updated stdout logging to output `event` and JSON fields only.
- Manual validation not run (per instructions).
