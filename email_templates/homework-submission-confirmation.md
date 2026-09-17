---
name: Homework submission confirmation
subject: "Homework submission saved: {{ homework_title }}"
footer_note: "You are receiving this because homework and project submission emails are enabled in your profile."
---

Hi {{ user_name }},

{{ intro_text }}

{{ update_text }} [{{ update_link_text }}]({{ update_url }})

{% if submitted_fields_text %}Submitted details:

{{ submitted_fields_text }}

{% endif %}{% if submitted_answers_text %}Submitted answers:

{{ submitted_answers_text }}

{% endif %}— {{ site_name }}
