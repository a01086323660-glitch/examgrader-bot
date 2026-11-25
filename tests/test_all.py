import csv, io, json, os, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import grader, llm, export
from bot import Bot
from storage import Store


class FakeTG:
    def __init__(self): self.sent, self.docs = [], []
    def send(self, chat, text, keyboard=None, remove_keyboard=False): self.sent.append(text)
    def download(self, fid): return b"img-" + fid.encode()
    def send_document(self, chat, name, content, caption="", mime="application/octet-stream"):
        self.docs.append((name, content, caption, mime))


KEY = {"title": "رياضيات", "questions": [{"id": "1", "answer": "5", "max_score": 2, "notes": ""},
                                          {"id": "2", "answer": "x=3", "max_score": 3, "notes": "partial ok"}]}


class FakeLLM:
    """يجاوب حسب نوع الطلب. paper_answers: قائمة ردود متتالية."""
    def __init__(self, papers): self.papers, self.calls = list(papers), []
    def complete(self, system, prompt, images):
        self.calls.append((system[:20], len(images)))
        if system.startswith("You extract"):
            return "```json\n" + json.dumps(KEY) + "\n```"
        return json.dumps(self.papers.pop(0))


def paper(name="سارة", nc=0.95, a1=2, c1=0.95, a2=3, c2=0.9):
    return {"student_name": name, "name_confidence": nc, "answers": [
        {"id": "1", "read_answer": "5", "awarded": a1, "confidence": c1, "reason": "ok"},
        {"id": "2", "read_answer": "x=3", "awarded": a2, "confidence": c2, "reason": "ok"}]}


def msg(text=None, photo=None, group=None, uid=1):
    m = {"chat": {"id": uid, "type": "private"}, "from": {"id": uid}}
    if text: m["text"] = text
    if photo: m["photo"] = [{"file_id": photo, "width": 10, "height": 10, "file_size": 5}]
    if group: m["media_group_id"] = group
    return {"message": m}


def make(papers, **kw):
    tg, st = FakeTG(), Store(":memory:")
    return Bot(tg, FakeLLM(papers), st, settle=0, **kw), tg, st


def setup_exam(b):
    for u in (msg("/newexam"), msg("1) 5 (2 درجة)"), msg(photo="k1"), msg("/done"), msg("/ok")):
        b.handle_update(u)


def grade_photo(b, photo="p1", group=None):
    b.handle_update(msg(photo=photo, group=group))
    b.tick()


