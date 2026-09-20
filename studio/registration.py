"""D2.1b: claim every mounted Studio route for the package navigation registry.

The site keeps rendering templates/studio/base.html. This module only tells
the package which destination owns which route so ``studio_routes --check``
can see a partition.
"""

from community_base.studio.registry import (
    Destination,
    Section,
    register,
    routes_without_home,
)

from studio_courses.urls import ROUTE_DEFINITIONS


def _course_route_names() -> tuple[str, ...]:
    return tuple(f"studio_courses_{name}" for _, _, name in ROUTE_DEFINITIONS)


def register_dtc_studio_destinations() -> None:
    # Package dashboard is mounted at the same path as studio:home; the site
    # home wins the request, so the package name is not a sidebar destination.
    routes_without_home.add("studio_dashboard")
    register(
        Section(
            slug="home",
            title="",
            order=0,
            icon="layout-dashboard",
            destinations=(
                Destination(
                    key="dashboard",
                    title="Dashboard",
                    url_name="studio:home",
                    route_names=("home",),
                    order=0,
                    icon="layout-dashboard",
                ),
            ),
        )
    )
    register(
        Section(
            slug="site",
            title="Site",
            order=10,
            icon="sliders-horizontal",
            destinations=(
                Destination(
                    key="site_settings",
                    title="Site settings",
                    url_name="studio:settings",
                    route_names=("settings",),
                    order=10,
                    icon="settings",
                ),
                Destination(
                    key="navigation",
                    title="Navigation",
                    url_name="studio:navigation",
                    route_names=("navigation",),
                    order=20,
                    icon="menu",
                ),
                Destination(
                    key="sponsors",
                    title="Sponsors",
                    url_name="studio:sponsor-list",
                    route_names=(
                        "sponsor-list",
                        "sponsor-export",
                        "sponsor-detail",
                        "sponsor-archive",
                        "sponsor-reactivate",
                    ),
                    order=30,
                    icon="handshake",
                ),
            ),
        )
    )
    register(
        Section(
            slug="access",
            title="Access",
            order=20,
            icon="key",
            destinations=(
                Destination(
                    key="credentials",
                    title="API credentials",
                    url_name="studio:credential-list",
                    route_names=(
                        "credential-list",
                        "credential-rotate",
                        "credential-revoke",
                    ),
                    order=10,
                    icon="key",
                ),
            ),
        )
    )
    register(
        Section(
            slug="audit",
            title="Audit",
            order=30,
            icon="scroll-text",
            destinations=(
                Destination(
                    key="audit",
                    title="Audit",
                    url_name="studio:audit-list",
                    route_names=("audit-list", "audit-export", "audit-detail"),
                    order=10,
                    icon="scroll-text",
                ),
            ),
        )
    )
    register(
        Section(
            slug="events",
            title="Events",
            order=50,
            icon="calendar",
            destinations=(
                Destination(
                    key="event_identities",
                    title="Identities",
                    url_name="studio:event-identity-list",
                    route_names=("event-identity-list", "event-identity-detail"),
                    order=10,
                    icon="id-card",
                ),
                Destination(
                    key="event_qna",
                    title="Q&A",
                    url_name="studio:event-qna-detail",
                    route_names=(
                        "event-qna-detail",
                        "event-qna-update",
                        "event-qna-moderate",
                        "event-qna-retry",
                        "event-qna-cohost",
                        "event-qna-cohost-revoke",
                    ),
                    order=20,
                    icon="message-circle",
                ),
            ),
        )
    )
    register(
        Section(
            slug="courses",
            title="Courses",
            order=35,
            icon="book",
            destinations=(
                Destination(
                    key="studio_courses",
                    title="Course operations",
                    url_name="studio_courses_course_list",
                    route_names=_course_route_names() + ("cadmin_course_list",),
                    order=20,
                    icon="graduation-cap",
                ),
            ),
        )
    )
