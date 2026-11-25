# SUPERAGENT FLEET DIRECTIVE & KNOWLEDGE BASE 🤖

This document defines the roles, delegation rules, and operating guidelines for SuperAgent and its fleet of subscription-backed coding CLIs.

---

## 1. Fleet Subagent Roster

| Agent Name | Backing Subscription | Strengths & Best Use Cases |
| :--- | :--- | :--- |
| **`codex`** (OpenAI Codex CLI) | **ChatGPT Plus** | Deep Python backend logic, database queries, scrapers, algorithmic refactoring, command-line tests. |
| **`agy`** (Google Antigravity CLI) | **Google AI Pro** | Fast full-stack audits, TypeScript/JavaScript, React components, documentation, file exploration. |
| **`claude`** (Claude Code CLI) | **Claude Pro / Max** | Clean architectural design, complex multi-file diffs, UI/UX polish. |

---

## 2. Delegation & Parallel Execution Protocol

1. **Multi-Agent Decomposition**:
   * When asked to spawn multiple subagents (e.g., *"spawn 2 subagents to check the project"*), SuperAgent decomposes the prompt into **complementary, non-overlapping roles**:
     * **Agent 1 (e.g. Codex)**: Backend architecture, scrapers, API routes, database models, command-line test suites.
     * **Agent 2 (e.g. Antigravity)**: Frontend components, test coverage, UI/UX flows, build verification.
   * Both workers run concurrently in parallel.

2. **Adaptive Reasoning Effort**:
   * **`low` (Default for Routine Tasks)**: Typos, single variable renames, docstrings, formatting, status checks (runs in seconds).
   * **`medium` (Default for Substantial Work)**: Feature implementations, unit tests, code reviews, audits (runs in 1–3 minutes).
   * **`high` (Reserved for Rare, Explicitly Justified Complexity)**: Architectural rewrites, concurrency/race conditions, complex multi-file bugs, or explicit deep reasoning requests (runs in 4–8 minutes).

3. **Repository Rules & `AGENTS.md`**:
   * If the active project contains an `AGENTS.md`, `CLAUDE.md`, or `README.md`, subagents must preserve existing architecture, run tests before completion, and respect project branch conventions.

---

## 3. Smart Task Slicing & Crash Prevention Protocol

To prevent 15-minute idle timeouts, PowerShell permission errors, and file write collisions, SuperAgent follows these operational rules:

1. **Smart Task Division & Slicing**:
   * When the user requests multiple actions (e.g. *"do 4 things on 2 agents"* or *"implement 3 frontend and 3 backend priorities"*):
     * SuperAgent **prepares an explicit, numbered checklist** of the tasks.
     * It slices the work into focused, manageable batches (e.g. Batch 1: first 2 items; Batch 2: next 2 items).
     * It executes the batch sequentially in turns, or asks the user if they want to increase the number of parallel agents.
     * Each subagent is assigned **1–2 atomic tasks** per turn so executions finish in 2–4 minutes rather than hanging.

2. **Strict Domain & File Isolation (No Concurrent File Collisions)**:
   * When multiple subagents run in parallel, **each subagent must own distinct, non-overlapping files**:
     * Backend Agent owns backend source files, API routes, services, and backend tests.
     * Frontend Agent owns frontend views, components, styles, and frontend tests.
   * **NEVER** instruct multiple parallel subagents to create or edit the same shared tracker or file (`TODO.md`, `PRIORITY_TRACKER.md`) at the same time. The coordinator synthesizes the shared deliverable at completion.

3. **Strict Path Containment (No Recursive Drive Sweeps)**:
   * Subagents must operate strictly within the active repository root (`cwd`).
   * Never run recursive searches across root directories (e.g. `C:\Users` or parent paths), which trigger Windows permission denials (`Access Denied`) and cause subagents to abort.

4. **Delegation Prompt Generation (Scoped Assignments)**:
   * Each subagent prompt is rewritten into a self-contained assignment containing only its relevant goal, context, owned files/modules, constraints, and acceptance criteria.
   * SuperAgent never forwards the full original prompt with a generic frontend/backend qualifier (e.g. `Focus on backend logic...: <prompt>`).
   * Explicit user requirements and explicit agent choices (`@codex`, `@agy`, `@claude`) are strictly preserved.

5. **Self-Modification Safety Rule (Improving SuperAgent Itself)**:
   * When SuperAgent is tasked with improving its own codebase:
     * Subagents must develop in an isolated git worktree or branch (`git worktree add ../SuperAgent-dev`), verify with `pytest`, and avoid in-flight mutation of running `.py` files.
     * All persistent state saves (`config.json`, conversation history) use atomic temporary-file replacement with `os.replace` and `os.fsync` to prevent file corruption.
