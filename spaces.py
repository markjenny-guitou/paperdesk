"""Account-free private browser spaces. Never derive identity from an IP address."""
import base64
from collections import deque
import hashlib
import hmac
from http.cookies import CookieError, SimpleCookie
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import threading
import time

COOKIE_NAME = 'haoge_space'
COOKIE_AGE = 365 * 24 * 60 * 60


class BrowserSpaces:
    def __init__(self, data_dir):
        self.root = Path(data_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.directory = self.root / 'spaces'
        self.directory.mkdir(exist_ok=True)
        secret_path = self.root / '.space-secret'
        try:
            descriptor = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            self.secret = secret_path.read_bytes()
        else:
            self.secret = secrets.token_bytes(32)
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(self.secret)
        if len(self.secret) != 32:
            raise ValueError('空间密钥损坏，请从完整数据备份恢复，不要重新生成密钥')
        self.lock = threading.Lock()
        self.creation_times = {}
        self.global_creation_times = deque()

    def path(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r'[a-f0-9]{32}', identifier):
            raise ValueError('文献空间无效')
        return self.directory / identifier

    def _signature(self, value):
        return base64.urlsafe_b64encode(hmac.new(self.secret, value.encode(), hashlib.sha256).digest()).decode().rstrip('=')

    def token(self, identifier):
        self.path(identifier)
        return identifier + '.' + self._signature('space-v1:' + identifier)

    def resolve(self, token):
        if not isinstance(token, str) or not re.fullmatch(r'[a-f0-9]{32}\.[A-Za-z0-9_-]{43}', token):
            return None
        identifier = token.split('.', 1)[0]
        if not hmac.compare_digest(token, self.token(identifier)):
            return None
        return identifier if self.path(identifier).is_dir() else None

    def from_cookie(self, header):
        try:
            cookie = SimpleCookie()
            cookie.load(header or '')
            return self.resolve(cookie[COOKIE_NAME].value) if COOKIE_NAME in cookie else None
        except (CookieError, ValueError):
            return None

    def csrf(self, identifier):
        self.path(identifier)
        return self._signature('csrf-v1:' + identifier)

    def cookie(self, identifier, secure=False):
        return (f'{COOKIE_NAME}={self.token(identifier)}; Path=/; Max-Age={COOKIE_AGE}; '
                'HttpOnly; SameSite=Lax' + ('; Secure' if secure else ''))

    def create(self, client_ip):
        stamp = time.monotonic()
        # Prevent repeated cookie-free requests from creating unlimited SQLite files.
        with self.lock:
            while self.global_creation_times and self.global_creation_times[0] <= stamp - 86400:
                self.global_creation_times.popleft()
            self.creation_times = {k: v for k, v in self.creation_times.items() if v and v[-1] > stamp - 3600}
            events = self.creation_times.setdefault(client_ip, deque())
            while events and events[0] <= stamp - 3600:
                events.popleft()
            if len(events) >= 30 or len(self.creation_times) > 4096 or len(self.global_creation_times) >= 500:
                raise ValueError('当前网络创建空间过于频繁，请稍后再试；已有空间仍可使用')
            events.append(stamp)
            self.global_creation_times.append(stamp)
        identifier = secrets.token_hex(16)
        self.path(identifier).mkdir(mode=0o700)
        return identifier

    def migrate_legacy(self, initialize_database):
        """Copy the shared library into an owner-only space; retain the originals."""
        owner_file = self.root / '.owner-space.json'
        if owner_file.exists():
            owner = json.loads(owner_file.read_text(encoding='utf-8'))
            if self.resolve(owner['token']) != owner['space']:
                raise ValueError('原有文献空间的恢复信息无效，请检查数据备份')
            return owner['space']
        identifier = secrets.token_hex(16)
        destination = self.path(identifier)
        destination.mkdir(mode=0o700)
        legacy_database = self.root / 'library.sqlite3'
        if legacy_database.exists():
            source = sqlite3.connect(legacy_database)
            target = sqlite3.connect(destination / 'library.sqlite3')
            try:
                source.backup(target)
            finally:
                target.close()
                source.close()
            if (self.root / 'papers').is_dir():
                shutil.copytree(self.root / 'papers', destination / 'papers', dirs_exist_ok=True)
        initialize_database(identifier)
        owner = dict(space=identifier, token=self.token(identifier))
        descriptor = os.open(owner_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(owner, stream)
        return identifier
