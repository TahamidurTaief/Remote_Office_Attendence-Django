from django.urls import path

from . import views


app_name = "audit"

urlpatterns = [
    path("trash/", views.TrashListView.as_view(), name="trash_list"),
    path("trash/<int:pk>/restore/", views.TrashRestoreView.as_view(), name="trash_restore"),
    path("trash/<int:pk>/permanent-delete/", views.TrashPermanentDeleteView.as_view(), name="trash_permanent_delete"),
    path("trash/bulk/", views.TrashBulkActionView.as_view(), name="trash_bulk"),
    path("activity/", views.ActivityListView.as_view(), name="activity_list"),
    path("activity/<int:pk>/detail/", views.AuditEventDetailView.as_view(), name="event_detail"),
]

