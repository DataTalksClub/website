from django.contrib import admin
from unfold.admin import ModelAdmin

from courses.models.testimonial import Testimonial


@admin.register(Testimonial)
class TestimonialAdmin(ModelAdmin):
    """The editing surface for member quotes.

    ``Testimonial.clean`` reports the stored placement constraint here, so an
    editor who names a course on a homepage testimonial gets a field error
    instead of a database failure.
    """

    list_display = ("name", "placement", "course", "position", "published")
    list_filter = ("placement", "published", "course")
    search_fields = ("name", "attribution", "quote")
    ordering = ("placement", "position", "id")
    fieldsets = (
        (None, {"fields": ("placement", "course", "published", "position")}),
        ("Person", {"fields": ("name", "attribution", "portrait_asset_key")}),
        ("Quote", {"fields": ("quote", "source_url")}),
        (
            "Optional transition",
            {
                "classes": ("collapse",),
                "fields": ("role_before", "role_after", "elapsed"),
            },
        ),
    )
