import re

VIEWS_PATH = 'apps/projects/views.py'

with open(VIEWS_PATH, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Fix ProjectTaskUpdateView
old_task_update = """class ProjectTaskUpdateView(RoleRequiredMixin, UpdateView):
    allowed_roles = ['admin', 'system_owner', 'manager']
    # TODO: branch-scoping deferred — depends on Role/Permission system (see separate RBAC work)
    model = ProjectTask
    form_class = ProjectTaskForm
    template_name = 'projects/task_form.html'"""

new_task_update = """class ProjectTaskUpdateView(RoleRequiredMixin, UpdateView):
    required_permission = 'projects.edit'
    action_type = 'edit'
    model = ProjectTask
    form_class = ProjectTaskForm
    template_name = 'projects/task_form.html'

    def get_queryset(self):
        qs = super().get_queryset()
        return PermissionEngine.filter_queryset(
            user=self.request.user,
            queryset=qs,
            codename='projects.edit',
            branch_field='project__branch'
        )"""

# Handle em-dash or en-dash or regular hyphen in TODO comment
if old_task_update not in content:
    # Try regex replacement for ProjectTaskUpdateView
    content = re.sub(
        r"class ProjectTaskUpdateView\(RoleRequiredMixin, UpdateView\):\s+allowed_roles = \['admin', 'system_owner', 'manager'\]\s+# TODO: branch-scoping deferred[^\n]*\n\s+model = ProjectTask",
        """class ProjectTaskUpdateView(RoleRequiredMixin, UpdateView):
    required_permission = 'projects.edit'
    action_type = 'edit'
    model = ProjectTask""",
        content
    )
    # Add get_queryset right after template_name
    content = content.replace(
        "template_name = 'projects/task_form.html'\n\n    def get_context_data",
        """template_name = 'projects/task_form.html'

    def get_queryset(self):
        qs = super().get_queryset()
        return PermissionEngine.filter_queryset(
            user=self.request.user,
            queryset=qs,
            codename='projects.edit',
            branch_field='project__branch'
        )

    def get_context_data"""
    )
else:
    content = content.replace(old_task_update, new_task_update)

# 2. Fix ProjectTaskDeleteView
content = re.sub(
    r"class ProjectTaskDeleteView\(AdminRequiredMixin, View\):\s+def post\(self, request, pk\):\s+# TODO: branch-scoping deferred[^\n]*\n\s+task = get_object_or_404\(ProjectTask, pk=pk\)",
    """class ProjectTaskDeleteView(AdminRequiredMixin, View):
    required_permission = 'projects.delete'
    action_type = 'delete'

    def post(self, request, pk):
        task = PermissionEngine.get_scoped_object_or_404(
            model_or_qs=ProjectTask,
            user=request.user,
            codename='projects.delete',
            branch_field='project__branch',
            pk=pk
        )""",
    content
)

# 3. Fix DailyProgressLogUpdateView
content = re.sub(
    r"class DailyProgressLogUpdateView\(AdminRequiredMixin, UpdateView\):\s+# TODO: branch-scoping deferred[^\n]*\n\s+model = DailyProgressLog\s+form_class = DailyProgressLogForm\s+template_name = 'projects/log_form\.html'",
    """class DailyProgressLogUpdateView(AdminRequiredMixin, UpdateView):
    required_permission = 'projects.edit'
    action_type = 'edit'
    model = DailyProgressLog
    form_class = DailyProgressLogForm
    template_name = 'projects/log_form.html'

    def get_queryset(self):
        qs = super().get_queryset()
        return PermissionEngine.filter_queryset(
            user=self.request.user,
            queryset=qs,
            codename='projects.edit',
            branch_field='project__branch'
        )""",
    content
)

# 4. Fix DailyProgressLogDeleteView
content = re.sub(
    r"class DailyProgressLogDeleteView\(AdminRequiredMixin, View\):\s+def post\(self, request, pk\):\s+# TODO: branch-scoping deferred[^\n]*\n\s+log = get_object_or_404\(DailyProgressLog, pk=pk\)",
    """class DailyProgressLogDeleteView(AdminRequiredMixin, View):
    required_permission = 'projects.delete'
    action_type = 'delete'

    def post(self, request, pk):
        log = PermissionEngine.get_scoped_object_or_404(
            model_or_qs=DailyProgressLog,
            user=request.user,
            codename='projects.delete',
            branch_field='project__branch',
            pk=pk
        )""",
    content
)

# 5. Fix ManpowerDeploymentUpdateView
content = re.sub(
    r"class ManpowerDeploymentUpdateView\(AdminRequiredMixin, UpdateView\):\s+# TODO: branch-scoping deferred[^\n]*\n\s+model = ManpowerDeployment\s+form_class = ManpowerDeploymentForm\s+template_name = 'projects/manpower_form\.html'",
    """class ManpowerDeploymentUpdateView(AdminRequiredMixin, UpdateView):
    required_permission = 'projects.edit'
    action_type = 'edit'
    model = ManpowerDeployment
    form_class = ManpowerDeploymentForm
    template_name = 'projects/manpower_form.html'

    def get_queryset(self):
        qs = super().get_queryset()
        return PermissionEngine.filter_queryset(
            user=self.request.user,
            queryset=qs,
            codename='projects.edit',
            branch_field='project__branch'
        )""",
    content
)

# 6. Fix ManpowerDeploymentDeleteView
content = re.sub(
    r"class ManpowerDeploymentDeleteView\(AdminRequiredMixin, View\):\s+def post\(self, request, pk\):\s+# TODO: branch-scoping deferred[^\n]*\n\s+deployment = get_object_or_404\(ManpowerDeployment, pk=pk\)",
    """class ManpowerDeploymentDeleteView(AdminRequiredMixin, View):
    required_permission = 'projects.delete'
    action_type = 'delete'

    def post(self, request, pk):
        deployment = PermissionEngine.get_scoped_object_or_404(
            model_or_qs=ManpowerDeployment,
            user=request.user,
            codename='projects.delete',
            branch_field='project__branch',
            pk=pk
        )""",
    content
)

# 7. Fix ProjectMaterialUpdateView
content = re.sub(
    r"class ProjectMaterialUpdateView\(AdminRequiredMixin, UpdateView\):\s+# TODO: branch-scoping deferred[^\n]*\n\s+model = ProjectMaterial\s+form_class = ProjectMaterialForm\s+template_name = 'projects/material_form\.html'",
    """class ProjectMaterialUpdateView(AdminRequiredMixin, UpdateView):
    required_permission = 'projects.edit'
    action_type = 'edit'
    model = ProjectMaterial
    form_class = ProjectMaterialForm
    template_name = 'projects/material_form.html'

    def get_queryset(self):
        qs = super().get_queryset()
        return PermissionEngine.filter_queryset(
            user=self.request.user,
            queryset=qs,
            codename='projects.edit',
            branch_field='project__branch'
        )""",
    content
)

# 8. Fix ProjectMaterialDeleteView
content = re.sub(
    r"class ProjectMaterialDeleteView\(AdminRequiredMixin, View\):\s+def post\(self, request, pk\):\s+# TODO: branch-scoping deferred[^\n]*\n\s+material = get_object_or_404\(ProjectMaterial, pk=pk\)",
    """class ProjectMaterialDeleteView(AdminRequiredMixin, View):
    required_permission = 'projects.delete'
    action_type = 'delete'

    def post(self, request, pk):
        material = PermissionEngine.get_scoped_object_or_404(
            model_or_qs=ProjectMaterial,
            user=request.user,
            codename='projects.delete',
            branch_field='project__branch',
            pk=pk
        )""",
    content
)

# Remove remaining allowed_roles in projects/views.py
content = re.sub(
    r"class GlobalTaskListView\(RoleRequiredMixin, ListView\):\s+allowed_roles = \['admin', 'manager', 'system_owner', 'super_admin'\]",
    """class GlobalTaskListView(RoleRequiredMixin, ListView):
    required_permission = 'projects.view'
    action_type = 'view'""",
    content
)

content = re.sub(
    r"class GlobalTaskCreateView\(RoleRequiredMixin, CreateView\):\s+allowed_roles = \['admin', 'manager'\]",
    """class GlobalTaskCreateView(RoleRequiredMixin, CreateView):
    required_permission = 'projects.add'
    action_type = 'add'""",
    content
)

# Fix role checks in function views
content = content.replace(
    "if request.user.is_superuser or PermissionEngine.evaluate(request.user, 'projects.edit').allowed or getattr(request.user, 'role', '') in ('admin', 'manager'):",
    "if request.user.is_superuser or PermissionEngine.evaluate(request.user, 'projects.edit').allowed:"
)

content = content.replace(
    "if user.is_superuser or PermissionEngine.evaluate(user, 'projects.view').allowed or getattr(user, 'role', '') in ('admin', 'manager'):",
    "if user.is_superuser or PermissionEngine.evaluate(user, 'projects.view').allowed:"
)

content = content.replace(
    "if not (request.user.is_superuser or PermissionEngine.evaluate(request.user, 'projects.edit').allowed or getattr(request.user, 'role', '') in ('admin', 'manager')):",
    "if not (request.user.is_superuser or PermissionEngine.evaluate(request.user, 'projects.edit').allowed):"
)

# Fix Gantt classes
content = re.sub(
    r"class ProjectGanttView\(LoginRequiredMixin, View\):",
    """class ProjectGanttView(RoleRequiredMixin, View):
    required_permission = 'projects.view'
    action_type = 'view'""",
    content
)

content = re.sub(
    r"class ProjectGanttExportView\(LoginRequiredMixin, View\):",
    """class ProjectGanttExportView(RoleRequiredMixin, View):
    required_permission = 'projects.export'
    action_type = 'export'""",
    content
)

content = re.sub(
    r"class ProjectGanttImportView\(View\):",
    """class ProjectGanttImportView(RoleRequiredMixin, View):
    required_permission = 'projects.add'
    action_type = 'add'""",
    content
)

content = re.sub(
    r"class ProjectGanttImportPreviewView\(View\):",
    """class ProjectGanttImportPreviewView(RoleRequiredMixin, View):
    required_permission = 'projects.add'
    action_type = 'add'""",
    content
)

content = re.sub(
    r"class ProjectGanttImportConfirmView\(View\):",
    """class ProjectGanttImportConfirmView(RoleRequiredMixin, View):
    required_permission = 'projects.add'
    action_type = 'add'""",
    content
)

with open(VIEWS_PATH, 'w', encoding='utf-8') as f:
    f.write(content)

print("Patch applied to apps/projects/views.py")
