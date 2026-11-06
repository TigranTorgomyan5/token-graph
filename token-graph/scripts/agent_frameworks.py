"""Language and framework adapters for the local agents.

Everything that knows a language's syntax or a web framework's API lives here, chosen by file
extension, so a Go pattern never runs on a Python file and the agent core never names a framework.

    file_routes(filename, lines, constants) -> ["POST /v1/login — src/auth.go:12", ...]
    lifecycle_label(filename, line, entry)  -> "server start" / "signal handling" / ... or ""
    handler_name(filename, lines, number)   -> the function a route at that line dispatches to, if visible

Every result is a pattern match on a real source line: the line is real, the label is a guess.
Routes assembled across files (a router mounted under a prefix elsewhere) are not resolved.
"""
from __future__ import annotations
import re
from pathlib import Path

LANGUAGES = {
    '.go': 'go', '.py': 'python', '.js': 'js', '.mjs': 'js', '.cjs': 'js', '.ts': 'js', '.tsx': 'js', '.jsx': 'js',
    '.cs': 'csharp', '.java': 'jvm', '.kt': 'jvm', '.kts': 'jvm', '.rs': 'rust', '.php': 'php', '.rb': 'ruby',
}
VERBS = ('get', 'post', 'put', 'patch', 'delete', 'head', 'options')


def language(filename: str) -> str | None:
    return LANGUAGES.get(Path(filename).suffix.casefold())


def is_comment(lang: str | None, line: str) -> bool:
    text = line.lstrip()
    if lang in {'python', 'ruby'}:
        return text.startswith('#')
    if lang == 'php':
        return text.startswith(('//', '/*', '* ', '#')) and not text.startswith('#[')
    return text.startswith(('//', '/*', '* ', '*/')) or text == '*'


def join_path(*parts: str) -> str:
    """Join route segments the way routers do: one slash between parts, leading slash kept."""
    pieces = [part.strip('/') for part in parts if part and part.strip('/')]
    return '/' + '/'.join(pieces) if pieces else '/'


def _route(method: str, path: str, filename: str, number: int) -> str:
    return f'{method} {path} — {filename}:{number}'


def _next_declaration(lines: list[str], index: int, lang: str | None) -> str:
    """The first line after lines[index] that is not an annotation, attribute, decorator, comment or blank."""
    for text in lines[index + 1:index + 12]:
        stripped = text.strip()
        if not stripped or is_comment(lang, text) or stripped.startswith(('@', '[', '#[')):
            continue
        return stripped
    return ''


# ── Balanced-bracket helpers (C-like syntax) ─────────────────────────────────

def split_args(text: str, start: int) -> tuple[list[str], int] | None:
    """Split the comma-separated items between the bracket at text[start] and its match; None if unbalanced."""
    args, depth, current, index, quote = [], 0, [], start + 1, ''
    pairs = {'(': ')', '[': ']', '{': '}'}
    while index < len(text):
        char = text[index]
        if quote:
            if char == '\\':
                current.append(text[index:index + 2]); index += 2; continue
            if char == quote:
                quote = ''
        elif char in '"`\'':
            quote = char
        elif char in pairs:
            depth += 1
        elif char in ')]}':
            if depth == 0:
                args.append(''.join(current).strip())
                return [arg for arg in args if arg], index
            depth -= 1
        elif char == ',' and depth == 0:
            args.append(''.join(current).strip()); current = []; index += 1; continue
        current.append(char)
        index += 1
    return None


def call_args(line: str, name: str) -> list[list[str]]:
    """Argument lists of each call to name(...) on this line; not its definition or a method call."""
    calls = []
    for match in re.finditer(r'(?<![\w.])' + re.escape(name) + r'\s*\(', line):
        if re.search(r'\bfunc\s*$', line[:match.start()]) or re.match(r'\s*:?=\s*func', line[match.end() - 1:]):
            continue
        split = split_args(line, match.end() - 1)
        if split:
            calls.append(split[0])
    return calls


def _call_text(lines: list[str], index: int, limit: int = 4) -> str:
    """lines[index] joined with following lines until its parentheses balance."""
    parts, depth = [], 0
    for text in lines[index:index + limit]:
        parts.append(text.strip())
        depth += text.count('(') - text.count(')')
        if depth <= 0:
            break
    return ' '.join(parts)


# ── Go: net/http, chi, gorilla-style method calls and route helpers ──────────

GO_VERB = r'\.(Get|Post|Put|Patch|Delete|Head|Options)\(\s*'


def go_path_helpers(text: str) -> dict[str, tuple[int, int | None, str | None, str]]:
    """Go helpers that register a route for a path parameter, e.g.

        func post[P body](r chi.Router, log *slog.Logger, pattern, typeName string, ...) { r.Post("/"+pattern, ...) }
        cfg := func(method, path, name string, h http.HandlerFunc) { rt.Handle(method, "/"+path, ...) }

    Returns name -> (path parameter index, method parameter index or None, fixed method or None, path prefix).
    """
    helpers = {}
    for match in re.finditer(r'^func\s+(\w+)\s*(?:\[[^\]]*\])?\s*(?=\()|\b(\w+)\s*:?=\s*func\s*(?=\()', text, re.M):
        name = match.group(1) or match.group(2)
        signature = split_args(text, match.end())
        if not signature:
            continue
        params, close = signature
        names = [param.split()[0] for param in params]
        takes_router = any(re.search(r'Router|Mux', param) for param in params)
        body_start = text.find('{', close)
        if body_start < 0:
            continue
        body = split_args(text, body_start)   # also balances the body's { }
        body_text = text[body_start:body[1]] if body else text[body_start:body_start + 2000]
        for index, param in enumerate(names):
            path_arg = r'(?:"(/?)"\s*\+\s*)?' + re.escape(param) + r'\b'
            verb = re.search(GO_VERB + path_arg, body_text)
            fixed = re.search(r'\.(?:Method|MethodFunc|Handle|HandleFunc)\(\s*http\.Method(\w+)\s*,\s*' + path_arg, body_text)
            given = re.search(r'\.(?:Method|MethodFunc|Handle|HandleFunc)\(\s*(\w+)\s*,\s*' + path_arg, body_text)
            found = verb or fixed or given
            # r.Header.Get(name) is not a route: a helper must build "/"+path or take a router.
            if not found or not (found.group(2) or takes_router) or re.search(r'\b(?:Header|Query|Values|URL|Form)\(?\)?$', body_text[:found.start()]):
                continue
            if verb:
                helpers[name] = (index, None, verb.group(1).upper(), verb.group(2) or '')
            elif fixed:
                helpers[name] = (index, None, fixed.group(1).upper(), fixed.group(2) or '')
            elif given and given.group(1) in names:
                helpers[name] = (index, names.index(given.group(1)), None, given.group(2) or '')
            else:
                continue
            break
    return helpers


