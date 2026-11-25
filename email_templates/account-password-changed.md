---
name: Account password changed
subject: "Your DataTalks.Club password was {{ password_action }}"
---

Hi{% if user_name %} {{ user_name }}{% endif %},

The password for your DataTalks.Club account was just {{ password_action }}.

If that was you, there is nothing to do.

If it was not, [reset your password]({{ reset_url }}) now and sign in with
Google, GitHub or Slack to check your account.

— DataTalks.Club
