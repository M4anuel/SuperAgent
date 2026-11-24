# TODO

## SuperAgent CLI context

Project context recorded from `README.md`, `SUPERAGENT.md`, and `pyproject.toml`:

- SuperAgent CLI is a terminal coordinator for multiple coding projects, delegating tasks to Claude Code (`claude`), OpenAI Codex (`codex`), and Google Antigravity (`agy`).
- The documented Gemini brain triages conversational requests and coding tasks, with adaptive reasoning effort (`low`, `medium`, `high`) for delegated work.
- The project registry lives at `~/.superagent/config.json`.
- The interactive TUI provides task status cards, elapsed timers, a feed and subagent console, project switching, and task cancellation.
- Headless execution is available through `superagent run`; project management uses `superagent project`.
- The Python package is `superagent-cli`, requires Python 3.10 or later, and exposes `superagent` through `superagent.cli:main`.

## Pending clarification

- [ ] Obtain the complete installation command from the supplied context. The installation command is incomplete; no missing arguments, package source, path, or other details have been inferred or invented. The existing installation example in `README.md` is separate reference material and is not treated as a completion of the supplied command.