def _go_routes(filename: str, lines: list[str], constants: dict[str, str]) -> list[str]:
    routes = []
    text = '\n'.join(lines)
    # Helpers that register a route for a prefix they receive, e.g.
    #   func catchAll(r chi.Router, prefix string, h http.Handler) { r.Method(http.MethodPost, prefix, h) ... }
    prefix_helpers: dict[str, list[str]] = {}
    for match in re.finditer(r'^func (\w+)\(([^)]*)\)[^{]*\{(.*?)^\}', text, re.M | re.S):
        params = re.findall(r'(\w+)\s+string\b', match.group(2))
        methods = [m.upper() for param in params for m in re.findall(r'\.Method\(http\.Method(\w+)\s*,\s*' + re.escape(param) + r'\b', match.group(3))]
        if methods and re.search(r'Router|Mux', match.group(2)):
            prefix_helpers[match.group(1)] = list(dict.fromkeys(methods))
    path_helpers = {name: spec for name, spec in go_path_helpers(text).items() if name not in prefix_helpers}
    # chi sub-routers: r.Route("/v1/user", func(r chi.Router) { r.Post("/get", ...) }) registers /v1/user/get.
    depth, stack = 0, []

    def full(path: str) -> str:
        prefix = ''.join(part for part, _ in stack)
        return (prefix + ('' if path == '/' else path)) or '/' if prefix else path

    for number, line in enumerate(lines, 1):
        if is_comment('go', line):
            continue
        route_open = re.search(r'\.Route\(\s*"(/[^"]*)"\s*,\s*func', line)
        method = re.search(r'\.Method\(http\.Method(\w+)\s*,\s*"/"\s*\+\s*(\w+)', line)
        if method:
            routes.append(_route(method.group(1).upper(), full('/' + constants.get(method.group(2), '{' + method.group(2) + '}').lstrip('/')), filename, number))
        # Any receiver (sub-routers are often gr/sr/br); a leading "/" keeps out calls like Header.Get("Content-Type").
        # The literal must be the whole path: "/"+pattern inside a helper is handled by go_path_helpers.
        static = re.search(r'\b[A-Za-z_]\w*\.(Get|Post|Put|Patch|Delete|Head|Options|Handle|HandleFunc)\("(/[^"]*)"(?!\s*\+)', line) or re.search(
            r'\.With\(.*\)\.(Get|Post|Put|Patch|Delete|Head|Options|Handle)\("(/[^"]*)"(?!\s*\+)', line) or re.search(
            r'\.(?:Handle|HandleFunc|Method|MethodFunc)\(\s*http\.Method(\w+)\s*,\s*"(/[^"]*)"(?!\s*\+)', line)
        if static and not re.search(r'HandleFunc\("(/swagger/?)"\s*,', line):
            verb = static.group(1).upper()
            # Go 1.22 net/http patterns carry the method: mux.HandleFunc("POST /v1/x", h).
            routes.append(_route('HANDLE' if verb == 'HANDLEFUNC' else verb, full(static.group(2)), filename, number))
        mux = re.search(r'\.(?:Handle|HandleFunc)\(\s*"([A-Z]+) (/[^"]*)"', line)
        if mux:
            routes.append(_route(mux.group(1), full(mux.group(2)), filename, number))
        for name, (path_index, method_index, verb, prefix) in path_helpers.items():
            for args in call_args(line, name):
                literal = re.fullmatch(r'"([^"]*)"', args[path_index]) if path_index < len(args) else None
                if not literal:
                    continue
                if method_index is not None:
                    given = re.fullmatch(r'http\.Method(\w+)|"([A-Za-z]+)"', args[method_index]) if method_index < len(args) else None
                    verb = (given.group(1) or given.group(2)).upper() if given else None
                if verb:
                    path = prefix + literal.group(1)
                    routes.append(_route(verb, full(path if path.startswith('/') else '/' + path), filename, number))
        for name, methods in prefix_helpers.items():
            call = re.search(r'\b' + re.escape(name) + r'\(\s*\w+\s*,\s*"(/[^"]*)"', line)
            if call:
                path = full(call.group(1))
                routes.append(f'{"/".join(methods)} {path} (and {path.rstrip("/")}/* via {name}) — {filename}:{number}')
        depth += line.count('{') - line.count('}')
        if route_open:
            stack.append((route_open.group(1).rstrip('/'), depth))
        while stack and depth < stack[-1][1]:
            stack.pop()
        # Swagger UI served from embedded assets (documentRoute constant, "/swagger/"+name loop).
        if 'HandleFunc(documentRoute' in line:
            route = constants.get('documentRoute', '/swagger/{documentName}/swagger.json')
            routes.append(_route('GET', route.replace('{documentName}', constants.get('documentName', '{documentName}')), filename, number))
        if 'HandleFunc("/swagger/"+name' in line:
            routes.append(f'GET /swagger/{{asset}} (asset names listed in uiAssets) — {filename}:{number}')
        for path in re.findall(r'HandleFunc\("(/swagger/?)"\s*,', line):
            routes.append(f'GET/HEAD {path} (redirect) — {filename}:{number}')
    if Path(filename).name.casefold() == 'openapi.go':
        for number, line in enumerate(lines, 1):
            asset = re.match(r'\s*"([\w.-]+)"\s*:\s*\{', line)
            if asset:
                routes.append(f'GET /swagger/{asset.group(1)} (plus supported HEAD/asset methods) — {filename}:{number}')
    return routes


# ── Python: FastAPI, Flask, Starlette, aiohttp ───────────────────────────────

def _python_routes(filename: str, lines: list[str], constants: dict[str, str]) -> list[str]:
    routes, prefixes = [], {}
    for number, line in enumerate(lines, 1):
        if is_comment('python', line):
            continue
        # router = APIRouter(prefix="/users") / bp = Blueprint("users", __name__, url_prefix="/users")
        made = re.match(r'\s*(\w+)\s*(?::[^=]+)?=\s*(?:[\w.]+\.)?(?:APIRouter|Blueprint|Router)\((.*)', line)
        if made:
            prefix = re.search(r'\b(?:url_)?prefix\s*=\s*[rbu]?[\'"]([^\'"]*)', made.group(2))
            prefixes[made.group(1)] = prefix.group(1) if prefix else ''
        decorator = re.match(r'\s*@(\w+)\.(get|post|put|patch|delete|head|options|route|api_route|websocket)\(\s*[rbu]?[\'"]([^\'"]*)[\'"](.*)', line)
        if decorator:
            receiver, kind, path, rest = decorator.groups()
            methods = re.search(r'methods\s*=\s*[\[(]([^\])]*)', rest)
            verbs = (re.findall(r'[\'"](\w+)[\'"]', methods.group(1)) if methods else []) if kind in {'route', 'api_route'} else [kind]
            verb = '/'.join(v.upper() for v in verbs) or 'GET'
            routes.append(_route('WS' if kind == 'websocket' else verb, join_path(prefixes.get(receiver, ''), path), filename, number))
            continue
        # Starlette Route("/x", endpoint, methods=[...]); aiohttp web.get("/x", h) / app.router.add_post("/x", h).
        starlette = re.search(r'\bRoute\(\s*[\'"](/[^\'"]*)[\'"](.*)', line)
        if starlette:
            methods = re.search(r'methods\s*=\s*\[([^\]]*)', starlette.group(2))
            verb = '/'.join(v.upper() for v in re.findall(r'[\'"](\w+)[\'"]', methods.group(1))) if methods else 'GET'
            routes.append(_route(verb, starlette.group(1), filename, number))
        aio = re.search(r'\b(?:web\.|\.add_)(get|post|put|patch|delete|head|options)\(\s*[\'"](/[^\'"]*)', line)
        if aio:
            routes.append(_route(aio.group(1).upper(), aio.group(2), filename, number))
    return routes


# ── JavaScript / TypeScript: Express, Fastify, Hono, Koa-router, NestJS ──────

JS_ROUTER = r'(?:app|router|server|fastify|routes?|r|api|\w*[Rr]outer|\w*[Aa]pp)'
JS_CLIENTS = {'axios', 'http', 'https', 'fetch', 'client', 'request', 'superagent', 'ky', 'got', '$http', 'httpClient', 'this'}


def _js_routes(filename: str, lines: list[str], constants: dict[str, str]) -> list[str]:
    routes, controller = [], ''
    for number, line in enumerate(lines, 1):
        if is_comment('js', line):
            continue
        nest_class = re.match(r'\s*@Controller\(\s*(?:[\'"`]([^\'"`]*)[\'"`]|\{[^}]*path\s*:\s*[\'"`]([^\'"`]*))?', line)
        if nest_class:
            controller = nest_class.group(1) or nest_class.group(2) or ''
            continue
        nest = re.match(r'\s*@(Get|Post|Put|Patch|Delete|Head|Options|All)\(\s*(?:[\'"`]([^\'"`]*)[\'"`])?\s*\)', line)
        if nest:
            routes.append(_route(nest.group(1).upper(), join_path(controller, nest.group(2) or ''), filename, number))
            continue
        chained = re.search(r'\.route\(\s*[\'"`](/[^\'"`]*)[\'"`]\s*\)((?:\s*\.\s*(?:get|post|put|patch|delete|all)\s*\([^)]*\))+)', line)
        if chained:
            verbs = re.findall(r'\.\s*(get|post|put|patch|delete|all)\s*\(', chained.group(2))
            routes.append(_route('/'.join(v.upper() for v in verbs), chained.group(1), filename, number))
            continue
        for call in re.finditer(r'\b(' + JS_ROUTER + r')\.(get|post|put|patch|delete|head|options|all)\(\s*[\'"`](/[^\'"`]*)[\'"`]', line):
            if call.group(1) not in JS_CLIENTS:
                routes.append(_route(call.group(2).upper(), call.group(3), filename, number))
        # fastify.route({ method: 'POST', url: '/x' })
        fastify = re.search(r'\.route\(\s*\{.*?method\s*:\s*[\'"`](\w+)[\'"`].*?url\s*:\s*[\'"`](/[^\'"`]*)', line)
        if fastify:
            routes.append(_route(fastify.group(1).upper(), fastify.group(2), filename, number))
    return routes


# ── C#: ASP.NET Core controllers and minimal APIs ────────────────────────────

def _csharp_routes(filename: str, lines: list[str], constants: dict[str, str]) -> list[str]:
    routes, class_route, controller, groups = [], '', '', {}
    pending_class_route = None
    for number, line in enumerate(lines, 1):
        if is_comment('csharp', line):
            continue
        declaration = re.search(r'\bclass\s+(\w+)', line)
        if declaration:
            controller = re.sub(r'Controller$', '', declaration.group(1))
            class_route = (pending_class_route or '').replace('[controller]', controller)
            pending_class_route = None
        route_attr = re.search(r'\[\s*Route\(\s*"([^"]*)"', line)
        verb_attrs = re.findall(r'\bHttp(Get|Post|Put|Patch|Delete|Head|Options)(?:\(\s*"([^"]*)")?', line) if line.lstrip().startswith('[') else []
        if route_attr and not verb_attrs and re.search(r'\bclass\b', _next_declaration(lines, number - 1, 'csharp')):
            pending_class_route = route_attr.group(1)
            continue
        for verb, template in verb_attrs:
            template = template or (route_attr.group(1) if route_attr else '')
            template = template.replace('[controller]', controller).replace('[action]', '{action}')
            path = '/' + template.lstrip('~/') if template.startswith(('/', '~/')) else join_path(class_route, template)
            routes.append(_route(verb.upper(), path, filename, number))
        # Minimal APIs: var api = app.MapGroup("/api"); api.MapPost("/login", ...); app.MapGroup("/x").MapGet("/y", ...)
        group = re.search(r'\b(?:var|[\w<>]+)\s+(\w+)\s*=\s*(\w+)\.MapGroup\(\s*"([^"]*)"', line)
        if group:
            groups[group.group(1)] = join_path(groups.get(group.group(2), ''), group.group(3))
        for mapped in re.finditer(r'\b(\w+)((?:\.MapGroup\(\s*"[^"]*"\s*\))*)\.Map(Get|Post|Put|Patch|Delete|Methods)\(\s*"([^"]*)"', line):
            receiver, chain, verb, path = mapped.groups()
            inline = re.findall(r'MapGroup\(\s*"([^"]*)"', chain)
            routes.append(_route('MAP' if verb == 'Methods' else verb.upper(), join_path(groups.get(receiver, ''), *inline, path), filename, number))
    return routes


