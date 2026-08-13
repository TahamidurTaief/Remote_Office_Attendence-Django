from django.urls import path
from . import views

app_name = 'payroll'

urlpatterns = [
    path('', views.PayrollRunListView.as_view(), name='payroll_run_list'),
    path('runs/', views.PayrollRunListView.as_view(), name='payroll_run_list_alias'),
    path('runs/create/', views.PayrollRunCreateView.as_view(), name='payroll_run_create'),
    path('runs/<int:pk>/', views.PayrollRunDetailView.as_view(), name='payroll_run_detail'),
    path('runs/<int:pk>/grid/', views.PayrollRunGridPartialView.as_view(), name='payroll_run_grid_partial'),
    path('runs/<int:pk>/sync/', views.PayrollRunSyncView.as_view(), name='payroll_run_sync'),
    path('runs/<int:pk>/transition/', views.PayrollRunTransitionView.as_view(), name='payroll_run_transition'),
    path('runs/<int:pk>/reverse/', views.PayrollRunReverseView.as_view(), name='payroll_run_reverse'),
    path('runs/<int:pk>/adjustments/', views.PayrollAdjustmentModalView.as_view(), name='payroll_adjustment_modal'),
    path('runs/<int:pk>/adjustments/add/', views.PayrollAdjustmentAddView.as_view(), name='payroll_adjustment_add'),
    path('runs/<int:pk>/adjustments/<int:adj_pk>/delete/', views.PayrollAdjustmentDeleteView.as_view(), name='payroll_adjustment_delete'),
    path('payslips/<int:pk>/', views.EmployeePayslipDetailView.as_view(), name='payslip_detail'),
    path('payslips/<int:pk>/pdf/', views.EmployeePayslipPDFView.as_view(), name='payslip_pdf'),
    path('my-payslips/', views.MyPayslipsView.as_view(), name='my_payslips'),
    path('runs/<int:pk>/register/', views.PayrollRegisterView.as_view(), name='payroll_register'),
    path('runs/<int:pk>/register/export/<str:format>/', views.PayrollRegisterExportView.as_view(), name='payroll_register_export'),
    path('runs/<int:pk>/bank-report/', views.BankReportView.as_view(), name='bank_report'),
    path('runs/<int:pk>/bank-report/export/<str:format>/', views.BankReportExportView.as_view(), name='bank_report_export'),
    path('runs/<int:pk>/cash-report/', views.CashReportView.as_view(), name='cash_report'),
    path('runs/<int:pk>/cash-report/export/<str:format>/', views.CashReportExportView.as_view(), name='cash_report_export'),
]
