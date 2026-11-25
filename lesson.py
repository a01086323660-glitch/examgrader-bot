"""تحضير الدروس: مقابلة قصيرة ثم توليد نموذج احترافي وتصدير PDF/HTML."""
import html
import io
import os
from llm import LLMError, parse_json

FIELDS = (
    "day", "date_hijri", "date_greg", "grade", "section", "period",
    "subject", "topic", "methods", "resources",
    "warmup", "warmup_time", "outcomes", "outcomes_time", "path",
    "formative", "closure", "closure_time", "summative", "summative_time",
    "advanced", "remedial", "teacher_name", "page_no",
)

# حقول أساسية يُفضّل سؤالها إن ناقصت
CRITICAL = ("grade", "section", "subject", "topic", "period", "teacher_name")

ANALYZE_SYSTEM = (
    "You help an Arabic school teacher prepare an official lesson plan (تحضير درس). "
    "Input may be Arabic text and/or photos of textbook pages. "
    "Return ONLY JSON: {"
    "\"extracted\": {object with any of these keys if known: "
    + ", ".join(FIELDS)
    + "}, "
    "\"questions\": [{\"id\": str (field key), \"ask\": str (short Arabic question)}], "
    "\"notes\": str}. "
    "Ask at most 5 questions, only for missing CRITICAL info: grade, section, subject, topic, "
    "period, teacher_name, and optionally methods/resources/duration if totally unknown. "
    "Do not ask about evaluator notes. Infer topic/outcomes from the lesson content when possible. "
    "Questions must be in clear Arabic, one fact each."
)

GENERATE_SYSTEM = (
    "You are an expert Arabic curriculum lesson planner for school teachers. "
    "Build a complete professional lesson plan filling ALL fields. "
    "Return ONLY JSON object with exactly these string keys: "
    + ", ".join(FIELDS)
    + ". "
    "Write in formal clear Arabic suitable for official school inspection. "
    "outcomes: numbered learning outcomes. path: numbered lesson activities aligned to outcomes. "
    "Include realistic times in minutes for warmup_time, outcomes_time, closure_time, summative_time. "
    "formative and summative assessment should be practical. "
    "advanced and remedial activities must be brief and useful. "
    "Leave date_hijri/date_greg/day empty if unknown (teacher fills by hand). "
    "page_no may be empty. Never invent evaluator comments."
)

LABELS = {
    "day": "اليوم", "date_hijri": "التاريخ هـ", "date_greg": "التاريخ م",
    "grade": "الصف", "section": "الشعبة", "period": "الحصة",
    "subject": "المادة", "topic": "الموضوع",
    "methods": "طرائق التدريس", "resources": "موارد التعلم ووسائطه",
    "warmup": "التمهيد والتهيئة", "warmup_time": "زمن التمهيد",
    "outcomes": "مخرجات التعلم", "outcomes_time": "زمن المخرجات",
    "path": "مسار الدرس", "formative": "التقويم المرحلي",
    "closure": "إغلاق الدرس", "closure_time": "زمن الإغلاق",
    "summative": "التقويم النهائي", "summative_time": "زمن التقويم النهائي",
    "advanced": "نشاط للمتميزين", "remedial": "نشاط للأقل تحصيلاً",
    "teacher_name": "اسم المعلم", "page_no": "رقم الصفحة",
}


def _empty_plan():
    return {k: "" for k in FIELDS}


def _merge(base, extra):
    out = dict(base)
    for k, v in (extra or {}).items():
        if k in out and v is not None and str(v).strip():
            out[k] = str(v).strip()
    return out


def analyze_lesson(llm, texts, images):
    """يستخرج المعروف ويقترح أسئلة ناقصة."""
    prompt = "Lesson material follows.\n\n" + ("\n".join(texts) if texts else "(images only)")
    d = parse_json(llm.complete(ANALYZE_SYSTEM, prompt, images))
    extracted = _merge(_empty_plan(), d.get("extracted") or {})
    questions = []
    seen = set()
    for q in d.get("questions") or []:
        qid = str(q.get("id") or "").strip()
        ask = str(q.get("ask") or "").strip()
        if not qid or qid not in FIELDS or qid in seen:
            continue
        if extracted.get(qid):
            continue
        seen.add(qid)
        questions.append({"id": qid, "ask": ask or f"ما قيمة «{LABELS.get(qid, qid)}»؟"})
        if len(questions) >= 5:
            break
    # ضمان سؤال الحقول الحرجة الناقصة
    for c in CRITICAL:
        if not extracted.get(c) and c not in seen and len(questions) < 5:
            questions.append({"id": c, "ask": f"ما {LABELS[c]}؟"})
            seen.add(c)
    return {"extracted": extracted, "questions": questions, "notes": str(d.get("notes") or "")}