# ── Java / Kotlin: Spring MVC/WebFlux, JAX-RS, Ktor ──────────────────────────

def _jvm_routes(filename: str, lines: list[str], constants: dict[str, str]) -> list[str]:
    routes, class_prefix, stack, depth = [], '', [], 0
    pending_verb = None
    for number, line in enumerate(lines, 1):
        if is_comment('jvm', line):
            continue
        stripped = line.strip()
        mapping = re.match(r'@(Get|Post|Put|Patch|Delete|Request)Mapping\b\s*(?:\((.*))?', stripped)
        path_attr = re.match(r'@Path\(\s*"([^"]*)"', stripped)
        on_class = re.search(r'\b(?:class|interface|object)\b', _next_declaration(lines, number - 1, 'jvm'))
        if mapping:
            args = mapping.group(2) or ''
            path = re.search(r'(?:^|\b(?:value|path)\s*=\s*)\{?\s*"([^"]*)"', args)
            path = path.group(1) if path else ''
            if on_class and mapping.group(1) == 'Request':
                class_prefix = path
                continue
            verb = mapping.group(1).upper()
            if verb == 'REQUEST':
                given = re.findall(r'RequestMethod\.(\w+)', args)
                verb = '/'.join(given) or 'ANY'
            routes.append(_route(verb, join_path(class_prefix, path), filename, number))
            continue
        if path_attr and on_class:
            class_prefix = path_attr.group(1)
            continue
        jaxrs = re.match(r'@(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b', stripped)
        if jaxrs:
            pending_verb = (jaxrs.group(1), number)
            nearby = [re.match(r'@Path\(\s*"([^"]*)"', text.strip()) for text in lines[number:number + 3]]
            sub = next((m.group(1) for m in nearby if m), '')
            routes.append(_route(jaxrs.group(1), join_path(class_prefix, sub), filename, number))
            continue
        # Ktor: routing { route("/api") { post("/login") { ... } } }
        if Path(filename).suffix.casefold() in {'.kt', '.kts'}:
            opened = re.search(r'\broute\(\s*"([^"]*)"\s*\)\s*\{', line)
            call = re.search(r'\b(get|post|put|patch|delete|head|options)\(\s*"([^"]*)"\s*\)\s*\{', line)
            if call:
                routes.append(_route(call.group(1).upper(), join_path(*(p for p, _ in stack), call.group(2)), filename, number))
            depth += line.count('{') - line.count('}')
            if opened:
                stack.append((opened.group(1), depth))
            while stack and depth < stack[-1][1]:
                stack.pop()
    return routes


# ── Rust: Axum, Actix Web, Rocket, Warp-less routers ─────────────────────────

def _rust_routes(filename: str, lines: list[str], constants: dict[str, str]) -> list[str]:
    routes = []
    for number, line in enumerate(lines, 1):
        if is_comment('rust', line):
            continue
        attribute = re.match(r'\s*#\[(get|post|put|patch|delete|head|options)\(\s*"([^"]*)"', line)
        if attribute:
            routes.append(_route(attribute.group(1).upper(), attribute.group(2), filename, number))
            continue
        # .route("/x", get(h).post(h2)) (Axum) and .route("/x", web::post().to(h)) (Actix), possibly multi-line,
        # with the path as a literal or a const (.route(METRICS_PATH, any(scrape))).
        if re.search(r'\.route\(\s*(?:"|[A-Z_][A-Z0-9_]*\s*,|$)', line):
            call = _call_text(lines, number - 1, limit=6)
            route = re.search(r'\.route\(\s*(?:"(/[^"]*)"|([A-Z_][A-Z0-9_]*))\s*,(.*)', call)
            path = route and (route.group(1) or constants.get(route.group(2) or ''))
            if path:
                verbs = re.findall(r'(?:\bweb::|(?<![\w:]))(get|post|put|patch|delete|head|options|any)\s*\(', route.group(3))
                routes.append(_route('/'.join(dict.fromkeys(v.upper() for v in verbs)) or 'ROUTE', path, filename, number))
        resource = re.search(r'web::resource\(\s*"(/[^"]*)"\s*\)(.*)', line)
        if resource:
            verbs = re.findall(r'web::(get|post|put|patch|delete)\(', resource.group(2))
            routes.append(_route('/'.join(v.upper() for v in verbs) or 'ROUTE', resource.group(1), filename, number))
    return routes


# ── PHP: Laravel, Symfony; Ruby: Sinatra, Rails ──────────────────────────────

def _php_routes(filename: str, lines: list[str], constants: dict[str, str]) -> list[str]:
    routes = []
    for number, line in enumerate(lines, 1):
        if is_comment('php', line):
            continue
        laravel = re.search(r'Route::(get|post|put|patch|delete|options|any)\(\s*[\'"]([^\'"]+)', line)
        if laravel:
            routes.append(_route(laravel.group(1).upper(), join_path(laravel.group(2)), filename, number))
        symfony = re.search(r'(?:#\[|@)Route\(\s*[\'"]([^\'"]+)[\'"](.*)', line)
        if symfony:
            verbs = re.findall(r'[\'"](GET|POST|PUT|PATCH|DELETE)[\'"]', symfony.group(2), re.I)
            routes.append(_route('/'.join(v.upper() for v in verbs) or 'ROUTE', symfony.group(1), filename, number))
    return routes


def _ruby_routes(filename: str, lines: list[str], constants: dict[str, str]) -> list[str]:
    routes = []
    for number, line in enumerate(lines, 1):
        if is_comment('ruby', line):
            continue
        verb = re.match(r'\s*(get|post|put|patch|delete)\s+[\'"]([^\'"]+)[\'"]', line)
        if verb:
            routes.append(_route(verb.group(1).upper(), join_path(verb.group(2)), filename, number))
        resources = re.match(r'\s*resources?\s+:(\w+)', line)
        if resources:
            routes.append(_route('RESOURCES', '/' + resources.group(1), filename, number))
    return routes


def string_constants(text: str) -> dict[str, str]:
    """Named string constants a route may use instead of a literal, in each language's declaration syntax:
    Go/Python `name = "v"`, Rust `const NAME: &str = "v"`, C# `const string Name = "v"`,
    Java `static final String NAME = "v"`, JS/TS `const NAME = "v"`."""
    pattern = (r'^\s*(?:(?:pub(?:\([^)]*\))?|export|public|private|protected|internal|static|final|const|readonly|let|var|val)\s+)*'
               r'(?:(?:string|String|str)\s+)?(\w+)\s*(?::\s*[&\'\w\s<>]+)?=\s*"([^"]+)"')
    return dict(re.findall(pattern, text, re.M))


ROUTES = {'go': _go_routes, 'python': _python_routes, 'js': _js_routes, 'csharp': _csharp_routes,
          'jvm': _jvm_routes, 'rust': _rust_routes, 'php': _php_routes, 'ruby': _ruby_routes}


def file_routes(filename: str, lines: list[str], constants: dict[str, str] | None = None) -> list[str]:
    # Routes cite `file:line` with forward slashes on every OS (Windows paths come with `\`).
    filename = filename.replace('\\', '/')
    extractor = ROUTES.get(language(filename) or '')
    return extractor(filename, lines, constants or {}) if extractor else []


# ── Lifecycle: entry points, server start, signals, shutdown, route mounting ──

