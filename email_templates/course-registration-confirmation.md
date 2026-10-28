---
name: Course registration confirmation
subject: "Confirm your registration for {{ course_title }}"
optional_context: [start_date, verify_email_prompt, suggested_courses]
---

Hi {{ user_name }},

Thanks for registering for **{{ course_title }}**! Please click the confirmation link to complete
your registration.
{% if requires_approval %}
Your application will then wait for review: acceptance is not automatic, and we will
email you once it is approved.
{% endif %}
{% if confirmation_url %}[Confirm registration]({{ confirmation_url }})

You MUST click this confirmation link to complete registration. The link expires after 7 days. If you did not register, ignore this email.

{% endif %}{% if start_date %}The course starts on {{ start_date }}. {% endif %}Everything you need lives on the
course page:

[Open the course]({{ course_url }})

{{ verify_email_prompt }}

{{ suggested_courses }}

See you there,

— {{ site_name }}
