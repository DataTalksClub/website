"""One vocabulary for the state of a piece of coursework.

Homework and projects are the same thing to a reader — a deadline, a
submission, a score — and a course page stacks them as two tables. Both read
the surfaces named here, so one word cannot wear two pills.

The four surfaces divide the states by what they ask of the reader:

``PAST``
    Over, with nothing gained and nothing left to do — closed, never
    submitted, failed.
``YOUR_MOVE``
    Open to you right now — an open deadline, peer reviews you still owe.
``DONE``
    You did your part and are waiting on the course — submitted, reviews
    delivered.
``RESULT``
    A number came back — scored, passed.
"""

from __future__ import annotations

PAST = "status-pill-wait"
YOUR_MOVE = "status-pill-open"
DONE = "status-pill-live"
RESULT = "status-pill-mint"
