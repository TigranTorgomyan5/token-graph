"""Runtime boundary for bundled analysis: no external network or helper agents."""
from __future__ import annotations
import os
from pathlib import Path
import sys

_INSTALLED = False


def install(allow_ollama: bool = False) -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    os.environ['TOKEN_GRAPH_OFFLINE'] = '1'
    os.environ['TOKEN_GRAPH_ALLOW_OLLAMA'] = '1' if allow_ollama else '0'
    scripts = Path(__file__).resolve().parent
    vendor = scripts.parent / 'vendor'
    os.environ['PYTHONPATH'] = os.pathsep.join([str(scripts), str(vendor)])
    os.environ['PYTHONHASHSEED'] = '0'
    os.environ['GRAPHIFY_QUERY_LOG_DISABLE'] = '1'
    for key in list(os.environ):
        upper = key.upper()
        if upper.endswith(('_API_KEY', '_API_BASE', '_BASE_URL')) or '_PROXY' in upper or upper in {'ANTHROPIC_AUTH_TOKEN', 'GRAPHIFY_OUT'}:
            os.environ.pop(key, None)
    if allow_ollama:
        os.environ.update(OLLAMA_HOST='http://127.0.0.1:11434', OLLAMA_BASE_URL='http://127.0.0.1:11434/v1', OLLAMA_API_KEY='token-graph-local')
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'

    def audit(event: str, args: tuple) -> None:
        if event == 'socket.getaddrinfo':
            host, port = args[:2]
            if not (allow_ollama and host in {'127.0.0.1', b'127.0.0.1', 'localhost', b'localhost', '::1'} and str(port) == '11434'):
                raise PermissionError('Token Graph offline mode blocks external DNS/network access')
        elif event in {'socket.connect', 'socket.connect_ex', 'socket.sendto'}:
            address = args[-1]
            # Local Unix sockets support Python multiprocessing; IP sockets are restricted.
            if isinstance(address, tuple):
                if not (allow_ollama and address[0] in {'127.0.0.1', '::1'} and address[1] == 11434):
                    raise PermissionError('Token Graph offline mode blocks external network access')
            elif not isinstance(address, (str, bytes)):
                raise PermissionError('Token Graph offline mode blocks this socket type')
        elif event == 'socket.bind':
            address = args[1]
            if isinstance(address, tuple) and address[0] not in {'127.0.0.1', '::1'}:
                raise PermissionError('Token Graph permits local loopback listeners only')
        elif event in {'os.system', 'os.posix_spawn', 'os.exec'}:
            raise PermissionError('Token Graph offline mode blocks shell execution')
        elif event == 'subprocess.Popen':
            executable, argv, cwd, env = args
            # Windows audits the joined command line and executable=None; split it back.
            if isinstance(argv, (str, bytes)):
                import shlex
                argv = [word.strip('"') for word in shlex.split(os.fsdecode(argv), posix=False)]
            argv = list(argv)
            if executable is None and argv:
                executable = argv[0]
            if executable is None:
                raise PermissionError('Token Graph blocks external helper programs in offline mode')
            name = Path(os.fsdecode(executable)).name
            if name.lower() in {'git', 'git.exe'}:
                words = list(argv)[1:]
                while words and words[0] in {'-C', '--git-dir', '--work-tree'}:
                    words = words[2:]
                if words and words[0] in {'rev-parse', 'ls-files', 'check-ignore', 'diff', 'log', 'show', 'status', 'ls-tree', '--version'}:
                    return
                raise PermissionError('Token Graph permits read-only local Git commands only')
            # Workers inherit the offline policy through sitecustomize.py.
            if Path(os.fsdecode(executable)).resolve() == Path(sys.executable).resolve():
                effective = env if env is not None else os.environ
                if any(flag in {'-S', '-I', '-E'} for flag in list(argv)[1:]):
                    raise PermissionError('Token Graph workers must inherit the offline policy')
                if effective.get('TOKEN_GRAPH_OFFLINE') == '1' and str(scripts) in effective.get('PYTHONPATH', '').split(os.pathsep):
                    return
            raise PermissionError('Token Graph blocks external helper programs in offline mode')

    sys.addaudithook(audit)
