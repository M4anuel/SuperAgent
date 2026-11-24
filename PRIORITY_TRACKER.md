# SuperAgent priority tracker

Updated 2026-11-24. Sources: `SUPERAGENT.md`, `README.md`, and the existing
SuperAgent audit in conversation `conv_1795474325_78914b`, turn
`turn_1795477005_ad728e` under `~/.superagent/history/conversations`.
Relevant reports are preserved in `docs/CODEX_SUPERAGENT_AUDIT.md` and
`docs/AGY_SUPERAGENT_AUDIT.md`. The reports in `~/.superagent/reports` refer to
BetterILIAS and are not the source for this project.

Backend items retain their original order in the Codex audit, rather than the
reordered combined top-ten synthesis. Frontend selections and implementation
status are recorded in `AGY_PRIORITY_EVIDENCE.md`.

| ID | Agent | Priority | Status | Validation evidence |
| --- | --- | --- | --- | --- |
| B1 | Codex | Make execution permissions explicit; restrict planners/synthesizers and require opt-in for unrestricted workers | Complete | `tests/test_backend_priorities.py` and `tests/test_recovered_backend.py`: restricted/unrestricted policies, invalid dispatch, Antigravity alias, and read-only planner/synthesis timeout checks; profile/timeout controls documented in `README.md` |
| B2 | Codex | Remove the hardcoded default project; validate paths and detect the deepest matching project | Complete | Fresh/existing registries, isolated defaults, empty/missing/file paths, deleted directories before launch, and deepest matching nested project regressions pass |
| B3 | Codex | Supervise subprocesses and background tasks; clean process trees on cancellation, timeout and shutdown | Complete | Controlled Windows process-tree checks pass for worker cancellation, timeout, shutdown, reader failure, exited wrappers, cancellation during launch, and planner/synthesis cancellation; large-line and split UTF-8 output pass |
| F1 | agy / frontend | Recover visibly from unhandled triage exceptions | Complete | Visible escaped error message, restored focus, context-bound `:retry`; failure/retry regression passes |
| F2 | agy / frontend | Replace single-line prompt input with expanding multiline TextArea | Complete | Enter submits; Shift+Enter/Ctrl+J inserts newline; height grows from 1 to 6 lines; multiline explicit routing preserved and tested |
| F3 | agy / frontend | Batch or paginate console log rendering when selecting a task | Complete | 400-line pages, batched writes, bounded line/display size, earlier/latest navigation; 10,000-line regression passes |

## Coordination

Codex owns B1-B3. agy confirmed F1-F3 from its ranked audit matrix (ranks 2,
3 and 6), inspected the implementation, ran the focused Textual regressions
and the full suite, and recorded completion in `AGY_PRIORITY_EVIDENCE.md`.
Backend and frontend changes were integrated and verified together. Windows
cleanup includes both pre-assignment descendant termination and Job Objects;
multiline explicit routing is preserved.

## Remaining audit backlog

These items remain unverified and pending unless explicitly covered by B1-B3
or F1-F3 above. Source details and original ordering remain in the two audit
documents; overlapping recommendations are combined here.

| ID | Area | Improvement | Status |
| --- | --- | --- | --- |
| R1 | Packaging | Declare runtime/test/optional dependencies and verify clean installation | Pending |
| R2 | Dispatch | Enforce fleet restrictions and validate model decisions at every dispatch boundary | Pending |
| R3 | History | Bind completion to the originating project and conversation | Pending |
| R4 | Coordination | Isolate concurrent writers and attribute changes from a recorded baseline | Pending |
| R5 | Persistence | Preserve corrupt configuration, use atomic writes and protect concurrent saves | Pending |
| R6 | Testing | Extend isolated regression coverage and Windows/Linux CI | Pending |
| R7 | Logging | Bound retained output, spool complete logs and release finished task records | Pending |
| R8 | Async execution | Remove remaining blocking model calls from async workflows | Pending |
| R9 | Planning | Validate successful structured planner output and preserve complete reports | Pending |
| R10 | Headless | Propagate failure exit codes and run configured project validation | Pending |
| R11 | Storage | Protect credentials, validate history IDs and enforce path containment | Pending |
| R12 | Context | Honor agent adapters/settings and provide bounded conversation context | Pending |
| R13 | Projects | Add deliberate project quick-pick navigation | Pending |
| R14 | Layout | Support narrow terminals, wrapping cards and keyboard card navigation | Pending |
| R15 | Feedback | Show incremental worker completion and pause auto-scroll while reading | Pending |
| R16 | Clipboard | Copy feed/synthesis/diffs and support platform clipboard fallbacks | Pending |
| R17 | Diagnostics | Add CLI/TUI pre-flight tooling checks | Pending |
| R18 | Documentation | Correct installation paths and document all commands/shortcuts | Pending |

## Final verification

The original backend assignment and existing implementation were preserved.
Strict verification exposed a launch cancellation descendant leak on Windows;
the replacement backend agent fixed it and reran the integrated suite.

Validation on Windows / Python 3.13:

```text
python -B -m pytest -q -p no:cacheprovider -W error::ResourceWarning -W error::pytest.PytestUnraisableExceptionWarning
77 passed in 16.62s
git diff --check
Exit code 0 (only Git's LF/CRLF conversion notices)
```

Tests isolate user configuration/history and block real model or agent
subprocesses. Lifecycle tests launch controlled Python children. Live agent
integration and Linux execution were not exercised in this recovery.
Frontend evidence is recorded in `AGY_PRIORITY_EVIDENCE.md` and
`tests/test_frontend_priorities.py`. Existing untracked `TODO.md`, audit reports,
and encoding fix were preserved. Generated tracked bytecode was restored to its initial state. Remaining audit
recommendations above have not been marked complete by inference.
