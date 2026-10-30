#!/usr/bin/env python3
"""Package distributable skill sources without changing viewers or shipping local state."""
from pathlib import Path
import zipfile

FILES = {'SKILL.md', 'README.md', 'INSTALL.md', 'INSTALL-WINDOWS.md', 'GUIDE.md',
         'create-shortcut.ps1', 'requirements.txt', 'requirements.lock'}
DIRECTORIES = {'scripts', 'assets', 'vendor', 'extras', 'agents'}


def package_skill(root: Path, destination: Path | None = None) -> Path:
    root = root.resolve()
    destination = destination or root.parent / 'token-graph.zip'
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob('*')):
            relative = path.relative_to(root)
            if relative.parts[0] not in FILES | DIRECTORIES:
                continue
            if not path.is_file() or path.is_symlink() or any(part.startswith('.') or part == '__pycache__' for part in relative.parts) or path.suffix in {'.pyc', '.pyo'}:
                continue
            archive.write(path, (Path('token-graph') / relative).as_posix())
    return destination


if __name__ == '__main__':
    output = package_skill(Path(__file__).resolve().parents[1])
    print(f'Packaged {output} ({output.stat().st_size:,} bytes)')
