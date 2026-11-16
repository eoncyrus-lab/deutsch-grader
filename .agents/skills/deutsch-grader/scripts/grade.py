from __future__ import annotations

import argparse
import getpass
import http.cookiejar
import html
import json
import os
import sys
import time
import urllib.error
import urllib.request


class ApiError(RuntimeError):
    pass


class Client:
    def __init__(self, base: str, timeout: float = 30.0):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.csrf = ""
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def request(self, method: str, path: str, payload: dict | None = None, csrf: bool = False):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method)
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if csrf:
            request.add_header("X-CSRF-Token", self.csrf)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(body).get("detail", body)
            except json.JSONDecodeError:
                detail = body
            raise ApiError(f"{exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            raise ApiError(f"cannot reach {self.base}: {exc.reason}") from exc

    def login(self, email: str, password: str) -> None:
        data = self.request("POST", "/api/auth/login", {"email": email, "password": password})
        self.csrf = data["user"]["csrf"]

    def students(self):
        return self.request("GET", "/api/students")

    def submit(self, student_id: str, title: str, text: str):
        return self.request("POST", "/api/submissions/text", {
            "student_id": student_id, "title": title, "text": text,
        }, csrf=True)

    def detail(self, submission_id: str):
        return self.request("GET", "/api/submissions/" + submission_id)


def pick_student(students, requested: str | None) -> dict:
    if requested:
        for student in students:
            if student["id"] == requested or student["pseudonym"] == requested:
                return student
        raise ApiError(f"student not found: {requested}")
    if len(students) == 1:
        return students[0]
    names = ", ".join(item["pseudonym"] for item in students) or "none"
    raise ApiError(f"multiple students; pass --student. Available: {names}")


def summarize(detail: dict) -> dict:
    scores = detail.get("scores") or {}
    ai = scores.get("ai") or {}
    errors = [e for e in detail.get("errors", []) if e.get("effective")]
    checklist = detail.get("checklist", [])
    return {
        "submission": {
            "id": detail["submission"]["id"],
            "student": detail["submission"].get("student"),
            "status": detail["submission"]["status"],
        },
        "run": detail.get("run"),
        "ai_score": ai,
        "effective_errors": [
            {
                "original": e.get("original"),
                "corrected": e.get("corrected"),
                "type": e.get("type"),
                "code": e.get("error_code"),
                "rule": e.get("rule"),
            }
            for e in errors
        ],
        "unresolved_checklist": [item for item in checklist if not item.get("valid")],
        "comments": detail.get("comments") or {},
    }


def render_report(detail: dict, source_url: str) -> str:
    submission = detail.get("submission") or {}
    run = detail.get("run") or {}
    scores = detail.get("scores") or {}
    ai = scores.get("ai") or {}
    reviewed = scores.get("reviewed") or {}
    final = scores.get("final") or {}
    errors = detail.get("errors") or []
    checklist = detail.get("checklist") or []
    comments = detail.get("comments") or {}

    effective = [e for e in errors if e.get("effective")]
    suggestions = [e for e in errors if not e.get("effective")]

    def value(value, fallback="—"):
        return fallback if value is None else str(value)

    def score_block(label, score):
        total = score.get("total")
        language = score.get("language")
        content = score.get("content")
        structure = score.get("structure")
        return (
            '<div class="score-card"><div class="score-label">' + html.escape(label) + '</div>'
            '<div class="score-total">' + value(total) + ' <span>/ 10</span></div>'
            '<div class="dim">语言 ' + value(language) + ' / 5</div>'
            '<div class="dim">内容 ' + value(content) + ' / 3</div>'
            '<div class="dim">结构 ' + value(structure) + ' / 2</div></div>'
        )

    def error_rows(items, show_effective):
        if not items:
            return '<tr><td colspan="4" class="muted">无</td></tr>'
        rows = []
        for e in items:
            certainty = e.get("certainty") or ""
            badge = "确定" if certainty == "确定" else "建议核对"
            rows.append(
                '<tr><td><del>' + html.escape(str(e.get("original", ""))) + '</del> → <ins>' +
                html.escape(str(e.get("corrected", ""))) + '</ins></td><td>' +
                html.escape(str(e.get("type", ""))) + '<br><span class="muted">' +
                html.escape(str(e.get("error_code") or e.get("pattern") or "")) + '</span></td><td>' +
                html.escape(str(e.get("rule", ""))) + '</td><td><span class="pill ' +
                ("" if badge == "确定" else "warn") + '">' + badge + '</span></td></tr>'
            )
        return "".join(rows)

    checklist_rows = []
    for item in checklist:
        valid = item.get("valid")
        badge = '<span class="pill">已确认</span>' if valid else '<span class="pill warn">待人工确认</span>'
        checklist_rows.append(
            '<tr><td>' + html.escape(str(item.get("key"))) + '</td><td><code>' +
            html.escape(json.dumps(item.get("value"), ensure_ascii=False)) + '</code></td><td>' + badge + '</td></tr>'
        )

    notice = "AI 草稿 · 必须经教师终审后才能作为成绩使用"
    if submission.get("status") == "needs_manual":
        notice = "流程需要人工处理 · 当前不是完整分数"
    elif submission.get("status") == "finalized":
        notice = "教师已完成终审 · 可导出学生版反馈"

    run_error = run.get("error")
    error_box = '<div class="error-box">' + html.escape(str(run_error)) + '</div>' if run_error else ""

    return """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>德语作文批改报告 · __STUDENT__</title>
<style>
:root{--bg:#f6f5f1;--panel:#fffdf8;--ink:#1f2328;--muted:#6b7280;--line:#e4e0d6;--accent:#145c4c;--warn:#9a5b13;--warn-bg:#fbf0dd}
@media (prefers-color-scheme:dark){:root{--bg:#151715;--panel:#1d211e;--ink:#e8e6df;--muted:#9aa39b;--line:#313832;--accent:#7fc7b2;--warn:#e0b06a;--warn-bg:#3a2e1d}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}
main{max-width:920px;margin:0 auto;padding:28px 18px 60px}h1{font-size:28px;letter-spacing:-.02em;margin:0 0 6px}h2{font-size:17px;margin:0 0 10px}h3{font-size:14px;margin:16px 0 6px}
.meta{color:var(--muted);font-size:13px;display:flex;gap:8px;flex-wrap:wrap}.pill{display:inline-block;border-radius:99px;padding:2px 9px;font-size:12px;background:#e5f1ec;color:var(--accent);white-space:nowrap}
.pill.warn{background:var(--warn-bg);color:var(--warn)}.notice{margin:18px 0;padding:10px 12px;border-left:3px solid var(--accent);background:var(--panel);color:var(--muted);font-size:13px}
.error-box{margin:12px 0;padding:10px 12px;border:1px solid #a33a32;color:#a33a32;border-radius:8px;white-space:pre-wrap}
.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:18px 0}.score-card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px}
.score-label{color:var(--muted);font-size:12px}.score-total{font-size:30px;font-weight:700;letter-spacing:-.03em;margin:4px 0}.score-total span{font-size:13px;font-weight:400;color:var(--muted)}
.dim{font-size:12px;color:var(--muted)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px;margin:14px 0}h2 .muted{font-weight:400;font-size:12px}
table{width:100%;border-collapse:collapse}th,td{border-bottom:1px solid var(--line);padding:8px 6px;text-align:left;vertical-align:top;font-size:13px}th{color:var(--muted);font-weight:600;font-size:12px}
del{color:#a33a32;text-decoration-thickness:1px}ins{color:var(--accent);text-decoration:none;font-weight:650}code{font-size:12px;word-break:break-word}
.muted{color:var(--muted)}.footer{margin-top:28px;color:var(--muted);font-size:12px;border-top:1px solid var(--line);padding-top:12px}
@media(max-width:680px){.grid{grid-template-columns:1fr}h1{font-size:23px}main{padding:18px 12px 40px}}
</style>
</head>
<body><main>
<h1>__TASK__ · __STUDENT__</h1>
<div class="meta"><span>__LEVEL__</span><span>·</span><span>状态：__STATUS__</span><span>·</span><span>__CREATED__</span></div>
<div class="notice">__NOTICE__</div>
__ERROR_BOX__
<div class="grid">
__AI_BLOCK__
__REVIEWED_BLOCK__
__FINAL_BLOCK__
</div>
<section class="card"><h2>错误清单 <span class="muted">计分 __EFFECTIVE_COUNT__ 条</span></h2>
<h3>确定错误</h3><table><thead><tr><th>原文 → 修改</th><th>类型</th><th>依据</th><th>状态</th></tr></thead><tbody>__ERROR_ROWS__</tbody></table>
<h3>建议核对 / 未计分</h3><table><thead><tr><th>原文 → 修改</th><th>类型</th><th>依据</th><th>状态</th></tr></thead><tbody>__SUGGESTION_ROWS__</tbody></table></section>
<section class="card"><h2>要素核对</h2><table><thead><tr><th>字段</th><th>值</th><th>状态</th></tr></thead><tbody>__CHECKLIST_ROWS__</tbody></table></section>
<section class="card"><h2>教师评语</h2><p>__CONTENT_COMMENT__</p><p>__STRUCTURE_COMMENT__</p><p class="muted">__LANGUAGE_SUMMARY__</p><p><strong>__ENCOURAGEMENT__</strong></p></section>
<div class="footer">由 Deutsch Grader 生成 · API __SOURCE__ · 页面只包含批改结果，不包含学生真实姓名</div>
</main></body></html>""".replace("__STUDENT__", html.escape(str(submission.get("student") or "匿名学生"))).replace("__TASK__", html.escape(str(submission.get("task") or "德语作文"))).replace("__LEVEL__", html.escape(str(submission.get("level") or ""))).replace("__STATUS__", html.escape(str(submission.get("status") or ""))).replace("__CREATED__", html.escape(str(submission.get("created_at") or ""))).replace("__NOTICE__", html.escape(notice)).replace("__ERROR_BOX__", error_box).replace("__AI_BLOCK__", score_block("AI 草稿", ai)).replace("__REVIEWED_BLOCK__", score_block("教师复核", reviewed)).replace("__FINAL_BLOCK__", score_block("最终成绩", final)).replace("__EFFECTIVE_COUNT__", str(len(effective))).replace("__ERROR_ROWS__", error_rows(effective, True)).replace("__SUGGESTION_ROWS__", error_rows(suggestions, False)).replace("__CHECKLIST_ROWS__", "".join(checklist_rows) or '<tr><td colspan="3" class="muted">无</td></tr>').replace("__CONTENT_COMMENT__", html.escape(str(comments.get("content_comment") or "—"))).replace("__STRUCTURE_COMMENT__", html.escape(str(comments.get("structure_comment") or "—"))).replace("__LANGUAGE_SUMMARY__", html.escape(str(comments.get("language_summary") or "—"))).replace("__ENCOURAGEMENT__", html.escape(str(comments.get("encouragement_de") or ""))).replace("__SOURCE__", html.escape(source_url))


def write_report(detail: dict, path: str, source_url: str) -> str:
    target = os.path.abspath(path)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(render_report(detail, source_url))
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Submit a German essay to Deutsch Grader and summarize the draft result.")
    parser.add_argument("--url", default=os.environ.get("DG_API_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--email", default=os.environ.get("DG_API_EMAIL", ""))
    parser.add_argument("--password", default=os.environ.get("DG_API_PASSWORD", ""))
    parser.add_argument("--student", default=None, help="student id or pseudonym")
    parser.add_argument("--title", default="Meine beste Reise")
    parser.add_argument("--text", default=None)
    parser.add_argument("--file", default=None)
    parser.add_argument("--submission-id", default=None, help="fetch an existing submission instead of creating one")
    parser.add_argument("--html", default=None, help="write a standalone HTML report to this path")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--no-wait", action="store_true")
    args = parser.parse_args()

    if not args.email:
        raise SystemExit("set DG_API_EMAIL or pass --email")
    password = args.password or getpass.getpass("Deutsch Grader password: ")
    client = Client(args.url)
    try:
        client.login(args.email, password)
        if args.submission_id:
            detail = client.detail(args.submission_id)
            print(json.dumps(summarize(detail), ensure_ascii=False, indent=2))
            if args.html:
                print("HTML=" + write_report(detail, args.html, args.url))
            if detail["submission"]["status"] == "needs_manual":
                sys.exit(3)
            return
        if bool(args.text) == bool(args.file):
            raise SystemExit("pass exactly one of --text, --file, or --submission-id")
        text = args.text if args.text else open(args.file, encoding="utf-8").read()
        student = pick_student(client.students(), args.student)
        created = client.submit(student["id"], args.title, text)
        if args.no_wait:
            print(json.dumps(created, ensure_ascii=False, indent=2))
            return
        submission_id = created["submission_id"]
        deadline = time.time() + args.timeout
        detail = {}
        while time.time() < deadline:
            detail = client.detail(submission_id)
            status = detail["submission"]["status"]
            if status in {"ready_for_review", "finalized", "needs_manual"}:
                break
            time.sleep(args.interval)
        else:
            print(json.dumps({"status": "timeout", "submission_id": submission_id}, ensure_ascii=False, indent=2))
            sys.exit(4)
        print(json.dumps(summarize(detail), ensure_ascii=False, indent=2))
        if args.html:
            print("HTML=" + write_report(detail, args.html, args.url))
        if detail["submission"]["status"] == "needs_manual":
            sys.exit(3)
    except ApiError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()
