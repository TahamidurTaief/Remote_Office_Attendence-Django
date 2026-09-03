from django.urls import path
from . import views

app_name = 'schedule'

urlpatterns = [
    path('', views.CalendarMonthView.as_name() if hasattr(views.CalendarMonthView, 'as_name') else views.CalendarMonthView.as_view(), name='month_view'),
    path('shifts/', views.ShiftScheduleView.as_view(), name='shift_schedule'),
    path('create/', views.ScheduleEventCreateView.as_view(), name='create'),
    path('event/<int:pk>/edit/', views.ScheduleEventUpdateView.as_view(), name='edit'),
    path('event/<int:pk>/delete/', views.ScheduleEventDeleteView.as_view(), name='delete'),
]
