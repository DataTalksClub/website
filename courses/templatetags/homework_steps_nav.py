"""Navigation items for the homework stepper: main-column chips and rail sub-steps."""

from django import template

register = template.Library()


def _has_answer(answer):
    if isinstance(answer, str):
        return bool(answer.strip())
    return bool(answer)


@register.filter
def homework_nav_steps(stepper):
    """Return one ``{key, title, url, current, done}`` item per stepper step.

    A question step is done exactly when the Review list shows an answer for
    it: the saved draft while the homework is open, the accepted submission
    once it is closed or scored. Review rows and nav steps are built from the
    same step URLs, so they match by URL. Introduction and Review & submit are
    never done. Reads only what the stepper render already holds.
    """

    if not stepper:
        return []
    answered_urls = {
        row.get("url")
        for row in stepper.get("review_display_rows") or ()
        if _has_answer(row.get("answer"))
    }
    assignment = stepper.get("assignment")
    question_keys = [question.key for question in assignment.questions] if assignment else []
    keys = ["intro", *question_keys, "review"]
    nav_steps = stepper.get("nav_steps") or ()
    return [
        {
            "key": keys[index] if index < len(keys) else "",
            "title": title,
            "url": url,
            "current": bool(current),
            "done": url in answered_urls,
        }
        for index, (title, url, current) in enumerate(nav_steps)
    ]


@register.filter
def homework_state_pill(state):
    """The DTC status-pill tone for a shared ``LearnerHomeworkState``."""

    from courses.views.homework_states import homework_state_pill as pill

    return pill(state)
