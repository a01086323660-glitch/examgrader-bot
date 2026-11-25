"""بوت المعلم الاحترافي: تصحيح أوراق + تحضير دروس."""
import logging
import os
import time

import grader
import lesson
from export import build_csv, build_report, review_ready
from llm import LLMError, make_llm
from storage import Store
from telegram import TG

log = logging.getLogger("examgrader")

HELP = """أهلاً! بوت المعلم الاحترافي 📝

① تصحيح الأوراق
• «ورقة ورقة» أو «طابور أوراق»
• امتحان جديد → سلم → اعتماد → صور الأوراق → تقرير → اعتماد النتائج

② تحضير درس (دفتر رسمي)
1. «تحضير درس» ثم أرسل محتوى الدرس (نص و/أو صور كتاب)
2. «تم الدرس» — البوت يسأل فقط الناقص
3. أجب الأسئلة القصيرة
4. راجع المسودة ثم «اعتماد التحضير» → ملف HTML للطباعة/PDF

خط اليد والعربي مدعومان. صورة واضحة = نتيجة أفضل."""

BTN = {
    "تحضير درس": "/newlesson",
    "تم الدرس": "/done",
    "اعتماد التحضير": "/ok",
    "امتحان جديد": "/newexam",
    "تم السلم": "/done",
    "اعتماد السلم": "/ok",
    "ورقة ورقة": "/mode_one",
    "طابور أوراق": "/mode_batch",
    "الأوراق": "/papers",
    "للمراجعة": "/review",
    "التالي للمراجعة": "/next_review",
    "تقرير الصف": "/report",
    "حالة الطابور": "/queue",
    "إيقاف الطابور": "/stop_queue",
    "تصدير CSV": "/csv",
    "اعتماد النتائج": "/finalize",
    "الامتحانات": "/exams",
    "عرض ورقة": "/show",
    "تعديل علامة": "/fix",
    "تعديل اسم": "/name",
    "اعتماد ورقة": "/ok_paper",
    "حذف ورقة": "/delete",
    "اختيار امتحان": "/use",
    "المزيد": "/more",
    "رجوع": "/menu",
    "مساعدة": "/help",
    "إلغاء": "/cancel",
}

# لوحة قصيرة حتى لا تغطيها أزرار نظام الهاتف
MAIN_KB = [
    ["تحضير درس", "امتحان جديد"],
    ["ورقة ورقة", "طابور أوراق"],
    ["تقرير الصف", "اعتماد النتائج"],
    ["المزيد", "مساعدة", "إلغاء"],
]

MORE_KB = [
    ["تم السلم", "اعتماد السلم", "تم الدرس"],
    ["اعتماد التحضير", "التالي للمراجعة", "للمراجعة"],
    ["الأوراق", "حالة الطابور", "تصدير CSV"],
    ["عرض ورقة", "اعتماد ورقة", "حذف ورقة"],
    ["تعديل علامة", "تعديل اسم", "اختيار امتحان"],
    ["الامتحانات", "إيقاف الطابور", "رجوع"],
]

KEY_KB = [["تم السلم", "إلغاء"], ["مساعدة"]]
CONFIRM_KB = [["اعتماد السلم", "إلغاء"], ["مساعدة"]]
LESSON_KB = [["تم الدرس", "إلغاء"], ["مساعدة"]]
LESSON_ASK_KB = [["إلغاء"], ["مساعدة"]]
LESSON_OK_KB = [["اعتماد التحضير", "إلغاء"], ["مساعدة"]]
REVIEW_KB = [["التالي للمراجعة", "اعتماد ورقة", "تعديل علامة"],
             ["تعديل اسم", "تقرير الصف", "رجوع"]]

NEED_ARG = {
    "/show": "أرسل رقم الورقة (مثال: 3)",
    "/ok_paper": "أرسل رقم الورقة لاعتمادها (مثال: 3)",
    "/delete": "أرسل رقم الورقة لحذفها (مثال: 3)",
    "/use": "أرسل رقم الامتحان (شوف «الامتحانات» أولاً)",
    "/name": "أرسل: رقم_الورقة الاسم_الجديد\nمثال: 3 أحمد محمد",
    "/fix": "أرسل: رقم_الورقة رقم_السؤال العلامة\nمثال: 3 2 1.5",
}


