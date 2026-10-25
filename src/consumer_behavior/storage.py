"""One local SQLite store: traces, observed feedback and simulated feedback remain distinct."""
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class EventStore:
    def __init__(self, path: str | Path = ":memory:"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(path))
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS traces (
                run_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS feedback (
                feedback_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, user_id TEXT NOT NULL,
                source TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS memories (
                memory_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                source TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS memories_user ON memories(user_id, source);
            CREATE UNIQUE INDEX IF NOT EXISTS feedback_run_source ON feedback(run_id, source);
            CREATE TABLE IF NOT EXISTS profiles (
                user_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS research_sessions (
                session_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        """)

    def save_trace(self, trace: dict):
        with self.connection:
            self.connection.execute("INSERT INTO traces VALUES (?, ?, ?)",
                                    (trace["run_id"], trace["user_id"], json.dumps(trace, ensure_ascii=False)))

    def get_trace(self, run_id: str, user_id: str) -> dict:
        row = self.connection.execute("SELECT payload FROM traces WHERE run_id=? AND user_id=?",
                                      (run_id, user_id)).fetchone()
        if row is None:
            raise KeyError("该用户的运行记录不存在")
        return json.loads(row[0])

    def memories(self, user_id: str, source: str = "real") -> list[dict]:
        rows = self.connection.execute("SELECT payload FROM memories WHERE user_id=? AND source=? ORDER BY rowid",
                                       (user_id, source)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def clear_user(self, user_id: str):
        with self.connection:
            self.connection.execute("DELETE FROM research_sessions WHERE session_id=?", (user_id,))
            for table in ("traces", "feedback", "memories", "profiles"):
                self.connection.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))

    def save_research_session(self, session):
        with self.connection:
            self.connection.execute("INSERT OR REPLACE INTO research_sessions VALUES (?, ?)",
                (session["id"], json.dumps(session, ensure_ascii=False, allow_nan=False)))

    def get_research_session(self, session_id):
        row = self.connection.execute("SELECT payload FROM research_sessions WHERE session_id=?", (session_id,)).fetchone()
        if row is None:
            raise KeyError("研究会话不存在")
        return json.loads(row[0])

    def save_profile(self, profile):
        with self.connection:
            self.connection.execute("INSERT OR REPLACE INTO profiles VALUES (?, ?)",
                                    (profile["user_id"], json.dumps(profile, ensure_ascii=False)))

    def get_profile(self, user_id):
        row = self.connection.execute("SELECT payload FROM profiles WHERE user_id=?", (user_id,)).fetchone()
        if row is None:
            raise KeyError("用户会话不存在")
        return json.loads(row[0])

    def close(self):
        self.connection.close()
