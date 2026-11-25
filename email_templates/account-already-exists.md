---
name: Account already exists
subject: "You already have a DataTalks.Club account"
---

Hi{% if user_name %} {{ user_name }}{% endif %},

Someone tried to create a DataTalks.Club account with this email address, but
you already have one.

[Sign in]({{ login_url }}) with Google, GitHub, Slack or your password. If you
do not have a password yet or forgot it, [reset your password]({{ reset_url }}).

If this was not you, ignore this email: nothing has changed.

— DataTalks.Club
