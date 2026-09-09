---
name: Deadline reminder
subject: "{{ course_title }}: deadline on {{ deadline }}"
footer_note: "You receive deadline reminders for courses you are enrolled in."
---

Hi {{ user_name }},

A friendly reminder that **{{ course_title }}** has something due on {{ deadline }}.

Open the course page to see what is due and submit your work:

[Go to the course]({{ course_url }})

If you have already submitted, you can ignore this note.

— {{ site_name }}
