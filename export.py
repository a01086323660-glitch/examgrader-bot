"""تصدير CSV وتقرير الصف. UTF-8 مع BOM حتى Excel يعرض العربي صح."""
import csv
import io


def _safe(v):
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def _n(x):
    return int(x) if float(x) == int(x) else round(float(x), 2)


def build_csv(exam, papers, final=False):
    qids = [q["id"] for q in exam["key"]["questions"]]
    buf = io.StringIO()
    w = csv.writer(buf)
    status_h = "حالة الاعتماد" if final else "يحتاج مراجعة"
    w.writerow(["اسم الطالب", "العلامة", "من", "النسبة %"] + [f"س{q}" for q in qids]
               + [status_h, "ملاحظات", "عُدّل يدوياً"])
    for p in papers:
        by = {a["id"]: a for a in p["answers"]}
        pct = round(100 * p["total"] / p["max_total"], 1) if p["max_total"] else 0
        if final:
            status = "معتمد"
        else:
            status = "نعم" if p["needs_review"] else "لا"
        w.writerow([_safe(p["student_name"] or "غير معروف"), _n(p["total"]), _n(p["max_total"]), pct]
                   + [_n(by[q]["awarded"]) if q in by else "" for q in qids]
                   + [status, _safe("; ".join(p.get("reasons") or [])),
                      "نعم" if p.get("edited") else "لا"])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def build_report(exam, papers):
    """نص تقرير جودة للصف."""
    n = len(papers)
    if not n:
        return f"تقرير: {exam.get('title') or 'امتحان'}\nما في أوراق بعد."
    flagged = [p for p in papers if p["needs_review"]]
    edited = sum(1 for p in papers if p.get("edited"))
    totals = [p["total"] for p in papers]
    mx = papers[0]["max_total"] or 0
    avg = round(sum(totals) / n, 2)
    avg_pct = round(100 * avg / mx, 1) if mx else 0
    hi = max(papers, key=lambda p: p["total"])
    lo = min(papers, key=lambda p: p["total"])
    # أكثر الأسئلة المعلّمة
    qflag = {}
    for p in papers:
        for a in p["answers"]:
            if a.get("flag"):
                qflag[a["id"]] = qflag.get(a["id"], 0) + 1
    top_q = sorted(qflag.items(), key=lambda x: -x[1])[:5]
    lines = [
        f"📊 تقرير الصف — {exam.get('title') or 'امتحان'} #{exam['id']}",
        f"عدد الأوراق: {n}",
        f"المعدل: {_n(avg)}/{_n(mx)} ({avg_pct}%)",
        f"أعلى علامة: {_n(hi['total'])} — {hi['student_name'] or 'غير معروف'} #{hi['id']}",
        f"أدنى علامة: {_n(lo['total'])} — {lo['student_name'] or 'غير معروف'} #{lo['id']}",
        f"تحتاج مراجعة: {len(flagged)}/{n}" + (" ✅ جاهز للاعتماد النهائي" if not flagged else " ⚠️"),
        f"عُدّلت يدوياً: {edited}",
    ]
    if top_q:
        lines.append("أسئلة الأكثر شكّاً: " + ", ".join(f"س{q}({c})" for q, c in top_q))
    if flagged:
        lines.append("أوراق بانتظار المراجعة:")
        for p in flagged[:15]:
            why = " | ".join(p["reasons"][:2]) if p["reasons"] else "—"
            lines.append(f"  ⚠️ #{p['id']} {p['student_name'] or 'غير معروف'}: {why}")
        if len(flagged) > 15:
            lines.append(f"  … و{len(flagged) - 15} أخرى")
        lines.append("اضغط «التالي للمراجعة» لمراجعتها واحدة واحدة، ثم «اعتماد النتائج».")
    else:
        lines.append("كل الأوراق معتمدة ✅ — يمكنك «اعتماد النتائج» لتصدير CSV النهائي.")
    return "\n".join(lines)


def review_ready(papers):
    """هل يمكن اعتماد النتائج النهائية؟"""
    return bool(papers) and not any(p["needs_review"] for p in papers)
