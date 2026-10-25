---
name: Course registration confirmation
subject: "Confirm your registration for {{ course_title }}"
---

Hi {{ user_name }},

Thanks for registering for **{{ course_title }}**! Please click the confirmation link to complete
your registration.

{% if confirmation_url %}[Confirm registration]({{ confirmation_url }})

You MUST click this confirmation link to complete registration. The link expires after 7 days. If you did not register, ignore this email.

{% endif %}The course starts on {{ start_date }}. Everything you need lives on the
course page:

[Open the course]({{ course_url }})

{{ verify_email_prompt }}

See you there,

— {{ site_name }}
