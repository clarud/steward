from pathlib import Path
import subprocess
import sys

import pytest

from steward.runtime import RuntimeAlreadyRunningError, telegram_runtime_lock


def test_runtime_lock_rejects_duplicates_and_releases_after_exit(tmp_path: Path) -> None:
    with telegram_runtime_lock(tmp_path):
        with pytest.raises(RuntimeAlreadyRunningError):
            with telegram_runtime_lock(tmp_path):
                pytest.fail("duplicate runtime acquired the lock")
    with telegram_runtime_lock(tmp_path):
        pass


def test_runtime_lock_is_released_after_abrupt_process_exit(tmp_path: Path) -> None:
    # os._exit bypasses context-manager cleanup, simulating a crashed process.
    script = (
        "import os,sys; from pathlib import Path; "
        "from steward.runtime import telegram_runtime_lock; "
        "guard=telegram_runtime_lock(Path(sys.argv[1])); guard.__enter__(); os._exit(17)"
    )
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], timeout=15)
    assert result.returncode == 17
    with telegram_runtime_lock(tmp_path):
        pass
