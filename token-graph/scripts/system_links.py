#!/usr/bin/env python3
"""Systems: several service folders and how they talk to each other.

A system is a named list of service folders (any language, anywhere on disk). Each
service's source and config text is scanned for communication endpoints - NATS
subjects, Kafka topics, RabbitMQ exchanges/queues, Redis/MQTT/SQS/Service Bus
channels, MassTransit/NServiceBus message types, gRPC services, HTTP routes, HTTP
client calls and service URLs - and a producer/caller in one service is linked to
the consumer/server in another.

Detection is deterministic pattern matching over text (no parser, network or model),
so it works for any language. A constant used as a subject or topic is resolved from
simple `NAME = "value"` assignments in the same service. Links are evidence with file
and line, not proof: verify them in the code.

  python system_links.py scan <service-dir> <service-dir> ... [--json]
  python system_links.py discover <parent-dir>
  python system_links.py impact [files...] [--base REF]   cross-service effect of the current git changes
  python system_links.py hook                              Claude Code PostToolUse hook (reads the event on stdin)
  python system_links.py install-hook | uninstall-hook     add/remove that hook in ~/.claude/settings.json

Systems are saved per user in ~/.token-graph/systems.json, so every copy of the skill (the
Explorer server, ~/.claude/skills/token-graph, ~/.codex/skills/token-graph) sees the same ones.
"""
from __future__ import annotations

import bisect
import copy
import fnmatch
import json
import os
import re
import subprocess
import sys
import time
import uuid
from collections import defaultdict
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
HOME_DIR = Path(os.environ.get("TOKEN_GRAPH_HOME") or Path.home() / ".token-graph")
SYSTEMS = HOME_DIR / "systems.json"
LEGACY_SYSTEMS = SKILL / ".explorer-systems.json"   # before systems were shared per user
CACHE_DIR = HOME_DIR / "system-cache"               # last scan per system, for the fast edit hook
_self = Path(__file__).stat()
SCANNER_VERSION = f"{_self.st_size}:{int(_self.st_mtime)}"   # a changed scanner invalidates cached scans
MAX_SERVICES = 40
MAX_FILES = 40_000
MAX_FILE_BYTES = 1_000_000
MAX_EVIDENCE = 8

SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "bin", "obj", "dist", "build", "out", "target", "vendor",
             ".venv", "venv", "env", "__pycache__", "graphify-out", ".idea", ".vs", ".vscode", "packages",
             "coverage", ".next", ".nuxt", ".runtime", ".gradle", ".terraform", "wwwroot", "TestResults"}
SOURCE_EXT = {".cs", ".fs", ".vb", ".go", ".java", ".kt", ".kts", ".scala", ".groovy", ".py", ".ts", ".tsx", ".js",
              ".jsx", ".mjs", ".cjs", ".rb", ".php", ".rs", ".swift", ".c", ".cc", ".cpp", ".h", ".hpp", ".ex",
              ".exs", ".erl", ".dart", ".lua", ".proto", ".cshtml", ".razor", ".vue", ".svelte"}
CONFIG_EXT = {".json", ".yaml", ".yml", ".toml", ".ini", ".env", ".properties", ".config", ".conf"}
CONFIG_NAME = re.compile(r"^(dockerfile.*|docker-compose.*|compose.*|\.env.*)$", re.I)
TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|specs?|testing|fixtures?|mocks?)/|(^|/)[^/]*[.-]tests?/|"
                       r"[._-](test|tests|spec)\.[a-z]+$|Tests?\.(cs|java|kt)$|_test\.go$|(^|/)test_[^/]*\.py$", re.I)
GENERATED = re.compile(r"\.pb\.go$|_pb2(_grpc)?\.py$|\.g\.cs$|\.designer\.cs$|Grpc\.cs$|\.pb\.(cc|h)$|_pb\.[jt]s$|"
                       r"_grpc_pb\.[jt]s$|\.min\.js$|(^|/)package-lock\.json$|(^|/)[^/]*\.lock\.json$", re.I)
PROJECT_MARKERS = re.compile(r"\.(csproj|fsproj|vbproj|sln)$|^(go\.mod|package\.json|pom\.xml|build\.gradle(\.kts)?|"
                             r"pyproject\.toml|requirements\.txt|setup\.py|Cargo\.toml|Gemfile|composer\.json|mix\.exs|"
                             r"Dockerfile|pubspec\.yaml)$", re.I)

# ---- what a file talks through (its markers gate the rules that can fire in it) ----------------
MARKERS = {
    "nats": re.compile(r"(?i)(?<![a-z])nats|inats"),
    "kafka": re.compile(r"(?i)kafka|confluent"),
    "rabbitmq": re.compile(r"(?i)rabbit|amqp|basic_?publish|basic_?consume|\bpika\b"),
    "redis": re.compile(r"(?i)redis"),
    "mqtt": re.compile(r"(?i)mqtt"),
    "sqs": re.compile(r"(?i)(?<![a-z])sqs(?![a-z])|(?<![a-z])sns(?![a-z])|amazonsqs"),
    "servicebus": re.compile(r"(?i)servicebus"),
    "pubsub": re.compile(r"(?i)cloud[./]pubsub|cloud-pubsub|google\.pubsub"),
    "zeromq": re.compile(r"(?i)zeromq|\bzmq\b|netmq"),
    "bus": re.compile(r"MassTransit|NServiceBus|Rebus|IConsumer<|IHandleMessages<"),
    "grpc": re.compile(r"(?i)grpc"),
}
BROKERS = ["nats", "kafka", "rabbitmq", "redis", "mqtt", "sqs", "servicebus", "pubsub", "zeromq"]
LISTENER_TECH = {"rabbit": "rabbitmq", "kafka": "kafka", "jms": "jms", "sqs": "sqs", "nats": "nats"}