LIFECYCLE = {
    'go': {
        'process entry': r'^\s*func main\(\)',
        'server start': r'\bListenAndServe\w*\(|\bnet\.Listen\(|\b\w+\.Serve(?:TLS)?\(',
        'signal handling': r'\bsignal\.Notify(?:Context)?\(',
        'shutdown': r'\b\w+\.Shutdown\(',
        'routes mounted': r'^\s*\w+\.Mount\(|\)\.Mount\(',
    },
    'python': {
        'process entry': r'^\s*(?:async\s+)?def main\(|^if __name__ == [\'"]__main__[\'"]',
        'server start': r'\buvicorn\.run\(|\bserve_forever\(|\bweb\.run_app\(|\b(?:app|server)\.run\(|\bhypercorn\b.*\bserve\(',
        'signal handling': r'\bsignal\.signal\(|\badd_signal_handler\(',
        'shutdown': r'\bon_shutdown\b|\bshutdown_event\b|@\w+\.on_event\(\s*[\'"]shutdown|\bserver\.shutdown\(',
        'routes mounted': r'\binclude_router\(|\bregister_blueprint\(|\b\w+\.mount\(\s*[\'"]/',
    },
    'js': {
        'process entry': r'^\s*(?:export\s+)?(?:async\s+)?function (?:main|bootstrap|start)\(|\bNestFactory\.create\(',
        'server start': r'\.listen\(',
        'signal handling': r'\bprocess\.on\(\s*[\'"]SIG',
        'shutdown': r'\b(?:server|app|fastify)\.close\(|\benableShutdownHooks\(',
        'routes mounted': r'\b\w+\.use\(\s*[\'"`]/|\b(?:fastify|app|server)\.register\(|\b\w+\.route\(\s*[\'"`]/[^\'"`]*[\'"`]\s*,',
    },
    'csharp': {
        'process entry': r'^\s*(?:public\s+|internal\s+)?static\s+(?:async\s+)?(?:void|int|Task(?:<int>)?)\s+Main\(|\bWebApplication\.Create(?:Slim)?Builder\(|\bHost\.CreateDefaultBuilder\(',
        'server start': r'\b(?:app|host|builder)\.Run(?:Async)?\(|\.Build\(\)\.Run(?:Async)?\(|\b(?:app|host)\.Start(?:Async)?\(',
        'signal handling': r'\bConsole\.CancelKeyPress\b|\bApplicationStopping\b|\bAppDomain\.CurrentDomain\.ProcessExit\b',
        'shutdown': r'\bStopAsync\(|\bApplicationStopping\.Register\(',
        'routes mounted': r'\bMapControllers\(|\bMapGroup\(|\bUseEndpoints\(|\bMapHub<|\bMapGrpcService<',
    },
    'jvm': {
        'process entry': r'^\s*(?:public\s+)?static\s+void\s+main\(|^\s*fun main\(',
        'server start': r'\bSpringApplication\.run\(|\brunApplication<|\bembeddedServer\(|\.start\(\s*wait\s*=',
        'signal handling': r'\baddShutdownHook\(',
        'shutdown': r'@PreDestroy\b|\bsmartLifecycle\b|\.stop\(\s*\d',
        'routes mounted': r'^\s*routing\s*\{|\binstall\(\s*Routing\b',
    },
    'rust': {
        'process entry': r'^\s*(?:pub\s+)?(?:async\s+)?fn main\(|#\[(?:tokio|actix_web|async_std)::main\]',
        'server start': r'\baxum::serve\(|\bHttpServer::new\(|\.launch\(\)|\bTcpListener::bind\(|\bServer::bind\(|\.serve\(',
        'signal handling': r'\btokio::signal::|\bctrl_c\(\)|\bsignal\(\s*SignalKind',
        'shutdown': r'\bwith_graceful_shutdown\(|\.shutdown\(\)',
        'routes mounted': r'\.nest\(\s*"|\.merge\(|\.nest_service\(',
    },
    'php': {
        'process entry': r'\$app\s*=\s*.*::configure\(|\bnew\s+Kernel\(',
        'server start': r'\$app->run\(|\$kernel->handle\(',
        'routes mounted': r'Route::(?:prefix|group|middleware)\(',
    },
    'ruby': {
        'server start': r'\brun\s+\w|\bRack::Handler\b|\bPuma::',
        'routes mounted': r'\bmount\s+\w|\bRails\.application\.routes\.draw\b',
    },
}
# A shutdown of tracing/metrics/logging (otel providers, an obs bundle) flushes telemetry; it is not the server stopping.
TELEMETRY_SHUTDOWN = re.compile(r'\b(?:obs|otel\w*|telemetry|observability|tracer\w*|meter\w*|tp|mp|lp|\w*[Pp]rovider|metrics|sentry|logger|log)'
                                r'(?:\.\w+)*\.(?:Shutdown|shutdown|ForceFlush|force_flush|flush|Flush)\(')
_COMPILED = {lang: {label: re.compile(pattern) for label, pattern in table.items()} for lang, table in LIFECYCLE.items()}


def lifecycle_label(filename: str, line: str, entry: bool = True) -> str:
    """What a lifecycle-looking line does, by its file's language; '' if nothing matches.

    Only route mounting is reported outside entry files (startup code is in entry files)."""
    lang = language(filename)
    table = _COMPILED.get(lang or '')
    if not table or not line.strip() or is_comment(lang, line):
        return ''
    if TELEMETRY_SHUTDOWN.search(line):
        return 'telemetry flush' if entry else ''
    for label, pattern in table.items():
        if (entry or label == 'routes mounted') and pattern.search(line):
            return label
    return ''


# ── Route handler names ──────────────────────────────────────────────────────

_DECLARATION = {
    'python': re.compile(r'^\s*(?:async\s+)?def\s+(\w+)\s*\('),
    'rust': re.compile(r'^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+(\w+)'),
    'js': re.compile(r'^\s*(?:public\s+|private\s+|protected\s+)?(?:async\s+)?(\w+)\s*\('),
    'csharp': re.compile(r'\b(\w+)\s*(?:<[^>]*>)?\s*\((?![^)]*=>)'),
    'jvm': re.compile(r'\b(?:fun\s+)?(\w+)\s*\('),
    'php': re.compile(r'\bfunction\s+(\w+)\s*\('),
}
# A line that starts a function or method declaration in one of the supported languages.
DECLARATION_START = re.compile(r'^\s*(?:(?:export|default|pub(?:\([^)]*\))?|public|private|protected|internal|static|async|virtual|override|'
                               r'final|abstract|suspend|open)\s+)*(?:func|def|fn|function|fun)\b|^\s*(?:public|private|protected|internal)\s')
_NOT_NAMES = {'if', 'for', 'while', 'switch', 'return', 'new', 'await', 'async', 'catch', 'function', 'typeof', 'super', 'this'}


def handler_reference(filename: str, lines: list[str], number: int) -> tuple[str | None, str | None]:
    """(name, qualifier) of the function a route registered at lines[number - 1] runs, when the source shows it.

    Decorator/attribute routes (@app.post, [HttpPost], @GetMapping, #[get]) run the function declared
    below them; call-style routes (r.Post("/x", m.login), .route("/x", post(authentication::login))) pass it
    as the last plain name in the call. The qualifier is the module/object before it (authentication, cart,
    InvoiceController), which helps pick the right definition when several functions share the name."""
    lang = language(filename)
    if not 0 < number <= len(lines):
        return None, None
    line = lines[number - 1].strip()
    if line.startswith(('@', '[', '#[')) and lang in _DECLARATION:
        declaration = _next_declaration(lines, number - 1, lang)
        found = [name for name in _DECLARATION[lang].findall(declaration) if name not in _NOT_NAMES]
        return (found[0] if found else None), None
    call = _call_text(lines, number - 1, limit=6)
    if lang == 'php':   # Route::post('/x', [InvoiceController::class, 'store']) / 'InvoiceController@store'
        named = re.search(r'(\w+)::class\s*,\s*[\'"](\w+)[\'"]|[\'"](\w+)@(\w+)[\'"]', call)
        return ((named.group(2) or named.group(4)), (named.group(1) or named.group(3))) if named else (None, None)
    # The last name closed by the call's parentheses; trailing commas/parens of multi-line calls are allowed.
    names = re.findall(r'(?:\b(\w+)(?:\.|::))?([A-Za-z_]\w*)\s*\)[\s,)]*;?\s*$', call)
    if not names or names[-1][1] in _NOT_NAMES:
        return None, None
    qualifier, name = names[-1]
    return name, qualifier or None


def function_body(lines: list[str], number: int, limit: int) -> list[str]:
    """The lines of the function declared at lines[number - 1], up to limit: the body ends at the first
    later line indented no deeper than the declaration (a closing brace or the next declaration)."""
    if not 0 < number <= len(lines):
        return []
    indent = len(lines[number - 1]) - len(lines[number - 1].lstrip())
    body = [lines[number - 1]]
    # A signature can span lines (func (m *Module) dynamicValidate(\n\tw ..., r ...,\n) (handled bool) {): the body
    # starts after the line that opens it, not at the first line back at the declaration's indent.
    opened = '{' in lines[number - 1] or lines[number - 1].rstrip().endswith(':') or lines[number - 1].rstrip().endswith('=>')
    while not opened and len(body) < min(12, limit) and number - 1 + len(body) < len(lines):
        text = lines[number - 1 + len(body)]
        body.append(text)
        opened = '{' in text or text.rstrip().endswith(':') or text.rstrip().endswith('=>') or text.rstrip().endswith(';')
    start = number - 1 + len(body)
    for text in lines[start:number - 1 + limit]:
        if text.strip() and len(text) - len(text.lstrip()) <= indent:
            if text.strip()[:1] in '})]':
                body.append(text)
            break
        body.append(text)
    return body


