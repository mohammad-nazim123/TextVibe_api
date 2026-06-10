from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path

from accounts.views import BillboardView


def health(_request):
    return JsonResponse({"status": "ok", "service": "textvibe-api"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("", health, name="health"),
    path("api/auth/", include("accounts.urls")),
    path("api/payments/", include("payments.urls")),
    # Public billboard feed for the website (no auth).
    path("api/billboard/", BillboardView.as_view(), name="billboard"),
]

# Serve uploaded media (avatars) during development.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
