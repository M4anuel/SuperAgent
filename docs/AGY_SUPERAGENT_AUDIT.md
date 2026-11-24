# SuperAgent Comprehensive System Audit & Top 10 Improvement Plan
This audit reviews SuperAgent across all eight operational domains specified: **Terminal UI**, **User Workflows**, **Status Feedback**, **Console Interaction**, **Clipboard Behavior**, **Error Recovery**, **Onboarding**, and **Documentation**.
Per the fleet multi-agent protocol, the investigation was split into two complementary agent audits:
* **Agent 1**: Audited the presentation and interaction layers (**Terminal UI**, **Console Interaction**, **Clipboard Behavior**, and **Status Feedback**).
* **Agent 2**: Audited the architectural, operational, and lifecycle layers (**User Workflows**, **Error Recovery**, **Onboarding**, and **Documentation**).
Below are the detailed findings from both agents, followed by the combined, deduplicated, and ranked **Top 10 Improvements Matrix**.
---
## Agent 1 Audit: UI, Console, Clipboard & Status Feedback
### 1. Terminal UI & Responsive Layout
* **Rigid Column Split & Small-Terminal Clipping**:
  * **File Reference**: [app.py](file:///C:/Users/manue/Downloads/SuperAgent/superagent/tui/app.py#L44-L71) (`APP_CSS`)
  * **Finding**: `#cards-pane` is hardcoded to `width: 45%` and `#log-pane` to `width: 55%` within a fixed horizontal container. On narrower terminals (< 100 columns), subagent status cards and console tabs collapse and wrap awkwardly. There is no toggle or split-resizing shortcut (e.g., maximize console or feed).
  * **Proposed Improvement**: Implement responsive breakpoints in CSS or support a fullscreen toggle binding (`F11` or `Ctrl+B` to toggle sidebar collapse) to dedicate 100% width to the active feed/console on compact displays.
  * **Impact**: Drastically improves usability on laptops, split-terminal tmux panes, and compact windows.
  * **Effort**: Low (2–3 hours).
* **Card Content Overflow & Missing Focus Navigation**:
  * **File Reference**: [cards.py](file:///C:/Users/manue/Downloads/SuperAgent/superagent/tui/cards.py#L30-L78), [app.py](file:///C:/Users/manue/Downloads/SuperAgent/superagent/tui/app.py#L133-L144)
  * **Finding**: `SubagentCard` is constrained to `height: 4`. If an instruction or prompt exceeds ~35 characters, the text is abruptly clipped. Additionally, while `SubagentCard.can_focus = True`, focus is permanently trapped on `#prompt-input`. Users cannot navigate cards with `Up`/`Down` or `J`/`K` without reaching for the mouse.
  * **Proposed Improvement**: Change `SubagentCard` height to dynamic or wrap-safe (`min-height: 4; height: auto;`), and add keyboard bindings (`Alt+Up` / `Alt+Down` or a focus-cycle mode) to traverse cards via keyboard.
  * **Impact**: Eliminates truncation of task briefs and allows 100% keyboard-driven task inspection.
  * **Effort**: Low–Medium (3–4 hours).
---
### 2. Console Interaction & Log Streaming
* **Synchronous Log Dump Freezing the Event Loop on Card Switch**:
  * **File Reference**: [app.py](file:///C:/Users/manue/Downloads/SuperAgent/superagent/tui/app.py#L554-L576) (`select_task`)
  * **Finding**: When a user selects a card, `select_task()` calls `log_widget.clear()` and iterates synchronously through `task.log_lines`, calling `log_widget.write()` on every line. For large subagent outputs (e.g., test suites or file dumps with 2,000+ lines), this blocks Textual’s main thread, causing frame drops and input lag.
  * **Proposed Improvement**: Paginate or batch large log line writes (or cap historical rendering to the last 500 lines with an "Expand full log" option), streaming chunks asynchronously via Textual worker or `asyncio.sleep(0)`.
  * **Impact**: Restores smooth 60fps UI responsiveness when switching between tasks with massive outputs.
  * **Effort**: Medium (4–5 hours).
* **Auto-Scroll Snapping Traps User During Live Output**:
  * **File Reference**: [app.py](file:///C:/Users/manue/Downloads/SuperAgent/superagent/tui/app.py#L518-L524) (`on_runner_event`)
  * **Finding**: While an agent is streaming output, every incoming line triggers `log_widget.write()`. In Textual `RichLog`, writing automatically snaps the scroll position to the bottom. If the user scrolls up to inspect an error, each incoming line forcibly jerks the view back down.
  * **Proposed Improvement**: Check `log_widget.scroll_offset` vs. max scroll before writing; if user has scrolled up, pause auto-scroll and display a floating indicator: `"Auto-scroll paused (press G or scroll to bottom to resume)"`.
  * **Impact**: Enables smooth inspection of running subagents without fighting aggressive auto-scroll.
  * **Effort**: Medium (3–4 hours).
---
### 3. Clipboard Behavior & Multi-Target Extraction
* **Fragile Clipboard Fallbacks & Phantom Copy Notifications**:
  * **File Reference**: [app.py](file:///C:/Users/manue/Downloads/SuperAgent/superagent/tui/app.py#L167-L192) (`copy_to_clipboard`)
  * **Finding**: `copy_to_clipboard` falls back to `win32clipboard` and Windows `clip`. On Linux/macOS or terminal environments where `clip` is absent and OSC 52 is blocked by the host emulator, copying silently fails, yet the UI still triggers `self.notify("Copied ... to clipboard!")`. Furthermore, `action_copy_active_log` only copies the raw CLI console log—there is no shortcut to copy the latest First Responder synthesis, direct reply, or diff summary.
  * **Proposed Improvement**: Add cross-platform clipboard support (e.g., `pyperclip` or `wl-copy`/`xclip`/`pbcopy`), verify copy success before notifying, and add `Ctrl+Shift+C` / `:copy synthesis` to copy the latest coordinator report.
  * **Impact**: Reliable cross-platform clipboard support and instant sharing of coordinator reports without manual text highlighting.
  * **Effort**: Low (2–3 hours).
* **Unbound `Ctrl+C` Despite Documentation Claims**:
  * **File Reference**: [app.py](file:///C:/Users/manue/Downloads/SuperAgent/superagent/tui/app.py#L158-L165) (`BINDINGS`), [README.md](file:///C:/Users/manue/Downloads/SuperAgent/README.md#L20)
  * **Finding**: [README.md](file:///C:/Users/manue/Downloads/SuperAgent/README.md#L20) advertises `"Panel-bounded mouse selection & Windows clipboard sync (Ctrl+C and Ctrl+Y)"`. However, `Ctrl+C` is not registered in `SuperagentApp.BINDINGS`. Pressing `Ctrl+C` either triggers default terminal SIGINT termination or does nothing.
  * **Proposed Improvement**: Explicitly bind `Ctrl+C` in Textual to copy active selection or active card brief if no text is selected.
  * **Impact**: Eliminates discrepancy with documentation and avoids accidental app quits when users instinctively press `Ctrl+C`.
  * **Effort**: Low (1 hour).
---
### 4. Status Feedback & Telemetry Efficiency
* **Timer Sprawl & Event-Loop Overhead**:
  * **File Reference**: [cards.py](file:///C:/Users/manue/Downloads/SuperAgent/superagent/tui/cards.py#L48-L56), [app.py](file:///C:/Users/manue/Downloads/SuperAgent/superagent/tui/app.py#L280-L281)
  * **Finding**: Every single `SubagentCard` mounts its own independent interval timer (`self.set_interval(0.25, self.refresh_timer)`). If 8 subagents are executed in a session, 8 separate timer callbacks run every 250ms alongside the status bar timer in `SuperagentApp`.
  * **Proposed Improvement**: Centralize timer ticks in `SuperagentApp` (a single 500ms heartbeat) and notify active cards, or only update elapsed timers on running cards rather than finished/failed cards.
  * **Impact**: Reduces CPU utilization, improves battery life on laptops, and prevents Textual UI render churn.
  * **Effort**: Low (2 hours).
* **Feed Lacks Intermediate Progress Feedback During Multi-Agent Runs**:
  * **File Reference**: [app.py](file:///C:/Users/manue/Downloads/SuperAgent/superagent/tui/app.py#L696-L700) (`_monitor_and_synthesize_batch`)
  * **Finding**: In `_monitor_and_synthesize_batch`, the loop sleeps quietly while workers run. The main Feed shows only `Status: Workers dispatched in background` and gives zero feedback as individual workers finish (e.g. `Agent 1/2 finished in 34s, waiting on Agent 2/2...`) until the final synthesis arrives minutes later.
  * **Proposed Improvement**: Post incremental completion notices directly into the feed as each subagent in a batch completes.
  * **Impact**: Users receive immediate confirmation when subtasks finish rather than wondering if the coordinator is stalled.
  * **Effort**: Low (2 hours).
---
## Agent 2 Audit: Workflows, Error Recovery, Onboarding & Documentation
### 1. User Workflows & Multiline Input
* **Single-Line `Input` Precludes Complex Instructions & Code Snippets**:
  * **File Reference**: [app.py](file:///C:/Users/manue/Downloads/SuperAgent/superagent/tui/app.py#L254-L257) (`compose`), [app.py](file:///C:/Users/manue/Downloads/SuperAgent/superagent/tui/app.py#L129-L131) (`APP_CSS`)
  * **Finding**: SuperAgent uses a standard single-line `textual.widgets.Input`. Users cannot submit multi-line instructions, paste stack traces, provide SQL schemas, or write structured prompts without having newlines stripped or triggering premature submissions.
  * **Proposed Improvement**: Replace `Input` with Textual's `TextArea` configured in prompt mode (e.g., `Shift+Enter` for newline, `Enter` to submit, auto-growing height from 1 to 5 lines).
  * **Impact**: Crucial for real-world developer workflows where instructions include error traces, code blocks, or test commands.
  * **Effort**: Medium (4–6 hours).
* **Project Switching Lacks Selection Modal / Quick-Pick UI**:
  * **File Reference**: [app.py](file:///C:/Users/manue/Downloads/SuperAgent/superagent/tui/app.py#L913-L926) (`action_switch_project_dialog`)
  * **Finding**: Despite the method name `action_switch_project_dialog` and keybinding description `"Projects"`, pressing `Ctrl+P` simply blind-cycles to the next registered project in an array, immediately clearing and re-rendering the feed. If 6 projects are registered, the user must repeatedly cycle through all of them blind.
  * **Proposed Improvement**: Implement a modal screen or floating fuzzy popup (similar to VS Code's `Ctrl+P`) listing all registered projects with their active branches and paths.
  * **Impact**: Provides instant, deliberate navigation between projects without accidental context churn.
  * **Effort**: Medium (4–5 hours).
---
### 2. Error Recovery & Process Lifecycle
* **Unhandled Async Triage Exceptions Cause Permanent UI Hang**:
  * **File Reference**: [app.py](file:///C:/Users/manue/Downloads/SuperAgent/superagent/tui/app.py#L606-L616) (`_process_user_task`)
  * **Finding**: `_process_user_task` launches as an untracked background task (`asyncio.create_task(...)`). Inside, `await self.brain.triage(...)` has no enclosing `try...except` block. If triage fails (due to network timeout, malformed JSON, Gemini API rate limits, or missing CLI executables), an unhandled exception is thrown in the background task. The UI permanently displays `⏳ SuperAgent First Responder evaluating task...`, locking the user in limbo.
  * **Proposed Improvement**: Wrap `_process_user_task` with a top-level `try...except Exception as e:` handler that posts a clear, styled recovery box in `#superagent-feed` with diagnostic details and a `:retry` action.
  * **Impact**: Critical stability fix. Eliminates coordinator deadlocks caused by unhandled network or subprocess errors.
  * **Effort**: Low (1–2 hours).
* **Process Termination Leaves Orphaned Child Subprocesses on Windows**:
  * **File Reference**: [runner.py](file:///C:/Users/manue/Downloads/SuperAgent/superagent/runner.py#L233-L246) (`cancel_task`)
  * **Finding**: `cancel_task` calls `task._process.terminate()`. On Windows, CLI subagents (like `codex`, `claude`, or `agy`) run as wrapper scripts (Node.js or PowerShell wrappers). Terminating the root wrapper does not kill spawned child processes (compilers, git commands, python test suites), leaving orphaned background processes consuming CPU and holding file locks.
  * **Proposed Improvement**: Use Windows tree-kill (`taskkill /F /T /PID {pid}`) or `psutil.Process(pid).children(recursive=True)` before terminating the parent process.
  * **Impact**: Prevents background runaway processes and file lock contention during rapid task cancellation.
  * **Effort**: Low–Medium (2–3 hours).
* **Destructive Config Overwrite on JSON Parse Error**:
  * **File Reference**: [config.py](file:///C:/Users/manue/Downloads/SuperAgent/superagent/config.py#L70-L80) (`load`)
  * **Finding**: In `SuperagentConfig.load()`, if `config.json` contains invalid JSON or encounters a read error, the exception handler immediately resets `self._data = DEFAULT_CONFIG.copy()` and calls `self.save()`. This permanently deletes all user projects, API keys, and conversation pointers without creating a backup.
  * **Proposed Improvement**: On parse error, rename the corrupted file to `config.json.bak.<timestamp>` before writing defaults, and alert the user in the CLI/TUI.
  * **Impact**: Prevents catastrophic loss of user project configurations during sudden power loss or editor syntax errors.
  * **Effort**: Low (1 hour).
---
### 3. Onboarding & Zero-State Experience
* **Hardcoded Non-Existent Default Project on First Run**:
  * **File Reference**: [config.py](file:///C:/Users/manue/Downloads/SuperAgent/superagent/config.py#L10-L21) (`DEFAULT_CONFIG`), [cli.py](file:///C:/Users/manue/Downloads/SuperAgent/superagent/cli.py#L50-L61)
  * **Finding**: `DEFAULT_CONFIG` includes a hardcoded mock project `"betterilias"` with path `Path(__file__).resolve().parents[3]`. On any fresh machine, this path points to a non-existent directory. If a new user launches `superagent`, the banner shows `betterilias` pointing to invalid storage, and task execution fails with path errors.
  * **Proposed Improvement**: Replace static defaults with dynamic zero-state initialization: if no projects exist, automatically register the current working directory (`os.getcwd()`) with the folder name as the default project, or display an onboarding greeting wizard.
  * **Impact**: First-run experience works out of the box without requiring manual `superagent project add` configuration.
  * **Effort**: Low–Medium (2–3 hours).
* **Lack of Pre-Flight CLI & Tooling Diagnostics**:
  * **File Reference**: [brain.py](file:///C:/Users/manue/Downloads/SuperAgent/superagent/brain.py#L88-L115), [runner.py](file:///C:/Users/manue/Downloads/SuperAgent/superagent/runner.py#L17-L46)
  * **Finding**: SuperAgent assumes `codex`, `agy`, or `claude` are installed and authenticated. If a user does not have `codex` installed and hasn't set `GEMINI_API_KEY`, triage hangs for 25 seconds before timing out on `_run_planning_model`. There is no `doctor` or startup check to report which CLI agents are healthy.
  * **Proposed Improvement**: Add a `superagent doctor` CLI command and an in-app `:doctor` diagnostic panel that tests each CLI tool via `shutil.which()`, checks version strings, and verifies subscription/API credentials.
  * **Impact**: Drastically reduces user confusion during initial setup and self-diagnoses missing dependencies.
  * **Effort**: Medium (3–4 hours).
---
### 4. Documentation & Packaging Integrity
* **Undeclared Runtime Dependency in `pyproject.toml`**:
  * **File Reference**: [pyproject.toml](file:///C:/Users/manue/Downloads/SuperAgent/pyproject.toml#L12-L15), [history.py](file:///C:/Users/manue/Downloads/SuperAgent/superagent/history.py#L9), [brain.py](file:///C:/Users/manue/Downloads/SuperAgent/superagent/brain.py#L9)
  * **Finding**: `history.py` and `brain.py` import `pydantic` (`from pydantic import BaseModel, Field`). However, `pyproject.toml` only declares `textual>=0.80.0` and `rich>=13.0.0`. Installing SuperAgent via `pip install -e .` in a fresh virtualenv fails immediately with `ModuleNotFoundError: No module named 'pydantic'`.
  * **Proposed Improvement**: Add `"pydantic>=2.0.0"` to `dependencies` in `pyproject.toml`.
  * **Impact**: Essential packaging fix; prevents broken installations in clean environments.
  * **Effort**: Trivial (5 minutes).
* **Inaccurate Quickstart Paths and Undocumented Commands in README**:
  * **File Reference**: [README.md](file:///C:/Users/manue/Downloads/SuperAgent/README.md#L28), [README.md](file:///C:/Users/manue/Downloads/SuperAgent/README.md#L57-L61)
  * **Finding**:
    1. README line 28 specifies `cd tools/superagent`, but this project root is `C:\Users\manue\Downloads\SuperAgent`.
    2. README lists only 4 shortcuts (`Ctrl+P`, `Ctrl+K`, `Ctrl+L`, `Ctrl+Q`), omitting `Ctrl+H` (History), `Ctrl+Y` (Copy), and critical colon commands (`:new`, `:title`, `:agents`, `:key`, `:fr`).
  * **Proposed Improvement**: Update README.md with accurate installation paths, full shortcut tables, and comprehensive reference for colon commands.
  * **Impact**: Ensures accurate developer onboarding and prevents command discovery friction.
  * **Effort**: Low (1 hour).
---
## Combined, Deduplicated & Ranked Top 10 Improvements
Both agents' findings were merged, deduplicated, evaluated against user impact and operational risk, and ranked from highest to lowest priority:
| Rank | Improvement Area | Category | Impact | Estimated Effort | Key File References |
| :---: | :--- | :--- | :--- | :---: | :--- |
| **1** | **Packaging Integrity: Add missing `pydantic` dependency** | Packaging / Setup | **Critical** (Fixes `ModuleNotFoundError` on fresh `pip install`) | **15 mins** (Trivial) | [pyproject.toml#L12-L15](file:///C:/Users/manue/Downloads/SuperAgent/pyproject.toml#L12-L15)<br>[history.py#L9](file:///C:/Users/manue/Downloads/SuperAgent/superagent/history.py#L9)<br>[brain.py#L9](file:///C:/Users/manue/Downloads/SuperAgent/superagent/brain.py#L9) |
| **2** | **Crash Resilience: Guard `_process_user_task` against unhandled triage exceptions** | Error Recovery | **Critical** (Prevents permanent UI deadlock if Gemini or CLI planner fails) | **1–2 hours** (Low) | [app.py#L606-L616](file:///C:/Users/manue/Downloads/SuperAgent/superagent/tui/app.py#L606-L616)<br>[brain.py#L437-L440](file:///C:/Users/manue/Downloads/SuperAgent/superagent/brain.py#L437-L440) |
| **3** | **Input UX: Replace single-line `Input` with auto-expanding `TextArea`** | User Workflows | **High** (Enables multi-line prompts, code snippets, stack traces, and schemas) | **4–5 hours** (Medium) | [app.py#L129-L131](file:///C:/Users/manue/Downloads/SuperAgent/superagent/tui/app.py#L129-L131)<br>[app.py#L254-L257](file:///C:/Users/manue/Downloads/SuperAgent/superagent/tui/app.py#L254-L257)<br>[app.py#L577-L583](file:///C:/Users/manue/Downloads/SuperAgent/superagent/tui/app.py#L577-L583) |
| **4** | **Onboarding Zero-State: Auto-register CWD instead of hardcoded `betterilias`** | Onboarding | **High** (Out-of-the-box first run works without `FileNotFoundError`) | **2–3 hours** (Low) | [config.py#L10-L21](file:///C:/Users/manue/Downloads/SuperAgent/superagent/config.py#L10-L21)<br>[config.py#L172-L181](file:///C:/Users/manue/Downloads/SuperAgent/superagent/config.py#L172-L181)<br>[cli.py#L50-L61](file:///C:/Users/manue/Downloads/SuperAgent/superagent/cli.py#L50-L61) |
| **5** | **Process Lifecycle: Recursive process-tree termination on Windows** | Error Recovery | **High** (Eliminates orphaned node/powershell/python child tasks on cancel) | **2–3 hours** (Low) | [runner.py#L233-L246](file:///C:/Users/manue/Downloads/SuperAgent/superagent/runner.py#L233-L246) |
| **6** | **Console Performance: Chunk / paginate large log writes in `select_task`** | Console / UI | **Medium–High** (Eliminates event loop freezing when switching to 1000+ line tasks) | **3–4 hours** (Medium) | [app.py#L554-L576](file:///C:/Users/manue/Downloads/SuperAgent/superagent/tui/app.py#L554-L576)<br>[app.py#L518-L524](file:///C:/Users/manue/Downloads/SuperAgent/superagent/tui/app.py#L518-L524) |
| **7** | **Interactive Navigation: Visual Project Quick-Pick modal for `Ctrl+P`** | User Workflows | **Medium** (Replaces blind project cycling with an interactive selection dialog) | **3–4 hours** (Medium) | [app.py#L913-L926](file:///C:/Users/manue/Downloads/SuperAgent/superagent/tui/app.py#L913-L926) |
| **8** | **UI Responsiveness: Intelligent log auto-scroll pause & card keyboard focus** | Terminal UI / Console | **Medium** (Permits reading logs without snap-back; allows `Up`/`Down` card focus) | **3–4 hours** (Medium) | [app.py#L518-L524](file:///C:/Users/manue/Downloads/SuperAgent/superagent/tui/app.py#L518-L524)<br>[cards.py#L30-L78](file:///C:/Users/manue/Downloads/SuperAgent/superagent/tui/cards.py#L30-L78) |
| **9** | **Clipboard & Export: Multi-target clipboard sync (Feed synthesis & code blocks)** | Clipboard | **Medium** (Allows copying synthesis reports & diffs; fixes Linux/macOS clip fallbacks) | **2–3 hours** (Low) | [app.py#L167-L200](file:///C:/Users/manue/Downloads/SuperAgent/superagent/tui/app.py#L167-L200)<br>[README.md#L20](file:///C:/Users/manue/Downloads/SuperAgent/README.md#L20) |
| **10** | **Pre-Flight Diagnostics: `superagent doctor` & config corruption safety** | Onboarding / Docs | **Medium** (Detects missing CLI binaries upfront; backs up corrupted configs) | **3–4 hours** (Medium) | [cli.py#L30-L45](file:///C:/Users/manue/Downloads/SuperAgent/superagent/cli.py#L30-L45)<br>[config.py#L70-L80](file:///C:/Users/manue/Downloads/SuperAgent/superagent/config.py#L70-L80)<br>[README.md#L28-L61](file:///C:/Users/manue/Downloads/SuperAgent/README.md#L28-L61) |
---
## Detailed Specifications for Ranked Top 10 Improvements
### 1. Packaging Integrity: Add missing `pydantic` dependency
* **Problem**: Running `pip install -e .` in a pristine environment will install `textual` and `rich`, but runtime crashes immediately on `import superagent` because `pydantic` is missing from `pyproject.toml`.
* **Fix**: Edit [pyproject.toml](file:///C:/Users/manue/Downloads/SuperAgent/pyproject.toml#L12-L15) to include `"pydantic>=2.0.0"`.
### 2. Crash Resilience: Guard `_process_user_task` against unhandled triage exceptions
* **Problem**: When a user submits a prompt, `_process_user_task` is fired with `asyncio.create_task`. Any unhandled exception inside `self.brain.triage()` (e.g. timeout, network drop, JSON parse failure) terminates the task silently, leaving the user with an unresolvable `⏳ SuperAgent First Responder evaluating task...` state.
* **Fix**: In [app.py](file:///C:/Users/manue/Downloads/SuperAgent/superagent/tui/app.py#L608-L640), wrap the triage and delegation logic in a structured `try...except Exception as e:` block. If triage fails, automatically fall back to local heuristic execution, output a red diagnostic alert in `#superagent-feed`, and reset the input bar.
### 3. Input UX: Replace single-line `Input` with auto-expanding `TextArea`
* **Problem**: Complex coding prompts frequently include stack traces, code snippets, or numbered requirements. The current single-line `Input` widget strips newlines or submits prematurely.
* **Fix**: Replace `Input` in [app.py](file:///C:/Users/manue/Downloads/SuperAgent/superagent/tui/app.py#L254-L257) with a customized `TextArea`. Bind `Shift+Enter` to insert a newline and `Enter` to submit. Dynamically adjust height between 1 and 6 lines based on line count.
### 4. Onboarding Zero-State: Auto-register CWD instead of hardcoded `betterilias`
* **Problem**: New users running `superagent` for the first time see `betterilias` pointing to an invalid directory (`parents[3]`).
* **Fix**: In [config.py](file:///C:/Users/manue/Downloads/SuperAgent/superagent/config.py#L10-L21) and [cli.py](file:///C:/Users/manue/Downloads/SuperAgent/superagent/cli.py#L50-L56), if `config.json` is not found, dynamically initialize `projects` with the user's current directory `os.getcwd()` as the active project. If outside a git repository, display a clear welcoming banner guiding the user to run `:add <name> <path>`.
### 5. Process Lifecycle: Recursive process-tree termination on Windows
* **Problem**: Subagents launched through PowerShell or Node wrappers leave orphan child processes (compilers, git processes, test runners) running indefinitely when `cancel_task` is triggered.
* **Fix**: In [runner.py](file:///C:/Users/manue/Downloads/SuperAgent/superagent/runner.py#L233-L246), check `sys.platform == "win32"`. If true, execute `subprocess.run(["taskkill", "/F", "/T", "/PID", str(task._process.pid)])` prior to calling `terminate()`.
### 6. Console Performance: Chunk / paginate large log writes in `select_task`
* **Problem**: Switching between subagent cards synchronously loops over `task.log_lines` and issues individual `write()` calls, freezing the Textual UI loop when logs reach several thousand lines.
* **Fix**: In [app.py](file:///C:/Users/manue/Downloads/SuperAgent/superagent/tui/app.py#L560-L576), batch lines into chunks of 100 or write joined text blocks (`log_widget.write("\n".join(chunk))`). Restrict initial render to the tail 400 lines with a button or key (`Ctrl+A`) to load earlier lines.
### 7. Interactive Navigation: Visual Project Quick-Pick modal for `Ctrl+P`
* **Problem**: `Ctrl+P` immediately switches to `projects[(curr_idx + 1) % len(projects)]` blind, forcing the user to repeatedly cycle through every project to find the desired one.
* **Fix**: In [app.py](file:///C:/Users/manue/Downloads/SuperAgent/superagent/tui/app.py#L913-L926), bind `Ctrl+P` to display a Textual `ModalScreen` containing an `OptionList` with registered projects, their active branch, and their paths, with fuzzy keyboard search.
### 8. UI Responsiveness: Intelligent log auto-scroll pause & card keyboard focus
* **Problem**: Scrolling up in the console during execution is thwarted by incoming logs forcing the viewport to the bottom. Additionally, cards cannot be navigated without mouse clicks.
* **Fix**: In [app.py](file:///C:/Users/manue/Downloads/SuperAgent/superagent/tui/app.py#L518-L524), detect if `scroll_offset < max_scroll`. If user is scrolled up, do not force auto-scroll on new log entries. In [app.py](file:///C:/Users/manue/Downloads/SuperAgent/superagent/tui/app.py#L158-L165), add `Alt+J` / `Alt+K` (or `Alt+Down` / `Alt+Up`) bindings to cycle active card selection from the keyboard.
### 9. Clipboard & Export: Multi-target clipboard sync
* **Problem**: `copy_to_clipboard` only extracts the raw console log of the active subagent. Coordinator synthesis reports, direct replies, and diff summaries cannot be copied quickly.
* **Fix**: Add a command `:copy` (with options `:copy log`, `:copy synthesis`, `:copy diff`) and shortcut `Ctrl+Shift+Y` to copy the latest synthesis report to the system clipboard.
### 10. Pre-Flight Diagnostics: `superagent doctor` & config corruption safety
* **Problem**: Users have no built-in way to verify which agent CLIs (`claude`, `codex`, `agy`) or API keys are configured and functional. Moreover, invalid JSON in `config.json` wipes user settings immediately.
* **Fix**:
  1. Add a `superagent doctor` command in [cli.py](file:///C:/Users/manue/Downloads/SuperAgent/superagent/cli.py#L17-L45) checking CLI binaries in PATH and API keys.
  2. In [config.py](file:///C:/Users/manue/Downloads/SuperAgent/superagent/config.py#L70-L80), back up corrupt configs to `config.json.bak` instead of silently wiping them.
  3. Align [README.md](file:///C:/Users/manue/Downloads/SuperAgent/README.md) with actual project paths and colon commands.