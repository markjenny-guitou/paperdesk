"""Real HTTP isolation tests. All libraries, cookies and PDFs are disposable."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import requests


class SpaceIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'PAPERDESK_DATA': self.temp.name})
        self.env.start()
        spec = importlib.util.spec_from_file_location('space_test_app', Path(__file__).parents[1] / 'app.py')
        self.app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.app)
        self.app.init_db()
        self.legacy = self.app.prepare_results([self.app.paper('crossref', 'Legacy private paper', year=2024)], 'legacy', {})[0]
        with self.app.connect() as conn:
            conn.execute('UPDATE papers SET favorite=1 WHERE id=?', (self.legacy['id'],))
        (self.app.DOWNLOADS / (self.legacy['id'] + '.pdf')).write_bytes(b'%PDF-1.4\nLegacy private content')
        self.app.init_spaces()

        def adapter(query, options):
            return [self.app.paper('crossref', query, doi='10.1234/' + query.replace(' ', '-'), year=2024)]
        self.app.ADAPTERS = {name: adapter for name in self.app.SOURCES}
        app = self.app
        class QuietHandler(app.Handler):
            def log_message(self, *args): pass
        self.server = app.ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
        self.server.daemon_threads = True
        app.PORT = self.server.server_port
        self.base = f'http://127.0.0.1:{app.PORT}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.clients = []

    def tearDown(self):
        for client in self.clients: client.close()
        for _ in range(100):
            if all(j['status'] != 'running' for j in self.app.JOBS.values()): break
            time.sleep(.02)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.env.stop()
        self.temp.cleanup()

    def client(self):
        client = requests.Session()
        client.trust_env = False
        self.clients.append(client)
        response = client.get(self.base + '/api/status')
        self.assertEqual(response.status_code, 200)
        client.headers['X-PaperDesk-Token'] = response.json()['token']
        return client

    def get(self, client, path):
        return client.get(self.base + path, timeout=5)

    def post(self, client, path, data):
        return client.post(self.base + path, json=data, timeout=5)

    def identifier(self, client):
        return self.app.SPACES.resolve(client.cookies.get('haoge_space'))

    def seed(self, client, title='Private paper'):
        context = self.app.CURRENT_SPACE.set(self.identifier(client))
        try:
            item = self.app.prepare_results([self.app.paper('crossref', title, year=2024)], 'paper', {})[0]
            (self.app.download_directory() / (item['id'] + '.pdf')).write_bytes(b'%PDF-1.4\nPrivate content')
            return item
        finally:
            self.app.CURRENT_SPACE.reset(context)

    def wait_job(self, client, identifier):
        for _ in range(100):
            response = self.get(client, '/api/search/' + identifier)
            self.assertEqual(response.status_code, 200)
            if response.json()['status'] != 'running': return response.json()
            time.sleep(.02)
        self.fail('Mock search did not finish')

    def test_same_ip_creates_distinct_spaces_and_http_only_cookie(self):
        a, b = self.client(), self.client()
        self.assertNotEqual(self.identifier(a), self.identifier(b))
        self.assertEqual(self.get(a, '/api/history').json()['items'], [])
        self.assertEqual(self.get(b, '/api/favorites').json()['results'], [])
        response = requests.get(self.base + '/api/status')
        cookie = response.headers['Set-Cookie']
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=Lax', cookie)
        self.assertNotIn('Authorization', response.request.headers)

    def test_foreign_paper_cannot_be_favorited_exported_or_downloaded(self):
        a, b = self.client(), self.client()
        paper = self.seed(a)
        self.assertEqual(self.post(a, '/api/favorite', {'id': paper['id'], 'favorite': True}).status_code, 200)
        self.assertEqual(len(self.get(a, '/api/favorites').json()['results']), 1)
        self.assertEqual(self.get(b, '/api/favorites').json()['results'], [])
        for path, data in [('/api/favorite', {'id': paper['id'], 'favorite': True}),
                           ('/api/export', {'ids': [paper['id']], 'format': 'ris'}),
                           ('/api/download', {'id': paper['id']})]:
            with self.subTest(path=path): self.assertEqual(self.post(b, path, data).status_code, 400)
        file_path = '/files/' + paper['id'] + '.pdf'
        self.assertEqual(self.get(a, file_path).status_code, 200)
        self.assertEqual(self.get(b, file_path).status_code, 404)
        self.assertEqual(requests.get(self.base + file_path).status_code, 401)

    def test_same_public_paper_has_separate_favorite_state(self):
        a, b = self.client(), self.client()
        pa, pb = self.seed(a), self.seed(b)
        self.assertEqual(pa['id'], pb['id'])
        self.post(a, '/api/favorite', {'id': pa['id'], 'favorite': True})
        self.assertEqual(self.get(a, '/api/status').json()['favorites'], 1)
        self.assertEqual(self.get(b, '/api/status').json()['favorites'], 0)

    def test_identical_pdf_filename_resolves_to_each_visitors_own_file(self):
        a, b = self.client(), self.client()
        pa, pb = self.seed(a), self.seed(b)
        self.assertEqual(pa['id'], pb['id'])
        for client, marker in [(a, b'Visitor A'), (b, b'Visitor B')]:
            target = self.app.SPACES.path(self.identifier(client)) / 'papers' / (pa['id'] + '.pdf')
            target.write_bytes(b'%PDF-1.4\n' + marker)
        path = '/files/' + pa['id'] + '.pdf'
        self.assertIn(b'Visitor A', self.get(a, path).content)
        self.assertIn(b'Visitor B', self.get(b, path).content)

    def test_background_search_dedup_memory_and_sqlite_history_are_scoped(self):
        a, b = self.client(), self.client()
        ra = self.post(a, '/api/search', {'query': 'private query'}).json()
        rb = self.post(b, '/api/search', {'query': 'private query'}).json()
        self.assertNotEqual(ra['id'], rb['id'])
        ja = self.wait_job(a, ra['id'])
        jb = self.wait_job(b, rb['id'])
        self.assertNotIn('_space', ja)
        self.assertEqual(len(ja['results']), 1)
        self.assertEqual(len(jb['results']), 1)
        self.assertEqual(self.get(b, '/api/search/' + ra['id']).status_code, 404)
        self.assertEqual(len(self.get(a, '/api/history').json()['items']), 1)
        self.post(a, '/api/favorite', {'id': ja['results'][0]['id'], 'favorite': True})
        self.assertFalse(self.get(b, '/api/search/' + rb['id']).json()['results'][0]['favorite'])
        self.app.JOBS.clear()
        self.assertEqual(self.get(b, '/api/search/' + ra['id']).status_code, 404)
        self.assertEqual(self.get(a, '/api/search/' + ra['id']).status_code, 200)
        with self.app.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM papers').fetchone()[0], 1)

    def test_forged_cookie_and_cross_space_csrf_are_rejected(self):
        a, b = self.client(), self.client()
        token_a = a.headers['X-PaperDesk-Token']
        b.headers['X-PaperDesk-Token'] = token_a
        self.assertEqual(self.post(b, '/api/search', {'query': 'test'}).status_code, 403)
        forged = self.identifier(a) + '.' + 'A' * 43
        self.assertEqual(requests.get(self.base + '/api/history', headers={'Cookie': 'haoge_space=' + forged}).status_code, 401)
        self.assertEqual(self.get(a, '/api/status').json()['favorites'], 0)

    def test_entry_restores_own_space_and_rejects_foreign_origin(self):
        a, b = self.client(), self.client()
        paper = self.seed(a)
        self.post(a, '/api/favorite', {'id': paper['id'], 'favorite': True})
        key = self.get(a, '/api/space/entry').json()['key']
        bad = b.post(self.base + '/api/space/restore', json={'key': key}, headers={'Origin': 'https://evil.example'})
        self.assertEqual(bad.status_code, 403)
        self.assertEqual(self.get(b, '/api/status').json()['favorites'], 0)
        self.assertEqual(self.post(b, '/api/space/restore', {'key': key}).status_code, 200)
        self.assertEqual(self.get(b, '/api/status').json()['favorites'], 1)
        self.assertEqual(self.identifier(a), self.identifier(b))
        self.assertEqual(self.post(b, '/api/space/restore', {'key': 'invalid'}).status_code, 400)

    def test_legacy_library_is_private_and_migration_is_idempotent(self):
        visitor = self.client()
        owner = json.loads((self.app.DATA / '.owner-space.json').read_text())
        self.assertEqual(self.get(visitor, '/api/favorites').json()['results'], [])
        self.assertEqual(self.get(visitor, '/files/' + self.legacy['id'] + '.pdf').status_code, 404)
        self.post(visitor, '/api/space/restore', {'key': owner['token']})
        status = self.get(visitor, '/api/status').json()
        visitor.headers['X-PaperDesk-Token'] = status['token']
        self.assertEqual(status['favorites'], 1)
        self.assertEqual(self.get(visitor, '/files/' + self.legacy['id'] + '.pdf').status_code, 200)
        self.post(visitor, '/api/favorite', {'id': self.legacy['id'], 'favorite': False})
        self.app.init_spaces()
        self.assertEqual(self.get(visitor, '/api/status').json()['favorites'], 0)
        with self.app.connect() as conn:
            self.assertEqual(conn.execute('SELECT favorite FROM papers WHERE id=?', (self.legacy['id'],)).fetchone()[0], 1)

    def test_health_and_static_page_do_not_create_private_libraries(self):
        count = len(list(self.app.SPACES.directory.iterdir()))
        self.assertTrue(requests.get(self.base + '/api/health').json()['ok'])
        self.assertEqual(requests.get(self.base + '/').status_code, 200)
        self.assertEqual(len(list(self.app.SPACES.directory.iterdir())), count)

    def test_cross_site_bootstrap_and_host_header_are_rejected(self):
        self.assertEqual(requests.get(self.base + '/api/status', headers={'Sec-Fetch-Site': 'cross-site'}).status_code, 403)
        self.assertEqual(requests.get(self.base + '/api/status', headers={'Host': 'evil.example'}).status_code, 403)

    def test_existing_space_survives_new_space_creation_limit(self):
        a = self.client()
        for _ in range(29):
            self.app.SPACES.create('127.0.0.1')
        response = requests.get(self.base + '/api/status')
        self.assertEqual(response.status_code, 429)
        self.assertEqual(self.get(a, '/api/status').status_code, 200)


if __name__ == '__main__':
    unittest.main()
