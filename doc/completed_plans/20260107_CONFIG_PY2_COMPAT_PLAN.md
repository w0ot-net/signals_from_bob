# Config Python 2 Compatibility Plan

Status: completed

## Goal
- Make `sfb/config.py` valid under Python 2.7 while preserving current defaults
  and behavior on Python 3.

## Non-Goals
- Add non-stdlib dependencies (no dataclasses backport, no typing module).
- Change config semantics or CLI behavior unless required for compatibility.

## Affected Components
- sfb/config.py

## Plan
1. Remove Python 3-only syntax and imports.
   - Drop `dataclasses` and the `@dataclass` decorator.
   - Remove `typing` imports and the `TYPE_CHECKING` block.
   - Replace PEP 526 annotated attributes with plain assignments.
2. Add an explicit initializer for `Config`.
   - Introduce a `_FIELDS` tuple listing valid config field names.
   - In `__init__(**kwargs)`, set instance attributes from class defaults and
     apply keyword overrides.
   - Reject unknown keys with a clear `TypeError`.
   - Call `validate()` at the end to preserve the current `__post_init__`
     behavior.
3. Preserve external behavior.
   - Keep `Config()` and `Config(**config_kwargs)` usage intact.
   - Avoid compatibility shims; prefer the simplified class-based config.

## Validation
- `python3 -m py_compile sfb/config.py`
- Optional: user-run Python 2.7 import check (`python2.7 -c "import sfb.config"`).

## Execution Notes
- Removed dataclass/typing usage in `sfb/config.py`, replaced annotations with
  plain assignments, added explicit `_FIELDS` + `__init__`, and dropped the
  type-annotated `make_cipher` signature for Python 2.7 compatibility.
