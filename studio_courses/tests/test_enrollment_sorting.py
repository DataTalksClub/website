"""UX-09: enrollment sorting is server-side, allowlisted, and pre-pagination.

The enrollment table's sort used to be a client-side re-ordering of whatever
rows the current page happened to contain, so "score descending" could never
surface the globally highest scorer from a later page. The sort now lives in
the URL, is validated against an explicit allowlist, is applied to the full
filtered list before pagination, and the header controls carry aria-sort and
preserve the search/status filters.
"""

from django.test import TestCase
from django.urls import reverse

from accounts.studio_test_support import make_studio_user
from courses.models import Cohort, Enrollment, User
from studio_courses.views.view_models import (
    enrollment_list_data,
    normalize_enrollment_sort,
)


def usernames(enrollments):
    return [enrollment.student.username for enrollment in enrollments]


class NormalizeEnrollmentSortTests(TestCase):
    def test_unknown_values_fall_back_to_the_default_column(self):
        self.assertEqual(
            normalize_enrollment_sort("password; --", "desc"),
            ("position", "desc"),
        )
        self.assertEqual(
            normalize_enrollment_sort("student", "upside-down"),
            ("student", "asc"),
        )
        self.assertEqual(normalize_enrollment_sort("", ""), ("position", "asc"))

    def test_known_pairs_pass_through(self):
        self.assertEqual(
            normalize_enrollment_sort("total_score", "asc"),
            ("total_score", "asc"),
        )


class EnrollmentSortOrderTests(TestCase):
    def setUp(self):
        self.course = Cohort.objects.create(
            slug="sort-course",
            title="Sort Course",
            description="Sorting acceptance course",
        )
        # Positions 1..30 put the eventual top scorer on the second page of
        # the legacy ordering: exactly the row the old client-side sort could
        # never bring onto page one.
        for index in range(1, 31):
            student = User.objects.create_user(
                username=f"student-{index:02d}",
                email=f"student-{index:02d}@example.com",
                password="test",
            )
            Enrollment.objects.create(
                student=student,
                course=self.course,
                display_name=f"Student {index:02d}",
                position_on_leaderboard=index,
                total_score=31 - index,
            )

    def ranked(self, sort, direction, status="all"):
        enrollments, _counts = enrollment_list_data(
            self.course, "", status, sort=sort, direction=direction
        )
        return enrollments

    def test_descending_score_puts_the_global_top_first(self):
        ranked_enrollments = self.ranked("total_score", "desc")
        # student-30 holds score 1 under the legacy ordering; the globally
        # highest score (500 would be another student's) belongs to
        # student-01 at position 30.
        self.assertEqual(usernames(ranked_enrollments)[0], "student-01")
        self.assertEqual(usernames(ranked_enrollments)[-1], "student-30")
        # The first page of 25 must now contain the row that used to live on
        # page two.
        first_page = ranked_enrollments[:25]
        self.assertIn("student-01", usernames(first_page))

    def test_ascending_score_is_the_mirror(self):
        ranked_enrollments = self.ranked("total_score", "asc")
        self.assertEqual(usernames(ranked_enrollments)[0], "student-30")

    def test_missing_leaderboard_position_sorts_last_in_both_directions(self):
        unranked = Enrollment.objects.get(
            student__username="student-15",
            course=self.course,
        )
        unranked.position_on_leaderboard = None
        unranked.save()

        ascending = self.ranked("position", "asc")
        descending = self.ranked("position", "desc")
        self.assertEqual(usernames(ascending)[-1], "student-15")
        self.assertEqual(usernames(descending)[-1], "student-15")
        # Descending still starts from the highest rank.
        self.assertEqual(usernames(descending)[0], "student-30")

    def test_score_ties_keep_the_queryset_order(self):
        # Positions 2, 4 and 6 collapse onto one score (student-16 already
        # holds it): a stable sort must present the whole tied group in the
        # queryset's (position, id) order, after every higher score.
        Enrollment.objects.filter(course=self.course, position_on_leaderboard__in=[2, 4, 6]).update(
            total_score=15
        )
        ranked = usernames(self.ranked("total_score", "desc"))
        tied = [
            name
            for name in ranked
            if name in {"student-02", "student-04", "student-06", "student-16"}
        ]
        self.assertEqual(tied, ["student-02", "student-04", "student-06", "student-16"])

    def test_student_sort_is_case_insensitive(self):
        uppercase = Enrollment.objects.get(student__username="student-02", course=self.course)
        uppercase.student.username = "AAA-STUDENT"
        uppercase.student.save()
        ranked_enrollments = self.ranked("student", "asc")
        self.assertEqual(usernames(ranked_enrollments)[0], "AAA-STUDENT")

    def test_no_sort_arguments_match_the_legacy_ordering(self):
        legacy = list(
            Enrollment.objects.filter(course=self.course).order_by("position_on_leaderboard", "id")
        )
        self.assertEqual(
            usernames(self.ranked("position", "asc")),
            [enrollment.student.username for enrollment in legacy],
        )

    def test_status_filter_and_sort_compose(self):
        hidden = Enrollment.objects.get(student__username="student-03", course=self.course)
        hidden.display_on_leaderboard = False
        hidden.save()
        ranked_enrollments, counts = enrollment_list_data(
            self.course, "", "hidden", sort="total_score", direction="desc"
        )
        self.assertEqual(usernames(ranked_enrollments), ["student-03"])
        self.assertEqual(counts["hidden"], 1)


