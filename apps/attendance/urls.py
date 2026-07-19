from django.urls import path
from . import views

app_name = 'attendance'

urlpatterns = [
    path('check-in/',       views.check_in,           name='check_in'),
    path('check-out/',      views.check_out,          name='check_out'),
    path('status/',         views.attendance_status,  name='status'),
    path('field-visit/',    views.field_visit_submit, name='field_visit_submit'),
    path('location-sync/',  views.location_sync,      name='location_sync'),
    path('live-locations/', views.live_locations,     name='live_locations'),
    path('tracking-config/', views.get_tracking_config, name='tracking_config'),
    path('save-location/', views.save_location, name='save_location'),
    path('save-location-mandatory/', views.save_mandatory_location, name='save_mandatory_location'),
]
