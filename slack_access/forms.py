from django import forms

from .models import Application

VOUCH_WARNING = (
    "By vouching, you take responsibility with your own reputation for inviting this person. "
    "If they turn out to be a spammer, you will be banned too. "
    "Only vouch for people you really know."
)


class ApplicationForm(forms.ModelForm):
    class Meta:
        model = Application
        fields = ["who", "work", "what"]
        labels = {
            "who": "Who are you?",
            "work": "Where do you work?",
            "what": "What do you do?",
        }
        help_texts = {"work": "If you are studying or not currently working, say so here."}
        widgets = {
            "who": forms.TextInput(attrs={"class": "field-input"}),
            "work": forms.TextInput(attrs={"class": "field-input"}),
            "what": forms.Textarea(attrs={"class": "field-input", "rows": 4}),
        }


class VouchForm(forms.Form):
    acknowledge = forms.BooleanField(
        label="I know this person and accept this responsibility.",
        help_text="Confirm only after reading the responsibility warning.",
        widget=forms.CheckboxInput(
            attrs={
                "class": "form-check-input",
                "aria-describedby": "slack-responsibility-warning",
            }
        ),
    )
