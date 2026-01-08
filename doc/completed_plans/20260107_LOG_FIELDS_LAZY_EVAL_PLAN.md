# Lazy Log Fields Evaluation Plan

Status: completed

## Goal
- Avoid calling `fields()` for structured logs that are filtered out, while
  preserving current log output and filter behavior.

## Non-Goals
- Change filter logic, profile semantics, or log record formats.
- Add non-stdlib dependencies.

## Affected Components
- sfb/logging_util.py (log_event, StructuredLogFormatter, SQLiteLogHandler)

## Plan
1. Add a shared resolver for record fields.
   - Implement `_resolve_fields(record)` in `sfb/logging_util.py` that:
     - Detects a callable `record.fields`.
     - Evaluates it once and caches the result on the record (overwrite
       `record.fields` with the dict).
     - Handles `None` cleanly.
2. Make `log_event` lazy.
   - Store the callable in `record.fields` instead of calling it immediately.
   - Keep the existing `logger.isEnabledFor` guard and callable validation.
3. Resolve fields at emission time.
   - In `StructuredLogFormatter.format`, call `_resolve_fields(record)` before
     encoding fields.
   - In `SQLiteLogHandler.emit`, call `_resolve_fields(record)` before calling
     `_encode_fields(...)` so the SQLite payload uses the resolved dict.
4. Behavior notes.
   - Document that `fields()` exceptions will now occur during formatting/
     emission rather than at `log_event` call time, and ensure `handleError`
     behavior is acceptable.

## Validation
- `python3 -m py_compile sfb/logging_util.py`
- Manual sanity: run a profile with a narrow whitelist and confirm the
  filtered events no longer evaluate their `fields()` functions.

## Execution Notes
- Added `_resolve_fields(record)` to lazily evaluate/caches callable fields.
- Updated `log_event` to store callables, and both formatter/SQLite handler to
  resolve fields at emission time.
