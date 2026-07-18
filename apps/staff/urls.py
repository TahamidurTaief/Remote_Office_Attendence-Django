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
    
    # Manager Projects
    path('my-projects/', views.my_projects, name='my_projects'),
    path('my-projects/<int:project_id>/', views.my_project_detail, name='my_project_detail'),
    path('my-projects/<int:project_id>/tasks/add/', views.my_project_add_task, name='my_project_add_task'),
]
