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
     * **Agent 1 (e.g. Codex)**: Backend architecture, scrapers, API routes, database models.
     * **Agent 2 (e.g. Antigravity)**: Frontend components, test coverage, UI/UX flows, build verification.
   * Both workers run concurrently in parallel.

2. **Adaptive Reasoning Effort**:
   * **`low`**: Typos, single variable renames, docstrings, formatting, status checks.
   * **`medium`**: Feature implementations, unit tests, code reviews, audits.
   * **`high`**: Architectural rewrites, concurrency / race conditions, complex multi-file bugs.

3. **Repository Rules & `AGENTS.md`**:
   * If the active project contains an `AGENTS.md`, `CLAUDE.md`, or `README.md`, subagents must preserve existing architecture, run tests before completion, and respect project branch conventions.
