"""Systems: services in any language are linked by how they talk (NATS, Kafka, HTTP, gRPC, MassTransit)."""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'token-graph' / 'scripts'))
import system_links  # noqa: E402
import explorer_server  # noqa: E402

FIXTURE = ROOT / 'tests' / 'fixtures' / 'system'
SERVICES = ['Adastra', 'Bet.Api', 'wallet-service', 'notifier']


def edges(result):
    names = [s['name'] for s in result['services']]
    return {(names[e['source']], names[e['target']], e['tech']): [c['name'] for c in e['channels']] for e in result['edges']}


class ScannerTests(unittest.TestCase):
    def test_service_setting_does_not_link_sibling_implementation(self):
        files = [('config.go', '.go', 'package config\nfunc Load() { base := req("SPORT_SERVICE_URI"); _ = base }')]
        endpoints = system_links.extract(files, system_links.collect_consts(files))
        services = [{'name': 'nh-sport', 'scan': {'endpoints': endpoints}},
                    {'name': 'nh-sport-go', 'scan': {'endpoints': []}}]
        self.assertEqual(system_links.link(services), [])
        services[0]['scan']['endpoints'], services[1]['scan']['endpoints'] = [], endpoints
        self.assertEqual(system_links.link(services), [])

    def test_go_config_service_setting_links_only_when_target_is_unambiguous(self):
        files = [('config.go', '.go', 'package config\nfunc Load() { base := req("CONFIG_SERVICE_URI"); _ = base }')]
        gateway = {'name': 'nh-gateway-go', 'scan': {'endpoints': system_links.extract(files, system_links.collect_consts(files))}}
        target = lambda name: {'name': name, 'scan': {'endpoints': []}}
        edges = system_links.link([gateway, target('nh-config-service-go')])
        self.assertEqual([(e['source'], e['target'], e['confidence']) for e in edges], [(0, 1, ['config key'])])
        self.assertEqual(edges[0]['channels'][0]['from'][0]['line'], 2)
        self.assertEqual(system_links.link([gateway, target('nh-config-service-go'), target('nh-config-service')]), [])

    def test_routes_without_destination_do_not_link_to_shared_or_suffix_matches(self):
        def service(name, source):
            files = [(name + '.go', '.go', source)]
            return {'name': name, 'scan': {'endpoints': system_links.extract(files, system_links.collect_consts(files))}}
        caller = service('caller', 'func run() { http.NewRequest(http.MethodGet, "/api/v1/users", nil) }')
        first = service('first', 'func routes(r Router) { r.Get("/api/v1/users", handler) }')
        second = service('second', 'func routes(r Router) { r.Get("/api/v1/users", handler) }')
        self.assertEqual(system_links.link([caller, first, second]), [])
        suffix = service('suffix', 'func routes(r Router) { r.Get("/internal/api/v1/users", handler) }')
        self.assertEqual(system_links.link([caller, suffix]), [])
        self.assertEqual(len(system_links.link([caller, first])), 1)

    def test_go_http_helper_follows_route_arguments(self):
        source = '''package gateway
const tokenPath = "api/v1/auth/token"
func (c *Client) issue(ctx context.Context) { c.token(ctx, tokenPath) }
func (c *Client) token(ctx context.Context, path string) { c.send(ctx, path) }
func (c *Client) send(ctx context.Context, path string) { http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+path, nil) }
'''
        files = [('client.go', '.go', source)]
        endpoints = system_links.extract(files, system_links.collect_consts(files))
        outgoing = [e for e in endpoints if e['tech'] == 'http' and e['role'] == 'out']
        self.assertEqual([(e['channel'], e['line']) for e in outgoing], [('api/v1/auth/token', 3)])

    def test_env_endpoint_is_redacted_and_links_by_host(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gateway, sport = root / 'gateway', root / 'sport-go'
            gateway.mkdir(); sport.mkdir()
            (gateway / '.env.local').write_text('SPORT_URL=http://sport-go:8080/private?token=secret\nAPI_KEY=secret-value\n')
            result = system_links.scan_system([{'path': str(gateway)}, {'path': str(sport)}])
            self.assertEqual(edges(result)[('gateway', 'sport-go', 'http')], ['SPORT_URL'])
            self.assertNotIn('secret', json.dumps(result))
            scan = system_links.scan_service(gateway)
            self.assertNotIn('secret', repr(scan))

    def test_ambiguous_service_name_does_not_pick_first(self):
        self.assertIsNone(system_links.service_for_tokens(['sport'], [['sport'], ['sport']], -1))

    def test_go_method_routes_and_http_requests_link(self):
        server = [('controller.go', '.go', 'const TokenRoute = "api/v1/auth/token"\nfunc mount(r chi.Router) { r.Method(http.MethodPost, "/"+TokenRoute, handler) }')]
        client = [('client.go', '.go', 'func send(ctx context.Context) { http.NewRequestWithContext(ctx, http.MethodPost, "/api/v1/auth/token", nil) }')]
        services = []
        for name, files in [('gateway', client), ('sport', server)]:
            endpoints = system_links.extract(files, system_links.collect_consts(files))
            services.append({'name': name, 'scan': {'endpoints': endpoints}})
        result = system_links.link(services)
        self.assertEqual([(e['source'], e['target'], e['tech']) for e in result], [(0, 1, 'http')])
        self.assertEqual(result[0]['channels'][0]['name'], '/api/v1/auth/token')

    @classmethod
    def setUpClass(cls):
        cls.result = system_links.scan_system([{'path': str(FIXTURE / s)} for s in SERVICES])
        cls.edges = edges(cls.result)

    def test_links_every_technology_in_the_fixture(self):
        self.assertEqual(set(self.edges), {
            ('Adastra', 'Bet.Api', 'nats'), ('Adastra', 'Bet.Api', 'http'), ('Adastra', 'Bet.Api', 'grpc'),
            ('Adastra', 'Bet.Api', 'masstransit'), ('Adastra', 'wallet-service', 'nats'),
            ('Adastra', 'wallet-service', 'http'), ('wallet-service', 'notifier', 'kafka'),
            ('notifier', 'wallet-service', 'http')})

    def test_constants_wildcards_and_routes_are_resolved(self):
        self.assertEqual(self.edges[('Adastra', 'Bet.Api', 'nats')], ['bets.placed'])        # Subjects.BetPlaced
        self.assertEqual(self.edges[('Adastra', 'wallet-service', 'nats')], ['wallet.*.debit'])  # $"wallet.{market}.debit"
        self.assertEqual(self.edges[('Adastra', 'Bet.Api', 'http')], ['api/bets/{id:guid}'])  # [Route] + [HttpGet]
        self.assertEqual(self.edges[('wallet-service', 'notifier', 'kafka')], ['wallet-events'])  # NATS + Kafka in one file

    def test_evidence_points_at_both_sides(self):
        edge = next(e for e in self.result['edges'] if e['tech'] == 'grpc')
        channel = edge['channels'][0]
        self.assertEqual(channel['from'][0]['file'], 'src/OddsClient.cs')
        self.assertEqual(channel['to'][0]['file'], 'Grpc/BetGrpcService.cs')
        self.assertGreater(channel['to'][0]['line'], 0)

    def test_health_routes_and_tests_are_ignored(self):
        self.assertNotIn('/health', json.dumps(self.result['edges']))

    def test_discover_lists_service_folders(self):
        self.assertEqual([s['name'] for s in system_links.discover_services(FIXTURE)], sorted(SERVICES, key=str.lower))

    def test_route_matching(self):
        seg = system_links.route_segments
        self.assertEqual(system_links.segs_match(seg('api/bets/*'), seg('/api/bets/{id}')), 2)
        self.assertEqual(system_links.segs_match(seg('bets/42'), seg('/api/bets/{id}')), 1)
        self.assertEqual(system_links.segs_match(seg('/health'), seg('/health')), 0)
        self.assertTrue(system_links.subject_match('orders.eu.created', 'orders.>'))
        self.assertFalse(system_links.subject_match('orders.created', 'payments.created'))


def fake_graphs(root):
    """Small code graphs for two services (the real engine is not needed for linking)."""
    graphs = {
        'Adastra': {'nodes': [{'id': 'pub', 'label': 'BetPublisher', 'source_file': 'src/BetPublisher.cs', 'source_location': 'L11'},
                              {'id': 'place', 'label': '.Place()', 'source_file': 'src/BetPublisher.cs', 'source_location': 'L17'}],
                    'links': [{'source': 'pub', 'target': 'place', 'relation': 'method'}]},
        'Bet.Api': {'nodes': [{'id': 'listener', 'label': 'BetListener', 'source_file': str(root / 'Bet.Api' / 'Grpc' / 'BetGrpcService.cs'), 'source_location': 'L12'},
                              {'id': 'run', 'label': '.Run()', 'source_file': 'Grpc/BetGrpcService.cs', 'source_location': 'L14'}],
                    'links': [{'source': 'listener', 'target': 'run', 'relation': 'method'}]},
    }
    for name, graph in graphs.items():
        (root / name / 'graphify-out').mkdir()
        (root / name / 'graphify-out' / 'graph.json').write_text(json.dumps(graph))


class RealWorldPatternTests(unittest.TestCase):
    """Patterns seen in a real .NET system: subscriber base classes, subjects in a shared library."""
    def test_subscriber_property_and_shared_constants(self):
        files = {
            'Shared/Subjects.cs': 'public static class Subjects { public const string PLACE_BET = "casino.place_bet"; public static string TIMER(string p) => $"timer.{p}"; }',
            'Shared/Shared.csproj': '<Project/>',
            'Game/Pub.cs': 'class BetPublisher { INatsPublisherClient nats; async Task Go() { await nats.PublishFireAndForgetAsync(\n subject: Subjects.PLACE_BET, message: x); await nats.PublishAsync(Subjects.TIMER("Game"), x); } }',
            'Bets/PlaceBetSubscriber.cs': 'internal class PlaceBetSubscriber : ProcessingPushMessageService<BetPlacedEvent>\n{\n INatsSubscriberClient c;\n protected override string Subject => Subjects.PLACE_BET;\n}\nclass TimerSubscriber : ProcessingPullMessageService<T> { INatsSubscriberClient c; protected override string Subject => Subjects.TIMER("Game"); }',
            'Bets/nuget.config': '<add key="github" value="https://nuget.pkg.github.com/Org/index.json" />',
            'Gateway/DemoController.cs': '[Route("demo")] public class DemoController { [HttpGet("{partnerKey}/{gameCode}")] public void Get() {} }',
        }
        with tempfile.TemporaryDirectory() as tmp:
            for rel, text in files.items():
                (Path(tmp) / rel).parent.mkdir(parents=True, exist_ok=True)
                (Path(tmp) / rel).write_text(text)
            result = system_links.scan_system([{'path': str(Path(tmp) / s)} for s in ('Game', 'Bets', 'Shared', 'Gateway')])
        found = edges(result)
        self.assertEqual(sorted(found[('Game', 'Bets', 'nats')]), ['Subjects.TIMER()', 'casino.place_bet'])
        self.assertNotIn(('Bets', 'Gateway', 'http'), found)   # a NuGet feed URL is not a call to a demo route

    def test_helpers_self_loops_and_comments_are_not_gaps(self):
        files = {
            'Shared/Names.cs': 'public static class Subjects { public static string CONFIRM(string g) => $"casino.{g}_confirm"; }\n'
                               'public static class RabbitExchangeNames { public const string ROUND_EXCHANGE = nameof(ROUND_EXCHANGE); }\n'
                               'class Lib { INatsPublisherClient n; Task Send(string channel) => db.PublishAsync(RedisChannel.Literal(channel), x); }',
            'Bet/NatsHelper.cs': 'public static class NatsHelper {\n private static readonly Dictionary<long, string> _confirm =\n'
                                 '  BuildMap(g => Subjects.CONFIRM(g.SubjectName!));\n'
                                 ' public static string GetConfirmSubject(GameTypeEnum gameType, long gameId) =>\n  Lookup(_confirm, gameType, gameId);\n}',
            'Bet/Pub.cs': 'class BetPublisher { INatsPublisherClient nats; async Task Go(Bet b) {\n'
                          ' await nats.PublishAsync(subject: NatsHelper.GetConfirmSubject(b.Type, b.GameId), message: x);\n'
                          ' // await nats.PublishAsync(Subjects.OLD_SUBJECT, x);\n'
                          ' await _partnerService.GetAsync(request.PartnerId, ct); } }',
            'Game/ConfirmSubscriber.cs': 'class ConfirmSubscriber { INatsSubscriberClient c; protected override string Subject => Subjects.CONFIRM("Game"); }',
            'Game/RabbitMqRegister.cs': 'class RabbitMqRegister { void Add() {\n'
                                        ' services.AddAsyncPublisher(s => s.AddPublisherHost(o).AddOption(new RabbitPubSubOption {\n'
                                        '  Name = RabbitPublisherNames.ROUND, Exchange = RabbitExchangeNames.ROUND_EXCHANGE }));\n'
                                        ' services.AddAsyncSubscriber(s => s.AddSubscriberHost(o).AddOption(new RabbitPubSubOption {\n'
                                        '  Name = RabbitPublisherNames.ROUND, Exchange = RabbitExchangeNames.ROUND_EXCHANGE,\n'
                                        '  Queue = RabbitQueueNames.GAME_ROUND_QUEUE })); } }',
            'Game/RoundDeadLetterSubscriber.cs': 'class RoundDeadLetterSubscriber { IRabbitMq r; protected override string QueueName =>'
                                                 ' $"{RabbitExchangeNames.ROUND_EXCHANGE}_DeadLetter"; }',
            'Game/Hub.cs': 'class GameHub { IRedis r; async Task Kick() { await database.PublishAsync(RedisChannel.Literal(ConnectionKeys.KickChannel(_name)), id); }\n'
                           ' async Task Listen() { await _redisSubscriberClient.SubscribeAsync(ConnectionKeys.KickChannel(_name), (c, v) => {}); } }',
            'Game2/RabbitMqRegister.cs': 'class RabbitMqRegister { void Add() {\n'
                                         ' services.AddAsyncPublisher(s => s.AddPublisherHost(o).AddOption(new RabbitPubSubOption {\n'
                                         '  Exchange = RabbitExchangeNames.ROUND_EXCHANGE }));\n'
                                         ' services.AddAsyncSubscriber(s => s.AddSubscriberHost(o).AddOption(new RabbitPubSubOption {\n'
                                         '  Exchange = RabbitExchangeNames.ROUND_EXCHANGE })); } }',
        }
        with tempfile.TemporaryDirectory() as tmp:
            for rel, text in files.items():
                (Path(tmp) / rel).parent.mkdir(parents=True, exist_ok=True)
                (Path(tmp) / rel).write_text(text)
            result = system_links.scan_system([{'path': str(Path(tmp) / s)} for s in ('Bet', 'Game', 'Game2', 'Shared')])
        found = edges(result)
        self.assertEqual(found[('Bet', 'Game', 'nats')], ['Subjects.CONFIRM()'])   # through NatsHelper and its field
        self.assertNotIn(('Game', 'Game2', 'rabbitmq'), found)   # each game feeds its own round exchange
        self.assertNotIn(('Game2', 'Game', 'rabbitmq'), found)
        unmatched = {s['name']: s['unmatched'] for s in result['services']}
        self.assertEqual(unmatched['Game'], [])     # own loop, bound queue, dead letters, kicks between replicas
        self.assertEqual(unmatched['Game2'], [])
        self.assertEqual(unmatched['Shared'], [])   # a library passing a channel variable through
        self.assertNotIn('OLD_SUBJECT', json.dumps(unmatched['Bet']))   # commented-out code
        self.assertNotIn('PartnerId', json.dumps(unmatched['Bet']))


class SystemGraphTests(unittest.TestCase):
    def test_merged_graph_links_the_sending_and_receiving_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(FIXTURE, root, dirs_exist_ok=True)
            fake_graphs(root)
            scan = system_links.scan_system([{'path': str(root / s)} for s in SERVICES])
            graph = system_links.system_graph('SparkleGaming', scan)
        ids = {n['id'] for n in graph['nodes']}
        self.assertIn('s0::place', ids)
        self.assertEqual(graph['missing_graphs'], ['wallet-service', 'notifier'])
        nats = [l for l in graph['links'] if l.get('tech') == 'nats' and l['channel'] == 'bets.placed']
        self.assertEqual([(l['source'], l['target']) for l in nats], [('s0::place', 's1::run')])   # .Place() -> .Run()
        to_wallet = [l for l in graph['links'] if l.get('tech') == 'nats' and l['channel'] == 'wallet.*.debit']
        self.assertEqual(to_wallet[0]['target'], 's2::service')   # no code graph: the service node stands in
        self.assertTrue(all(n['project_path'] for n in graph['nodes']))
        self.assertEqual({l['source'] for l in graph['links'] if l['relation'] == 'method'}, {'s0::pub', 's1::listener'})


class ImpactTests(unittest.TestCase):
    """A change in one service that touches a connection is reported, with the other side."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        shutil.copytree(FIXTURE, self.root / 'system')
        self.patches = [mock.patch.object(system_links, 'SYSTEMS', self.root / 'systems.json'),
                        mock.patch.object(system_links, 'LEGACY_SYSTEMS', self.root / 'legacy.json'),
                        mock.patch.object(system_links, 'CACHE_DIR', self.root / 'cache'),
                        mock.patch.object(system_links, 'SETTINGS', self.root / 'claude' / 'settings.json')]
        for p in self.patches:
            p.start()
        self.system = system_links.save_system({'name': 'SparkleGaming', 'services': [{'path': str(self.root / 'system' / s)} for s in SERVICES]})
        self.publisher = self.root / 'system' / 'Adastra' / 'src' / 'BetPublisher.cs'
        self.original = self.publisher.read_text()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_renaming_a_subject_breaks_the_link(self):
        services = system_links.scan_services(self.system['services'])
        changed = self.original.replace('"bets.placed"', '"bets.created"')
        notes = system_links.analyze_change(services, 0, 'src/BetPublisher.cs', changed, None, old_text=self.original)
        self.assertTrue(any(n.startswith("BROKEN nats 'bets.placed'") and 'Bet.Api' in n for n in notes), notes)
        self.assertTrue(any(n.startswith("UNMATCHED nats 'bets.created'") for n in notes), notes)

    def test_editing_near_a_publish_call_names_the_receiver(self):
        services = system_links.scan_services(self.system['services'])
        line = self.original.splitlines().index(next(l for l in self.original.splitlines() if 'Subjects.BetPlaced' in l)) + 1
        notes = system_links.analyze_change(services, 0, 'src/BetPublisher.cs', self.original, [(line, line)], old_text=self.original)
        self.assertTrue(any(n.startswith("CHANGED near nats 'bets.placed'") and 'Bet.Api Grpc/BetGrpcService.cs' in n for n in notes), notes)

    def test_editing_a_message_contract_is_reported(self):
        services = system_links.scan_services(self.system['services'])
        text = self.original + '\npublic class SettlementRequested { public Guid BetId { get; set; } }\n'
        last = text.count('\n')
        notes = system_links.analyze_change(services, 0, 'src/BetPublisher.cs', text, [(last, last)], old_text=self.original)
        self.assertTrue(any(n.startswith('CONTRACT type SettlementRequested') for n in notes), notes)

    def test_hook_warns_claude_only_for_connection_changes(self):
        system_links.load_cached(self.system)   # cache = state before the edit
        event = lambda new: json.dumps({'tool_name': 'Edit', 'tool_input': {'file_path': str(self.publisher), 'old_string': 'x', 'new_string': new}})
        self.publisher.write_text(self.original.replace('"bets.placed"', '"bets.created"'))
        with mock.patch('sys.stderr', new=io.StringIO()) as err:
            self.assertEqual(system_links.hook(event('"bets.created"')), 2)
        self.assertIn("BROKEN nats 'bets.placed'", err.getvalue())
        other = self.root / 'system' / 'Adastra' / 'src' / 'Util.cs'
        other.write_text('public static class Util { public static int Two() => 2; }')
        quiet = json.dumps({'tool_name': 'Write', 'tool_input': {'file_path': str(other), 'content': ''}})
        self.assertEqual(system_links.hook(quiet), 0)
        outside = json.dumps({'tool_name': 'Write', 'tool_input': {'file_path': str(self.root / 'elsewhere.cs')}})
        self.assertEqual(system_links.hook(outside), 0)

    @unittest.skipUnless(shutil.which('git'), 'git not installed')
    def test_impact_reads_git_changes(self):
        repo = self.root / 'system' / 'Adastra'
        git = lambda *a: subprocess.run(['git', *a], cwd=repo, check=True, capture_output=True)
        git('init', '-q'); git('add', '.'); git('-c', 'user.email=t@t', '-c', 'user.name=t', 'commit', '-qm', 'init')
        self.publisher.write_text(self.original.replace('"bets.placed"', '"bets.created"'))
        cwd = os.getcwd()
        os.chdir(repo)
        try:
            report = system_links.impact([])
        finally:
            os.chdir(cwd)
        self.assertTrue(any("BROKEN nats 'bets.placed'" in line for line in report), report)

    def test_install_and_remove_the_claude_code_hook(self):
        system_links.SETTINGS.parent.mkdir()
        system_links.SETTINGS.write_text(json.dumps({'model': 'x', 'hooks': {'PostToolUse': [{'matcher': 'Bash', 'hooks': [{'type': 'command', 'command': 'other'}]}]}}))
        system_links.install_hook()
        system_links.install_hook()   # idempotent
        hooks = json.loads(system_links.SETTINGS.read_text())['hooks']['PostToolUse']
        self.assertEqual(len(hooks), 2)
        self.assertIn('system_links.py', hooks[1]['hooks'][0]['command'])
        system_links.install_hook(remove=True)
        settings = json.loads(system_links.SETTINGS.read_text())
        self.assertEqual(settings['model'], 'x')
        self.assertEqual(settings['hooks']['PostToolUse'][0]['matcher'], 'Bash')


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        self.patches = [mock.patch.object(system_links, 'SYSTEMS', tmp / 'systems.json'),
                        mock.patch.object(system_links, 'LEGACY_SYSTEMS', tmp / 'legacy.json'),
                        mock.patch.object(system_links, 'CACHE_DIR', tmp / 'cache'),
                        mock.patch.object(explorer_server, 'PROJECTS', tmp / 'projects.json')]
        for p in self.patches:
            p.start()
        explorer_server.Handler.token = 'tok'
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), explorer_server.Handler)
        explorer_server.Handler.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close()
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def call(self, path, body=None):
        request = urllib.request.Request(f'http://127.0.0.1:{explorer_server.Handler.port}{path}',
                                         data=None if body is None else json.dumps(body).encode(),
                                         headers={'X-Token-Graph': 'tok', 'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())

    def test_save_scan_and_delete_a_system(self):
        found = self.call('/api/systems/discover', {'path': str(FIXTURE)})['services']
        system = self.call('/api/systems/save', {'name': 'SparkleGaming', 'services': found})['system']
        self.assertEqual(len(self.call('/api/systems')['systems']), 1)
        scan = self.call('/api/systems/scan', {'id': system['id']})
        self.assertIn(('Adastra', 'Bet.Api', 'nats'), edges(scan))
        projects = json.loads(explorer_server.PROJECTS.read_text())
        self.assertEqual(len(projects), 4)   # services become known projects (source preview, editors)
        graph = self.call('/api/systems/graph', {'id': system['id']})
        self.assertEqual(len([n for n in graph['nodes'] if n['file_type'] == 'service']), 4)
        self.call('/api/systems/delete', {'id': system['id']})
        self.assertEqual(self.call('/api/systems')['systems'], [])


if __name__ == '__main__':
    unittest.main()