def generate_plan(llm, texts, images, extracted, answers):
    """يولّد التحضير الكامل بعد إجابات المعلم."""
    known = _merge(extracted, answers)
    lines = [f"{k}: {v}" for k, v in known.items() if v]
    prompt = (
        "Teacher answers and known fields:\n" + "\n".join(lines)
        + "\n\nOriginal lesson material:\n" + ("\n".join(texts) if texts else "(see images)")
    )
    d = parse_json(llm.complete(GENERATE_SYSTEM, prompt, images))
    plan = _merge(known, d)
    if not plan.get("topic") and not plan.get("subject"):
        raise LLMError("ما قدرت أطلع موضوع/مادة للتحضير")
    return plan


def preview_text(plan):
    """ملخص للمراجعة في تيليغرام."""
    keys = ("subject", "topic", "grade", "section", "period", "teacher_name",
            "methods", "resources", "warmup", "outcomes", "path",
            "formative", "closure", "summative", "advanced", "remedial")
    lines = ["📋 مسودة تحضير الدرس — راجع ثم اعتمد:"]
    for k in keys:
        v = (plan.get(k) or "").strip()
        if not v:
            continue
        short = v if len(v) <= 400 else v[:400] + "…"
        lines.append(f"\n• {LABELS[k]}:\n{short}")
    return "\n".join(lines)


def build_html(plan):
    """نموذج HTML احترافي مطابق تقريباً لدفتر التحضير — يُطبع PDF من المتصفح."""
    def e(k):
        return html.escape(plan.get(k) or "")

    def box(title, body, time_key=None):
        t = f'<div class="time">{html.escape(LABELS.get(time_key, "الزمن"))}: {e(time_key)}</div>' if time_key else ""
        return f'<div class="box"><div class="box-h">{html.escape(title)}</div>{t}<div class="box-b">{e(body)}</div></div>'

    return f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8"/>