def _n(x):
    return int(x) if float(x) == int(x) else round(float(x), 2)


def _bar(done, total, width=10):
    if total <= 0:
        return "░" * width
    filled = min(width, int(round(width * done / total)))
    return "█" * filled + "░" * (width - filled)


class Bot:
    def __init__(self, tg, llm, store, allowed=None, passes=2, threshold=0.75, settle=3.0):
        self.tg, self.llm, self.store = tg, llm, store
        self.allowed, self.passes, self.threshold, self.settle = allowed, passes, threshold, settle
        self.state = {}       # chat -> UI/key state
        self.pending = {}    # chat -> album buffer
        self.queue = {}      # chat -> [file_ids, ...]
        self.batch = {}      # chat -> progress stats
        self.busy = set()    # chats currently grading one paper
        self.grade_mode = {} # chat -> "one" | "batch"  (افتراضي: ورقة ورقة)
        self.menu = {}       # chat -> "main" | "more"

    def has_work(self):
        return bool(self.pending or any(self.queue.values()) or self.busy)

    def _mode(self, chat):
        return self.grade_mode.get(chat, "one")

    def _mode_label(self, chat):
        return "ورقة ورقة" if self._mode(chat) == "one" else "طابور أوراق"

    def _kb(self, chat):
        st = self.state.get(chat)
        mode = (st or {}).get("mode")
        if mode == "key":
            return KEY_KB
        if mode == "confirm":
            return CONFIRM_KB
        if mode == "lesson_collect":
            return LESSON_KB
        if mode == "lesson_ask":
            return LESSON_ASK_KB
        if mode == "lesson_confirm":
            return LESSON_OK_KB
        if mode == "reviewing":
            return REVIEW_KB
        if self.menu.get(chat) == "more":
            return MORE_KB
        return MAIN_KB

    def _send(self, chat, text, keyboard=None):
        kb = self._kb(chat) if keyboard is None else keyboard
        return self.tg.send(chat, text, keyboard=kb)

    def _batch(self, chat):
        return self.batch.setdefault(chat, {
            "done": 0, "total": 0, "ok": 0, "flagged": 0, "failed": 0})

    # ---------- dispatch ----------
    def handle_update(self, u):
        m = u.get("message")
        if not m:
            return
        chat = m["chat"]["id"]
        uid = (m.get("from") or {}).get("id")
        if m["chat"].get("type") != "private":
            return
        if self.allowed and uid not in self.allowed:
            self._send(chat, "هالبوت خاص. ما عندك صلاحية.")
            return
        try:
            self._route(chat, m)
        except LLMError as e:
            self._send(chat, f"⚠️ مشكلة مع خدمة الذكاء الاصطناعي: {e}")
        except Exception:
            log.exception("handler failed")
            self._send(chat, "⚠️ صار خطأ غير متوقع. جرب مرة ثانية.")

    def _image_id(self, m):
        if m.get("photo"):
            return max(m["photo"], key=lambda p: p.get("file_size", 0) or p["width"] * p["height"])["file_id"]
        d = m.get("document")
        if d and str(d.get("mime_type", "")).startswith("image/"):
            return d["file_id"]
        return None

    def _route(self, chat, m):
        text = (m.get("text") or m.get("caption") or "").strip()
        if text in BTN:
            text = BTN[text]
        st = self.state.get(chat)
        if st and st.get("mode") == "await_arg" and text and not text.startswith("/"):
            cmd, prompt_arg = st["cmd"], text
            self.state.pop(chat, None)
            self._settle_pending(chat)
            return self.command(chat, cmd, prompt_arg)
        if text.startswith("/"):
            self._settle_pending(chat)
            cmd, _, arg = text.partition(" ")
            return self.command(chat, cmd.split("@")[0].lower(), arg.strip())
        fid = self._image_id(m)
        if st and st.get("mode") == "lesson_ask" and text:
            return self._lesson_answer(chat, st, text)
        if st and st.get("mode") == "lesson_collect":
            if text:
                st["texts"].append(text)
            if fid:
                st["images"].append(fid)
            return
        if st and st.get("mode") == "lesson_confirm":
            return self._send(chat, "اضغط «اعتماد التحضير» للتصدير أو «إلغاء».")
        if st and st.get("mode") == "key":
            if text:
                st["texts"].append(text)
            if fid:
                st["images"].append(fid)
            return
        if st and st.get("mode") == "confirm":
            return self._send(chat, "اضغط «اعتماد السلم» أو «إلغاء».")
        if fid:
            return self.add_photo(chat, fid, m.get("media_group_id"))
        self._send(chat, "اختر «تحضير درس» أو «امتحان جديد»، أو اضغط «مساعدة».")

    # ---------- queue / grading ----------
    def add_photo(self, chat, fid, group):
        if not self.store.current_exam(chat):
            return self._send(chat, "ما في امتحان فعّال. اضغط «امتحان جديد» أول.")
        p = self.pending.get(chat)
        if p and group and p["group"] == group:
            p["file_ids"].append(fid)
            p["last"] = time.time()
            return
        if p:
            self._enqueue_pending(chat)
        self.pending[chat] = {"group": group, "file_ids": [fid], "last": time.time()}
        # لا نصحّح فوراً: ننتظر settle حتى تقدر ترسل دفعة كاملة

    def _enqueue_pending(self, chat):
        p = self.pending.pop(chat, None)
        if not p:
            return
        self.queue.setdefault(chat, []).append(p["file_ids"])
        b = self._batch(chat)
        b["total"] += 1
        waiting = len(self.queue[chat])
        if self._mode(chat) == "batch":
            self._send(chat, f"📥 أُضيفت للطابور — بانتظار: {waiting} | الإجمالي: {b['total']}")
        else:
            self._send(chat, f"📷 استلمت الورقة (وضع ورقة ورقة) — {'جاري التصحيح…' if chat not in self.busy else 'بالانتظار خلف ورقة حالية'}")

    def _settle_pending(self, chat):
        if chat in self.pending:
            self._enqueue_pending(chat)

    def tick(self):
        now = time.time()
        for chat in list(self.pending):
            if now - self.pending[chat]["last"] >= self.settle:
                self._enqueue_pending(chat)
        for chat in list(self.queue):
            if chat not in self.busy and self.queue.get(chat):
                self._grade_next(chat)

    def _grade_next(self, chat):
        q = self.queue.get(chat) or []
        if not q:
            return
        self.busy.add(chat)
        file_ids = q.pop(0)
        b = self._batch(chat)
        one = self._mode(chat) == "one"
        try:
            exam = self.store.current_exam(chat)
            if not exam:
                b["failed"] += 1
                b["done"] += 1
                return self._send(chat, "⚠️ ما في امتحان فعّال — توقّف التصحيح.")
            imgs = [(self.tg.download(f), "image/jpeg") for f in file_ids]
            if one:
                self._send(chat, f"⏳ عم صحّح ورقة ({len(imgs)} صفحة)...")
            else:
                cur = b["done"] + 1
                self._send(chat, f"⏳ تصحيح {cur}/{b['total']}  {_bar(cur - 1, b['total'])}\n"
                                 f"({len(imgs)} صفحة)")
            r = grader.grade_paper(self.llm, exam["key"], imgs, self.passes, self.threshold)
            pid = self.store.add_paper(exam["id"], r["name"], r["answers"], r["total"],
                                       r["max_total"], r["needs_review"], r["reasons"])
            b["done"] += 1
            msg = (f"ورقة #{pid}: {r['name'] or 'غير معروف'}\n"
                   f"العلامة: {_n(r['total'])}/{_n(r['max_total'])}")
            if not one:
                msg += f"\nالتقدّم: {b['done']}/{b['total']} {_bar(b['done'], b['total'])}"
            if r["needs_review"]:
                b["flagged"] += 1
                msg += ("\n⚠️ تحتاج مراجعة: " + " | ".join(r["reasons"])
                        + "\nاستخدم «التالي للمراجعة» لاحقاً.")
            else:
                b["ok"] += 1
                msg += "\n✅ ثقة عالية"
            left = len(self.queue.get(chat) or [])
            if one:
                if left:
                    msg += f"\nملاحظة: في طابور متبقّي {left} (أرسلت أكثر من ورقة)."
                else:
                    msg += "\nأرسل الورقة التالية متى ما جهزت."
                    self.batch.pop(chat, None)
            elif left:
                msg += f"\nمتبقي في الطابور: {left}"
            else:
                msg += (f"\n\n✅ انتهت الدفعة: {b['ok']} موثوقة، {b['flagged']} للمراجعة، "
                        f"{b['failed']} فشلت.\n«تقرير الصف» للمخلص.")
                self.batch.pop(chat, None)
            self._send(chat, msg)
        except LLMError as e:
            b["failed"] += 1
            b["done"] += 1
            self._send(chat, f"⚠️ فشل تصحيح ورقة ({e}). أعد إرسالها."
                             + ("" if one else f"\nالتقدّم: {b['done']}/{b['total']}"))
        except Exception:
            log.exception("grade failed")
            b["failed"] += 1
            b["done"] += 1
            self._send(chat, "⚠️ فشل تصحيح ورقة. أعد إرسالها."
                             + ("" if one else f"\nالتقدّم: {b['done']}/{b['total']}"))
        finally:
            self.busy.discard(chat)

    # ---------- commands ----------
    def command(self, chat, cmd, arg):
        s, tg = self.store, self.tg
        if cmd in ("/start", "/help"):
            self.menu[chat] = "main"
            return self._send(chat, HELP + f"\n\nالوضع الحالي: «{self._mode_label(chat)}»", keyboard=MAIN_KB)
        if cmd == "/more":
            self.menu[chat] = "more"
            return self._send(chat, "أزرار إضافية — اضغط «رجوع» للقائمة المختصرة.", keyboard=MORE_KB)
        if cmd == "/menu":
            self.menu[chat] = "main"
            if (self.state.get(chat) or {}).get("mode") == "reviewing":
                self.state.pop(chat, None)
            return self._send(chat, "القائمة الرئيسية.", keyboard=MAIN_KB)
        if cmd == "/mode_one":
            self.grade_mode[chat] = "one"
            return self._send(chat, "✅ تم تفعيل وضع «ورقة ورقة».\n"
                                    "أرسل ورقة واحدة، انتظر النتيجة، ثم أرسل التالية.")
        if cmd == "/mode_batch":
            self.grade_mode[chat] = "batch"
            return self._send(chat, "✅ تم تفعيل وضع «طابور أوراق».\n"
                                    "أرسل كل صور الصف دفعة واحدة — تُصفّ في طابور وتُصحَّح مع شريط تقدّم.")
        if cmd == "/cancel":
            self.state.pop(chat, None)
            self.pending.pop(chat, None)
            self.menu[chat] = "main"
            return self._send(chat, "تم الإلغاء (الطابور ما انمسح — استخدم «إيقاف الطابور» إن لزم).",
                              keyboard=MAIN_KB)
        if cmd == "/stop_queue":
            n = len(self.queue.pop(chat, []) or [])
            self.pending.pop(chat, None)
            self.batch.pop(chat, None)
            self.busy.discard(chat)
            return self._send(chat, f"تم إيقاف الطابور ومسح {n} ورقة كانت بانتظار التصحيح.",
                              keyboard=MAIN_KB)
        if cmd == "/queue":
            wait = len(self.queue.get(chat) or [])
            pend = 1 if chat in self.pending else 0
            b = self.batch.get(chat)
            lines = [f"الوضع: «{self._mode_label(chat)}»",
                     f"📬 الطابور: {wait} بانتظار التصحيح",
                     f"تجميع ألبوم جاري: {'نعم' if pend else 'لا'}"]
            if b:
                lines.append(f"التقدّم: {b['done']}/{b['total']} {_bar(b['done'], b['total'])}")
                lines.append(f"✅ {b['ok']} | ⚠️ {b['flagged']} | فشل {b['failed']}")
            if chat in self.busy:
                lines.append("الآن: جاري تصحيح ورقة…")
            return self._send(chat, "\n".join(lines))
        if cmd == "/newlesson":
            self.state[chat] = {"mode": "lesson_collect", "texts": [], "images": []}
            return self._send(chat, "📘 وضع تحضير الدرس\n"
                                    "أرسل محتوى الدرس (نص من عندك و/أو صور من الكتاب — عدة صفحات مسموح).\n"
                                    "لما تخلص اضغط «تم الدرس». سأسألك فقط عن الناقص ثم أجهّز النموذج الرسمي.")
        if cmd == "/newexam":
            self.state[chat] = {"mode": "key", "texts": [], "images": []}
            return self._send(chat, "ابعت سلم التصحيح مع الإجابات والعلامات (نص و/أو صور).\n"
                                    "إذا السلم صفحتين أو أكثر: ابعت كل الصفحات ثم «تم السلم».")
        st = self.state.get(chat)
        if cmd in NEED_ARG and not arg:
            # أثناء المراجعة: «اعتماد ورقة» يعتمد الورقة المعروضة دون رقم
            if cmd == "/ok_paper" and st and st.get("paper_id"):
                arg = str(st["paper_id"])
            else:
                self.state[chat] = {"mode": "await_arg", "cmd": cmd}
                return self._send(chat, NEED_ARG[cmd], keyboard=[["إلغاء"]])
        if cmd == "/done":
            if st and st.get("mode") == "lesson_collect":
                return self._lesson_finish_collect(chat, st)
            if not st or st.get("mode") != "key":
                return self._send(chat, "ابدأ بـ «امتحان جديد» أو «تحضير درس».")
            if not st["texts"] and not st["images"]:
                return self._send(chat, "لسا ما بعتّ شي.")
            self._send(chat, "⏳ عم أقرأ سلم التصحيح...")
            imgs = [(tg.download(f), "image/jpeg") for f in st["images"]]
            key = grader.parse_key(self.llm, st["texts"], imgs)
            self.state[chat] = {"mode": "confirm", "key": key}
            lines = [f"• س{q['id']} ({_n(q['max_score'])}): {q['answer']}" for q in key["questions"]]
            return self._send(chat, f"قرأت: {key['title']}\n" + "\n".join(lines)
                              + f"\nالمجموع: {_n(sum(q['max_score'] for q in key['questions']))}\n\n"
                                "إذا صح اضغط «اعتماد السلم».")
        if cmd == "/ok":
            if st and st.get("mode") == "lesson_confirm":
                return self._lesson_export(chat, st)
            if not st or st.get("mode") != "confirm":
                return self._send(chat, "ما في شيء بانتظار الاعتماد.")
            eid = s.add_exam(chat, st["key"]["title"], st["key"])
            self.state.pop(chat, None)
            self.batch.pop(chat, None)
            self.queue.pop(chat, None)
            return self._send(chat, f"✅ تم اعتماد الامتحان #{eid}.\n"
                                    f"الوضع الحالي: «{self._mode_label(chat)}».\n"
                                    "غيّره من الأزرار إن رغبت، ثم أرسل صور الأوراق.",
                              keyboard=MAIN_KB)
        if cmd == "/exams":
            ex = s.list_exams(chat)
            cur = s.current_exam(chat)
            return self._send(chat, "\n".join(
                f"{'👉 ' if cur and e['id'] == cur['id'] else ''}#{e['id']} {e['title']}"
                for e in ex) or "ما في امتحانات.")
        if cmd == "/use":
            if arg.isdigit() and s.get_exam(chat, int(arg)):
                s.set_current(chat, int(arg))
                return self._send(chat, f"الامتحان الفعّال: #{arg}")
            return self._send(chat, "رقم امتحان غير صحيح. شوف «الامتحانات».")

        exam = s.current_exam(chat)
        if not exam:
            return self._send(chat, "ما في امتحان فعّال. اضغط «امتحان جديد».")
        papers = s.list_papers(exam["id"])

        if cmd == "/report":
            return self._send(chat, build_report(exam, papers))
        if cmd == "/papers":
            if not papers:
                return self._send(chat, "ما في أوراق بعد.")
            lines = [f"#{p['id']} {p['student_name'] or 'غير معروف'}: "
                     f"{_n(p['total'])}/{_n(p['max_total'])}"
                     f"{' ⚠️' if p['needs_review'] else ' ✅'}"
                     f"{' ✎' if p.get('edited') else ''}" for p in papers]
            fl = sum(1 for p in papers if p["needs_review"])
            lines.append(f"\nالمجموع: {len(papers)} | للمراجعة: {fl}")
            return self._send(chat, "\n".join(lines))
        if cmd == "/review":
            fl = [p for p in papers if p["needs_review"]]
            if not fl:
                return self._send(chat, "ما في أوراق للمراجعة ✅ — يمكنك «اعتماد النتائج».")
            self.state[chat] = {"mode": "reviewing"}
            return self._send(chat, "⚠️ أوراق تحتاج مراجعة:\n" + "\n".join(
                f"#{p['id']} {p['student_name'] or 'غير معروف'}: " + " | ".join(p["reasons"][:2])
                for p in fl) + "\n\nاضغط «التالي للمراجعة» للبدء.", keyboard=REVIEW_KB)
        if cmd == "/next_review":
            fl = [p for p in papers if p["needs_review"]]
            if not fl:
                self.state.pop(chat, None)
                return self._send(chat, "انتهت المراجعة ✅ اضغط «اعتماد النتائج».", keyboard=MAIN_KB)
            self.state[chat] = {"mode": "reviewing", "paper_id": fl[0]["id"]}
            return self._show_paper(chat, fl[0], leftover=len(fl) - 1)
        if cmd == "/csv":
            if not papers:
                return self._send(chat, "ما في أوراق بعد.")
            fl = sum(1 for p in papers if p["needs_review"])
            cap = f"مسودة — {len(papers)} طالب"
            if fl:
                cap += f" | ⚠️ {fl} لسا للمراجعة (غير نهائي)"
            else:
                cap += " | جاهز — يُفضّل «اعتماد النتائج» للنسخة النهائية"
            tg.send_document(chat, f"draft_{exam['id']}.csv", build_csv(exam, papers, final=False), cap)
            return
        if cmd == "/finalize":
            if not papers:
                return self._send(chat, "ما في أوراق بعد.")
            if not review_ready(papers):
                fl = [p for p in papers if p["needs_review"]]
                return self._send(chat, "⛔ لا يمكن اعتماد النتائج وفي أوراق معلّمة.\n"
                                        f"المتبقي: {len(fl)} — استخدم «التالي للمراجعة» ثم أعد المحاولة.\n"
                                        + "\n".join(f"#{p['id']} {p['student_name'] or '?'}" for p in fl[:10]))
            tg.send_document(chat, f"final_{exam['id']}.csv", build_csv(exam, papers, final=True),
                             f"✅ نتائج نهائية معتمدة — {len(papers)} طالب — {exam.get('title') or ''}")
            return self._send(chat, "✅ تم تصدير الملف النهائي المعتمد.\n"
                                    "كل الأوراق اجتازت المراجعة أو كانت بثقة عالية.")

        # أوامر على ورقة
        parts = arg.split(None, 2 if cmd == "/fix" else 1)
        # اعتماد ورقة بدون رقم أثناء وضع المراجعة → الورقة الحالية
        if cmd == "/ok_paper" and (not parts or not parts[0].isdigit()):
            rid = (st or {}).get("paper_id")
            if rid:
                parts = [str(rid)]
            else:
                return self._send(chat, "لازم رقم الورقة. مثال: 3")
        if not parts or not parts[0].isdigit():
            return self._send(chat, "لازم رقم الورقة. مثال بعد الزر: 3")
        p = s.get_paper(chat, int(parts[0]))
        if not p or p["exam_id"] != exam["id"]:
            return self._send(chat, "ما لقيت هالورقة بالامتحان الفعّال.")
        if cmd == "/show":
            return self._show_paper(chat, p)
        if cmd == "/name":
            if len(parts) < 2 or not parts[1].strip():
                return self._send(chat, "مثال: 3 أحمد محمد")
            s.update_paper(p["id"], student_name=parts[1].strip(), edited=1)
            return self._send(chat, "تم تعديل الاسم.")
        if cmd == "/ok_paper":
            s.update_paper(p["id"], needs_review=0, reasons=[])
            left = sum(1 for x in s.list_papers(exam["id"]) if x["needs_review"])
            msg = f"✅ اعتمدت الورقة #{p['id']}."
            if left:
                msg += f" متبقي للمراجعة: {left}. «التالي للمراجعة»."
                self.state[chat] = {"mode": "reviewing"}
            else:
                msg += " لا باقي مراجعات — يمكنك «اعتماد النتائج»."
                self.state.pop(chat, None)
            return self._send(chat, msg, keyboard=REVIEW_KB if left else MAIN_KB)
        if cmd == "/delete":
            s.delete_paper(chat, p["id"])
            return self._send(chat, "تم الحذف.")
        if cmd == "/fix":
            if len(parts) < 3:
                return self._send(chat, "مثال: 3 2 1.5  (ورقة 3، سؤال 2، علامة 1.5)")
            a = next((x for x in p["answers"] if x["id"] == parts[1]), None)
            try:
                val = float(parts[2].replace(",", "."))
            except ValueError:
                return self._send(chat, "العلامة لازم تكون رقم.")
            if not a or not 0 <= val <= a["max"]:
                return self._send(chat, "سؤال غير موجود أو العلامة خارج المدى.")
            a["awarded"], a["confidence"], a["flag"] = val, 1.0, False
            total = round(sum(x["awarded"] for x in p["answers"]), 2)
            s.update_paper(p["id"], answers=p["answers"], total=total, edited=1)
            return self._send(chat, f"تم. المجموع الجديد: {_n(total)}/{_n(p['max_total'])}. "
                                    f"لما تخلص اضغط «اعتماد ورقة»"
                                    + (f" (أو أرسل {p['id']})" if not (st or {}).get("paper_id") else "")
                                    + ".")
        self._send(chat, "أمر غير معروف. اضغط «مساعدة».")

    def _show_paper(self, chat, p, leftover=None):
        lines = [f"ورقة #{p['id']}: {p['student_name'] or 'غير معروف'} — "
                 f"{_n(p['total'])}/{_n(p['max_total'])}"
                 + (" ⚠️" if p["needs_review"] else " ✅")]
        if p.get("reasons"):
            lines.append("سبب المراجعة: " + " | ".join(p["reasons"]))
        for a in p["answers"]:
            lines.append(f"{'⚠️' if a.get('flag') else '•'} س{a['id']}: "
                         f"{_n(a['awarded'])}/{_n(a['max'])} | قرأت: {a['read_answer']} | {a['reason']}")
        if leftover is not None:
            lines.append(f"\nمتبقي بعد هذه: {leftover}")
            lines.append("عدّل إن لزم ثم «اعتماد ورقة»، أو «التالي للمراجعة».")
        return self._send(chat, "\n".join(lines), keyboard=REVIEW_KB)

    # ---------- lesson prep ----------
    def _lesson_imgs(self, st):
        return [(self.tg.download(f), "image/jpeg") for f in st.get("images") or []]

    def _lesson_finish_collect(self, chat, st):
        if not st.get("texts") and not st.get("images"):
            return self._send(chat, "لسا ما بعتّ محتوى الدرس.")
        self._send(chat, "⏳ عم أقرأ الدرس وأحضّر الأسئلة الناقصة...")
        imgs = self._lesson_imgs(st)
        info = lesson.analyze_lesson(self.llm, st["texts"], imgs)
        answers = {}
        questions = info["questions"]
        if not questions:
            return self._lesson_generate(chat, st, info["extracted"], answers)
        q0 = questions[0]
        self.state[chat] = {
            "mode": "lesson_ask", "texts": st["texts"], "images": st["images"],
            "extracted": info["extracted"], "questions": questions, "qi": 0, "answers": answers,
        }
        known = [f"• {lesson.LABELS[k]}: {v}" for k, v in info["extracted"].items() if v]
        head = "فهمت من الدرس:\n" + "\n".join(known[:8]) + "\n\n" if known else ""
        return self._send(chat, head + f"سؤال 1/{len(questions)}:\n{q0['ask']}")

    def _lesson_answer(self, chat, st, text):
        qs = st["questions"]
        qi = st["qi"]
        if qi >= len(qs):
            return self._lesson_generate(chat, st, st["extracted"], st["answers"])
        st["answers"][qs[qi]["id"]] = text.strip()
        st["qi"] = qi + 1
        if st["qi"] >= len(qs):
            return self._lesson_generate(chat, st, st["extracted"], st["answers"])
        nq = qs[st["qi"]]
        return self._send(chat, f"سؤال {st['qi'] + 1}/{len(qs)}:\n{nq['ask']}")

    def _lesson_generate(self, chat, st, extracted, answers):
        self._send(chat, "⏳ عم أكتب تحضير الدرس الكامل...")
        imgs = self._lesson_imgs(st)
        plan = lesson.generate_plan(self.llm, st.get("texts") or [], imgs, extracted, answers)
        self.state[chat] = {
            "mode": "lesson_confirm", "texts": st.get("texts") or [],
            "images": st.get("images") or [], "plan": plan,
        }
        return self._send(chat, lesson.preview_text(plan)
                          + "\n\nإذا مناسب اضغط «اعتماد التحضير» للتصدير."
                            "\nللتعديل: «إلغاء» ثم ابدأ «تحضير درس» من جديد بمعلومات أوضح.")

    def _lesson_export(self, chat, st):
        plan = st.get("plan")
        if not plan:
            return self._send(chat, "ما في تحضير جاهز.")
        name, data, mime = lesson.build_export(plan)
        cap = "✅ تحضير درس PDF جاهز للطباعة/التسليم" if name.endswith(".pdf") else \
              "✅ تحضير HTML — افتحه بالمتصفح ثم طباعة ← PDF"
        self.tg.send_document(chat, name, data, caption=cap, mime=mime)
        self.state.pop(chat, None)
        return self._send(chat, "تم تصدير التحضير بنجاح. يمكنك بدء «تحضير درس» جديد.",
                          keyboard=MAIN_KB)