class T(unittest.TestCase):
    def test_full_flow_and_csv(self):
        b, tg, st = make([paper(), paper(), paper("خالد", a2=1.5), paper("خالد", a2=1.5)])
        setup_exam(b)
        self.assertIn("اعتماد الامتحان", tg.sent[-1])
        grade_photo(b, "p1")
        self.assertIn("سارة", tg.sent[-1]); self.assertIn("5/5", tg.sent[-1]); self.assertIn("✅", tg.sent[-1])
        grade_photo(b, "p2")
        self.assertIn("3.5/5", tg.sent[-1])
        b.handle_update(msg("/csv"))
        name, data, cap, _mime = tg.docs[0]
        self.assertTrue(name.startswith("draft_"))
        self.assertTrue(data.startswith(b"\xef\xbb\xbf"))
        rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
        self.assertEqual(rows[1][:4], ["سارة", "5", "5", "100.0"])
        self.assertEqual(rows[2][:3], ["خالد", "3.5", "5"])

    def test_batch_queue_progress(self):
        answers = [paper("أ"), paper("أ"), paper("ب"), paper("ب"), paper("ج"), paper("ج")]
        b, tg, st = make(answers)
        setup_exam(b)
        b.handle_update(msg("/mode_batch"))
        b.handle_update(msg(photo="1"))
        b.handle_update(msg(photo="2"))
        b.handle_update(msg(photo="3"))
        self.assertEqual(len(st.list_papers(1)), 0)  # لسا بالطابور
        b.tick()  # تصحّح واحدة
        self.assertEqual(len(st.list_papers(1)), 1)
        self.assertIn("1/3", "\n".join(tg.sent))
        b.tick(); b.tick()
        self.assertEqual(len(st.list_papers(1)), 3)
        self.assertIn("انتهت الدفعة", "\n".join(tg.sent))

    def test_mode_one_vs_batch_messages(self):
        b, tg, st = make([paper(), paper()])
        setup_exam(b)
        self.assertEqual(b._mode(1), "one")
        b.handle_update(msg("/mode_one"))
        grade_photo(b, "p")
        self.assertIn("عم صحّح ورقة", "\n".join(tg.sent))
        self.assertNotIn("انتهت الدفعة", tg.sent[-1])
        b2, tg2, st2 = make([paper(), paper()])
        setup_exam(b2)
        b2.handle_update(msg("/mode_batch"))
        grade_photo(b2, "p")
        self.assertIn("1/1", "\n".join(tg2.sent))
        self.assertIn("انتهت الدفعة", "\n".join(tg2.sent))

    def test_album_is_one_paper(self):
        b, tg, st = make([paper(), paper()])
        setup_exam(b)
        for f in ("a", "b", "c"): b.handle_update(msg(photo=f, group="g1"))
        self.assertEqual(len(st.list_papers(1)), 0)
        b.tick()
        self.assertEqual(len(st.list_papers(1)), 1)
        self.assertEqual(b.llm.calls[-1][1], 3)

    def test_disagreement_and_low_confidence_flag(self):
        b, tg, st = make([paper(a2=3), paper(a2=1)])
        setup_exam(b); grade_photo(b, "p")
        self.assertIn("⚠️", tg.sent[-1])
        self.assertTrue(st.list_papers(1)[0]["needs_review"])
        b2, tg2, st2 = make([paper(c1=0.3), paper(c1=0.3)])
        setup_exam(b2); grade_photo(b2, "p")
        self.assertTrue(st2.list_papers(1)[0]["needs_review"])
        b3, tg3, st3 = make([paper(name="", nc=0.2), paper(name="", nc=0.2)])
        setup_exam(b3); grade_photo(b3, "p")
        self.assertIn("الاسم", st3.list_papers(1)[0]["reasons"][0])

    def test_clamp_and_missing_question(self):
        d = paper(a1=99); d["answers"].pop()
        b, tg, st = make([d, d], passes=2)
        setup_exam(b); grade_photo(b, "p")
        p = st.list_papers(1)[0]
        self.assertEqual(p["total"], 2)
        self.assertTrue(p["needs_review"])

    def test_fix_name_ok_delete(self):
        b, tg, st = make([paper(a2=1), paper(a2=1)])
        setup_exam(b); grade_photo(b, "p")
        b.handle_update(msg("/fix 1 2 2.5")); self.assertIn("4.5/5", tg.sent[-1])
        b.handle_update(msg("/fix 1 2 9")); self.assertIn("خارج", tg.sent[-1])
        b.handle_update(msg("/name 1 ليلى")); b.handle_update(msg("/ok_paper 1"))
        p = st.list_papers(1)[0]
        self.assertEqual((p["student_name"], p["needs_review"], p["total"]), ("ليلى", 0, 4.5))
        b.handle_update(msg("/delete 1")); self.assertEqual(st.list_papers(1), [])

    def test_finalize_requires_review(self):
        b, tg, st = make([paper(a2=3), paper(a2=1)])  # اختلاف بين المرورين → مراجعة
        setup_exam(b); grade_photo(b, "p")
        self.assertTrue(st.list_papers(1)[0]["needs_review"])
        b.handle_update(msg("/finalize"))
        self.assertIn("لا يمكن اعتماد", tg.sent[-1])
        self.assertEqual(tg.docs, [])
        b.handle_update(msg("/ok_paper 1"))
        b.handle_update(msg("/finalize"))
        self.assertTrue(tg.docs[-1][0].startswith("final_"))
        self.assertIn("نهائية معتمدة", tg.docs[-1][2])
        rows = list(csv.reader(io.StringIO(tg.docs[-1][1].decode("utf-8-sig"))))
        self.assertEqual(rows[0][-3], "حالة الاعتماد")
        self.assertEqual(rows[1][-3], "معتمد")


class LessonLLM:
    def __init__(self): self.calls = []
    def complete(self, system, prompt, images):
        self.calls.append(system[:24])
        if system.startswith("You help an Arabic"):
            return json.dumps({
                "extracted": {"subject": "رياضيات", "topic": "الجمع ضمن 100"},
                "questions": [{"id": "grade", "ask": "ما الصف؟"},
                              {"id": "section", "ask": "ما الشعبة؟"},
                              {"id": "teacher_name", "ask": "ما اسمك؟"}],
                "notes": "",
            })
        return json.dumps({
            "day": "", "date_hijri": "", "date_greg": "", "grade": "2", "section": "أ",
            "period": "1", "subject": "رياضيات", "topic": "الجمع ضمن 100",
            "methods": "حوار ومناقشة", "resources": "سبورة وبطاقات",
            "warmup": "مراجعة الجمع البسيط", "warmup_time": "5",
            "outcomes": "1) يجمع ضمن 100", "outcomes_time": "10",
            "path": "1) عرض 2) تدريب", "formative": "أسئلة شفوية",
            "closure": "تلخيص", "closure_time": "3",
            "summative": "تمارين قصيرة", "summative_time": "7",
            "advanced": "مسائل ذهنية", "remedial": "بطاقات مساندة",
            "teacher_name": "أحمد", "page_no": "",
        })


