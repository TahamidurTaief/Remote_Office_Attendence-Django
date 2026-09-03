from django.urls import path
from . import views
from . import roles_views

app_name = 'admin_panel'

urlpatterns = [
    path('dashboard/', views.RoleBasedDashboardView.as_view(), name='dashboard'),
    path('dashboard/partial/', views.DashboardPartialView.as_view(), name='dashboard_partial'),
    path('dashboard/employee-kpis/', views.EmployeeKPIWidgetView.as_view(), name='employee_kpis'),
    path('global-search/', views.GlobalSearchView.as_view(), name='global_search'),
    path('attendance/', views.AdminAttendanceListView.as_view(), name='attendance_list'),
    path('attendance/create/', views.AdminAttendanceCreateView.as_view(), name='attendance_create'),
    path('attendance/<int:pk>/edit/', views.AdminAttendanceUpdateView.as_view(), name='attendance_edit'),
    path('attendance/<int:pk>/delete/', views.AdminAttendanceDeleteView.as_view(), name='attendance_delete'),
    path('attendance/<int:pk>/detail/', views.AttendanceDetailView.as_view(), name='attendance_detail'),
    path('attendance/<int:pk>/locations/', views.AttendanceLocationsView.as_view(), name='attendance_locations'),
    path('attendance/export/', views.ExportAttendanceCSVView.as_view(), name='attendance_export'),
    path('attendance/manual-entry/', views.ManualEntryView.as_view(), name='manual_entry'),

    # Leave CRUD
    path('leave/requests/create/', views.AdminLeaveRequestCreateView.as_view(), name='leave_request_create'),
    path('leave/requests/<int:pk>/edit/', views.AdminLeaveRequestUpdateView.as_view(), name='leave_request_edit'),
    path('leave/requests/<int:pk>/delete/', views.AdminLeaveRequestDeleteView.as_view(), name='leave_request_delete'),
    path('leave/balances/<int:pk>/edit/', views.AdminLeaveBalanceUpdateView.as_view(), name='leave_balance_edit'),

    # Reports
    path('reports/', views.ReportsMainView.as_view(), name='reports_main'),
    path('reports/attendance/', views.DailyReportView.as_view(), name='reports_attendance'),
    path('reports/daily/', views.DailyReportView.as_view(), name='reports_daily'),
    path('reports/monthly/', views.MonthlyReportView.as_view(), name='reports_monthly'),
    path('reports/employee/<int:pk>/', views.EmployeeReportView.as_view(), name='reports_employee'),
    path('reports/employee/<int:pk>/month/<int:year>/<int:month>/', views.EmployeeReportView.as_view(), name='reports_employee_month'),
    path('reports/employee/<int:pk>/day/<str:date_str>/', views.EmployeeDayDetailView.as_view(), name='reports_employee_day'),
    path('reports/export/', views.export_attendance, name='export_attendance'),
    path('reports/export/csv/', views.ExportReportCSVView.as_view(), name='reports_export_csv'),
    path('reports/export/pdf/', views.ExportReportPDFView.as_view(), name='reports_export_pdf'),
    path('reports/export/monthly-xlsx/', views.export_monthly_xlsx, name='reports_export_monthly_xlsx'),
    path('reports/absent/', views.AbsentReportView.as_view(), name='reports_absent'),
    path('reports/absent/add/', views.AdminAddLeaveView.as_view(), name='reports_absent_add_leave'),
    path('reports/absent/export/excel/', views.ExportAbsentReportExcelView.as_view(), name='reports_absent_excel'),
    path('reports/absent/export/pdf/', views.ExportAbsentReportPDFView.as_view(), name='reports_absent_pdf'),
    # Leave Reports
    path('reports/leave/monthly/', views.LeaveMonthlyReportView.as_view(), name='reports_leave_monthly'),
    path('reports/leave/employee/<int:pk>/', views.LeaveEmployeeReportView.as_view(), name='reports_leave_employee'),
    path('reports/leave/employee/<int:pk>/month/<int:year>/<int:month>/', views.LeaveEmployeeReportView.as_view(), name='reports_leave_employee_month'),
    path('reports/leave/export/csv/', views.ExportLeaveReportCSVView.as_view(), name='reports_leave_export_csv'),
    path('reports/leave/export/pdf/', views.ExportLeaveReportPDFView.as_view(), name='reports_leave_export_pdf'),
    path('reports/leave/export/monthly-xlsx/', views.export_leave_monthly_xlsx, name='reports_leave_export_xlsx'),

    path('settings/schedule/', views.OfficeScheduleView.as_view(), name='schedule_settings'),
    path('expired-data/', views.ExpiredDataView.as_view(), name='expired_data'),
    path('expired-data/delete/', views.delete_expired_selected, name='delete_expired_selected'),
    path('expired-data/delete-all/', views.delete_all_expired, name='delete_all_expired'),

    # Roles & Access
    path('roles/', roles_views.DynamicRoleListView.as_view(), name='role_list'),
    path('roles/add/', roles_views.DynamicRoleCreateView.as_view(), name='role_create'),
    path('roles/<int:pk>/edit/', roles_views.DynamicRoleUpdateView.as_view(), name='role_edit'),
    path('roles/<int:pk>/delete/', roles_views.DynamicRoleDeleteView.as_view(), name='role_delete'),
    path('roles/<int:pk>/matrix/', roles_views.DynamicRoleMatrixView.as_view(), name='role_matrix'),
    path('roles/<int:pk>/members/', roles_views.RoleMembersView.as_view(), name='role_members'),
    path('roles/<int:role_id>/perm/<int:perm_id>/toggle/', roles_views.RolePermissionToggleView.as_view(), name='role_perm_toggle'),
    path('roles/<int:role_id>/perm/<int:perm_id>/scope/', roles_views.RolePermissionScopeView.as_view(), name='role_perm_scope'),
    path('users/<int:pk>/permissions/', roles_views.UserPermissionsView.as_view(), name='user_permissions'),
    path('users/<int:pk>/permissions/override/', roles_views.UserPermissionOverrideSaveView.as_view(), name='user_permission_override'),
    path('permissions/matrix/', roles_views.DynamicRoleListView.as_view(), name='permission_matrix'),
    path('audit-logs/', roles_views.AdminAuditLogView.as_view(), name='admin_audit_logs'),
    path('security-dashboard/', roles_views.AdminSecurityDashboardView.as_view(), name='security_dashboard'),

    # AI Intelligence Workspace & Chatbot
    path('ai/assistant/', views.AIWorkspaceView.as_view(), {'submodule': 'assistant'}, name='ai_assistant'),
    path('ai/attendance-insights/', views.AIWorkspaceView.as_view(), {'submodule': 'attendance-insights'}, name='ai_attendance_insights'),
    path('ai/project-insights/', views.AIWorkspaceView.as_view(), {'submodule': 'project-insights'}, name='ai_project_insights'),
    path('ai/payroll-insights/', views.AIWorkspaceView.as_view(), {'submodule': 'payroll-insights'}, name='ai_payroll_insights'),
    path('ai/smart-reports/', views.AIWorkspaceView.as_view(), {'submodule': 'smart-reports'}, name='ai_smart_reports'),
    path('ai/settings/', views.AIWorkspaceView.as_view(), {'submodule': 'settings'}, name='ai_settings'),
    path('ai/chatbot/dummy-response/', views.AIChatbotDummyResponseView.as_view(), name='ai_chatbot_response'),
]
