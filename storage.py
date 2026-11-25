"""SQLite storage. كل البيانات مربوطة بـ chat_id للمعلم."""
import json
import sqlite3
import time


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS exams(
            id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL,
            title TEXT, key_json TEXT NOT NULL, created REAL);
        CREATE TABLE IF NOT EXISTS papers(
            id INTEGER PRIMARY KEY AUTOINCREMENT, exam_id INTEGER NOT NULL,
            student_name TEXT, answers_json TEXT NOT NULL,
            total REAL, max_total REAL, needs_review INTEGER DEFAULT 0,
            reasons_json TEXT DEFAULT '[]', edited INTEGER DEFAULT 0, created REAL);
        CREATE TABLE IF NOT EXISTS chat_state(
            chat_id INTEGER PRIMARY KEY, current_exam_id INTEGER);
        """)

    def add_exam(self, chat_id, title, key):
        cur = self.db.execute("INSERT INTO exams(chat_id,title,key_json,created) VALUES(?,?,?,?)",
                              (chat_id, title, json.dumps(key, ensure_ascii=False), time.time()))
        self.db.commit()
        self.set_current(chat_id, cur.lastrowid)
        return cur.lastrowid

    def set_current(self, chat_id, exam_id):
        self.db.execute("INSERT INTO chat_state(chat_id,current_exam_id) VALUES(?,?) "
                        "ON CONFLICT(chat_id) DO UPDATE SET current_exam_id=excluded.current_exam_id",
                        (chat_id, exam_id))
        self.db.commit()

    def current_exam(self, chat_id):
        r = self.db.execute("SELECT e.* FROM chat_state s JOIN exams e ON e.id=s.current_exam_id "
                            "WHERE s.chat_id=? AND e.chat_id=?", (chat_id, chat_id)).fetchone()
        return self._exam(r)

    def get_exam(self, chat_id, exam_id):
        return self._exam(self.db.execute("SELECT * FROM exams WHERE id=? AND chat_id=?",
                                          (exam_id, chat_id)).fetchone())

    def list_exams(self, chat_id):
        return [self._exam(r) for r in self.db.execute(
            "SELECT * FROM exams WHERE chat_id=? ORDER BY id", (chat_id,))]

    @staticmethod
    def _exam(r):
        if not r:
            return None
        d = dict(r)
        d["key"] = json.loads(d.pop("key_json"))
        return d

    def add_paper(self, exam_id, name, answers, total, max_total, needs_review, reasons):
        cur = self.db.execute(
            "INSERT INTO papers(exam_id,student_name,answers_json,total,max_total,needs_review,"
            "reasons_json,created) VALUES(?,?,?,?,?,?,?,?)",
            (exam_id, name, json.dumps(answers, ensure_ascii=False), total, max_total,
             int(needs_review), json.dumps(reasons, ensure_ascii=False), time.time()))
        self.db.commit()
        return cur.lastrowid

    def _paper(self, r):
        if not r:
            return None
        d = dict(r)
        d["answers"] = json.loads(d.pop("answers_json"))
        d["reasons"] = json.loads(d.pop("reasons_json"))
        return d

    def get_paper(self, chat_id, paper_id):
        return self._paper(self.db.execute(
            "SELECT p.* FROM papers p JOIN exams e ON e.id=p.exam_id WHERE p.id=? AND e.chat_id=?",
            (paper_id, chat_id)).fetchone())

    def list_papers(self, exam_id):
        return [self._paper(r) for r in self.db.execute(
            "SELECT * FROM papers WHERE exam_id=? ORDER BY id", (exam_id,))]

    def update_paper(self, paper_id, **f):
        if "answers" in f:
            f["answers_json"] = json.dumps(f.pop("answers"), ensure_ascii=False)
        if "reasons" in f:
            f["reasons_json"] = json.dumps(f.pop("reasons"), ensure_ascii=False)
        cols = ",".join(f"{k}=?" for k in f)
        self.db.execute(f"UPDATE papers SET {cols} WHERE id=?", (*f.values(), paper_id))
        self.db.commit()

    def delete_paper(self, chat_id, paper_id):
        if not self.get_paper(chat_id, paper_id):
            return False
        self.db.execute("DELETE FROM papers WHERE id=?", (paper_id,))
        self.db.commit()
        return True
