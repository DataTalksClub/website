"""Editorial composition for the Book of the Week Q&A on a book's page.

A book's ``archive`` is the week's ``#book-of-the-week`` Slack conversation as
the content repository records it: one thread per question, each with the
asker's name, the question text and the replies in order.  The page reads it as
an interview, so this module says, for every message, who wrote it and whether
that person is one of the book's authors -- the authors' replies are the
answers, everyone else's are follow-ups -- and splits each message on its
source line breaks into the paragraphs the writer typed.

An author is recognised by the written name on the reply matching the name of
one of the book's credited authors (the byline the page already shows), compared
without case or surrounding space.  Nothing is invented: a reply whose name
matches no author stays a plain follow-up, and a thread nobody answered still
shows its question.

Slack splits one answer into several messages when the writer presses enter
twice; consecutive replies by the same person read as one reply here, so the
author's chip heads an answer once rather than once per message.  A thread the
author opened themselves (a welcome post) is attributed to the author too.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BookMessage:
    name: str
    paragraphs: tuple[str, ...]
    #: The author's credit (name, profile address) when the writer is an author.
    author: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class BookThread:
    anchor: str
    asker: str
    question: tuple[str, ...]
    replies: tuple[BookMessage, ...]
    #: The author's credit when an author opened the thread (a welcome post).
    asker_author: Mapping[str, Any] | None = None

    @property
    def answered_by_author(self) -> bool:
        return any(reply.author is not None for reply in self.replies)


def _paragraphs(text: object) -> tuple[str, ...]:
    if not isinstance(text, str):
        return ()
    return tuple(line.strip() for line in text.splitlines() if line.strip())


def _key(name: object) -> str:
    return " ".join(str(name or "").split()).casefold()


def book_threads(
    archive: object, author_profiles: Sequence[Mapping[str, Any]]
) -> tuple[BookThread, ...]:
    """The week's threads, each reply attributed to an author or to a reader."""

    if not isinstance(archive, (list, tuple)):
        return ()
    authors = {_key(author.get("name")): author for author in author_profiles}
    authors.pop("", None)
    threads: list[BookThread] = []
    for thread in archive:
        if not isinstance(thread, Mapping):
            continue
        replies: list[BookMessage] = []
        for reply in thread.get("replies") or ():
            if not isinstance(reply, Mapping):
                continue
            name = str(reply.get("name") or "")
            paragraphs = _paragraphs(reply.get("text"))
            if replies and _key(replies[-1].name) == _key(name):
                previous = replies.pop()
                replies.append(
                    BookMessage(previous.name, previous.paragraphs + paragraphs, previous.author)
                )
                continue
            replies.append(BookMessage(name, paragraphs, authors.get(_key(name))))
        threads.append(
            BookThread(
                anchor=f"question-{len(threads) + 1}",
                asker=str(thread.get("name") or ""),
                question=_paragraphs(thread.get("text")),
                replies=tuple(replies),
                asker_author=authors.get(_key(thread.get("name"))),
            )
        )
    return tuple(threads)
