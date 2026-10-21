"""Local authentication only. User documents and service results live in RESCS."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
import uuid


class Accounts:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "accounts.sqlite3"
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, username TEXT UNIQUE,
          name TEXT, role TEXT, salt TEXT, digest TEXT, created REAL);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id TEXT REFERENCES users(id),
          csrf TEXT, expires REAL);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, at REAL, user_id TEXT,
          action TEXT, detail TEXT);
        ''')
        self.db.commit()
        if os.name == "posix": self.path.chmod(0o600)

    @staticmethod
    def _hash(password, salt):
        return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()

    def empty(self):
        with self.lock: return self.db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0

    @staticmethod
    def public(row):
        return {k: row[k] for k in ("id", "username", "name", "role")}

    def create(self, username, password, name, *, first=False):
        if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{3,40}", username):
            raise ValueError("Username: 3–40 letters, numbers, dots, underscores or dashes.")
        if not isinstance(password, str) or not 12 <= len(password) <= 256:
            raise ValueError("Use a password between 12 and 256 characters.")
        if not isinstance(name, str) or not name.strip() or len(name) > 80:
            raise ValueError("A display name of 1–80 characters is required.")
        salt = secrets.token_hex(16)
        digest = self._hash(password, salt)
        with self.lock:
            if first and not self.empty(): raise ValueError("Initial setup is already complete.")
            try:
                with self.db:
                    user_id = uuid.uuid4().hex
                    self.db.execute("INSERT INTO users VALUES(?,?,?,?,?,?,?)",
                                    (user_id, username.lower(), name.strip(), "owner" if first else "member", salt, digest, time.time()))
            except sqlite3.IntegrityError as exc:
                raise ValueError("Username already exists.") from exc
            return self.public(self.db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())

    def login(self, username, password):
        if not isinstance(username, str) or not isinstance(password, str) or len(password) > 256:
            raise PermissionError("Invalid username or password.")
        with self.lock:
            row = self.db.execute("SELECT * FROM users WHERE username=?", (username.lower(),)).fetchone()
            salt = row["salt"] if row else "00" * 16
            actual = self._hash(password, salt)
            if row is None or not hmac.compare_digest(actual, row["digest"]):
                raise PermissionError("Invalid username or password.")
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            with self.db:
                self.db.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
                self.db.execute("INSERT INTO sessions VALUES(?,?,?,?)",
                                (hashlib.sha256(token.encode()).hexdigest(), row["id"], csrf, time.time()+43200))
            return token, csrf, self.public(row)

    def session(self, token):
        if not isinstance(token, str): return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.lock:
            row = self.db.execute("SELECT users.*,sessions.csrf FROM sessions JOIN users ON users.id=sessions.user_id WHERE token=? AND expires>?",
                                  (digest, time.time())).fetchone()
            if not row: return None
            return {**self.public(row), "csrf": row["csrf"]}

    def logout(self, token):
        with self.lock, self.db:
            self.db.execute("DELETE FROM sessions WHERE token=?", (hashlib.sha256(token.encode()).hexdigest(),))

    def profiles(self):
        with self.lock: return [self.public(row) for row in self.db.execute("SELECT * FROM users ORDER BY created")]

    def audit(self, user, action, detail=""):
        # Never record credentials or prompt bodies in the administrative log.
        with self.lock, self.db:
            self.db.execute("INSERT INTO audit(at,user_id,action,detail) VALUES(?,?,?,?)",
                            (time.time(), user, action, json.dumps(detail)[:300]))
            self.db.execute("DELETE FROM audit WHERE id NOT IN (SELECT id FROM audit ORDER BY id DESC LIMIT 2000)")

    def events(self):
        with self.lock: return [dict(row) for row in self.db.execute("SELECT at,user_id,action,detail FROM audit ORDER BY id DESC LIMIT 100")]

    def close(self):
        with self.lock: self.db.close()
