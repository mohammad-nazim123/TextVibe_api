from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import Post, SupportMessage, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ("-date_joined",)
    list_display = ("email", "phone_number", "name", "tokens", "subscription_tier", "is_verified", "is_staff", "is_active", "date_joined")
    list_filter = ("is_verified", "is_staff", "is_active", "subscription_tier")
    search_fields = ("email", "phone_number", "name")
    readonly_fields = ("date_joined", "last_login")
    filter_horizontal = ("groups", "user_permissions")
    fieldsets = (
        (None, {"fields": ("email", "phone_number", "password")}),
        ("Profile", {"fields": ("name", "avatar", "tokens")}),
        (
            "Subscription",
            {
                "fields": (
                    "subscription_tier",
                    "subscription_purchased_at",
                    "subscription_expires_at",
                )
            },
        ),
        ("Status", {"fields": ("is_active", "is_verified")}),
        ("Permissions", {"fields": ("is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "phone_number", "password1", "password2"),
            },
        ),
    )


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "short_text", "duration_seconds", "created_at"]
    list_filter = ["duration_seconds", "created_at"]
    search_fields = ["user__email", "user__phone_number", "text"]
    readonly_fields = ["created_at", "style_runs", "background_texture", "border"]
    raw_id_fields = ["user"]
    ordering = ["-created_at"]

    @admin.display(description="Text")
    def short_text(self, obj):
        return obj.text[:60] + "…" if len(obj.text) > 60 else obj.text


@admin.register(SupportMessage)
class SupportMessageAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "short_message", "created_at"]
    list_filter = ["created_at"]
    search_fields = ["user__email", "user__phone_number", "message"]
    readonly_fields = ["created_at"]
    raw_id_fields = ["user"]
    ordering = ["-created_at"]

    @admin.display(description="Message")
    def short_message(self, obj):
        return obj.message[:80] + "…" if len(obj.message) > 80 else obj.message
