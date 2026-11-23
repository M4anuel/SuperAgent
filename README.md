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

## TUI Shortcuts
- `Ctrl+P`: Cycle through registered projects
- `Ctrl+K`: Cancel currently selected subagent task
- `Ctrl+L`: Clear completed/cancelled task cards
- `Ctrl+Q`: Exit SuperAgent
