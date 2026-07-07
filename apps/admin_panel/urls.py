from django.urls import path
from . import views

app_name = 'admin_panel'

urlpatterns = [
    path('dashboard/', views.AdminDashboardView.as_view(), name='dashboard'),
    path('dashboard/partial/', views.DashboardPartialView.as_view(), name='dashboard_partial'),
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
    path('settings/schedule/', views.OfficeScheduleView.as_view(), name='schedule_settings'),
    path('expired-data/', views.ExpiredDataView.as_view(), name='expired_data'),
    path('expired-data/delete/', views.delete_expired_selected, name='delete_expired_selected'),
    path('expired-data/delete-all/', views.delete_all_expired, name='delete_all_expired'),
]
