"""Bounded Ollama requests to a loopback address only; never use system proxies."""
from __future__ import annotations
import ipaddress
import errno
import json
import socket
import threading
import time
from urllib import error, parse, request

LOCK = threading.Lock()
LOCK_PORT = 47938
DEFAULT_MODEL = 'qwen3:14b-q4_K_M'
DEFAULT_ENDPOINT = 'http://127.0.0.1:11434'


def endpoint(value: str) -> str:
    url = parse.urlsplit(value)
    try:
        local = url.hostname == 'localhost' or ipaddress.ip_address(url.hostname or '').is_loopback
    except ValueError:
        local = False
    if url.scheme != 'http' or not local or url.username or url.password or url.path not in ('', '/') or url.query or url.fragment:
        raise ValueError('Ollama endpoint must be a loopback HTTP origin, such as http://127.0.0.1:11434.')
    return value.rstrip('/')


def call(path: str, *, body: dict | None = None, base: str = DEFAULT_ENDPOINT, timeout: int = 600) -> dict:
    if path not in ('/api/tags', '/api/chat'):
        raise ValueError('Unsupported Ollama operation')
    payload = json.dumps(body).encode() if body is not None else None
    req = request.Request(endpoint(base) + path, data=payload, headers={'Content-Type': 'application/json'})
    try:
        with LOCK:
            guard = None
            if path == '/api/chat':
                # A loopback socket provides an inter-process lock without writing files.
                deadline = time.monotonic() + timeout
                while True:
                    guard = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    try:
                        guard.bind(('127.0.0.1', LOCK_PORT))
                        break
                    except OSError as exc:
                        guard.close()
                        if exc.errno != errno.EADDRINUSE:
                            raise
                        if time.monotonic() >= deadline:
                            raise TimeoutError('Another local inference request did not finish in time.')
                        time.sleep(0.2)
            try:
                with request.build_opener(request.ProxyHandler({})).open(req, timeout=timeout) as response:
                    result = json.load(response)
            finally:
                if guard is not None:
                    guard.close()
    except error.HTTPError as exc:
        raise ValueError(f'Ollama returned HTTP {exc.code}. Check the model and server.') from exc
    if not isinstance(result, dict):
        raise ValueError('Ollama returned invalid response.')
    return result


def local_models(base: str = DEFAULT_ENDPOINT) -> set[str]:
    data = call('/api/tags', base=base, timeout=5)
    return {item['name'] for item in data.get('models', []) if isinstance(item, dict)
            and isinstance(item.get('name'), str) and ':cloud' not in item['name'].lower()
            and not item.get('remote_model') and not item.get('remote_host')}
