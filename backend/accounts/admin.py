from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    fieldsets = BaseUserAdmin.fieldsets + (("Perfil", {"fields": ("display_name", "avatar_url")}),)
    list_display = ("username", "display_name", "email", "is_active", "is_staff")
