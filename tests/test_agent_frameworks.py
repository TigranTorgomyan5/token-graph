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

    def test_routes_cite_files_with_forward_slashes_on_every_os(self):
        lines = (SERVICES / 'laravel/routes/api.php').read_text().splitlines()
        windows = frameworks.file_routes('routes\\api.php', lines)
        self.assertEqual(windows, frameworks.file_routes('routes/api.php', lines))
        self.assertTrue(windows and all('routes/api.php:' in route for route in windows))

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


class FrameworkBodyTests(unittest.TestCase):
    """Request body type, its JSON field names and the response statuses, per framework, found by code."""

    CASES = [
        # (handler file, text on the handler's declaration line, body type, type file, JSON field names)
        ('fastapi/app/routers/orders.py', 'async def create_order', 'OrderIn', 'fastapi/app/routers/orders.py', ['sku', 'quantity']),
        ('nestjs/src/auth/auth.controller.ts', 'async login(', 'LoginDto', 'nestjs/src/auth/auth.controller.ts', ['phone', 'password']),
        ('aspnet/Api/Controllers/PaymentsController.cs', 'Charge(', 'ChargeRequest', 'aspnet/Api/Controllers/PaymentsController.cs', ['card', 'amount']),
        ('spring/src/main/java/com/example/shop/OrderController.java', 'place(', 'Order', 'spring/src/main/java/com/example/shop/Order.java', ['sku', 'quantity']),
        ('axum/src/main.rs', 'async fn create_entry', 'NewEntry', 'axum/src/main.rs', ['accountId', 'amountCents']),   # serde rename_all
    ]

    @staticmethod
    def body_at(path, needle):
        lines = (SERVICES / path).read_text().splitlines()
        number = next(n for n, text in enumerate(lines, 1) if needle in text and not text.strip().startswith(('@', '[', '#[')))
        return lines, [(number + offset, text) for offset, text in enumerate(frameworks.function_body(lines, number, 80))]

    def test_request_body_types_and_their_json_field_names(self):
        for path, needle, type_name, type_path, names in self.CASES:
            with self.subTest(framework=path.split('/')[0]):
                _, body = self.body_at(path, needle)
                self.assertEqual(type_name, (frameworks.request_body(path, body) or {}).get('type'))
                type_lines = (SERVICES / type_path).read_text().splitlines()
                pattern = frameworks.type_declaration(frameworks.language(type_path), type_name)
                line = next(n for n, text in enumerate(type_lines, 1) if pattern.search(text))
                self.assertEqual(names, [name for name, _, _ in frameworks.type_fields(type_path, type_lines, line)])

    def test_response_statuses_per_framework(self):
        cases = [('fastapi/app/routers/orders.py', 'async def cancel_order', 409),     # raise HTTPException(status_code=409)
                 ('aspnet/Api/Controllers/PaymentsController.cs', 'Charge(', 200),       # return Ok()
                 ('axum/src/main.rs', 'async fn create_entry', 201)]                     # (StatusCode::CREATED, Json(...))
        for path, needle, status in cases:
            with self.subTest(framework=path.split('/')[0]):
                _, body = self.body_at(path, needle)
                self.assertIn(status, [write['status'] for write in frameworks.response_writes(path, body)])

    def test_go_bodies_inline_grouped_embedded_and_writers(self):
        # Live nh-player-service-go register: inline struct with json tags; nh-gateway-go-sync initGameRequest:
        # grouped names on one line; a writer whose status is set inside it is returned for the caller to resolve.
        lines = ['func (m *Module) register(w http.ResponseWriter, r *http.Request) {', '\tvar req struct {',
                 '\t\tPhoneNumber string `json:"phoneNumber"`', '\t\tSecret string `json:"-"`', '\t\tAffiliate *string `json:"affiliate,omitempty"`',
                 '\t}', '\tif err := httpx.DecodeJSON(r, &req); err != nil {', '\t\tapierror.WriteValidation(w, r, nil)', '\t\treturn', '\t}',
                 '\tif req.PhoneNumber == "" {', '\t\tapierror.Write(w, r, http.StatusBadRequest, apierror.ValidationFailed, nil)', '\t\treturn', '\t}',
                 '\th.info(r, "done")', '\thttpx.WriteJSON(w, http.StatusOK, map[string]any{', '\t\t"sessionId": id,', '\t})', '}']
        body = [(n, text) for n, text in enumerate(lines, 1)]
        request = frameworks.request_body('register.go', body)
        self.assertEqual(2, request['inline'])
        self.assertEqual([('phoneNumber', 'string', 3), ('affiliate', '*string', 5)], frameworks.type_fields('register.go', lines, 2))
        writes = frameworks.response_writes('register.go', body)
        self.assertEqual([(8, None, 'apierror.WriteValidation'), (12, 400, 'apierror.Write'), (16, 200, 'httpx.WriteJSON')],
                         [(w['line'], w['status'], w['writer']) for w in writes])          # h.info(r, ...) is not a response
        self.assertEqual('req.PhoneNumber == ""', writes[1]['when'])
        self.assertEqual(['sessionId'], frameworks.response_keys('register.go', writes, body))   # multi-line map literal
        grouped = ['type initGameRequest struct {', '\tAuthToken, GameUrl string', '\tDemo bool', '\tbaseRequest', '}']
        self.assertEqual(['AuthToken', 'GameUrl', 'Demo', '*baseRequest'], [name for name, _, _ in frameworks.type_fields('r.go', grouped, 1)])

    def test_status_names_in_each_language(self):
        for text, status in [('http.StatusUnprocessableEntity', 422), ('StatusCodes.Status404NotFound', 404), ('HttpStatus.NOT_FOUND', 404),
                             ('StatusCode::CREATED', 201), ('status.HTTP_201_CREATED', 201), ('return NotFound();', 404),
                             ('throw new ConflictException("x")', 409), ('res.status(201).json(x)', 201), ('Results.Ok(x)', 200)]:
            self.assertEqual(status, frameworks.status_code(text), text)

    def test_multiline_signature_body_starts_after_it_opens(self):
        # Live nh-player-service-go dynamicValidate: the signature spans three lines.
        lines = ['func (m *Module) dynamicValidate(', '\tw http.ResponseWriter, r *http.Request,', ') (handled bool) {',
                 '\tapierror.Write(w, r, http.StatusServiceUnavailable, code, nil)', '\treturn true', '}', 'func next() {}']
        self.assertEqual(lines[:6], frameworks.function_body(lines, 1, 40))


