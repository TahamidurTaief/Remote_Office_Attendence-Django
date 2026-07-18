from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse_lazy
from django.http import HttpResponse
import csv
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView, View
from django.contrib import messages
from django.db.models import Q
from datetime import date, timedelta
from apps.accounts.mixins import AdminRequiredMixin, RoleRequiredMixin
from apps.branches.models import Branch
from apps.employees.models import EmployeeProfile
from apps.notifications.models import Notification
from apps.notifications.dispatch import send_email_notification
from .models import Project, ProjectType, TaskTemplate, TaskTemplateItem, ProjectTask, DailyProgressLog, ManpowerDeployment, ProjectMaterial, ProjectSignOff
from .forms import ProjectForm, ProjectTypeForm, TaskTemplateForm, TaskTemplateItemForm, ProjectTaskForm, DailyProgressLogForm, ManpowerDeploymentForm, ProjectMaterialForm, GlobalProjectTaskForm






class ProjectListView(AdminRequiredMixin, ListView):
    model = Project
    template_name = 'projects/project_list.html'
    context_object_name = 'projects'
    paginate_by = 10

    def get_queryset(self):
        # TODO: branch-scoping deferred — depends on Role/Permission system (see separate RBAC work)
        queryset = super().get_queryset().select_related(
            'project_manager', 'site_engineer', 'branch', 'project_type'  # #15 — add project_type to avoid N+1
        )
        search_query = self.request.GET.get('search', '')
        status_filter = self.request.GET.get('status', '')
        branch_filter = self.request.GET.get('branch', '')

        if search_query:
            queryset = queryset.filter(
                Q(name__icontains=search_query) |
                Q(client_name__icontains=search_query) |
                Q(location__icontains=search_query)
            )

        if status_filter:
            queryset = queryset.filter(status=status_filter)

        if branch_filter:
            queryset = queryset.filter(branch_id=branch_filter)

        return queryset.order_by('-created_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search'] = self.request.GET.get('search', '')
        context['status_val'] = self.request.GET.get('status', '')
        context['branch_id'] = self.request.GET.get('branch', '')
        context['branches'] = Branch.objects.all()
        context['status_choices'] = Project.STATUS_CHOICES
        return context

class ProjectDetailView(AdminRequiredMixin, DetailView):
    model = Project
    template_name = 'projects/project_detail.html'
    context_object_name = 'project'

    def get_queryset(self):
        # TODO: branch-scoping deferred — depends on Role/Permission system (see separate RBAC work)
        # #16 — add project_type to select_related to avoid N+1 on template rendering
        return super().get_queryset().select_related(
            'project_manager', 'site_engineer', 'branch', 'sign_off', 'project_type'
        )

    def get_context_data(self, **kwargs):
        from django.db.models import Count
        context = super().get_context_data(**kwargs)
        
        tasks_qs = self.object.tasks.select_related('responsible_person').all().order_by('order')
        member_id = self.request.GET.get('member')
        if member_id:
            try:
                tasks_qs = tasks_qs.filter(responsible_person_id=int(member_id))
            except ValueError:
                pass
        context['tasks'] = tasks_qs
        
        # Get all distinct responsible persons assigned to tasks in this project
        project_members = EmployeeProfile.objects.filter(assigned_tasks__project=self.object).distinct().order_by('full_name')
        context['project_members'] = project_members
        context['selected_member_id'] = int(member_id) if member_id and member_id.isdigit() else None

        context['templates'] = TaskTemplate.objects.annotate(items_count=Count('items')).order_by('name')
        # #17 — removed redundant 'project' from select_related (already available via self.object)
        context['progress_logs'] = self.object.progress_logs.select_related('logged_by').all().order_by('-date', '-created_at')
        context['log_form'] = DailyProgressLogForm(initial={'date': date.today()})
        context['manpower_logs'] = self.object.manpower_logs.all().order_by('-date', 'trade')
        context['manpower_form'] = ManpowerDeploymentForm(initial={'date': date.today()})
        context['materials'] = self.object.materials.all().order_by('material_name')
        context['material_form'] = ProjectMaterialForm()

        # Get or create sign off from select_related cache if available
        try:
            sign_off = self.object.sign_off
        except ProjectSignOff.DoesNotExist:
            sign_off = ProjectSignOff.objects.create(project=self.object)
        context['sign_off'] = sign_off

        return context




class ProjectCreateView(AdminRequiredMixin, CreateView):
    model = Project
    form_class = ProjectForm
    template_name = 'projects/project_form.html'
    success_url = reverse_lazy('projects:project_list')

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        messages.success(self.request, 'Project created successfully.')
        return super().form_valid(form)

class ProjectUpdateView(AdminRequiredMixin, UpdateView):
    model = Project
    form_class = ProjectForm
    template_name = 'projects/project_form.html'
    success_url = reverse_lazy('projects:project_list')

    def form_valid(self, form):
        messages.success(self.request, 'Project updated successfully.')
        return super().form_valid(form)

class ProjectDeleteView(AdminRequiredMixin, View):
    def post(self, request, pk):
        # TODO: branch-scoping deferred — depends on Role/Permission system (see separate RBAC work)
        project = get_object_or_404(Project, pk=pk)
        project_name = project.name
        project.delete()
        messages.success(request, f'Project "{project_name}" was successfully deleted.')
        return redirect('projects:project_list')