<title>تحضير درس — {e('topic') or e('subject')}</title>
<style>
  @page {{ size: A4; margin: 12mm; }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: "Segoe UI", Tahoma, "Arial", sans-serif;
    color: #111; font-size: 12px; line-height: 1.45; margin: 0; padding: 0;
  }}
  .page {{
    border: 2px solid #1a1a1a; padding: 10px 12px; max-width: 210mm; margin: 0 auto;
    min-height: 277mm;
  }}
  h1 {{
    text-align: center; font-size: 16px; margin: 0 0 8px; letter-spacing: 0.5px;
  }}
  .meta {{
    display: grid; grid-template-columns: 1.2fr 1fr; gap: 6px 10px; margin-bottom: 8px;
  }}
  .row {{ display: flex; flex-wrap: wrap; gap: 6px 14px; margin: 3px 0; }}
  .f {{ display: inline-flex; gap: 4px; align-items: baseline; }}
  .f b {{ white-space: nowrap; }}
  .f span {{
    border-bottom: 1px dotted #333; min-width: 4.5em; padding: 0 4px; display: inline-block;
  }}
  .grid2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 6px; }}
  .box {{
    border: 1px solid #222; border-radius: 2px; margin-top: 6px; overflow: hidden;
  }}
  .box-h {{
    background: #f0f0f0; border-bottom: 1px solid #222; padding: 3px 8px; font-weight: 700;
  }}
  .box-b {{
    padding: 6px 8px; white-space: pre-wrap; min-height: 2.2em;
  }}
  .time {{
    float: left; direction: ltr; font-size: 11px; padding: 2px 8px; border-bottom: 1px solid #ccc;
  }}
  .split {{
    display: grid; grid-template-columns: 1fr 1fr; gap: 0; border: 1px solid #222; margin-top: 6px;
  }}
  .split > div {{ padding: 0; }}
  .split > div + div {{ border-right: 1px solid #222; }}
  .foot {{
    display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 14px; font-size: 11px;
  }}
  .sign {{ border-top: 1px solid #444; margin-top: 28px; padding-top: 4px; }}
  .eval {{
    border: 1px solid #222; margin-top: 10px; padding: 6px 8px; min-height: 70px;
  }}
  .eval h3 {{ margin: 0 0 4px; font-size: 12px; }}
  .hint {{
    text-align: center; color: #555; font-size: 10px; margin-top: 8px;
  }}
  @media print {{
    .hint {{ display: none; }}
    body {{ print-color-adjust: exact; -webkit-print-color-adjust: exact; }}
  }}
</style>
</head>
<body>
  <div class="page">
    <h1>تحضير درس</h1>
    <div class="meta">
      <div>
        <div class="row">
          <div class="f"><b>اليوم:</b><span>{e('day')}</span></div>
          <div class="f"><b>التاريخ:</b><span>{e('date_hijri')}</span><b>هـ</b>
            <span>{e('date_greg')}</span><b>م</b></div>
        </div>
        <div class="row">
          <div class="f"><b>الصف:</b><span>{e('grade')}</span></div>
          <div class="f"><b>الشعبة:</b><span>{e('section')}</span></div>
          <div class="f"><b>الحصة:</b><span>{e('period')}</span></div>
        </div>
        <div class="row">
          <div class="f"><b>المادة:</b><span>{e('subject')}</span></div>
        </div>
        <div class="row">
          <div class="f" style="flex:1"><b>الموضوع:</b><span style="flex:1;min-width:60%">{e('topic')}</span></div>
        </div>
      </div>
      <div>
        {box('طرائق التدريس', 'methods')}
        {box('موارد التعلم ووسائطه', 'resources')}
      </div>
    </div>

    {box('التمهيد والتهيئة', 'warmup', 'warmup_time')}

    <div class="split">
      <div>{box('مخرجات التعلم (المتوقع من الطالب في نهاية الدرس)', 'outcomes', 'outcomes_time')}</div>
      <div>{box('مسار الدرس (الأنشطة المحققة للأهداف)', 'path')}</div>
    </div>

    {box('التقويم المرحلي', 'formative')}
    {box('إغلاق الدرس', 'closure', 'closure_time')}
    {box('التقويم النهائي', 'summative', 'summative_time')}

    <div class="grid2">
      {box('نشاط للمتميزين', 'advanced')}
      {box('نشاط للأقل تحصيلاً', 'remedial')}
    </div>

    <div class="eval">
      <h3>ملاحظات المقيم</h3>
      <div>نقاط القوة: ………………………………</div>
      <div style="margin-top:8px">نقاط يجب معالجتها: ………………………………</div>
    </div>

    <div class="foot">
      <div class="sign">اسم وتوقيع المعلم: {e('teacher_name')}</div>
      <div class="sign">اسم وتوقيع المقيم مع الصفة: ……………</div>
    </div>
    <p class="hint">للحفظ PDF: افتح الملف في المتصفح ← طباعة ← الوجهة: حفظ كـ PDF</p>
  </div>
</body>
</html>
""".encode("utf-8")


def safe_filename(plan, ext="pdf"):
    topic = (plan.get("topic") or plan.get("subject") or "lesson").strip()
    cleaned = "".join(c if c.isalnum() or c in " _-" else "_" for c in topic)[:40].strip() or "lesson"
    return f"tahdeer_{cleaned}.{ext.lstrip('.')}"


def _arabic(text):
    """تشكيل وعرض عربي صحيح داخل PDF."""
    if text is None:
        return ""
    s = str(text).strip()
    if not s:
        return ""
    import arabic_reshaper
    from bidi.algorithm import get_display
    return get_display(arabic_reshaper.reshape(s))


def _find_font():
    candidates = [
        os.environ.get("TAHDEER_FONT", ""),
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\tahoma.ttf",
        r"C:\Windows\Fonts\segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    ]
    for p in candidates:
        if p and os.path.isfile(p):
            return p
    raise LLMError("ما لقيت خط TTF يدعم العربي. ضع المسار في TAHDEER_FONT")


def build_pdf(plan):
    """PDF A4 احترافي بنموذج تحضير الدرس (عربي RTL)."""
    from fpdf import FPDF

    font_path = _find_font()
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()
    pdf.add_font("Ar", "", font_path)
    pdf.add_font("Ar", "B", font_path)
    page_w = pdf.w - pdf.l_margin - pdf.r_margin

    def set_font(bold=False, size=10):
        pdf.set_font("Ar", "B" if bold else "", size)

    def cell_ar(w, h, text, border=0, align="R", fill=False, ln=0):
        pdf.cell(w, h, _arabic(text), border=border, align=align, fill=fill, new_x="RIGHT" if ln == 0 else "LMARGIN",
                 new_y="TOP" if ln == 0 else "NEXT")

    def multi_ar(w, h, text, border=0, align="R"):
        pdf.multi_cell(w, h, _arabic(text or " "), border=border, align=align)

    def section(title, body, time_val=None, min_h=16):
        set_font(True, 10)
        pdf.set_fill_color(235, 235, 235)
        head = title
        if time_val:
            head = f"{title}    |    الزمن: {time_val}"
        multi_ar(page_w, 5.5, head, border=1, align="C")
        set_font(False, 9)
        y0 = pdf.get_y()
        multi_ar(page_w, 5, body or " ", border=1, align="R")
        # ضمان ارتفاع أدنى بسيط
        used = pdf.get_y() - y0
        if used < min_h:
            pdf.rect(pdf.l_margin, y0, page_w, min_h)
            pdf.set_y(y0 + min_h)

    def pair_section(title_r, body_r, title_l, body_l, time_r=None):
        """عمودان متجاوران (مخرجات | مسار)."""
        col = page_w / 2
        set_font(True, 9)
        pdf.set_fill_color(235, 235, 235)
        y = pdf.get_y()
        x = pdf.l_margin
        tr = title_r + (f" | الزمن: {time_r}" if time_r else "")
        # عناوين
        pdf.set_xy(x + col, y)
        multi_ar(col, 5, tr, border=1, align="C")
        y1 = pdf.get_y()
        pdf.set_xy(x, y)
        multi_ar(col, 5, title_l, border=1, align="C")
        y2 = pdf.get_y()
        y_body = max(y1, y2)
        # أجسام
        set_font(False, 8.5)
        pdf.set_xy(x + col, y_body)
        multi_ar(col, 4.8, body_r or " ", border=1, align="R")
        y3 = pdf.get_y()
        pdf.set_xy(x, y_body)
        multi_ar(col, 4.8, body_l or " ", border=1, align="R")
        y4 = pdf.get_y()
        pdf.set_y(max(y3, y4))

    # عنوان
    set_font(True, 16)
    multi_ar(page_w, 9, "تحضير درس", border=0, align="C")
    pdf.ln(2)

    # رأس البيانات
    set_font(False, 9)
    g = plan.get
    line1 = f"اليوم: {g('day') or '……'}    التاريخ: {g('date_hijri') or '……'} هـ  /  {g('date_greg') or '……'} م"
    line2 = f"الصف: {g('grade') or '……'}    الشعبة: {g('section') or '……'}    الحصة: {g('period') or '……'}"
    line3 = f"المادة: {g('subject') or '……'}"
    line4 = f"الموضوع: {g('topic') or '……'}"
    for line in (line1, line2, line3, line4):
        multi_ar(page_w, 5.5, line, border=1, align="R")

    pdf.ln(1)
    section("طرائق التدريس", g("methods"), min_h=12)
    section("موارد التعلم ووسائطه", g("resources"), min_h=12)
    section("التمهيد والتهيئة", g("warmup"), g("warmup_time"), min_h=14)
    pair_section(
        "مخرجات التعلم", g("outcomes"),
        "مسار الدرس (الأنشطة)", g("path"),
        time_r=g("outcomes_time"),
    )
    section("التقويم المرحلي", g("formative"), min_h=12)
    section("إغلاق الدرس", g("closure"), g("closure_time"), min_h=12)
    section("التقويم النهائي", g("summative"), g("summative_time"), min_h=12)

    # متميزين / أقل تحصيلاً
    col = page_w / 2
    y = pdf.get_y()
    x = pdf.l_margin
    set_font(True, 9)
    pdf.set_fill_color(235, 235, 235)
    pdf.set_xy(x + col, y)
    multi_ar(col, 5, "نشاط للمتميزين", border=1, align="C")
    y1 = pdf.get_y()
    pdf.set_xy(x, y)
    multi_ar(col, 5, "نشاط للأقل تحصيلاً", border=1, align="C")
    y2 = pdf.get_y()
    yb = max(y1, y2)
    set_font(False, 8.5)
    pdf.set_xy(x + col, yb)
    multi_ar(col, 4.8, g("advanced") or " ", border=1, align="R")
    y3 = pdf.get_y()
    pdf.set_xy(x, yb)
    multi_ar(col, 4.8, g("remedial") or " ", border=1, align="R")
    pdf.set_y(max(y3, pdf.get_y()))

    pdf.ln(2)
    section("ملاحظات المقيم — نقاط القوة / نقاط يجب معالجتها", " ", min_h=22)

    pdf.ln(6)
    set_font(False, 9)
    multi_ar(page_w / 2, 5, f"اسم وتوقيع المعلم: {g('teacher_name') or '………………'}", align="R")
    pdf.set_y(pdf.get_y())
    pdf.set_x(pdf.l_margin)
    multi_ar(page_w / 2, 5, "اسم وتوقيع المقيم مع الصفة: ………………", align="R")

    out = io.BytesIO()
    pdf.output(out)
    return out.getvalue()


def build_export(plan):
    """يفضّل PDF؛ إن فشل يرجع HTML."""
    try:
        return safe_filename(plan, "pdf"), build_pdf(plan), "application/pdf"
    except Exception:
        return safe_filename(plan, "html"), build_html(plan), "text/html; charset=utf-8"
