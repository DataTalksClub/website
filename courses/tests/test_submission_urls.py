from unittest import TestCase

from courses.views.submission_urls import canonical_github_repository_url


class CanonicalGithubRepositoryUrlTests(TestCase):
    def test_normalizes_repository_urls(self):
        for submitted in (
            "https://github.com/learner/project.git",
            "https://github.com/learner/project/",
            "http://www.github.com/learner/project.git/",
        ):
            with self.subTest(submitted=submitted):
                self.assertEqual(
                    canonical_github_repository_url(submitted),
                    "https://github.com/learner/project",
                )

    def test_preserves_other_links_and_specific_github_content(self):
        for submitted in (
            "https://example.com/learner/project.git",
            "https://github.com/learner/project/tree/main",
            "https://github.com/learner/project?tab=readme-ov-file",
            "https://github.com/learner/project#readme",
            "https://github.com.evil.example/learner/project.git",
            "https://user@github.com/learner/project.git",
            "https://github.com:8443/learner/project.git",
            "https://github.com:bad/learner/project.git",
        ):
            with self.subTest(submitted=submitted):
                self.assertEqual(canonical_github_repository_url(submitted), submitted)