class TaskTemplateListView(AdminRequiredMixin, ListView):
    model = TaskTemplate
    template_name = 'projects/template_list.html'
    context_object_name = 'templates'
    paginate_by = 10

    def get_queryset(self):
        from django.db.models import Count
        queryset = super().get_queryset().annotate(items_count=Count('items'))
        search_query = self.request.GET.get('search', '')
        if search_query:
            queryset = queryset.filter(name__icontains=search_query)
        return queryset.order_by('-created_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search'] = self.request.GET.get('search', '')
        return context

class TaskTemplateCreateView(AdminRequiredMixin, CreateView):
    model = TaskTemplate
    form_class = TaskTemplateForm
    template_name = 'projects/template_form.html'
    success_url = reverse_lazy('projects:template_list')

    def form_valid(self, form):
        messages.success(self.request, 'Task template created successfully.')
        return super().form_valid(form)

class TaskTemplateUpdateView(AdminRequiredMixin, UpdateView):
    model = TaskTemplate
    form_class = TaskTemplateForm
    template_name = 'projects/template_form.html'
    success_url = reverse_lazy('projects:template_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['items'] = self.object.items.all().order_by('order')
        context['item_form'] = TaskTemplateItemForm()
        return context

    def form_valid(self, form):
        messages.success(self.request, 'Task template updated successfully.')
        return super().form_valid(form)

class TaskTemplateDeleteView(AdminRequiredMixin, View):
    def post(self, request, pk):
        template = get_object_or_404(TaskTemplate, pk=pk)
        template_name = template.name
        template.delete()
        messages.success(request, f'Task template "{template_name}" was successfully deleted.')
        return redirect('projects:template_list')

# Template Item endpoints
class TemplateAddItemView(AdminRequiredMixin, View):
    def post(self, request, template_pk):
        template = get_object_or_404(TaskTemplate, pk=template_pk)
        form = TaskTemplateItemForm(request.POST)
        if form.is_valid():
            item = form.save(commit=False)
            item.template = template
            item.save()
            messages.success(request, 'Template item added successfully.')
        else:
            messages.error(request, 'Failed to add template item. Please verify details.')
        return redirect('projects:template_edit', pk=template.pk)

class TemplateDeleteItemView(AdminRequiredMixin, View):
    def post(self, request, pk):
        item = get_object_or_404(TaskTemplateItem, pk=pk)
        template_pk = item.template.pk
        item.delete()
        messages.success(request, 'Template item deleted successfully.')
        return redirect('projects:template_edit', pk=template_pk)

# ProjectTask CRUD
class ProjectTaskCreateView(AdminRequiredMixin, CreateView):
    model = ProjectTask
    form_class = ProjectTaskForm
    template_name = 'projects/task_form.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['project'] = get_object_or_404(Project, pk=self.kwargs['project_id'])
        return context

    def form_valid(self, form):
        project = get_object_or_404(Project, pk=self.kwargs['project_id'])
        form.instance.project = project
        response = super().form_valid(form)
        
        # Notify assigned employee
        task = self.object
        if task.responsible_person and task.responsible_person.user:
            Notification.objects.create(
                recipient=task.responsible_person.user,
                employee=task.responsible_person,
                title=f"New Task Assigned: {task.activity}",
                message=f"You have been assigned to task '{task.activity}' for project '{project.name}'.",
                notif_type='field_visit'
            )
            subject = f"New Task Assigned: {task.activity}"
            message = (
                f"Hello {task.responsible_person.full_name},\n\n"
                f"You have been assigned to the following task in project '{project.name}':\n"
                f"Task: {task.activity}\n"
                f"Planned: {task.planned_start or '—'} to {task.planned_finish or '—'}\n"
                f"Status: {task.status}\n\n"
            )
            if task.assignment_attachment:
                message += f"See attached reference file: {task.assignment_attachment.url}\n\n"
            message += "Regards,\nFieldTrack System"
            send_email_notification(task.responsible_person.user, subject, message)
            
        messages.success(self.request, 'Task added successfully.')
        return response

    def get_success_url(self):
        return reverse_lazy('projects:project_detail', kwargs={'pk': self.kwargs['project_id']})

class ProjectTaskUpdateView(AdminRequiredMixin, UpdateView):
    # TODO: branch-scoping deferred — depends on Role/Permission system (see separate RBAC work)
    model = ProjectTask
    form_class = ProjectTaskForm
    template_name = 'projects/task_form.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['project'] = self.object.project
        return context

    def form_valid(self, form):
        old_task = ProjectTask.objects.get(pk=self.get_object().pk)
        old_resp = old_task.responsible_person
        response = super().form_valid(form)
        
        new_task = self.object
        if new_task.responsible_person and new_task.responsible_person != old_resp:
            if new_task.responsible_person.user:
                Notification.objects.create(
                    recipient=new_task.responsible_person.user,
                    employee=new_task.responsible_person,
                    title=f"Task Assigned: {new_task.activity}",
                    message=f"You have been assigned to task '{new_task.activity}' for project '{new_task.project.name}'.",
                    notif_type='field_visit'
                )
                subject = f"Task Assigned: {new_task.activity}"
                message = (
                    f"Hello {new_task.responsible_person.full_name},\n\n"
                    f"You have been assigned to the following task in project '{new_task.project.name}':\n"
                    f"Task: {new_task.activity}\n"
                    f"Planned: {new_task.planned_start or '—'} to {new_task.planned_finish or '—'}\n"
                    f"Status: {new_task.status}\n\n"
                )
                if new_task.assignment_attachment:
                    message += f"See attached reference file: {new_task.assignment_attachment.url}\n\n"
                message += "Regards,\nFieldTrack System"
                send_email_notification(new_task.responsible_person.user, subject, message)

        messages.success(self.request, 'Task updated successfully.')
        return response

    def get_success_url(self):
        return reverse_lazy('projects:project_detail', kwargs={'pk': self.object.project.pk})

class ProjectTaskDeleteView(AdminRequiredMixin, View):
    def post(self, request, pk):
        # TODO: branch-scoping deferred — depends on Role/Permission system (see separate RBAC work)
        task = get_object_or_404(ProjectTask, pk=pk)
        project_pk = task.project.pk
        task_activity = task.activity
        task.delete()
        messages.success(request, f'Task "{task_activity}" deleted successfully.')
        return redirect('projects:project_detail', pk=project_pk)


class ProjectTaskReorderView(AdminRequiredMixin, View):
    """Move a task up or down by swapping its order value with the adjacent task.
    POST body: direction = 'up' | 'down'
    """
    def post(self, request, pk):
        task = get_object_or_404(ProjectTask, pk=pk)
        direction = request.POST.get('direction')
        project_tasks = ProjectTask.objects.filter(project=task.project).order_by('order')
        task_list = list(project_tasks)
        idx = next((i for i, t in enumerate(task_list) if t.pk == task.pk), None)

        if direction == 'up' and idx is not None and idx > 0:
            sibling = task_list[idx - 1]
            task.order, sibling.order = sibling.order, task.order
            task.save(update_fields=['order'])
            sibling.save(update_fields=['order'])
        elif direction == 'down' and idx is not None and idx < len(task_list) - 1:
            sibling = task_list[idx + 1]
            task.order, sibling.order = sibling.order, task.order
            task.save(update_fields=['order'])
            sibling.save(update_fields=['order'])

        messages.success(request, 'Task order updated.')
        return redirect('projects:project_detail', pk=task.project.pk)


class ProjectTaskBulkStatusView(AdminRequiredMixin, View):
    """Bulk-update the status of multiple tasks in one POST.
    POST body: task_ids (list of pk strings), new_status (string)
    """
    def post(self, request, project_id):
        project = get_object_or_404(Project, pk=project_id)
        task_ids = request.POST.getlist('task_ids')
        new_status = request.POST.get('new_status', '').strip()

        if not task_ids:
            messages.error(request, 'No tasks selected.')
            return redirect('projects:project_detail', pk=project_id)

        valid_statuses = dict(ProjectTask.STATUS_CHOICES)
        if new_status not in valid_statuses:
            messages.error(request, f'Invalid status: {new_status}')
            return redirect('projects:project_detail', pk=project_id)

        # Restrict update to tasks that belong to this project (prevents IDOR)
        updated = ProjectTask.objects.filter(
            pk__in=task_ids, project=project
        ).update(status=new_status)

        messages.success(request, f'{updated} task(s) updated to "{valid_statuses[new_status]}".')
        return redirect('projects:project_detail', pk=project_id)


# Apply Template view
class ProjectApplyTemplateView(AdminRequiredMixin, View):
    def post(self, request, project_id):
        project = get_object_or_404(Project, pk=project_id)
        template_id = request.POST.get('template_id')
        if not template_id:
            messages.error(request, 'No template selected.')
            return redirect('projects:project_detail', pk=project_id)

        template = get_object_or_404(TaskTemplate, pk=template_id)

        # #9 — Server-side guard: block silent task wipe.
        # Existing tasks must be explicitly confirmed before deletion via force=true param.
        existing_count = ProjectTask.objects.filter(project=project).count()
        if existing_count > 0:
            force = request.POST.get('force') == 'true'
            if not force:
                messages.error(
                    request,
                    f'This project already has {existing_count} task(s). '
                    'Check the "Replace existing tasks" box to confirm you want to delete them and apply the new template.'
                )
                return redirect('projects:project_detail', pk=project_id)
            # force=true confirmed: delete existing tasks before applying template
            ProjectTask.objects.filter(project=project).delete()

        # Sequentially schedule tasks starting from project.start_date
        current_start = project.start_date
        for item in template.items.all().order_by('order'):
            duration = item.default_duration_days or 1
            planned_finish = current_start + timedelta(days=duration - 1)
            ProjectTask.objects.create(
                project=project,
                order=item.order,
                activity=item.activity,
                responsible_person=None,
                planned_start=current_start,
                planned_finish=planned_finish,
                duration_days=duration,
                status='Not Started'
            )
            current_start = planned_finish + timedelta(days=1)

        messages.success(request, f'Template "{template.name}" applied successfully.')
        return redirect('projects:project_detail', pk=project_id)

# Inline HTMX Task Status Update
class ProjectTaskUpdateStatusView(AdminRequiredMixin, View):
    def post(self, request, pk):
        task = get_object_or_404(ProjectTask, pk=pk)
        status = request.POST.get('status')
        if status in dict(ProjectTask.STATUS_CHOICES):
            task.status = status
            task.save()
        return render(request, 'projects/partials/task_status_dropdown.html', {'task': task})


class DailyProgressLogCreateView(AdminRequiredMixin, CreateView):
    model = DailyProgressLog
    form_class = DailyProgressLogForm
    template_name = 'projects/log_form.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['project'] = get_object_or_404(Project, pk=self.kwargs['project_id'])
        return context

    def form_valid(self, form):
        project = get_object_or_404(Project, pk=self.kwargs['project_id'])
        form.instance.project = project
        form.instance.logged_by = self.request.user
        messages.success(self.request, 'Daily progress log added successfully.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('projects:project_detail', kwargs={'pk': self.kwargs['project_id']})


class DailyProgressLogUpdateView(AdminRequiredMixin, UpdateView):
    # TODO: branch-scoping deferred — depends on Role/Permission system (see separate RBAC work)
    model = DailyProgressLog
    form_class = DailyProgressLogForm
    template_name = 'projects/log_form.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['project'] = self.object.project
        return context

    def form_valid(self, form):
        messages.success(self.request, 'Daily progress log updated successfully.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('projects:project_detail', kwargs={'pk': self.object.project.pk})


class DailyProgressLogDeleteView(AdminRequiredMixin, View):
    def post(self, request, pk):
        # TODO: branch-scoping deferred — depends on Role/Permission system (see separate RBAC work)
        log = get_object_or_404(DailyProgressLog, pk=pk)
        project_pk = log.project.pk
        log.delete()
        messages.success(request, 'Daily progress log deleted successfully.')
        return redirect('projects:project_detail', pk=project_pk)


class ManpowerDeploymentCreateView(AdminRequiredMixin, CreateView):
    model = ManpowerDeployment
    form_class = ManpowerDeploymentForm
    template_name = 'projects/manpower_form.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['project'] = get_object_or_404(Project, pk=self.kwargs['project_id'])
        return context

    def form_valid(self, form):
        project = get_object_or_404(Project, pk=self.kwargs['project_id'])
        form.instance.project = project
        messages.success(self.request, 'Manpower requirement added successfully.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('projects:project_detail', kwargs={'pk': self.kwargs['project_id']})


class ManpowerDeploymentUpdateView(AdminRequiredMixin, UpdateView):
    # TODO: branch-scoping deferred — depends on Role/Permission system (see separate RBAC work)
    model = ManpowerDeployment
    form_class = ManpowerDeploymentForm
    template_name = 'projects/manpower_form.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['project'] = self.object.project
        return context

    def form_valid(self, form):
        messages.success(self.request, 'Manpower log updated successfully.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('projects:project_detail', kwargs={'pk': self.object.project.pk})


class ManpowerDeploymentDeleteView(AdminRequiredMixin, View):
    def post(self, request, pk):
        # TODO: branch-scoping deferred — depends on Role/Permission system (see separate RBAC work)
        deployment = get_object_or_404(ManpowerDeployment, pk=pk)
        project_pk = deployment.project.pk
        deployment.delete()
        messages.success(request, 'Manpower log deleted successfully.')
        return redirect('projects:project_detail', pk=project_pk)


class ManpowerDeploymentAutoFillView(AdminRequiredMixin, View):
    def post(self, request, pk):
        from apps.attendance.models import Attendance
        deployment = get_object_or_404(ManpowerDeployment, pk=pk)
        
        count = Attendance.objects.filter(
            project=deployment.project,
            date=deployment.date,
            employee__designation=deployment.trade
        ).values('employee').distinct().count()

        deployment.present_count = count
        deployment.save()
        messages.success(request, f"Auto-filled attendance for {deployment.trade} on {deployment.date.strftime('%b %d, %Y')}: {count} present.")
        return redirect('projects:project_detail', pk=deployment.project.pk)


class ProjectMaterialCreateView(AdminRequiredMixin, CreateView):
    model = ProjectMaterial
    form_class = ProjectMaterialForm
    template_name = 'projects/material_form.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['project'] = get_object_or_404(Project, pk=self.kwargs['project_id'])
        return context

    def form_valid(self, form):
        project = get_object_or_404(Project, pk=self.kwargs['project_id'])
        form.instance.project = project
        messages.success(self.request, 'Project material added successfully.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('projects:project_detail', kwargs={'pk': self.kwargs['project_id']})


class ProjectMaterialUpdateView(AdminRequiredMixin, UpdateView):
    # TODO: branch-scoping deferred — depends on Role/Permission system (see separate RBAC work)
    model = ProjectMaterial
    form_class = ProjectMaterialForm
    template_name = 'projects/material_form.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['project'] = self.object.project
        return context

    def form_valid(self, form):
        messages.success(self.request, 'Project material updated successfully.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('projects:project_detail', kwargs={'pk': self.object.project.pk})


class ProjectMaterialDeleteView(AdminRequiredMixin, View):
    def post(self, request, pk):
        # TODO: branch-scoping deferred — depends on Role/Permission system (see separate RBAC work)
        material = get_object_or_404(ProjectMaterial, pk=pk)
        project_pk = material.project.pk
        material.delete()
        messages.success(request, 'Project material deleted successfully.')
        return redirect('projects:project_detail', pk=project_pk)


class ProjectConfirmSignOffView(AdminRequiredMixin, View):
    def post(self, request, project_id):
        project = get_object_or_404(Project, pk=project_id)
        sign_off, _ = ProjectSignOff.objects.get_or_create(project=project)
        
        role = request.POST.get('role')
        name = request.POST.get('name', '').strip()
        
        if not role or role not in ['project_manager', 'site_engineer', 'consultant', 'client_representative']:
            messages.error(request, "Invalid sign-off role specified.")
            return redirect('projects:project_detail', pk=project_id)
            
        if not name:
            messages.error(request, "Please provide a name for the sign-off.")
            return redirect('projects:project_detail', pk=project_id)
            
        from django.utils import timezone
        if role == 'project_manager':
            sign_off.project_manager_name = name
            sign_off.project_manager_signed_at = timezone.now()
        elif role == 'site_engineer':
            sign_off.site_engineer_name = name
            sign_off.site_engineer_signed_at = timezone.now()
        elif role == 'consultant':
            sign_off.consultant_name = name
            sign_off.consultant_signed_at = timezone.now()
        elif role == 'client_representative':
            sign_off.client_representative_name = name
            sign_off.client_representative_signed_at = timezone.now()
            
        sign_off.save()
        messages.success(request, f"Successfully signed off as {role.replace('_', ' ').title()}.")
        return redirect('projects:project_detail', pk=project_id)


class ProjectExportPDFView(AdminRequiredMixin, View):
    def get(self, request, project_id):
        from django.http import HttpResponse
        from django.utils import timezone
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        
        from django.db.models import Prefetch
        
        project = get_object_or_404(
            # #16 — also prefetch project_type for PDF to avoid extra query
            Project.objects.select_related('branch', 'sign_off', 'project_type')
            .prefetch_related(
                # #14 — tasks pre-fetched here; iterate project.tasks.all() below to use this cache
                Prefetch('tasks', queryset=ProjectTask.objects.select_related('responsible_person').order_by('order')),
                Prefetch('progress_logs', queryset=DailyProgressLog.objects.order_by('-date')),
                Prefetch('manpower_logs', queryset=ManpowerDeployment.objects.order_by('-date', 'trade')),
                Prefetch('materials', queryset=ProjectMaterial.objects.order_by('material_name'))
            ),
            pk=project_id
        )
        
        try:
            sign_off = project.sign_off
        except ProjectSignOff.DoesNotExist:
            sign_off = ProjectSignOff.objects.create(project=project)
        
        response = HttpResponse(content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="work_plan_sheet_{project.id}_{timezone.localdate()}.pdf"'
        
        # Build Document
        doc = SimpleDocTemplate(
            response,
            pagesize=A4,
            rightMargin=30,
            leftMargin=30,
            topMargin=30,
            bottomMargin=30,
            pageCompression=0,
        )
        
        styles = getSampleStyleSheet()
        elements = []
        
        # Title/Header Styles
        title_style = ParagraphStyle(
            'TitleStyle',
            parent=styles['Heading1'],
            fontName='Helvetica-Bold',
            fontSize=16,
            leading=18,
            textColor=colors.HexColor('#111827'),
            alignment=1  # Center
        )
        
        section_style = ParagraphStyle(
            'SectionStyle',
            parent=styles['Heading2'],
            fontName='Helvetica-Bold',
            fontSize=12,
            leading=14,
            textColor=colors.HexColor('#1F2937'),
            spaceBefore=12,
            spaceAfter=6
        )
        
        body_style = ParagraphStyle(
            'BodyStyle',
            parent=styles['Normal'],
            fontName='Helvetica',
            fontSize=8,
            leading=10,
            textColor=colors.HexColor('#374151')
        )
        
        body_bold = ParagraphStyle(
            'BodyBold',
            parent=body_style,
            fontName='Helvetica-Bold'
        )
        
        cell_style = ParagraphStyle(
            'CellCenter',
            parent=body_style,
            alignment=1
        )
        
        cell_left = ParagraphStyle(
            'CellLeft',
            parent=body_style,
            alignment=0
        )
        
        header_cell = ParagraphStyle(
            'HeaderCenter',
            parent=body_style,
            fontName='Helvetica-Bold',
            textColor=colors.HexColor('#374151'),
            alignment=1
        )
        
        header_cell_left = ParagraphStyle(
            'HeaderLeft',
            parent=body_style,
            fontName='Helvetica-Bold',
            textColor=colors.HexColor('#374151'),
            alignment=0
        )
        
        # 1. Main Title — #6: use project_type.name instead of hardcoded 'HVAC'
        pdf_title = f"{project.project_type.name.upper()} PROJECT WORK PLAN SHEET"
        elements.append(Paragraph(pdf_title, title_style))
        elements.append(Paragraph(f"Generated: {timezone.localtime(timezone.now()).strftime('%d %b %Y, %I:%M %p')}", cell_style))
        elements.append(Spacer(1, 15))
        
        # 2. Project Information Section
        elements.append(Paragraph("Project Details", section_style))
        info_data = [
            [Paragraph("Project Name:", body_bold), Paragraph(project.name, body_style),
             Paragraph("Client Name:", body_bold), Paragraph(project.client_name, body_style)],
            [Paragraph("Location:", body_bold), Paragraph(project.location, body_style),
             # #6: render '\u2014' when system_type is null/empty instead of blank
             Paragraph("System Type:", body_bold), Paragraph(project.system_type or "\u2014", body_style)],
            [Paragraph("Start Date:", body_bold), Paragraph(str(project.start_date), body_style),
             Paragraph("Branch:", body_bold), Paragraph(project.branch.name if project.branch else "—", body_style)]
        ]
        info_table = Table(info_data, colWidths=[90, 175, 90, 175])
        info_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F9FAFB')),
            ('PADDING', (0, 0), (-1, -1), 6),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E5E7EB')),
        ]))
        elements.append(info_table)
        elements.append(Spacer(1, 10))
        
        # 3. Task Checklist Table
        elements.append(Paragraph("Project Task Checklist", section_style))
        task_headers = [
            Paragraph("SN", header_cell),
            Paragraph("Activity", header_cell_left),
            Paragraph("Responsible", header_cell_left),
            Paragraph("Start", header_cell),
            Paragraph("Finish", header_cell),
            Paragraph("Status", header_cell),
            Paragraph("Remarks", header_cell_left),
        ]
        task_data = [task_headers]
        for idx, task in enumerate(project.tasks.all(), 1):
            task_data.append([
                Paragraph(str(idx), cell_style),
                Paragraph(task.activity, cell_left),
                Paragraph(task.responsible_person.full_name if task.responsible_person else "—", cell_left),
                Paragraph(str(task.planned_start) if task.planned_start else "—", cell_style),
                Paragraph(str(task.planned_finish) if task.planned_finish else "—", cell_style),
                Paragraph(task.get_status_display(), cell_style),
                Paragraph(task.remarks or "—", cell_left),
            ])
        task_table = Table(task_data, colWidths=[25, 140, 95, 55, 55, 60, 100], repeatRows=1)
        task_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F3F4F6')),
            ('PADDING', (0, 0), (-1, -1), 5),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E5E7EB')),
        ]))
        elements.append(task_table)
        elements.append(Spacer(1, 10))
        
        # 4. Daily Progress Summary
        elements.append(Paragraph("Daily Progress Logs", section_style))
        prog_headers = [
            Paragraph("Date", header_cell),
            Paragraph("Supervisor", header_cell_left),
            Paragraph("Manpower", header_cell),
            Paragraph("Planned Work", header_cell_left),
            Paragraph("Completed Work", header_cell_left),
            Paragraph("Delay Reason", header_cell_left),
        ]
        prog_data = [prog_headers]
        for log in project.progress_logs.all():
            prog_data.append([
                Paragraph(str(log.date), cell_style),
                Paragraph(log.supervisor_name, cell_left),
                Paragraph(str(log.manpower_count or "—"), cell_style),
                Paragraph(log.planned_work, cell_left),
                Paragraph(log.completed_work, cell_left),
                Paragraph(log.delay_reason or "—", cell_left),
            ])
        prog_table = Table(prog_data, colWidths=[60, 85, 55, 120, 120, 90], repeatRows=1)
        prog_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F3F4F6')),
            ('PADDING', (0, 0), (-1, -1), 5),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E5E7EB')),
        ]))
        elements.append(prog_table)
        elements.append(Spacer(1, 10))
        
        # 5. Manpower Deployment
        elements.append(Paragraph("Manpower Deployment Requirements", section_style))
        man_headers = [
            Paragraph("Date", header_cell),
            Paragraph("Trade", header_cell_left),
            Paragraph("Required Count", header_cell),
            Paragraph("Present Count", header_cell),
        ]
        man_data = [man_headers]
        for log in project.manpower_logs.all():
            man_data.append([
                Paragraph(str(log.date), cell_style),
                Paragraph(log.trade, cell_left),
                Paragraph(str(log.required_count), cell_style),
                Paragraph(str(log.present_count if log.present_count is not None else "—"), cell_style),
            ])
        man_table = Table(man_data, colWidths=[70, 200, 130, 130], repeatRows=1)
        man_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F3F4F6')),
            ('PADDING', (0, 0), (-1, -1), 5),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E5E7EB')),
        ]))
        elements.append(man_table)
        elements.append(Spacer(1, 10))
        
        # 6. Material Tracking
        elements.append(Paragraph("Material Tracking", section_style))
        mat_headers = [
            Paragraph("Material Name", header_cell_left),
            Paragraph("Unit", header_cell),
            Paragraph("Required Qty", header_cell),
            Paragraph("Received Qty", header_cell),
            Paragraph("Balance Qty", header_cell),
            Paragraph("Remarks", header_cell_left),
        ]
        mat_data = [mat_headers]
        for log in project.materials.all():
            mat_data.append([
                Paragraph(log.material_name, cell_left),
                Paragraph(log.unit, cell_style),
                Paragraph(str(log.required_qty), cell_style),
                Paragraph(str(log.received_qty), cell_style),
                Paragraph(str(log.balance), cell_style),
                Paragraph(log.remarks or "—", cell_left),
            ])
        mat_table = Table(mat_data, colWidths=[150, 50, 80, 80, 80, 90], repeatRows=1)
        mat_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F3F4F6')),
            ('PADDING', (0, 0), (-1, -1), 5),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E5E7EB')),
        ]))
        elements.append(mat_table)
        elements.append(Spacer(1, 15))
        
        # 7. Sign-off Block
        elements.append(Paragraph("Project Work Plan Sign-off", section_style))
        
        def get_sign_str(name, signed_at):
            if signed_at:
                return f"{name}<br/>Signed: {timezone.localtime(signed_at).strftime('%d %b %Y, %I:%M %p')}"
            return "Pending Sign-off"
            
        sign_data = [
            [
                Paragraph("<b>Project Manager</b>", cell_style),
                Paragraph("<b>Site Engineer</b>", cell_style),
                Paragraph("<b>Consultant</b>", cell_style),
                Paragraph("<b>Client Representative</b>", cell_style)
            ],
            [
                Paragraph(get_sign_str(sign_off.project_manager_name, sign_off.project_manager_signed_at), cell_style),
                Paragraph(get_sign_str(sign_off.site_engineer_name, sign_off.site_engineer_signed_at), cell_style),
                Paragraph(get_sign_str(sign_off.consultant_name, sign_off.consultant_signed_at), cell_style),
                Paragraph(get_sign_str(sign_off.client_representative_name, sign_off.client_representative_signed_at), cell_style)
            ]
        ]
        sign_table = Table(sign_data, colWidths=[132, 132, 132, 132])
        sign_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F3F4F6')),
            ('PADDING', (0, 0), (-1, -1), 8),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#D1D5DB')),
        ]))
        elements.append(sign_table)
        
        doc.build(elements)
        return response


