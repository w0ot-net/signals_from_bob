# MC/DC Full Coverage Plan

Status: draft

## Goal

Achieve 100% MC/DC coverage across the non-test Python codebase by building a
standard-library-only instrumentation harness, running targeted test suites,
closing decision-coverage gaps, and enforcing a coverage gate.

## Non-Goals

- Modify any code under ./tests.
- Run tests/e2e/.
- Change runtime behavior outside of coverage-instrumented runs.

## Affected Components

- sfb/**/*.py (all non-test modules)
- sfb.py
- unit_tests/** (new/updated tests only)
- integration_tests/** (as needed, but not tests/e2e/)
- scripts/mcdc_runner.py (new)
- sfb/testing/mcdc.py (new)
- doc/architecture/ (optional notes on coverage harness)

## Design Notes

- Use only the Python standard library.
- MC/DC requires per-decision condition tracking with short-circuit behavior
  preserved; use AST rewriting at import time to wrap boolean expressions.
- Collect coverage data per run and merge across runs (and OSes) to reach 100%.
- Keep instrumentation off by default and enable via env var or CLI flag.
- Do not alter production logic; only add instrumentation wrappers and test
  coverage harness.

## Plan

1. Define the MC/DC data model and storage format.
   - Create `sfb/testing/mcdc.py` with:
     - A decision registry that assigns stable IDs to each boolean decision
       and its constituent conditions.
     - A logging API to record condition vectors and decision outcomes.
     - A JSON output format that can be merged across runs.
   - Document the expected JSON schema so runs can be merged deterministically.

2. Build an AST-based instrumentation loader.
   - Implement an import hook in `scripts/mcdc_runner.py` that:
     - Parses module ASTs for boolean decisions (`and`, `or`, ternaries, and
       chained comparisons).
     - Rewrites decisions to evaluate each condition exactly once, preserving
       short-circuit semantics while recording per-condition truth values and
       final decision value.
     - Emits stable IDs based on module path + line/col + decision index to
       avoid drift between runs.
   - Ensure instrumentation excludes:
     - Test files.
     - Generated files.
     - Optional skip list for modules that are platform-specific or unsafe to
       import during analysis.

3. Create a runner that executes target suites under instrumentation.
   - Add `scripts/mcdc_runner.py` to:
     - Accept a list of test entry points (unit_tests and integration_tests).
     - Run with `python3` and set env vars to enable instrumentation.
     - Write per-run coverage JSON to `logs/` with a timestamped filename.
   - Add a merge step that combines JSON files into a single MC/DC report.

4. Establish a baseline coverage report.
   - Run the runner on existing unit_tests and integration_tests to produce a
     baseline MC/DC report.
   - Emit a sorted list of uncovered decisions with file/line references to
     guide test additions.

5. Close MC/DC gaps with targeted tests.
   - Add new tests under `unit_tests/` or `integration_tests/` (not ./tests).
   - Prioritize critical logic (tunnel, transport, reliability, modules).
   - Use focused inputs to toggle each boolean condition independently and
     satisfy MC/DC requirements per decision.

6. Add a coverage gate.
   - Extend `scripts/mcdc_runner.py` with a `--require-100` option that fails
     if MC/DC is below 100%.
   - Provide a summary report: total decisions, covered decisions, and a list
     of remaining gaps if any.

7. Document usage.
   - Add a short note under `doc/architecture/` or `README.md` describing how
     to run the MC/DC harness and interpret the output.

## Validation

- Run `python3 scripts/mcdc_runner.py --suite unit_tests --suite integration_tests`.
- Confirm the merged report shows 100% MC/DC for all non-test modules.
- Do not run tests/e2e/.
