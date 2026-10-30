#!/usr/bin/env python3
"""Poll local source changes and rebuild with the bundled Token Graph launcher."""
from pathlib import Path
import os
import signal
import subprocess
import sys
import time

scripts = Path(__file__).resolve().parent
sys.path.insert(0, str(scripts.parent / 'vendor'))
from graphify.detect import detect
from token_graph import clean_env


def snapshot(root: Path) -> dict:
    result = {}
    for raw in detect(root, google_workspace=False).get('files', {}).get('code', []):
        path = Path(raw)
        try:
            info = path.stat()
            result[str(path)] = (info.st_mtime_ns, info.st_size)
        except FileNotFoundError:
            pass
    return result


def main() -> None:
    root = Path(sys.argv[1]).resolve()
    stopped = False

    def stop(*_):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    previous = snapshot(root)
    print(f'Token Graph watching {root}', flush=True)
    while not stopped:
        time.sleep(1)
        current = snapshot(root)
        if current != previous:
            completed = subprocess.run([sys.executable, str(scripts / 'token_graph.py'), 'update', str(root)], env=clean_env(), check=False)
            if completed.returncode == 0:
                previous = current
            else:
                print('Token Graph update failed; retrying on the next source change.', flush=True)
                previous = current
    print('Token Graph watcher stopped.', flush=True)


if __name__ == '__main__':
    main()
