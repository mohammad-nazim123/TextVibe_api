from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.core.validators import RegexValidator
from django.db import models

from .managers import UserManager

phone_validator = RegexValidator(
    regex=r"^\+?[1-9]\d{7,14}$",
    message="Enter a valid phone number in international format, e.g. +919876543210.",
)


class User(AbstractBaseUser, PermissionsMixin):
    """A user identified by their verified email. No password for OTP-only accounts."""

    email = models.EmailField(max_length=254, unique=True, null=True, blank=True, db_index=True)
    google_id = models.CharField(max_length=200, blank=True, default="")
    phone_number = models.CharField(
        max_length=20,
        unique=True,
        null=True,
        blank=True,
        db_index=True,
        validators=[phone_validator],
    )
    name = models.CharField(max_length=80, blank=True, default="")
    avatar = models.ImageField(upload_to="avatars/", blank=True, null=True)
    tokens = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    is_verified = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        ordering = ["-date_joined"]

    def __str__(self):
        return self.email or self.phone_number or f"user:{self.pk}"


class Post(models.Model):
    """A composed post saved via the Send button."""

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="posts"
    )
    text = models.TextField()
    image = models.ImageField(upload_to="posts/", blank=True, null=True)
    canvas_image = models.ImageField(upload_to="posts/canvas/", blank=True, null=True)
    text_image = models.ImageField(upload_to="posts/text/", blank=True, null=True)
    text_canvas_width = models.PositiveIntegerField(default=0)
    text_canvas_height = models.PositiveIntegerField(default=0)
    background_color = models.CharField(max_length=9, blank=True, default="")
    background_texture = models.JSONField(null=True, blank=True)
    # Per-word styling so the billboard website can reproduce the message
    # exactly: a list of {text, color, fontFamily, fontSize} runs.
    style_runs = models.JSONField(default=list, blank=True)
    # Reserved for a border design the user adds later (null until then).
    border = models.JSONField(null=True, blank=True)
    # Asset identifiers sent by the app so the billboard can load the same PNGs.
    frame_id = models.CharField(max_length=64, blank=True, null=True)
    background_id = models.CharField(max_length=64, blank=True, null=True)
    # How long the billboard shows this message (seconds, clamped 3..300).
    duration_seconds = models.PositiveIntegerField(default=5)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Post #{self.pk} by {self.user}"

    def calculate_token_cost(self):
        """Calculate the token cost for this post based on its properties."""
        cost = 0

        # 1 token per non-whitespace character
        cost += len(self.text.replace(" ", "").replace("\n", "").replace("\t", ""))

        # Background texture or color cost
        if self.background_texture:
            texture = self.background_texture.get("texture", "")
            cost += 30 if texture.startswith("legendary_") else 20
        elif self.background_color and self.background_color.lower() != "#ffffff":
            cost += 4

        # Border cost
        if self.border:
            style = self.border.get("style", "")
            if style == "texture":
                texture = self.border.get("texture", "")
                cost += 30 if texture.startswith("legendary_") else 20
            else:
                cost += 4

        # Duration cost
        duration_cost_map = {3: 5, 5: 7, 10: 12, 30: 30}
        cost += duration_cost_map.get(self.duration_seconds, 7)

        # Image cost
        if self.image:
            cost += 25

        return cost
