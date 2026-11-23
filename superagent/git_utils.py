from __future__ import annotations
import subprocess
from pathlib import Path
from typing import Optional, Tuple

class GitManager:
    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path).resolve()

    def run_git(self, *args: str) -> Tuple[int, str]:
        try:
            res = subprocess.run(
                ["git", *args],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=False
            )
            out = res.stdout if res.returncode == 0 else (res.stderr or res.stdout)
            return res.returncode, out.strip()
        except Exception as e:
            return -1, str(e)

    def is_git_repo(self) -> bool:
        code, _ = self.run_git("rev-parse", "--is-inside-work-tree")
        return code == 0

    def current_branch(self) -> str:
        code, out = self.run_git("branch", "--show-current")
        return out if code == 0 else "main"

    def create_worktree(self, branch_name: str, worktree_dir: str) -> Tuple[bool, str]:
        wt_path = Path(worktree_dir).resolve()
        wt_path.parent.mkdir(parents=True, exist_ok=True)
        # Try creating worktree with new branch or checking out
        code, out = self.run_git("worktree", "add", "-b", branch_name, str(wt_path))
        if code != 0:
            # Maybe branch already exists
            code, out = self.run_git("worktree", "add", str(wt_path), branch_name)
        return code == 0, out

    def remove_worktree(self, worktree_dir: str, force: bool = False) -> Tuple[bool, str]:
        wt_path = Path(worktree_dir).resolve()
        args = ["worktree", "remove", str(wt_path)]
        if force:
            args.append("--force")
        code, out = self.run_git(*args)
        return code == 0, out

    def get_diff_summary(self, target_branch: Optional[str] = None) -> str:
        base = target_branch or "main"
        code, out = self.run_git("diff", "--stat", base)
        return out if code == 0 else ""

    def get_status_short(self) -> str:
        code, out = self.run_git("status", "--short")
        return out if code == 0 else ""

    def get_uncommitted_diff_stat(self) -> str:
        code, out = self.run_git("diff", "--stat", "HEAD")
        if code != 0 or not out:
            code, out = self.run_git("diff", "--stat")
        return out if code == 0 else ""
