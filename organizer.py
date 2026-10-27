#!/usr/bin/env python3
"""작업 파일 자동 정리 도구: 파일명 규칙으로 `날짜_작업_대상` 폴더에 정리한다."""
import argparse
import datetime as dt
import fnmatch
import glob
import json
import re
import secrets
import shutil
import sys
import uuid
import webbrowser
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

DATE_RE = re.compile(r"(?<!\d)(\d{4})[-._]?(\d{2})[-._]?(\d{2})(?!\d)")
DATE_SOURCES = ("filename", "mtime")
PLACEHOLDERS = {"date", "task", "target"}
DEFAULTS = {
    "date_source": "filename",
    "date_format": "%Y-%m-%d",
    "folder_template": "{date}_{task}_{target}",
    "tasks": [],
    "targets": [],
    "ignore": [],
}


# ---------- 규칙 ----------

def load_rules(path):
    path = Path(path)
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    rules = {**DEFAULTS, **raw}
    for key in ("tasks", "targets", "ignore"):
        rules[key] = rules[key] or []
    rules["_path"] = path.resolve()
    base = rules["_path"].parent
    for key in ("source_dir", "output_dir"):
        if rules.get(key):
            rules[key] = (base / Path(str(rules[key])).expanduser()).resolve()
    log = rules.get("log_file") or "organizer_log.jsonl"
    rules["log_file"] = (base / Path(str(log)).expanduser()).resolve()
    return rules


def validate_rules(rules):
    errors = []
    for key in ("source_dir", "output_dir"):
        if not rules.get(key):
            errors.append(f"{key}가 없습니다")
    src = rules.get("source_dir")
    if src and not Path(src).is_dir():
        errors.append(f"source_dir 폴더가 없습니다: {src}")
    if rules["date_source"] not in DATE_SOURCES:
        errors.append(f"date_source는 {'/'.join(DATE_SOURCES)} 중 하나여야 합니다")
    try:
        dt.date.today().strftime(rules["date_format"])
    except ValueError as e:
        errors.append(f"date_format 오류: {e}")
    used = set(re.findall(r"{(\w*)}", rules["folder_template"]))
    if used - PLACEHOLDERS:
        errors.append(f"folder_template에 알 수 없는 항목: {sorted(used - PLACEHOLDERS)}")
    for key in ("tasks", "targets"):
        seen = set()
        for i, entry in enumerate(rules[key], 1):
            name = entry.get("name") if isinstance(entry, dict) else None
            kws = entry.get("keywords") if isinstance(entry, dict) else None
            if not name:
                errors.append(f"{key}[{i}]: name이 없습니다")
            elif name in seen:
                errors.append(f"{key}: 이름 중복 '{name}'")
            seen.add(name)
            if not kws or not all(isinstance(k, str) and k for k in kws):
                errors.append(f"{key}[{i}] '{name}': keywords가 비었거나 잘못되었습니다")
    return errors


def save_rules(rules):
    # 경로는 원본 표기를 유지하기 위해 파일을 다시 읽어 필요한 키만 갱신한다
    with open(rules["_path"], encoding="utf-8") as f:
        on_disk = yaml.safe_load(f) or {}
    for key in ("tasks", "targets", "ignore"):
        on_disk[key] = rules[key]
    with open(rules["_path"], "w", encoding="utf-8") as f:
        yaml.safe_dump(on_disk, f, allow_unicode=True, sort_keys=False)


# ---------- 추출 ----------

def extract_date(path, rules):
    def from_name():
        for m in DATE_RE.finditer(path.name):
            try:
                return dt.date(*map(int, m.groups()))
            except ValueError:
                continue
        return None

    def from_mtime():
        try:
            return dt.date.fromtimestamp(path.stat().st_mtime)
        except OSError:
            return None

    order = (from_name, from_mtime) if rules["date_source"] == "filename" else (from_mtime, from_name)
    for fn in order:
        d = fn()
        if d:
            return d
    return dt.date.today()


def match_name(filename, entries):
    """위에서 아래로 평가해 첫 매치의 이름을 돌려준다."""
    lowered = filename.lower()
    for entry in entries:
        if any(kw.lower() in lowered for kw in entry["keywords"]):
            return entry["name"]
    return None


