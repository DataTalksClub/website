import argparse
from urllib.parse import quote

import pytest

from scripts.dev.check_course_illustrations import (
    ROOT,
    base_url,
    course_route,
    inspect_page,
    output_directory,
    parse_args,
    viewport,
)


@pytest.mark.parametrize(
    "value",
    [
        "https://user:secret@example.com",
        "https://example.com/courses",
        "file:///tmp/page",
        "http://localhost:8000?token=secret",
        "http://localhost:8000#fragment",
    ],
)
def test_origins_cannot_include_credentials_or_private_request_inputs(value):
    with pytest.raises(argparse.ArgumentTypeError):
        base_url(value)


@pytest.mark.parametrize(
    "value",
    [
        "/courses/../admin",
        "//example.com",
        "/courses/ml?token=x",
        "/courses/register",
        "/courses/projects",
        "/courses/ml-zoomcamp/2026",
        "https://example.com/courses",
    ],
)
def test_only_public_catalogue_or_family_routes_can_be_selected(value):
    with pytest.raises(argparse.ArgumentTypeError):
        course_route(value)


@pytest.mark.parametrize("value", [".", ".tmp", ".tmp/../../outside", "/var/tmp/course-check"])
def test_output_stays_in_a_project_scratch_subdirectory(value):
    with pytest.raises(argparse.ArgumentTypeError):
        output_directory(value)


def test_repeated_hosts_and_course_selection_keep_the_full_default_viewport_matrix():
    args = parse_args(
        [
            "--base-url",
            "http://localhost:8000/",
            "--base-url",
            "http://127.0.0.1:8091",
            "--route",
            "/courses",
            "--family",
            "ml-zoomcamp",
            "--output-dir",
            ".tmp/course-check-test",
        ]
    )

    assert args.base_url == ["http://localhost:8000", "http://127.0.0.1:8091"]
    assert args.route == ["/courses", "/courses/ml-zoomcamp"]
    assert args.viewport == ((1440, 900), (768, 1024), (390, 844), (320, 740))
    assert args.theme == ["light", "dark"]
    assert args.output_dir == ROOT / ".tmp/course-check-test"


def test_custom_viewport_requires_positive_bounded_geometry():
    assert viewport("390x844") == (390, 844)
    with pytest.raises(argparse.ArgumentTypeError):
        viewport("-1x0")


def _catalogue_markup(theme):
    image_index = 0

    def image(class_name="", loading="lazy"):
        nonlocal image_index
        image_index += 1
        image_url = "data:image/svg+xml," + quote(
            '<svg xmlns="http://www.w3.org/2000/svg" width="80" height="80">'
            f'<rect width="80" height="80" fill="purple"/><!-- {image_index} --></svg>'
        )
        return (
            f'<img class="{class_name}" src="{image_url}" alt="" '
            f'width="80" height="80" loading="{loading}" decoding="async">'
        )

    def pair():
        return image("doodle-light") + image("doodle-dark")

    hidden = "dark" if theme == "light" else "light"
    return (
        f"<style>.doodle-{hidden} {{ display:none }} "
        ".spacer {height:5000px} body {margin:0}</style>"
        '<div class="hero-collage-card">'
        + image("doodle-light", "eager")
        + image("doodle-dark", "eager")
        + "<p class='hero-collage-caption'>A course title is not a placeholder</p></div>"
        + '<div class="spacer"></div><div class="catalog-card-media">'
        + pair()
        + '</div><div class="spacer"></div><div class="catalog-card-media">'
        + image()
        + '</div><div class="spacer"></div><div class="catalog-card-media">'
        + pair()
        + "</div>"
    )


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_checker_loads_every_lazy_card_and_accepts_single_campaign_images(page, theme):
    page.set_viewport_size({"width": 390, "height": 844})
    page.set_content(_catalogue_markup(theme))
    assert (
        page.locator(".catalog-card-media")
        .last.locator("img:visible")
        .evaluate("img => !img.complete")
    )

    report = inspect_page(page, route="/courses", theme=theme, width=390)

    assert report["failures"] == []
    assert len(report["geometry"]) == 4
    assert len(report["images"]) == 7
    assert sum(image["visible"] for image in report["images"]) == 4
    assert all(
        image["complete"] and image["natural_width"]
        for image in report["images"]
        if image["visible"]
    )
    assert page.evaluate("window.scrollY") == 0


@pytest.mark.parametrize(
    "markup",
    ["<span class='course-placeholder'></span>", "<span>Course artwork coming soon</span>"],
)
def test_checker_rejects_leftover_placeholder_class_or_text_even_with_an_image(page, markup):
    page.set_content(_catalogue_markup("light"))
    page.locator(".catalog-card-media").last.evaluate(
        "(el, markup) => el.insertAdjacentHTML('beforeend', markup)", markup
    )

    report = inspect_page(page, route="/courses", theme="light", width=1280)

    assert any("placeholder class or media text remains" in error for error in report["failures"])


def test_checker_still_requires_the_correct_single_visible_theme_image(page):
    page.set_content(_catalogue_markup("light"))
    page.locator(".catalog-card-media").last.locator(".doodle-dark").evaluate(
        "img => img.style.display = 'inline'"
    )

    report = inspect_page(page, route="/courses", theme="light", width=1280)

    assert any("wrong number of visible images" in error for error in report["failures"])


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_checker_requires_visible_family_art_on_mobile(page, theme):
    hidden = "dark" if theme == "light" else "light"
    image_url = "data:image/svg+xml," + quote(
        '<svg xmlns="http://www.w3.org/2000/svg" width="80" height="80"/>'
    )
    page.set_viewport_size({"width": 390, "height": 844})
    page.set_content(
        f"<style>.doodle-{hidden} {{ display:none }}</style>"
        '<div class="family-hero-art">'
        f'<img class="doodle-light" src="{image_url}" alt="" decoding="async">'
        f'<img class="doodle-dark" src="{image_url}" alt="" decoding="async">'
        "</div>"
    )

    report = inspect_page(page, route="/courses/ml-zoomcamp", theme=theme, width=390)

    assert report["failures"] == []
    assert sum(image["visible"] for image in report["images"]) == 1
