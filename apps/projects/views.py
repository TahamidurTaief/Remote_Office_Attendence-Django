import json
from django.http import JsonResponse
from django.core.exceptions import PermissionDenied
from django.utils import timezone
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse_lazy
from django.http import HttpResponse
import csv
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView, View
from django.contrib import messages
from apps.attendance.sync_utils import parse_and_validate_client_time
from django.db.models import Q, Prefetch
from datetime import date, timedelta
from django.contrib.auth.mixins import LoginRequiredMixin
from apps.accounts.mixins import AdminRequiredMixin, RoleRequiredMixin
from apps.accounts.engine import PermissionEngine
from apps.branches.models import Branch
from apps.employees.models import EmployeeProfile
from apps.notifications.models import Notification
from apps.notifications.dispatch import send_email_notification
from .models import (
    Project, ProjectType, TaskTemplate, TaskTemplateItem, ProjectTask,
    DailyProgressLog, ManpowerDeployment, ProjectMaterial, ProjectSignOff,
    TaskDependency, GanttImportBatch
)
from .forms import ProjectForm, ProjectTypeForm, TaskTemplateForm, TaskTemplateItemForm, ProjectTaskForm, DailyProgressLogForm, ManpowerDeploymentForm, ProjectMaterialForm, GlobalProjectTaskForm, TaskDependencyForm
from apps.projects.services.gantt_import import (
    WorkbookSafetyValidator,
    GanttWorkbookParser,
    GanttDuplicateDetector,
    GanttImportStagingManager,
    GanttImportExecutor,
    GanttImportError,
    check_gantt_import_permission
)






