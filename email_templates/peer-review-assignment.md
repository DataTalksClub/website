---
name: Peer review assignment
subject: "Peer review is open: {{ project_title }}"
footer_note: "You receive homework and project submission emails because you submitted; opt out on your profile settings page."
---

Hi {{ user_name }},

{{ intro_text }}

{% if submitted_at %}You submitted your project on {{ submitted_at }}.

{% endif %}**Deadline:** {{ deadline_summary }}

## Your {{ number_of_peers_to_evaluate }} projects to review

{% for review in assigned_reviews %}1. [Review assignment #{{ review.review_id }}]({{ review.eval_url }}){% if review.submission_github_link %} — [repository]({{ review.submission_github_link }}){% endif %}

{% endfor %}
[Open all your peer reviews]({{ evaluations_url }})

— {{ site_name }}
