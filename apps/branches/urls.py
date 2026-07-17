from django.urls import path
from . import views

app_name = 'branches'

urlpatterns = [
    path('', views.BranchListView.as_view(), name='branch_list'),
    path('add/', views.BranchCreateView.as_view(), name='branch_add'),
    path('<int:pk>/edit/', views.BranchEditView.as_view(), name='branch_edit'),
    path('<int:pk>/delete/', views.BranchDeleteView.as_view(), name='branch_delete'),
    
    # Holiday URLs
    path('holidays/', views.HolidayListView.as_view(), name='holiday_list'),
    path('holidays/add/', views.HolidayCreateView.as_view(), name='holiday_add'),
    path('holidays/<int:pk>/edit/', views.HolidayEditView.as_view(), name='holiday_edit'),
    path('holidays/<int:pk>/delete/', views.HolidayDeleteView.as_view(), name='holiday_delete'),
]
