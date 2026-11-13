"""Local research-group accounts. A lab shares its result catalog and GPU queue."""
from __future__ import annotations

import argparse
import hashlib
import hmac
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
import unicodedata
from contextlib import closing, contextmanager


def account_name(value):
    name = unicodedata.normalize("NFC", value.strip())
    if not re.fullmatch(r"[\w.-]{1,48}", name) or name in {".", ".."}:
        raise ValueError("담당자 이름에는 한글·영문·숫자·점·밑줄·붙임표를 사용하세요. / Use 1–48 letters, digits, dots, underscores or hyphens.")
    return name


class LabAccounts:
    def __init__(self, workspace):
        key = hashlib.sha256(os.fsencode(Path(workspace).resolve())).hexdigest()[:32]
        self.marker = Path(workspace).resolve() / ".nanocofold" / "lab-enabled"
        self.cookie = "nanocofold_lab_" + key
        self.db = Path.home() / ".config" / "nanocofold" / "labs" / key / "accounts.sqlite3"

    @property
    def enabled(self):
        return self.marker.exists() or self.db.exists()

    @contextmanager
    def connect(self):
        if not self.db.is_file():
            raise ValueError("연구실 계정 DB를 복원하세요. / Restore the lab account database.")
        with closing(sqlite3.connect(self.db, timeout=10)) as db, db:
            yield db

    def set_password(self, name, password, *, create=True):
        name = account_name(name)
        if not 10 <= len(password) <= 256:
            raise ValueError("비밀번호는 10–256자입니다. / Use a password of 10–256 characters.")
        salt = secrets.token_bytes(16)
        verifier = self._hash(password, salt)
        self.db.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.marker.exists() and not self.db.exists():
            raise ValueError("연구실 계정 DB를 복원하세요. / Restore the lab account database.")
        self.db.touch(mode=0o600, exist_ok=True)
        self.marker.parent.mkdir(parents=True, exist_ok=True)
        self.marker.touch(mode=0o600, exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS accounts(name TEXT PRIMARY KEY, salt BLOB NOT NULL, verifier BLOB NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, name TEXT NOT NULL, expires INTEGER NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires)")
            if create:
                try:
                    db.execute("INSERT INTO accounts VALUES (?,?,?)", (name, salt, verifier))
                except sqlite3.IntegrityError:
                    raise ValueError("이미 있는 계정입니다. / Account already exists.") from None
            elif not db.execute("UPDATE accounts SET salt=?, verifier=? WHERE name=?", (salt, verifier, name)).rowcount:
                raise ValueError("등록된 계정이 없습니다. / Unknown account.")
            db.execute("DELETE FROM sessions WHERE name=?", (name,))

    @staticmethod
    def _hash(password, salt):
        return hashlib.scrypt(password.encode(), salt=salt, n=2**15, r=8, p=1, dklen=32, maxmem=64*1024**2)

    def login(self, name, password):
        try:
            name = account_name(name)
        except ValueError:
            return None
        if not 1 <= len(password) <= 256:
            return None
        with self.connect() as db:
            row = db.execute("SELECT salt,verifier FROM accounts WHERE name=?", (name,)).fetchone()
            calculated = self._hash(password, row[0] if row else bytes(16))
            if not row or not hmac.compare_digest(calculated, row[1]):
                return None
            token = secrets.token_urlsafe(32)
            now = int(time.time())
            db.execute("DELETE FROM sessions WHERE expires<=?", (now,))
            # A simultaneous password reset revokes this login, too.
            added = db.execute("INSERT INTO sessions SELECT ?,name,? FROM accounts WHERE name=? AND verifier=?",
                               (self._token_hash(token),now+12*3600,name,calculated))
            return token if added.rowcount else None

    @staticmethod
    def _token_hash(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def user(self, token):
        if not token or len(token)>128:
            return None
        with self.connect() as db:
            row = db.execute("SELECT name FROM sessions WHERE token=? AND expires>?", (self._token_hash(token),int(time.time()))).fetchone()
            return row[0] if row else None

    def logout(self, token):
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE token=?", (self._token_hash(token),))

    def disable(self, name):
        name = account_name(name)
        with self.connect() as db:
            db.execute("DELETE FROM accounts WHERE name=?", (name,))
            db.execute("DELETE FROM sessions WHERE name=?", (name,))


def main(workspace, arguments=None):
    import getpass
    p = argparse.ArgumentParser(description="Manage NanoCoFold lab accounts on the installation computer.")
    p.add_argument("action", choices=("add", "password", "disable", "list"))
    p.add_argument("name", nargs="?")
    args = p.parse_args(arguments)
    lab = LabAccounts(workspace)
    if args.action == "list":
        if lab.enabled:
            with lab.connect() as db:
                for name, in db.execute("SELECT name FROM accounts ORDER BY name"):
                    print(name)
        return
    if not args.name:
        p.error("계정 이름을 입력하세요. / Enter an account name.")
    name = account_name(args.name)
    exists = False
    if lab.enabled:
        with lab.connect() as db:
            exists = bool(db.execute("SELECT 1 FROM accounts WHERE name=?", (name,)).fetchone())
    if args.action == "add" and exists:
        p.error("이미 있는 계정입니다. / Use password to change an existing account.")
    if args.action != "add" and not exists:
        p.error("등록된 계정이 없습니다. / Unknown account.")
    if args.action == "disable":
        lab.disable(name)
    else:
        password = getpass.getpass("비밀번호 / Password: ")
        if password != getpass.getpass("다시 입력 / Repeat: "):
            p.error("비밀번호가 일치하지 않습니다. / Passwords do not match.")
        lab.set_password(name, password, create=args.action == "add")
    print("완료 / Done:", name)


if __name__ == "__main__":
    import sys
    try:
        main(Path(sys.argv[1]), sys.argv[2:])
    except (OSError, ValueError) as exc:
        sys.exit(str(exc))