def called_names(filename: str, body: list[str]) -> list[str]:
    """Names a function body calls, in order: obj.Method(...), pkg::func(...), func(...).

    Covers calls the graph may not resolve to an edge, such as a method on a struct field or an
    injected interface (e.Service.GetBalanceByWalletID(...))."""
    lang = language(filename)
    names = []
    # A one-line body calls on the declaration line itself: func (a *API) getHistory(...) { a.writeHistory(w, r) }.
    first = body[0].split('{', 1)[1] if body and '{' in body[0] else ''
    for text in [first] + body[1:]:
        if not text or is_comment(lang, text):
            continue
        names.extend(re.findall(r'(?:\.|::|\b)([A-Za-z_]\w*)\s*(?:<[^<>()]*>)?\s*\(', text))
    return [name for name in dict.fromkeys(names) if name not in _NOT_NAMES]


_EXIT = {
    'go': r'^\s*(?:return\b|panic\()', 'python': r'^\s*(?:return\b|raise\b)', 'js': r'^\s*(?:return\b|throw\b)',
    'csharp': r'^\s*(?:return\b|throw\b)', 'jvm': r'^\s*(?:return\b|throw\b)', 'php': r'^\s*(?:return\b|throw\b)',
    'rust': r'^\s*(?:return\b|Err\(|Ok\()', 'ruby': r'^\s*(?:return\b|raise\b)',
}
# return / return nil / return err / return "", false / return None: an exit that says nothing about the outcome.
_BARE_EXIT = re.compile(r'^\s*return\s*(?:(?:nil|null|None|err|error|e|ex|false|true|False|True|""|\'\'|0|ok|\(\))\s*,?\s*)*;?\s*$')


def outcome_lines(filename: str, numbered: list[tuple[int, str]], limit: int = 5) -> list[tuple[int, str]]:
    """The exits of a function that say something: returns that build a value or call something, raises and throws."""
    pattern = _EXIT.get(language(filename) or '')
    if not pattern:
        return []
    exits = [(number, text.strip()) for number, text in numbered[1:]
             if re.match(pattern, text) and not _BARE_EXIT.match(text) and not is_comment(language(filename), text)]
    return exits[:limit]


def handler_name(filename: str, lines: list[str], number: int) -> str | None:
    return handler_reference(filename, lines, number)[0]


# ── Calls through a receiver's fields (a.svc.Update -> configsvc.Service.Update) ──

def receiver(filename: str, declaration: str) -> tuple[str, str] | None:
    """(variable, type) of a method's receiver: func (a *API) updateConfig(...) -> ('a', 'API'). Go only."""
    if language(filename) != 'go':
        return None
    match = re.match(r'\s*func\s*\(\s*(\w+)\s+\*?([A-Za-z_]\w*)(?:\[[^\]]*\])?\s*\)', declaration)
    return (match.group(1), match.group(2)) if match else None


def struct_field_types(filename: str, lines: list[str], number: int) -> dict[str, str]:
    """Every field of the Go struct declared at lines[number - 1], exported or not: {name: type}.
    Embedded fields map their type name to itself."""
    if language(filename) != 'go' or not 0 < number <= len(lines):
        return {}
    fields: dict[str, str] = {}

    def add(stripped: str) -> None:
        embedded = re.match(r'\*?(?:\w+\.)?([A-Za-z_]\w*)\s*(?:`[^`]*`)?$', stripped)
        if embedded:
            fields.setdefault(embedded.group(1), stripped.split('`')[0].strip())
            return
        match = re.match(r'(\w+(?:\s*,\s*\w+)*)\s+([^`]+?)\s*(?:`[^`]*`)?$', stripped)
        if match:
            for name in match.group(1).split(','):
                fields.setdefault(name.strip(), match.group(2).strip())

    head = lines[number - 1].split('//')[0]
    inner = head.split('{', 1)[1] if '{' in head else ''
    if '}' in inner:                       # one line: type API struct{ svc *S; log *slog.Logger }
        for part in inner.rsplit('}', 1)[0].split(';'):
            if part.strip():
                add(part.strip())
        return fields
    depth = head.count('{') - head.count('}')
    for index in range(number, min(len(lines), number + 120)):
        stripped = lines[index].split('//')[0].strip()
        before, depth = depth, depth + stripped.count('{') - stripped.count('}')
        if depth <= 0:
            break
        # Only the struct's own fields: a nested struct's (opts struct { svc *mock.Svc }) are not the outer's.
        if before == 1 and stripped:
            add(stripped)
    return fields


def imports(filename: str, lines: list[str]) -> dict[str, str]:
    """{qualifier: import path} of a Go file: `configsvc "x/internal/config"` -> configsvc, a plain
    "x/internal/configsvc" -> its last segment (the one before a /vN major version)."""
    if language(filename) != 'go':
        return {}
    found, block = {}, False
    for text in lines:
        stripped = text.split('//')[0].strip()
        if stripped.startswith('import ('):
            block = True
            continue
        if block and stripped == ')':
            break
        spec = stripped[len('import'):].strip() if stripped.startswith('import ') else (stripped if block else '')
        match = re.fullmatch(r'(?:([A-Za-z_]\w*|\.)\s+)?"([^"]+)"', spec)
        if match and match.group(1) not in {'_', '.'}:
            segments = match.group(2).split('/')
            name = segments[-2] if len(segments) > 1 and re.fullmatch(r'v\d+', segments[-1]) else segments[-1]
            found[match.group(1) or name.replace('-', '_')] = match.group(2)
        if not block and stripped.startswith(('func ', 'type ', 'var ', 'const ')):
            break
    return found


def field_type_target(filename: str, field_type: str) -> tuple[str | None, str] | None:
    """(package qualifier or None for the same package, type name) of a field's type: *configsvc.Service ->
    ('configsvc', 'Service'); *Repository -> (None, 'Repository'). Maps, funcs and channels give None."""
    if language(filename) != 'go':
        return None
    match = re.fullmatch(r'(?:(\w+)\.)?([A-Za-z_]\w*)', re.sub(r'^[\s*&\[\]]+', '', field_type))
    return (match.group(1), match.group(2)) if match else None


STATUS_PARAMETER = re.compile(r'^(?:status|code|statuscode|status_code|httpstatus|httpcode)$', re.I)


def call_arguments(call: str, name: str) -> list[str]:
    """The top-level arguments of the call of name in call: WriteJSONStrict(w, resp.Status, games) ->
    ['w', 'resp.Status', 'games']."""
    start = re.search(r'\b' + re.escape(name) + r'\s*\(', call)
    if not start:
        return []
    args, depth, current = [], 0, ''
    for char in call[start.end():]:
        if char in '([{':
            depth += 1
        elif char in ')]}':
            if depth == 0:
                break
            depth -= 1
        if char == ',' and depth == 0:
            args.append(current.strip())
            current = ''
        else:
            current += char
    return args + ([current.strip()] if current.strip() else [])


def status_parameter(filename: str, signature: str) -> int | None:
    """Index of the parameter that carries the HTTP status in a writer's declaration, so the status is the
    caller's argument, not one the writer picks: func WriteJSON(w http.ResponseWriter, status int, body any) -> 1."""
    lang = language(filename)
    text = re.sub(r'^\s*func\s*\([^)]*\)\s*', 'func ', signature) if lang == 'go' else signature
    name = re.search(r'(\w+)\s*(?:<[^>]*>|\[[^\]]*\])?\s*\(', text)
    if not name:
        return None
    for index, part in enumerate(call_arguments(text, name.group(1))):
        # The parameter's own name: first word in Go/Python/Rust/TS (status int, status: int), last in C#/Java.
        words = re.findall(r'[A-Za-z_]\w*', part.split('=')[0].split(':')[0])
        candidates = words[:1] if lang in {'go', 'python', 'rust', 'js', 'php', 'ruby'} else words[-1:]
        if candidates and STATUS_PARAMETER.match(candidates[0].lstrip('$')):
            return index
    return None


# ── Tests that name a route (repo-scout "Pinned by") ──────────────────────────

_TEST_FUNCTION = {
    'go': re.compile(r'^\s*func\s+(?:\(\s*\w+\s+\*?\w+\s*\)\s*)?(Test\w+)\s*\('),      # TestX, and testify suite methods
    'python': re.compile(r'^\s*(?:async\s+)?def\s+(test_\w+)'),
    'js': re.compile(r'^\s*(?:it|test|describe)(?:\.\w+)?\(\s*[\'"`]([^\'"`]+)'),
    'csharp': re.compile(r'^\s*(?:public\s+)?(?:async\s+)?(?:void|Task)\s+(\w+)\s*\('),
    'jvm': re.compile(r'^\s*(?:public\s+)?(?:void|fun)\s+(\w+)\s*\('),
    'rust': re.compile(r'^\s*(?:async\s+)?fn\s+(\w+)\s*\('),
}
_HELPER_FUNCTION = {
    'go': re.compile(r'^\s*func\s+(?:\([^)]*\)\s*)?(\w+)\s*[\[(]'),
    'python': re.compile(r'^\s*(?:async\s+)?def\s+(\w+)'),
    'js': re.compile(r'^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)|^\s*(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*(?:async\s*)?\('),
}
_TABLE_ROW = {'go': re.compile(r'^\s*\{\s*"([^"]+)"\s*,')}       # table-driven test row: {"CaseName", "/path", ...}


