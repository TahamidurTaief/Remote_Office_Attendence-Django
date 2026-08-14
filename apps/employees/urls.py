from django.urls import path
from . import views
from . import api_views

app_name = 'employees'

urlpatterns = [
    path('', views.EmployeeMasterListView.as_view(), name='employee_list'),
    path('add/', views.EmployeeWizardView.as_view(), name='employee_add'),
    path('<int:pk>/', views.EmployeeMasterDetailView.as_view(), name='employee_detail'),
    path('<int:pk>/edit/', views.EmployeeMasterEditView.as_view(), name='employee_edit'),
    path('<int:pk>/toggle-status/', views.ToggleStatusView.as_view(), name='toggle_status'),
    path('<int:pk>/suspend/', views.EmployeeSuspendToggleView.as_view(), name='employee_suspend_toggle'),
    path('<int:pk>/suspend/modal/', views.EmployeeSuspendModalView.as_view(), name='employee_suspend_toggle_modal'),
    
    # Document CRUD
    path('<int:employee_pk>/documents/add/', views.EmployeeDocumentCreateView.as_view(), name='document_add'),
    path('documents/<int:pk>/edit/', views.EmployeeDocumentEditView.as_view(), name='document_edit'),
    path('documents/<int:pk>/delete/', views.EmployeeDocumentDeleteView.as_view(), name='document_delete'),

    # Phase 2 Employee Master (SSOT) Routes
    path('master/', views.EmployeeMasterListView.as_view(), name='master_list'),
    path('master/create/', views.EmployeeMasterCreateView.as_view(), name='master_create'),
    path('master/<int:pk>/', views.EmployeeMasterDetailView.as_view(), name='master_detail'),
    path('master/<int:pk>/edit/', views.EmployeeMasterEditView.as_view(), name='master_edit'),
    path('master/<int:pk>/archive/', views.EmployeeMasterArchiveView.as_view(), name='master_archive'),
    path('master/<int:pk>/delete/', views.EmployeeMasterDeleteView.as_view(), name='master_delete'),
    path('master/<int:pk>/audit/', views.EmployeeAuditLogView.as_view(), name='master_audit'),
    path('org-chart/', views.OrgChartView.as_view(), name='org_chart'),
    path('org-chart/node/<int:pk>/', views.OrgChartNodeView.as_view(), name='org_chart_node'),
    path('delegations/', views.ManagerDelegationListView.as_view(), name='delegation_list'),
    path('delegations/create/', views.ManagerDelegationCreateView.as_view(), name='delegation_create'),
    path('delegations/<int:pk>/end/', views.ManagerDelegationEndView.as_view(), name='delegation_end'),
    path('reports/', views.EmployeeReportsView.as_view(), name='reports'),

    # Document Management & Asset Assignment Routes (Phase 2 Step 3)
    path('master/<int:pk>/documents/upload/', views.EmployeeDocumentUploadView.as_view(), name='document_upload'),
    path('documents/<int:pk>/download/', views.EmployeeDocumentDownloadView.as_view(), name='document_download'),
    path('documents/<int:pk>/verify/', views.EmployeeDocumentVerifyView.as_view(), name='document_verify'),
    path('documents/<int:pk>/archive/', views.EmployeeDocumentArchiveView.as_view(), name='document_archive'),
    path('assets/', views.AssetListView.as_view(), name='asset_list'),
    path('assets/create/', views.AssetCreateView.as_view(), name='asset_create'),
    path('master/<int:pk>/assets/assign/', views.AssetAssignView.as_view(), name='asset_assign'),
    path('assets/assignment/<int:pk>/return/', views.AssetReturnView.as_view(), name='asset_return'),
    path('assets/assignment/<int:pk>/reassign/', views.AssetReassignView.as_view(), name='asset_reassign'),

    # Lifecycle State Machine Routes (Phase 2 Step N)
    path('master/<int:pk>/lifecycle/action/', views.LifecycleActionView.as_view(), name='lifecycle_action'),
    path('lifecycle-requests/', views.LifecyclePendingListView.as_view(), name='lifecycle_requests'),
    path('lifecycle-requests/<int:req_pk>/review/', views.LifecycleReviewView.as_view(), name='lifecycle_review'),

    path('wizard/', views.EmployeeWizardView.as_view(), name='employee_wizard'),
    path('wizard/<uuid:uuid>/step/<int:step>/', views.EmployeeWizardView.as_view(), name='employee_wizard_step'),
    path('master/<int:pk>/timeline/', views.EmployeeTimelineView.as_view(), name='employee_timeline'),

    # API endpoints
    path('api/employees/<int:pk>/subordinates/', api_views.SubordinatesAPIView.as_view(), name='api_subordinates'),
    path('api/employees/<int:pk>/direct-reports/', api_views.DirectReportsAPIView.as_view(), name='api_direct_reports'),
    path('api/employees/<int:pk>/org-chain/', api_views.OrgChainAPIView.as_view(), name='api_org_chain'),
    path('api/employees/org-analytics/', api_views.OrgAnalyticsAPIView.as_view(), name='api_org_analytics'),
    path('api/employees/<int:pk>/is-manager/<int:target_pk>/', api_views.IsManagerAPIView.as_view(), name='api_is_manager'),

    # Department management
    path('departments/', views.DepartmentListView.as_view(), name='department_list'),
    path('departments/create/', views.DepartmentCreateView.as_view(), name='department_create'),
    path('departments/<int:pk>/edit/', views.DepartmentEditView.as_view(), name='department_edit'),
    path('departments/<int:pk>/delete/', views.DepartmentDeleteView.as_view(), name='department_delete'),
    path('departments/export/', views.DepartmentExportCSVView.as_view(), name='department_export_csv'),
    path('departments/import/', views.DepartmentImportCSVView.as_view(), name='department_import_csv'),

    # Designation management
    path('designations/', views.DesignationListView.as_view(), name='designation_list'),
    path('designations/create/', views.DesignationCreateView.as_view(), name='designation_create'),
    path('designations/<int:pk>/edit/', views.DesignationEditView.as_view(), name='designation_edit'),

    # HTMX cascade APIs
    path('api/departments-for-branch/', views.DepartmentsForBranchAPIView.as_view(), name='api_departments_for_branch'),
    path('api/designations-for-department/', views.DesignationsForDepartmentAPIView.as_view(), name='api_designations_for_department'),
]

