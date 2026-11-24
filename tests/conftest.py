"""Default tests never launch subscription agents or access user config/history."""
import asyncio
from pathlib import Path
import sys

import pytest


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    import superagent.config as config
    import superagent.brain as brain
    from superagent.history import HistoryManager

    monkeypatch.setattr(config, 'CONFIG_DIR', tmp_path / 'config')
    monkeypatch.setattr(config, 'CONFIG_FILE', tmp_path / 'config/config.json')
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    monkeypatch.delenv('GOOGLE_API_KEY', raising=False)
    monkeypatch.setattr(brain, 'HAVE_GEMINI', False)
    original_init = HistoryManager.__init__

    def history_init(self, base_dir=None):
        original_init(self, base_dir=base_dir if base_dir is not None else tmp_path / 'history')
    monkeypatch.setattr(HistoryManager, '__init__', history_init)

    original_spawn = asyncio.create_subprocess_exec

    async def guarded_spawn(program, *args, **kwargs):
        if Path(program).resolve() != Path(sys.executable).resolve():
            raise RuntimeError(f'External agent subprocess forbidden in tests: {program}')
        return await original_spawn(program, *args, **kwargs)
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', guarded_spawn)
