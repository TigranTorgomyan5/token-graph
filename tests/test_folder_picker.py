"""Choosing a folder on any OS: the in-page folder list (/api/folders) and the native dialogs."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'token-graph' / 'scripts'))
import explorer_server  # noqa: E402


class ListFoldersTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for name in ('Wallet', 'bet', 'Рабочий стол', '.git', '$Recycle.Bin'):
            (self.root / name).mkdir()
        (self.root / 'README.md').write_text('x', encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def test_lists_sub_folders_only_sorted_without_hidden(self):
        result = explorer_server.list_folders(str(self.root))
        self.assertEqual([f['name'] for f in result['folders']], ['bet', 'Wallet', 'Рабочий стол'])
        self.assertEqual(result['folders'][2]['path'], str(self.root.resolve() / 'Рабочий стол'))
        self.assertEqual(result['parent'], str(self.root.resolve().parent))
        self.assertEqual(result['roots'][0], {'name': 'Home', 'path': str(Path.home())})
        self.assertFalse(result['truncated'])

    def test_default_is_home_and_filesystem_root_has_no_parent(self):
        self.assertEqual(explorer_server.list_folders(None)['path'], str(Path.home().resolve()))
        top = Path(self.root.resolve().anchor)
        self.assertEqual(explorer_server.list_folders(str(top))['parent'], '')

    def test_not_a_folder(self):
        with self.assertRaises(ValueError):
            explorer_server.list_folders(str(self.root / 'README.md'))

    def test_many_folders_are_capped(self):
        with mock.patch.object(explorer_server, 'MAX_FOLDERS', 2):
            result = explorer_server.list_folders(str(self.root))
        self.assertEqual(len(result['folders']), 2)
        self.assertTrue(result['truncated'])

    def test_endpoint(self):
        explorer_server.Handler.token = 'tok'
        server = ThreadingHTTPServer(('127.0.0.1', 0), explorer_server.Handler)
        explorer_server.Handler.port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close()))
        base = f'http://127.0.0.1:{server.server_address[1]}/api/folders?path='

        def get(path, token='tok'):
            request = urllib.request.Request(base + urllib.parse.quote(path), headers={'X-Token-Graph': token})
            with urllib.request.urlopen(request, timeout=10) as response:
                return json.loads(response.read())

        self.assertIn('Рабочий стол', [f['name'] for f in get(str(self.root))['folders']])
        with self.assertRaises(urllib.error.HTTPError) as missing:
            get(str(self.root / 'nope'))
        self.assertEqual(missing.exception.code, 400)
        with self.assertRaises(urllib.error.HTTPError) as denied:
            get(str(self.root), token='wrong')
        self.assertEqual(denied.exception.code, 403)


class NativeDialogTest(unittest.TestCase):
    def done(self, code, out='', err=''):
        return subprocess.CompletedProcess([], code, out, err)

    def test_cancel_in_zenity_or_kdialog_is_an_answer(self):
        self.assertEqual(explorer_server._picked_path(self.done(1), cancel_code=1), ('', True))
        # exit 1 with an error (no display) means the dialog is unavailable: try the next one
        self.assertIsNone(explorer_server._picked_path(self.done(1, err='cannot open display'), cancel_code=1))
        self.assertIsNone(explorer_server._picked_path(self.done(1)))
        self.assertEqual(explorer_server._picked_path(self.done(0, '/home/me/shop\n')), ('/home/me/shop', False))

    def test_linux_cancel_does_not_open_a_second_dialog(self):
        calls = []

        def run(command, **_):
            calls.append(command[0])
            return self.done(1)

        with mock.patch.object(explorer_server, 'desktop_os', lambda: 'linux'), \
                mock.patch.object(explorer_server.shutil, 'which', lambda name: '/usr/bin/zenity' if name == 'zenity' else None), \
                mock.patch.object(explorer_server, '_run_folder_picker', run):
            self.assertEqual(explorer_server.pick_folder(str(Path.home())), {'path': '', 'manual': False})
        self.assertEqual(calls, ['/usr/bin/zenity'])

    def test_windows_dialog_writes_utf8_and_opens_in_front(self):
        script = explorer_server.WINDOWS_FOLDER_PICKER
        self.assertIn('[Console]::OutputEncoding = [System.Text.Encoding]::UTF8', script)
        self.assertIn('$owner.TopMost = $true', script)
        self.assertIn('ShowDialog($owner)', script)
        self.assertIn('activate', explorer_server.APPLE_FOLDER_PICKER)

    @unittest.skipUnless(os.name == 'nt', 'Windows PowerShell encoding')
    def test_windows_utf8_output_keeps_cyrillic_paths(self):
        path = 'C:\\Users\\user\\OneDrive\\Рабочий стол'
        env = dict(os.environ, TG_TEST=path)
        result = subprocess.run(['powershell.exe', '-NoProfile', '-Command',
                                 '[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; [Console]::Out.Write($env:TG_TEST)'],
                                capture_output=True, text=True, encoding='utf-8', errors='replace', env=env,
                                creationflags=explorer_server.NO_WINDOW)
        self.assertEqual(result.stdout, path)



class DiscoverServicesTest(unittest.TestCase):
    """Choose project folder: a folder that is itself one service is returned as that service."""
    def setUp(self):
        import system_links
        self.links = system_links
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def make(self, *files):
        for name in files:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('x', encoding='utf-8')

    def names(self, folder):
        return [s['name'] for s in self.links.discover_services(self.root / folder)]

    def test_service_folder_with_src_and_tests_is_one_service(self):
        self.make('Slot/Slot.sln', 'Slot/src/Slot.Api/Slot.Api.csproj', 'Slot/tests/Slot.Tests/Slot.Tests.csproj')
        self.assertEqual(self.names('Slot'), ['Slot'])

    def test_parent_of_services_lists_them(self):
        self.make('Shop/Wallet/Wallet.sln', 'Shop/Bet/go.mod', 'Shop/Web/package.json')
        self.assertEqual(self.names('Shop'), ['Bet', 'Wallet', 'Web'])

    def test_monorepo_root_with_its_own_project_still_lists_services(self):
        self.make('Mono/package.json', 'Mono/orders/package.json', 'Mono/payments/package.json', 'Mono/tests/x.test.js')
        self.assertEqual(self.names('Mono'), ['orders', 'payments'])

    def test_a_system_holds_up_to_60_services(self):
        from unittest import mock
        for i in range(61):
            (self.root / f's{i:02}').mkdir()
        services = [{'name': f's{i:02}', 'path': str(self.root / f's{i:02}')} for i in range(61)]
        with mock.patch.object(self.links, 'SYSTEMS', self.root / 'systems.json'),                 mock.patch.object(self.links, 'LEGACY_SYSTEMS', self.root / 'legacy.json'):
            self.assertEqual(len(self.links.save_system({'name': 'Big', 'services': services[:60]})['services']), 60)
            with self.assertRaisesRegex(ValueError, 'at most 60 services'):
                self.links.save_system({'name': 'Too big', 'services': services})

    def test_folder_without_services(self):
        self.make('Notes/readme.md')
        self.assertEqual(self.names('Notes'), [])


if __name__ == '__main__':
    unittest.main()