class ReceiverFieldCallTests(unittest.TestCase):
    """Go calls through a receiver's fields: a.svc.Update -> the Update method of the field's type."""

    def test_receiver_struct_fields_and_field_calls(self):
        # Live nh-config-service-go: (a *API) upsert calls a.svc.Update; API has svc *configsvc.Service.
        self.assertEqual(('a', 'API'), frameworks.receiver('api.go', 'func (a *API) upsert(w http.ResponseWriter, r *http.Request, b binding, update bool) {'))
        self.assertIsNone(frameworks.receiver('api.go', 'func upsert(w http.ResponseWriter) {'))
        self.assertIsNone(frameworks.receiver('api.py', 'def upsert(self):'))
        struct = ['type API struct {', '\tsvc         *configsvc.Service', '\tpaybridge   *paybridge.Client', '\tlog, audit *logging.Logger',
                  '\tDeps', '\tdevelopment bool // set at start', '}', 'func next() {}']
        self.assertEqual({'svc': '*configsvc.Service', 'paybridge': '*paybridge.Client', 'log': '*logging.Logger', 'audit': '*logging.Logger',
                          'Deps': 'Deps', 'development': 'bool'}, frameworks.struct_field_types('api.go', struct, 1))
        body = ['\tif update {', '\t\te, err = a.svc.Update(ctx, b.root)', '\t} else {', '\t\te, err = a.svc.Create(ctx, b.root)',
                '\t}', '\t// a.svc.Delete(ctx) is a comment', '\tx.svc.Other()']
        self.assertEqual([('svc', 'Update'), ('svc', 'Create')], frameworks.field_calls('api.go', body, 'a'))
        self.assertTrue(frameworks.method_declares('service.go', 'func (s *Service) Update(ctx context.Context, value *jsonx.Node) (*Entity, error) {', 'Service', 'Update'))
        self.assertFalse(frameworks.method_declares('service.go', 'func (r *Repo) Update(ctx context.Context) error {', 'Service', 'Update'))


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
