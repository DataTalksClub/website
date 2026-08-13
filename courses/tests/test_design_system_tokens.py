from pathlib import Path

from django.test import SimpleTestCase

COURSES_DIR = Path(__file__).resolve().parents[1]


class DesignSystemTokenTests(SimpleTestCase):
    def test_deadline_script_emits_only_semantic_status_classes(self):
        script = (COURSES_DIR / "static" / "time_left.js").read_text()

        for class_name in (
            "time-left--normal",
            "time-left--warning",
            "time-left--overdue",
        ):
            self.assertIn(class_name, script)

        self.assertNotRegex(script, r"text-slate-")
        self.assertNotRegex(script, r"text-\[#")
        self.assertNotRegex(script, r"dark:text-")
        self.assertNotRegex(script, r"#[0-9a-fA-F]{3,8}")

    def test_course_controls_use_theme_safe_tokens(self):
        stylesheet = (COURSES_DIR / "static" / "courses.css").read_text()

        self.assertRegex(stylesheet, r"--border-control:\s*#6e7781")
        self.assertRegex(stylesheet, r"body\.dark-mode[^{]*\{[\s\S]*--border-control:\s*#6e7681")
        self.assertIn("--focus-ring: #315f8f", stylesheet)
        self.assertIn("--focus-ring: #8bb7df", stylesheet)
        self.assertIn(".time-left--warning", stylesheet)
        self.assertIn("color: var(--warning-text)", stylesheet)
        self.assertIn("var(--control-readonly-bg)", stylesheet)
        self.assertIn("var(--control-readonly-text)", stylesheet)
        self.assertIn("var(--impersonation-bg)", stylesheet)
        self.assertIn("var(--impersonation-text)", stylesheet)
