---
name: Event reminder
subject: "Reminder: {{ event_title }} starts {{ starts_in }}"
optional_context: [join_url]
---

Hi{% if user_name %} {{ user_name }}{% endif %},

**{{ event_title }}** starts {{ starts_in }}.

**When:** {{ event_when }}
{% if join_url %}
**Join on Zoom:** [{{ join_url }}]({{ join_url }})

This link is for you: please don't share it.
{% endif %}
[Add to calendar]({{ calendar_url }}) · [Event page]({{ event_url }})

See you there,

— {{ site_name }}