class ProjectMaterialIncrementView(AdminRequiredMixin, View):
    def post(self, request, pk):
        material = get_object_or_404(ProjectMaterial, pk=pk)
        increment_qty = request.POST.get('increment_qty')
        if increment_qty:
            try:
                from decimal import Decimal
                qty = Decimal(increment_qty)
                if qty > 0:
                    material.received_qty += qty
                    material.save()
                    messages.success(request, f"Added {qty} {material.unit} to {material.material_name}.")
                else:
                    messages.error(request, "Increment quantity must be positive.")
            except Exception:
                messages.error(request, "Invalid increment quantity.")
        else:
            messages.error(request, "No increment quantity provided.")
        return redirect('projects:project_detail', pk=material.project.pk)


class ProjectTypeListView(AdminRequiredMixin, ListView):
    model = ProjectType
    template_name = 'projects/project_type_list.html'
    context_object_name = 'project_types'
    ordering = ['name']
    paginate_by = 10

    def get_queryset(self):
        queryset = super().get_queryset()
        search_query = self.request.GET.get('search', '')
        if search_query:
            queryset = queryset.filter(name__icontains=search_query)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search'] = self.request.GET.get('search', '')
        return context


class ProjectTypeCreateView(AdminRequiredMixin, CreateView):
    model = ProjectType
    form_class = ProjectTypeForm
    template_name = 'projects/project_type_form.html'
    success_url = reverse_lazy('projects:project_type_list')

    def form_valid(self, form):
        messages.success(self.request, 'Project type created successfully.')
        return super().form_valid(form)


