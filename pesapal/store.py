import json
import sqlite3
import threading

JSON_TABLES = ("pending", "outcomes")


class Store:
    """SQLite persistence, so users, the ledger and the hash chain survive restarts."""

    def __init__(self, path: str = ":memory:"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.RLock()
        with self.lock:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, name TEXT, phone TEXT UNIQUE, gender TEXT);
                CREATE TABLE IF NOT EXISTS entries(id INTEGER PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS reminders(id INTEGER PRIMARY KEY AUTOINCREMENT, to_id TEXT, text TEXT);
                CREATE TABLE IF NOT EXISTS feedback(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT,
                                                    clarity INT, trust INT, ease INT);
                CREATE TABLE IF NOT EXISTS pending(key TEXT PRIMARY KEY, data TEXT);
                CREATE TABLE IF NOT EXISTS outcomes(key TEXT PRIMARY KEY, data TEXT);
                CREATE TABLE IF NOT EXISTS docs(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, owner TEXT, data TEXT);
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
            """)
            self._migrate()

    def _migrate(self):
        have = {r[1] for r in self.db.execute("PRAGMA table_info(users)")}
        for name, ddl in [("email", "TEXT DEFAULT ''"), ("pin_hash", "TEXT DEFAULT ''"), ("verified", "INTEGER DEFAULT 1")]:
            if name not in have:
                self.db.execute(f"ALTER TABLE users ADD COLUMN {name} {ddl}")
        self.db.commit()

    def _run(self, sql, args=()):
        with self.lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur

    def _all(self, sql, args=()):
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    # meta
    def get_meta(self, key):
        row = self._all("SELECT value FROM meta WHERE key=?", (key,))
        return row[0][0] if row else None

    def set_meta(self, key, value):
        self._run("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, value))

    # users, entries, reminders, feedback
    def save_user(self, u):
        self._run("INSERT INTO users(id,name,phone,gender,email,pin_hash,verified) VALUES(?,?,?,?,?,?,?)",
                  (u["id"], u["name"], u["phone"], u["gender"], u["email"], u["pin_hash"], int(u["verified"])))

    def update_user(self, u):
        self._run("UPDATE users SET name=?,phone=?,gender=?,email=?,pin_hash=?,verified=? WHERE id=?",
                  (u["name"], u["phone"], u["gender"], u["email"], u["pin_hash"], int(u["verified"]), u["id"]))

    def load_users(self):
        return [dict(id=a, name=b, phone=c, gender=d, email=e or "", pin_hash=f or "", verified=bool(g))
                for a, b, c, d, e, f, g in self._all("SELECT id,name,phone,gender,email,pin_hash,verified FROM users")]

    def save_entry(self, e):
        self._run("INSERT INTO entries VALUES(?,?)", (e["id"], json.dumps(e)))

    def load_entries(self):
        return [json.loads(r[0]) for r in self._all("SELECT data FROM entries ORDER BY id")]

    def save_reminder(self, r):
        self._run("INSERT INTO reminders(to_id,text) VALUES(?,?)", (r["to_id"], r["text"]))

    def load_reminders(self):
        return [dict(to_id=a, text=b) for a, b in self._all("SELECT to_id,text FROM reminders ORDER BY id")]

    def save_feedback(self, f):
        self._run("INSERT INTO feedback(user_id,clarity,trust,ease) VALUES(?,?,?,?)",
                  (f["user_id"], f["clarity"], f["trust"], f["ease"]))

    def load_feedback(self):
        return [dict(user_id=a, clarity=b, trust=c, ease=d)
                for a, b, c, d in self._all("SELECT user_id,clarity,trust,ease FROM feedback")]

    # pending payments and their outcomes
    def put_json(self, table, key, data):
        assert table in JSON_TABLES
        self._run(f"INSERT OR REPLACE INTO {table} VALUES(?,?)", (key, json.dumps(data)))

    def del_json(self, table, key):
        assert table in JSON_TABLES
        self._run(f"DELETE FROM {table} WHERE key=?", (key,))

    def load_json(self, table):
        assert table in JSON_TABLES
        return {k: json.loads(v) for k, v in self._all(f"SELECT key,data FROM {table}")}

    def raw_update_entry(self, entry_id, data):
        """Used only by the tamper demo and the tests."""
        self._run("UPDATE entries SET data=? WHERE id=?", (json.dumps(data), entry_id))

    # money features (fund, budget, bills, kitties) are stored as small JSON documents
    def add_doc(self, kind, owner, data):
        return self._run("INSERT INTO docs(kind,owner,data) VALUES(?,?,?)", (kind, owner, json.dumps(data))).lastrowid

    def save_doc(self, doc_id, data):
        self._run("UPDATE docs SET data=? WHERE id=?", (json.dumps(data), doc_id))

    def del_doc(self, doc_id):
        self._run("DELETE FROM docs WHERE id=?", (doc_id,))

    def list_docs(self, kind, owner=None):
        sql, args = "SELECT id,owner,data FROM docs WHERE kind=?", [kind]
        if owner is not None:
            sql, args = sql + " AND owner=?", args + [owner]
        return [(i, o, json.loads(d)) for i, o, d in self._all(sql + " ORDER BY id", args)]
