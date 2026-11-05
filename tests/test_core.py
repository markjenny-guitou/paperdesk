"""Core retrieval tests use a temporary database, never the user's library."""
import importlib.util
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {'PAPERDESK_DATA': self.temp.name})
        self.environment.start()
        spec = importlib.util.spec_from_file_location('isolated_paperdesk', Path(__file__).parents[1] / 'app.py')
        self.app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.app)
        self.app.init_db()

    def tearDown(self):
        self.environment.stop()
        self.temp.cleanup()

    def sample(self, source='crossref', title='Graph neural networks: a review', **fields):
        return self.app.paper(source, title, year=2024, **fields)

    def test_merge_cross_source_doi_variants_and_keep_richer_abstract(self):
        records = [self.sample(doi='https://doi.org/10.1234/ABC', abstract='Short', citations=2),
                   self.sample('openalex', title='Graph Neural Networks — A Review', doi='10.1234/abc', abstract='A more complete abstract.', pdf='https://example.org/paper.pdf', citations=8)]
        result = self.app.merge_papers(records)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['citations'], 8)
        self.assertEqual(result[0]['abstract'], 'A more complete abstract.')
        self.assertEqual(set(result[0]['sources']), {'Crossref', 'OpenAlex'})
        self.assertTrue(result[0]['pdf'])

    def test_same_title_with_conflicting_doi_is_not_collapsed(self):
        records = [self.sample(doi='10.1234/a'), self.sample(doi='10.1234/b')]
        self.assertEqual(len(self.app.merge_papers(records)), 2)

    def test_favorite_survives_doi_enrichment_and_retrieval(self):
        first = self.app.prepare_results([self.sample()], 'graph neural network', {})[0]
        with self.app.connect() as conn:
            conn.execute('UPDATE papers SET favorite=1 WHERE id=?', (first['id'],))
        enriched = self.app.prepare_results([self.sample(doi='10.1234/new')], 'graph neural network', {})[0]
        self.assertEqual(first['id'], enriched['id'])
        self.assertTrue(enriched['favorite'])
        self.assertTrue(self.app.lookup_paper(first['id'])['favorite'])

    def test_relevance_and_year_filter_remove_old_and_rank_title_match(self):
        old = self.sample(title='Graph neural networks old edition') | {'year': 2011}
        relevant = self.sample(abstract='Survey of graph neural networks.')
        distractor = self.sample(title='Food networks', abstract='Historical analysis of supply networks.')
        result = self.app.prepare_results([distractor, old, relevant], 'graph neural network', {'yearFrom': 2022})
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['title'], relevant['title'])

    def test_chinese_query_date_and_common_terms(self):
        query, options = self.app.plan_query('2022年以来关于图神经网络的综述', {})
        self.assertEqual(query, 'graph neural network review')
        self.assertEqual(options['yearFrom'], 2022)
        self.assertTrue(options['review'])
        self.assertEqual(self.app.plan_query('肠道菌群与抑郁症', {})[0], 'gut microbiota depression')
        with self.assertRaises(ValueError):
            self.app.plan_query('networks', {'yearFrom': 2025, 'yearTo': 2020})

    def test_exports_quote_excel_formulas_and_escape_bibtex(self):
        p = self.app.prepare_results([self.sample(title='=Title {A} & B_1', doi='10.1234/a')], 'title', {})[0]
        csv = self.app.export_records([p['id']], 'csv')
        self.assertIn("'=Title", csv)
        bib = self.app.export_records([p['id']], 'bibtex')
        self.assertIn(r'\{A\}', bib)
        self.assertIn(r'\&', bib)
        ris = self.app.export_records([p['id']], 'ris')
        self.assertIn('DO  - 10.1234/a', ris)

    def test_download_rejects_private_targets_and_non_https(self):
        with self.assertRaises(ValueError): self.app.verify_download_url('http://example.com/file.pdf')
        with patch.object(socket, 'getaddrinfo', return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))]):
            with self.assertRaises(ValueError): self.app.verify_download_url('https://example.com/private.pdf')

    def test_pubmed_backend_error_is_not_reported_as_empty_success(self):
        class Response:
            def json(self): return {'esearchresult': {'ERROR': 'Search backend unavailable'}}
        with patch.object(self.app, 'get', return_value=Response()):
            with self.assertRaises(ValueError): self.app.pubmed('cancer', {})

    def test_crossref_drops_peer_reviews_and_supplementary_components(self):
        class Response:
            def json(self):
                return {'message': {'items': [
                    {'type': 'peer-review', 'title': ['Review for a paper']},
                    {'type': 'component', 'title': ['Supplementary figures']},
                    {'type': 'journal-article', 'title': ['An actual research paper'],
                     'DOI': '10.1234/research', 'author': [{'name': 'Research Consortium'}, {}]},
                ]}}
        with patch.object(self.app, 'get', return_value=Response()):
            papers = self.app.crossref('research', {})
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0]['authors'], ['Research Consortium'])

    def test_journal_names_are_exact_and_multiple_selections_are_or(self):
        records = [self.sample(title='A', venue='Nature'),
                   self.sample(title='B', venue='Science'),
                   self.sample(title='C', venue='Nature Medicine'),
                   self.sample(title='D', venue=''),
                   self.sample(title='E', venue='Journal of Other Sciences')]
        results = self.app.prepare_results(records, 'research', {'journals': ['Nature', 'Science']})
        self.assertEqual({p['title'] for p in results}, {'A', 'B'})
        unfiltered = self.app.prepare_results(records, 'research', {'journals': []})
        self.assertEqual(len(unfiltered), 5)

    def test_journal_aliases_issn_and_published_preprint_merge(self):
        choices = [dict(title='Journal of Example', aliases=['J Example'], issns=['1234-567X'])]
        self.assertTrue(self.app.matches_journals({'venue': 'J. Example'}, choices))
        self.assertTrue(self.app.matches_journals({'venue': 'Abbreviated', 'issns': ['1234-567X']}, choices))
        self.assertFalse(self.app.matches_journals({'venue': 'Journal of Example Medicine'}, choices))
        records = [self.sample('arxiv', title='A published study', venue='arXiv', doi='10.1234/a'),
                   self.sample('crossref', title='A published study', venue='Nature', doi='10.1234/a')]
        results = self.app.prepare_results(records, 'study', {'journals': ['Nature']})
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['venue'], 'Nature')

    def test_journal_selection_validation_and_alias_canonicalization(self):
        self.assertEqual(self.app.validate_journals(['JACS', 'Journal of the American Chemical Society', ' Nature ']),
                         ['Journal of the American Chemical Society', 'Nature'])
        self.assertEqual(self.app.validate_journals(['A Custom Journal']), ['A Custom Journal'])
        for values in ('Nature', [None], ['\nNature'], [''], ['x' * 241]):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.app.validate_journals(values)

    def test_journal_conditions_are_sent_to_crossref_and_biomedical_sources(self):
        class Response:
            def json(self): return {'message': {'items': []}, 'esearchresult': {'idlist': []}, 'resultList': {'result': []}}
        with patch.object(self.app, 'get', return_value=Response()) as fetch:
            self.app.crossref('cancer', {'journals': ['Nature', 'Science']})
            filters = fetch.call_args.args[1]['filter']
            # Metadata-enriched catalogs use ISSN; otherwise exact container titles.
            self.assertTrue(('container-title:Nature' in filters and 'container-title:Science' in filters) or filters.count('issn:') >= 2)
            self.app.pubmed('cancer OR tumor', {'journals': ['Nature', 'Science']})
            term = fetch.call_args.args[1]['term']
            self.assertIn('(cancer OR tumor) AND (', term)
            self.assertIn('"Nature"[Journal] OR "Science"[Journal]', term)
            self.app.europepmc('cancer', {'journals': ['Nature', 'Science']})
            expression = fetch.call_args.args[1]['query']
            self.assertTrue('JOURNAL:"Nature"' in expression or 'ISSN:' in expression)
            self.assertNotIn('JOURNAL_TITLE:', expression)

    def test_custom_comma_journal_avoids_crossref_filter_injection(self):
        class Response:
            def json(self): return {'message': {'items': []}}
        with patch.object(self.app, 'get', return_value=Response()) as fetch:
            self.app.crossref('cancer', {'journals': ['Custom Journal, New Series']})
            params = fetch.call_args.args[1]
            self.assertEqual(params['query.container-title'], 'Custom Journal, New Series')
            self.assertNotIn('container-title:', params.get('filter', ''))

    def test_openalex_uses_journal_source_ids_and_primary_publication_metadata(self):
        class Response:
            def json(self): return {'results': [{'title': 'Published result', 'publication_year': 2024,
                'primary_location': {'source': {'display_name': 'Nature', 'issn': ['0028-0836']}},
                'best_oa_location': {'source': {'display_name': 'Institutional Repository'}, 'pdf_url': 'https://example.org/a.pdf'}}]}
        with patch.object(self.app, 'openalex_journal_id', side_effect=['S1', 'S2']), patch.object(self.app, 'get', return_value=Response()) as fetch:
            items = self.app.openalex('cancer', {'journals': ['Nature', 'Science'], 'yearFrom': 2022})
            self.assertIn('primary_location.source.id:S1|S2', fetch.call_args.args[1]['filter'])
            self.assertEqual(items[0]['venue'], 'Nature')
            self.assertEqual(items[0]['issns'], ['0028-0836'])
            self.assertTrue(items[0]['pdf'])

    def test_large_journal_selection_batches_without_losing_any_name(self):
        calls = []
        def adapter(query, options):
            calls.append(options['journals'])
            return options['journals']
        titles = [f'Journal {i}' for i in range(29)]
        self.assertEqual(self.app.journal_batches(adapter, 'topic', {'journals': titles}), titles)
        self.assertEqual([len(c) for c in calls], [12, 12, 5])

    def test_journal_selection_is_saved_and_unsupported_sources_are_skipped(self):
        with patch.object(self.app.threading, 'Thread') as thread:
            job = self.app.start_search({'query': 'cancer', 'journals': ['Nature', 'Science']})
            thread.return_value.start.assert_called_once()
        try:
            self.assertEqual(job['options']['journals'], ['Nature', 'Science'])
            self.assertEqual(job['sources']['arxiv']['state'], 'skipped')
            self.assertEqual(job['sources']['semantic']['state'], 'skipped')
            with self.app.connect() as conn:
                saved = conn.execute('SELECT options FROM runs WHERE id=?', (job['id'],)).fetchone()
            import json
            self.assertEqual(json.loads(saved['options'])['journals'], ['Nature', 'Science'])
        finally:
            self.app.SEARCH_SLOTS.release()

    def test_known_different_journal_name_overrides_inconsistent_issn(self):
        choices = [dict(title='Nature', aliases=[], issns=['1234-567X'])]
        self.assertFalse(self.app.matches_journals({'venue': 'Nature Medicine', 'issns': ['1234-567X']}, choices))


if __name__ == '__main__':
    unittest.main()