class ProjectListView(AdminRequiredMixin, ListView):
    required_permission = 'projects.view'
    action_type = 'view'
    model = Project
    template_name = 'projects/project_list.html'
    context_object_name = 'projects'
    paginate_by = 10

    def get_queryset(self):
        scoped_qs = PermissionEngine.filter_by_data_scope(
            user=self.request.user,
            queryset=super().get_queryset(),
            codename='projects.view',
            branch_field='branch'
        )
        queryset = scoped_qs.select_related(
            'branch', 'project_type'
        ).prefetch_related(
            'project_managers', 'site_engineers', 'project_members'
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
        context['branches'] = PermissionEngine.filter_by_data_scope(
            user=self.request.user,
            queryset=Branch.objects.all(),
            codename='branches.view',
            branch_field='id'
        ) if not self.request.user.is_superuser else Branch.objects.all()
        context['status_choices'] = Project.STATUS_CHOICES
        return context

class ProjectDetailView(AdminRequiredMixin, DetailView):
    required_permission = 'projects.view'
    action_type = 'view'
    model = Project
    template_name = 'projects/project_detail.html'
    context_object_name = 'project'

    def get_queryset(self):
        scoped_qs = PermissionEngine.filter_by_data_scope(
            user=self.request.user,
            queryset=super().get_queryset(),
            codename='projects.view',
            branch_field='branch'
        )
        return scoped_qs.select_related(
            'branch', 'sign_off', 'project_type'
        ).prefetch_related(
            'project_managers', 'site_engineers', 'project_members'
        )

    def get_context_data(self, **kwargs):
        from django.db.models import Count
        context = super().get_context_data(**kwargs)
        tasks_qs = self.object.tasks.select_related('responsible_person').all().order_by('order')

        # G8 fix: single aggregation query for all status counts
        from django.db.models import Q as Q_
        status_counts = self.object.tasks.aggregate(
            all_count=Count('id'),
            not_started_count=Count('id', filter=Q_(status='Not Started')),
            in_progress_count=Count('id', filter=Q_(status='In Progress')),
            delayed_count=Count('id', filter=Q_(status='Delayed')),
            completed_count=Count('id', filter=Q_(status='Completed')),
        )
        context['all_count'] = status_counts['all_count']
        context['not_started_count'] = status_counts['not_started_count']
        context['in_progress_count'] = status_counts['in_progress_count']
        context['delayed_count'] = status_counts['delayed_count']
        context['completed_count'] = status_counts['completed_count']

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

        from django.contrib.contenttypes.models import ContentType
        from apps.notifications.models import ActivityLog
        task_ids = self.object.tasks.values_list('id', flat=True)
        ct_task = ContentType.objects.get_for_model(ProjectTask)
        context['activities'] = ActivityLog.objects.filter(
            target_content_type=ct_task,
            target_object_id__in=task_ids
        ).select_related('actor', 'actor__employee_profile').order_by('-created_at')[:20]

        return context




class ProjectCreateView(AdminRequiredMixin, CreateView):
    required_permission = 'projects.add'
    action_type = 'add'
    model = Project
    form_class = ProjectForm
    template_name = 'projects/project_form.html'
    success_url = reverse_lazy('projects:project_list')

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        eval_res = PermissionEngine.evaluate(request.user, 'projects.add', action_type='add')
        if not eval_res.allowed:
            return self.handle_no_permission()
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        response = super().form_valid(form)
        
        task_template = form.cleaned_data.get('task_template')
        if task_template:
            from datetime import timedelta
            from .models import ProjectTask
            current_start = self.object.start_date
            for item in task_template.items.all().order_by('order'):
                duration = item.default_duration_days or 1
                planned_finish = current_start + timedelta(days=duration - 1)
                ProjectTask.objects.create(
                    project=self.object,
                    order=item.order,
                    activity=item.activity,
                    responsible_person=None,
                    planned_start=current_start,
                    planned_finish=planned_finish,
                    duration_days=duration,
                    status='Not Started'
                )
                current_start = planned_finish + timedelta(days=1)
            messages.success(self.request, f'Project created and tasks initialized from template "{task_template.name}".')
        else:
            messages.success(self.request, 'Project created successfully.')
        return response

class ProjectUpdateView(AdminRequiredMixin, UpdateView):
    required_permission = 'projects.edit'
    action_type = 'edit'
    model = Project
    form_class = ProjectForm
    template_name = 'projects/project_form.html'
    success_url = reverse_lazy('projects:project_list')

    def get_queryset(self):
        return PermissionEngine.filter_by_data_scope(
            user=self.request.user,
            queryset=super().get_queryset(),
            codename='projects.update',
            branch_field='branch'
        )

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        eval_res = PermissionEngine.evaluate(request.user, 'projects.update', action_type='update')
        if not eval_res.allowed:
            return self.handle_no_permission()
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from apps.employees.models import EmployeeProfile
        from .models import TaskTemplate
        from django.db.models import Count
        # Tasks list for the task management section
        context['tasks'] = self.object.tasks.select_related('responsible_person').order_by('order')
        context['task_form'] = ProjectTaskForm()
        context['employees'] = EmployeeProfile.objects.filter(is_active=True).order_by('full_name')
        context['templates'] = TaskTemplate.objects.annotate(items_count=Count('items')).order_by('name')
        return context

    def form_valid(self, form):
        messages.success(self.request, 'Project updated successfully.')
        return super().form_valid(form)



class ProjectDeleteView(AdminRequiredMixin, View):
    required_permission = 'projects.delete'
    action_type = 'delete'
    def post(self, request, pk):
        eval_res = PermissionEngine.evaluate(request.user, 'projects.delete', action_type='delete')
        if not eval_res.allowed:
            raise PermissionDenied("You do not have permission to delete projects.")
        project = PermissionEngine.get_scoped_object_or_404(
            Project,
            user=request.user,
            codename='projects.delete',
            pk=pk,
            action_type='delete',
            branch_field='branch'
        )
        project_name = project.name
        project.delete()
        messages.success(request, f'Project "{project_name}" was successfully deleted.')
        return redirect('projects:project_list')

class TaskTemplateListView(AdminRequiredMixin, ListView):
    required_permission = 'projects.view'
    action_type = 'view'
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
    required_permission = 'projects.add'
    action_type = 'add'
    model = TaskTemplate
    form_class = TaskTemplateForm
    template_name = 'projects/template_form.html'
    success_url = reverse_lazy('projects:template_list')

    def form_valid(self, form):
        messages.success(self.request, 'Task template created successfully.')
        return super().form_valid(form)

class TaskTemplateUpdateView(AdminRequiredMixin, UpdateView):
    required_permission = 'projects.edit'
    action_type = 'edit'
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
    required_permission = 'projects.delete'
    action_type = 'delete'
    def post(self, request, pk):
        template = get_object_or_404(TaskTemplate, pk=pk)
        template_name = template.name
        template.delete()
        messages.success(request, f'Task template "{template_name}" was successfully deleted.')
        return redirect('projects:template_list')

# Template Item endpoints
class TemplateAddItemView(AdminRequiredMixin, View):
    required_permission = 'projects.edit'
    action_type = 'edit'
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
    required_permission = 'projects.edit'
    action_type = 'edit'
    def post(self, request, pk):
        item = get_object_or_404(TaskTemplateItem, pk=pk)
        template_pk = item.template.pk
        item.delete()
        messages.success(request, 'Template item deleted successfully.')
        return redirect('projects:template_edit', pk=template_pk)

class TemplateEditItemView(AdminRequiredMixin, UpdateView):
    required_permission = 'projects.edit'
    action_type = 'edit'
    model = TaskTemplateItem
    form_class = TaskTemplateItemForm
    template_name = 'projects/template_form.html'  # default fallback

    def get_template_names(self):
        if self.request.headers.get('HX-Request') == 'true':
            return ['projects/partials/edit_item_drawer.html']
        return [self.template_name]

    def form_valid(self, form):
        messages.success(self.request, 'Template item updated successfully.')
        self.object = form.save()
        if self.request.headers.get('HX-Request') == 'true':
            from django.http import HttpResponse
            return HttpResponse('<script>window.location.reload();</script>')
        return redirect('projects:template_edit', pk=self.object.template.pk)

# ProjectTask CRUD
class ProjectTaskCreateView(AdminRequiredMixin, CreateView):
    required_permission = 'projects.add'
    action_type = 'add'
    model = ProjectTask
    form_class = ProjectTaskForm
    template_name = 'projects/task_form.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['project'] = get_object_or_404(Project, pk=self.kwargs['project_id'])
        return context

    def post(self, request, *args, **kwargs):
        attachments = request.FILES.getlist('assignment_attachments')
        from django.core.exceptions import ValidationError
        from apps.projects.models import validate_task_attachment
        for attachment in attachments:
            try:
                validate_task_attachment(attachment)
            except ValidationError as e:
                form = self.get_form()
                form.add_error(None, f"File validation failed for {attachment.name}: {e.message}")
                return self.form_invalid(form)
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        project = get_object_or_404(Project, pk=self.kwargs['project_id'])
        form.instance.project = project
        response = super().form_valid(form)
        
        # Notify assigned employee
        task = self.object
        attachments = self.request.FILES.getlist('assignment_attachments')
        if attachments:
            from apps.projects.models import TaskAttachment
            for index, attachment in enumerate(attachments):
                if index == 0 and not task.assignment_attachment:
                    task.assignment_attachment = attachment
                    task.save(update_fields=['assignment_attachment'])
                TaskAttachment.objects.create(
                    task=task,
                    file=attachment,
                    attachment_type='assignment'
                )

        if task.responsible_person and task.responsible_person.user:
            from apps.notifications.dispatch import log_activity
            subject = f"New Task Assigned: {task.activity}"
            notif_msg = f"You have been assigned to task '{task.activity}' for project '{project.name}'."
            email_msg = (
                f"Hello {task.responsible_person.full_name},\n\n"
                f"You have been assigned to the following task in project '{project.name}':\n"
                f"Task: {task.activity}\n"
                f"Planned: {task.planned_start or '—'} to {task.planned_finish or '—'}\n"
                f"Status: {task.status}\n\n"
            )
            if task.assignment_attachment:
                email_msg += f"See attached reference file: {task.assignment_attachment.url}\n\n"
            email_msg += "Regards,\nFieldTrack System"

            log_activity(
                actor=self.request.user,
                verb='task_assigned',
                target=task,
                metadata={
                    'title': subject,
                    'message': notif_msg,
                    'email_subject': subject,
                    'email_message': email_msg,
                    'notif_type': 'field_visit'
                },
                notify_users=[task.responsible_person.user],
                email_also=True
            )
            
        messages.success(self.request, 'Task added successfully.')
        return response

    def get_success_url(self):
        return reverse_lazy('projects:project_detail', kwargs={'pk': self.kwargs['project_id']})

class ProjectTaskUpdateView(RoleRequiredMixin, UpdateView):
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
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['project'] = self.object.project
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        attachments = request.FILES.getlist('assignment_attachments')
        from django.core.exceptions import ValidationError
        from apps.projects.models import validate_task_attachment
        for attachment in attachments:
            try:
                validate_task_attachment(attachment)
            except ValidationError as e:
                form = self.get_form()
                form.add_error(None, f"File validation failed for {attachment.name}: {e.message}")
                return self.form_invalid(form)
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        old_task = ProjectTask.objects.get(pk=self.get_object().pk)
        old_resp = old_task.responsible_person
        response = super().form_valid(form)
        
        new_task = self.object
        attachments = self.request.FILES.getlist('assignment_attachments')
        if attachments:
            from apps.projects.models import TaskAttachment
            for index, attachment in enumerate(attachments):
                if index == 0 and not new_task.assignment_attachment:
                    new_task.assignment_attachment = attachment
                    new_task.save(update_fields=['assignment_attachment'])
                TaskAttachment.objects.create(
                    task=new_task,
                    file=attachment,
                    attachment_type='assignment'
                )

        if new_task.responsible_person and new_task.responsible_person != old_resp:
            if new_task.responsible_person.user:
                from apps.notifications.dispatch import log_activity
                subject = f"Task Assigned: {new_task.activity}"
                proj_name = new_task.project.name if new_task.project else "Standalone"
                notif_msg = f"You have been assigned to task '{new_task.activity}' for project '{proj_name}'."
                email_msg = (
                    f"Hello {new_task.responsible_person.full_name},\n\n"
                    f"You have been assigned to the following task in project '{proj_name}':\n"
                    f"Task: {new_task.activity}\n"
                    f"Planned: {new_task.planned_start or '—'} to {new_task.planned_finish or '—'}\n"
                    f"Status: {new_task.status}\n\n"
                )
                if new_task.assignment_attachment:
                    email_msg += f"See attached reference file: {new_task.assignment_attachment.url}\n\n"
                email_msg += "Regards,\nFieldTrack System"

                log_activity(
                    actor=self.request.user,
                    verb='task_assigned',
                    target=new_task,
                    metadata={
                        'title': subject,
                        'message': notif_msg,
                        'email_subject': subject,
                        'email_message': email_msg,
                        'notif_type': 'field_visit'
                    },
                    notify_users=[new_task.responsible_person.user],
                    email_also=True
                )

        messages.success(self.request, 'Task updated successfully.')
        if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest' or self.request.headers.get('HX-Request') == 'true':
            return JsonResponse({
                'success': True,
                'message': 'Task updated successfully.',
                'task': {
                    'id': new_task.id,
                    'activity': new_task.activity,
                    'status': new_task.status,
                    'responsible_person_name': new_task.responsible_person.full_name if new_task.responsible_person else 'Unassigned',
                }
            })
        return response

    def form_invalid(self, form):
        if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest' or self.request.headers.get('HX-Request') == 'true':
            errors = {}
            for field, err_list in form.errors.items():
                errors[field] = [str(e) for e in err_list]
            return JsonResponse({'success': False, 'errors': errors}, status=400)
        return super().form_invalid(form)

    def get_success_url(self):
        if self.object.project:
            return reverse_lazy('projects:project_detail', kwargs={'pk': self.object.project.pk})
        return reverse_lazy('projects:global_task_list')

class ProjectTaskDeleteView(AdminRequiredMixin, View):
    required_permission = 'projects.delete'
    action_type = 'delete'

    def post(self, request, pk):
        task = PermissionEngine.get_scoped_object_or_404(
            model_or_qs=ProjectTask,
            user=request.user,
            codename='projects.delete',
            branch_field='project__branch',
            pk=pk
        )
        project_pk = task.project.pk if task.project else None
        task_activity = task.activity
        task.delete()
        messages.success(request, f'Task "{task_activity}" deleted successfully.')
        if project_pk:
            return redirect('projects:project_detail', pk=project_pk)
        return redirect('projects:global_task_list')


class ProjectTaskReorderView(AdminRequiredMixin, View):
    required_permission = 'projects.edit'
    action_type = 'edit'
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
    required_permission = 'projects.edit'
    action_type = 'edit'
    """Bulk-update the status of multiple tasks in one POST.
    POST body: task_ids (list of pk strings), new_status / status (string)
    """
    def post(self, request, project_id=None, pk=None, *args, **kwargs):
        pid = project_id or pk or kwargs.get('project_id') or kwargs.get('pk')
        project = get_object_or_404(Project, pk=pid)
        task_ids = request.POST.getlist('task_ids')
        if not task_ids and request.POST.get('task_ids_csv'):
            task_ids = [x.strip() for x in request.POST.get('task_ids_csv').split(',') if x.strip()]

        new_status = (request.POST.get('new_status') or request.POST.get('status', '')).strip()

        if not task_ids:
            messages.error(request, 'No tasks selected.')
            return redirect('projects:project_detail', pk=pid)

        valid_statuses = dict(ProjectTask.STATUS_CHOICES)
        if new_status not in valid_statuses:
            messages.error(request, f'Invalid status: {new_status}')
            return redirect('projects:project_detail', pk=pid)

        # Restrict update to tasks that belong to this project (prevents IDOR)
        updated = ProjectTask.objects.filter(
            pk__in=task_ids, project=project
        ).update(status=new_status)

        messages.success(request, f'{updated} task(s) updated to "{valid_statuses[new_status]}".')
        return redirect('projects:project_detail', pk=pid)


class ProjectTaskBulkDeleteView(AdminRequiredMixin, View):
    required_permission = 'projects.delete'
    action_type = 'delete'
    """Bulk-delete multiple tasks belonging to a project.
    POST body: task_ids (list of pk strings or task_ids_csv)
    """
    def post(self, request, project_id=None, pk=None, *args, **kwargs):
        pid = project_id or pk or kwargs.get('project_id') or kwargs.get('pk')
        project = get_object_or_404(Project, pk=pid)
        task_ids = request.POST.getlist('task_ids')
        if not task_ids and request.POST.get('task_ids_csv'):
            task_ids = [x.strip() for x in request.POST.get('task_ids_csv').split(',') if x.strip()]

        if not task_ids:
            messages.error(request, 'No tasks selected for deletion.')
            return redirect('projects:project_detail', pk=pid)

        deleted_count, _ = ProjectTask.objects.filter(
            pk__in=task_ids, project=project
        ).delete()

        messages.success(request, f'Successfully deleted {deleted_count} task(s).')
        return redirect('projects:project_detail', pk=pid)


# Apply Template view
class ProjectApplyTemplateView(AdminRequiredMixin, View):
    required_permission = 'projects.edit'
    action_type = 'edit'
    def post(self, request, project_id):
        project = get_object_or_404(Project, pk=project_id)
        template_id = request.POST.get('template_id')
        referer = request.META.get('HTTP_REFERER')
        
        if not template_id:
            messages.error(request, 'No template selected.')
            if referer:
                return redirect(referer)
            return redirect('projects:project_detail', pk=project_id)

        template = get_object_or_404(TaskTemplate, pk=template_id)

        # Append new template tasks alongside existing ones
        from django.db.models import Max
        max_order = ProjectTask.objects.filter(project=project).aggregate(Max('order'))['order__max'] or 0
        current_order = max_order + 1

        # Sequentially schedule tasks starting from project.start_date
        current_start = project.start_date
        for item in template.items.all().order_by('order'):
            duration = item.default_duration_days or 1
            planned_finish = current_start + timedelta(days=duration - 1)
            ProjectTask.objects.create(
                project=project,
                order=current_order,
                activity=item.activity,
                responsible_person=None,
                planned_start=current_start,
                planned_finish=planned_finish,
                duration_days=duration,
                status='Not Started'
            )
            current_start = planned_finish + timedelta(days=1)
            current_order += 1

        messages.success(request, f'Template "{template.name}" applied successfully.')
        if referer:
            return redirect(referer)
        return redirect('projects:project_detail', pk=project_id)

# Inline HTMX Task Status Update
class ProjectTaskUpdateStatusView(AdminRequiredMixin, View):
    required_permission = 'projects.edit'
    action_type = 'edit'
    def post(self, request, pk):
        task = get_object_or_404(ProjectTask, pk=pk)
        status = request.POST.get('status')
        if status in dict(ProjectTask.STATUS_CHOICES):
            task.status = status
            task.save()
        return render(request, 'projects/partials/task_status_dropdown.html', {'task': task})


class DailyProgressLogCreateView(AdminRequiredMixin, CreateView):
    required_permission = 'projects.add'
    action_type = 'add'
    model = DailyProgressLog
    form_class = DailyProgressLogForm
    template_name = 'projects/log_form.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        content_type = self.request.content_type or ''
        if 'application/json' in content_type and self.request.method in ('POST', 'PUT'):
            try:
                import json
                kwargs['data'] = json.loads(self.request.body)
            except (json.JSONDecodeError, ValueError):
                pass
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['project'] = get_object_or_404(Project, pk=self.kwargs['project_id'])
        return context

    def post(self, request, *args, **kwargs):
        content_type = request.content_type or ''
        if 'application/json' in content_type:
            try:
                data = json.loads(request.body)
            except (json.JSONDecodeError, ValueError):
                data = request.POST
        else:
            data = request.POST

        sync_uuid = data.get('sync_uuid')
        if sync_uuid:
            existing = DailyProgressLog.objects.filter(sync_uuid=sync_uuid).first()
            if existing:
                if 'application/json' in content_type or request.headers.get('x-requested-with') == 'XMLHttpRequest':
                    return JsonResponse({'success': True, 'id': existing.id})
                messages.success(request, 'Daily progress log added successfully.')
                return redirect(self.get_success_url())
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        project = get_object_or_404(Project, pk=self.kwargs['project_id'])
        form.instance.project = project
        form.instance.logged_by = self.request.user

        content_type = self.request.content_type or ''
        if 'application/json' in content_type:
            try:
                data = json.loads(self.request.body)
            except (json.JSONDecodeError, ValueError):
                data = self.request.POST
        else:
            data = self.request.POST

        sync_uuid = data.get('sync_uuid')
        if sync_uuid:
            form.instance.sync_uuid = sync_uuid

        client_event_time_str = data.get('client_event_time')
        client_time = parse_and_validate_client_time(client_event_time_str)

        if client_time:
            form.instance.client_event_time = client_time
            form.instance.synced_at = timezone.now()

        response = super().form_valid(form)

        if client_time:
            DailyProgressLog.objects.filter(pk=form.instance.pk).update(created_at=client_time)

        if 'application/json' in content_type or self.request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'success': True, 'id': form.instance.id})

        messages.success(self.request, 'Daily progress log added successfully.')
        return response

    def get_success_url(self):
        return reverse_lazy('projects:project_detail', kwargs={'pk': self.kwargs['project_id']})


