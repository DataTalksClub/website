---
name: Homework score notification
subject: "Scores available: {{ homework_title }}"
footer_note: "If you don't want to receive homework/project submission and score emails, turn off homework and project submission emails in your profile: {{ profile_url }}"
---

Hi {{ user_name }},

Your score for **{{ homework_title }}** in {{ course_title }} is ready.

Your score: **{{ total_score }}**

- Questions: {{ questions_score }}
- Learning in public: {{ learning_in_public_score }}
- FAQ contribution: {{ faq_score }}

Next steps:

- [Review your homework score]({{ scores_url }})
- [Check the course leaderboard]({{ leaderboard_url }})
- [Open the course page]({{ course_url }})

— {{ site_name }}
