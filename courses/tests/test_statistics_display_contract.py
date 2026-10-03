"""Complete statistics presentation contracts through the real site callers."""

from django.test import SimpleTestCase

from courses.models.homework import HomeworkStatistics
from courses.models.project import ProjectStatistics
from courses.views.project_statistics import project_statistics_sections

Value = int | float | None
Statistics = HomeworkStatistics | ProjectStatistics
Section = tuple[str, str, str]

ROWS = (
    ("min", "Minimum", "fas fa-arrow-down"),
    ("max", "Maximum", "fas fa-arrow-up"),
    ("avg", "Average", "fas fa-equals"),
    ("q1", "25th Percentile", "fas fa-percentage"),
    ("median", "Median", "fas fa-percentage"),
    ("q3", "75th Percentile", "fas fa-percentage"),
)
HOMEWORK = (
    ("questions_score", "Questions score", "fas fa-question-circle"),
    ("total_score", "Total score", "fas fa-star"),
    ("time_spent_lectures", "Time spent on lectures", "fas fa-book-reader"),
    ("time_spent_homework", "Time spent on homework", "fas fa-clock"),
    ("learning_in_public_score", "Learning in public score", "fas fa-globe"),
)
PROJECT = (
    ("project_score", "Project score", "fas fa-project-diagram"),
    ("project_learning_in_public_score", "Project learning in public score", "fas fa-globe"),
    ("peer_review_score", "Peer review score", "fas fa-users"),
    (
        "peer_review_learning_in_public_score",
        "Peer review learning in public score",
        "fas fa-share-alt",
    ),
    ("total_score", "Total score", "fas fa-star"),
    ("time_spent", "Time spent on project", "fas fa-clock"),
)
VIEW_KEYS = ("min", "q1", "median", "avg", "q3", "max")


def set_distribution(
    stats: Statistics, fields: tuple[Section, ...], values: tuple[Value, ...]
) -> None:
    for field, _, _ in fields:
        for (row, _, _), value in zip(ROWS, values, strict=True):
            setattr(stats, f"{row}_{field}", value)


def expected_display(fields: tuple[Section, ...], values: tuple[Value, ...]) -> list:
    result = []
    for _, label, icon in fields:
        rows = []
        for (_, row_label, row_icon), value in zip(ROWS, values, strict=True):
            rows.append((value, row_label, row_icon))
        result.append((label, rows, icon))
    return result


def context_card(label: str, values: tuple[Value, ...], positions: tuple[Value, ...]) -> dict:
    q1, median, average, q3, width = positions
    return {
        "label": label,
        "is_time": False,
        "values": dict(zip(VIEW_KEYS, values, strict=True)),
        "has_data": True,
        "q1_position": q1,
        "median_position": median,
        "average_position": average,
        "q3_position": q3,
        "iqr_width": width,
    }


