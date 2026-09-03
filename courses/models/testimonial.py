"""Member testimonials, owned by the database and scoped by placement.

A testimonial is either a site-wide one shown on the homepage or one that
belongs to a single course family.  That scope is a stored field plus a nullable
relation, and the pair is checked in the database rather than trusted at the
read site: ``courses_testimonial_placement_scope`` refuses a homepage row that
names a course, a course row that names none, and any placement value the two
branches do not spell out.  A convention enforced only by the code that happens
to write a row is the kind that quietly stops holding.
"""

from __future__ import annotations

import logging

from django.contrib.staticfiles.storage import staticfiles_storage
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator, URLValidator
from django.db import models
from django.db.models import Q

logger = logging.getLogger(__name__)

#: A key names a location inside one configured prefix and nothing else: no
#: scheme, no host, no leading slash, no traversal, no backslash.  Storing an
#: absolute URL instead would put the origin in every row, so moving the bucket
#: or the CDN would become a data migration -- and it would let whoever can edit
#: a testimonial point the anonymous homepage's ``<img src>`` at any third-party
#: host, which is a data-controlled outbound request on the most-cached page the
#: site owns.  A relative key cannot express that.
#:
#: The pattern and message are plain strings, and the validator is built inline
#: on the field, so a migration serializes it *by value* and never imports this
#: module.  ``courses.models.curriculum``'s ``source_path`` is the precedent.
ASSET_KEY_PATTERN = r"^(?!/)(?!.*\.\.)(?!.*\\)(?!\w+:)[\w.-]+(?:/[\w.-]+)*$"
ASSET_KEY_MESSAGE = (
    "Enter a key relative to the site-assets prefix, e.g. 'testimonials/example.jpg'."
)

#: Where a ``site-assets/`` key still resolves while the CDN move is in flight.
#: The bucket layout being built drops the ``public-projection/`` prefix, so its
#: top level becomes ``images/`` and ``site-assets/`` as siblings and portraits
#: land at ``site-assets/testimonials/``.  In the repository the same files are
#: still served by staticfiles under ``core/``.  Only this prefix moves when the
#: bucket lands: the stored key is already relative to ``site-assets/`` and
#: carries no part of either layout, which is the property that lets it survive
#: the move untouched.
INTERIM_SITE_ASSET_STATIC_PREFIX = "core/"


class TestimonialPlacement(models.TextChoices):
    """Where one testimonial belongs."""

    HOMEPAGE = "homepage", "Homepage"
    COURSE = "course", "Course family"


class Testimonial(models.Model):
    """One member quote, with the attribution that makes it checkable.

    ``source_url`` links the quote back to the public post it was taken from, so
    the attribution is verifiable rather than asserted, and
    ``portrait_asset_key`` names the person's portrait inside the site-assets
    prefix.  Read the portrait through :attr:`portrait_url`, never by resolving
    the stored key directly; that property is what keeps a bad row from taking
    the page down with it.

    ``role_before``, ``role_after`` and ``elapsed`` are optional because not
    every real testimonial states a role transition or how long it took, and a
    card must not invent one.  The template skips the transition chips unless
    both roles are present, and skips the elapsed pill when it is empty.
    """

    placement = models.CharField(
        max_length=16,
        choices=TestimonialPlacement.choices,
        help_text="Homepage testimonials carry no course; course testimonials carry exactly one.",
    )
    course = models.ForeignKey(
        "courses.Course",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="testimonials",
        help_text="The course family this testimonial belongs to. Empty for the homepage.",
    )
    name = models.CharField(max_length=200)
    attribution = models.CharField(
        max_length=200,
        blank=True,
        help_text="The role and country line shown under the name, e.g. 'Data Engineer · Spain'.",
    )
    quote = models.TextField()
    source_url = models.URLField(
        max_length=500,
        blank=True,
        validators=[URLValidator()],
        help_text="The public post the quote is taken from.",
    )
    portrait_asset_key = models.CharField(
        max_length=200,
        blank=True,
        validators=[RegexValidator(ASSET_KEY_PATTERN, ASSET_KEY_MESSAGE)],
        help_text=(
            "Portrait key relative to the site-assets prefix, "
            "e.g. 'testimonials/example.jpg'. Empty renders the plain avatar mark."
        ),
    )
    role_before = models.CharField(max_length=120, blank=True)
    role_after = models.CharField(max_length=120, blank=True)
    elapsed = models.CharField(
        max_length=60,
        blank=True,
        help_text="How long the change took, e.g. '6 months'.",
    )
    position = models.PositiveIntegerField(
        default=0,
        help_text="Lower positions are shown first within one placement.",
    )
    published = models.BooleanField(
        default=False,
        help_text="Unpublished testimonials are stored but never rendered.",
    )

    class Meta:
        ordering = ("position", "id")
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(placement=TestimonialPlacement.HOMEPAGE, course__isnull=True)
                    | Q(placement=TestimonialPlacement.COURSE, course__isnull=False)
                ),
                name="courses_testimonial_placement_scope",
            ),
        ]
        indexes = [
            models.Index(
                fields=("placement", "published", "position"),
                name="courses_testimonial_read_idx",
            ),
        ]

    def __str__(self) -> str:
        scope = self.course.title if self.course_id else "homepage"
        return f"{self.name} ({scope})"

    @property
    def portrait_url(self) -> str:
        """The portrait's public URL, or ``""`` when it cannot be resolved.

        **This never raises, and that is the whole point of it.**  A stored key
        is a database value: it can change after the asset manifest was built,
        by an editor typing a name that does not exist.  Under
        ``CompressedManifestStaticFilesStorage`` an unknown reference is a
        ``ValueError``, not a missing file -- and this is read inside the
        homepage's story loop, so one stale row would abandon the whole render
        and answer 500 rather than lose one portrait.  The five good rows, and
        every other band on the page, would go with it.

        Degrading instead costs one card its photograph: the empty string takes
        the template's decorative-avatar branch, which is the same designed
        empty state a testimonial with no portrait already uses.  The broad
        ``except`` is deliberate rather than lazy -- what matters is that no
        failure mode of asset resolution, present or future, can reach the
        caller, and narrowing it to today's exception type would quietly
        reintroduce the hazard the day the storage backend changes.
        """

        key = self.portrait_asset_key.strip()
        if not key:
            return ""
        try:
            return staticfiles_storage.url(f"{INTERIM_SITE_ASSET_STATIC_PREFIX}{key}")
        except Exception:
            # The key is editorial, not secret, and naming it is what makes the
            # warning actionable; there is no request or person in it.
            logger.warning(
                "Testimonial portrait could not be resolved; rendering the avatar mark.",
                extra={"portrait_asset_key": key},
            )
            return ""

    def clean(self) -> None:
        """Report the stored constraint as a form error instead of a 500."""

        super().clean()
        if self.placement == TestimonialPlacement.HOMEPAGE and self.course_id is not None:
            raise ValidationError({"course": "A homepage testimonial cannot name a course."})
        if self.placement == TestimonialPlacement.COURSE and self.course_id is None:
            raise ValidationError({"course": "A course testimonial must name a course."})
