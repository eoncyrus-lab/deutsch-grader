from __future__ import annotations

import argparse
import getpass
import http.cookiejar
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Submit a German essay to Deutsch Grader and summarize the draft result.")
    parser.add_argument("--url", default=os.environ.get("DG_API_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--email", default=os.environ.get("DG_API_EMAIL", ""))
    parser.add_argument("--password", default=os.environ.get("DG_API_PASSWORD", ""))
    parser.add_argument("--student", default=None, help="student id or pseudonym")
    parser.add_argument("--title", default="Meine beste Reise")
    parser.add_argument("--text", default=None)
    parser.add_argument("--file", default=None)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--no-wait", action="store_true")
    args = parser.parse_args()

    if not args.email:
        raise SystemExit("set DG_API_EMAIL or pass --email")
    password = args.password or getpass.getpass("Deutsch Grader password: ")
    if bool(args.text) == bool(args.file):
        raise SystemExit("pass exactly one of --text or --file")
    text = args.text if args.text else open(args.file, encoding="utf-8").read()

    client = Client(args.url)
    try:
        client.login(args.email, password)
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
        if detail["submission"]["status"] == "needs_manual":
            sys.exit(3)
    except ApiError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()
