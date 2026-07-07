from django.urls import path
from . import views

app_name = 'staff'

urlpatterns = [
    path('home/', views.home, name='home'),
    path('attendance-card/', views.attendance_card, name='attendance_card'),
    path('check-in/', views.check_in_page, name='check_in'),
    path('field-visit/', views.field_visit_page, name='field_visit'),
    path('attendance/', views.attendance_history, name='attendance'),
    path('profile/', views.profile, name='profile'),
    path('change-password/', views.staff_change_password, name='change_password'),
]