def is_ignored(name, rules):
    return any(fnmatch.fnmatch(name, pat) for pat in rules["ignore"])


def scan(rules):
    src, out = rules["source_dir"], rules["output_dir"]
    skip = {rules["_path"], rules["log_file"]}
    for p in sorted(src.iterdir()):
        if not p.is_file() or p.resolve() in skip or is_ignored(p.name, rules):
            continue
        if out == p.resolve() or out in p.resolve().parents:
            continue
        yield p


# ---------- 계획 ----------

def make_folder(rules, path, task, target):
    date = extract_date(path, rules).strftime(rules["date_format"])
    return rules["folder_template"].format(date=date, task=task, target=target)


def build_plan(rules):
    """(폴더명 -> [(파일, 작업, 대상)], 미분류 [(파일, 작업, 대상)])"""
    groups, unclassified = defaultdict(list), []
    for p in scan(rules):
        task = match_name(p.name, rules["tasks"])
        target = match_name(p.name, rules["targets"])
        if task and target:
            groups[make_folder(rules, p, task, target)].append((p, task, target))
        else:
            unclassified.append((p, task, target))
    return groups, unclassified


def unique_dest(dst, taken=()):
    cand, i = dst, 0
    while cand.exists() or cand in taken:
        i += 1
        cand = dst.with_name(f"{dst.stem}_{i}{dst.suffix}")
    return cand


def print_plan(rules, groups, unclassified):
    taken = set()
    for folder, items in sorted(groups.items()):
        print(f"[{folder}] ({len(items)}개)")
        for p, _, _ in items:
            dst = unique_dest(rules["output_dir"] / folder / p.name, taken)
            taken.add(dst)
            note = "" if dst.name == p.name else f" -> {dst.name}"
            print(f"    {p.name}{note}")
    if unclassified:
        print(f"\n미분류 ({len(unclassified)}개)")
        for p, task, target in unclassified:
            missing = [n for n, v in (("작업", task), ("대상", target)) if not v]
            print(f"    {p.name}  (없음: {', '.join(missing)})")
    total = sum(len(v) for v in groups.values())
    print(f"\n이동 예정 {total}개 / 폴더 {len(groups)}개 / 미분류 {len(unclassified)}개")


# ---------- 로그 ----------