class StatisticsDisplayContractTests(SimpleTestCase):
    def assert_complete_display(
        self, stats: Statistics, fields: tuple[Section, ...], values: tuple[Value, ...]
    ) -> None:
        set_distribution(stats, fields, values)
        actual = stats.get_stat_fields()
        self.assertIs(type(actual), list)
        self.assertEqual(actual, expected_display(fields, values))
        for section in actual:
            self.assertIs(type(section), tuple)
            self.assertIs(type(section[1]), list)
            for row, value in zip(section[1], values, strict=True):
                self.assertIs(type(row), tuple)
                self.assertIs(type(row[0]), type(value))

    def test_homework_complete_output_preserves_missing_zero_integer_and_fractional_values(
        self,
    ) -> None:
        for values in (
            (None,) * 6,
            (0,) * 6,
            (2, 20, 11.5, 5.25, 10.0, 16.75),
            (None, 0, 2.5, 0, 4, 6.25),
        ):
            with self.subTest(values=values):
                self.assert_complete_display(HomeworkStatistics(), HOMEWORK, values)

    def test_project_complete_output_preserves_missing_zero_integer_and_fractional_values(
        self,
    ) -> None:
        for values in (
            (None,) * 6,
            (0,) * 6,
            (2, 20, 11.5, 5.25, 10.0, 16.75),
            (None, 0, 2.5, 0, 4, 6.25),
        ):
            with self.subTest(values=values):
                self.assert_complete_display(ProjectStatistics(), PROJECT, values)

    def test_homework_sections_read_their_own_distribution(self) -> None:
        stats = HomeworkStatistics()
        expected = []
        for index, section in enumerate(HOMEWORK):
            values = (index, index + 10, index + 4.5, index + 2.25, index + 5.0, index + 7.75)
            set_distribution(stats, (section,), values)
            expected.extend(expected_display((section,), values))
        self.assertEqual(stats.get_stat_fields(), expected)

    def test_project_sections_read_their_own_distribution(self) -> None:
        stats = ProjectStatistics()
        expected = []
        for index, section in enumerate(PROJECT):
            values = (index, index + 10, index + 4.5, index + 2.25, index + 5.0, index + 7.75)
            set_distribution(stats, (section,), values)
            expected.extend(expected_display((section,), values))
        self.assertEqual(stats.get_stat_fields(), expected)

    def test_view_context_preserves_fractional_positions_and_only_project_time_marker(self) -> None:
        stats = ProjectStatistics()
        set_distribution(stats, PROJECT, (0, 10, 4.25, 2.5, 5.0, 7.5))
        expected = []
        for _, label, _ in PROJECT:
            expected.append(
                context_card(label, (0, 2.5, 5.0, 4.25, 7.5, 10), (25.0, 50.0, 42.5, 75.0, 50.0))
            )
        expected[-1]["is_time"] = True
        self.assertEqual(project_statistics_sections(stats), expected)

    def test_view_context_preserves_zero_and_equal_range_positions(self) -> None:
        for value in (0, 3.25):
            with self.subTest(value=value):
                stats = ProjectStatistics()
                set_distribution(stats, PROJECT, (value,) * 6)
                expected = []
                for _, label, _ in PROJECT:
                    expected.append(
                        context_card(label, (value,) * 6, (50.0, 50.0, 50.0, 50.0, 0.0))
                    )
                expected[-1]["is_time"] = True
                self.assertEqual(project_statistics_sections(stats), expected)

    def test_view_context_preserves_absent_data_and_no_positions(self) -> None:
        stats = ProjectStatistics()
        expected = []
        for _, label, _ in PROJECT:
            card = context_card(label, (None,) * 6, (None,) * 5)
            card["has_data"] = False
            expected.append(card)
        expected[-1]["is_time"] = True
        self.assertEqual(project_statistics_sections(stats), expected)

    def test_view_context_preserves_partial_data_without_fabricating_iqr(self) -> None:
        stats = ProjectStatistics()
        set_distribution(stats, PROJECT, (0, 10, 4.25, None, 5.0, 7.5))
        expected = []
        for _, label, _ in PROJECT:
            card = context_card(
                label, (0, None, 5.0, 4.25, 7.5, 10), (None, 50.0, 42.5, 75.0, None)
            )
            card["has_data"] = False
            expected.append(card)
        expected[-1]["is_time"] = True
        self.assertEqual(project_statistics_sections(stats), expected)

    def test_view_context_preserves_clamped_positions_and_nonnegative_iqr(self) -> None:
        stats = ProjectStatistics()
        set_distribution(stats, PROJECT, (0, 10, 12.5, 20.0, -1.0, -5.0))
        expected = []
        for _, label, _ in PROJECT:
            expected.append(
                context_card(label, (0, 20.0, -1.0, 12.5, -5.0, 10), (100.0, 0.0, 100.0, 0.0, 0.0))
            )
        expected[-1]["is_time"] = True
        self.assertEqual(project_statistics_sections(stats), expected)