class EnrollmentSortViewTests(TestCase):
    def setUp(self):
        self.course = Cohort.objects.create(
            slug="sort-view-course",
            title="Sort View Course",
            description="Sorting template contract course",
        )
        for index in range(1, 31):
            student = User.objects.create_user(
                username=f"view-{index:02d}",
                email=f"view-{index:02d}@example.com",
                password="test",
            )
            Enrollment.objects.create(
                student=student,
                course=self.course,
                position_on_leaderboard=index,
                total_score=31 - index,
            )
        self.staff = make_studio_user(username="sort-staffer", roles=("course_operator",))
        self.client.force_login(self.staff)
        self.url = reverse(
            "studio_courses_enrollments",
            kwargs={"course_slug": self.course.slug},
        )

    def first_row_username(self, response):
        return response.context["enrollments"][0].student.username

    def test_descending_score_url_brings_the_global_top_to_page_one(self):
        response = self.client.get(self.url, {"sort": "total_score", "dir": "desc"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.first_row_username(response), "view-01")

    def test_unknown_sort_param_is_refused_by_the_allowlist(self):
        response = self.client.get(self.url, {"sort": "student__password", "dir": "desc"})
        self.assertEqual(response.status_code, 200)
        # The column falls back to the default; an explicit direction is
        # still honored, so this is position descending.
        self.assertEqual(self.first_row_username(response), "view-30")
        self.assertEqual(response.context["current_sort"], "position")

    def test_unknown_sort_and_direction_fall_back_to_the_default_view(self):
        response = self.client.get(self.url, {"sort": "junk", "dir": "junk"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.first_row_username(response), "view-01")
        self.assertEqual(response.context["current_sort"], "position")
        self.assertEqual(response.context["current_direction"], "asc")

    def test_active_header_carries_aria_sort_and_arrow(self):
        response = self.client.get(self.url, {"sort": "total_score", "dir": "desc"})
        body = response.content.decode()
        self.assertIn('aria-sort="descending"', body)
        self.assertNotIn('aria-sort="ascending"', body)
        # Server-rendered arrow, present without JavaScript.
        self.assertIn("▼", body)

    def test_sort_links_preserve_filters_and_reset_the_page(self):
        response = self.client.get(self.url, {"q": "view-0", "page": "2"})
        body = response.content.decode()
        # A header link keeps the search, switches the sort, and carries no
        # page parameter — landing on page one of the new ordering.
        self.assertIn(
            "?q=view-0&amp;sort=student&amp;dir=asc",
            body,
        )
        self.assertNotIn("?q=view-0&amp;sort=student&amp;dir=asc&amp;page=", body)

    def test_pagination_links_carry_the_sort(self):
        response = self.client.get(self.url, {"sort": "total_score", "dir": "desc"})
        body = response.content.decode()
        self.assertIn("page=2&amp;sort=total_score&amp;dir=desc", body)

    def test_search_form_repeats_the_current_sort(self):
        response = self.client.get(self.url, {"sort": "hw_count", "dir": "asc"})
        body = response.content.decode()
        self.assertIn('name="sort" value="hw_count"', body)
        self.assertIn('name="dir" value="asc"', body)

    def test_sorted_state_is_announced_in_the_results_line(self):
        response = self.client.get(self.url, {"sort": "total_score", "dir": "desc"})
        self.assertContains(response, "sorted by total_score (desc)")

    def test_second_page_of_a_sorted_view_is_the_sorted_sequence(self):
        response = self.client.get(self.url, {"sort": "total_score", "dir": "desc", "page": "2"})
        self.assertEqual(response.status_code, 200)
        page_two = usernames(response.context["enrollments"])
        self.assertEqual(len(page_two), 5)
        # Scores descend with position ascending, so the tail of the sorted
        # sequence is the lowest scorer.
        self.assertEqual(page_two[-1], "view-30")
