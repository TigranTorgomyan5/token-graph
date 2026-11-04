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
    for text in lines[number:number - 1 + limit]:
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
    for text in body[1:]:
        if is_comment(lang, text):
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