def log_event(rules, **fields):
    entry = {"time": dt.datetime.now().isoformat(timespec="seconds"), **fields}
    with open(rules["log_file"], "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_log(rules):
    if not rules["log_file"].exists():
        return []
    with open(rules["log_file"], encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# ---------- 실행 ----------

def move_one(rules, run_id, src, folder, task, target):
    dst_dir = rules["output_dir"] / folder
    dir_created = not dst_dir.exists()
    base = dict(run_id=run_id, src=str(src), task=task, target=target, action="move")
    try:
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = unique_dest(dst_dir / src.name)
        shutil.move(str(src), str(dst))
    except OSError as e:
        log_event(rules, **base, dst=str(dst_dir / src.name), result=f"error: {e}")
        print(f"  실패: {src.name} ({e})")
        return False
    log_event(rules, **base, dst=str(dst), dir_created=dir_created, result="ok")
    print(f"  이동: {src.name} -> {dst.relative_to(rules['output_dir'])}")
    return True


def handle_unclassified(rules, run_id, unclassified):
    """대화형: 규칙 추가 / 건너뛰기 / 분류 불필요."""
    for p, task, target in unclassified:
        missing = [n for n, v in (("작업", task), ("대상", target)) if not v]
        print(f"\n미분류: {p.name} (없음: {', '.join(missing)})")
        choice = input("  [r] 규칙 추가  [s] 이번만 건너뛰기  [i] 분류 불필요 > ").strip().lower()
        if choice == "r":
            for label, key, found in (("작업", "tasks", task), ("대상", "targets", target)):
                if found:
                    continue
                kws = input(f"  {label} 키워드 (쉼표 구분, 빈 입력=건너뜀) > ")
                kws = [k.strip() for k in kws.split(",") if k.strip()]
                if not kws:
                    continue
                name = input(f"  {label}명 > ").strip()
                if name:
                    rules[key].append({"name": name, "keywords": kws})
            save_rules(rules)
            task = match_name(p.name, rules["tasks"])
            target = match_name(p.name, rules["targets"])
            if task and target:
                move_one(rules, run_id, p, make_folder(rules, p, task, target), task, target)
            else:
                print("  아직 분류되지 않아 건너뜁니다.")
        elif choice == "i":
            rules["ignore"].append(glob.escape(p.name))
            save_rules(rules)
            print("  ignore 목록에 추가했습니다.")
        else:
            print("  건너뜁니다.")


def cmd_run(rules, args):
    groups, unclassified = build_plan(rules)
    print_plan(rules, groups, unclassified)
    if not args.apply:
        print("\n(Dry Run) 실제 이동하려면 --apply 를 붙이세요.")
        return 0
    if not groups and not unclassified:
        return 0
    if not args.yes and input("\n진행할까요? [y/N] ").strip().lower() != "y":
        print("취소했습니다.")
        return 0
    run_id = uuid.uuid4().hex[:8]
    print(f"\n실행 (run_id={run_id})")
    failed = 0
    for folder, items in sorted(groups.items()):
        for p, task, target in items:
            failed += not move_one(rules, run_id, p, folder, task, target)
    if unclassified and not args.no_interactive and sys.stdin.isatty():
        handle_unclassified(rules, run_id, unclassified)
    return 1 if failed else 0


def cmd_undo(rules, args):
    entries = read_log(rules)
    undone = {(e["run_id"], e["src"]) for e in entries if e["action"] == "undo"}
    moves = [e for e in entries if e["action"] == "move" and e["result"] == "ok"
             and (e["run_id"], e["dst"]) not in undone]
    if not moves:
        print("되돌릴 이동 내역이 없습니다.")
        return 0
    run_id = args.run_id or moves[-1]["run_id"]
    targets = [e for e in moves if e["run_id"] == run_id][::-1]
    if not targets:
        print(f"run_id={run_id} 에 되돌릴 내역이 없습니다.")
        return 1
    for e in targets:
        print(f"    {e['dst']} -> {e['src']}")
    if not args.yes and input(f"\nrun_id={run_id} {len(targets)}건을 되돌릴까요? [y/N] ").strip().lower() != "y":
        print("취소했습니다.")
        return 0
    for e in targets:
        src, dst = Path(e["src"]), Path(e["dst"])
        base = dict(run_id=run_id, src=e["dst"], dst=e["src"], action="undo")
        if not dst.exists() or src.exists():
            reason = "현재 파일이 없음" if not dst.exists() else "원래 위치에 같은 이름이 있음"
            print(f"  건너뜀: {dst.name} ({reason})")
            log_event(rules, **base, result=f"skipped: {reason}")
            continue
        src.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(dst), str(src))
        if e.get("dir_created") and not any(dst.parent.iterdir()):
            dst.parent.rmdir()
        log_event(rules, **base, result="ok")
        print(f"  복원: {src.name}")
    return 0


def draft_from(rules, body, strict):
    """편집 화면에서 온 초안을 규칙 사본에 적용한다. strict가 아니면 미완성 항목은 버린다."""
    draft = dict(rules)
    for key in ("tasks", "targets"):
        entries = [{"name": str(e.get("name", "")).strip(),
                    "keywords": [str(k).strip() for k in e.get("keywords", []) if str(k).strip()]}
                   for e in body.get(key, [])]
        draft[key] = entries if strict else [e for e in entries if e["name"] and e["keywords"]]
    draft["ignore"] = [str(p).strip() for p in body.get("ignore", []) if str(p).strip()]
    return draft


def preview_rows(draft, extra_names):
    groups, unclassified = build_plan(draft)
    rows = [{"name": p.name, "task": t, "target": g, "folder": folder}
            for folder, items in groups.items() for p, t, g in items]
    rows += [{"name": p.name, "task": t, "target": g, "folder": None} for p, t, g in unclassified]
    for name in extra_names:
        t, g = match_name(name, draft["tasks"]), match_name(name, draft["targets"])
        folder = make_folder(draft, Path(name), t, g) if t and g else None
        rows.append({"name": name, "task": t, "target": g, "folder": folder, "extra": True})
    rows.sort(key=lambda r: (r["folder"] is not None, r["folder"] or "", r["name"]))
    return rows


def cmd_edit(rules, args):
    token = secrets.token_urlsafe(16)
    html = (Path(__file__).parent / "editor.html").read_text(encoding="utf-8")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, body, ctype="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def host_ok(self):  # DNS rebinding 방지
            return self.headers.get("Host", "").rsplit(":", 1)[0] in ("127.0.0.1", "localhost")

        def authed(self):  # 다른 사이트에서 오는 요청 차단
            return self.host_ok() and self.headers.get("X-Token") == token

        def body(self):
            return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")

        def do_GET(self):
            if self.path == "/" and self.host_ok():
                self.send(200, html.replace("__TOKEN__", token).encode("utf-8"), "text/html; charset=utf-8")
            elif self.path == "/api/rules" and self.authed():
                self.send(200, {"tasks": rules["tasks"], "targets": rules["targets"], "ignore": rules["ignore"],
                                "source_dir": str(rules["source_dir"]), "output_dir": str(rules["output_dir"]),
                                "file": str(rules["_path"])})
            else:
                self.send(404, {"error": "not found"})

        def do_POST(self):  # 미리보기
            if self.path != "/api/preview" or not self.authed():
                return self.send(404, {"error": "not found"})
            try:
                body = self.body()
                self.send(200, {"rows": preview_rows(draft_from(rules, body, False), body.get("extra", []))})
            except (ValueError, OSError, KeyError) as e:
                self.send(400, {"errors": [str(e)]})

        def do_PUT(self):  # 저장
            if self.path != "/api/rules" or not self.authed():
                return self.send(404, {"error": "not found"})
            try:
                draft = draft_from(rules, self.body(), True)
            except ValueError as e:
                return self.send(400, {"errors": [str(e)]})
            errors = validate_rules(draft)
            if errors:
                return self.send(400, {"errors": errors})
            for key in ("tasks", "targets", "ignore"):
                rules[key] = draft[key]
            save_rules(rules)
            self.send(200, {"ok": True, "tasks": rules["tasks"], "targets": rules["targets"], "ignore": rules["ignore"]})

    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError as e:
        print(f"포트 {args.port}를 열 수 없습니다: {e} (--port 로 다른 포트를 지정하세요)")
        return 2
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"규칙 편집기: {url}  (종료: Ctrl+C)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.")
    return 0