def test_function(filename: str, line: str) -> tuple[str, str] | None:
    """('test', name) when line declares a test, ('helper', name) for any other function, else None.
    Go's TestMain is shared setup: ('helper', 'TestMain')."""
    lang = language(filename)
    test = _TEST_FUNCTION.get(lang or '')
    match = test.match(line) if test else None
    if match:
        name = next(group for group in match.groups() if group)
        return ('helper', name) if lang == 'go' and name == 'TestMain' else ('test', name)
    helper = _HELPER_FUNCTION.get(lang or '')
    match = helper.match(line) if helper else None
    return ('helper', next(group for group in match.groups() if group)) if match else None


def table_case(filename: str, line: str) -> str | None:
    """The case name of a table-driven test row on line, if it is one."""
    pattern = _TABLE_ROW.get(language(filename) or '')
    match = pattern.match(line) if pattern else None
    return match.group(1) if match else None


def field_calls(filename: str, body: list[str], variable: str) -> list[tuple[str, str]]:
    """(field, method) of each call through the receiver's fields: a.svc.Update(...) -> ('svc', 'Update')."""
    if language(filename) != 'go':
        return []
    calls = []
    for text in body:
        if not is_comment('go', text):
            calls += re.findall(r'\b' + re.escape(variable) + r'\.(\w+)\.(\w+)\s*\(', text)
    return list(dict.fromkeys(calls))


def method_declares(filename: str, line: str, type_name: str, method: str) -> bool:
    """Whether line declares method on type_name: func (s *Service) Update(...)."""
    if language(filename) != 'go':
        return False
    return bool(re.match(r'\s*func\s*\(\s*\w+\s+\*?' + re.escape(type_name) + r'(?:\[[^\]]*\])?\s*\)\s*' + re.escape(method) + r'\s*\(', line))


def is_interface_type(filename: str, line: str, type_name: str) -> bool:
    """Whether line declares type_name as an interface: type Limiter interface {."""
    if language(filename) != 'go':
        return False
    return bool(re.match(r'\s*type\s+' + re.escape(type_name) + r'\s+interface\b', line))


def method_receiver(filename: str, line: str, method: str) -> str | None:
    """The receiver type of a method declaration of that name on any type: func (l *redisLimiter) Check( -> 'redisLimiter'."""
    if language(filename) != 'go':
        return None
    match = re.match(r'\s*func\s*\(\s*\w+\s+\*?(\w+)(?:\[[^\]]*\])?\s*\)\s*' + re.escape(method) + r'\s*\(', line)
    return match.group(1) if match else None


def middleware_uses(filename: str, lines: list[str]) -> list[tuple[int, str | None, str]]:
    """(line, package qualifier or None, name) of each named middleware the file mounts on a router:
    api.Use(internalauth.Gate(...)) -> (n, 'internalauth', 'Gate'); app.use(requireAuth); app.UseMiddleware<Gate>().
    Inline functions (Use(func...)) have no name and give nothing."""
    lang = language(filename)
    pattern = {
        'go': r'\.Use\(\s*(?:(\w+)\.)?([A-Za-z_]\w*)',
        'js': r'\b(?:app|router|api|server|\w+Router)\.use\(\s*(?:(\w+)\.)?([A-Za-z_]\w*)',
        'csharp': r'\.UseMiddleware<\s*(?:(\w+)\.)?([A-Za-z_]\w*)',
        'python': r'add_middleware\(\s*(?:(\w+)\.)?([A-Za-z_]\w*)',
    }.get(lang or '')
    if not pattern:
        return []
    found = []
    for number, text in enumerate(lines, 1):
        if is_comment(lang, text):
            continue
        match = re.search(pattern, text)
        if match and match.group(2) not in {'func', 'function', 'async', 'req', 'request'}:
            found.append((number, match.group(1), match.group(2)))
    return found


# ── Request and response bodies ──────────────────────────────────────────────
#
# Found by code from the handler's own lines and the type definitions they name. A body type is
# reported with the fields its declaration shows (JSON names as the serializer writes them where the
# declaration says so); a response is each line that writes one, with the status that line sets.

from http import HTTPStatus  # noqa: E402

_STATUS_BY_NAME = {status.name.replace('_', '').casefold(): status.value for status in HTTPStatus}
_STATUS_BY_NAME.update({'ok': 200, 'requestentitytoolarge': 413, 'unprocessableentity': 422, 'teapot': 418})
# Framework result helpers whose name is the status: Ok(), NotFound(), ResponseEntity.ok(), NotFoundException.
_STATUS_HELPERS = {'ok': 200, 'created': 201, 'accepted': 202, 'nocontent': 204, 'badrequest': 400, 'unauthorized': 401,
                   'forbid': 403, 'forbidden': 403, 'notfound': 404, 'conflict': 409, 'unprocessableentity': 422,
                   'internalservererror': 500, 'serviceunavailable': 503}


def status_code(text: str) -> int | None:
    """The HTTP status a source fragment names: http.StatusBadRequest, StatusCodes.Status404NotFound,
    HttpStatus.NOT_FOUND, StatusCode::CREATED, status.HTTP_201_CREATED, a bare 3-digit code."""
    named = re.search(r'\b(?:http\.)?Status([A-Z]\w+)\b', text)
    if named and named.group(1).casefold() in _STATUS_BY_NAME:
        return _STATUS_BY_NAME[named.group(1).casefold()]
    # A bare 3-digit number is a status only as an argument next to a response word or a status/code name:
    # WriteHeader(404), c.JSON(500, ...), Status(404), status = 404, but not make([]byte, 512).
    bare = r'(?:\b(?:WriteHeader|Status|StatusCode|JSON|String|Send|SendStatus|AbortWithStatus|AbortWithStatusJSON|Error|abort|status|sendStatus)\s*\(\s*|\b(?:status|statusCode|status_code|code|Code)\s*[:=]+\s*)([1-5]\d\d)(?![\w.])|\breturn\b[^\n]*,\s*([1-5]\d\d)\s*;?\s*$'
    digits = re.search(r'\bStatus(\d{3})\w*|\bHTTP_(\d{3})_|' + bare, text)
    if digits:
        return int(next(group for group in digits.groups() if group))
    upper = re.search(r'\b(?:HttpStatus|StatusCode|HttpStatusCode)(?:\.|::)([A-Z][A-Za-z_]+)\b', text)
    if upper and upper.group(1).replace('_', '').casefold() in _STATUS_BY_NAME:
        return _STATUS_BY_NAME[upper.group(1).replace('_', '').casefold()]
    # return NotFound(), Results.Ok(...), and a package's helpers too: Go's return mvc.BadRequest(...), mvc.Ok(...)
    helper = re.search(r'(?:\bResults\.|\bResponseEntity\.|\breturn\s+(?:new\s+)?(?:[a-z]\w*\.)?|\bthrow\s+new\s+)(\w+?)(?:Result|Exception|ObjectResult)?\s*\(', text)
    if helper and helper.group(1).casefold() in _STATUS_HELPERS:
        return _STATUS_HELPERS[helper.group(1).casefold()]
    return None


def _camel(name: str) -> str:
    return name[:1].lower() + name[1:]