class DailyProgressLogUpdateView(AdminRequiredMixin, UpdateView):
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
        )

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
    required_permission = 'projects.delete'
    action_type = 'delete'

    def post(self, request, pk):
        log = PermissionEngine.get_scoped_object_or_404(
            model_or_qs=DailyProgressLog,
            user=request.user,
            codename='projects.delete',
            branch_field='project__branch',
            pk=pk
        )
        project_pk = log.project.pk
        log.delete()
        messages.success(request, 'Daily progress log deleted successfully.')
        return redirect('projects:project_detail', pk=project_pk)


class ManpowerDeploymentCreateView(AdminRequiredMixin, CreateView):
    required_permission = 'projects.add'
    action_type = 'add'
    model = ManpowerDeployment
    form_class = ManpowerDeploymentForm
    template_name = 'projects/manpower_form.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        content_type = self.request.content_type or ''
        if 'application/json' in content_type and self.request.method in ('POST', 'PUT'):
            try:
                import json
                kwargs['data'] = json.loads(self.request.body)
            except (json.JSONDecodeError, ValueError):
                pass
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['project'] = get_object_or_404(Project, pk=self.kwargs['project_id'])
        return context

    def post(self, request, *args, **kwargs):
        content_type = request.content_type or ''
        if 'application/json' in content_type:
            try:
                data = json.loads(request.body)
            except (json.JSONDecodeError, ValueError):
                data = request.POST
        else:
            data = request.POST

        sync_uuid = data.get('sync_uuid')
        if sync_uuid:
            existing = ManpowerDeployment.objects.filter(sync_uuid=sync_uuid).first()
            if existing:
                if 'application/json' in content_type or request.headers.get('x-requested-with') == 'XMLHttpRequest':
                    return JsonResponse({'success': True, 'id': existing.id})
                messages.success(request, 'Manpower requirement added successfully.')
                return redirect(self.get_success_url())
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        project = get_object_or_404(Project, pk=self.kwargs['project_id'])
        form.instance.project = project

        content_type = self.request.content_type or ''
        if 'application/json' in content_type:
            try:
                data = json.loads(self.request.body)
            except (json.JSONDecodeError, ValueError):
                data = self.request.POST
        else:
            data = self.request.POST

        sync_uuid = data.get('sync_uuid')
        if sync_uuid:
            form.instance.sync_uuid = sync_uuid

        client_event_time_str = data.get('client_event_time')
        client_time = parse_and_validate_client_time(client_event_time_str)

        if client_time:
            form.instance.client_event_time = client_time
            form.instance.synced_at = timezone.now()

        response = super().form_valid(form)

        if 'application/json' in content_type or self.request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'success': True, 'id': form.instance.id})

        messages.success(self.request, 'Manpower requirement added successfully.')
        return response

    def get_success_url(self):
        return reverse_lazy('projects:project_detail', kwargs={'pk': self.kwargs['project_id']})


class ManpowerDeploymentUpdateView(AdminRequiredMixin, UpdateView):
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
        )

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
    required_permission = 'projects.delete'
    action_type = 'delete'

    def post(self, request, pk):
        deployment = PermissionEngine.get_scoped_object_or_404(
            model_or_qs=ManpowerDeployment,
            user=request.user,
            codename='projects.delete',
            branch_field='project__branch',
            pk=pk
        )
        project_pk = deployment.project.pk
        deployment.delete()
        messages.success(request, 'Manpower log deleted successfully.')
        return redirect('projects:project_detail', pk=project_pk)