def cmd_check(rules, args):
    errors = validate_rules(rules)
    for msg in errors:
        print(f"오류: {msg}")
    if not errors:
        print("규칙 파일에 문제가 없습니다.")
    return 1 if errors else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-c", "--config", default="rules.yaml", help="규칙 파일 (기본: rules.yaml)")
    sub = ap.add_subparsers(dest="cmd")
    run = sub.add_parser("run", help="정리 (기본은 Dry Run)")
    run.add_argument("--apply", action="store_true", help="실제로 폴더 생성 및 이동")
    run.add_argument("-y", "--yes", action="store_true", help="확인 질문 생략")
    run.add_argument("--no-interactive", action="store_true", help="미분류 대화형 처리 생략")
    undo = sub.add_parser("undo", help="이동 로그 기반 되돌리기 (기본: 마지막 실행)")
    undo.add_argument("--run-id")
    undo.add_argument("-y", "--yes", action="store_true")
    sub.add_parser("check", help="규칙 파일 검증")
    edit = sub.add_parser("edit", help="브라우저에서 규칙 편집 (로컬 서버)")
    edit.add_argument("--port", type=int, default=8765)
    edit.add_argument("--no-browser", action="store_true", help="브라우저를 자동으로 열지 않음")
    args = ap.parse_args(argv)
    if not args.cmd:
        args = ap.parse_args(["-c", args.config, "run"])

    try:
        rules = load_rules(args.config)
    except (OSError, yaml.YAMLError) as e:
        print(f"규칙 파일을 읽을 수 없습니다: {e}")
        return 2
    if args.cmd != "check":
        errors = validate_rules(rules)
        if errors:
            print("규칙 파일 오류 (`check`로 확인):\n  " + "\n  ".join(errors))
            return 2
    return {"run": cmd_run, "undo": cmd_undo, "check": cmd_check, "edit": cmd_edit}[args.cmd](rules, args)


if __name__ == "__main__":
    sys.exit(main())
