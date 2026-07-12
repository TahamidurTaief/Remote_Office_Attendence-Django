from django.urls import path
from . import views

app_name = 'leave'

urlpatterns = [
    # Admin URLs
    path('admin/', views.AdminLeaveDashboardView.as_view(), name='admin_dashboard'),
    path('admin/requests/<int:pk>/approve/', views.ApproveLeaveRequestView.as_view(), name='approve_request'),
    path('admin/requests/<int:pk>/reject/', views.RejectLeaveRequestView.as_view(), name='reject_request'),
    path('admin/balances/', views.AdminEmployeeBalancesView.as_view(), name='admin_balances'),
    path('admin/balances/<int:employee_id>/', views.AdminEmployeeBalanceDetailView.as_view(), name='admin_balance_detail'),
    
    # Admin Leave Type Management
    path('admin/types/', views.AdminLeaveTypesView.as_view(), name='admin_leave_types'),
    path('admin/types/add/', views.AdminLeaveTypeCreateView.as_view(), name='admin_leave_type_add'),
    path('admin/types/<int:pk>/edit/', views.AdminLeaveTypeUpdateView.as_view(), name='admin_leave_type_edit'),

    # Staff URLs
    path('staff/', views.StaffLeaveDashboardView.as_view(), name='staff_dashboard'),
    path('staff/request/', views.StaffLeaveRequestCreateView.as_view(), name='staff_request_create'),
    path('admin/requests/<int:pk>/reschedule/', views.RescheduleLeaveRequestView.as_view(), name='reschedule_request'),
]