class LessonTests(unittest.TestCase):
    def test_lesson_interview_and_export(self):
        import lesson as lesson_mod
        tg, st = FakeTG(), Store(":memory:")
        b = Bot(tg, LessonLLM(), st, settle=0)
        b.handle_update(msg("/newlesson"))
        b.handle_update(msg("درس الجمع ضمن مئة من الكتاب"))
        b.handle_update(msg("/done"))
        self.assertIn("سؤال 1/", tg.sent[-1])
        b.handle_update(msg("الثاني"))   # صف
        b.handle_update(msg("أ"))        # شعبة
        b.handle_update(msg("أحمد"))     # معلم
        # قد يُسأل عن الحصة أيضاً
        if b.state.get(1) and b.state[1].get("mode") == "lesson_ask":
            b.handle_update(msg("1"))
        self.assertIn("مسودة تحضير", tg.sent[-1])
        b.handle_update(msg("/ok"))
        self.assertTrue(tg.docs[-1][0].endswith(".pdf"))
        self.assertTrue(tg.docs[-1][1].startswith(b"%PDF"))
        self.assertEqual(tg.docs[-1][3], "application/pdf")

    def test_html_has_sections(self):
        import lesson as lesson_mod
        plan = {k: "x" for k in lesson_mod.FIELDS}
        plan["topic"] = "المثنى"
        html = lesson_mod.build_html(plan).decode("utf-8")
        self.assertIn("مخرجات التعلم", html)
        self.assertIn("المثنى", html)
        self.assertIn("نشاط للمتميزين", html)

    def test_pdf_export(self):
        import lesson as lesson_mod
        plan = {k: "نص" for k in lesson_mod.FIELDS}
        plan["topic"] = "المثنى"
        pdf = lesson_mod.build_pdf(plan)
        self.assertTrue(pdf.startswith(b"%PDF"))
        name, data, mime = lesson_mod.build_export(plan)
        self.assertTrue(name.endswith(".pdf"))
        self.assertEqual(mime, "application/pdf")
        self.assertTrue(data.startswith(b"%PDF"))

    def test_report_and_next_review(self):
        b, tg, st = make([paper(a2=3), paper(a2=1), paper(), paper()])
        setup_exam(b)
        grade_photo(b, "a"); grade_photo(b, "b")
        b.handle_update(msg("/report"))
        self.assertIn("تقرير الصف", tg.sent[-1])
        self.assertIn("تحتاج مراجعة: 1/2", tg.sent[-1])
        b.handle_update(msg("/next_review"))
        self.assertIn("ورقة #1", tg.sent[-1])
        b.handle_update(msg("/ok_paper"))  # يعتمد الورقة الحالية بدون رقم
        self.assertEqual(st.list_papers(1)[0]["needs_review"], 0)
        self.assertTrue(export.review_ready(st.list_papers(1)))

    def test_stop_queue(self):
        b, tg, st = make([paper(), paper()] * 5)
        setup_exam(b)
        for i in range(3):
            b.handle_update(msg(photo=str(i)))
        b.handle_update(msg("/stop_queue"))
        self.assertEqual(b.queue.get(1), None)
        b.tick()
        self.assertEqual(st.list_papers(1), [])

    def test_isolation_and_allowlist(self):
        b, tg, st = make([paper(), paper()], allowed={1})
        setup_exam(b)
        b.handle_update(msg("/papers", uid=2)); self.assertIn("ما عندك صلاحية", tg.sent[-1])
        b.allowed = None
        b.handle_update(msg("/papers", uid=2)); self.assertIn("ما في امتحان", tg.sent[-1])

    def test_llm_failure_reported(self):
        b, tg, st = make([])
        setup_exam(b)
        b.llm.papers = None
        grade_photo(b, "p")
        self.assertIn("⚠️", tg.sent[-1])

    def test_csv_formula_escape(self):
        ex = {"key": KEY, "id": 1, "title": "ت"}
        p = {"student_name": "=cmd", "answers": [], "total": 1, "max_total": 5,
             "needs_review": 0, "reasons": [], "edited": 0}
        self.assertIn("'=cmd", export.build_csv(ex, [p]).decode("utf-8-sig"))

    def test_providers_request_shape(self):
        cap = {}
        def fake(url, payload, headers, timeout=120):
            cap.update(url=url, payload=payload, headers=headers)
            return cap["resp"]
        with mock.patch.object(llm, "_post_json", fake):
            cap["resp"] = {"candidates": [{"content": {"parts": [{"text": "{\"a\":1}"}]}}]}
            self.assertEqual(llm.Gemini("K", "m").complete("s", "p", [(b"x", "image/jpeg")]), '{"a":1}')
            self.assertIn("inline_data", json.dumps(cap["payload"])); self.assertEqual(cap["headers"]["x-goog-api-key"], "K")
            cap["resp"] = {"choices": [{"message": {"content": "{}"}}]}
            llm.OpenAI("K", "gpt").complete("s", "p", [(b"x", "image/jpeg")])
            self.assertIn("data:image/jpeg;base64,", json.dumps(cap["payload"]))
            self.assertEqual(cap["headers"]["Authorization"], "Bearer K")
        self.assertEqual(llm.parse_json("```json\n{\"a\": 2}\n```"), {"a": 2})
        with self.assertRaises(llm.LLMError): llm.parse_json("nope")
        with self.assertRaises(SystemExit): llm.make_llm({"LLM_PROVIDER": "x"})


if __name__ == "__main__":
    unittest.main()
