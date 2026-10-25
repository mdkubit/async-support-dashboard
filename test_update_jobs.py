from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import update_jobs as updater


class UpdaterTests(unittest.TestCase):
    now = datetime(2026, 10, 25, tzinfo=timezone.utc)

    def row(self, description, **changes):
        row = dict(title="Customer Support Specialist", company="Example",
                   description=description, date="2026-10-24T12:00:00Z",
                   url="https://example.org/jobs/1", source="Remotive")
        return dict(row, **changes)

    def screen(self, description, **changes):
        return updater.screen(self.row(description, **changes), self.now, 45)[0]

    def test_accepts_explicit_nonvoice(self):
        for text in ["Email and chat support. No phone calls required.",
                     "Non-voice support with Zendesk tickets.",
                     "Text-only customer support via email.",
                     "Chat-only support.",
                     "Email support. Phone calls are not required."]:
            with self.subTest(text=text):
                self.assertIsNotNone(self.screen(text))

    def test_accepts_occasional_phone_and_async_roles(self):
        for text in ["Answer email and phone calls using Zendesk.",
                     "Chat support and occasional outbound calling.",
                     "Email and ticket support in an asynchronous team.",
                     "Email and no phone support, but occasional customer calls.",
                     "Non-voice email support with weekly Zoom meetings."]:
            with self.subTest(text=text):
                job = self.screen(text)
                self.assertIsNotNone(job)
                self.assertNotIn("explicit-non-voice", job["tags"])
        self.assertIsNotNone(self.screen("Phone calls are rare. EMAIL support."))
        self.assertIsNotNone(self.screen("Occasional phone support; CHAT is primary."))

    def test_all_positive_keywords_case_insensitive(self):
        for keyword in ["chat", "email", "ticket", "tickets", "zendesk", "intercom",
                        "slack", "discord", "asynchronous", "async", "written"]:
            with self.subTest(keyword=keyword):
                self.assertIsNotNone(self.screen(f"Work using {keyword.upper()}."))
        self.assertIsNone(self.screen("Work using a chatbot and ticketmaster."))

    def test_rejects_heavy_phone_and_call_center_titles(self):
        for phrase in ["heavy phone volume", "cold calling", "inbound call queue",
                       "telemarketing", "high-volume calls"]:
            with self.subTest(phrase=phrase):
                self.assertIsNone(self.screen(f"EMAIL support includes {phrase.upper()}."))
        self.assertIsNone(self.screen("Use email.", title="Call Center Agent"))
        self.assertIsNone(self.screen("Use chat.", title="Support Call-Centre Agent"))
        self.assertIsNotNone(self.screen("Email support. No cold calling."))
        self.assertIsNone(self.screen("Email. No cold calling, but heavy phone volume."))

    def test_rejects_non_support_and_bad_metadata(self):
        text = "Non-voice email support."
        for changes in [dict(title="Software Engineer"), dict(company=""),
                        dict(date="invalid"), dict(date="2020-01-01"),
                        dict(url="javascript:alert(1)")]:
            with self.subTest(changes=changes):
                self.assertIsNone(self.screen(text, **changes))

    def test_html_boundaries_and_normalized_dates(self):
        self.assertEqual(updater.plain("<p>chat</p><p>support &amp; tickets</p>"),
                         "chat support & tickets")
        date = updater.parse_date("Sat, 24 Oct 2026 08:00:00 -0400")
        self.assertEqual(date.isoformat(), "2026-10-24T12:00:00+00:00")

    def test_rss_adapter(self):
        feed = b'''<rss xmlns:content="http://purl.org/rss/1.0/modules/content/">
          <channel><item><title>Example: Email Support</title>
          <link>https://example.org/jobs/1</link>
          <pubDate>Sat, 24 Oct 2026 12:00:00 GMT</pubDate>
          <content:encoded><![CDATA[<p>No phone calls. Email tickets.</p>]]>
          </content:encoded></item></channel></rss>'''
        with patch.object(updater, "fetch", return_value=feed):
            rows = updater.read_wwr()
        self.assertEqual(rows[0]["company"], "Example")
        self.assertIsNotNone(updater.screen(rows[0], self.now, 45)[0])

    def test_api_adapter_and_schema_failure(self):
        payload = {"jobs": [{"title": "Email Support", "company_name": "Example",
                             "description": "Non-voice email support",
                             "publication_date": "2026-10-24T12:00:00",
                             "url": "https://example.org/jobs/1"}]}
        with patch.object(updater, "fetch", return_value=json.dumps(payload).encode()):
            self.assertEqual(updater.read_remotive()[0]["company"], "Example")
        with patch.object(updater, "fetch", return_value=b'{}'):
            with self.assertRaises(ValueError):
                updater.read_remotive()

    def test_failed_source_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "jobs.json"
            output.write_text('[{"existing": true}]\n')
            before = output.read_bytes()
            with patch("sys.argv", ["update_jobs.py", "--output", str(output)]), \
                 patch.object(updater, "read_wwr", side_effect=OSError("offline")), \
                 patch.object(updater, "read_remotive", return_value=[]), \
                 self.assertLogs(updater.LOG, level="ERROR"):
                self.assertEqual(updater.main(), 1)
            self.assertEqual(output.read_bytes(), before)

    def test_atomic_output_and_unchanged_write(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "jobs.json"
            updater.write_atomic(output, [])
            before = output.stat().st_mtime_ns
            updater.write_atomic(output, [])
            self.assertEqual(output.stat().st_mtime_ns, before)
            self.assertEqual(json.loads(output.read_text()), [])

    def test_empty_success_seeds_samples_and_live_jobs_replace_them(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "jobs.json"
            with patch("sys.argv", ["update_jobs.py", "--output", str(output)]), \
                 patch.object(updater, "read_wwr", return_value=[]), \
                 patch.object(updater, "read_remotive", return_value=[]):
                self.assertEqual(updater.main(), 0)
            samples = json.loads(output.read_text())
            self.assertEqual(len(samples), 3)
            for job in samples:
                self.assertIn("Sample Listing", job["tags"])
                self.assertIn("Sample Listing", job["title"])
                self.assertIn("example.invalid", job["source_link"])
                self.assertEqual(set(job), {"title", "company", "date_posted",
                                            "source_link", "tags"})
            before = output.read_bytes()
            updater.write_atomic(output, updater.sample_jobs())
            self.assertEqual(output.read_bytes(), before)
            live = self.row("Use email and Intercom.",
                            date=datetime.now(timezone.utc).isoformat())
            with patch("sys.argv", ["update_jobs.py", "--output", str(output)]), \
                 patch.object(updater, "read_wwr", return_value=[live]), \
                 patch.object(updater, "read_remotive", return_value=[live]):
                self.assertEqual(updater.main(), 0)
            jobs = json.loads(output.read_text())
            self.assertEqual(len(jobs), 1)
            self.assertNotIn("Sample Listing", jobs[0]["tags"])


if __name__ == "__main__":
    unittest.main()