def load_env_file(path=".env"):
    """يحمّل .env بدون مكتبات خارجية (للتشغيل المجاني السهل)."""
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    load_env_file(os.environ.get("ENV_FILE", ".env"))
    env = os.environ
    if not env.get("BOT_TOKEN"):
        raise SystemExit("ناقص BOT_TOKEN — ضع المفاتيح في ملف .env أو متغيرات البيئة")
    if not env.get("GEMINI_API_KEY") and not env.get("OPENAI_API_KEY"):
        raise SystemExit("ناقص مفتاح الذكاء الاصطناعي (GEMINI_API_KEY أو OPENAI_API_KEY)")
    allowed = {int(x) for x in env.get("ALLOWED_USER_IDS", "").replace(" ", "").split(",") if x}
    if not allowed:
        log.warning("ALLOWED_USER_IDS غير مضبوط — البوت مفتوح لأي مستخدم يعرف الرابط")
    default_db = "/data/examgrader.db" if os.path.isdir("/data") else "examgrader.db"
    bot = Bot(TG(env["BOT_TOKEN"]), make_llm(env), Store(env.get("DB_PATH", default_db)),
              allowed or None, int(env.get("GRADE_PASSES", "2")),
              float(env.get("CONFIDENCE_THRESHOLD", "0.75")),
              float(env.get("SETTLE_SECONDS", "3")))
    log.info("البوت يعمل — تصحيح أوراق + تحضير دروس (إيقاف: Ctrl+C)")
    offset = 0
    while True:
        try:
            timeout = 1 if bot.has_work() else 30
            for u in bot.tg.get_updates(offset, timeout=timeout):
                offset = u["update_id"] + 1
                bot.handle_update(u)
            bot.tick()
        except Exception:
            log.exception("loop error")
            time.sleep(3)


if __name__ == "__main__":
    main()
