from django.urls import path
from . import views

app_name = 'projects'

urlpatterns = [
    path('', views.ProjectListView.as_view(), name='project_list'),
    path('add/', views.ProjectCreateView.as_view(), name='project_add'),
    path('<int:pk>/', views.ProjectDetailView.as_view(), name='project_detail'),
    path('<int:pk>/edit/', views.ProjectUpdateView.as_view(), name='project_edit'),
    path('<int:pk>/delete/', views.ProjectDeleteView.as_view(), name='project_delete'),
    
    # Project Types
    path('types/', views.ProjectTypeListView.as_view(), name='project_type_list'),
    path('types/add/', views.ProjectTypeCreateView.as_view(), name='project_type_create'),
    path('types/<int:pk>/edit/', views.ProjectTypeUpdateView.as_view(), name='project_type_edit'),
    path('types/<int:pk>/delete/', views.ProjectTypeDeleteView.as_view(), name='project_type_delete'),
    
    # Task Templates
    path('templates/', views.TaskTemplateListView.as_view(), name='template_list'),
    path('templates/add/', views.TaskTemplateCreateView.as_view(), name='template_add'),
    path('templates/<int:pk>/edit/', views.TaskTemplateUpdateView.as_view(), name='template_edit'),
    path('templates/<int:pk>/delete/', views.TaskTemplateDeleteView.as_view(), name='template_delete'),
    path('templates/<int:template_pk>/items/add/', views.TemplateAddItemView.as_view(), name='template_add_item'),
    path('templates/items/<int:pk>/delete/', views.TemplateDeleteItemView.as_view(), name='template_delete_item'),
    
    # Project Tasks
    path('<int:project_id>/tasks/add/', views.ProjectTaskCreateView.as_view(), name='project_task_add'),
    path('tasks/<int:pk>/edit/', views.ProjectTaskUpdateView.as_view(), name='project_task_edit'),
    path('tasks/<int:pk>/delete/', views.ProjectTaskDeleteView.as_view(), name='project_task_delete'),
    path('tasks/<int:pk>/shift-subsequent/', views.ProjectTaskShiftSubsequentView.as_view(), name='project_task_shift_subsequent'),
    path('tasks/<int:pk>/update-status/', views.ProjectTaskUpdateStatusView.as_view(), name='project_task_update_status'),
    path('tasks/<int:pk>/reorder/', views.ProjectTaskReorderView.as_view(), name='project_task_reorder'),
    path('<int:project_id>/tasks/bulk-status/', views.ProjectTaskBulkStatusView.as_view(), name='project_task_bulk_status'),
    path('<int:project_id>/apply-template/', views.ProjectApplyTemplateView.as_view(), name='project_apply_template'),
    
    # Daily Progress Logs
    path('<int:project_id>/logs/add/', views.DailyProgressLogCreateView.as_view(), name='progress_log_add'),
    path('logs/<int:pk>/edit/', views.DailyProgressLogUpdateView.as_view(), name='progress_log_edit'),
    path('logs/<int:pk>/delete/', views.DailyProgressLogDeleteView.as_view(), name='progress_log_delete'),
    
    # Manpower Deployment Logs
    path('<int:project_id>/manpower/add/', views.ManpowerDeploymentCreateView.as_view(), name='manpower_add'),
    path('manpower/<int:pk>/edit/', views.ManpowerDeploymentUpdateView.as_view(), name='manpower_edit'),
    path('manpower/<int:pk>/delete/', views.ManpowerDeploymentDeleteView.as_view(), name='manpower_delete'),
    path('manpower/<int:pk>/autofill/', views.ManpowerDeploymentAutoFillView.as_view(), name='manpower_autofill'),

    # Material Tracking
    path('<int:project_id>/materials/add/', views.ProjectMaterialCreateView.as_view(), name='material_add'),
    path('materials/<int:pk>/edit/', views.ProjectMaterialUpdateView.as_view(), name='material_edit'),
    path('materials/<int:pk>/delete/', views.ProjectMaterialDeleteView.as_view(), name='material_delete'),
    path('materials/<int:pk>/increment/', views.ProjectMaterialIncrementView.as_view(), name='material_increment'),

    # Sign-off & PDF Export
    path('<int:project_id>/confirm-signoff/', views.ProjectConfirmSignOffView.as_view(), name='confirm_signoff'),
    path('<int:project_id>/request-signoff/', views.ProjectRequestSignOffView.as_view(), name='request_signoff'),
    path('<int:project_id>/export-pdf/', views.ProjectExportPDFView.as_view(), name='export_pdf'),

    # CSV Exports
    path('<int:pk>/export-tasks/', views.ExportProjectTasksCSVView.as_view(), name='export_tasks_csv'),
    path('<int:pk>/export-manpower/', views.ExportProjectManpowerCSVView.as_view(), name='export_manpower_csv'),
    path('<int:pk>/export-materials/', views.ExportProjectMaterialsCSVView.as_view(), name='export_materials_csv'),
]

