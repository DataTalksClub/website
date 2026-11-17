#!/usr/bin/env python3
"""Convert the retired Astro Academy source into DataTalksClub/content files.

One-time migration, never a runtime or recurring step.  Academy used to be a
separate Astro site (``DataTalksClub/academy``).  Its copy now lives in the
editorial content repository as ordinary synced content -- ``academy/*.md``
pages with structured frontmatter, ``academy/shared.yaml`` for the copy the
pages share, and ``images/academy/`` for the artwork -- and reaches the
database through ``manage.py sync_content --source dtc-content`` and the
``academy_page`` parser (``content/sync_parsers/academy.py``).

This script is the import boundary for the legacy source.  It reads a reviewed
source tree (``src/data``, ``src/pages``, ``src/components``, ``src/assets``,
``public``) and writes the exported tree to a directory outside this
repository, by default ``~/prod/academy-django/content-export``.  Nothing it
writes is read by the website; the owner commits the exported tree to the
content repository.

Copy is preserved verbatim:

* ``src/data/*.ts`` is evaluated with Node (type stripping, no build) and its
  values are written as they are;
* copy that only exists inside ``.astro`` markup is named here once and every
  such literal is checked against the source file it comes from, so the export
  can only contain text the reviewed source actually carries;
* the legal pages' markup is converted to Markdown.

Base-relative links in the source (``/privacy/``) are written as the site
paths the build produced (``/academy/privacy/``).

    uv run --frozen python scripts/convert_academy_source.py \\
        --source-root ~/prod/academy-django/<commit>/src-tarball/tree \\
        --source-commit <full reviewed commit>
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = Path.home() / "prod" / "academy-django" / "content-export"
BASE = "/academy"
DATA_MODULES = ("shared", "tracks", "sessions", "faq", "bildungsgutschein")


class ConversionError(RuntimeError):
    """The source tree does not carry what the export needs."""


# --------------------------------------------------------------------------- source access


class Source:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._texts: dict[str, str] = {}

    def text(self, relative: str) -> str:
        if relative not in self._texts:
            path = self.root / relative
            if not path.is_file():
                raise ConversionError(f"missing source file: {relative}")
            self._texts[relative] = path.read_text(encoding="utf-8")
        return self._texts[relative]

    def literal(self, relative: str, value: str) -> str:
        """Return ``value`` after checking the source file carries it verbatim.

        Markup wraps long copy across lines, so runs of whitespace compare equal.
        """

        raw = self.text(relative)
        haystacks = (
            _squash(html.unescape(raw)),
            _squash(html.unescape(re.sub(r"<[^>]+>", "", raw))),
        )
        if not any(_squash(value) in haystack for haystack in haystacks):
            raise ConversionError(f"copy not found in {relative}: {value[:60]!r}")
        return value

    def data(self) -> dict[str, Any]:
        """Evaluate ``src/data/*.ts`` with Node's type stripping and return plain values."""

        with tempfile.TemporaryDirectory(prefix="academy-convert-") as scratch:
            folder = Path(scratch)
            for name in (*DATA_MODULES, "ics"):
                body = self.text(f"src/data/{name}.ts")
                # Node resolves ESM imports by exact filename.
                body = re.sub(r"from '\./(\w+)'", r"from './\1.ts'", body)
                (folder / f"{name}.ts").write_text(body, encoding="utf-8")
            program = """
const folder = process.argv[1];
const load = (name) => import('file://' + folder + '/' + name + '.ts');
const shared = await load('shared');
const tracks = await load('tracks');
const sessions = await load('sessions');
const faq = await load('faq');
const bildung = await load('bildungsgutschein');
const ics = await load('ics');
const out = {
  shared: Object.fromEntries(Object.entries(shared).filter(([, v]) => typeof v !== 'function')),
  tracks: tracks.tracks,
  sessions: Object.fromEntries(Object.entries(sessions).filter(([, v]) => typeof v !== 'function')),
  rsvpButton: sessions.form.success.rsvpButton({ dateLabel: '{date}' }),
  faq: Object.fromEntries(Object.entries(faq)),
  bildungsgutschein: bildung.bildungsgutschein,
  ics: ics.icsFor(sessions.sessions),
};
process.stdout.write(JSON.stringify(out));
"""
            completed = subprocess.run(
                ["node", "--input-type=module", "-e", program, str(folder)],
                check=True,
                capture_output=True,
                text=True,
            )
        return json.loads(completed.stdout)


def _squash(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _base_links(value: str) -> str:
    """Apply the build's base path to the source's base-relative Markdown links."""

    return re.sub(r"\]\((/[^)]*)\)", lambda m: f"]({BASE}{m.group(1)})", value)


def _assert_equal(label: str, actual: Any, expected: Any) -> None:
    if actual != expected:
        raise ConversionError(f"source changed shape: {label}")


# --------------------------------------------------------------------------- legal pages


class _LegalMarkdown(HTMLParser):
    """Turn the static legal markup (h1/h2/p/a/code/strong/address/br) into Markdown."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.blocks: list[str] = []
        self._buffer: list[str] = []
        self._stack: list[str] = []
        self._href = ""
        self._link_text: list[str] = []
        self._in_address = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"h1", "h2", "p"}:
            self._flush()
            self._stack.append(tag)
        elif tag == "a":
            self._href = dict(attrs).get("href") or ""
            self._link_text = []
            self._stack.append("a")
        elif tag == "code":
            self._emit("`")
        elif tag == "strong":
            self._emit("**")
        elif tag == "address":
            self._in_address = True
        elif tag == "br":
            self._emit("\\\n")
        else:
            raise ConversionError(f"unsupported legal markup: <{tag}>")

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._stack.pop()
            label = _squash("".join(self._link_text))
            self._emit(f"[{label}]({self._href})")
        elif tag == "code":
            self._emit("`")
        elif tag == "strong":
            self._emit("**")
        elif tag == "address":
            self._in_address = False
        elif tag in {"h1", "h2", "p"}:
            text = self._text()
            if tag == "h1":
                self.title = text
            elif tag == "h2":
                self.blocks.append(f"## {text}")
            elif text:
                self.blocks.append(text)
            self._buffer = []
            self._stack.pop()

    def handle_data(self, data: str) -> None:
        if self._stack and self._stack[-1] == "a":
            self._link_text.append(data)
        else:
            self._emit(data)

    def _emit(self, value: str) -> None:
        self._buffer.append(value)

    def _text(self) -> str:
        raw = "".join(self._buffer)
        lines = [_squash(line) for line in raw.split("\\\n")]
        return "\\\n".join(line for line in lines if line)

    def _flush(self) -> None:
        if self._buffer and _squash("".join(self._buffer)):
            self.blocks.append(self._text())
        self._buffer = []


def legal_markdown(source: Source, relative: str) -> tuple[str, str]:
    text = source.text(relative)
    match = re.search(r"<main\b[^>]*>(.*?)</main>", text, flags=re.S)
    if match is None:
        raise ConversionError(f"no main element: {relative}")
    markup = match.group(1).replace("{' '}", " ")
    if "{" in markup:
        raise ConversionError(f"legal markup carries an expression: {relative}")
    parser = _LegalMarkdown()
    parser.feed(markup)
    parser.close()
    if not parser.title or not parser.blocks:
        raise ConversionError(f"legal page is empty: {relative}")
    return parser.title, "\n\n".join(parser.blocks) + "\n"


def base_props(source: Source, relative: str, prop: str) -> str:
    match = re.search(rf'\b{prop}="([^"]+)"', source.text(relative))
    if match is None:
        raise ConversionError(f"missing {prop} in {relative}")
    return match.group(1)


# --------------------------------------------------------------------------- export


def build_export(source: Source) -> dict[str, Any]:
    data = source.data()
    shared = data["shared"]
    sessions_module = data["sessions"]
    lit = source.literal
    contact = shared["contactEmail"]
    mail_link = f"[{contact}](mailto:{contact})"

    signup_form = "src/components/SignupForm.astro"
    sessions_form = "src/components/SessionsForm.astro"
    materials = "src/pages/sessions/materials.astro"
    confirm = "src/pages/confirm.astro"
    footer = "src/components/Footer.astro"
    signup_script = "src/scripts/signup.ts"

    survey = re.search(r"const SURVEY =\s*'([^']+)'", source.text(signup_script))
    if survey is None:
        raise ConversionError("survey address not found")

    chip_groups = []
    for key, question, options in (
        (
            "location",
            "Where are you based?",
            (
                ("berlin", "Berlin"),
                ("germany", "Elsewhere in Germany"),
                ("outside-germany", "Outside Germany"),
            ),
        ),
        (
            "german_level",
            "German level",
            (
                ("a2-or-below", "A2 or below"),
                ("b1", "B1"),
                ("b2", "B2"),
                ("c1-plus", "C1+"),
                ("native", "Native"),
            ),
        ),
        (
            "full_day_availability",
            "Can you study full days, Monday to Friday, 09:00 to 16:30, for 3 months?",
            (("yes", "Yes"), ("no", "No")),
        ),
    ):
        lit(signup_form, f'aria-label="{question}"')
        chip_groups.append(
            {
                "key": key,
                "question": question,
                "options": [
                    {
                        "value": value,
                        "label": lit(
                            signup_form,
                            f'data-group="{key}" data-value="{value}" '
                            f'aria-pressed="false">{label}<',
                        ).split(">", 1)[1][:-1],
                    }
                    for value, label in options
                ],
            }
        )

    payment_options = [
        lit(signup_form, f'<option value="{value}">{value}</option>').split(">")[1].split("<")[0]
        for value in ("Bildungsgutschein", "Self-funded", "Employer", "Not sure yet")
    ]

    footer_links = [
        {"label": lit(footer, f">{label}</a>") and label, "href": href}
        for label, href in (
            ("FAQ", f"{BASE}/faq/"),
            ("Bildungsgutschein", f"{BASE}/bildungsgutschein/"),
            ("Impressum", f"{BASE}/impressum/"),
            ("Privacy", f"{BASE}/privacy/"),
            ("DataTalks.Club", "https://datatalks.club"),
        )
    ]

    form = shared["form"]
    shared_yaml = {
        "brand": lit("src/layouts/Base.astro", "DataTalks.Club Academy"),
        "contact_email": contact,
        "seat_caption": shared["seatCaption"],
        "waitlist_button": form["button"],
        "facts": {
            "label": lit("src/components/FactsStrip.astro", "Key facts"),
            "items": [{"label": f["label"], "value": f["value"]} for f in shared["facts"]],
        },
        "hero": {
            "instructor_line": shared["heroInstructorLine"],
            "honesty": shared["heroHonesty"],
        },
        "instructor": {
            "heading": shared["instructor"]["heading"],
            "paragraphs": shared["instructor"]["paragraphs"],
            "photo": "images/academy/alexey.jpg",
            "photo_alt": lit("src/components/Instructor.astro", "Alexey Grigorev"),
            "proof": [
                {"label": p["label"], "href": p["href"]} for p in shared["instructor"]["proof"]
            ],
        },
        "project_heading": shared["project"]["heading"],
        "fit": {
            "for_title": shared["fit"]["forTitle"],
            "not_title": shared["fit"]["notTitle"],
            "for_shared": shared["fit"]["forShared"],
            "not": [_base_links(item) for item in shared["fit"]["not"]],
        },
        "syllabus": {
            "heading": shared["syllabus"]["heading"],
            "intro": shared["syllabus"]["intro"],
            "produce_label": lit("src/components/Syllabus.astro", "You produce:"),
        },
        "funding": {
            "heading": shared["funding"]["heading"],
            "paragraphs": shared["funding"]["paragraphs"],
            "link": {
                "label": shared["funding"]["linkLabel"],
                "href": shared["funding"]["linkHref"],
            },
        },
        "admission": {
            "heading": shared["admission"]["heading"],
            "body": shared["admission"]["body"],
            "steps_heading": shared["admission"]["stepsHeading"],
            "steps": [
                {"title": s["title"], "text": s["text"]} for s in shared["admission"]["steps"]
            ],
        },
        "faq": {
            "heading": shared["faqHeading"],
            # An item without an answer takes the program's own prerequisites.
            "items": [
                {"question": item["q"], **({"answer": _base_links(item["a"])} if item["a"] else {})}
                for item in shared["faq"]
            ],
        },
        "join": {"heading": shared["join"]["heading"], "text": shared["join"]["text"]},
        "footer": {
            "links": footer_links,
            "copyright": lit(footer, "© 2026 DataTalks.Club"),
            "other_track": lit(footer, "Looking at the other track?"),
        },
        "signup": {
            "program_label": lit(signup_form, "Program"),
            "name_label": form["nameLabel"],
            "name_placeholder": lit(signup_form, "Your name"),
            "payment_label": lit(signup_form, "How do you plan to pay?"),
            "payment_placeholder": lit(signup_form, "Select an option"),
            "payment_options": payment_options,
            "payment_error": lit(signup_form, "Please choose how you plan to pay."),
            "email_label": form["label"],
            "email_placeholder": lit(signup_form, "you@example.com"),
            "button": form["button"],
            "busy": lit(signup_script, "Joining…"),
            "email_error": form["error"],
            "failed": lit(
                signup_form,
                f"That didn't go through. Please try again, or email {contact} and I'll add you.",
            ).replace(contact, mail_link),
            "noscript": lit(
                signup_form, f"Signing up needs JavaScript. Or email {contact} and I'll add you."
            ).replace(contact, mail_link),
            "micro": form["micro"],
            "privacy_label": lit(signup_form, ">Privacy</a>")[1:-4],
            "success": {
                "eyebrow": lit(signup_form, "Check your inbox"),
                "title": lit(signup_form, "Confirm your email"),
                "text": lit(
                    signup_form,
                    "I sent a confirmation link to your email. "
                    "Click it to confirm your spot on the waitlist.",
                ),
                "questions_label": lit(signup_form, "Quick questions (optional)"),
                "questions": chip_groups,
                "answers_error": lit(signup_form, "That answer didn't save. Please tap it again."),
                "survey_text": lit(signup_form, "I use the survey answers to shape the syllabus."),
                "survey_button": lit(signup_form, "Take the survey"),
                "survey_url": survey.group(1),
            },
        },
    }

    files: dict[str, Any] = {"academy/shared.yaml": shared_yaml}

    # ---- home
    home = shared["home"]
    index = "src/pages/index.astro"
    files["academy/index.md"] = (
        {
            "layout": "home",
            "path": f"{BASE}/",
            "title": home["metaTitle"],
            "description": home["metaDescription"],
            "og_image": "images/academy/og/home.png",
            "sitemap": 1,
            "hero": {
                "eyebrow": home["eyebrow"],
                "headline": home["headline"],
                "lead": home["lead"],
                "links": [
                    {
                        "label": lit(index, "Free sessions: Hireable With AI"),
                        "href": f"{BASE}/sessions/",
                    },
                    {
                        "label": lit(index, "Bildungsgutschein (Deutsch)"),
                        "href": f"{BASE}/bildungsgutschein/",
                    },
                ],
            },
            "tracks": {
                "label": lit(index, "Choose a track"),
                "button_prefix": lit(index, "Read about"),
                # The panels show outcomes 1, 3 and 5 of each program ([0, 2, 4]).
                "outcomes": [1, 3, 5],
            },
            "funding": {
                "heading": shared["funding"]["heading"],
                "paragraphs": shared["funding"]["paragraphs"][:1],
            },
            "schema": {
                "organization": {
                    "name": "DataTalks.Club",
                    "url": "https://datatalks.club",
                    "same_as": [
                        lit(index, url)
                        for url in (
                            "https://x.com/DataTalksClub",
                            "https://www.linkedin.com/company/datatalks-club/",
                            "https://www.youtube.com/@DataTalksClub",
                            "https://github.com/DataTalksClub",
                            "https://datatalks.club/slack.html",
                        )
                    ],
                }
            },
        },
        "",
    )

    # ---- programs
    track_page = "src/pages/[track].astro"
    for position, track in enumerate(data["tracks"], start=2):
        _assert_equal("track emphasis", track["emphasis"], "")
        groups = []
        for group in track["syllabus"]:
            modules = []
            for module in group["modules"]:
                entry = {"title": module["title"], "produce": module["produce"]}
                if module.get("blurb"):
                    entry["description"] = module["blurb"]
                if module.get("topics"):
                    entry["topics"] = module["topics"]
                modules.append(entry)
            groups.append({"weeks": group["weeks"], "title": group["title"], "modules": modules})
        files[f"academy/{track['slug']}.md"] = (
            {
                "layout": "program",
                "path": f"{BASE}/{track['slug']}/",
                "title": track["metaTitle"],
                "description": track["metaDescription"],
                "og_image": f"images/academy/og/{track['slug']}.png",
                "sitemap": position,
                "program": {
                    "slug": track["slug"],
                    "name": track["name"],
                    "label": track["label"],
                    "survey_option": track["surveyOption"],
                },
                "hero": {
                    "eyebrow": lit(track_page, "Berlin or live online"),
                    "headline": track["headline"],
                    "lead": track["lead"],
                    "basis": track["basis"],
                },
                "outcomes": {"heading": track["outcomesTitle"], "items": track["outcomes"]},
                "project": track["project"],
                "fit_first": track["fitFirst"],
                "prerequisites": track["prerequisites"],
                "syllabus": groups,
                "schema": {
                    "start_date": lit(track_page, "2027-04"),
                    "course_modes": ["full-time", "Online", "Onsite"],
                    "location": "Berlin",
                    "country": "DE",
                    "instructor": lit(track_page, "Alexey Grigorev"),
                    "provider": "DataTalks.Club",
                    "provider_url": "https://datatalks.club",
                },
            },
            "",
        )

    # ---- sessions
    page = sessions_module["sessionsPage"]
    sform = sessions_module["form"]
    sessions_page = "src/pages/sessions/index.astro"
    variants = sessions_module["variants"]
    # Every utm_content variant carries the approved copy, so one hero serves all.
    _assert_equal("session hero variants", variants["b"], variants["a"])
    _assert_equal("session hero variants", variants["c"], variants["a"])
    _assert_equal("sessions fact line", page["factLine"], "")
    ics_source = source.text("src/data/ics.ts")
    stamp = re.search(r"'DTSTAMP:(\d{8}T\d{6}Z)'", ics_source)
    prodid = re.search(r"'PRODID:([^']+)'", ics_source)
    uid = re.search(r"UID:\$\{s\.id\}@([^`]+)`", ics_source)
    if not (stamp and prodid and uid):
        raise ConversionError("calendar identity not found")
    sessions = []
    for session in sessions_module["sessions"]:
        sessions.append(
            {
                "id": session["id"],
                "number": session["number"],
                "date_label": session["dateLabel"],
                "weekday_date": session["weekdayDate"],
                "starts": _iso_utc(session["startUtc"]),
                "ends": _iso_utc(session["endUtc"]),
                "title": session["title"],
                "hero_topic": session["heroTopic"],
                "short_label": session["shortLabel"],
                "description": session["description"],
                "zoom_url": session["zoomUrl"],
                "meetup_url": session["meetupUrl"],
            }
        )
    status_options = [{"value": o["value"], "label": o["label"]} for o in sform["statuses"]]
    files["academy/sessions.md"] = (
        {
            "layout": "sessions",
            "path": f"{BASE}/sessions/",
            "title": page["metaTitle"],
            "description": page["metaDescription"],
            "og_image": "images/academy/og/sessions.png",
            "sitemap": 4,
            "series": {
                "name": sessions_module["SERIES"],
                "calendar_slug": _calendar_slug(source),
                "hero_time": sessions_module["HERO_TIME"],
            },
            "hero": {
                "eyebrow": page["eyebrow"],
                "headline": variants["a"]["headline"],
                "lead": variants["a"]["subhead"],
                "button": page["button"],
                "under_button": page["underButton"],
                "speaker_line": page["speakerLine"],
                "speaker_photo": "images/academy/alexey.jpg",
                "speaker_alt": lit(sessions_page, "Alexey Grigorev"),
            },
            "sessions": sessions,
            "rsvp_published": sessions_module["rsvp_published"],
            "audience": {
                "heading": page["forHeading"],
                "intro": page["forIntro"],
                "items": page["forList"],
                "outro": page["forOutro"],
                "note": _base_links(page["forNote"]),
            },
            "schedule": {"heading": page["sessionsHeading"], "after": page["afterSessions"]},
            "host": {"heading": page["whoHeading"], "text": page["whoText"]},
            "numbers": {
                "heading": page["numbersHeading"],
                "items": [_base_links(n) for n in page["numbers"]],
            },
            "faq": {
                "heading": page["faqHeading"],
                "items": [{"question": f["q"], "answer": _base_links(f["a"])} for f in page["faq"]],
            },
            "final": {"heading": page["finalHeading"], "text": page["finalText"]},
            "form": {
                "email_label": sform["emailLabel"],
                "email_placeholder": lit(sessions_form, "you@example.com"),
                "email_error": lit(sessions_form, "Please enter a valid email address."),
                "name_label": sform["nameLabel"],
                "sessions_label": sform["sessionsLabel"],
                "sessions_error": lit(sessions_form, "Pick at least one session."),
                "track_label": sform["trackLabel"],
                "tracks": [{"value": o["value"], "label": o["label"]} for o in sform["tracks"]],
                "status_label": sform["statusLabel"],
                "statuses": status_options,
                "years_label": sform["yearsLabel"],
                "based_label": sform["basedLabel"],
                "based": [{"value": o["value"], "label": o["label"]} for o in sform["based"]],
                "bildung_label": sform["bildungLabel"],
                "bildung": [{"value": o["value"], "label": o["label"]} for o in sform["bildung"]],
                "free_text_label": sform["freeTextLabel"],
                "optin_label": sform["optinLabel"],
                "micro": sform["micro"],
                "button": sform["button"],
                "busy": lit(signup_script, "Sending…"),
                "failed": lit(
                    sessions_form,
                    f"That did not go through. Please try again, or write to {contact}.",
                ).replace(contact, mail_link),
                "noscript": lit(
                    sessions_form, f"Registration needs JavaScript. Or write to {contact}."
                ).replace(contact, mail_link),
                "privacy_label": lit(sessions_form, ">Privacy</a>")[1:-4],
                "success": {
                    "eyebrow": lit(sessions_form, "Check your inbox"),
                    "title": sform["success"]["title"],
                    "text": sform["success"]["text"],
                    "rsvp_heading": sform["success"]["rsvpHeading"],
                    "rsvp_text": sform["success"]["rsvpText"],
                    "rsvp_button": data["rsvpButton"],
                    "calendar_heading": sform["success"]["calendarHeading"],
                    "calendar_all": sform["success"]["calendarAll"],
                },
            },
            "calendar": {
                "prodid": prodid.group(1),
                "uid_domain": uid.group(1),
                "stamp": stamp.group(1),
                "zoom_label": lit("src/data/ics.ts", "Zoom link:"),
                "note": lit(
                    "src/data/ics.ts",
                    "Free online session with Alexey Grigorev. "
                    "Everyone who registers gets the recording.",
                ),
                "alarm_prefix": lit("src/data/ics.ts", "Starting in 15 minutes:"),
            },
            "footer_links": [
                {"label": lit(sessions_page, "Impressum"), "href": f"{BASE}/impressum/"},
                {"label": lit(sessions_page, "Privacy"), "href": f"{BASE}/privacy/"},
                {"label": contact, "href": f"mailto:{contact}"},
            ],
            "schema": {
                "location_name": lit(sessions_page, "Zoom"),
                "organizer": "DataTalks.Club",
                "organizer_url": "https://datatalks.club",
                "performer": lit(sessions_page, "Alexey Grigorev"),
            },
        },
        "",
    )

    # ---- session materials (QR)
    qr = sessions_module["qrForm"]
    files["academy/sessions-materials.md"] = (
        {
            "layout": "materials",
            "path": f"{BASE}/sessions/materials/",
            "title": qr["metaTitle"],
            "description": qr["metaDescription"],
            "og_image": "images/academy/og/sessions.png",
            "noindex": True,
            "heading": qr["heading"],
            "text": qr["text"],
            "button": qr["button"],
            "busy": lit(signup_script, "Sending…"),
            "success": qr["success"],
            "failed": lit(
                materials, f"That did not go through. Please try again, or write to {contact}."
            ).replace(contact, mail_link),
            "noscript": lit(
                materials, f"This form needs JavaScript. Or write to {contact}."
            ).replace(contact, mail_link),
            "micro": lit(
                materials,
                "I'll use your email to send the recording and the materials. "
                "You can unsubscribe any time.",
            ),
        },
        "",
    )

    # ---- confirmation
    files["academy/confirm.md"] = (
        {
            "layout": "confirm",
            "path": f"{BASE}/confirm/",
            "title": base_props(source, confirm, "title"),
            "description": base_props(source, confirm, "description"),
            "noindex": True,
            "eyebrow": lit(confirm, "Email Confirmation"),
            "heading": lit(confirm, "Confirming Your Email"),
            "message": lit(confirm, "Checking your confirmation link."),
            "back": lit(confirm, "Back to DataTalks.Club Academy"),
            "states": {
                "error_eyebrow": lit(confirm, "'Error'")[1:-1],
                "confirmed_eyebrow": lit(confirm, "'Confirmed'")[1:-1],
                "missing_title": lit(confirm, "Invalid Confirmation Link"),
                "missing_message": lit(
                    confirm,
                    "This confirmation link is missing a token. "
                    "Please submit the form again to get a new link.",
                ),
                "unconfigured_title": lit(confirm, "Confirmation Failed"),
                "unconfigured_message": lit(
                    confirm, "Confirmation service is not configured. Please try again later."
                ),
                "confirmed_title": lit(confirm, "Email Confirmed"),
                "confirmed_message": lit(confirm, "Your email is confirmed."),
                "invalid_title": lit(confirm, "Invalid Confirmation Link"),
                "invalid_message": lit(
                    confirm,
                    "This confirmation link is invalid or expired. Please submit the form again.",
                ),
                "failed_title": lit(confirm, "Confirmation Failed"),
                "failed_message": lit(
                    confirm, "Something went wrong while confirming your email. Please try again."
                ),
            },
        },
        "",
    )

    # ---- Bildungsgutschein (German)
    b = data["bildungsgutschein"]
    for target in ("/agentic-engineering/#signup-hero", "/ai-engineering/#signup-hero"):
        lit("src/pages/bildungsgutschein.astro", target)
    files["academy/bildungsgutschein.md"] = (
        {
            "layout": "guide",
            "path": f"{BASE}/bildungsgutschein/",
            "lang": "de",
            "hreflang": True,
            "title": b["metaTitle"],
            "description": b["metaDescription"],
            "sitemap": 5,
            "hero": {
                "eyebrow": b["eyebrow"],
                "headline": b["headline"],
                "lead": b["answerBlock"],
                "funding_line": b["fundingLine"],
            },
            "facts": {
                "label": lit("src/pages/bildungsgutschein.astro", "Fakten im Überblick"),
                "items": [{"label": f["label"], "value": f["value"]} for f in b["facts"]],
            },
            "fit": {
                "heading": b["fit"]["heading"],
                "for_title": b["fit"]["forTitle"],
                "for": b["fit"]["forItems"],
                "not_title": b["fit"]["notTitle"],
                "not": b["fit"]["notItems"],
            },
            "programs": {
                "heading": b["programs"]["heading"],
                "intro": b["programs"]["intro"],
                "items": [
                    {
                        "slug": p["slug"],
                        "name": p["name"],
                        "summary": p["summary"],
                        "basis": p["basis"],
                        "button": p["cta"],
                    }
                    for p in b["programs"]["items"]
                ],
            },
            "guide": {
                "heading": b["guide"]["heading"],
                "paragraphs": b["guide"]["paragraphs"],
                "steps_title": b["guide"]["stepsTitle"],
                "steps": b["guide"]["steps"],
                "links": [
                    {"label": link["label"], "href": link["href"]} for link in b["guide"]["links"]
                ],
            },
            "join": {
                "heading": b["cta"]["heading"],
                "text": b["cta"]["body"],
                "actions": [
                    {
                        "label": b["cta"]["primaryAgentic"],
                        "href": f"{BASE}/agentic-engineering/#signup-hero",
                    },
                    {"label": b["cta"]["primaryAi"], "href": f"{BASE}/ai-engineering/#signup-hero"},
                ],
                "sessions_label": b["cta"]["sessionsLabel"],
            },
        },
        "",
    )

    # ---- FAQ
    faq_page = data["faq"]["faqPage"]
    files["academy/faq.md"] = (
        {
            "layout": "faq",
            "path": f"{BASE}/faq/",
            "title": faq_page["metaTitle"],
            "description": faq_page["metaDescription"],
            "og_image": "images/academy/og/home.png",
            "sitemap": 6,
            "eyebrow": faq_page["eyebrow"],
            "heading": faq_page["heading"],
            "lead": faq_page["lead"],
            "button": lit("src/pages/faq.astro", "Join the Waitlist"),
            "list_label": lit("src/pages/faq.astro", "Questions and answers"),
            "items": [{"question": e["q"], "answer": e["a"]} for e in data["faq"]["faqEntries"]],
        },
        "",
    )

    # ---- legal
    for name, lang, position in (("privacy", "en", 7), ("impressum", "de", 8)):
        relative = f"src/pages/{name}.astro"
        heading, body = legal_markdown(source, relative)
        if name == "privacy":
            body = correct_privacy(body)
        files[f"academy/{name}.md"] = (
            {
                "layout": "legal",
                "path": f"{BASE}/{name}/",
                "lang": lang,
                "title": base_props(source, relative, "title"),
                "description": base_props(source, relative, "description"),
                "sitemap": position,
                "heading": heading,
            },
            body,
        )

    return {"files": files, "ics": data["ics"]}


#: The owner-approved corrections to the Astro privacy notice (issue #515).  The
#: notice described the retired static site: GitHub Pages hosting and the
#: Astro client's own page-view counter.  Academy now runs on the DataTalks.Club
#: platform, and its only page-view measurement is the site's consent-based
#: analytics.  Each correction replaces one exact source passage, so a source
#: that no longer carries it fails the export instead of being silently
#: rewritten; everything else in the notice stays verbatim.
PRIVACY_CORRECTIONS = (
    (
        "The site counts page views per page and campaign in aggregate (a daily number per "
        "page and campaign). These counts contain no personal data and no identifier for you "
        "or your device. The site sets no cookies and stores nothing on your device.",
        "Page views are measured only through the DataTalks.Club website's consent-based "
        "analytics: they are off unless you allow them, and you can change that choice at any "
        "time under Analytics preferences. See the "
        "[DataTalks.Club privacy policy](/privacy) for that measurement.",
    ),
    (
        "GitHub, Inc. (USA) hosts this static site through GitHub Pages. Both process your IP "
        "address and browser details in their server logs to deliver the pages and to keep the "
        "service secure.",
        "This site is hosted on the DataTalks.Club platform, which processes your IP address "
        "and browser details in its server logs to deliver the pages and to keep the service "
        "secure. Amazon Web Services does the same for the service that receives the form.",
    ),
    (
        "GitHub and Amazon are US companies or belong to one.",
        "Amazon is a US company or belongs to one.",
    ),
    ("13 November 2026.", "17 November 2026."),
)


def correct_privacy(body: str) -> str:
    for old, new in PRIVACY_CORRECTIONS:
        if body.count(old) != 1:
            raise ConversionError(f"privacy passage not found once: {old[:60]!r}")
        body = body.replace(old, new)
    return body


def _calendar_slug(source: Source) -> str:
    """The all-sessions calendar is ``src/pages/sessions/<slug>.ics.ts``."""

    slug = "hireable-with-ai"
    source.text(f"src/pages/sessions/{slug}.ics.ts")
    return slug


def _iso_utc(value: str) -> str:
    match = re.fullmatch(r"(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z", value)
    if match is None:
        raise ConversionError("session time is not a UTC stamp")
    y, mo, d, h, mi, s = match.groups()
    return f"{y}-{mo}-{d}T{h}:{mi}:{s}Z"


IMAGES = {
    "src/assets/alexey.jpg": "images/academy/alexey.jpg",
    "public/og/home.png": "images/academy/og/home.png",
    "public/og/agentic-engineering.png": "images/academy/og/agentic-engineering.png",
    "public/og/ai-engineering.png": "images/academy/og/ai-engineering.png",
    "public/og/sessions.png": "images/academy/og/sessions.png",
    "public/qr/session-1.png": "images/academy/qr/session-1.png",
    "public/qr/session-2.png": "images/academy/qr/session-2.png",
    "public/qr/session-3.png": "images/academy/qr/session-3.png",
    "public/qr/session-1.svg": "images/academy/qr/session-1.svg",
    "public/qr/session-2.svg": "images/academy/qr/session-2.svg",
    "public/qr/session-3.svg": "images/academy/qr/session-3.svg",
}


def _dump(value: Any) -> str:
    return yaml.safe_dump(value, allow_unicode=True, sort_keys=False, width=10_000)


def write_export(export: dict[str, Any], source: Source, output: Path, commit: str) -> list[str]:
    written = []
    if output.exists():
        shutil.rmtree(output / "academy", ignore_errors=True)
        shutil.rmtree(output / "images" / "academy", ignore_errors=True)
    for relative, value in export["files"].items():
        path = output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if relative.endswith(".yaml"):
            path.write_text(_dump(value), encoding="utf-8")
        else:
            metadata, body = value
            text = "---\n" + _dump(metadata) + "---\n"
            if body:
                text += "\n" + body
            path.write_text(text, encoding="utf-8")
        written.append(relative)
    for origin, target in IMAGES.items():
        path = output / target
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source.root / origin, path)
        written.append(target)
    (output / "academy-source.json").write_text(
        json.dumps({"repository": "DataTalksClub/academy", "commit": commit}, indent=2) + "\n",
        encoding="utf-8",
    )
    return sorted(written)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--build",
        type=Path,
        help="The reviewed build of the same commit; its calendars are checked byte for byte.",
    )
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[0-9a-f]{40}", args.source_commit):
        parser.error("--source-commit must be a full commit SHA")
    output = args.output.expanduser().resolve()
    if output.is_relative_to(PROJECT_ROOT):
        parser.error("the export belongs outside this repository")
    source = Source(args.source_root.expanduser().resolve())
    export = build_export(source)
    if args.build:
        built = (args.build / "sessions" / "hireable-with-ai.ics").read_bytes().decode("utf-8")
        if built != export["ics"]:
            raise ConversionError("the source calendar does not match the reviewed build")
    written = write_export(export, source, output, args.source_commit)
    print(json.dumps({"output": str(output), "files": written}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