class ProjectTypeUpdateView(AdminRequiredMixin, UpdateView):
    model = ProjectType
    form_class = ProjectTypeForm
    template_name = 'projects/project_type_form.html'
    success_url = reverse_lazy('projects:project_type_list')

    def form_valid(self, form):
        messages.success(self.request, 'Project type updated successfully.')
        return super().form_valid(form)


class ProjectTypeDeleteView(AdminRequiredMixin, DeleteView):
    model = ProjectType
    success_url = reverse_lazy('projects:project_type_list')

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        if self.object.projects.exists():
            messages.error(request, 'Cannot delete this project type because it is referenced by existing projects.')
            return redirect('projects:project_type_list')
        messages.success(request, 'Project type deleted successfully.')
        return super().post(request, *args, **kwargs)


class ExportProjectTasksCSVView(AdminRequiredMixin, View):
    def get(self, request, pk):
        project = get_object_or_404(Project, pk=pk)
        tasks = project.tasks.select_related('responsible_person').all().order_by('order')
        
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="project_{project.id}_tasks.csv"'
        
        writer = csv.writer(response)
        writer.writerow(['Order', 'Activity', 'Responsible Person', 'Planned Start', 'Planned Finish', 'Duration (Days)', 'Status', 'Remarks'])
        
        for task in tasks:
            resp_name = task.responsible_person.full_name if task.responsible_person else '-'
            writer.writerow([
                task.order,
                task.activity,
                resp_name,
                task.planned_start or '-',
                task.planned_finish or '-',
                task.duration_days or '-',
                task.status,
                task.remarks or ''
            ])
            
        return response

