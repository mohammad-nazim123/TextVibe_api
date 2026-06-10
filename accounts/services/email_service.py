from django.conf import settings
from django.core.mail import send_mail


def send_otp_email(email: str, otp: str) -> None:
    ttl_minutes = getattr(settings, "OTP_TTL_SECONDS", 300) // 60
    send_mail(
        subject="Your TextVibe verification code",
        message=(
            f"Your TextVibe verification code is {otp}. "
            f"It expires in {ttl_minutes} minute(s). Do not share it with anyone."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
        fail_silently=False,
    )