STR = r"""[@$fFrRbB]{0,3}(?:"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|`[^`\n]*`)"""
IDENT = r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*(?!\s*[(\w])"
CONST_CALL = r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\.[A-Z][A-Z0-9_]+\s*\([^()\n]{0,80}\)"   # Subjects.PLACE_BET("Adastra")


def arg(name: str = "arg") -> str:
    return rf"(?P<{name}>{STR}|{CONST_CALL}|{IDENT})"


GEN = r"\s*(?:<[^<>()]*(?:<[^<>()]*>[^<>()]*)*>)?\s*\(\s*"          # optional <T> then "("
OPEN = (r"(?:\{\s*)?(?:[A-Za-z_]\w*\s*[:=]\s*)?(?:\[\s*|new\s*[\w.]*\s*(?:<[^<>]*>)?\s*(?:\[\s*\])?\s*\{\s*|"
        r"(?:List|Set|Arrays)\s*\.\s*(?:of|asList)\s*\(\s*|listOf\s*\(\s*|\[\]string\s*\{\s*|"
        r"(?:\w+\s*\.\s*)?(?:Literal|Pattern)\s*\(\s*)?")                  # RedisChannel.Literal(name)
OUT_VERBS = r"(?:publish|request|produce|sendtoqueue|send_to_queue|xadd|lpush|rpush)(?:async|core|coreasync|many|manyasync|_async)?"
IN_VERBS = (r"(?:subscribe|queuesubscribe|queue_subscribe|psubscribe|consume|basicconsume|basic_consume|"
            r"createconsumer|addconsumer|xreadgroup|blpop|brpop)(?:async|core|coreasync|_async)?")
PATH_LIT = r"""(?P<arg>[@$]{0,2}["'`](?:(?:GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+)?/[^"'`\n]*["'`])"""

# (tech, role, regex, gate). tech "broker" = the file's message broker; role "ctx" = decided by nearby words.
RULES: list[tuple[str, str, re.Pattern, str | None]] = [
    ("broker", "out", re.compile(rf"(?i)(?<![\w]){OUT_VERBS}{GEN}{OPEN}{arg()}"), "broker"),
    ("broker", "in", re.compile(rf"(?i)(?<![\w]){IN_VERBS}{GEN}{OPEN}{arg()}"), "broker"),
    ("broker", "ctx", re.compile(rf"(?i)(?<![\w.])(?:topics?|subjects?|queue|queuename|queue_name|exchange|exchangename|exchange_name|stream)\s*[:=]\s*(?:\[\]string\s*\{{\s*|\[\s*)?{arg()}"), "broker"),
    ("broker", "out", re.compile(rf"(?i)producer\w*\s*\.\s*send\w*{GEN}{OPEN}{arg()}"), "broker"),
    # subscriber/publisher base classes: `protected override string Subject => Subjects.PLACE_BET;`
    ("broker", "class", re.compile(rf"\b(?:string|String|str)\s+(?:Subject|Topic|TopicName|Queue|QueueName|Exchange|ExchangeName|RoutingKey|Channel|ChannelName)\s*(?:=>|\{{\s*get\s*(?:=>|\{{\s*return))\s*{arg()}"), "broker"),
    ("kafka", "out", re.compile(rf"(?i)template\s*\.\s*send(?:default)?\w*{GEN}{arg()}"), "kafka"),
    ("kafka", "out", re.compile(rf"new\s+ProducerRecord\s*(?:<[^<>]*>)?\s*\(\s*{arg()}"), "kafka"),
    ("kafka", "in", re.compile(rf"KafkaConsumer\s*\(\s*{arg()}"), "kafka"),
    ("rabbitmq", "out", re.compile(rf"(?i)basic_?publish(?:async)?{GEN}(?:exchange\s*[:=]\s*)?{arg()}\s*,\s*(?:routing_?key\s*[:=]\s*)?{arg('arg2')}"), "rabbitmq"),
    ("rabbitmq", "in", re.compile(rf"(?i)(?:queue_?bind|bindqueue)(?:async)?{GEN}(?:queue\s*[:=]\s*)?(?:{STR}|{IDENT})\s*,\s*(?:exchange\s*[:=]\s*)?{arg()}"), "rabbitmq"),
    ("rabbitmq", "out", re.compile(rf"(?i)convertandsend{GEN}{arg()}"), "rabbitmq"),
    ("listener", "in", re.compile(rf"@(?P<t>Rabbit|Kafka|Jms|Sqs|Nats)Listener\s*\(\s*(?:\w+\s*=\s*)?\{{?\s*{arg()}"), None),
    ("masstransit", "in", re.compile(r"\b(?:IConsumer|IHandleMessages|IJobConsumer)\s*<\s*(?P<type>[\w.]+)\s*>"), "bus"),
    ("masstransit", "out", re.compile(r"\b(?:Publish|Send|SchedulePublish|ScheduleSend|GetResponse)(?:Async)?\s*<\s*(?P<type>[\w.]+)\s*>"), "bus"),
    ("masstransit", "out", re.compile(r"\b(?:Publish|Send)(?:Async)?\s*\(\s*new\s+(?P<type>[\w.]+)"), "bus"),
    ("masstransit", "out", re.compile(r"\bIRequestClient\s*<\s*(?P<type>[\w.]+)\s*>"), "bus"),
    ("grpc", "def", re.compile(r"(?m)^\s*service\s+(?P<type>\w+)\s*\{"), "grpc"),
    ("grpc", "in", re.compile(r"\b(?P<type>\w+)\.(?P=type)Base\b"), "grpc"),
    ("grpc", "in", re.compile(r"(?<!func )\bRegister(?P<type>\w+)Server\s*\("), "grpc"),
    ("grpc", "in", re.compile(r"\b(?P<type>\w+)Grpc\.(?P=type)ImplBase\b"), "grpc"),
    ("grpc", "in", re.compile(r"\badd_(?P<type>\w+)Servicer_to_server\b"), "grpc"),
    ("grpc", "in", re.compile(r"\.addService\s*\(\s*[\w.]*?(?P<type>\w+)\.service\b"), "grpc"),
    ("grpc", "out", re.compile(r"\b(?P<type>\w+)\.(?P=type)Client\b"), "grpc"),
    ("grpc", "out", re.compile(r"(?<!func )\bNew(?P<type>\w+)Client\s*\("), "grpc"),
    ("grpc", "out", re.compile(r"\b(?P<type>\w+)Grpc\.new(?:Blocking|Future)?Stub\b"), "grpc"),
    ("grpc", "out", re.compile(r"_grpc\.(?P<type>\w+)Stub\s*\("), "grpc"),
    # HTTP clients: the literal must look like a path or URL (checked later).
    ("http", "out", re.compile(rf"\.(?:Get|Post|Put|Delete|Patch|Send)(?:FromJson|AsJson|String|Stream|ByteArray)?Async{GEN}{arg()}"), None),
    ("http", "out", re.compile(rf"\[\s*(?:Get|Post|Put|Delete|Patch|Head)\s*\(\s*{arg()}"), None),  # Refit
    ("http", "out", re.compile(rf"(?<![\w.])fetch\s*\(\s*{arg()}"), None),
    ("http", "out", re.compile(rf"(?i)(?<![\w.])(?:axios|https?|client|httpclient|_httpclient|_client|this\.http|this\.httpclient|requests|httpx|session|self\.session|self\.client|superagent|ky|got|\$http|instance|resttemplate|webclient|resty)\s*\.\s*(?:get|post|put|delete|patch|head|request|getforobject|postforobject|getforentity|postforentity|exchange){GEN}{arg()}"), None),
    ("host", "out", re.compile(rf"@FeignClient\s*\(\s*(?:(?:name|value)\s*=\s*)?{arg()}"), None),
]
HTTP_SERVER = [
    re.compile(rf"\.Map(?:Get|Post|Put|Delete|Patch|Methods)\s*\(\s*{PATH_LIT.replace('/[', '/?[', 1)}"),
    re.compile(rf"(?i)(?<![\w.])(?:app|router|r|e|g|server|mux|engine|route|routes|v\d+|group|grp|fastify|http)\s*\.\s*(?:get|post|put|delete|patch|all|head|options|route|handlefunc|handle){GEN}{PATH_LIT}"),
    re.compile(rf"@\w+\.(?:route|get|post|put|delete|patch|api_route|websocket)\s*\(\s*{PATH_LIT}"),
]
ROUTE_PREFIX = [
    re.compile(rf"\[\s*(?:Route|RoutePrefix)\s*\(\s*(?:template\s*:\s*)?(?P<arg>{STR})"),
    re.compile(rf"@(?:RequestMapping|Controller|Path)\s*\(\s*(?:(?:value|path)\s*=\s*)?\{{?\s*(?P<arg>{STR})"),
]
METHOD_ROUTE = [
    re.compile(rf"\[\s*Http(?:Get|Post|Put|Delete|Patch|Head|Options)\b\s*(?:\(\s*(?:template\s*:\s*)?(?P<arg>{STR}))?"),
    re.compile(rf"@(?:Get|Post|Put|Delete|Patch)Mapping\b\s*(?:\(\s*(?:(?:value|path)\s*=\s*)?\{{?\s*(?P<arg>{STR}))?"),
    re.compile(rf"@(?:Get|Post|Put|Delete|Patch|All|Head|Options)\s*\(\s*(?P<arg>{STR})?\s*\)"),
]
CLASS_DECL = re.compile(r"\bclass\s+(\w+)")
URL = re.compile(r"(?P<scheme>https?|grpcs?|wss?)://(?P<host>[A-Za-z0-9_.-]+)(?::(?P<port>\d+))?(?P<path>/[^\s\"'`<>)\]},;]*)?")
URL_KEY = re.compile(r"""["']?(?P<key>[A-Za-z_][\w:.\-]*)["']?\s*[:=]\s*["']?\s*$""")
ASSIGN = re.compile(r"""["']?\b(?P<name>[A-Za-z_]\w*)["']?\s*(?::\s*[\w.<>\[\]?]+\s*)?(?::=|=|:)\s*[@$fFrR]{0,2}(?P<q>["'`])(?P<val>[^"'`\n{}]{1,200})(?P=q)""")
NAMEOF = re.compile(r"\b(?P<name>[A-Za-z_]\w*)\s*=\s*nameof\s*\(\s*(?:[\w.]*\.)?(?P<val>\w+)\s*\)")   # X = nameof(X)
IN_CTX = re.compile(r"(?i)reader|consumer|subscri|listen|receiv|handler|bind")
OUT_CTX = re.compile(r"(?i)writer|producer|publish|send|emit|dispatch")
NAME_HOLDER = re.compile(r"\w*(?:Names|Ids|Keys|Constants)\b")   # RabbitPublisherNames.X says nothing about the role
HASH_COMMENT_EXT = {".py", ".rb", ".sh", ".ps1", ".r", ".pl", ".yaml", ".yml", ".toml", ".ini", ".properties", ".conf", ".env"}
HASH_LINE = re.compile(r"(?m)^[ \t]*#.*$")
SLASH_LINE = re.compile(r"(?m)^[ \t]*(?://|/\*|\*(?:\s|/|$)).*$")
CAPS_REF = re.compile(r"\b(?:[A-Za-z_]\w*\.)+[A-Z][A-Z0-9_]{2,}\b(?:\s*\([^()\n]{0,80}\))?")   # Subjects.PLACE_BET(game)
DEAD_LETTER = re.compile(r"(?i)dead_?letter|\bdl[qx]\b")
GENERIC_NAMES = {"subject", "subjects", "topic", "topics", "queue", "queues", "queuename", "name", "channel", "channels", "stream", "exchange",
                 "routingkey", "value", "key", "id", "url", "uri", "path", "route", "endpoint", "address", "type",
                 "message", "msg", "event", "data", "payload", "string", "empty", "length"}
GENERIC_TOKENS = {"service", "services", "svc", "api", "apis", "server", "srv", "app", "application", "worker", "web",
                  "backend", "be", "ms", "micro", "microservice", "microservices", "host", "hosts", "grpc", "http",
                  "https", "url", "uri", "urls", "endpoint", "endpoints", "address", "base", "client", "clients",
                  "internal", "local", "localhost", "dev", "prod", "staging", "default", "cluster", "port", "src",
                  "core", "svc", "net", "com", "io", "org", "www", "settings", "config", "options", "connection"}
TRIVIAL_ROUTE = {"health", "healthz", "healthcheck", "metrics", "ready", "readyz", "live", "livez", "liveness",
                 "readiness", "ping", "swagger", "status", "version", "info", "favicon.ico", "index.html"}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "host.docker.internal", "[::1]"}
