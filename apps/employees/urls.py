from django.urls import path
from . import views

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

    # Document Management & Asset Assignment Routes (Phase 2 Step 3)
    path('master/<int:pk>/documents/upload/', views.EmployeeDocumentUploadView.as_view(), name='document_upload'),
    path('documents/<int:pk>/download/', views.EmployeeDocumentDownloadView.as_view(), name='document_download'),
    path('assets/', views.AssetListView.as_view(), name='asset_list'),
    path('assets/create/', views.AssetCreateView.as_view(), name='asset_create'),
    path('master/<int:pk>/assets/assign/', views.AssetAssignView.as_view(), name='asset_assign'),
    path('assets/assignment/<int:pk>/return/', views.AssetReturnView.as_view(), name='asset_return'),

    # Lifecycle State Machine Routes (Phase 2 Step N)
    path('master/<int:pk>/lifecycle/action/', views.LifecycleActionView.as_view(), name='lifecycle_action'),
    path('lifecycle-requests/', views.LifecyclePendingListView.as_view(), name='lifecycle_requests'),
    path('lifecycle-requests/<int:req_pk>/review/', views.LifecycleReviewView.as_view(), name='lifecycle_review'),

    # Multi-Step Wizard & Timeline Routes
    path('wizard/', views.EmployeeWizardView.as_view(), name='employee_wizard'),
    path('wizard/<int:pk>/step/<int:step>/', views.EmployeeWizardView.as_view(), name='employee_wizard_step'),
    path('master/<int:pk>/timeline/', views.EmployeeTimelineView.as_view(), name='employee_timeline'),
]

