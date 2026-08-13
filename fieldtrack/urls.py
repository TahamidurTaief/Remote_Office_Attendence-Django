from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.views.generic import TemplateView
from django.contrib.staticfiles.storage import staticfiles_storage
from django.http import JsonResponse, HttpResponse
import os
import environ
from apps.admin_panel import views as admin_panel_views

env = environ.Env()

def manifest_view(request):
    manifest = {
        "background_color": "#0f172a",
        "dir": "ltr",
        "display": "standalone",
        "name": "Signtech Track",
        "orientation": "any",
        "scope": "/",
        "short_name": "Signtech",
        "start_url": "/staff/home/",
        "theme_color": "#6366f1",
        "description": "Smart Attendance & Field Workforce Management",
        "id": "com.signtech.fieldtrack",
        "icons": [
            {
                "src": "/static/icons/icon-72.png",
                "sizes": "72x72",
                "type": "image/png"
            },
            {
                "src": "/static/icons/icon-96.png",
                "sizes": "96x96",
                "type": "image/png"
            },
            {
                "src": "/static/icons/icon-128.png",
                "sizes": "128x128",
                "type": "image/png"
            },
            {
                "src": "/static/icons/icon-144.png",
                "sizes": "144x144",
                "type": "image/png"
            },
            {
                "src": "/static/icons/icon-152.png",
                "sizes": "152x152",
                "type": "image/png"
            },
            {
                "src": "/static/icons/icon-192.png",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any maskable"
            },
            {
                "src": "/static/icons/icon-384.png",
                "sizes": "384x384",
                "type": "image/png"
            },
            {
                "src": "/static/icons/icon-512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any maskable"
            }
        ]
    }
    return JsonResponse(manifest)

def sw_view(request):
    sw_path = os.path.join(settings.BASE_DIR, 'static', 'js', 'sw.js')
    try:
        with open(sw_path, 'r') as f:
            content = f.read()
        response = HttpResponse(content, content_type='application/javascript')
    except FileNotFoundError:
        response = HttpResponse('// Service worker', content_type='application/javascript')
    # SW must never be cached by the browser — only the SW runtime manages its own updates.
    # Without no-store, Chrome/WebView may hold the old worker for up to 24 h.
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    response['Service-Worker-Allowed'] = '/'
    return response

def assetlinks_view(request):
    """
    Digital Asset Links for TWA verification.
    REQUIRED for the Android app to open full-screen (no address bar).
    Replace the fingerprint below with the real SHA-256 cert fingerprint:
        keytool -list -v -keystore android.keystore -alias android
    Copy the "SHA256:" value (with colons) into sha256_cert_fingerprints below.
    """
    data = [{
        "relation": ["delegate_permission/common.handle_all_urls"],
        "target": {
            "namespace": "android_app",
            "package_name": "bd.com.athome.attendence.twa",
            "sha256_cert_fingerprints": [
                env('TWA_SHA256_FINGERPRINT', default='REPLACE_WITH_REAL_SHA256_FINGERPRINT')
            ]
        }
    }]
    return JsonResponse(data, safe=False)

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('apps.accounts.urls')),
    path('branches/', include('apps.branches.urls')),
    path('employees/', include('apps.employees.urls')),
    path('attendance/', include('apps.attendance.urls')),
    path('staff/', include('apps.staff.urls')),
    path('admin-panel/', include('apps.admin_panel.urls')),
    path('dashboard/', admin_panel_views.RoleBasedDashboardView.as_view(), name='dashboard'),
    path('notifications/', include('apps.notifications.urls')),
    path('backups/', include('apps.backups.urls')),
    path('leave/', include('apps.leave.urls')),
    path('projects/', include('apps.projects.urls')),
    path('schedule/', include('apps.schedule.urls')),
    path('expense/', include('apps.expense.urls')),
    path('payroll/', include('apps.payroll.urls')),
    
    # PWA URLs served from root
    path('manifest.json', manifest_view, name='manifest'),
    path('sw.js', sw_view, name='sw'),

    # Digital Asset Links — required for TWA full-screen verification
    path('.well-known/assetlinks.json', assetlinks_view, name='assetlinks'),
]

from django.conf.urls.static import static

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    urlpatterns += [path('__reload__/', include('django_browser_reload.urls'))]


