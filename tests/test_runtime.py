from pathlib import Path
import subprocess
import sys
import queue
import threading
import time

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


def test_live_process_excludes_contender_until_owner_is_killed(tmp_path: Path) -> None:
    script = (
        "import sys; from pathlib import Path; "
        "from steward.runtime import telegram_runtime_lock; "
        "guard=telegram_runtime_lock(Path(sys.argv[1])); guard.__enter__(); "
        "print('LOCKED', flush=True); sys.stdin.read()"
    )
    owner_dir = tmp_path / "owner"
    owner = subprocess.Popen(
        [sys.executable, "-c", script, str(owner_dir)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    ready = queue.Queue()
    reader = threading.Thread(target=lambda: ready.put(owner.stdout.readline()), daemon=True)
    reader.start()
    try:
        # Bounded readiness handshake: process existence alone is not proof of ownership.
        assert ready.get(timeout=15).strip() == "LOCKED"
        assert owner.poll() is None
        contender = (
            "import sys; from pathlib import Path; "
            "from steward.runtime import telegram_runtime_lock, RuntimeAlreadyRunningError\n"
            "try:\n"
            " with telegram_runtime_lock(Path(sys.argv[1])): pass\n"
            "except RuntimeAlreadyRunningError:\n"
            " sys.exit(23)\n"
        )
        result = subprocess.run([sys.executable, "-c", contender, str(owner_dir)],
                                capture_output=True, text=True, timeout=15)
        assert result.returncode == 23, result.stderr
        assert owner.poll() is None
        with telegram_runtime_lock(tmp_path / "independent"):
            pass
        owner.kill()
        owner.wait(timeout=15)
        with telegram_runtime_lock(owner_dir):
            pass
        assert (owner_dir / "telegram-runtime.db").is_file()
    finally:
        if owner.poll() is None:
            owner.kill()
        owner.communicate(timeout=15)
        reader.join(timeout=2)


def test_runtime_lock_waits_briefly_for_an_abrupt_owner_lock_release(tmp_path: Path) -> None:
    """The bounded grace period makes crash recovery reliable on Windows."""

    script = (
        "import sys; from pathlib import Path; "
        "from steward.runtime import telegram_runtime_lock; "
        "guard=telegram_runtime_lock(Path(sys.argv[1])); guard.__enter__(); "
        "print('LOCKED', flush=True); sys.stdin.read()"
    )
    owner = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert owner.stdout.readline().strip() == "LOCKED"
        owner.kill()
        owner.wait(timeout=15)
        started = time.monotonic()
        with telegram_runtime_lock(tmp_path):
            pass
        assert time.monotonic() - started < 2
    finally:
        if owner.poll() is None:
            owner.kill()
        owner.communicate(timeout=15)
