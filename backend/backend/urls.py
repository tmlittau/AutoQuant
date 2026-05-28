"""URL config: Django admin at /admin/, NinjaAPI at /api/, CSRF primer at /api/csrf."""

from django.contrib import admin
from django.http import JsonResponse
from django.urls import path
from django.views.decorators.csrf import ensure_csrf_cookie

from portfolio_app.api import api


@ensure_csrf_cookie
def csrf_view(request):
    """Lightweight view the SPA calls on boot to seed the ``csrftoken`` cookie.
    Returns ``{ok: true}``; the actual value of the side-effect is the cookie."""
    return JsonResponse({"ok": True})


urlpatterns = [
    path("admin/", admin.site.urls),
    # ``api/csrf`` MUST come before ``api/`` so ninja's catch-all doesn't shadow it.
    path("api/csrf", csrf_view),
    path("api/", api.urls),
]
