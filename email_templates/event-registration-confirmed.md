---
name: Event registration confirmed
subject: "You're registered: {{ event_title }}"
optional_context: [join_url]
---

Hi {{ user_name }},

You're registered for **{{ event_title }}**.

**When:** {{ event_when }}
{% if join_url %}
**Join on Zoom:** [{{ join_url }}]({{ join_url }})

This link is for you: please don't share it. We will also send it in the
reminder before the event.
{% endif %}
[Add to calendar]({{ calendar_url }}) · [Event page]({{ event_url }})

See you there,

— {{ site_name }}
