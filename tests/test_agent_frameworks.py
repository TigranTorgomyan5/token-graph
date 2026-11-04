"""Framework adapters for the local agents: routes, lifecycle lines and route handlers per language.

Each tests/fixtures/services/<framework> folder is a small service written the way that framework is
used in practice (prefixes, groups, class-level routes, a client call that is not a route)."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'token-graph' / 'scripts'))
import agent_frameworks as frameworks  # noqa: E402
import explore_agent as explorer  # noqa: E402

SERVICES = ROOT / 'tests' / 'fixtures' / 'services'

EXPECTED_ROUTES = {
    'fastapi': {
        'GET /health — app/main.py:11',
        'POST /v1/orders/create — app/routers/orders.py:8',
        'GET /v1/orders/{order_id} — app/routers/orders.py:15',
        'POST/DELETE /v1/orders/{order_id}/cancel — app/routers/orders.py:20',
    },
    'flask': {'POST /users/login — shop/app.py:7', 'GET /users/me — shop/app.py:12', 'GET / — shop/app.py:17'},
    'express': {'GET /health — src/index.js:8', 'POST /add — src/routes/cart.js:4', 'GET/DELETE /items — src/routes/cart.js:5'},
    'nestjs': {'POST /auth/login — src/auth/auth.controller.ts:7', 'GET /auth — src/auth/auth.controller.ts:12'},
    'aspnet': {
        'POST /api/v1/Payments/charge — Api/Controllers/PaymentsController.cs:11',
        'GET /api/v1/Payments/{id} — Api/Controllers/PaymentsController.cs:18',
        'GET /status — Api/Controllers/PaymentsController.cs:21',
        'POST /api/refunds/create — Api/Program.cs:9',
        'GET /health — Api/Program.cs:10',
    },
    'spring': {
        'POST /api/orders/place — src/main/java/com/example/shop/OrderController.java:9',
        'GET /api/orders — src/main/java/com/example/shop/OrderController.java:14',
        'POST /api/orders/{id}/cancel — src/main/java/com/example/shop/OrderController.java:19',
    },
    'ktor': {'GET /health — src/main/kotlin/Application.kt:4', 'POST /api/login — src/main/kotlin/Application.kt:6'},
    'axum': {
        'GET/POST /v1/entries — src/main.rs:9',
        'GET /v1/entries/{id} — src/main.rs:10',
        'GET /metrics — src/main.rs:14',
        'GET /v1/legacy — src/main.rs:24',
    },
    'laravel': {'POST /invoices — routes/api.php:4', 'GET /invoices/{id} — routes/api.php:5'},
    'sinatra': {'GET /health — app.rb:4', 'POST /signup — app.rb:8'},
}

# label -> a fragment of the line it must be found on, per entry file.
EXPECTED_LIFECYCLE = {
    'fastapi/app/main.py': {'process entry': 'def main', 'server start': 'uvicorn.run', 'signal handling': 'signal.signal',
                            'shutdown': 'on_event("shutdown")', 'routes mounted': 'include_router'},
    'flask/shop/app.py': {'server start': 'app.run', 'routes mounted': 'register_blueprint'},
    'express/src/index.js': {'process entry': 'function main', 'server start': 'app.listen', 'signal handling': "process.on('SIGTERM'",
                             'routes mounted': "app.use('/api/cart'"},
    'nestjs/src/main.ts': {'process entry': 'NestFactory.create', 'server start': 'app.listen', 'shutdown': 'enableShutdownHooks'},
    'aspnet/Api/Program.cs': {'process entry': 'WebApplication.CreateBuilder', 'server start': 'app.Run()', 'routes mounted': 'MapControllers',
                              'signal handling': 'ApplicationStopping'},
    'spring/src/main/java/com/example/shop/ShopApplication.java': {'process entry': 'static void main', 'server start': 'SpringApplication.run',
                                                                    'signal handling': 'addShutdownHook'},
    'ktor/src/main/kotlin/Application.kt': {'process entry': 'fun main', 'server start': 'embeddedServer', 'routes mounted': 'routing {'},
    'axum/src/main.rs': {'process entry': 'fn main', 'server start': 'axum::serve', 'signal handling': 'tokio::signal',
                         'routes mounted': '.nest("/admin"'},
}

EXPECTED_HANDLERS = {
    'fastapi/app/routers/orders.py:8': 'create_order', 'flask/shop/app.py:7': 'login', 'express/src/routes/cart.js:4': 'addItem',
    'nestjs/src/auth/auth.controller.ts:7': 'login', 'aspnet/Api/Controllers/PaymentsController.cs:11': 'Charge',
    'aspnet/Api/Program.cs:9': 'CreateRefund', 'spring/src/main/java/com/example/shop/OrderController.java:9': 'place',
    'axum/src/main.rs:10': 'get_entry', 'axum/src/main.rs:14': 'metrics', 'axum/src/main.rs:24': 'legacy',
    'laravel/routes/api.php:4': 'store',
}


def service_files(name):
    folder = SERVICES / name
    return {str(path.relative_to(folder)): path.read_text().splitlines() for path in sorted(folder.rglob('*')) if path.is_file()}


class FrameworkRouteTests(unittest.TestCase):
    def test_each_framework_sample_yields_exactly_its_routes(self):
        for name, expected in EXPECTED_ROUTES.items():
            with self.subTest(framework=name):
                found = set()
                for filename, lines in service_files(name).items():
                    found.update(frameworks.file_routes(filename, lines, frameworks.string_constants('\n'.join(lines))))
                self.assertEqual(expected, found)

    def test_route_hints_run_each_language_only_on_its_own_files(self):
        # The whole sample set as one project: Python patterns must not fire on Go-like or JS files and so on.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(SERVICES, root, dirs_exist_ok=True)
            files = [str(path.relative_to(root)) for path in root.rglob('*') if path.is_file()]
            (root / 'graphify-out').mkdir()
            (root / 'graphify-out' / 'graph.json').write_text(json.dumps({'nodes': [{'source_file': f} for f in files]}))
            hints = set(explorer.endpoint_route_hints(root).splitlines())
        expected = {route.replace(' — ', f' — {name}/') for name, routes in EXPECTED_ROUTES.items() for route in routes}
        self.assertEqual(expected, hints)

    def test_outgoing_client_calls_and_comments_are_not_routes(self):
        every = '\n'.join(route for name in EXPECTED_ROUTES for f, lines in service_files(name).items()
                          for route in frameworks.file_routes(f, lines, frameworks.string_constants('\n'.join(lines))))
        for needle in ('/payments/charge', '/upstream/config', '/users/lookup', '/commented', '/old', '/not-a-route'):
            self.assertNotIn(needle, every)

    def test_java_package_named_example_is_production_code(self):
        from explorer_evidence import classify_source
        self.assertEqual('production', classify_source('src/main/java/com/example/shop/OrderController.java'))
        self.assertEqual('example', classify_source('examples/basic/main.go'))
        self.assertEqual('example', classify_source('docs-site/sample/app.py'))

    def test_unknown_languages_have_no_routes_or_lifecycle(self):
        self.assertEqual([], frameworks.file_routes('notes/readme.md', ['GET /x']))
        self.assertEqual('', frameworks.lifecycle_label('build.gradle', 'app.listen(3000)'))

    def test_string_constants_in_each_declaration_style(self):
        text = '\n'.join(['pub const METRICS_PATH: &str = "/metrics";', 'const string Route = "api/v1";', 'documentRoute = "/swagger/x"',
                          'private static final String BASE = "/api";', 'export const PREFIX = "/v2";', 'HEALTH = "/health"'])
        self.assertEqual({'METRICS_PATH': '/metrics', 'Route': 'api/v1', 'documentRoute': '/swagger/x', 'BASE': '/api',
                          'PREFIX': '/v2', 'HEALTH': '/health'}, frameworks.string_constants(text))


class FrameworkLifecycleTests(unittest.TestCase):
    def test_lifecycle_lines_are_found_in_each_framework_entry_file(self):
        for path, expected in EXPECTED_LIFECYCLE.items():
            with self.subTest(file=path):
                lines = (SERVICES / path).read_text().splitlines()
                found = {}
                for line in lines:
                    label = frameworks.lifecycle_label(path, line, True)
                    if label:
                        found.setdefault(label, []).append(line)
                for label, fragment in expected.items():
                    self.assertTrue(any(fragment in line for line in found.get(label, [])), f'{label}: {fragment} not in {found.get(label)}')

    def test_telemetry_shutdown_is_not_the_server_shutdown(self):
        # Live nh_identity: `_ = obs.Shutdown(sctx)` was listed as the service shutdown.
        self.assertEqual('telemetry flush', frameworks.lifecycle_label('internal/app/app.go', '\t_ = obs.Shutdown(sctx)'))
        self.assertEqual('telemetry flush', frameworks.lifecycle_label('cmd/main.go', '\ttracerProvider.Shutdown(ctx)'))
        self.assertEqual('shutdown', frameworks.lifecycle_label('cmd/main.go', '\tif err := srv.Shutdown(shutdownCtx); err != nil {'))
        self.assertEqual('telemetry flush', frameworks.lifecycle_label('src/main.rs', '    otel_provider.shutdown()'))

    def test_only_route_mounting_counts_outside_entry_files(self):
        self.assertEqual('', frameworks.lifecycle_label('internal/x/x.go', '\tsrv.ListenAndServe()', entry=False))
        self.assertEqual('routes mounted', frameworks.lifecycle_label('internal/x/x.go', '\tusers.Mount(api)', entry=False))

    def test_patterns_do_not_cross_languages(self):
        # A Go server start is not a Python one, and the reverse.
        self.assertEqual('', frameworks.lifecycle_label('app/main.py', 'srv.ListenAndServe()'))
        self.assertEqual('', frameworks.lifecycle_label('cmd/main.go', 'uvicorn.run(app)'))
        self.assertEqual('', frameworks.lifecycle_label('cmd/main.go', '// srv.ListenAndServe() is called below'))


class FrameworkHandlerTests(unittest.TestCase):
    def test_route_handlers_are_named_in_each_framework(self):
        for location, expected in EXPECTED_HANDLERS.items():
            with self.subTest(route=location):
                path, number = location.rsplit(':', 1)
                lines = (SERVICES / path).read_text().splitlines()
                self.assertEqual(expected, frameworks.handler_name(path, lines, int(number)))

    def test_called_names_include_calls_through_fields(self):
        # Live nh-wallet-adapter-go: e.Service.GetBalanceByWalletID(...) had no call edge in the graph.
        lines = ['func (e *endpoints) balance(w http.ResponseWriter, r *http.Request, req *balanceRequest) error {',
                 '\tresult, err := e.Service.GetBalanceByWalletID(r.Context(), req.PlayerID, val(req.WalletID))',
                 '\t// e.Old(x) is a comment', '\twriteOK(w, r, balanceDto(result))', '\treturn nil', '}',
                 'func next() {', '\tnotInBody()', '}']
        body = frameworks.function_body(lines, 1, 40)
        self.assertEqual(lines[:6], body)                     # stops at the closing brace
        self.assertEqual(['GetBalanceByWalletID', 'Context', 'val', 'writeOK', 'balanceDto'], frameworks.called_names('x.go', body))
        python = ['    def login(self, body):', '        user = self.repo.find_user(body.phone)', '        return ok(user)', '', '    def other(self):']
        self.assertEqual(['find_user', 'ok'], frameworks.called_names('x.py', frameworks.function_body(python, 1, 40)))

    def test_outcome_lines_keep_meaningful_exits_only(self):
        # Live nh-wallet-adapter-go: the technical-failure result was the step the model left out.
        body = [(239, 'func (s *Service) GetBalanceByWalletID(ctx context.Context) (BalanceResult, error) {'),
                (245, '\t\treturn BalanceResult{ReasonCode: toTechnicalFailure(ex)}, nil'),
                (248, '\t\treturn BalanceResult{}, err'), (249, '\t\treturn nil, err'), (250, '\treturn balanceFromResolved(resolved, walletID, currency), nil'),
                (251, '\t// return x is a comment')]
        self.assertEqual([245, 248, 250], [n for n, _ in frameworks.outcome_lines('internal/wallet/service.go', body)])
        python = [(1, 'def login(body):'), (2, '    if not user:'), (3, '        raise Unauthorized("bad password")'), (4, '    return None'),
                  (5, '    return issue_tokens(user)')]
        self.assertEqual([3, 5], [n for n, _ in frameworks.outcome_lines('app/auth.py', python)])
        self.assertEqual([], frameworks.outcome_lines('notes.md', python))

    def test_inline_handlers_have_no_name(self):
        lines = (SERVICES / 'express/src/index.js').read_text().splitlines()
        self.assertIsNone(frameworks.handler_name('express/src/index.js', lines, 8))   # app.get('/health', (req, res) => ...)


if __name__ == '__main__':
    unittest.main()