IGNORED_HOSTS = {"example.com", "www.w3.org", "schemas.microsoft.com",
                 "json-schema.org", "schemas.xmlsoap.org", "tempuri.org", "xmlns"}


# ---- small helpers ---------------------------------------------------------------------------
def tokens(text: str) -> list[str]:
    return [t.lower() for t in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", text)]


def core_tokens(text: str) -> list[str]:
    toks = tokens(text)
    return [t for t in toks if t not in GENERIC_TOKENS] or toks


def contains_seq(hay: list[str], needle: list[str]) -> bool:
    n = len(needle)
    return bool(n) and any(hay[i:i + n] == needle for i in range(len(hay) - n + 1))


def short(line: str) -> str:
    line = line.strip()
    return line if len(line) <= 160 else line[:157] + "..."


def literal(raw: str, consts: dict) -> str | None:
    m = re.match(r"""^([@$fFrRbB]{0,3})(["'`])(.*)\2$""", raw, re.S)
    if not m:
        return None
    prefix, quote, value = m.groups()
    interpolated = "$" in prefix or "f" in prefix.lower() or quote == "`"
    if interpolated:
        def sub(mm):
            vals = consts.get(mm.group(1).split(".")[-1])
            return next(iter(vals)) if vals and len(vals) == 1 else "*"
        value = re.sub(r"\$?\{\s*([A-Za-z_][\w.]*)[^{}]*\}", sub, value)
        value = re.sub(r"\$?\{[^{}]*\}", "*", value)
    value = re.sub(r"%[sdvqx]", "*", value)
    return value.strip()


def resolve(raw: str, consts: dict) -> tuple[str, bool] | None:
    """(channel, resolved) for a captured argument; None when it is a variable we cannot read."""
    if raw[:1] in "\"'`" or (raw[:1] in "@$fFrRbB" and re.match(r"^[@$fFrRbB]{1,3}[\"'`]", raw)):
        value = literal(raw, consts)
        return (value, True) if value else None
    if "(" in raw:  # a constant-like factory: matched by name on both sides
        return re.sub(r"\s*\(.*$", "()", raw, flags=re.S), False
    last = raw.split(".")[-1]
    vals = consts.get(last)
    if vals and len(vals) == 1:
        return next(iter(vals)), True
    if last.lower() in GENERIC_NAMES or len(last) < 3:
        return None
    if re.fullmatch(r"[A-Z][A-Z0-9_]+", last) or (last[0].isupper() and "." in raw):
        return raw, False
    return None


def class_role(text: str, pos: int) -> str | None:
    """Role from the enclosing class: a PlaceBetSubscriber receives, a BetPublisher sends."""
    head = None
    for m in re.finditer(r"\bclass\s+\w+[^{;]*", text[:pos]):
        head = m.group(0)
    if head:
        if re.search(r"(?i)subscri|consum|listen|handler|receiv|processing|worker", head):
            return "in"
        if re.search(r"(?i)publish|produc|sender|emitter|dispatch", head):
            return "out"
    return ctx_role(text, pos)


def ctx_role(text: str, pos: int) -> str | None:
    window = NAME_HOLDER.sub(lambda m: " " * len(m.group(0)), text[max(0, pos - 300):pos])
    last_in = max((m.end() for m in IN_CTX.finditer(window)), default=-1)
    last_out = max((m.end() for m in OUT_CTX.finditer(window)), default=-1)
    if last_in == last_out == -1:
        return None
    return "in" if last_in > last_out else "out"


def near_broker(text: str, pos: int, present: set, fallback: str) -> str:
    """The broker named closest before a match (a file can use NATS and Kafka side by side)."""
    window = text[max(0, pos - 400):pos + 60]
    best, best_at = fallback, -1
    for name in BROKERS:
        if name in present:
            at = max((m.end() for m in MARKERS[name].finditer(window)), default=-1)
            if at > best_at:
                best, best_at = name, at
    return best


def next_code_line(text: str, pos: int) -> str:
    lines = text[pos:pos + 1200].split("\n")[1:8]
    for line in lines:
        s = line.strip()
        if s and not re.match(r"^(\[|@|//|/\*|\*|#\[|#)", s):
            return s
    return ""


def join_route(prefix: str, value: str) -> str:
    if not prefix:
        return value
    return prefix.rstrip("/") + "/" + value.lstrip("/") if value else prefix


def route_segments(raw: str) -> list[str]:
    s = re.sub(r"^(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+", "", raw.strip(), flags=re.I)
    s = re.sub(r"^[a-z][a-z0-9+.-]*://[^/]*", "", s, flags=re.I)
    s = s.split("?")[0].split("#")[0]
    segs = []
    for seg in s.strip("/").split("/"):
        if not seg or seg in ("~", "."):
            continue
        segs.append("*" if re.search(r"[{}:<>*%$]|^\d+$", seg) else seg.lower())
    return segs


def meaningful(segs: list[str]) -> list[str]:
    return [s for s in segs if s != "*" and not re.fullmatch(r"api|v\d+(\.\d+)?", s)]


def segs_match(client: list[str], route: list[str]) -> int:
    """2 = same shape, 1 = one is a tail of the other (base address / gateway prefix), 0 = no."""
    def same(a, b):  # wildcards match anything, but at least one real segment must agree
        pairs = list(zip(a, b))
        return all(x == y or x == "*" or y == "*" for x, y in pairs) and any(x == y != "*" for x, y in pairs)
    if not client or not route or not meaningful(route) or not meaningful(client):
        return 0
    if set(meaningful(route)) <= TRIVIAL_ROUTE:
        return 0
    if len(client) == len(route):
        return 2 if same(client, route) else 0
    shorter, longer = (client, route) if len(client) < len(route) else (route, client)
    if len(meaningful(shorter)) >= 1 and len(shorter) >= 2 and same(shorter, longer[-len(shorter):]):
        return 1
    return 0


def subject_match(a: str, b: str) -> bool:
    if a == b:
        return True
    ta, tb = re.split(r"[.:/]", a), re.split(r"[.:/]", b)
    for i, (x, y) in enumerate(zip(ta, tb)):
        if x in (">", "#") or y in (">", "#"):
            return True
        if x == y:
            continue
        if "*" in x and fnmatch.fnmatchcase(y, x):
            continue
        if "*" in y and fnmatch.fnmatchcase(x, y):
            continue
        return False
    return len(ta) == len(tb)


def too_broad(channel: str) -> bool:
    return not re.sub(r"[*>#.:/\-_\s]", "", channel)


# ---- scanning one service -------------------------------------------------------------------
def iter_files(root: Path):
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            ext = os.path.splitext(name)[1].lower()
            if ext not in SOURCE_EXT and ext not in CONFIG_EXT and not CONFIG_NAME.match(name) \
                    and not name.lower().startswith("appsettings"):
                continue
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            if TEST_PATH.search(rel) or GENERATED.search(rel):
                continue
            count += 1
            if count > MAX_FILES:
                return
            yield path, rel, ext


def read_text(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data[:2048]:
        return None
    return data.decode("utf-8", errors="replace")


def read_files(root: Path) -> list[tuple[str, str, str]]:
    files = []
    for path, rel, ext in iter_files(root):
        text = read_text(path)
        if text:
            files.append((rel, ext, text))
    return files


def code_only(text: str, ext: str) -> str:
    """Whole-line comments blanked to spaces (lines and offsets stay put): commented-out code is no link."""
    rx = HASH_LINE if ext in HASH_COMMENT_EXT else SLASH_LINE
    return rx.sub(lambda m: " " * len(m.group(0)), text)


def collect_consts(files, consts: dict | None = None) -> dict[str, set]:
    consts = consts if consts is not None else defaultdict(set)
    for _rel, ext, text in files:
        text = code_only(text, ext)
        for rx in (ASSIGN, NAMEOF):
            for m in rx.finditer(text):
                if len(consts[m.group("name")]) < 4:
                    consts[m.group("name")].add(m.group("val").strip())
    return consts


def scan_service(root: Path) -> dict:
    files = read_files(root)
    consts = collect_consts(files)
    endpoints = extract(files, consts)
    helpers = follow_helpers(files, consts, endpoints)
    return {"files": len(files), "endpoints": endpoints, "techs": tech_counts(endpoints), "consts": consts, "helpers": helpers}


def apply_helpers(endpoints: list[dict], helpers: dict) -> None:
    for e in endpoints:
        if not e["resolved"] and e["channel"] in helpers:
            e["via"] = e["channel"]
            e["channel"], e["resolved"] = helpers[e["via"]]


def follow_helpers(files, consts: dict, endpoints: list[dict]) -> dict[str, list]:
    """A helper call such as `NatsHelper.GetPlaceBetSubject(type, id)` takes the one shared name it builds
    (`Subjects.PLACE_BET()`), following its body and the fields it reads in this service.
    Returns {helper call: [channel, resolved]} for the edit hook, which sees one file at a time."""
    helpers: dict[str, list] = {}
    todo_endpoints = [e for e in endpoints if not e["resolved"] and e["channel"].endswith("()")
                      and not re.fullmatch(r"[A-Z][A-Z0-9_]+\(\)", e["channel"].split(".")[-1])]
    if not todo_endpoints:
        return helpers
    clean = [code_only(text, ext) for _rel, ext, text in files]

    def body(name: str) -> tuple[str, str]:
        """(body, parameter list) of the method or field `name` declares."""
        rx = re.compile(rf"\b{re.escape(name)}\s*(?:<[^<>()]*>)?\s*(?:\((?P<params>[^()]*)\)\s*(?P<open>=>|\{{)|=(?![=>]))")
        for text in clean:
            m = rx.search(text)
            if m:
                rest = text[m.end():m.end() + 600]
                return (rest if m.group("open") == "{" else rest.split(";", 1)[0]), m.group("params") or ""
        return "", ""

    for e in todo_endpoints:
        if e["channel"] in helpers:
            continue
        refs, todo, seen = set(), [e["channel"].split(".")[-1][:-2]], set()
        while todo and len(seen) < 6:
            name = todo.pop(0)
            seen.add(name)
            text, params = body(name)
            found = {re.sub(r"\s*\(.*$", "()", r, flags=re.S) for r in CAPS_REF.findall(text)}
            refs |= found
            if not found:   # read through fields such as `_placeBet = BuildMap(g => Subjects.PLACE_BET(g))`
                own = set(re.findall(r"\w+", params))
                todo += [w for w in re.findall(r"\b_?[a-z]\w{3,}\b", text) if w not in seen and w not in own][:4]
        if len(refs) == 1:
            got = resolve(next(iter(refs)), consts)
            if got:
                helpers[e["channel"]] = list(got)
    apply_helpers(endpoints, helpers)
    return helpers


def tech_counts(endpoints: list[dict]) -> dict[str, dict]:
    techs: dict[str, dict] = {}
    for e in endpoints:
        if e["role"] in ("in", "out"):
            techs.setdefault(e["tech"], {"in": 0, "out": 0})[e["role"]] += 1
    return techs


def extract(files, consts: dict) -> list[dict]:
    """Communication endpoints in already-read files: [(relative path, extension, text)]."""
    endpoints, seen = [], set()

    def add(tech, role, channel, resolved, rel, text, nl, pos, kind="channel", **extra):
        if not channel or len(channel) > 200:
            return
        line = bisect.bisect_right(nl, pos) + 1
        key = (tech, role, channel.lower(), rel, line, kind)
        if key in seen:
            return
        seen.add(key)
        start = nl[line - 2] + 1 if line > 1 else 0
        end = nl[line - 1] if line - 1 < len(nl) else len(text)
        endpoints.append({"tech": tech, "role": role, "channel": channel, "resolved": resolved, "kind": kind,
                          "file": rel, "line": line, "text": short(text[start:end]), **extra})

    for rel, ext, text in files:
        text = code_only(text, ext)
        nl = [m.start() for m in re.finditer("\n", text)]
        is_config = ext in CONFIG_EXT or CONFIG_NAME.match(rel.rsplit("/", 1)[-1]) is not None
        if not is_config:
            present = {name for name, rx in MARKERS.items() if rx.search(text)}
            if ext == ".proto":
                present.add("grpc")
            broker = next((b for b in BROKERS if b in present), None)
            for tech, role, rx, gate in RULES:
                if gate == "broker" and not broker:
                    continue
                if gate and gate != "broker" and gate not in present:
                    continue
                for m in rx.finditer(text):
                    t = near_broker(text, m.start(), present, broker) if tech == "broker" else LISTENER_TECH.get((m.groupdict().get("t") or "").lower(), tech) if tech == "listener" else tech
                    r = ctx_role(text, m.start()) if role == "ctx" else class_role(text, m.start()) if role == "class" else role
                    if not r or not t:
                        continue
                    if "type" in m.groupdict():
                        name = m.group("type").split(".")[-1]
                        if len(name) < 3 or re.fullmatch(r"T([A-Z]\w*)?", name):
                            continue
                        add(t, r, name, True, rel, text, nl, m.start())
                        continue
                    got = resolve(m.group("arg"), consts)
                    if not got and m.groupdict().get("arg2"):
                        got = resolve(m.group("arg2"), consts)
                    if got and t == "rabbitmq" and got[0] == "" and m.groupdict().get("arg2"):
                        got = resolve(m.group("arg2"), consts)
                    if not got:
                        continue
                    channel, resolved = got
                    if t in ("http", "host"):
                        if t == "http" and resolved and "/" not in channel:
                            continue
                        if t == "http" and not resolved and not re.search(r"(?i:ur[li]|route|path|endpoint)|^[A-Z][A-Z0-9_]{3,}(\(\))?$",
                                                                          channel.split(".")[-1]):
                            continue  # _service.GetAsync(request.PartnerId) is no HTTP call
                        if t == "host":
                            add("http", "out", channel, resolved, rel, text, nl, m.start(), kind="host", host=channel)
                            continue
                        if resolved and re.match(r"^[a-z]+://", channel, re.I):
                            continue  # full URLs are handled by the URL pass below
                        add("http", "out", channel, resolved, rel, text, nl, m.start(), kind="path")
                        continue
                    if resolved and too_broad(channel):
                        continue
                    add(t, r, channel, resolved, rel, text, nl, m.start())
            scan_routes(text, rel, nl, add, consts, ext)
            if ext == ".go":
                # net/http request constructors take the URL after the method.
                request = re.compile(rf"\bhttp\.NewRequest(?:WithContext)?\s*\(\s*(?:[^,\n]+,\s*)?(?:http\.Method\w+|{STR})\s*,\s*(?P<arg>{STR}|{IDENT})\s*,")
                for m in request.finditer(text):
                    got = resolve(m.group('arg'), consts)
                    if got and got[1] and got[0].startswith('/'):
                        add('http', 'out', got[0], True, rel, text, nl, m.start(), kind='path')
        # URLs anywhere (code and config): the host may name another service.
        for m in URL.finditer(text):
            host = m.group("host").lower()
            if host in IGNORED_HOSTS or host.endswith((".w3.org", ".microsoft.com", ".apache.org", ".invalid", ".example")):
                continue
            scheme = m.group("scheme").lower()
            tech = "grpc" if scheme.startswith("grpc") else "websocket" if scheme.startswith("ws") else "http"
            line_start = text.rfind("\n", 0, m.start()) + 1
            key = URL_KEY.search(text[line_start:m.start()])
            add(tech, "out", m.group(0), True, rel, text, nl, m.start(), kind="url", host=host,
                path=m.group("path") or "", key=key.group("key") if key else "")
    return endpoints


def scan_routes(text: str, rel: str, nl: list[int], add, consts: dict, ext: str) -> None:
    for rx in HTTP_SERVER:
        for m in rx.finditer(text):
            value = literal(m.group("arg"), {})
            if value:
                add("http", "in", value, True, rel, text, nl, m.start(), kind="route")
    if ext == ".go":
        # chi's Method takes the HTTP verb first; route expressions commonly join
        # a slash with a named constant. Resolve only unambiguous literal parts.
        term = rf"(?:{STR}|{IDENT})"
        route = re.compile(rf"\.Method\s*\(\s*(?:http\.Method\w+|{STR})\s*,\s*(?P<route>{term}(?:\s*\+\s*{term})*)\s*,")
        for m in route.finditer(text):
            parts = re.split(r"\s*\+\s*", m.group("route"))
            values = [resolve(part, consts) for part in parts]
            if all(value and value[1] for value in values):
                path = ''.join(value[0] for value in values)
                if path.startswith('/'):
                    add("http", "in", path, True, rel, text, nl, m.start(), kind="route")
    events = []
    for rx in ROUTE_PREFIX:
        events += [("pre", m.start(), m) for m in rx.finditer(text)]
    for rx in METHOD_ROUTE:
        events += [("rt", m.start(), m) for m in rx.finditer(text)]
    if not events:
        return
    events += [("cls", m.start(), m) for m in CLASS_DECL.finditer(text)]
    events.sort(key=lambda e: e[1])
    prefix, pending = "", None
    for kind, pos, m in events:
        if kind == "cls":
            name = re.sub(r"Controller$", "", m.group(1))
            prefix = (pending or "").replace("[controller]", name.lower()) if pending is not None else ""
            pending = None
            continue
        value = literal(m.group("arg"), {}) if m.groupdict().get("arg") else ""
        value = value if value is not None else ""
        if kind == "pre" and re.search(r"\bclass\b", next_code_line(text, m.end())):
            pending = value
            continue
        csharp = m.group(0).lstrip().startswith("[")
        route = value if csharp and value.startswith(("/", "~")) else join_route(prefix, value)
        if route:
            add("http", "in", route.replace("[action]", "*"), True, rel, text, nl, pos, kind="route")


# ---- linking services ------------------------------------------------------------------------
def service_for_tokens(toks: list[str], cores: list[list[str]], exclude: int) -> int | None:
    best, best_len = None, 0
    for i, core in enumerate(cores):
        if i != exclude and core and contains_seq(toks, core) and len(core) > best_len:
            best, best_len = i, len(core)
    return best


def link(services: list[dict]) -> list[dict]:
    cores = [core_tokens(s["name"]) for s in services]
    edges: dict[tuple, dict] = {}

    def evidence(e):
        return {"file": e["file"], "line": e["line"], "text": e["text"]}

    def connect(src, dst, tech, channel, out_e, in_e, confidence):
        edge = edges.setdefault((src, dst, tech), {"source": src, "target": dst, "tech": tech, "channels": {}, "confidence": set()})
        ch = edge["channels"].setdefault(channel, {"name": channel, "from": [], "to": []})
        for bucket, e in (("from", out_e), ("to", in_e)):
            if e and len(ch[bucket]) < MAX_EVIDENCE and evidence(e) not in ch[bucket]:
                ch[bucket].append(evidence(e))
        edge["confidence"].add(confidence)
        for e in (out_e, in_e):
            if e:
                e["matched"] = True

    by_role = [defaultdict(list) for _ in services]
    for i, s in enumerate(services):
        for e in s["scan"]["endpoints"]:
            by_role[i][(e["tech"], e["role"])].append(e)
    message_techs = {e["tech"] for s in services for e in s["scan"]["endpoints"] if e["tech"] not in ("http", "websocket")}

    def same(tech, out_e, in_e):
        if out_e["resolved"] != in_e["resolved"]:
            return False
        x, y = out_e["channel"], in_e["channel"]
        if tech in ("grpc", "masstransit") or not out_e["resolved"]:
            return x.split(".")[-1].lower() == y.split(".")[-1].lower()
        return subject_match(x.lower(), y.lower())

    for tech in message_techs:
        # a service that sends to itself (delayed retries, round timers, kicks between replicas) is fed by itself:
        # those receivers are not linked to every other service using the same per-service name
        for a in range(len(services)):
            for out_e in by_role[a][(tech, "out")]:
                for in_e in by_role[a][(tech, "in")]:
                    if same(tech, out_e, in_e):
                        out_e["internal"] = in_e["internal"] = True
        for a in range(len(services)):
            for out_e in by_role[a][(tech, "out")]:
                for b in range(len(services)):
                    if a == b:
                        continue
                    for in_e in by_role[b][(tech, "in")]:
                        if not in_e.get("internal") and same(tech, out_e, in_e):
                            x, y = out_e["channel"], in_e["channel"]
                            connect(a, b, tech, y if "*" in x or ">" in y else x, out_e, in_e,
                                    "exact" if out_e["resolved"] else "shared name")
    routes = [[(e, route_segments(e["channel"])) for e in by_role[i][("http", "in")]] for i in range(len(services))]

    def match_routes(a, segs, out_e, only=None, tech="http"):
        found = []
        for b in range(len(services)):
            if b == a or (only is not None and b != only):
                continue
            for in_e, rsegs in routes[b]:
                score = segs_match(segs, rsegs)
                if score:
                    found.append((score, b, in_e))
        if not found:
            return False
        top = max(f[0] for f in found)
        best = [f for f in found if f[0] == top]
        if only is None and len({b for _, b, _ in best}) > 2:
            return False  # too many services share this route shape
        for _, b, in_e in best:
            connect(a, b, tech, in_e["channel"], out_e, in_e, "route")
        return True

    for a in range(len(services)):
        for out_e in by_role[a][("http", "out")] + by_role[a][("grpc", "out")] + by_role[a][("websocket", "out")]:
            kind = out_e["kind"]
            if kind == "path":
                match_routes(a, route_segments(out_e["channel"]), out_e)
            elif kind in ("url", "host"):
                host_toks = core_tokens(out_e["host"])
                b = service_for_tokens(host_toks, cores, a)
                via = "url"
                if b is None and out_e.get("key"):
                    b = service_for_tokens(core_tokens(out_e["key"]), cores, a)
                    via = "config key"
                segs = route_segments(out_e.get("path") or "")
                if b is not None:
                    if not (segs and match_routes(a, segs, out_e, only=b, tech=out_e["tech"])):
                        label = out_e["host"] + (out_e.get("path") or "")
                        connect(a, b, out_e["tech"], label, out_e, None, via)
                elif segs and out_e["tech"] == "http" and (out_e["host"] in LOCAL_HOSTS or "." not in out_e["host"]
                                                          or re.fullmatch(r"[\d.]+", out_e["host"])):
                    match_routes(a, segs, out_e)
    result = []
    for edge in edges.values():
        channels = sorted(edge["channels"].values(), key=lambda c: c["name"].lower())
        result.append({"source": edge["source"], "target": edge["target"], "tech": edge["tech"],
                       "confidence": sorted(edge["confidence"]), "count": len(channels), "channels": channels})
    result.sort(key=lambda e: (e["source"], e["target"], e["tech"]))
    return result


def scan_services(paths: list[dict]) -> list[dict]:
    services = []
    for item in paths[:MAX_SERVICES]:
        root = Path(item["path"])
        scan = scan_service(root) if root.is_dir() else {"files": 0, "endpoints": [], "techs": {}, "consts": {}, "missing": True}
        services.append({"name": item.get("name") or root.name, "path": str(root), "scan": scan})
    for svc in services:
        resolve_shared(services, svc["scan"]["endpoints"])
    return services


def resolve_shared(services: list[dict], endpoints: list[dict]) -> None:
    """Constants declared in another service of the system (a shared contracts library such as
    CommonLibraries/SharedModels/Subjects.cs) resolve names left unresolved in this one."""
    shared: dict[str, set] = defaultdict(set)
    for svc in services:
        for name, values in svc["scan"].get("consts", {}).items():
            shared[name].update(values)
    for e in endpoints:
        if not e.get("resolved") and "(" not in e["channel"]:
            values = shared.get(e["channel"].split(".")[-1])
            if values and len(values) == 1:
                e["channel"], e["resolved"] = next(iter(values)), True


def fed_by_broker(e: dict, endpoints: list[dict]) -> bool:
    """Receivers no service has to send to: the service's own loop, dead-letter queues the broker fills,
    NATS server advisories ($JS.…, $SYS.…) and a RabbitMQ queue bound to an exchange whose sender is already
    known in the same file."""
    if e.get("internal") or (e["role"] == "in" and (DEAD_LETTER.search(e["channel"]) or e["channel"].startswith(("$JS.", "$SYS.")))):
        return True
    if e["tech"] != "rabbitmq" or e["role"] != "in" or not re.search(r"(?i)queue", e["text"]):
        return False
    return any(o is not e and o["tech"] == "rabbitmq" and o["role"] == "in" and o["file"] == e["file"]
               and (o.get("matched") or o.get("internal")) and not re.search(r"(?i)queue", o["text"]) for o in endpoints)


def scan_system(paths: list[dict], system: dict | None = None) -> dict:
    """paths: [{"name", "path"}]. Returns services (with unmatched endpoints) and service-to-service edges.
    With a saved system, the raw scan is also cached for the edit hook."""
    started = time.time()
    services = scan_services(paths)
    if system:
        save_cache(system, services)
    edges = link(services)
    out = []
    for s in services:
        eps = s["scan"]["endpoints"]
        outside = [{k: e[k] for k in ("tech", "role", "channel", "file", "line", "text")} for e in eps
                   if not e.get("matched") and e["role"] in ("in", "out") and not (e["tech"] == "http" and e["role"] == "in")
                   and e["kind"] != "url" and not fed_by_broker(e, eps)][:60]
        routes = sum(1 for e in eps if e["tech"] == "http" and e["role"] == "in")
        out.append({"name": s["name"], "path": s["path"], "files": s["scan"]["files"], "techs": s["scan"]["techs"],
                    "endpoints": len(eps), "routes": routes, "missing": bool(s["scan"].get("missing")),
                    "has_graph": (Path(s["path"]) / "graphify-out" / "graph.json").is_file(), "unmatched": outside})
    return {"services": out, "edges": edges, "seconds": round(time.time() - started, 2)}


def _first_line(location) -> int:
    match = re.search(r"(\d+)", str(location or ""))
    return int(match.group(1)) if match else 0


def _relative(root: Path, source_file: str) -> str:
    raw = Path(source_file)
    if raw.is_absolute():
        try:
            raw = raw.resolve().relative_to(root.resolve())
        except (ValueError, OSError):
            pass
    text = str(raw).replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.lower()


def system_graph(name: str, scan: dict) -> dict:
    """One graph for the whole system: every service's code graph (node ids prefixed per service,
    community = service) plus cross-service links between the code that sends and the code that
    receives. A service without a graph is represented by its service node only."""
    nodes, links, files_by_service, missing = [], [], [], []
    for i, svc in enumerate(scan["services"]):
        root, prefix = Path(svc["path"]), f"s{i}::"
        common = {"community": svc["name"], "community_name": svc["name"], "service": svc["name"], "project_path": svc["path"]}
        nodes.append({"id": f"{prefix}service", "label": svc["name"], "file_type": "service", "type": "service", **common})
        files: dict[str, list] = defaultdict(list)
        graph = root / "graphify-out" / "graph.json"
        try:
            data = json.loads(graph.read_text(encoding="utf-8")) if graph.is_file() else None
        except (OSError, ValueError):
            data = None
        if data is None:
            missing.append(svc["name"])
        else:
            for node in data.get("nodes", []):
                nid = prefix + str(node.get("id"))
                nodes.append({**node, "id": nid, **common})
                if node.get("source_file"):
                    files[_relative(root, str(node["source_file"]))].append(
                        (_first_line(node.get("source_location")), nid, str(node.get("label", "")).endswith(")")))
            for link in data.get("links", data.get("edges", [])):
                ref = lambda v: prefix + str(v.get("id") if isinstance(v, dict) else v)
                item = {**link, "source": ref(link["source"]), "target": ref(link["target"])}
                for key in ("_src", "_tgt"):
                    if link.get(key) is not None:
                        item[key] = ref(link[key])
                links.append(item)
        files_by_service.append(files)

    def anchor(i: int, ev: dict | None) -> str:
        """The graph node that holds an evidence line: the closest declaration starting at or before it."""
        candidates = files_by_service[i].get(ev["file"].lower()) if ev else None
        if not candidates:
            return f"s{i}::service"
        before = [c for c in candidates if 0 < c[0] <= ev["line"]]
        return (max(before, key=lambda c: (c[0], c[2])) if before else min(candidates, key=lambda c: c[0]))[1]

    seen, cross = set(), 0
    for edge in scan["edges"]:
        for channel in edge["channels"]:
            senders = {anchor(edge["source"], ev) for ev in channel["from"]} or {anchor(edge["source"], None)}
            receivers = {anchor(edge["target"], ev) for ev in channel["to"]} or {anchor(edge["target"], None)}
            for s in senders:
                for t in receivers:
                    key = (s, t, edge["tech"], channel["name"])
                    if key in seen:
                        continue
                    seen.add(key)
                    cross += 1
                    links.append({"source": s, "target": t, "relation": f"sends_via_{edge['tech']}", "tech": edge["tech"],
                                  "channel": channel["name"], "cross_service": True})
    return {"nodes": nodes, "links": links, "project_name": f"System: {name}", "system": True,
            "cross_links": cross, "missing_graphs": missing, "directed": True}


def discover_services(parent: Path, depth: int = 2) -> list[dict]:
    """Sub-folders of a parent folder that look like services (they hold a project or Dockerfile)."""
    found = []
    def has_project(folder: Path, level: int) -> bool:
        try:
            entries = list(folder.iterdir())
        except OSError:
            return False
        if any(e.is_file() and PROJECT_MARKERS.search(e.name) for e in entries):
            return True
        return level < depth and any(e.is_dir() and e.name not in SKIP_DIRS and not e.name.startswith(".")
                                     and has_project(e, level + 1) for e in entries)
    try:
        children = sorted((c for c in parent.iterdir() if c.is_dir()), key=lambda c: c.name.lower())
    except OSError:
        return []
    for child in children:
        if child.name in SKIP_DIRS or child.name.startswith("."):
            continue
        if has_project(child, 1):
            found.append({"name": child.name, "path": str(child.resolve())})
    return found


# ---- saved systems ---------------------------------------------------------------------------
def load_systems() -> list[dict]:
    source = SYSTEMS if SYSTEMS.is_file() or not LEGACY_SYSTEMS.is_file() else LEGACY_SYSTEMS
    try:
        data = json.loads(source.read_text(encoding="utf-8-sig"))
        return [s for s in data.get("systems", []) if isinstance(s, dict) and s.get("id")]
    except (OSError, ValueError, AttributeError):
        return []


def _write(items: list[dict]) -> None:
    SYSTEMS.parent.mkdir(parents=True, exist_ok=True)
    SYSTEMS.write_text(json.dumps({"systems": items}, indent=1), encoding="utf-8")


def save_system(body: dict) -> dict:
    name = str(body.get("name") or "").strip()[:80]
    if not name:
        raise ValueError("Give the system a name.")
    services, keys = [], set()
    for item in body.get("services") or []:
        raw = str((item or {}).get("path") or "").strip()
        if not raw or "://" in raw:
            continue
        path = Path(raw).expanduser().resolve()
        if not path.is_dir():
            raise ValueError(f"Folder does not exist: {path}")
        key = os.path.normcase(str(path))
        if key in keys:
            continue
        keys.add(key)
        services.append({"name": str(item.get("name") or path.name).strip()[:80] or path.name, "path": str(path)})
    if len(services) > MAX_SERVICES:
        raise ValueError(f"A system can hold at most {MAX_SERVICES} services.")
    root = str(body.get("root") or "").strip()
    items = load_systems()
    sid = str(body.get("id") or "") or uuid.uuid4().hex[:10]
    system = {"id": sid, "name": name, "root": root, "services": services, "updated": time.time()}
    items = [system if s["id"] == sid else s for s in items] if any(s["id"] == sid for s in items) else [system, *items]
    _write(items)
    return system


def delete_system(sid: str) -> None:
    _write([s for s in load_systems() if s["id"] != sid])


def get_system(sid: str) -> dict:
    for s in load_systems():
        if s["id"] == sid:
            return s
    raise ValueError("Unknown system")


# ---- cross-service impact of a change (CLI `impact`, Claude Code hook) -------------------------
def _cache_path(system: dict) -> Path:
    return CACHE_DIR / f"{re.sub(r'[^A-Za-z0-9_-]', '', system['id'])}.json"


def save_cache(system: dict, services: list[dict]) -> None:
    data = {"updated": system.get("updated"), "scanner": SCANNER_VERSION, "services": [
        {"name": s["name"], "path": s["path"], "files": s["scan"]["files"], "missing": bool(s["scan"].get("missing")),
         "consts": {k: sorted(v) for k, v in s["scan"].get("consts", {}).items()},
         "helpers": s["scan"].get("helpers", {}),
         "endpoints": [{k: v for k, v in e.items() if k not in ("matched", "internal")} for e in s["scan"]["endpoints"]]}
        for s in services]}
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(system).write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass


def load_cached(system: dict) -> list[dict]:
    """Raw scans of a system's services: the cache when it matches the saved system, else a fresh scan."""
    try:
        data = json.loads(_cache_path(system).read_text(encoding="utf-8"))
        if data.get("updated") == system.get("updated") and data.get("scanner") == SCANNER_VERSION \
                and len(data["services"]) == len(system["services"]):
            return [{"name": s["name"], "path": s["path"], "scan": {
                "files": s["files"], "missing": s["missing"], "endpoints": s["endpoints"], "helpers": s.get("helpers", {}),
                "consts": defaultdict(set, {k: set(v) for k, v in s["consts"].items()})}} for s in data["services"]]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    services = scan_services(system["services"])
    save_cache(system, services)
    return services


def _inside(path: Path, root: str) -> str | None:
    """path relative to root (posix) when path is inside root, else None."""
    a, b = os.path.normcase(str(path)), os.path.normcase(str(Path(root).resolve()))
    if a == b or not a.startswith(b.rstrip("\\/") + os.sep):
        return None
    return Path(str(path)[len(str(Path(root).resolve())):].lstrip("\\/")).as_posix()


def owners(path: Path) -> list[tuple[dict, int, str]]:
    """(system, service index, relative file) for every saved system service that contains path."""
    found = []
    for system in load_systems():
        for i, svc in enumerate(system["services"]):
            rel = _inside(path, svc["path"])
            if rel:
                found.append((system, i, rel))
    return found


def _connections(services: list[dict]) -> tuple[list[dict], dict]:
    services = copy.deepcopy(services)
    edges = link(services)
    keys = {}
    for e in edges:
        for ch in e["channels"]:
            keys[(services[e["source"]]["name"], services[e["target"]]["name"], e["tech"], ch["name"])] = ch
    return edges, keys


def with_local_consts(base: dict, rel: str, text: str | None) -> dict:
    """Service constants, with the ones this file declares taken from this version of the file."""
    merged = defaultdict(set, {k: set(v) for k, v in base.items()})
    if text:
        for name, values in collect_consts([(rel, "", text)]).items():
            merged[name] = set(values)
    return merged


def _near(line: int, ranges: list[tuple[int, int]] | None, slack: int = 0) -> bool:
    return ranges is None or any(a - slack <= line <= b + slack for a, b in ranges)


def enclosing_types(text: str, ranges: list[tuple[int, int]] | None) -> set[str]:
    """Names of the types (class/record/struct/interface/proto message …) an edit falls into or declares."""
    decls = [(text.count("\n", 0, m.start()) + 1, m.group(1)) for m in
             re.finditer(r"\b(?:class|record|struct|interface|message|enum|type)\s+([A-Z]\w{2,})", text)]
    if ranges is None:
        return {name for _, name in decls}
    names = set()
    for a, b in ranges:
        before = [d for d in decls if d[0] <= a]
        if before:
            names.add(before[-1][1])
        names.update(name for line, name in decls if a <= line <= b)
    return names


def analyze_change(services: list[dict], index: int, rel: str, new_text: str | None,
                   ranges: list[tuple[int, int]] | None, old_endpoints: list[dict] | None = None,
                   old_text: str | None = None) -> list[str]:
    """Warnings about connections to other services that a change of one file touches, breaks or adds.

    services: raw scans (the changed file may be in any state); old_endpoints: that file's endpoints before
    the change (from the cache) - or old_text to extract them; ranges: changed line ranges in the new file
    (None = the whole file)."""
    svc = services[index]
    ext = os.path.splitext(rel)[1].lower()
    base = svc["scan"].get("consts", {})
    if old_endpoints is None:
        old_endpoints = extract([(rel, ext, old_text)], with_local_consts(base, rel, old_text)) if old_text else []
    new_endpoints = extract([(rel, ext, new_text)], with_local_consts(base, rel, new_text)) if new_text else []
    apply_helpers(old_endpoints, svc["scan"].get("helpers", {}))
    apply_helpers(new_endpoints, svc["scan"].get("helpers", {}))
    resolve_shared(services, old_endpoints)
    resolve_shared(services, new_endpoints)
    others = [e for e in svc["scan"]["endpoints"] if e["file"] != rel]

    def with_file(endpoints):
        state = copy.copy(services)
        state[index] = {**svc, "scan": {**svc["scan"], "endpoints": others + endpoints}}
        return state

    before_edges, before = _connections(with_file(old_endpoints))
    after_services = with_file(new_endpoints)
    after_edges, after = _connections(after_services)
    name = svc["name"]
    loc = lambda ev: f"{ev['file']}:{ev['line']}"
    notes: list[str] = []
    for key in sorted(set(before) - set(after)):
        src, dst, tech, channel = key
        if name in (src, dst):
            other = before[key]["to"] if src == name else before[key]["from"]
            notes.append(f"BROKEN {tech} '{channel}': {src} -> {dst} no longer matches"
                         + (f" (other side: {dst if src == name else src} {loc(other[0])})" if other else ""))
    for key in sorted(set(after) - set(before)):
        src, dst, tech, channel = key
        if name in (src, dst):
            notes.append(f"NEW {tech} '{channel}': {src} -> {dst}")
    # endpoints on the edited lines that another service depends on
    for key, ch in sorted(after.items()):
        src, dst, tech, channel = key
        if ranges is None or key not in before or name not in (src, dst):
            continue  # a whole-file write: only broken/new links and contracts are worth a warning
        mine, theirs, other = (ch["from"], ch["to"], dst) if src == name else (ch["to"], ch["from"], src)
        hit = [ev for ev in mine if ev["file"] == rel and _near(ev["line"], ranges)]
        if hit:
            role = "sends" if src == name else "receives"
            notes.append(f"CHANGED near {tech} '{channel}' ({rel}:{hit[0]['line']} {role}) -> check {other}"
                         + (f" {loc(theirs[0])}" if theirs else ""))
    # message / contract types edited here that a connection carries
    types = enclosing_types(new_text or "", ranges) if new_text else set()
    if types:
        for key, ch in sorted(after.items()):
            src, dst, tech, channel = key
            evidence = [ev for ev in ch["from"] + ch["to"] if ev["file"] != rel]
            used = {t for t in types if t.lower() == channel.split(".")[-1].lower()
                    or any(re.search(rf"\b{re.escape(t)}\b", ev["text"]) for ev in evidence)}
            for t in sorted(used):
                notes.append(f"CONTRACT type {t} changed: carried by {tech} '{channel}' {src} -> {dst}"
                             + (f" ({loc(evidence[0])})" if evidence else ""))
    # new outgoing messages nobody in the system receives
    old_keys = {(e["tech"], e["role"], e["channel"].lower()) for e in old_endpoints}
    matched = {(ev["file"], ev["line"]) for e in after_edges for ch in e["channels"] for ev in ch["from"] + ch["to"]}
    for e in new_endpoints:
        if e["role"] == "out" and e["tech"] not in ("http", "websocket") and (e["tech"], e["role"], e["channel"].lower()) not in old_keys \
                and (e["file"], e["line"]) not in matched:
            notes.append(f"UNMATCHED {e['tech']} '{e['channel']}' sent at {rel}:{e['line']}: no service in the system receives it")
    return list(dict.fromkeys(notes))


def _git(cwd: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def git_changes(cwd: Path, base: str = "HEAD") -> list[tuple[Path, list[tuple[int, int]], str | None]]:
    """[(absolute file, changed new-side line ranges, old text)] for tracked changes and untracked files."""
    top = _git(cwd, "rev-parse", "--show-toplevel")
    if not top:
        return []
    top_path = Path(top.strip())
    diff = _git(top_path, "diff", "--unified=0", "--no-color", "--no-ext-diff", base) or ""
    changes, current, ranges = [], None, []
    for line in diff.splitlines() + ["diff --git end"]:
        if line.startswith("diff --git"):
            if current:
                changes.append((current, ranges))
            current, ranges = None, []
        elif line.startswith("+++ "):
            name = line[4:].strip()
            current = None if name == "/dev/null" else name[2:] if name.startswith("b/") else name
        elif line.startswith("@@") and current:
            m = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if m:
                start, count = int(m.group(1)), int(m.group(2) or 1)
                ranges.append((start, start + max(count, 1) - 1))
    result = []
    for rel, rngs in changes:
        old = _git(top_path, "show", f"{base}:{rel}")
        result.append((top_path / rel, rngs, old))
    for rel in (_git(top_path, "ls-files", "--others", "--exclude-standard") or "").splitlines():
        if rel.strip():
            result.append((top_path / rel.strip(), None, None))
    return result


def impact(paths: list[Path], base: str = "HEAD") -> list[str]:
    """Report for `impact`: git changes (optionally limited to paths) in services of saved systems."""
    cwd = Path.cwd()
    changes = git_changes(cwd, base)
    if paths:
        wanted = [os.path.normcase(str(p.resolve())) for p in paths]
        changes = [c for c in changes if any(os.path.normcase(str(c[0])).startswith(w) for w in wanted)]
    report, fresh = [], {}
    for file, ranges, old_text in changes:
        for system, index, rel in owners(file.resolve()):
            if system["id"] not in fresh:
                services = scan_services(system["services"])   # the disk state: changed files included
                save_cache(system, services)
                fresh[system["id"]] = services
            new_text = read_text(file) if file.is_file() else None
            notes = analyze_change(fresh[system["id"]], index, rel, new_text, ranges, old_text=old_text)
            if notes:
                report.append(f"[{system['name']} / {system['services'][index]['name']}] {rel}")
                report += [f"  - {n}" for n in notes]
    return report


def _edit_ranges(text: str, event: dict) -> list[tuple[int, int]] | None:
    tool, data = event.get("tool_name"), event.get("tool_input") or {}
    pieces = [e.get("new_string", "") for e in data.get("edits", [])] if tool == "MultiEdit" else [data.get("new_string", "")]
    if tool not in ("Edit", "MultiEdit"):
        return None
    ranges = []
    for piece in pieces:
        at = text.find(piece) if piece else -1
        if at < 0:
            return None
        first = text.count("\n", 0, at) + 1
        ranges.append((first, first + piece.count("\n")))
    return ranges


def hook(stdin_text: str) -> int:
    """Claude Code PostToolUse hook: after Edit/Write/MultiEdit of a file in a system service, tell Claude
    (exit code 2 + stderr) which connections to other services the edit touched, broke or added."""
    try:
        event = json.loads(stdin_text or "{}")
    except ValueError:
        return 0
    raw = (event.get("tool_input") or {}).get("file_path") or ""
    if not raw:
        return 0
    file = Path(raw).resolve()
    found = owners(file)
    if not found:
        return 0
    text = read_text(file) if file.is_file() else None
    lines = []
    for system, index, rel in found:
        services = load_cached(system)
        old = [e for e in services[index]["scan"]["endpoints"] if e["file"] == rel]
        notes = analyze_change(services, index, rel, text, _edit_ranges(text or "", event), old_endpoints=old)
        # keep the cache in step with the disk
        ext = os.path.splitext(rel)[1].lower()
        consts = services[index]["scan"]["consts"] = with_local_consts(services[index]["scan"]["consts"], rel, text)
        fresh = extract([(rel, ext, text)], consts) if text else []
        resolve_shared(services, fresh)
        services[index]["scan"]["endpoints"] = [e for e in services[index]["scan"]["endpoints"] if e["file"] != rel] + fresh
        save_cache(system, services)
        if notes:
            lines.append(f"Token Graph: this edit in {system['services'][index]['name']} ({rel}) touches other services of system '{system['name']}':")
            lines += [f"  - {n}" for n in notes]
    if not lines:
        return 0
    lines.append("Tell the user which services are affected and whether they need matching changes; verify in the other service's code.")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles default to a code page
    sys.stderr.write("\n".join(lines) + "\n")
    return 2


SETTINGS = Path.home() / ".claude" / "settings.json"
HOOK_MARK = "system_links.py"


def install_hook(remove: bool = False) -> str:
    try:
        settings = json.loads(SETTINGS.read_text(encoding="utf-8-sig")) if SETTINGS.is_file() else {}
    except ValueError:
        raise SystemExit(f"{SETTINGS} is not valid JSON; fix it first.")
    groups = settings.setdefault("hooks", {}).setdefault("PostToolUse", [])
    groups[:] = [g for g in groups if not any(HOOK_MARK in h.get("command", "") for h in g.get("hooks", []))]
    if not remove:
        python = sys.executable.replace("pythonw.exe", "python.exe")
        command = f'"{python}" "{Path(__file__).resolve()}" hook'
        groups.append({"matcher": "Edit|Write|MultiEdit", "hooks": [{"type": "command", "command": command, "timeout": 120}]})
    if not groups:
        settings["hooks"].pop("PostToolUse")
    if not settings["hooks"]:
        settings.pop("hooks")
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    if SETTINGS.is_file():
        backup = SETTINGS.with_suffix(".json.bak")
        backup.write_text(SETTINGS.read_text(encoding="utf-8-sig"), encoding="utf-8")
    SETTINGS.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return f"{'Removed' if remove else 'Installed'} the Token Graph cross-service hook in {SETTINGS}"


def main(argv: list[str]) -> int:
    if argv[:1] == ["hook"]:
        return hook(sys.stdin.read())
    if argv[:1] in (["install-hook"], ["uninstall-hook"]):
        print(install_hook(remove=argv[0] == "uninstall-hook"))
        return 0
    if argv[:1] == ["impact"]:
        base = argv[argv.index("--base") + 1] if "--base" in argv else "HEAD"
        paths = [Path(a) for i, a in enumerate(argv[1:], 1) if not a.startswith("--") and argv[i - 1] != "--base"]
        if not load_systems():
            print("No Token Graph systems saved yet (Explorer > System > New system).")
            return 0
        report = impact(paths, base)
        print("\n".join(report) if report else "No cross-service connections touched by the current changes.")
        return 0
    if len(argv) >= 2 and argv[0] == "discover":
        print(json.dumps(discover_services(Path(argv[1]).resolve()), indent=1))
        return 0
    if len(argv) < 2 or argv[0] != "scan":
        print(__doc__)
        return 2
    as_json = "--json" in argv
    paths = [{"path": str(Path(p).resolve())} for p in argv[1:] if p != "--json"]
    result = scan_system(paths)
    if as_json:
        print(json.dumps(result, indent=1))
        return 0
    names = [s["name"] for s in result["services"]]
    for s in result["services"]:
        techs = ", ".join(f"{t} {v['out']}->/{v['in']}<-" for t, v in sorted(s["techs"].items())) or "nothing found"
        print(f"{s['name']}: {s['files']} files · {techs}")
    print(f"\n{len(result['edges'])} connections ({result['seconds']}s)")
    for e in result["edges"]:
        chans = ", ".join(c["name"] for c in e["channels"][:4]) + (" ..." if e["count"] > 4 else "")
        print(f"  {names[e['source']]} -> {names[e['target']]}  [{e['tech']}]  {chans}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