class ExportProjectManpowerCSVView(AdminRequiredMixin, View):
    def get(self, request, pk):
        project = get_object_or_404(Project, pk=pk)
        logs = project.manpower_logs.all().order_by('-date', 'trade')
        
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="project_{project.id}_manpower.csv"'
        
        writer = csv.writer(response)
        writer.writerow(['Date', 'Trade', 'Required Count', 'Present Count'])
        
        for log in logs:
            writer.writerow([
                log.date,
                log.trade,
                log.required_count,
                log.present_count if log.present_count is not None else '-'
            ])
            
        return response

class ExportProjectMaterialsCSVView(AdminRequiredMixin, View):
    def get(self, request, pk):
        project = get_object_or_404(Project, pk=pk)
        materials = project.materials.all().order_by('material_name')
        
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="project_{project.id}_materials.csv"'
        
        writer = csv.writer(response)
        writer.writerow(['Material Name', 'Unit', 'Required Qty', 'Received Qty', 'Balance', 'Remarks'])
        
        for mat in materials:
            writer.writerow([
                mat.material_name,
                mat.unit,
                mat.required_qty,
                mat.received_qty,
                mat.balance,
                mat.remarks or ''
            ])
            
        return response


class ProjectTaskShiftSubsequentView(AdminRequiredMixin, View):
    def post(self, request, pk):
        try:
            task = get_object_or_404(ProjectTask, pk=pk)
            old_finish = task.planned_finish
            
            # Explicit confirmation param check
            confirm_shift = request.POST.get('confirm_shift') == 'true'
            
            form = ProjectTaskForm(request.POST, instance=task)
            if form.is_valid():
                new_finish = form.cleaned_data.get('planned_finish')
                
                # Save the task
                task = form.save()
                
                if confirm_shift and old_finish and new_finish and new_finish > old_finish:
                    delta = (new_finish - old_finish).days
                    if delta > 0:
                        subsequent_tasks = ProjectTask.objects.filter(
                            project=task.project,
                            order__gt=task.order
                        )
                        for t in subsequent_tasks:
                            if t.planned_start:
                                t.planned_start += timedelta(days=delta)
                            if t.planned_finish:
                                t.planned_finish += timedelta(days=delta)
                            t.save()
                        messages.success(request, f'Task updated and shifted subsequent tasks by {delta} days.')
                    else:
                        messages.success(request, 'Task updated successfully.')
                else:
                    messages.success(request, 'Task updated successfully.')
            else:
                messages.error(request, 'Invalid form data.')
                
            return redirect('projects:project_detail', pk=task.project.pk)
        except Exception as e:
            import traceback
            traceback.print_exc()
            raise e


