"""Cross-worker leaderboard invalidation backed by the cohort row."""

from django.conf import settings
from django.db.models import F

from courses.models.cohort import Cohort


def leaderboard_cache_version(cohort_id: int) -> int:
    # Always read the database: model instances and process-local cache entries
    # may predate another worker's score or visibility update.
    return Cohort.objects.values_list("leaderboard_cache_version", flat=True).get(
        pk=cohort_id
    )


def invalidate_leaderboard_cache(cohort_id: int) -> None:
    Cohort.objects.filter(pk=cohort_id).update(
        leaderboard_cache_version=F("leaderboard_cache_version") + 1
    )


def leaderboard_cache_ttl() -> int:
    return int(getattr(settings, "LEADERBOARD_CACHE_TTL", 60))
