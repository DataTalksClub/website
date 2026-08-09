from django.contrib import admin

from .models import CustomUser


class CustomUserAdmin(admin.ModelAdmin):
    search_fields = ["email"]
    change_form_template = 'loginas/change_form.html'


admin.site.register(CustomUser, CustomUserAdmin)
