"""One command policy for workers, planning and synthesis."""
from pathlib import Path

PROFILES = ('read-only', 'workspace-write', 'unrestricted')


def validate_project_path(path: str) -> str:
    if not path or not str(path).strip():
        raise ValueError('A project directory is required.')
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_dir():
        raise ValueError(f'Project directory does not exist: {resolved}')
    return str(resolved)


def agent_command(agent: str, prompt: str, cwd: str, effort: str = 'medium',
                  profile: str = 'workspace-write') -> list[str]:
    if profile not in PROFILES:
        raise ValueError(f'Unknown execution profile: {profile}')
    if effort not in ('low', 'medium', 'high'):
        raise ValueError(f'Unknown reasoning effort: {effort}')
    agent = agent.lower()
    if agent == 'antigravity':
        agent = 'agy'
    if agent == 'codex':
        command = ['codex', '-a', 'never', 'exec', '--skip-git-repo-check']
        if profile == 'unrestricted':
            command += ['--dangerously-bypass-approvals-and-sandbox']
        else:
            command += ['--sandbox', profile]
        return command + ['-c', f'model_reasoning_effort="{effort}"', '-C', cwd, prompt]
    if agent == 'agy':
        flags = (['--dangerously-skip-permissions'] if profile == 'unrestricted'
                 else ['--sandbox', '--mode', 'plan' if profile == 'read-only' else 'accept-edits'])
        return ['agy', *flags, '--effort', effort, '--output-format', 'stream-json', '-p', prompt]
    if agent == 'claude':
        flags = (['--dangerously-skip-permissions'] if profile == 'unrestricted'
                 else ['--permission-mode', 'plan' if profile == 'read-only' else 'acceptEdits'])
        return ['claude', *flags, '-p', prompt]
    raise ValueError(f'Unknown agent: {agent}')