class ProjectRequestSignOffView(AdminRequiredMixin, View):
    def post(self, request, project_id):
        project = get_object_or_404(Project, pk=project_id)
        role = request.POST.get('role')
        
        if role == 'consultant':
            email = project.consultant_email
            name = project.consultant
            stakeholder_type = 'Consultant'
        elif role == 'client_representative':
            email = project.client_email
            name = project.client_name
            stakeholder_type = 'Client Representative'
        else:
            messages.error(request, "Invalid sign-off role specified.")
            return redirect('projects:project_detail', pk=project_id)
            
        if not email:
            messages.error(request, f"No email address on file for {stakeholder_type}.")
            return redirect('projects:project_detail', pk=project_id)
            
        # Dispatch request email
        from apps.notifications.dispatch import send_email_notification
        from django.urls import reverse
        
        subject = f"Sign-off Requested: {project.name}"
        detail_url = request.build_absolute_uri(reverse('projects:project_detail', kwargs={'pk': project.pk}))
        message = (
            f"Hello {name or stakeholder_type},\n\n"
            f"You are requested to sign off on the work plan sheet for project '{project.name}'.\n"
            f"Please visit the project page to view and sign off:\n{detail_url}\n\n"
            f"Regards,\nFieldTrack System"
        )
        
        success = send_email_notification(email, subject, message)
        if success:
            messages.success(request, f"Sign-off request email sent to {stakeholder_type} ({email}).")
        else:
            messages.error(request, f"Failed to send sign-off request email to {stakeholder_type} ({email}).")
            
        return redirect('projects:project_detail', pk=project_id)