def type_fields(filename: str, lines: list[str], number: int) -> list[tuple[str, str, int]]:
    """(JSON name, type, line) of the fields declared by the type at lines[number - 1], as its language and
    serializer name them: Go json tags, C# camelCase or [JsonPropertyName], TS/Python/Java/Rust field names
    (serde rename and rename_all = "camelCase" respected)."""
    lang = language(filename)
    if not 0 < number <= len(lines):
        return []
    head = lines[number - 1]
    fields: list[tuple[str, str, int]] = []
    record = re.search(r'\brecord\s+\w+\s*\((.*)\)', head)
    if record and lang in {'csharp', 'jvm'}:          # positional record: record ChargeRequest(string Card, decimal Amount)
        for part in re.split(r',(?![^<]*>)', record.group(1)):
            bits = part.strip().split()
            if len(bits) >= 2:
                fields.append((_camel(bits[-1]) if lang == 'csharp' else bits[-1], ' '.join(bits[:-1]), number))
        return fields
    rename_all = any('rename_all' in text and 'camel' in text.casefold() for text in lines[max(0, number - 4):number - 1])
    depth, pending_name = 0, None
    for index in range(number - 1, min(len(lines), number + 80)):
        text = lines[index]
        depth += text.count('{') - text.count('}')
        if index == number - 1 and lang == 'python':
            depth = 1
        if lang == 'python' and index > number - 1 and text.strip() and len(text) - len(text.lstrip()) <= len(head) - len(head.lstrip()):
            break
        stripped = text.strip()
        if index > number - 1 and (depth <= 0 and lang != 'python'):
            break
        if index == number - 1 or not stripped or is_comment(lang, text):
            continue
        attribute = re.search(r'\[JsonPropertyName\(\s*"([^"]+)"|#\[serde\(\s*rename\s*=\s*"([^"]+)"|@JsonProperty\(\s*"([^"]+)"', stripped)
        if attribute and stripped.startswith(('[', '#[', '@')):
            pending_name = next(group for group in attribute.groups() if group)
            continue
        if lang == 'go':
            # A lone type name in a struct body is an embedding, exported or not (baseRequest, *pkg.Base).
            embedded = re.match(r'\*?([A-Za-z_]\w*(?:\.[A-Z]\w*)?)\s*(?:`[^`]*`)?\s*$', stripped)
            if embedded:                      # an embedded struct: its fields are the caller's to expand
                fields.append(('*' + embedded.group(1).split('.')[-1], 'embedded', index + 1))
                continue
            # One or several names sharing a type: AuthToken, GameUrl, Language string
            match = re.match(r'(\w+(?:\s*,\s*\w+)*)\s+([^`]+?)\s*(?:`([^`]*)`)?\s*$', stripped)
            if match and match.group(1)[0].isupper():
                tag = re.search(r'json:"([^",]*)', match.group(3) or '')
                if tag and tag.group(1) == '-':
                    continue
                names = [name.strip() for name in match.group(1).split(',')]
                for name in names:
                    if name[:1].isupper():
                        fields.append(((tag.group(1) if tag and tag.group(1) and len(names) == 1 else name), match.group(2).strip(), index + 1))
        elif lang == 'python':
            match = re.match(r'(\w+)\s*:\s*([^=#]+)', stripped)
            if match and not stripped.startswith(('def ', 'class ', '@')):
                fields.append((match.group(1), match.group(2).strip(), index + 1))
        elif lang == 'js':
            match = re.match(r'(?:readonly\s+|public\s+|private\s+)*(\w+)(\??)\s*[:!]\s*([^;=]+)', stripped)
            if match and not re.match(r'(?:constructor|get|set|static)\b', stripped):
                fields.append((match.group(1), match.group(3).strip() + ('?' if match.group(2) else ''), index + 1))
        elif lang == 'csharp':
            match = re.match(r'public\s+(?:required\s+)?([\w<>\[\]?, .]+?)\s+(\w+)\s*\{\s*get', stripped)
            if match:
                fields.append((pending_name or _camel(match.group(2)), match.group(1).strip(), index + 1))
        elif lang == 'jvm':
            match = re.match(r'(?:private|public|protected)?\s*(?:final\s+)?([\w<>\[\]?, .]+?)\s+(\w+)\s*(?:=.*)?;', stripped) or \
                    re.match(r'(?:val|var)\s+(\w+)\s*:\s*([^,=)]+)', stripped)
            if match and not stripped.startswith(('return', 'package', 'import')):
                name, kind = (match.group(2), match.group(1)) if stripped[:3] not in {'val', 'var'} else (match.group(1), match.group(2))
                fields.append((pending_name or name, kind.strip(), index + 1))
        elif lang == 'rust':
            match = re.match(r'(?:pub(?:\([^)]*\))?\s+)?(\w+)\s*:\s*([^,]+),?', stripped)
            if match:
                name = match.group(1)
                if rename_all:
                    name = re.sub(r'_(\w)', lambda m: m.group(1).upper(), name)
                fields.append((pending_name or name, match.group(2).strip(), index + 1))
        pending_name = None
    return fields


def type_declaration(lang: str | None, name: str) -> re.Pattern | None:
    """A pattern for the line that declares a body type named name, in its language."""
    word = re.escape(name)
    return {
        'go': re.compile(r'^\s*type\s+' + word + r'\s+struct\b'),
        'python': re.compile(r'^\s*class\s+' + word + r'\b'),
        'js': re.compile(r'^\s*(?:export\s+)?(?:class|interface)\s+' + word + r'\b|^\s*(?:export\s+)?type\s+' + word + r'\s*=\s*\{'),
        'csharp': re.compile(r'\b(?:class|record|struct)\s+' + word + r'\b'),
        'jvm': re.compile(r'\b(?:class|record|data\s+class)\s+' + word + r'\b'),
        'rust': re.compile(r'^\s*(?:pub(?:\([^)]*\))?\s+)?struct\s+' + word + r'\b'),
        'php': re.compile(r'\bclass\s+' + word + r'\b'),
    }.get(lang or '')


# Go handler parameters that are not a decoded request body: built-in types, the request/response pair, and
# types from packages that carry routing or infrastructure values (a path id, a clock, a logger).
GO_NON_BODY_TYPES = {'http.ResponseWriter', 'http.Request', 'context.Context', 'string', 'int', 'int8', 'int16', 'int32', 'int64',
                     'uint', 'uint8', 'uint16', 'uint32', 'uint64', 'float32', 'float64', 'bool', 'byte', 'rune', 'error', 'any',
                     'interface', 'struct', 'chan'}
GO_NON_BODY_PACKAGES = {'http', 'context', 'time', 'uuid', 'slog', 'log', 'zap', 'logrus', 'httprouter', 'chi', 'mux', 'gin', 'echo',
                        'fiber', 'sql', 'sqlx', 'pgx', 'redis', 'big', 'decimal', 'url', 'net', 'sync', 'atomic', 'otel', 'trace'}


def request_body(filename: str, body: list[tuple[int, str]]) -> dict | None:
    """How the handler reads its JSON body, from its own lines: {'type': name or None, 'inline': (start line,
    field lines) or None, 'decoder': the reading call, 'line': where, 'via': a binder function to resolve}.

    Go: DecodeJSON(r, &req) / json.NewDecoder(r.Body).Decode(&req) / mvc.Read[T](...) / bindX(w, r)
    (the binder's return type names the body); Python/FastAPI and TS/Nest: a typed body parameter;
    C#: a [FromBody] or complex action parameter; Java: @RequestBody; Rust: Json<T> extractor."""
    lang = language(filename)
    text = '\n'.join(line for _, line in body)
    if lang == 'go':
        signature = body[0][1] if body else ''
        for number, line in body:
            # The framework decoded the body and passes it in: issuePlayerToken(r *http.Request, b mvc.Body) asserts
            # req := b.(*issueRequest). Only an assertion on a parameter typed as a body or any.
            asserted = re.search(r'\b\w+\s*:?=\s*(\w+)\.\(\*?([A-Za-z_][\w.]*)\)', line)
            if asserted and re.search(r'\b' + re.escape(asserted.group(1)) + r'\s+(?:[\w.]*Body|any|interface\{\})\s*[,)]', signature):
                return {'type': asserted.group(2).split('.')[-1], 'decoder': line.strip()[:110], 'line': number}
            generic = re.search(r'\b[\w.]*Read\[(\w+)\]\(', line)
            if generic:
                return {'type': generic.group(1), 'decoder': line.strip()[:110], 'line': number}
            # Any decoder or binder that fills a variable: DecodeJSON(r, &req), json.Unmarshal(b, &req), bind(r, &req, ...)
            target = re.search(r'\b(?i:[\w.]*(?:decode|unmarshal|bind|read)\w*)\(\s*(?:[^,()&]*,\s*)?&(\w+)', line)
            typed = re.search(r'\b[\w.]*Read\(\s*r\s*,\s*\w+\s*,\s*(\w+)\s*\)', line)
            if typed and not target:          # mvc.Read(r, loginModel, validateLogin): the validator's parameter names T
                return {'type': None, 'validator': typed.group(1), 'decoder': line.strip()[:110], 'line': number}
            if target:
                var = target.group(1)
                for start, decl in body:
                    inline = re.search(r'\bvar\s+' + re.escape(var) + r'\s+struct\s*\{', decl)
                    if inline:
                        return {'type': None, 'inline': start, 'decoder': line.strip()[:110], 'line': number}
                    named = re.search(r'\bvar\s+' + re.escape(var) + r'\s+\*?([\w.]+)\s*$', decl) or \
                            re.search(r'\b' + re.escape(var) + r'\s*:=\s*&?([\w.]+)\{', decl)
                    if named:
                        return {'type': named.group(1).split('.')[-1], 'decoder': line.strip()[:110], 'line': number}
                return {'type': None, 'decoder': line.strip()[:110], 'line': number}
            binder = re.search(r'\b\w+\s*(?:,\s*\w+)?\s*:?=\s*((?i:bind|read|decode|parse)\w*)\(\s*(?:w\s*,\s*)?r\b', line)
            if binder:
                return {'type': None, 'via': binder.group(1), 'decoder': line.strip()[:110], 'line': number}
        # A handler that receives the decoded body as a parameter: balance(w, r, req *balanceRequest) error
        signature = ' '.join(line for _, line in body[:3]).split('{', 1)[0]
        if re.search(r'\b\w+\s+[\w.]*Body\s*[,)]', signature):
            return None       # a generic body interface (mvc.Body) with no assertion names no type
        params = re.search(r'\w+\s*\(([^()]*(?:\([^()]*\)[^()]*)*)\)\s*[^()]*$', signature.split(')', 1)[-1] if signature.lstrip().startswith('func (') else signature)
        for name, kind in re.findall(r'(\w+)\s+\*?([A-Za-z_][\w.]*)', params.group(1) if params else ''):
            if kind not in GO_NON_BODY_TYPES and kind.split('.')[0] not in GO_NON_BODY_PACKAGES and not kind.startswith('func'):
                return {'type': kind.split('.')[-1], 'decoder': f'parameter {name}', 'line': body[0][0]}
    elif lang == 'python':
        signature = body[0][1] if body else ''
        for name, kind in re.findall(r'(\w+)\s*:\s*([A-Z]\w+)', signature):
            if kind not in {'Request', 'Response', 'Session', 'Depends', 'Optional', 'List', 'Dict', 'Any', 'AsyncSession'}:
                return {'type': kind, 'decoder': signature.strip()[:110], 'line': body[0][0]}
        keys = re.findall(r'\b(?:request\.json|request\.get_json\(\)|data|payload|body)\s*(?:\[\s*[\'"](\w+)[\'"]\s*\]|\.get\(\s*[\'"](\w+)[\'"])', text)
        if keys:
            return {'type': None, 'keys': list(dict.fromkeys(a or b for a, b in keys)), 'decoder': 'request JSON keys used', 'line': body[0][0]}
    elif lang == 'js':
        for number, line in body[:3]:
            dto = re.search(r'@Body\(\s*\)\s*\w+\s*:\s*(\w+)', line)
            if dto:
                return {'type': dto.group(1), 'decoder': line.strip()[:110], 'line': number}
        keys = re.findall(r'\breq\.body\.(\w+)', text) + [key.strip().split(':')[0].strip() for group in
                                                           re.findall(r'\{([^}]*)\}\s*=\s*req\.body', text) for key in group.split(',')]
        if keys:
            return {'type': None, 'keys': list(dict.fromkeys(key for key in keys if key)), 'decoder': 'req.body fields used', 'line': body[0][0]}
    elif lang == 'csharp':
        signature = ' '.join(line for _, line in body[:2])
        param = re.search(r'\[FromBody\]\s*([A-Z]\w+)', signature) or re.search(r'\(\s*([A-Z]\w+)\s+\w+\s*[,)]', signature)
        if param and param.group(1) not in {'HttpContext', 'CancellationToken', 'String', 'Guid', 'Int32'}:
            return {'type': param.group(1), 'decoder': signature.strip()[:110], 'line': body[0][0]}
    elif lang == 'jvm':
        signature = ' '.join(line for _, line in body[:3])
        param = re.search(r'@RequestBody\s+(?:@\w+\s+)*([A-Z]\w+)', signature)
        if param:
            return {'type': param.group(1), 'decoder': signature.strip()[:110], 'line': body[0][0]}
    elif lang == 'rust':
        signature = ' '.join(line for _, line in body[:4])
        param = re.search(r'Json\s*\(\s*\w+\s*\)\s*:\s*Json\s*<\s*(\w+)\s*>', signature) or re.search(r'web::Json<\s*(\w+)\s*>', signature)
        if param:
            return {'type': param.group(1), 'decoder': signature.strip()[:110], 'line': body[0][0]}
    return None