class ManpowerDeploymentAutoFillView(AdminRequiredMixin, View):
    required_permission = 'projects.edit'
    action_type = 'edit'
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
    required_permission = 'projects.add'
    action_type = 'add'
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
        )

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
    required_permission = 'projects.delete'
    action_type = 'delete'

    def post(self, request, pk):
        material = PermissionEngine.get_scoped_object_or_404(
            model_or_qs=ProjectMaterial,
            user=request.user,
            codename='projects.delete',
            branch_field='project__branch',
            pk=pk
        )
        project_pk = material.project.pk
        material.delete()
        messages.success(request, 'Project material deleted successfully.')
        return redirect('projects:project_detail', pk=project_pk)


class ProjectConfirmSignOffView(AdminRequiredMixin, View):
    required_permission = 'projects.approve'
    action_type = 'approve'
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
    required_permission = 'projects.export'
    action_type = 'export'
    def get(self, request, project_id):
        from django.http import HttpResponse
        from django.utils import timezone
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        
        eval_res = PermissionEngine.evaluate(request.user, 'projects.view', action_type='view')
        if not eval_res.allowed:
            raise PermissionDenied("You do not have permission to view or export projects.")

        project = PermissionEngine.get_scoped_object_or_404(
            Project.objects.select_related('branch', 'sign_off', 'project_type')
            .prefetch_related(
                # #14 — tasks pre-fetched here; iterate project.tasks.all() below to use this cache
                Prefetch('tasks', queryset=ProjectTask.objects.select_related('responsible_person').order_by('order')),
                Prefetch('progress_logs', queryset=DailyProgressLog.objects.order_by('-date')),
                Prefetch('manpower_logs', queryset=ManpowerDeployment.objects.order_by('-date', 'trade')),
                Prefetch('materials', queryset=ProjectMaterial.objects.order_by('material_name'))
            ),
            user=request.user,
            codename='projects.view',
            pk=project_id,
            branch_field='branch'
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
    required_permission = 'projects.edit'
    action_type = 'edit'
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
    required_permission = 'projects.view'
    action_type = 'view'
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
        context['form'] = ProjectTypeForm()
        return context


class ProjectTypeCreateView(AdminRequiredMixin, CreateView):
    required_permission = 'projects.add'
    action_type = 'add'
    model = ProjectType
    form_class = ProjectTypeForm
    template_name = 'projects/project_type_form.html'
    success_url = reverse_lazy('projects:project_type_list')

    def get_template_names(self):
        if self.request.headers.get('HX-Request') == 'true':
            return ['projects/partials/create_type_drawer.html']
        return [self.template_name]

    def form_valid(self, form):
        messages.success(self.request, 'Project type created successfully.')
        response = super().form_valid(form)
        if self.request.headers.get('HX-Request') == 'true':
            from django.http import HttpResponse
            return HttpResponse('<script>window.location.reload();</script>')
        return response


class ProjectTypeUpdateView(AdminRequiredMixin, UpdateView):
    required_permission = 'projects.edit'
    action_type = 'edit'
    model = ProjectType
    form_class = ProjectTypeForm
    template_name = 'projects/project_type_form.html'
    success_url = reverse_lazy('projects:project_type_list')

    def get_template_names(self):
        if self.request.headers.get('HX-Request') == 'true':
            return ['projects/partials/edit_type_drawer.html']
        return [self.template_name]

    def form_valid(self, form):
        messages.success(self.request, 'Project type updated successfully.')
        response = super().form_valid(form)
        if self.request.headers.get('HX-Request') == 'true':
            from django.http import HttpResponse
            return HttpResponse('<script>window.location.reload();</script>')
        return response


class ProjectTypeDeleteView(AdminRequiredMixin, DeleteView):
    required_permission = 'projects.delete'
    action_type = 'delete'
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
    required_permission = 'projects.export'
    action_type = 'export'
    def get(self, request, pk):
        return self._generate_csv(request, pk)

    def post(self, request, pk):
        return self._generate_csv(request, pk)

    def _generate_csv(self, request, pk):
        eval_res = PermissionEngine.evaluate(request.user, 'projects.view', action_type='view')
        if not eval_res.allowed:
            raise PermissionDenied("You do not have permission to export project tasks.")
        project = PermissionEngine.get_scoped_object_or_404(
            Project,
            user=request.user,
            codename='projects.view',
            pk=pk,
            branch_field='branch'
        )
        tasks = project.tasks.select_related('responsible_person').all().order_by('order')

        # Support selective export via task_ids
        task_ids = request.POST.getlist('task_ids') or request.GET.getlist('task_ids')
        if not task_ids and request.GET.get('task_ids'):
            task_ids = [x.strip() for x in request.GET.get('task_ids').split(',') if x.strip()]
        elif not task_ids and request.POST.get('task_ids_csv'):
            task_ids = [x.strip() for x in request.POST.get('task_ids_csv').split(',') if x.strip()]

        if task_ids:
            tasks = tasks.filter(pk__in=task_ids)
        
        response = HttpResponse(content_type='text/csv')
        filename = f"project_{project.id}_selected_tasks.csv" if task_ids else f"project_{project.id}_tasks.csv"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        
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
    required_permission = 'projects.export'
    action_type = 'export'
    def get(self, request, pk):
        eval_res = PermissionEngine.evaluate(request.user, 'projects.view', action_type='view')
        if not eval_res.allowed:
            raise PermissionDenied("You do not have permission to export project manpower.")
        project = PermissionEngine.get_scoped_object_or_404(
            Project,
            user=request.user,
            codename='projects.view',
            pk=pk,
            branch_field='branch'
        )
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
    required_permission = 'projects.export'
    action_type = 'export'
    def get(self, request, pk):
        eval_res = PermissionEngine.evaluate(request.user, 'projects.view', action_type='view')
        if not eval_res.allowed:
            raise PermissionDenied("You do not have permission to export project materials.")
        project = PermissionEngine.get_scoped_object_or_404(
            Project,
            user=request.user,
            codename='projects.view',
            pk=pk,
            branch_field='branch'
        )
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
    required_permission = 'projects.edit'
    action_type = 'edit'
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
                
            if task.project:
                return redirect('projects:project_detail', pk=task.project.pk)
            return redirect('projects:global_task_list')
        except Exception as e:
            import traceback
            traceback.print_exc()
            raise e


class ProjectRequestSignOffView(AdminRequiredMixin, View):
    required_permission = 'projects.edit'
    action_type = 'edit'
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
    required_permission = 'projects.view'
    action_type = 'view'
    model = ProjectTask
    template_name = 'projects/global_task_list.html'
    context_object_name = 'tasks'
    
    def get_queryset(self):
        base_qs = ProjectTask.objects.select_related('project', 'responsible_person').all().order_by('project__name', 'order')
        qs = PermissionEngine.filter_by_data_scope(
            user=self.request.user,
            queryset=base_qs,
            codename='projects.view',
            branch_field='project__branch',
            employee_field='responsible_person'
        )
        
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
        # G9 fix: use self.object_list (already evaluated by ListView) instead of
        # calling self.get_queryset() a second time which fires another DB round-trip.
        tasks = self.object_list
        
        # Standalone tasks (Individual Tasks)
        context['individual_tasks'] = tasks.filter(project__isnull=True).order_by('order')
        
        # Project-based tasks grouped by project
        project_tasks = tasks.filter(project__isnull=False)
        
        from collections import defaultdict
        project_groups = defaultdict(list)
        for t in project_tasks:
            project_groups[t.project].append(t)
            
        project_list = []
        for proj, proj_tasks in project_groups.items():
            completed = sum(1 for t in proj_tasks if t.status == 'Completed')
            total = len(proj_tasks)
            percent = int((completed / total) * 100) if total > 0 else 0
            proj_tasks.sort(key=lambda x: x.order)
            project_list.append({
                'project': proj,
                'tasks': proj_tasks,
                'total_tasks': total,
                'completed_tasks': completed,
                'progress_percent': percent
            })
            
        # Sort project_list by project name
        project_list.sort(key=lambda x: x['project'].name)
        
        context['project_list'] = project_list
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
    required_permission = 'projects.add'
    action_type = 'add'
    model = ProjectTask
    form_class = GlobalProjectTaskForm
    template_name = 'projects/global_task_form.html'
    success_url = reverse_lazy('projects:global_task_list')

    def get(self, request, *args, **kwargs):
        if request.headers.get('HX-Request') == 'true' and ('project' in request.GET or 'assignment_mode' in request.GET):
            project_id = request.GET.get('project')
            mode = request.GET.get('assignment_mode', 'project')
            form = self.get_form_class()(initial={'project': project_id, 'assignment_mode': mode})
            return render(request, 'projects/partials/responsible_person_select.html', {
                'form': form,
                'project_id': project_id,
                'mode': mode,
            })
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        attachments = request.FILES.getlist('assignment_attachments')
        from django.core.exceptions import ValidationError
        from apps.projects.models import validate_task_attachment
        for attachment in attachments:
            try:
                validate_task_attachment(attachment)
            except ValidationError as e:
                form = self.get_form()
                form.add_error(None, f"File validation failed for {attachment.name}: {e.message}")
                return self.form_invalid(form)
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        task = self.object
        project_label = task.project.name if task.project else 'Standalone Task'
        
        # Save multiple assignment/reference attachments if provided
        attachments = self.request.FILES.getlist('assignment_attachments')
        if attachments:
            from apps.projects.models import TaskAttachment
            for index, attachment in enumerate(attachments):
                if index == 0 and not task.assignment_attachment:
                    task.assignment_attachment = attachment
                    task.save(update_fields=['assignment_attachment'])
                TaskAttachment.objects.create(
                    task=task,
                    file=attachment,
                    attachment_type='assignment'
                )

        # Notify newly assigned employee
        if task.responsible_person and task.responsible_person.user:
            Notification.objects.create(
                recipient=task.responsible_person.user,
                employee=task.responsible_person,
                title=f"New Task Assigned: {task.activity}",
                message=f"You have been assigned to task '{task.activity}' ({project_label}).",
                notif_type='field_visit'
            )
            subject = f"New Task Assigned: {task.activity}"
            message = (
                f"Hello {task.responsible_person.full_name},\n\n"
                f"You have been assigned to the following task ({project_label}):\n"
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
    
    # Verify permissions:
    # 1. If task is unassigned: only project managers of the project or admins can complete it
    # 2. If task is assigned: only the assigned employee can complete it
    employee = getattr(request.user, 'employee_profile', None)
    if not task.responsible_person:
        is_authorized = False
        from apps.accounts.engine import PermissionEngine
        if request.user.is_superuser or PermissionEngine.evaluate(request.user, 'projects.edit').allowed:
            is_authorized = True
        if employee and task.project and task.project.project_managers.filter(id=employee.id).exists():
            is_authorized = True
        if not is_authorized:
            return JsonResponse({'error': 'Only project managers or admins can update unassigned tasks.'}, status=403)
    else:
        if not employee or task.responsible_person != employee:
            return JsonResponse({'error': 'You are not assigned to this task.'}, status=403)
        
    note = ""
    progress_percent = None
    if request.content_type == 'application/json':
        try:
            data = json.loads(request.body)
            note = data.get('note', '')
            progress_percent = data.get('progress_percent')
        except json.JSONDecodeError:
            pass
    else:
        note = request.POST.get('note', '')
        progress_percent = request.POST.get('progress_percent')
        
    if progress_percent is not None:
        try:
            progress_percent = int(progress_percent)
            if progress_percent < 0 or progress_percent > 100:
                return JsonResponse({'error': 'Progress percent must be between 0 and 100.'}, status=400)
        except (ValueError, TypeError):
            return JsonResponse({'error': 'Invalid progress percent.'}, status=400)
    else:
        progress_percent = 100

    completion_attachments = request.FILES.getlist('completion_attachments')
    if completion_attachments:
        from django.core.exceptions import ValidationError
        from apps.projects.models import TaskAttachment, validate_task_attachment
        for attachment in completion_attachments:
            try:
                validate_task_attachment(attachment)
            except ValidationError as e:
                return JsonResponse({'error': f"File validation failed for {attachment.name}: {e.message}"}, status=400)
        
        # Save attachments
        for index, attachment in enumerate(completion_attachments):
            if index == 0 and not task.completion_attachment:
                task.completion_attachment = attachment
                task.save(update_fields=['completion_attachment'])
            
            TaskAttachment.objects.create(
                task=task,
                file=attachment,
                attachment_type='completion'
            )

    is_manager_or_admin = False
    from apps.accounts.engine import PermissionEngine
    if request.user.is_superuser or PermissionEngine.evaluate(request.user, 'projects.update', action_type='update').allowed:
        is_manager_or_admin = True
    elif employee and task.project:
        if task.project.project_managers.filter(id=employee.id).exists():
            is_manager_or_admin = True

    if is_manager_or_admin:
        task.progress_percent = progress_percent
        task.pending_progress_percent = None
        task.employee_note = note
        task.pending_employee_note = ""
        if progress_percent == 100:
            task.status = 'Completed'
            if not task.completed_at:
                task.completed_at = timezone.now()
        else:
            task.status = 'In Progress'
    else:
        task.pending_progress_percent = progress_percent
        task.pending_employee_note = note
        task.status = 'Under Review'
        
    task.save()
    if task.project:
        task.project.recalculate_progress()

    progress = task.project.progress_percent if task.project else 0
    return JsonResponse({
        'success': True,
        'progress_percent': progress,
        'completion_attachment_url': task.completion_attachment.url if task.completion_attachment else None
    })


def check_task_view_permission(user, task):
    from apps.accounts.engine import PermissionEngine
    if user.is_superuser or PermissionEngine.evaluate(user, 'projects.view').allowed:
        return True
    employee = getattr(user, 'employee_profile', None)
    if not employee:
        return False
    if task.responsible_person == employee:
        return True
    if task.project:
        if task.project.project_members.filter(id=employee.id).exists():
            return True
        if task.project.site_engineers.filter(id=employee.id).exists():
            return True
        if task.project.project_managers.filter(id=employee.id).exists():
            return True
    return False


@login_required
def task_detail_api(request, pk):
    import os
    task = get_object_or_404(ProjectTask, pk=pk)
    if not check_task_view_permission(request.user, task):
        return JsonResponse({'error': 'Permission denied'}, status=403)
        
    attachments_data = []
    # Fetch from TaskAttachment
    for att in task.attachments.all():
        name = att.filename
        ext = os.path.splitext(name)[1].lower()
        is_image = ext in ['.jpg', '.jpeg', '.png']
        is_pdf = ext == '.pdf'
        attachments_data.append({
            'url': att.file.url,
            'name': name,
            'type': att.attachment_type,
            'is_image': is_image,
            'is_pdf': is_pdf
        })
        
    # Also support legacy files if not already in TaskAttachment
    legacy_assignment = task.assignment_attachment
    if legacy_assignment and not any(a['url'] == legacy_assignment.url for a in attachments_data):
        name = os.path.basename(legacy_assignment.name)
        ext = os.path.splitext(name)[1].lower()
        attachments_data.append({
            'url': legacy_assignment.url,
            'name': name,
            'type': 'assignment',
            'is_image': ext in ['.jpg', '.jpeg', '.png'],
            'is_pdf': ext == '.pdf'
        })
    legacy_completion = task.completion_attachment
    if legacy_completion and not any(a['url'] == legacy_completion.url for a in attachments_data):
        name = os.path.basename(legacy_completion.name)
        ext = os.path.splitext(name)[1].lower()
        attachments_data.append({
            'url': legacy_completion.url,
            'name': name,
            'type': 'completion',
            'is_image': ext in ['.jpg', '.jpeg', '.png'],
            'is_pdf': ext == '.pdf'
        })

    replies_data = []
    for reply in task.replies.select_related('user', 'user__employee_profile').order_by('created_at'):
        emp = getattr(reply.user, 'employee_profile', None)
        full_name = emp.full_name if emp else (reply.user.email or reply.user.phone or "Unknown User")
        role = reply.user.role.capitalize() if hasattr(reply.user, 'role') else 'User'
        photo_url = emp.profile_photo.url if emp and emp.profile_photo else None
        
        replies_data.append({
            'author_name': full_name,
            'author_role': role,
            'author_photo_url': photo_url,
            'message': reply.message,
            'created_at': reply.created_at.strftime('%d/%m/%Y %H:%M')
        })

    data = {
        'id': task.id,
        'order': task.order,
        'activity': task.activity,
        'project_id': task.project_id,
        'project_name': task.project.name if task.project else 'Standalone Task',
        'branch_name': task.project.branch.name if task.project and task.project.branch else 'Global Workspace',
        'responsible_person_id': task.responsible_person_id,
        'responsible_person_name': task.responsible_person.full_name if task.responsible_person else 'Unassigned',
        'planned_start': task.planned_start.strftime('%d/%m/%Y') if task.planned_start else '—',
        'planned_finish': task.planned_finish.strftime('%d/%m/%Y') if task.planned_finish else '—',
        'raw_planned_start': task.planned_start.strftime('%Y-%m-%d') if task.planned_start else '',
        'raw_planned_finish': task.planned_finish.strftime('%Y-%m-%d') if task.planned_finish else '',
        'duration_days': f"{task.duration_days} days" if task.duration_days else '—',
        'raw_duration_days': task.duration_days or '',
        'status': task.status,
        'points': task.points,
        'remarks': task.remarks or '',
        'employee_note': task.employee_note or '',
        'progress_percent': task.progress_percent,
        'pending_progress_percent': task.pending_progress_percent,
        'pending_employee_note': task.pending_employee_note or '',
        'completed_at': task.completed_at.strftime('%d/%m/%Y %H:%M') if task.completed_at else '—',
        'attachments': attachments_data,
        'replies': replies_data
    }
    return JsonResponse(data)


@login_required
@require_POST
def task_add_reply_api(request, pk):
    task = get_object_or_404(ProjectTask, pk=pk)
    if not check_task_view_permission(request.user, task):
        return JsonResponse({'error': 'Permission denied'}, status=403)
        
    message = request.POST.get('message', '').strip()
    if not message:
        if request.content_type == 'application/json':
            try:
                payload = json.loads(request.body)
                message = payload.get('message', '').strip()
            except json.JSONDecodeError:
                pass
                
    if not message:
        return JsonResponse({'error': 'Message cannot be empty'}, status=400)
        
    from apps.projects.models import ProjectTaskReply
    ProjectTaskReply.objects.create(
        task=task,
        user=request.user,
        message=message
    )
    
    replies_data = []
    for r in task.replies.select_related('user', 'user__employee_profile').order_by('created_at'):
        emp = getattr(r.user, 'employee_profile', None)
        full_name = emp.full_name if emp else (r.user.email or r.user.phone or "Unknown User")
        role = r.user.role.capitalize() if hasattr(r.user, 'role') else 'User'
        photo_url = emp.profile_photo.url if emp and emp.profile_photo else None
        
        replies_data.append({
            'author_name': full_name,
            'author_role': role,
            'author_photo_url': photo_url,
            'message': r.message,
            'created_at': r.created_at.strftime('%d/%m/%Y %H:%M')
        })
        
    return JsonResponse({
        'success': True,
        'replies': replies_data
    })


@login_required
@require_POST
def task_approve_api(request, pk):
    from apps.accounts.engine import PermissionEngine
    if not (request.user.is_superuser or PermissionEngine.evaluate(request.user, 'projects.edit').allowed):
        return JsonResponse({'error': 'Permission denied'}, status=403)
        
    task = get_object_or_404(ProjectTask, pk=pk)
    action = request.POST.get('action', 'approve')
    
    if action == 'approve':
        if task.pending_progress_percent is not None:
            task.progress_percent = task.pending_progress_percent
        else:
            task.progress_percent = 100
        
        if task.pending_employee_note:
            task.employee_note = task.pending_employee_note
            
        task.pending_progress_percent = None
        task.pending_employee_note = ""
        
        if task.progress_percent == 100:
            task.status = 'Completed'
            if not task.completed_at:
                task.completed_at = timezone.now()
        else:
            task.status = 'In Progress'
            
        task.save()
        if task.project:
            task.project.recalculate_progress()
            
    elif action == 'reject':
        task.pending_progress_percent = None
        task.pending_employee_note = ""
        
        if task.progress_percent > 0:
            task.status = 'In Progress'
        else:
            task.status = 'Not Started'
            
        task.save()
        if task.project:
            task.project.recalculate_progress()
            
    return JsonResponse({
        'success': True,
        'status': task.status,
        'progress_percent': task.progress_percent,
        'project_progress': task.project.progress_percent if task.project else 0
    })


# ─────────────────────────────────────────────────────────────────────────────
# Gantt View (G2)
# ─────────────────────────────────────────────────────────────────────────────

class ProjectGanttView(RoleRequiredMixin, View):
    required_permission = 'projects.view'
    action_type = 'view'
    """
    Renders an Alpine.js-driven dynamic Gantt chart for a single project.
    Provides live interactive view switching between:
    1. Excel Project Planner Live Matrix View (same-to-same layout with daily cell highlights)
    2. Interactive SVG Bar Timeline View (with dependencies, milestones, today line)
    3. Multi-Zone / Activity Schedule Matrix View
    """

    def get(self, request, pk):
        from django.db.models import Prefetch
        import json
        from datetime import timedelta

        project = get_object_or_404(
            Project.objects.select_related('branch', 'project_type')
            .prefetch_related(
                'project_managers', 'site_engineers', 'project_members',
                Prefetch(
                    'tasks',
                    queryset=ProjectTask.objects.select_related('responsible_person')
                    .prefetch_related('predecessor_deps', 'successor_deps')
                    .order_by('order')
                )
            ),
            pk=pk
        )

        is_admin = getattr(request.user, 'is_superuser', False) or PermissionEngine.evaluate(request.user, 'projects.view', action_type='view').allowed
        if not is_admin and hasattr(request.user, 'employee_profile'):
            profile = request.user.employee_profile
            is_assigned = (
                project.project_managers.filter(pk=profile.pk).exists() or
                project.site_engineers.filter(pk=profile.pk).exists() or
                project.project_members.filter(pk=profile.pk).exists()
            )
            if not is_assigned:
                return redirect('/staff/home/')
        elif not is_admin:
            return redirect('/staff/home/')

        tasks = list(project.tasks.all())

        # Determine chart date range
        dates = []
        for t in tasks:
            if t.planned_start:
                dates.append(t.planned_start)
            if t.planned_finish:
                dates.append(t.planned_finish)
            if t.baseline_start:
                dates.append(t.baseline_start)
            if t.baseline_finish:
                dates.append(t.baseline_finish)
            if t.actual_start:
                dates.append(t.actual_start)
            if t.actual_finish:
                dates.append(t.actual_finish)

        today = date.today()
        if dates:
            chart_start = min(dates)
            chart_end = max(dates)
        else:
            chart_start = project.start_date or today
            chart_end = (project.completion_date or today + timedelta(days=30))

        if chart_end <= chart_start:
            chart_end = chart_start + timedelta(days=1)

        total_days = (chart_end - chart_start).days + 1

        def to_pct(d):
            """Convert a date to an offset percentage from chart_start."""
            if not d:
                return None
            delta = (d - chart_start).days
            return round((delta / total_days) * 100, 2)

        def dur_pct(start, finish):
            """Width percentage for a bar from start to finish inclusive."""
            if not start or not finish:
                return None
            d = (finish - start).days + 1
            return round((d / total_days) * 100, 2)

        gantt_tasks = []
        for t in tasks:
            deps = []
            for dep in t.predecessor_deps.all():
                deps.append({
                    'predecessor_id': dep.predecessor_id,
                    'type': dep.dep_type,
                    'lag': dep.lag_days,
                })

            gantt_tasks.append({
                'id': t.id,
                'order': t.order,
                'activity': t.activity,
                'responsible': t.responsible_person.full_name if t.responsible_person else None,
                'planned_start': t.planned_start.isoformat() if t.planned_start else None,
                'planned_finish': t.planned_finish.isoformat() if t.planned_finish else None,
                'baseline_start': t.baseline_start.isoformat() if t.baseline_start else None,
                'baseline_finish': t.baseline_finish.isoformat() if t.baseline_finish else None,
                'actual_start': t.actual_start.isoformat() if t.actual_start else None,
                'actual_finish': t.actual_finish.isoformat() if t.actual_finish else None,
                'progress_percent': t.progress_percent,
                'is_milestone': t.is_milestone,
                'is_delayed': t.is_delayed,
                'status': t.status,
                'effective_status': t.effective_status,
                'left_pct': to_pct(t.planned_start),
                'width_pct': dur_pct(t.planned_start, t.planned_finish),
                'baseline_left_pct': to_pct(t.baseline_start),
                'baseline_width_pct': dur_pct(t.baseline_start, t.baseline_finish),
                'actual_left_pct': to_pct(t.actual_start),
                'actual_width_pct': dur_pct(t.actual_start, t.actual_finish),
                'dependencies': deps,
            })

        # Build month/week headers for the chart ruler
        ruler_months = []
        cursor = chart_start.replace(day=1)
        while cursor <= chart_end:
            next_month = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
            month_end = min(next_month - timedelta(days=1), chart_end)
            left = to_pct(max(cursor, chart_start))
            width = dur_pct(max(cursor, chart_start), month_end)
            ruler_months.append({
                'label': cursor.strftime('%b %Y'),
                'left_pct': left,
                'width_pct': width,
            })
            cursor = next_month

        today_pct = to_pct(today) if chart_start <= today <= chart_end else None

        # ── 1. Authentic Monthly Master Schedule (matching GANTT CHART (2).xlsx) ──
        from apps.projects.services.gantt_reference import GanttReferenceService
        monthly_data = GanttReferenceService.get_monthly_master_schedule()

        # ── 2. Daily Planner Matrix Dataset (matching PROJECT SCHEDULE GANTT CHART.xlsx) ──
        # Provide authentic 33-task HVAC planner schedule with exact day highlights
        ref_planner = GanttReferenceService.get_hvac_planner_tasks(base_date=chart_start, display_days=60)
        planner_days = ref_planner['planner_days']
        
        # If project has real imported/custom tasks (> 12 or distinct), use project tasks;
        # otherwise provide authentic 33 activities matching reference Project Planner sheet
        if len(tasks) > 12 and not any('contract award & kick-off' in t.activity.lower() for t in tasks[:2]):
            planner_tasks = []
            for t in tasks:
                dur = t.duration_days
                if not dur and t.planned_start and t.planned_finish:
                    dur = max(1, (t.planned_finish - t.planned_start).days + 1)
                dur = dur or 1
                p_start = t.planned_start
                p_finish = t.planned_finish or (p_start + timedelta(days=dur - 1) if p_start else None)
                pct = t.progress_percent or 0
                completed_days = round((pct / 100.0) * dur) if dur else 0

                cells = []
                for d_info in planner_days:
                    d_obj = chart_start + timedelta(days=d_info['day_num'] - 1)
                    in_plan = bool(p_start and p_finish and p_start <= d_obj <= p_finish)
                    in_progress = bool(in_plan and p_start and (d_obj - p_start).days < completed_days)
                    is_act = bool(t.actual_start and d_obj == t.actual_start)
                    is_beyond = bool(t.actual_finish and p_finish and p_finish < d_obj <= t.actual_finish)

                    if in_progress:
                        cells.append('progress')
                    elif in_plan:
                        cells.append('plan')
                    elif is_beyond:
                        cells.append('beyond')
                    elif is_act:
                        cells.append('actual')
                    else:
                        cells.append('empty')

                act_dur = None
                if t.actual_start and t.actual_finish:
                    act_dur = max(1, (t.actual_finish - t.actual_start).days + 1)
                elif t.actual_start:
                    act_dur = max(1, (today - t.actual_start).days + 1)

                planner_tasks.append({
                    'id': t.id,
                    'order': t.order,
                    'activity': t.activity,
                    'is_milestone': t.is_milestone,
                    'is_delayed': t.is_delayed,
                    'status': t.status,
                    'plan_start': t.planned_start.strftime('%Y-%m-%d') if t.planned_start else '—',
                    'plan_duration': dur,
                    'plan_end': p_finish.strftime('%Y-%m-%d') if p_finish else '—',
                    'actual_start': t.actual_start.strftime('%Y-%m-%d') if t.actual_start else '—',
                    'actual_duration': act_dur or '—',
                    'progress_percent': t.progress_percent,
                    'cells': cells,
                })
        else:
            planner_tasks = ref_planner['planner_tasks']

        # ── 3. Work / Zone Schedule Matrix (matching PROJECT SCHEDULE GANTT CHART.xlsx DATE) ──
        zone_ref = GanttReferenceService.get_zone_schedule_matrix()
        zone_stages = zone_ref['zone_stages']
        zone_rows = zone_ref['zone_rows']
        project_milestones = zone_ref['project_milestones']

        context = {
            'project': project,
            'gantt_tasks_json': json.dumps(gantt_tasks),
            'ruler_months_json': json.dumps(ruler_months),
            'monthly_schedule_json': json.dumps(monthly_data['tasks']),
            'monthly_slots_json': json.dumps(monthly_data['slots']),
            'monthly_months_json': json.dumps(monthly_data['months']),
            'monthly_phases_json': json.dumps(monthly_data['phases']),
            'planner_days_json': json.dumps(planner_days),
            'planner_tasks_json': json.dumps(planner_tasks),
            'zone_stages_json': json.dumps(zone_stages),
            'zone_rows_json': json.dumps(zone_rows),
            'project_milestones_json': json.dumps(project_milestones),
            'chart_start': chart_start,
            'chart_end': chart_end,
            'today': today,
            'today_pct': today_pct,
            'total_days': total_days,
        }
        return render(request, 'projects/project_gantt.html', context)


class ProjectGanttExportView(RoleRequiredMixin, View):
    required_permission = 'projects.export'
    action_type = 'export'
    """
    Downloads a professionally formatted multi-sheet Excel workbook (.xlsx)
    for the project's Gantt schedule, matching the exact format of the
    reference Project Schedule and Planner spreadsheets.
    """

    def get(self, request, pk):
        from apps.projects.services.gantt_export import GanttExcelExportService
        from django.utils.text import slugify

        eval_res = PermissionEngine.evaluate(request.user, 'projects.view', action_type='view')
        if not eval_res.allowed:
            raise PermissionDenied("You do not have permission to export project schedules.")

        project = PermissionEngine.get_scoped_object_or_404(
            Project.objects.prefetch_related('tasks__responsible_person'),
            user=request.user,
            codename='projects.view',
            pk=pk,
            branch_field='branch'
        )

        xlsx_bytes = GanttExcelExportService.export_project_workbook(project)
        slug = slugify(project.name) or f"project_{project.pk}"
        filename = f"{slug}_gantt_schedule.xlsx"

        response = HttpResponse(
            xlsx_bytes,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response


# ─────────────────────────────────────────────────────────────────────────────
# Task Dependency CRUD (G1)
# ─────────────────────────────────────────────────────────────────────────────

class TaskDependencyCreateView(AdminRequiredMixin, View):
    required_permission = 'projects.edit'
    action_type = 'edit'
    """
    HTMX-friendly endpoint: add a predecessor dependency for a given task (successor).
    POST: predecessor, dep_type, lag_days
    Validates same-project constraint, self-loop, and circular dependency.
    """

    def post(self, request, pk):
        successor = get_object_or_404(ProjectTask, pk=pk)
        project = successor.project

        form = TaskDependencyForm(
            request.POST,
            project=project,
            successor=successor
        )

        if form.is_valid():
            dep = form.save(commit=False)
            dep.successor = successor
            try:
                dep.save()
                messages.success(
                    request,
                    f'Dependency added: #{dep.predecessor.order} {dep.predecessor.activity} '
                    f'→ #{successor.order} {successor.activity} [{dep.dep_type}, lag={dep.lag_days}d]'
                )
            except Exception as e:
                messages.error(request, f'Could not save dependency: {e}')
        else:
            for field, errors in form.errors.items():
                for err in errors:
                    messages.error(request, f'{err}')

        referer = request.META.get('HTTP_REFERER')
        if referer:
            return redirect(referer)
        if project:
            return redirect('projects:project_detail', pk=project.pk)
        return redirect('projects:global_task_list')


class TaskDependencyDeleteView(AdminRequiredMixin, View):
    required_permission = 'projects.edit'
    action_type = 'edit'
    """Remove a single task dependency record."""

    def post(self, request, pk):
        dep = get_object_or_404(TaskDependency, pk=pk)
        project_pk = dep.successor.project.pk if dep.successor.project else None
        dep.delete()
        messages.success(request, 'Task dependency removed.')
        referer = request.META.get('HTTP_REFERER')
        if referer:
            return redirect(referer)
        if project_pk:
            return redirect('projects:project_detail', pk=project_pk)
        return redirect('projects:global_task_list')


# ─────────────────────────────────────────────────────────────────────────────
# Gantt Excel Import Workflow
# ─────────────────────────────────────────────────────────────────────────────

class ProjectGanttImportView(RoleRequiredMixin, View):
    required_permission = 'projects.add'
    action_type = 'add'
    """
    Step 1: Upload & Initial Format Detection.
    Accepts .xlsx workbook, enforces code-level safety limits, discovers sheets,
    parses initial candidate tasks, and establishes staged batch foundation.
    """

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk):
        project = get_object_or_404(Project, pk=pk)
        if not check_gantt_import_permission(request.user, project):
            raise PermissionDenied("You do not have permission to import Gantt tasks for this project.")

        context = {
            'project': project,
            'active_step': 1,
        }
        return render(request, 'projects/gantt_import.html', context)

    def post(self, request, pk):
        project = get_object_or_404(Project, pk=pk)
        if not check_gantt_import_permission(request.user, project):
            raise PermissionDenied("You do not have permission to import Gantt tasks for this project.")

        uploaded_file = request.FILES.get('file')
        if not uploaded_file:
            context = {
                'project': project,
                'error': 'Please select an Excel (.xlsx) file to upload.',
                'error_field': 'file',
                'active_step': 1
            }
            if request.headers.get('HX-Request'):
                return render(request, 'projects/partials/gantt_import_upload_card.html', context, status=400)
            return render(request, 'projects/gantt_import.html', context, status=400)

        try:
            content, sha256_hash = WorkbookSafetyValidator.validate_file(uploaded_file, uploaded_file.name)
            parser = GanttWorkbookParser(content, uploaded_file.name)
            sheets_info = parser.discover_sheets()
            if not sheets_info:
                raise GanttImportError("Workbook does not contain any readable sheets.", code="no_sheets")

            # Cache file bytes for subsequent sheet-switching without re-uploading
            from django.core.cache import cache
            cache.set(f"gantt_file_{sha256_hash}", content, 3600 * 4)

            # Choose initial sheet: prefer first sheet with detectable rows
            selected_sheet = sheets_info[0]['name']
            for s in sheets_info:
                if s['total_rows'] > 0 and s['detected_format'] != 'unknown':
                    selected_sheet = s['name']
                    break

            default_emp_id = request.POST.get('default_employee_id')
            default_emp_id = int(default_emp_id) if default_emp_id and str(default_emp_id).isdigit() else None

            parse_res = parser.parse_sheet(selected_sheet, default_employee_id=default_emp_id)
            parser.close()

            # Annotate duplicates against existing project tasks
            annotated_rows = GanttDuplicateDetector.annotate_duplicates(project, parse_res['rows'])
            parse_res['stats']['duplicate'] = sum(1 for r in annotated_rows if r.get('is_duplicate'))

            # Create staged batch record
            batch = GanttImportStagingManager.create_batch(
                project=project,
                user=request.user,
                filename=uploaded_file.name,
                file_sha256=sha256_hash,
                detected_format=parse_res['detected_format'],
                selected_sheet=selected_sheet,
                staged_rows=annotated_rows,
                stats=parse_res['stats']
            )

            employees = EmployeeProfile.objects.filter(is_active=True).order_by('full_name')
            context = {
                'project': project,
                'batch': batch,
                'sheets': sheets_info,
                'selected_sheet': selected_sheet,
                'detected_format': parse_res['detected_format'],
                'calc_note': parse_res.get('calc_note', ''),
                'rows': annotated_rows,
                'stats': parse_res['stats'],
                'employees': employees,
                'current_filter': 'all',
                'active_step': 2,
            }

            if request.headers.get('HX-Request'):
                return render(request, 'projects/partials/gantt_import_preview.html', context)
            return render(request, 'projects/gantt_import.html', context)

        except GanttImportError as e:
            context = {
                'project': project,
                'error': e.message,
                'error_field': e.field or 'file',
                'active_step': 1
            }
            if request.headers.get('HX-Request'):
                return render(request, 'projects/partials/gantt_import_upload_card.html', context, status=400)
            messages.error(request, e.message)
            return render(request, 'projects/gantt_import.html', context, status=400)


class ProjectGanttImportPreviewView(RoleRequiredMixin, View):
    required_permission = 'projects.add'
    action_type = 'add'
    """
    Step 2: Preview, Sheet Switching, In-Place Row Corrections, & Re-Validation.
    Performs zero ProjectTask writes. Authoritative validation executed server-side.
    """

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk, batch_id):
        project = get_object_or_404(Project, pk=pk)
        if not check_gantt_import_permission(request.user, project):
            raise PermissionDenied("You do not have permission to view this import batch.")

        batch = GanttImportStagingManager.get_staged_batch(str(batch_id), project, request.user)

        # Handle Sheet Switching if requested
        sheet_switch = request.GET.get('sheet')
        if sheet_switch and sheet_switch != batch.selected_sheet:
            from django.core.cache import cache
            raw_content = cache.get(f"gantt_file_{batch.file_sha256}")
            if raw_content:
                parser = GanttWorkbookParser(raw_content, batch.filename)
                parse_res = parser.parse_sheet(sheet_switch)
                parser.close()
                annotated = GanttDuplicateDetector.annotate_duplicates(project, parse_res['rows'])
                parse_res['stats']['duplicate'] = sum(1 for r in annotated if r.get('is_duplicate'))

                batch.selected_sheet = sheet_switch
                batch.detected_format = parse_res['detected_format']
                batch.staged_data = {'rows': annotated, 'stats': parse_res['stats']}
                batch.task_count = len(annotated)
                batch.save(update_fields=['selected_sheet', 'detected_format', 'staged_data', 'task_count', 'updated_at'])

        # Discover sheets if raw content is available
        sheets_info = []
        from django.core.cache import cache
        raw_content = cache.get(f"gantt_file_{batch.file_sha256}")
        if raw_content:
            try:
                p = GanttWorkbookParser(raw_content, batch.filename)
                sheets_info = p.discover_sheets()
                p.close()
            except Exception:
                pass

        staged_data = batch.staged_data or {}
        rows = staged_data.get('rows', [])
        stats = staged_data.get('stats', {})

        filter_type = request.GET.get('filter', 'all')
        if filter_type == 'valid':
            filtered_rows = [r for r in rows if not r.get('errors') and not r.get('warnings') and not r.get('excluded')]
        elif filter_type == 'warning':
            filtered_rows = [r for r in rows if r.get('warnings') and not r.get('errors') and not r.get('excluded')]
        elif filter_type == 'invalid':
            filtered_rows = [r for r in rows if r.get('errors') and not r.get('excluded')]
        elif filter_type == 'duplicate':
            filtered_rows = [r for r in rows if r.get('is_duplicate')]
        elif filter_type == 'excluded':
            filtered_rows = [r for r in rows if r.get('excluded')]
        else:
            filtered_rows = rows

        employees = EmployeeProfile.objects.filter(is_active=True).order_by('full_name')
        context = {
            'project': project,
            'batch': batch,
            'sheets': sheets_info,
            'selected_sheet': batch.selected_sheet,
            'detected_format': batch.detected_format,
            'calc_note': staged_data.get('calc_note', ''),
            'rows': filtered_rows,
            'all_rows_count': len(rows),
            'stats': stats,
            'employees': employees,
            'current_filter': filter_type,
            'active_step': 2,
        }

        if request.headers.get('HX-Request'):
            return render(request, 'projects/partials/gantt_import_preview.html', context)
        return render(request, 'projects/gantt_import.html', context)

    def post(self, request, pk, batch_id):
        project = get_object_or_404(Project, pk=pk)
        if not check_gantt_import_permission(request.user, project):
            raise PermissionDenied("You do not have permission to modify this import batch.")

        batch = GanttImportStagingManager.get_staged_batch(str(batch_id), project, request.user)
        action = request.POST.get('action')

        try:
            if action == 'update_field':
                row_idx = int(request.POST.get('row_idx'))
                field = request.POST.get('field')
                value = request.POST.get('value')
                GanttImportStagingManager.update_staged_row(batch, row_idx, field, value)

            elif action == 'toggle_exclude':
                row_idx = int(request.POST.get('row_idx'))
                current_rows = batch.staged_data.get('rows', [])
                if 0 <= row_idx < len(current_rows):
                    new_val = not current_rows[row_idx].get('excluded', False)
                    GanttImportStagingManager.update_staged_row(batch, row_idx, 'excluded', new_val)

            elif action == 'exclude_all_invalid':
                for idx, r in enumerate(batch.staged_data.get('rows', [])):
                    if r.get('errors'):
                        GanttImportStagingManager.update_staged_row(batch, idx, 'excluded', True)

            elif action == 'set_default_responsible':
                resp_id = request.POST.get('responsible_id')
                if resp_id and str(resp_id).isdigit():
                    resp_id_int = int(resp_id)
                    for idx, r in enumerate(batch.staged_data.get('rows', [])):
                        if not r.get('responsible_id'):
                            GanttImportStagingManager.update_staged_row(batch, idx, 'responsible_id', resp_id_int)

        except GanttImportError as e:
            messages.error(request, e.message)

        return self.get(request, pk, batch_id)


class ProjectGanttImportConfirmView(RoleRequiredMixin, View):
    required_permission = 'projects.add'
    action_type = 'add'
    """
    Step 3: Atomic Confirmation and Idempotent ProjectTask Creation.
    Enforces transaction safety, row revalidation, non-colliding order, and audit tracking.
    """

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, pk, batch_id):
        project = get_object_or_404(Project, pk=pk)
        if not check_gantt_import_permission(request.user, project):
            raise PermissionDenied("You do not have permission to confirm this import.")

        batch = GanttImportStagingManager.get_staged_batch(str(batch_id), project, request.user)

        try:
            result = GanttImportExecutor.confirm_import(batch, project, request.user, request)
            messages.success(
                request,
                f"Successfully imported {result['imported_count']} Gantt tasks into {project.name}."
            )

            if request.headers.get('HX-Request'):
                context = {
                    'project': project,
                    'result': result,
                    'batch': batch
                }
                return render(request, 'projects/partials/gantt_import_success.html', context)

            return redirect('projects:project_gantt', pk=project.pk)

        except GanttImportError as e:
            if request.headers.get('HX-Request'):
                return HttpResponse(
                    f'<div class="p-4 text-xs font-medium text-red-700 bg-red-50 rounded-xl border border-red-200" role="alert">'
                    f'<strong>Import Error:</strong> {e.message}</div>',
                    status=400
                )
            messages.error(request, e.message)
            return redirect('projects:project_gantt_import_preview', pk=project.pk, batch_id=batch.uuid)