class GlobalTaskListView(RoleRequiredMixin, ListView):
    allowed_roles = ['admin', 'manager']
    model = ProjectTask
    template_name = 'projects/global_task_list.html'
    context_object_name = 'tasks'
    
    def get_queryset(self):
        # Optimized from the start using select_related
        qs = ProjectTask.objects.select_related('project', 'responsible_person').all().order_by('project__name', 'order')
        
        # Apply filters
        employee_id = self.request.GET.get('employee')
        project_id = self.request.GET.get('project')
        status = self.request.GET.get('status')
        date_start = self.request.GET.get('date_start')
        date_end = self.request.GET.get('date_end')
        
        if employee_id:
            qs = qs.filter(responsible_person_id=employee_id)
        if project_id:
            qs = qs.filter(project_id=project_id)
        if status:
            qs = qs.filter(status=status)
        if date_start:
            qs = qs.filter(planned_start__gte=date_start)
        if date_end:
            qs = qs.filter(planned_finish__lte=date_end)
            
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Pass lists for filters
        context['employees'] = EmployeeProfile.objects.all().order_by('full_name')
        context['projects'] = Project.objects.all().order_by('name')
        context['statuses'] = ProjectTask.STATUS_CHOICES
        
        # Preserve filter selections
        context['selected_employee'] = self.request.GET.get('employee', '')
        context['selected_project'] = self.request.GET.get('project', '')
        context['selected_status'] = self.request.GET.get('status', '')
        context['selected_date_start'] = self.request.GET.get('date_start', '')
        context['selected_date_end'] = self.request.GET.get('date_end', '')
        return context


class GlobalTaskCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = ['admin', 'manager']
    model = ProjectTask
    form_class = GlobalProjectTaskForm
    template_name = 'projects/global_task_form.html'
    success_url = reverse_lazy('projects:global_task_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        task = self.object
        # Notify newly assigned employee
        if task.responsible_person and task.responsible_person.user:
            Notification.objects.create(
                recipient=task.responsible_person.user,
                employee=task.responsible_person,
                title=f"New Task Assigned: {task.activity}",
                message=f"You have been assigned to task '{task.activity}' for project '{task.project.name}'.",
                notif_type='field_visit'
            )
            subject = f"New Task Assigned: {task.activity}"
            message = (
                f"Hello {task.responsible_person.full_name},\n\n"
                f"You have been assigned to the following task in project '{task.project.name}':\n"
                f"Task: {task.activity}\n"
                f"Planned: {task.planned_start or '—'} to {task.planned_finish or '—'}\n"
                f"Status: {task.status}\n\n"
            )
            if task.assignment_attachment:
                message += f"See attached reference file: {task.assignment_attachment.url}\n\n"
            message += "Regards,\nFieldTrack System"
            send_email_notification(task.responsible_person.user, subject, message)

        messages.success(self.request, 'Task created successfully.')
        return response


from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from apps.staff.views import check_staff_role
import json

@login_required
@require_POST
def staff_task_complete(request, pk):
    if not check_staff_role(request.user):
        return JsonResponse({'error': 'Unauthorized'}, status=403)
    
    task = get_object_or_404(ProjectTask, pk=pk)
    
    # Verify request.user's linked EmployeeProfile == task.responsible_person
    employee = getattr(request.user, 'employee_profile', None)
    if not employee or task.responsible_person != employee:
        return JsonResponse({'error': 'You are not assigned to this task.'}, status=403)
        
    note = ""
    if request.content_type == 'application/json':
        try:
            data = json.loads(request.body)
            note = data.get('note', '')
        except json.JSONDecodeError:
            pass
    else:
        note = request.POST.get('note', '')
        
    completion_attachment = request.FILES.get('completion_attachment')
    if completion_attachment:
        from django.core.exceptions import ValidationError
        from apps.projects.models import validate_task_attachment
        try:
            validate_task_attachment(completion_attachment)
        except ValidationError as e:
            return JsonResponse({'error': e.message}, status=400)
        task.completion_attachment = completion_attachment

    task.employee_note = note
    task.status = 'Completed'
    task.save()
    
    return JsonResponse({
        'success': True,
        'progress_percent': task.project.progress_percent,
        'completion_attachment_url': task.completion_attachment.url if task.completion_attachment else None
    })






