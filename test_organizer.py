import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

import organizer

RULES = """\
source_dir: ./source
output_dir: ./organized
tasks:
  - name: 정책삭제
    keywords: ["정책삭제"]
  - name: 정책신청
    keywords: ["정책신청"]
targets:
  - name: A방화벽
    keywords: ["A방화벽", "FW-A"]
ignore: ["~$*"]
"""


class OrganizerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "source").mkdir()
        (self.root / "rules.yaml").write_text(RULES, encoding="utf-8")
        self.cfg = str(self.root / "rules.yaml")

    def tearDown(self):
        self.tmp.cleanup()

    def touch(self, name):
        p = self.root / "source" / name
        p.write_text(name, encoding="utf-8")
        return p

    def run_cli(self, *args):
        return organizer.main(["-c", self.cfg, *args])

    def test_date_formats(self):
        rules = organizer.load_rules(self.cfg)
        for name in ("2026-10-27_a.txt", "a_20261027.txt", "a.2026.10.27.txt"):
            p = self.touch(name)
            self.assertEqual(organizer.extract_date(p, rules), dt.date(2026, 10, 27), name)

    def test_invalid_date_falls_back_to_mtime(self):
        rules = organizer.load_rules(self.cfg)
        p = self.touch("20261399_a.txt")
        self.assertEqual(organizer.extract_date(p, rules), dt.date.fromtimestamp(p.stat().st_mtime))

    def test_first_match_wins(self):
        entries = [{"name": "X", "keywords": ["a"]}, {"name": "Y", "keywords": ["a"]}]
        self.assertEqual(organizer.match_name("A.txt", entries), "X")

    def test_dry_run_moves_nothing(self):
        self.touch("2026-10-27_정책삭제_A방화벽.xlsx")
        self.assertEqual(self.run_cli("run"), 0)
        self.assertFalse((self.root / "organized").exists())

    def test_apply_collision_and_unclassified(self):
        self.touch("2026-10-27_정책삭제_A방화벽.xlsx")
        self.touch("2026-10-27_정책삭제_FW-A.xlsx")
        self.touch("메모.txt")
        self.touch("~$임시.docx")
        self.run_cli("run", "--apply", "-y", "--no-interactive")
        folder = self.root / "organized" / "2026-10-27_정책삭제_A방화벽"
        self.assertEqual(len(list(folder.iterdir())), 2)
        self.assertTrue((self.root / "source" / "메모.txt").exists())
        self.assertTrue((self.root / "source" / "~$임시.docx").exists())

    def test_existing_file_not_overwritten(self):
        folder = self.root / "organized" / "2026-10-27_정책삭제_A방화벽"
        folder.mkdir(parents=True)
        (folder / "2026-10-27_정책삭제_A방화벽.xlsx").write_text("old")
        self.touch("2026-10-27_정책삭제_A방화벽.xlsx")
        self.run_cli("run", "--apply", "-y", "--no-interactive")
        self.assertEqual((folder / "2026-10-27_정책삭제_A방화벽.xlsx").read_text(), "old")
        self.assertTrue((folder / "2026-10-27_정책삭제_A방화벽_1.xlsx").exists())

    def test_undo_restores_and_removes_created_folder(self):
        self.touch("2026-10-27_정책삭제_A방화벽.xlsx")
        self.run_cli("run", "--apply", "-y", "--no-interactive")
        self.run_cli("undo", "-y")
        self.assertTrue((self.root / "source" / "2026-10-27_정책삭제_A방화벽.xlsx").exists())
        self.assertFalse((self.root / "organized" / "2026-10-27_정책삭제_A방화벽").exists())
        self.assertEqual(self.run_cli("undo", "-y"), 0)  # 이미 되돌린 내역은 다시 되돌리지 않음

    def test_log_is_jsonl(self):
        self.touch("2026-10-27_정책삭제_A방화벽.xlsx")
        self.run_cli("run", "--apply", "-y", "--no-interactive")
        lines = (self.root / "organizer_log.jsonl").read_text(encoding="utf-8").splitlines()
        entry = json.loads(lines[0])
        self.assertEqual((entry["task"], entry["target"], entry["result"]), ("정책삭제", "A방화벽", "ok"))

    def test_output_inside_source_is_not_scanned(self):
        (self.root / "rules.yaml").write_text(
            RULES.replace("output_dir: ./organized", "output_dir: ./source/out"), encoding="utf-8")
        out = self.root / "source" / "out"
        out.mkdir()
        (out / "2026-10-27_정책삭제_A방화벽.xlsx").write_text("x")
        rules = organizer.load_rules(self.cfg)
        self.assertEqual(list(organizer.scan(rules)), [])

    def test_validation(self):
        (self.root / "rules.yaml").write_text(
            RULES.replace("{date}", "") + "folder_template: '{foo}'\ndate_source: x\n", encoding="utf-8")
        errors = organizer.validate_rules(organizer.load_rules(self.cfg))
        self.assertEqual(len(errors), 2)

    def test_preview_rows_uses_draft_and_extras(self):
        self.touch("2026-10-27_정책삭제_신규장비.xlsx")
        rules = organizer.load_rules(self.cfg)
        body = {"tasks": rules["tasks"],
                "targets": rules["targets"] + [{"name": "신규", "keywords": ["신규장비"]}, {"name": "", "keywords": []}],
                "ignore": []}
        rows = organizer.preview_rows(organizer.draft_from(rules, body, False), ["20261101_정책신청_FW-A.txt", "메모.txt"])
        by_name = {r["name"]: r for r in rows}
        self.assertEqual(by_name["2026-10-27_정책삭제_신규장비.xlsx"]["folder"], "2026-10-27_정책삭제_신규")
        self.assertEqual(by_name["20261101_정책신청_FW-A.txt"]["folder"], "2026-11-01_정책신청_A방화벽")
        self.assertIsNone(by_name["메모.txt"]["folder"])

    def test_draft_strict_keeps_incomplete_for_validation(self):
        rules = organizer.load_rules(self.cfg)
        draft = organizer.draft_from(rules, {"tasks": [{"name": "", "keywords": []}]}, True)
        self.assertTrue(organizer.validate_rules(draft))


if __name__ == "__main__":
    unittest.main()
