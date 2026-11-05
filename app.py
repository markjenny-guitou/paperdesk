"""PaperDesk: a local, persistent literature search workspace."""
from __future__ import annotations

import csv
import html
import io
import ipaddress
import json
import math
import os
import re
import secrets
import socket
import sqlite3
import threading
import time
import unicodedata
import uuid
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from functools import lru_cache
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urljoin, urlparse

import requests
from journal_catalog import JOURNALS
from spaces import BrowserSpaces

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('PAPERDESK_DATA', ROOT / 'data')).resolve()
DATA.mkdir(parents=True, exist_ok=True)
DOWNLOADS = DATA / 'papers'
DOWNLOADS.mkdir(exist_ok=True)
DB = DATA / 'library.sqlite3'
PORT = int(os.environ.get('PAPERDESK_PORT', '8766'))
DEPLOYMENT = os.environ.get('PAPERDESK_DEPLOYMENT', 'local')
SOURCES = {'openalex': 'OpenAlex', 'crossref': 'Crossref', 'arxiv': 'arXiv',
           'pubmed': 'PubMed', 'semantic': 'Semantic Scholar', 'europepmc': 'Europe PMC'}
JOBS: dict[str, dict] = {}
LOCK = threading.RLock()
SEARCH_SLOTS = threading.BoundedSemaphore(3)
AUTH_TOKEN = secrets.token_urlsafe(32)
CURRENT_SPACE = ContextVar('paperdesk_space', default=None)
SPACES = None
AGENT = 'PaperDesk/0.1 (personal local literature search)'


def space_directory(identifier=None):
    identifier = identifier or CURRENT_SPACE.get()
    if identifier is None: return DATA  # Core tests and legacy migration only.
    if SPACES is None: raise RuntimeError('Browser spaces have not been initialized')
    return SPACES.path(identifier)


def download_directory():
    directory = space_directory() / 'papers'
    directory.mkdir(exist_ok=True)
    return directory


