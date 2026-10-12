import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import update_language_stats as stats


class LanguageForPathTests(unittest.TestCase):
    def test_recognizes_source_files_and_special_filenames(self):
        self.assertEqual(stats.language_for_path("src/App.tsx"), "TypeScript")
        self.assertEqual(stats.language_for_path("types/API.D.TS"), "TypeScript")
        self.assertEqual(stats.language_for_path("backend/Dockerfile"), "Dockerfile")
        self.assertEqual(stats.language_for_path("notebooks/model.ipynb"), "Jupyter Notebook")

    def test_ignores_non_code_and_generated_files(self):
        self.assertIsNone(stats.language_for_path("README.md"))
        self.assertIsNone(stats.language_for_path("public/logo.png"))
        self.assertIsNone(stats.language_for_path("dist/app.min.js"))
        self.assertIsNone(stats.language_for_path("package-lock.json"))


class CommitCollectionTests(unittest.TestCase):
    @patch("update_language_stats.api_get")
    def test_search_deduplicates_a_commit_found_through_a_fork(self, api_get):
        shared = {"sha": "abc", "url": f"{stats.API_ROOT}/repos/a/project/commits/abc"}
        forked = {"sha": "abc", "url": f"{stats.API_ROOT}/repos/b/project/commits/abc"}
        api_get.return_value = (
            {"total_count": 2, "incomplete_results": False, "items": [shared, forked]},
            {},
        )

        commits = stats.search_authored_commits(
            "octocat", "token", 30, today=date(2026, 10, 13)
        )

        self.assertEqual(commits, [shared])

    @patch("update_language_stats._commit_files")
    @patch("update_language_stats.search_authored_commits")
    def test_collects_changed_lines_from_non_merge_code_commits(self, search, commit_files):
        search.return_value = [
            {
                "sha": "merge",
                "url": f"{stats.API_ROOT}/repos/team/app/commits/merge",
                "parents": [{}, {}],
                "repository": {"full_name": "team/app"},
            },
            {
                "sha": "code",
                "url": f"{stats.API_ROOT}/repos/team/app/commits/code",
                "parents": [{}],
                "repository": {"full_name": "team/app"},
            },
            {
                "sha": "docs",
                "url": f"{stats.API_ROOT}/repos/team/docs/commits/docs",
                "parents": [{}],
                "repository": {"full_name": "team/docs"},
            },
        ]
        commit_files.side_effect = [
            [
                {"filename": "src/app.ts", "changes": 10},
                {"filename": "api/main.py", "additions": 3, "deletions": 2},
                {"filename": "public/logo.png", "changes": 500},
            ],
            [{"filename": "README.md", "changes": 20}],
        ]

        totals, commits, repositories, changed_lines = stats.collect_languages(
            "octocat", "token", 365
        )

        self.assertEqual(totals, {"TypeScript": 10, "Python": 5})
        self.assertEqual(commits, 1)
        self.assertEqual(repositories, 1)
        self.assertEqual(changed_lines, 15)
        self.assertEqual(commit_files.call_count, 2)


class RenderingTests(unittest.TestCase):
    def test_svg_explains_commit_based_measurement(self):
        languages = stats.summarized_languages(stats.Counter({"Python": 75, "Go": 25}))

        svg = stats.render_svg("A&B", languages, 8, 3, 100, 365)

        self.assertIn("100 changed lines · 8 commits · 3 repos · 365d", svg)
        self.assertIn("non-merge commits", svg)
        self.assertIn("A&amp;B", svg)


if __name__ == "__main__":
    unittest.main()
