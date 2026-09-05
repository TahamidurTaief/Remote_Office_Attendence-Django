import re

PERM_MAPPING = {
    'ProjectListView': ('projects.view', 'view'),
    'ProjectDetailView': ('projects.view', 'view'),
    'ProjectCreateView': ('projects.add', 'add'),
    'ProjectUpdateView': ('projects.edit', 'edit'),
    'ProjectDeleteView': ('projects.delete', 'delete'),
    'TaskTemplateListView': ('projects.view', 'view'),
    'TaskTemplateCreateView': ('projects.add', 'add'),
    'TaskTemplateUpdateView': ('projects.edit', 'edit'),
    'TaskTemplateDeleteView': ('projects.delete', 'delete'),
    'TemplateAddItemView': ('projects.edit', 'edit'),
    'TemplateDeleteItemView': ('projects.edit', 'edit'),
    'TemplateEditItemView': ('projects.edit', 'edit'),
    'ProjectTaskCreateView': ('projects.add', 'add'),
    'ProjectTaskReorderView': ('projects.edit', 'edit'),
    'ProjectTaskBulkStatusView': ('projects.edit', 'edit'),
    'ProjectTaskBulkDeleteView': ('projects.delete', 'delete'),
    'ProjectApplyTemplateView': ('projects.edit', 'edit'),
    'ProjectTaskUpdateStatusView': ('projects.edit', 'edit'),
    'DailyProgressLogCreateView': ('projects.add', 'add'),
    'ManpowerDeploymentCreateView': ('projects.add', 'add'),
    'ManpowerDeploymentAutoFillView': ('projects.edit', 'edit'),
    'ProjectMaterialCreateView': ('projects.add', 'add'),
    'ProjectConfirmSignOffView': ('projects.approve', 'approve'),
    'ProjectExportPDFView': ('projects.export', 'export'),
    'ProjectMaterialIncrementView': ('projects.edit', 'edit'),
    'ProjectTypeListView': ('projects.view', 'view'),
    'ProjectTypeCreateView': ('projects.add', 'add'),
    'ProjectTypeUpdateView': ('projects.edit', 'edit'),
    'ProjectTypeDeleteView': ('projects.delete', 'delete'),
    'ExportProjectTasksCSVView': ('projects.export', 'export'),
    'ExportProjectManpowerCSVView': ('projects.export', 'export'),
    'ExportProjectMaterialsCSVView': ('projects.export', 'export'),
    'ProjectTaskShiftSubsequentView': ('projects.edit', 'edit'),
    'ProjectRequestSignOffView': ('projects.edit', 'edit'),
    'TaskDependencyCreateView': ('projects.edit', 'edit'),
    'TaskDependencyDeleteView': ('projects.edit', 'edit'),
}

VIEWS_PATH = 'apps/projects/views.py'
with open(VIEWS_PATH, 'r', encoding='utf-8') as f:
    content = f.read()

for cls_name, (perm, act) in PERM_MAPPING.items():
    pattern = rf"(class\s+{cls_name}\([^)]*\):)"
    replacement = rf"\1\n    required_permission = '{perm}'\n    action_type = '{act}'"
    content = re.sub(pattern, replacement, content, count=1)

with open(VIEWS_PATH, 'w', encoding='utf-8') as f:
    f.write(content)

print("Applied explicit permissions to all projects CBVs.")
