---
name: Project score notification
subject: "Scores available: {{ project_title }}"
footer_note: "You receive homework and project submission emails because you submitted; opt out on your profile settings page."
---

Hi {{ user_name }},

Your score for **{{ project_title }}** in {{ course_title }} is ready.

Your score: **{{ total_score }}**

- Project: {{ project_score }}
- Project learning in public: {{ project_learning_in_public_score }}
- Project FAQ: {{ project_faq_score }}
- Peer review: {{ peer_review_score }}
- Peer review learning in public: {{ peer_review_learning_in_public_score }}

{% if github_link %}Submission reviewed: [GitHub repository]({{ github_link }}){% if commit_id %} at commit `{{ commit_id }}`{% endif %}.

{% endif %}Next steps:

- [Review your project result]({{ scores_url }})
- [Open the project page]({{ project_url }})
- [Check the course leaderboard]({{ leaderboard_url }})
- [Open the course page]({{ course_url }})

— {{ site_name }}
