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
    path('attendance/<int:pk>/detail/', views.AttendanceDetailView.as_view(), name='attendance_detail'),
    path('attendance/<int:pk>/locations/', views.AttendanceLocationsView.as_view(), name='attendance_locations'),
    path('attendance/export/', views.ExportAttendanceCSVView.as_view(), name='attendance_export'),
    path('attendance/manual-entry/', views.ManualEntryView.as_view(), name='manual_entry'),

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
    path('settings/schedule/', views.OfficeScheduleView.as_view(), name='schedule_settings'),
    path('expired-data/', views.ExpiredDataView.as_view(), name='expired_data'),
    path('expired-data/delete/', views.delete_expired_selected, name='delete_expired_selected'),
    path('expired-data/delete-all/', views.delete_all_expired, name='delete_all_expired'),

    # Roles & Access
    path('roles/', roles_views.RoleListView.as_view(), name='role_list'),
    path('roles/add/', roles_views.RoleCreateView.as_view(), name='role_create'),
    path('roles/<int:pk>/edit/', roles_views.RoleUpdateView.as_view(), name='role_edit'),
    path('roles/<int:pk>/delete/', roles_views.RoleDeleteView.as_view(), name='role_delete'),
    path('roles/<int:pk>/clone/', roles_views.RoleCloneView.as_view(), name='role_clone'),
    path('roles/<int:pk>/permissions/', roles_views.RolePermissionsView.as_view(), name='role_permissions'),
    path('roles/<int:pk>/members/', roles_views.RoleMembersView.as_view(), name='role_members'),
    path('roles/<int:group_id>/permission/<int:perm_id>/toggle/', roles_views.PermissionToggleView.as_view(), name='role_permission_toggle'),
    path('permissions/matrix/', roles_views.PermissionMatrixView.as_view(), name='permission_matrix'),
    path('audit-logs/', roles_views.AdminAuditLogView.as_view(), name='admin_audit_logs'),
    path('security-dashboard/', roles_views.AdminSecurityDashboardView.as_view(), name='security_dashboard'),
    path('users/<int:pk>/permissions/', roles_views.UserPermissionsView.as_view(), name='user_permissions'),
]
