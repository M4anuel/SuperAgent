# SuperAgent CLI ⚡

A high-performance Terminal Coordinator that manages multiple coding projects and delegates subtasks to subscription-backed agent CLIs:
- **Anthropic Claude Code** (`claude`)
- **OpenAI Codex / ChatGPT CLI** (`codex`)
- **Google Antigravity** (`agy`)

## Features

- **Integrated Gemini Brain & First Responder**:
  - Automatically triages user requests into conversational replies vs. coding tasks.
  - Conversational prompts (*"respond yes"*, *"what is this project?"*) answer instantly in the feed with **zero subprocesses**.
  - Coding tasks are delegated with **Adaptive Reasoning Effort** (`low`, `medium`, `high`) based on complexity.
  - Dynamically configures subagent flags (e.g. `codex -c model_reasoning_effort="low|medium|high"`).
- **Multi-Project Registry**: Projects are stored in `~/.superagent/config.json`. Switch between repositories effortlessly with `:p <name>` or CLI flags.
- **Interactive Terminal UI (TUI)**:
  - Live status cards for every subagent (`RUNNING`, `DONE`, `FAILED`, `CANCELLED`) with effort badges and timers.
  - Real-time elapsed duration timers for each subtask.
  - Split view with **🤖 SuperAgent Feed** and **💻 Subagent Console**.
  - Panel-bounded mouse selection & Windows clipboard sync (`Ctrl+C` and `Ctrl+Y`).
- **Headless Execution**: Run non-interactive agent tasks directly from your shell scripts or CI.

## Installation

Install in editable mode globally or in your Python environment:

```bash
cd tools/superagent
python -m pip install -e .
```

This registers the `superagent` command globally in your terminal.

## Quickstart

### 1. Launch the TUI Dashboard
```bash
superagent
# or specify a project:
superagent tui betterilias
```

### 2. Manage Projects
New installations start with an empty project registry. Register an existing
directory before dispatching work. When repositories are nested, automatic
detection selects the deepest registered directory.

```bash
# List all registered projects
superagent project list

# Register a new repository
superagent project add my-api C:/Users/name/Projects/my-api --agent claude
```

### 3. Headless Task Delegation
```bash
superagent run claude "Refactor auth middleware to support bearer tokens" --project betterilias
```

Workers default to the `workspace-write` execution profile. Planning and
synthesis always use the CLI's restricted read-only/plan mode. SuperAgent prints
the selected profile before dispatch. To select another worker profile explicitly:

```bash
superagent run codex "Inspect the project" --project my-api --execution-profile read-only
superagent run codex "Implement the change" --project my-api --execution-profile unrestricted --timeout 600
```

The `unrestricted` profile explicitly enables the agent CLI's permission-bypass
flags. Set `execution_profile` in `~/.superagent/config.json` to change the TUI
and headless default; `task_timeout_seconds` defaults to 1800. Codex uses its
read-only/workspace sandbox, Antigravity uses sandbox and plan/accept-edits modes,
and Claude uses plan/acceptEdits permission modes. These are the respective CLI's
controls; SuperAgent does not add an OS filesystem sandbox of its own. Invalid
profiles and missing project directories fail before dispatch.

Cancellation, deadlines and TUI shutdown clean up owned process trees and await
exit. Windows uses a kill-on-close Job Object; POSIX uses a separate process
group. Git inspection runs off the UI event loop with a bounded deadline.

Run verification with `python -m pytest tests/`. Tests redirect configuration and
history into temporary directories and block subscription-agent launches by
default; lifecycle regressions use controlled Python subprocesses.

## TUI Shortcuts
- `Enter`: Send the prompt; `Shift+Enter` or `Ctrl+J`: Insert a newline
- `:retry`: Retry the last failed request in its original project/conversation
- `:logs earlier` / `:logs latest`: Browse 400-line console pages or resume live output
- `Enter`: Submit the prompt
- `Shift+Enter` or `Ctrl+J`: Insert a newline; pasted multiline text is preserved
- `:retry`: Retry the last failed request in its original project and conversation
- `:logs earlier` / `:logs latest`: Page through the selected task's console output
- `Ctrl+P`: Cycle through registered projects
- `Ctrl+H`: Open conversation history
- `Ctrl+K`: Cancel currently selected subagent task
- `Ctrl+L`: Clear completed/cancelled task cards
- `Ctrl+Q`: Exit SuperAgent

Implementation status and remaining audit recommendations are tracked in
[PRIORITY_TRACKER.md](PRIORITY_TRACKER.md).