_WRITER = {
    # A line that writes a response: Go writers take the ResponseWriter first; the others return or raise.
    'go': re.compile(r'\b([\w.]*\w)\(\s*w\s*[,)]|\bw\.WriteHeader\(|\bhttp\.(?:Error|Redirect)\('),
    'python': re.compile(r'\braise\s+HTTPException\(|\b(?:JSONResponse|Response|jsonify|abort)\(|^\s*return\s+[\[{(]|^\s*return\s+\w'),
    'js': re.compile(r'\bres\.(?:status|json|send|sendStatus|end)\(|\breply\.(?:code|send)\(|\bthrow\s+new\s+\w+Exception\(|\bc\.json\(|^\s*return\s+'),
    'csharp': re.compile(r'\breturn\s+(?:new\s+)?\w+\(|\bResults\.\w+\(|\bTypedResults\.\w+\(|\bthrow\s+new\s+\w+Exception\('),
    'jvm': re.compile(r'\bResponseEntity\.|\bthrow\s+new\s+ResponseStatusException\(|^\s*return\s+'),
    'rust': re.compile(r'\bStatusCode::\w+|\bJson\(|^\s*(?:return\s+)?(?:Ok|Err)\('),
    'php': re.compile(r'\bresponse\(\)->json\(|\babort\(|^\s*return\s+'),
    'ruby': re.compile(r'\b(?:render|status|halt|json)\b'),
}


def response_writes(filename: str, body: list[tuple[int, str]], limit: int = 12) -> list[dict]:
    """Each line of the handler that writes a response: {'line', 'status' (int or None), 'writer', 'text', 'when'}.

    'when' is the nearest enclosing if-condition, quoted. A writer whose status is not on the line (Go's
    apierror.WriteValidation(w, r, nil)) is returned with status None and its name, for the caller to resolve."""
    lang = language(filename)
    pattern = _WRITER.get(lang or '')
    if not pattern:
        return []
    out = []
    for index, (number, line) in enumerate(body[1:], 1):
        # Go handlers that return a framework result write it through the framework: return mvc.Ok(body, t), nil.
        # Only one whose status is named, so return s.store.Get(ctx) is not a response.
        returned = lang == 'go' and re.match(r'\s*return\s+[a-z]\w*\.[A-Z]\w*\(', line) and status_code(line)
        if is_comment(lang, line) or not (pattern.search(line) or returned):
            continue
        if lang in {'python', 'js', 'jvm', 'php'} and re.match(r'\s*return\b', line) and _BARE_EXIT.match(line):
            continue
        writer = pattern.search(line).group(1) if lang == 'go' and pattern.search(line) else None
        if writer and HTTP_NON_WRITERS.search(writer):
            continue
        status = status_code(line)
        if status is None and lang == 'js' and re.search(r'\bres\.json\(|\bc\.json\(', line):
            status = 200
        if status is None and lang == 'rust' and re.search(r'^\s*(?:return\s+)?Ok\(\s*Json\(', line):
            status = 200
        indent = len(line) - len(line.lstrip())
        when = None
        # The enclosing condition is the nearest line above at a lower indent, and only if it opens an if/case:
        # an earlier, already closed if (if req.ReasonCode != nil { ... }) is not it. A multi-line condition
        # (if err := m.store.Insert(ctx,\n ...); err != nil {) is quoted by its first line and marked as cut.
        for _, previous in reversed(body[1:index]):
            # A brace on its own line (C#, Allman-style Java/PHP) opens the block of the line above it.
            if not previous.strip() or previous.strip() == '{' or len(previous) - len(previous.lstrip()) >= indent:
                continue
            opener = re.match(r'\s*(?:\}\s*else\s+)?(?:if|elif|else\s+if)\b\s*(.+?)\s*$|\s*case\s+(.+?):\s*$', previous)
            if opener:
                when = (opener.group(1) or opener.group(2)).strip()
                # Whole when it opens its block on the line, or its parentheses close (C# `if (x)`, Ruby `if x`
                # has none to close); cut when they stay open (Go's if err := m.store.Insert(ctx,).
                complete = when.endswith(('{', ':')) or (when.count('(') == when.count(')') and not re.search(r'(?:,|&&|\|\||\band|\bor)$', when))
                when = when.rstrip('{:').strip()
                if when.startswith('(') and when.endswith(')'):     # C-like if (cond) { ... }
                    when = when[1:-1].strip()
                when = when[:90] + ('' if complete else ' …')
            break
        # The whole call, joined until its parentheses close: map[string]any{ "messageSent": true, ... })
        call, depth = [line.strip()], line.count('(') - line.count(')')
        for _, following in body[index + 1:index + 8]:
            if depth <= 0:
                break
            call.append(following.strip())
            depth += following.count('(') - following.count(')')
        out.append({'line': number, 'status': status, 'writer': writer, 'text': line.strip()[:120], 'when': when,
                    'call': ' '.join(call)[:600]})
        if len(out) >= limit:
            break
    return out


# Calls that take the ResponseWriter but only log or read (h.info(r, ...), r.Header.Get) are not responses.
HTTP_NON_WRITERS = re.compile(r'(?:^|\.)(?:log|info|warn|debug|Info|Warn|Debug|Log|Error\w*Context|InfoContext|WarnContext|With\w*|Header|ServeHTTP|Use)$')


def response_keys(filename: str, writes: list[dict], body: list[tuple[int, str]] | None = None) -> list[str]:
    """JSON keys a success response visibly carries: literal keys in the writing call, or, when it writes a
    variable (WriteJSON(w, 200, out)), the keys that variable is built with: out := map...{"k": ...}, out["k"] = ..."""
    keys = []
    for write in writes:
        if write['status'] not in (None, 200, 201):
            continue
        call = write.get('call', write['text'])
        keys += re.findall(r'["\'](\w+)["\']\s*:', call)
        variable = re.search(r',\s*([a-z_]\w*)\s*\)+\s*;?\s*$', call)
        if variable and body:
            name = re.escape(variable.group(1))
            for _, text in body:
                if re.search(r'\b' + name + r'\s*:?=', text):
                    keys += re.findall(r'["\'](\w+)["\']\s*:', text)
                keys += re.findall(r'\b' + name + r'\[\s*["\'](\w+)["\']\s*\]\s*=', text)
    return list(dict.fromkeys(keys))
