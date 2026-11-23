from __future__ import annotations
import argparse
import asyncio
import os
import sys
from pathlib import Path

from superagent.config import SuperagentConfig
from superagent.runner import SubagentRunner

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="superagent",
        description="SuperAgent: Coordinate Claude Code, Codex, and Antigravity across multiple projects."
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Subcommand to execute")

    # tui subcommand
    tui_parser = subparsers.add_parser("tui", help="Launch interactive Terminal UI dashboard")
    tui_parser.add_argument("project", nargs="?", help="Project name to focus on initially")

    # run subcommand (headless)
    run_parser = subparsers.add_parser("run", help="Run a task non-interactively via a subagent")
    run_parser.add_argument("agent", choices=["claude", "codex", "agy", "antigravity"], help="Agent to delegate to")
    run_parser.add_argument("task", help="Instruction or task description")
    run_parser.add_argument("--project", "-p", help="Target project name (defaults to active project)")

    # project subcommand
    project_parser = subparsers.add_parser("project", help="Manage projects")
    proj_sub = project_parser.add_subparsers(dest="project_action")
    
    proj_sub.add_parser("list", help="List registered projects")
    
    add_parser = proj_sub.add_parser("add", help="Register a new project")
    add_parser.add_argument("name", help="Friendly name / alias for the project")
    add_parser.add_argument("path", help="Filesystem path to the project repository")
    add_parser.add_argument("--agent", default="codex", help="Default agent for this project")
    add_parser.add_argument("--test", default="", help="Test command to run (e.g. 'pytest tests/')")

    remove_parser = proj_sub.add_parser("remove", help="Remove a project from registry")
    remove_parser.add_argument("name", help="Name of project to remove")

    args = parser.parse_args()

    # If no subcommand provided, launch TUI by default
    if not args.subcommand or args.subcommand == "tui":
        target_project = getattr(args, "project", None)
        # Check if current directory matches any project
        cfg = SuperagentConfig()
        if not target_project:
            detected = cfg.detect_project(os.getcwd())
            if detected:
                target_project = detected.name

        from superagent.tui.app import SuperagentApp
        app = SuperagentApp(initial_project=target_project)
        app.run()
        return

    if args.subcommand == "project":
        cfg = SuperagentConfig()
        if args.project_action == "list" or not args.project_action:
            projects = cfg.list_projects()
            print("\nRegistered Projects in ~/.superagent/config.json:")
            for p in projects:
                is_active = "*" if cfg.active_project and cfg.active_project.name == p.name else " "
                print(f" {is_active} {p.name:<18} -> {p.path} [agent: {p.default_agent}]")
            print()
        elif args.project_action == "add":
            proj = cfg.add_project(args.name, args.path, test_cmd=args.test, default_agent=args.agent)
            print(f"Registered project '{proj.name}' -> {proj.path}")
        elif args.project_action == "remove":
            if cfg.remove_project(args.name):
                print(f"Removed project '{args.name}'.")
            else:
                print(f"Project '{args.name}' not found.")
        return

    if args.subcommand == "run":
        cfg = SuperagentConfig()
        proj_name = args.project or (cfg.active_project.name if cfg.active_project else None)
        if not proj_name:
            print("Error: No active project found. Use 'superagent project add <name> <path>' first.", file=sys.stderr)
            sys.exit(1)
        
        proj = cfg.get_project(proj_name)
        if not proj:
            print(f"Error: Project '{proj_name}' not found.", file=sys.stderr)
            sys.exit(1)

        print(f"➜ Superagent delegating to {args.agent.upper()} in {proj.name} ({proj.path})...")
        runner = SubagentRunner()
        task = runner.create_task(
            agent=args.agent,
            task_desc=args.task,
            project_name=proj.name,
            cwd=proj.path
        )

        def print_log(t, event):
            if event == "log" and t.log_lines:
                print(t.log_lines[-1])

        runner.subscribe(print_log)
        asyncio.run(runner.execute_task(task))
        print(f"\nTask completed with status: {task.status} (exit code: {task.returncode}) in {task.elapsed_formatted}")

if __name__ == "__main__":
    main()