@contextmanager
def connect(identifier=None):
    conn = sqlite3.connect(space_directory(identifier) / 'library.sqlite3', timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db(identifier=None):
    with connect(identifier) as conn:
        conn.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS papers(id TEXT PRIMARY KEY, body TEXT NOT NULL,
          favorite INTEGER DEFAULT 0, saved_at TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, query TEXT NOT NULL,
          options TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
          snapshot TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS paper_aliases(alias TEXT PRIMARY KEY, paper_id TEXT NOT NULL);
        ''')
        conn.execute("UPDATE runs SET status='interrupted' WHERE status='running'")


def init_spaces():
    global SPACES
    SPACES = BrowserSpaces(DATA)
    SPACES.migrate_legacy(init_db)
    # Also repair interrupted jobs in pre-existing visitor spaces after a restart.
    for directory in SPACES.directory.iterdir():
        if re.fullmatch(r'[a-f0-9]{32}', directory.name) and (directory / 'library.sqlite3').is_file():
            init_db(directory.name)


def now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def clean(value):
    return re.sub(r'\s+', ' ', html.unescape(re.sub('<[^>]+>', ' ', str(value or '')))).strip()


def normalized_doi(value):
    value = unquote(str(value or '').strip()).lower()
    return re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)', '', value).rstrip(' .')


def normalized_title(value):
    return ''.join(c for c in unicodedata.normalize('NFKC', clean(value)).casefold() if c.isalnum())


def public_url(value):
    value = str(value or '')
    return value if urlparse(value).scheme in ('https', 'http') else ''


def journal_key(value):
    # Exact normalized names: Nature must never match Nature Medicine.
    text = unicodedata.normalize('NFKC', clean(value)).casefold().replace('&', 'and')
    text = re.sub(r'^the\s+', '', text)
    return ''.join(c for c in text if c.isalnum())


JOURNAL_INDEX = {journal_key(name): item for item in JOURNALS
                 for name in [item['title'], *item['aliases']]}


def selected_journals(options):
    return [JOURNAL_INDEX.get(journal_key(title), dict(title=title, aliases=[], issns=[]))
            for title in options.get('journals', [])]


def validate_journals(values):
    if values is None: return []
    if not isinstance(values, list) or len(values) > 1000:
        raise ValueError('期刊筛选必须是列表，最多选择 1000 本')
    result, seen = [], set()
    for value in values:
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 240 or any(ord(c) < 32 for c in value):
            raise ValueError('期刊名称须为 1–240 个字符，请填写完整刊名')
        value = value.strip()
        title = JOURNAL_INDEX.get(journal_key(value), {}).get('title', value)
        key = journal_key(title)
        if not key: raise ValueError('请输入有效的期刊名称')
        if key not in seen: result.append(title); seen.add(key)
    return sorted(result, key=str.casefold)


def matches_journals(item, choices):
    if not choices: return True
    names = {journal_key(name) for name in [item.get('venue', ''), *item.get('venueAliases', [])] if name}
    issns = {str(x).upper() for x in item.get('issns', [])}
    # Do not let inconsistent upstream ISSN metadata override another known title.
    known_names = {name for name in names if name in JOURNAL_INDEX}
    return any(names & {journal_key(j['title']), *(journal_key(a) for a in j['aliases'])}
               or (not known_names and issns & set(j['issns'])) for j in choices)


def journal_expression(options, field):
    # Quote values so a custom name cannot introduce database query operators.
    names = []
    for j in selected_journals(options):
        name = j['title'].replace('\\', ' ').replace('"', ' ')
        if field == 'pubmed': names.append(f'"{name}"[Journal]')
        else: names.append('(' + ' OR '.join('ISSN:' + s for s in j['issns']) + ')'
                           if j['issns'] else f'JOURNAL:"{name}"')
    return '(' + ' OR '.join(names) + ')' if names else ''


def journal_batches(adapter, query, options, size=12):
    """Keep long multi-select queries below upstream URL/query limits."""
    choices = options.get('journals', [])
    if len(choices) <= size: return None
    output = []
    for i in range(0, len(choices), size):
        output.extend(adapter(query, {**options, 'journals': choices[i:i+size]}))
    return output


@lru_cache(maxsize=1024)
def openalex_journal_id(title):
    journal = JOURNAL_INDEX.get(journal_key(title), dict(title=title, aliases=[], issns=[]))
    params = {'filter': 'issn:' + journal['issns'][0]} if journal['issns'] else {'search': title}
    data = get('https://api.openalex.org/sources', {**params, 'per-page': 20}).json()
    matches = [s for s in data.get('results', []) if s.get('type') == 'journal' and
               matches_journals(dict(venue=s.get('display_name', ''), venueAliases=s.get('alternate_titles') or [],
                                     issns=s.get('issn') or []), [journal])]
    if not matches: raise ValueError('此来源未找到所选期刊的准确索引；可使用其它来源')
    return max(matches, key=lambda s: s.get('works_count', 0))['id'].rsplit('/', 1)[-1]


def paper(source, title, **fields):
    return dict(title=clean(title), authors=[], year=None, venue='', abstract='', doi='',
                url='', pdf='', citations=0, kind='', sources=[SOURCES[source]], **{}) | fields


def get(url, params=None, headers=None):
    for attempt in range(3):
        response = requests.get(url, params=params, headers={'User-Agent': AGENT, **(headers or {})},
                                timeout=(5, 16))
        if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
            delay = response.headers.get('Retry-After', '')
            time.sleep(min(6, float(delay)) if delay.isdigit() else 1.5 * (attempt + 1))
            continue
        if response.status_code == 429:
            raise ValueError('来源暂时限流，其它来源仍可使用')
        if response.status_code in (401, 403):
            raise ValueError('来源要求授权或当前网络访问受限')
        response.raise_for_status()
        return response
    raise ValueError('来源暂时无法访问')


def openalex(query, options):
    batched = journal_batches(openalex, query, options, 50)
    if batched is not None: return batched
    params = {'search': query, 'per-page': 100 if options.get('journals') else 35}
    filters = []
    if options.get('yearFrom'):
        filters.append('from_publication_date:' + str(options['yearFrom']) + '-01-01')
    if options.get('yearTo'):
        filters.append('to_publication_date:' + str(options['yearTo']) + '-12-31')
    choices = options.get('journals', [])
    if choices:
        # Resolve journal IDs once per process. Never substitute a broad keyword search.
        with ThreadPoolExecutor(max_workers=4) as pool:
            source_ids = list(pool.map(openalex_journal_id, choices))
        filters.append('primary_location.source.id:' + '|'.join(source_ids))
    if filters: params['filter'] = ','.join(filters)
    records = get('https://api.openalex.org/works', params).json().get('results', [])
    output = []
    for item in records:
        inv = item.get('abstract_inverted_index') or {}
        words = {i: word for word, positions in inv.items() for i in positions}
        location = item.get('best_oa_location') or item.get('primary_location') or {}
        publication = (item.get('primary_location') or {}).get('source') or {}
        output.append(paper('openalex', item.get('title'),
            authors=[a.get('author', {}).get('display_name', '') for a in item.get('authorships', [])[:12]],
            year=item.get('publication_year'), doi=normalized_doi(item.get('doi')),
            abstract=' '.join(words[i] for i in sorted(words)),
            venue=publication.get('display_name', ''), issns=publication.get('issn') or [],
            url=public_url(location.get('landing_page_url') or item.get('doi') or item.get('id')),
            pdf=public_url(location.get('pdf_url')), citations=item.get('cited_by_count', 0),
            kind=item.get('type', '')))
    return output


def crossref(query, options):
    params = {'query.bibliographic': query, 'rows': 100 if options.get('journals') else 35}
    filters = []
    if options.get('yearFrom'): filters.append(f"from-pub-date:{options['yearFrom']}-01-01")
    if options.get('yearTo'): filters.append(f"until-pub-date:{options['yearTo']}-12-31")
    choices = selected_journals(options)
    if choices:
        # Same-field Crossref filters are OR-ed; batches keep request URLs bounded.
        groups = [choices[i:i+12] for i in range(0, len(choices), 12)]
        # A comma is a Crossref filter separator even inside a journal title.
        if len(groups) > 1 or any(',' in j['title'] and not j['issns'] for j in choices):
            plain = [j for j in choices if ',' not in j['title'] or j['issns']]
            groups = [plain[i:i+12] for i in range(0, len(plain), 12)]
            groups += [[j] for j in choices if ',' in j['title'] and not j['issns']]
            if len(groups) > 1:
                output = []
                for group in groups:
                    output.extend(crossref(query, {**options, 'journals': [j['title'] for j in group]}))
                return output
        if len(choices) == 1 and ',' in choices[0]['title'] and not choices[0]['issns']:
            params['query.container-title'] = choices[0]['title']
        elif all(j['issns'] for j in choices):
            filters.extend('issn:' + s for j in choices for s in j['issns'])
        else:
            filters.extend('container-title:' + j['title'] for j in choices)
    if filters: params['filter'] = ','.join(filters)
    output = []
    for item in get('https://api.crossref.org/works', params).json().get('message', {}).get('items', []):
        if item.get('type') in {'peer-review', 'component', 'dataset', 'grant', 'standard'}:
            continue
        dates = (item.get('published') or item.get('issued') or {}).get('date-parts', [[]])[0]
        title = (item.get('title') or [''])[0]
        doi = normalized_doi(item.get('DOI'))
        output.append(paper('crossref', title,
            authors=[name for a in item.get('author', [])[:12] if (name := clean(a.get('given', '') + ' ' + a.get('family', '') + ' ' + a.get('name', '')))],
            year=dates[0] if dates else None, doi=doi, abstract=clean(item.get('abstract')),
            venue=(item.get('container-title') or [''])[0], venueAliases=item.get('short-container-title') or [],
            issns=item.get('ISSN') or [], url='https://doi.org/' + quote(doi, safe='/'),
            citations=item.get('is-referenced-by-count', 0), kind=item.get('type', '')))
    return output


def arxiv(query, options):
    terms = re.findall(r'[\w\-]+', query, re.UNICODE)
    search_query = ' AND '.join('all:' + '"' + t + '"' for t in terms[:12])
    if options.get('yearFrom'):
        search_query += f" AND submittedDate:[{options['yearFrom']}01010000 TO {(options.get('yearTo') or datetime.now().year)}12312359]"
    response = get('https://export.arxiv.org/api/query',
                   {'search_query': search_query, 'max_results': 35, 'sortBy': 'relevance'})
    ns = {'a': 'http://www.w3.org/2005/Atom', 'x': 'http://arxiv.org/schemas/atom'}
    output = []
    for entry in ET.fromstring(response.content).findall('a:entry', ns):
        identifier = entry.findtext('a:id', '', ns)
        if not identifier or 'api/errors' in identifier: continue
        published = entry.findtext('a:published', '', ns)
        pdf_url = next((link.get('href') for link in entry.findall('a:link', ns)
                        if link.get('title') == 'pdf' or link.get('type') == 'application/pdf'), '')
        output.append(paper('arxiv', entry.findtext('a:title', '', ns),
            authors=[a.findtext('a:name', '', ns) for a in entry.findall('a:author', ns)[:12]],
            year=int(published[:4]) if published[:4].isdigit() else None,
            abstract=clean(entry.findtext('a:summary', '', ns)),
            doi=normalized_doi(entry.findtext('x:doi', '', ns)), url=public_url(identifier),
            pdf=public_url(pdf_url).replace('http://arxiv.org', 'https://arxiv.org'), venue='arXiv', kind='preprint'))
    return output


def pubmed(query, options):
    batched = journal_batches(pubmed, query, options)
    if batched is not None: return batched
    term = query
    if options.get('journals'): term = '(' + term + ') AND ' + journal_expression(options, 'pubmed')
    if options.get('review'): term += ' AND review[Publication Type]'
    if options.get('yearFrom'): term += f" AND {options['yearFrom']}:{options.get('yearTo') or datetime.now().year}[dp]"
    data = get('https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi',
               {'db': 'pubmed', 'term': term, 'retmode': 'json', 'retmax': 100 if options.get('journals') else 35}).json()
    if data.get('esearchresult', {}).get('ERROR') or data.get('error'):
        raise ValueError('来源搜索服务暂时故障，其它来源仍可使用')
    ids = data.get('esearchresult', {}).get('idlist', [])
    if not ids: return []
    xml = get('https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi',
              {'db': 'pubmed', 'id': ','.join(ids), 'retmode': 'xml'}).content
    output = []
    for record in ET.fromstring(xml).findall('.//PubmedArticle'):
        article = record.find('.//Article')
        if article is None: continue
        text = lambda path: clean(''.join(e for node in article.findall(path) for e in node.itertext()))
        doi = next((n.text for n in record.findall('.//ArticleId') if n.get('IdType') == 'doi'), '')
        pmid = record.findtext('.//PMID', '')
        year = text('.//PubDate/Year') or text('.//PubDate/MedlineDate')[:4]
        authors = []
        for a in article.findall('.//Author')[:12]:
            authors.append(clean((a.findtext('ForeName', '') + ' ' + a.findtext('LastName', '')) or a.findtext('CollectiveName', '')))
        output.append(paper('pubmed', text('ArticleTitle'), authors=authors,
            year=int(year) if year.isdigit() else None, abstract=text('Abstract/AbstractText'),
            venue=text('Journal/Title'), venueAliases=[text('Journal/ISOAbbreviation')],
            issns=[n.text for n in record.findall('.//ISSN') + record.findall('.//ISSNLinking') if n.text], doi=normalized_doi(doi),
            url=f'https://pubmed.ncbi.nlm.nih.gov/{pmid}/', kind=text('PublicationTypeList/PublicationType')))
    return output


def semantic(query, options):
    params = {'query': query, 'limit': 35,
              'fields': 'title,authors,year,abstract,venue,url,openAccessPdf,externalIds,citationCount,publicationTypes'}
    if options.get('yearFrom'): params['year'] = str(options['yearFrom']) + '-'
    output = []
    for item in get('https://api.semanticscholar.org/graph/v1/paper/search', params).json().get('data', []):
        output.append(paper('semantic', item.get('title'),
            authors=[a.get('name', '') for a in item.get('authors', [])[:12]], year=item.get('year'),
            abstract=item.get('abstract') or '', venue=item.get('venue') or '',
            doi=normalized_doi((item.get('externalIds') or {}).get('DOI')),
            url=public_url(item.get('url')), pdf=public_url((item.get('openAccessPdf') or {}).get('url')),
            citations=item.get('citationCount', 0), kind=' '.join(item.get('publicationTypes') or [])))
    return output


def europepmc(query, options):
    batched = journal_batches(europepmc, query, options)
    if batched is not None: return batched
    if options.get('journals'): query = '(' + query + ') AND ' + journal_expression(options, 'europepmc')
    if options.get('yearFrom'):
        query += f" FIRST_PDATE:[{options['yearFrom']}-01-01 TO {options.get('yearTo') or datetime.now().year}-12-31]"
    if options.get('review'): query += ' PUB_TYPE:review'
    data = get('https://www.ebi.ac.uk/europepmc/webservices/rest/search',
               {'query': query, 'format': 'json', 'resultType': 'core', 'pageSize': 100 if options.get('journals') else 35}).json()
    output = []
    for item in data.get('resultList', {}).get('result', []):
        journal = item.get('journalInfo', {}).get('journal', {}).get('title', '')
        urls = item.get('fullTextUrlList', {}).get('fullTextUrl', [])
        pdf = next((u.get('url', '') for u in urls if u.get('documentStyle') == 'pdf'
                    and 'open access' in str(u.get('availability', '')).lower()), '')
        authors = [a.get('fullName', '') for a in item.get('authorList', {}).get('author', [])[:12]]
        year = str(item.get('pubYear') or '')
        output.append(paper('europepmc', item.get('title'), authors=authors,
            year=int(year) if year.isdigit() else None, venue=journal,
            venueAliases=[item.get('journalInfo', {}).get('journal', {}).get('medlineAbbreviation', '')],
            issns=[v for k, v in item.get('journalInfo', {}).get('journal', {}).items() if k in ('issn', 'essn') and v],
            abstract=clean(item.get('abstractText')), doi=normalized_doi(item.get('doi')),
            url='https://europepmc.org/article/' + quote(item.get('source', 'MED')) + '/' + quote(str(item.get('id', ''))),
            pdf=public_url(pdf), citations=int(item.get('citedByCount') or 0),
            kind=' '.join(item.get('pubTypeList', {}).get('pubType', []))))
    return output


ADAPTERS = {'openalex': openalex, 'crossref': crossref, 'arxiv': arxiv, 'pubmed': pubmed, 'semantic': semantic, 'europepmc': europepmc}
GLOSSARY = {
    '大语言模型': 'large language model', '大型语言模型': 'large language model', '图神经网络': 'graph neural network',
    '因果推断': 'causal inference', '深度学习': 'deep learning', '机器学习': 'machine learning',
    '人工智能': 'artificial intelligence', '强化学习': 'reinforcement learning', '多模态': 'multimodal',
    '检索增强生成': 'retrieval augmented generation', '知识图谱': 'knowledge graph', '目标检测': 'object detection',
    '语义分割': 'semantic segmentation', '扩散模型': 'diffusion model', '生成式人工智能': 'generative AI',
    '阿尔茨海默病': 'Alzheimer disease', '肿瘤': 'tumor', '癌症': 'cancer', '免疫治疗': 'immunotherapy',
    '抑郁症': 'depression', '糖尿病': 'diabetes', '心血管': 'cardiovascular', '肠道菌群': 'gut microbiota',
    '钙钛矿': 'perovskite', '太阳能电池': 'solar cell', '锂离子电池': 'lithium ion battery',
    '固态电池': 'solid state battery', '电催化': 'electrocatalysis', '碳中和': 'carbon neutrality',
    '气候变化': 'climate change', '生态系统': 'ecosystem', '碳排放': 'carbon emissions',
    '供应链': 'supply chain', '数字经济': 'digital economy', '经济增长': 'economic growth',
    '教育公平': 'educational equity', '认知负荷': 'cognitive load', '城市规划': 'urban planning',
    '系统综述': 'systematic review', '元分析': 'meta analysis', '综述': 'review',
    '稳定性': 'stability', '研究进展': 'advances', '作用机制': 'mechanism',
}


def plan_query(raw, supplied):
    options = dict(supplied)
    options['journals'] = validate_journals(options.get('journals'))
    query = raw.strip()
    match = re.search(r'(20\d{2})\s*年?以来', query)
    if match and not options.get('yearFrom'):
        options['yearFrom'] = int(match.group(1))
    query = re.sub(r'20\d{2}\s*年?以来', '', query)
    recent = re.search(r'(?:最近|近)\s*([三五十\d]+)\s*年', query)
    if recent:
        count = {'三': 3, '五': 5, '十': 10}.get(recent.group(1))
        if count is None:
            try: count = int(recent.group(1))
            except ValueError: count = 5
        if not options.get('yearFrom'): options['yearFrom'] = datetime.now().year - count + 1
        query = query.replace(recent.group(0), '')
    options['review'] = bool(options.get('review') or '综述' in query or re.search(r'\breview\b', query, re.I))
    for word in ('我想找', '我想了解', '请帮我', '帮我找', '帮我', '查找', '检索', '搜索', '关于', '有关', '相关文献', '相关论文', '的论文', '的研究'):
        query = query.replace(word, ' ')
    for zh, en in sorted(GLOSSARY.items(), key=lambda p: -len(p[0])):
        query = query.replace(zh, ' ' + en + ' ')
    query = re.sub(r'\s+[与和及的]\s+', ' ', query)
    query = re.sub(r'(?<=[\u4e00-\u9fff])的|的(?=\s|$)', ' ', query)
    query = re.sub(r'\s+', ' ', query).strip(' ，。？！,.!?') or raw
    if options['review'] and not re.search(r'\breview\b', query, re.I): query += ' review'
    options['yearTo'] = options.get('yearTo') or None
    options['yearFrom'] = options.get('yearFrom') or None
    current = datetime.now().year
    for key in ('yearFrom', 'yearTo'):
        if options[key] is not None:
            options[key] = int(options[key])
            if not 1800 <= options[key] <= current + 1: raise ValueError('年份范围不正确')
    if options['yearFrom'] and options['yearTo'] and options['yearFrom'] > options['yearTo']:
        raise ValueError('起始年份不能晚于结束年份')
    return query, options


def merge_papers(records):
    merged, doi_index, title_index = [], {}, {}
    for record in records:
        item = dict(record)
        item['doi'] = normalized_doi(item.get('doi'))
        title_key = normalized_title(item.get('title'))
        if not title_key: continue
        index = doi_index.get(item['doi']) if item['doi'] else None
        if index is None:
            candidate = title_index.get(title_key)
            if candidate is not None:
                old_doi = merged[candidate].get('doi')
                if not old_doi or not item['doi'] or old_doi == item['doi']: index = candidate
        if index is None:
            index = len(merged)
            merged.append(item)
        else:
            old = merged[index]
            old['sources'] = list(dict.fromkeys(old['sources'] + item['sources']))
            # Prefer the published journal to a repository label after deduplication.
            if old.get('venue') in ('arXiv', '') and item.get('venue') not in ('arXiv', ''):
                old['venue'] = item['venue']
            for key in ('issns', 'venueAliases'):
                old[key] = list(dict.fromkeys(old.get(key, []) + item.get(key, [])))
            old['venueAliases'] = list(dict.fromkeys(old.get('venueAliases', []) +
                                                    [v for v in (record.get('venue'), old.get('venue')) if v]))
            old['citations'] = max(int(old.get('citations') or 0), int(item.get('citations') or 0))
            if len(item.get('abstract') or '') > len(old.get('abstract') or ''): old['abstract'] = item['abstract']
            for key in ('pdf', 'doi', 'venue', 'year', 'url', 'authors', 'kind'):
                if not old.get(key) and item.get(key): old[key] = item[key]
        current = merged[index]
        if current.get('doi'): doi_index[current['doi']] = index
        title_index[title_key] = index
    return merged


def tokens(text):
    ignored = {'the', 'and', 'with', 'for', 'from', 'about', 'into', 'of', 'on', 'in', 'to', 'an', 'is', 'are', 'by', 'or'}
    parts = re.findall(r'[a-z0-9]+|[\u4e00-\u9fff]{1,}', clean(text).casefold())
    output = []
    for part in parts:
        if re.fullmatch(r'[\u4e00-\u9fff]+', part) and len(part) > 3:
            output.extend(part[i:i+2] for i in range(len(part)-1))
        elif part not in ignored:
            output.append(part[:-1] if len(part) > 4 and part.endswith('s') else part)
    return output


def prepare_results(records, query, options):
    papers = merge_papers(records)
    choices = selected_journals(options)
    terms = set(tokens(query))
    document_terms = [set(tokens(p['title'] + ' ' + p.get('abstract', ''))) for p in papers]
    n = len(papers)
    frequencies = {term: sum(term in words for words in document_terms) for term in terms}
    output = []
    for p, words in zip(papers, document_terms):
        if not matches_journals(p, choices): continue
        year = p.get('year')
        try: year = int(year) if year else None
        except (TypeError, ValueError): year = None
        p['year'] = year
        if options.get('yearFrom') and (not year or year < options['yearFrom']): continue
        if options.get('yearTo') and (not year or year > options['yearTo']): continue
        title_words = set(tokens(p['title']))
        matched = terms & words
        weighted = sum(math.log(1 + (n + 1) / (1 + frequencies[t])) * (3 if t in title_words else 1) for t in matched)
        coverage = len(matched) / max(1, len(terms))
        p['rank'] = round(weighted + coverage * 8 + math.log1p(int(p.get('citations') or 0)) * .03, 3)
        p['match'] = round(coverage * 100)
        p['matchTerms'] = sorted(matched)
        p['abstract'] = clean(p.get('abstract'))[:16000]
        p['url'] = public_url(p.get('url'))
        p['pdf'] = public_url(p.get('pdf'))
        p['id'] = uuid.uuid5(uuid.NAMESPACE_URL, p['doi'] or normalized_title(p['title'])).hex
        p['favorite'] = False
        output.append(p)
    output.sort(key=lambda p: p['rank'], reverse=True)
    with connect() as conn:
        for p in output:
            aliases = (['doi:' + p['doi']] if p['doi'] else []) + ['title:' + normalized_title(p['title'])]
            old = conn.execute('SELECT id, body, favorite FROM papers WHERE id=?', (p['id'],)).fetchone()
            if not old:
                for alias in aliases:
                    candidate = conn.execute('SELECT p.id,p.body,p.favorite FROM paper_aliases a JOIN papers p ON p.id=a.paper_id WHERE a.alias=?', (alias,)).fetchone()
                    if candidate:
                        prior_doi = json.loads(candidate['body']).get('doi')
                        if alias.startswith('title:') and prior_doi and p['doi'] and prior_doi != p['doi']: continue
                        old = candidate
                        p['id'] = old['id']
                        break
            if old:
                previous = json.loads(old['body'])
                if previous.get('localFile'): p['localFile'] = previous['localFile']
                p['favorite'] = bool(old['favorite'])
            conn.execute('INSERT INTO papers(id,body) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body',
                         (p['id'], json.dumps(p, ensure_ascii=False)))
            for alias in aliases:
                conn.execute('INSERT OR IGNORE INTO paper_aliases VALUES(?,?)', (alias, p['id']))
    return output


def snapshot(job):
    return {k: v for k, v in job.items() if not k.startswith('_')}


def persist(job):
    with connect() as conn:
        conn.execute('INSERT INTO runs VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,snapshot=excluded.snapshot',
                     (job['id'], job['query'], json.dumps(job['options']), job['status'], job['createdAt'],
                      json.dumps(snapshot(job), ensure_ascii=False)))


def source_error(exc):
    if isinstance(exc, requests.Timeout): return '来源响应较慢，本次未返回结果；可稍后重试'
    if isinstance(exc, requests.exceptions.SSLError): return '来源 HTTPS 连接失败，请检查网络或代理'
    if isinstance(exc, requests.RequestException): return '来源网络连接失败；其它来源仍可使用'
    if isinstance(exc, ValueError): return str(exc)[:160]
    return '来源数据解析失败，请稍后重试'


def run_search(job):
    space_context = CURRENT_SPACE.set(job.get('_space'))
    try:
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {pool.submit(ADAPTERS[s], job['searchQuery'], job['options']): s for s in SOURCES
                       if job['sources'][s]['state'] != 'skipped'}
            for future in as_completed(futures):
                source = futures[future]
                with LOCK:
                    try:
                        records = future.result()
                        job['_records'].extend(records)
                        job['sources'][source] = dict(state='done', count=len(records), message='')
                    except Exception as exc:
                        job['sources'][source] = dict(state='error', count=0, message=source_error(exc))
                    job['results'] = prepare_results(job['_records'], job['searchQuery'], job['options'])
                    persist(job)
        with LOCK:
            job['status'] = 'done' if any(v['state'] == 'done' for v in job['sources'].values()) else 'error'
            job['finishedAt'] = now()
            job['elapsed'] = round(time.monotonic() - job.pop('_started', time.monotonic()), 1)
            persist(job)
    except Exception:
        with LOCK:
            job['status'] = 'error'
            job['message'] = '本地任务失败，已有结果已保留，请重新检索'
            persist(job)
    finally:
        SEARCH_SLOTS.release()
        CURRENT_SPACE.reset(space_context)


def start_search(payload):
    raw = str(payload.get('query', '')).strip()
    if not raw or len(raw) > 500: raise ValueError('请输入 1–500 个字符的研究主题')
    query, options = plan_query(raw, {'yearFrom': payload.get('yearFrom'), 'yearTo': payload.get('yearTo'),
                                     'review': bool(payload.get('review')), 'journals': payload.get('journals')})
    with LOCK:
        for job in JOBS.values():
            if (job.get('_space') == CURRENT_SPACE.get() and job['status'] == 'running'
                    and job['query'] == raw and job['options'] == options):
                return snapshot(job)
        if sum(j['status'] == 'running' and j.get('_space') == CURRENT_SPACE.get() for j in JOBS.values()) >= 2:
            raise ValueError('你的空间已有 2 个检索任务在运行，请等待其中一个完成')
        if not SEARCH_SLOTS.acquire(blocking=False): raise ValueError('已有 3 个检索任务在运行，请等一个完成后再试')
        job = dict(id=uuid.uuid4().hex, query=raw, searchQuery=query, options=options, status='running',
                   createdAt=now(), sources={s: dict(state='running', count=0, message='') for s in SOURCES},
                   results=[], _records=[], _started=time.monotonic(), _space=CURRENT_SPACE.get())
        if options['journals']:
            for source in ('arxiv', 'semantic'):
                job['sources'][source] = dict(state='skipped', count=0, message='此来源不提供准确的期刊筛选，本次跳过')
        JOBS[job['id']] = job
        # Bound in-memory retention; SQLite holds the full search history.
        for old_id in list(JOBS):
            if len(JOBS) <= 24: break
            if JOBS[old_id]['status'] != 'running': JOBS.pop(old_id)
        persist(job)
        threading.Thread(target=run_search, args=(job,), daemon=True).start()
        return snapshot(job)


def lookup_paper(identifier):
    with connect() as conn:
        row = conn.execute('SELECT body,favorite FROM papers WHERE id=?', (identifier,)).fetchone()
    if not row: raise ValueError('未找到这篇论文，请重新检索')
    item = json.loads(row['body'])
    item['favorite'] = bool(row['favorite'])
    return item


def verify_download_url(url):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('仅支持公开 HTTPS 论文链接')
    if parsed.port not in (None, 443): raise ValueError('下载链接的端口不受支持')
    addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError('下载链接不是公开网络地址')


def download_paper(identifier):
    item = lookup_paper(identifier)
    target = download_directory() / (identifier + '.pdf')
    if target.exists(): return dict(file='/files/' + target.name, filename=item['title'] + '.pdf')
    url = item.get('pdf', '')
    if not url: raise ValueError('未找到开放 PDF，请通过论文原文入口访问')
    temporary = download_directory() / (identifier + '.' + uuid.uuid4().hex + '.part')
    try:
        for _ in range(6):
            verify_download_url(url)
            response = requests.get(url, headers={'User-Agent': AGENT}, timeout=(6, 25), stream=True, allow_redirects=False)
            if response.is_redirect:
                location = response.headers.get('Location', '')
                response.close()
                url = urljoin(url, location)
                continue
            response.raise_for_status()
            size = 0
            with response, temporary.open('wb') as output:
                for chunk in response.iter_content(65536):
                    if not chunk: continue
                    if size == 0 and not chunk.lstrip().startswith(b'%PDF-'):
                        raise ValueError('来源返回的不是 PDF，请使用原文入口手动获取')
                    size += len(chunk)
                    if size > 80 * 1024 * 1024: raise ValueError('文件超过 80 MB，请使用原文入口下载')
                    output.write(chunk)
            if not size: raise ValueError('来源返回了空文件')
            temporary.replace(target)
            item['localFile'] = target.name
            with connect() as conn:
                conn.execute('UPDATE papers SET body=? WHERE id=?', (json.dumps(item, ensure_ascii=False), identifier))
            return dict(file='/files/' + target.name, filename=item['title'] + '.pdf')
        raise ValueError('来源重定向次数过多，请使用原文入口')
    finally:
        temporary.unlink(missing_ok=True)


def export_records(ids, kind):
    papers = [lookup_paper(str(identifier)) for identifier in ids[:500]]
    if kind == 'csv':
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(['标题', '作者', '年份', '期刊/会议', 'DOI', '原文', '开放PDF', '数据源', '摘要'])
        for p in papers:
            cells = [p['title'], '; '.join(p['authors']), p['year'] or '', p['venue'], p['doi'], p['url'], p['pdf'], '; '.join(p['sources']), p['abstract']]
            writer.writerow(["'" + str(v) if str(v).startswith(('=', '+', '-', '@')) else v for v in cells])
        return '\ufeff' + buf.getvalue()
    if kind == 'ris':
        lines = []
        for p in papers:
            lines += ['TY  - JOUR', 'TI  - ' + p['title']]
            lines += ['AU  - ' + clean(a) for a in p['authors']]
            for field, value in [('PY', p['year']), ('JO', p['venue']), ('DO', p['doi']), ('UR', p['url']), ('AB', p['abstract'])]:
                if value: lines.append(field + '  - ' + clean(value))
            lines += ['ER  - ', '']
        return '\n'.join(lines)
    if kind != 'bibtex': raise ValueError('不支持的导出格式')
    def bib(value):
        chars = {'\\': '\\textbackslash{}', '{': '\\{', '}': '\\}', '%': '\\%', '&': '\\&', '#': '\\#', '_': '\\_', '$': '\\$'}
        return ''.join(chars.get(c, c) for c in clean(value))
    entries = []
    for p in papers:
        fields = [('title', p['title']), ('author', ' and '.join(p['authors'])), ('year', p['year']), ('journal', p['venue']), ('doi', p['doi']), ('url', p['url'])]
        entries.append('@article{paper' + p['id'][:12] + ',\n' + ',\n'.join('  ' + k + ' = {' + bib(v) + '}' for k, v in fields if v) + '\n}')
    return '\n\n'.join(entries)


class Handler(SimpleHTTPRequestHandler):
    server_version = 'PaperDesk/0.1'

    def translate_path(self, path):
        relative = unquote(urlparse(path).path).lstrip('/') or 'index.html'
        candidate = (ROOT / 'web' / relative).resolve()
        if not candidate.is_relative_to((ROOT / 'web').resolve()): return str(ROOT / 'web' / '__missing__')
        return str(candidate)

    def end_headers(self):
        if getattr(self, '_space_cookie', None):
            self.send_header('Set-Cookie', self._space_cookie)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
        super().end_headers()

    def send_json(self, data, status=200):
        content = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        self._space_cookie = None
        route = urlparse(self.path).path
        if route.startswith('/api/') or route.startswith('/files/'):
            if self.headers.get('Host', '') not in {f'127.0.0.1:{PORT}', f'localhost:{PORT}'} or self.headers.get('Sec-Fetch-Site') == 'cross-site':
                return self.send_json(dict(error='请从当前站点页面访问'), 403)
        if route == '/api/health':
            health = dict(ok=True, app='PaperDesk', displayName='豪哥论文', mode='browser-spaces')
            if DEPLOYMENT == 'local': health['root'] = str(ROOT)
            return self.send_json(health)
        if not (route.startswith('/api/') or route.startswith('/files/')):
            return self.handle_get()
        identifier = SPACES.from_cookie(self.headers.get('Cookie'))
        if identifier is None and route == '/api/status':
            try:
                identifier = SPACES.create(self.client_ip())
                init_db(identifier)
                self._space_cookie = SPACES.cookie(identifier, self.is_https())
            except ValueError as exc:
                return self.send_json(dict(error=str(exc)), 429)
        if identifier is None:
            return self.send_json(dict(error='文献空间尚未就绪，请刷新页面'), 401)
        context = CURRENT_SPACE.set(identifier)
        try:
            return self.handle_get()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return
        finally:
            CURRENT_SPACE.reset(context)

    def client_ip(self):
        if self.client_address[0] in ('127.0.0.1', '::1'):
            forwarded = self.headers.get('X-Real-IP', '')
            try: return str(ipaddress.ip_address(forwarded))
            except ValueError: pass
        return self.client_address[0]

    def is_https(self):
        return self.client_address[0] in ('127.0.0.1', '::1') and self.headers.get('X-Forwarded-Proto') == 'https'

    def valid_origin(self):
        origin = self.headers.get('Origin')
        if not origin: return True  # Non-browser clients still need the private cookie + CSRF token.
        host = self.headers.get('Host', '')
        if self.client_address[0] in ('127.0.0.1', '::1'):
            host = self.headers.get('X-Forwarded-Host') or host
        parsed = urlparse(origin)
        return parsed.scheme in ('http', 'https') and parsed.netloc.casefold() == host.casefold()

    def handle_get(self):
        route = urlparse(self.path).path
        if route == '/api/journals':
            return self.send_json(dict(items=JOURNALS, categories=list(dict.fromkeys(j['category'] for j in JOURNALS))))
        if route == '/api/status':
            with connect() as conn:
                count = conn.execute('SELECT COUNT(*) FROM papers WHERE favorite=1').fetchone()[0]
            return self.send_json(dict(ok=True, app='PaperDesk', displayName='豪哥论文', deployment=DEPLOYMENT,
                                       favorites=count, sources=SOURCES, spaceMode='browser',
                                       token=SPACES.csrf(CURRENT_SPACE.get())))
        if route == '/api/space/entry':
            return self.send_json(dict(key=SPACES.token(CURRENT_SPACE.get())))
        if route == '/api/history':
            with connect() as conn:
                rows = conn.execute('SELECT id,query,status,created_at,options,snapshot FROM runs ORDER BY created_at DESC LIMIT 16').fetchall()
            return self.send_json(dict(items=[dict(id=r['id'], query=r['query'], status=r['status'], createdAt=r['created_at'],
                                                  journals=json.loads(r['options']).get('journals', []),
                                                  count=len(json.loads(r['snapshot']).get('results', []))) for r in rows]))
        if route == '/api/favorites':
            with connect() as conn:
                rows = conn.execute('SELECT body FROM papers WHERE favorite=1 ORDER BY saved_at DESC').fetchall()
            return self.send_json(dict(results=[json.loads(r['body']) | {'favorite': True} for r in rows]))
        if route.startswith('/api/search/'):
            identifier = route.rsplit('/', 1)[-1]
            with LOCK:
                job = JOBS.get(identifier)
                if job and job.get('_space') == CURRENT_SPACE.get(): return self.send_json(snapshot(job))
            with connect() as conn:
                row = conn.execute('SELECT snapshot,status FROM runs WHERE id=?', (identifier,)).fetchone()
            if not row: return self.send_json(dict(error='检索记录不存在'), 404)
            data = json.loads(row['snapshot'])
            data['status'] = row['status']
            # Refresh favorite flags when opening an old run.
            for p in data['results']: p['favorite'] = lookup_paper(p['id'])['favorite']
            return self.send_json(data)
        if route.startswith('/files/'):
            name = route.rsplit('/', 1)[-1]
            if not re.fullmatch(r'[a-f0-9]{32}\.pdf', name): return self.send_error(404)
            target = download_directory() / name
            if not target.exists(): return self.send_error(404)
            self.send_response(200)
            self.send_header('Content-Type', 'application/pdf')
            self.send_header('Content-Disposition', 'attachment; filename="' + name + '"')
            self.send_header('Content-Length', str(target.stat().st_size))
            self.end_headers()
            with target.open('rb') as stream:
                while chunk := stream.read(65536): self.wfile.write(chunk)
            return
        if route.startswith('/api/'): return self.send_json(dict(error='接口不存在'), 404)
        return super().do_GET()

    def do_POST(self):
        self._space_cookie = None
        identifier = SPACES.from_cookie(self.headers.get('Cookie'))
        context = CURRENT_SPACE.set(identifier)
        try:
            return self.handle_post()
        finally:
            CURRENT_SPACE.reset(context)

    def handle_post(self):
        try:
            host = self.headers.get('Host', '')
            allowed = {f'127.0.0.1:{PORT}', f'localhost:{PORT}'}
            if host not in allowed or not self.valid_origin():
                return self.send_json(dict(error='请从当前站点页面执行操作'), 403)
            if self.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/json':
                return self.send_json(dict(error='请求格式不正确'), 415)
            route = urlparse(self.path).path
            if route != '/api/space/restore':
                if CURRENT_SPACE.get() is None:
                    return self.send_json(dict(error='文献空间尚未就绪，请刷新页面'), 401)
                if not secrets.compare_digest(self.headers.get('X-PaperDesk-Token', ''), SPACES.csrf(CURRENT_SPACE.get())):
                    return self.send_json(dict(error='空间校验失败，请刷新页面重试'), 403)
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 256000: raise ValueError('请求内容大小不正确')
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict): raise ValueError('请求格式不正确')
            if route == '/api/space/restore':
                identifier = SPACES.resolve(payload.get('key'))
                if identifier is None: return self.send_json(dict(error='空间入口无效，请检查保存的完整链接'), 400)
                self._space_cookie = SPACES.cookie(identifier, self.is_https())
                return self.send_json(dict(ok=True))
            if route == '/api/search': return self.send_json(start_search(payload), 202)
            if route == '/api/favorite':
                item = lookup_paper(str(payload.get('id', '')))
                favorite = bool(payload.get('favorite'))
                with connect() as conn:
                    conn.execute('UPDATE papers SET favorite=?,saved_at=? WHERE id=?', (int(favorite), now(), item['id']))
                with LOCK:
                    for job in JOBS.values():
                        if job.get('_space') != CURRENT_SPACE.get(): continue
                        for p in job['results']:
                            if p['id'] == item['id']: p['favorite'] = favorite
                return self.send_json(dict(ok=True, favorite=favorite))
            if route == '/api/download':
                return self.send_json(download_paper(str(payload.get('id', ''))))
            if route == '/api/export':
                ids = payload.get('ids')
                if not isinstance(ids, list) or not ids: raise ValueError('请先选择要导出的论文')
                kind = str(payload.get('format', 'ris'))
                return self.send_json(dict(content=export_records(ids, kind), format=kind))
            return self.send_json(dict(error='接口不存在'), 404)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return self.send_json(dict(error=str(exc)[:200]), 400)
        except requests.RequestException:
            return self.send_json(dict(error='PDF 来源连接失败，请通过原文入口获取'), 502)
        except Exception as exc:
            print('Request failed:', type(exc).__name__, str(exc)[:200], flush=True)
            return self.send_json(dict(error='本地服务处理失败，请查看日志后重试'), 500)


if __name__ == '__main__':
    init_spaces()
    server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    server.daemon_threads = True
    print(f'PaperDesk ready: http://127.0.0.1:{PORT}', flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
