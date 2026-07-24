from datetime import datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse_lazy
from django.views.generic import ListView, CreateView, DetailView, UpdateView, View
from django.http import HttpResponse, HttpResponseForbidden
from django.contrib import messages
from apps.accounts.mixins import AdminRequiredMixin, RoleRequiredMixin
from apps.notifications.models import log_audit
from .models import EmployeeProfile, EmployeeLocationSync, EmployeeDocument
from .forms import EmployeeCreateForm, EmployeeEditForm, EmployeeDocumentForm
from apps.branches.models import Branch
from apps.attendance.models import Attendance
from django.db.models import Q
from django.utils import timezone
import calendar

def get_client_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')

class EmployeeListView(AdminRequiredMixin, ListView):
    model = EmployeeProfile
    template_name = 'employees/employee_list.html'
    context_object_name = 'employees'
    paginate_by = 20
    
    def get_queryset(self):
        queryset = super().get_queryset().select_related('branch', 'user')
        search_query = self.request.GET.get('search', '')
        department_filter = self.request.GET.get('department', '')
        branch_filter = self.request.GET.get('branch', '')
        status_filter = self.request.GET.get('status', '')

        if search_query:
            queryset = queryset.filter(
                Q(full_name__icontains=search_query) | 
                Q(employee_id__icontains=search_query)
            )
            
        if department_filter:
            queryset = queryset.filter(department=department_filter)
            
        if branch_filter:
            queryset = queryset.filter(branch_id=branch_filter)
            
        if status_filter == 'active':
            queryset = queryset.filter(is_active=True)
        elif status_filter == 'inactive':
            queryset = queryset.filter(is_active=False)
            
        return queryset.order_by('full_name', 'employee_id')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search'] = self.request.GET.get('search', '')
        context['department'] = self.request.GET.get('department', '')
        context['branch_id'] = self.request.GET.get('branch', '')
        context['status'] = self.request.GET.get('status', '')
        
        context['departments'] = EmployeeProfile.objects.values_list('department', flat=True).distinct().exclude(department='')
        from apps.branches.utils import get_cached_branches
        context['branches'] = get_cached_branches()
        return context

class EmployeeCreateView(AdminRequiredMixin, CreateView):
    model = EmployeeProfile
    form_class = EmployeeCreateForm
    template_name = 'employees/employee_form.html'
    success_url = reverse_lazy('employees:employee_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from apps.leave.models import LeaveType
        leave_types = LeaveType.objects.all().order_by('name')
        context['leave_types_with_overrides'] = [
            {
                'id': lt.id,
                'name': lt.name,
                'category': lt.category,
                'default_days': lt.default_days_per_year,
                'override_days': self.request.POST.get(f'leave_override_{lt.id}', '') if self.request.method == 'POST' else ''
            }
            for lt in leave_types
        ]
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        from apps.leave.models import LeaveType, LeaveBalance
        from .models import EmployeeLeaveRule
        
        leave_types = LeaveType.objects.all()
        for lt in leave_types:
            post_key = f'leave_override_{lt.id}'
            override_val = self.request.POST.get(post_key)
            if override_val is not None and override_val.strip() != '':
                try:
                    days = int(override_val)
                    EmployeeLeaveRule.objects.update_or_create(
                        employee=self.object,
                        leave_type=lt,
                        defaults={'days_per_year': days}
                    )
                    year = timezone.now().year
                    balance, bal_created = LeaveBalance.objects.get_or_create(
                        employee=self.object,
                        leave_type=lt,
                        year=year,
                        defaults={'total_days': days, 'used_days': 0}
                    )
                    if not bal_created:
                        balance.total_days = days
                        balance.save()
                except ValueError:
                    pass
            else:
                EmployeeLeaveRule.objects.filter(employee=self.object, leave_type=lt).delete()
                year = timezone.now().year
                LeaveBalance.objects.filter(
                    employee=self.object,
                    leave_type=lt,
                    year=year
                ).update(total_days=lt.default_days_per_year)

        messages.success(self.request, 'Employee profile and user account created successfully.')
        return response

class EmployeeEditView(AdminRequiredMixin, UpdateView):
    model = EmployeeProfile
    form_class = EmployeeEditForm
    template_name = 'employees/employee_form.html'
    success_url = reverse_lazy('employees:employee_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from apps.leave.models import LeaveType
        from .models import EmployeeLeaveRule
        leave_types = LeaveType.objects.all().order_by('name')
        
        if self.request.method == 'POST':
            context['leave_types_with_overrides'] = [
                {
                    'id': lt.id,
                    'name': lt.name,
                    'category': lt.category,
                    'default_days': lt.default_days_per_year,
                    'override_days': self.request.POST.get(f'leave_override_{lt.id}', '')
                }
                for lt in leave_types
            ]
        else:
            overrides = {
                rule.leave_type_id: rule.days_per_year 
                for rule in EmployeeLeaveRule.objects.filter(employee=self.object)
            }
            context['leave_types_with_overrides'] = [
                {
                    'id': lt.id,
                    'name': lt.name,
                    'category': lt.category,
                    'default_days': lt.default_days_per_year,
                    'override_days': overrides.get(lt.id, '')
                }
                for lt in leave_types
            ]
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        from apps.leave.models import LeaveType, LeaveBalance
        from .models import EmployeeLeaveRule
        
        leave_types = LeaveType.objects.all()
        for lt in leave_types:
            post_key = f'leave_override_{lt.id}'
            override_val = self.request.POST.get(post_key)
            if override_val is not None and override_val.strip() != '':
                try:
                    days = int(override_val)
                    EmployeeLeaveRule.objects.update_or_create(
                        employee=self.object,
                        leave_type=lt,
                        defaults={'days_per_year': days}
                    )
                    year = timezone.now().year
                    balance, bal_created = LeaveBalance.objects.get_or_create(
                        employee=self.object,
                        leave_type=lt,
                        year=year,
                        defaults={'total_days': days, 'used_days': 0}
                    )
                    if not bal_created:
                        balance.total_days = days
                        balance.save()
                except ValueError:
                    pass
            else:
                EmployeeLeaveRule.objects.filter(employee=self.object, leave_type=lt).delete()
                year = timezone.now().year
                LeaveBalance.objects.filter(
                    employee=self.object,
                    leave_type=lt,
                    year=year
                ).update(total_days=lt.default_days_per_year)

        messages.success(self.request, 'Employee profile updated successfully.')
        return response

class EmployeeDetailView(AdminRequiredMixin, DetailView):
    model = EmployeeProfile
    template_name = 'employees/employee_detail.html'
    context_object_name = 'employee'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['today'] = timezone.localdate()
        employee = self.object
        
        now = timezone.now()
        start_date = now.replace(day=1).date()
        _, last_day = calendar.monthrange(now.year, now.month)
        end_date = now.replace(day=last_day).date()
        
        attendances_this_month = Attendance.objects.filter(
            employee=employee,
            date__gte=start_date,
            date__lte=end_date,
            is_expired=False
        )
        
        stats = {
            'present': attendances_this_month.filter(status__in=['on_time', 'late']).count(),
            'absent': attendances_this_month.filter(status='absent').count(),
            'late': attendances_this_month.filter(status='late').count(),
            'field_visits': attendances_this_month.filter(type='field').count()
        }
        context['stats'] = stats
        
        # Previous history filtering
        history_date_from = self.request.GET.get('history_date_from', '')
        history_date_to = self.request.GET.get('history_date_to', '')
        history_status = self.request.GET.get('history_status', '')
        history_type = self.request.GET.get('history_type', '')
        
        history_qs = Attendance.objects.filter(employee=employee, is_expired=False)
        if history_date_from:
            history_qs = history_qs.filter(date__gte=history_date_from)
        if history_date_to:
            history_qs = history_qs.filter(date__lte=history_date_to)
        if history_status:
            if history_status == 'present':
                history_qs = history_qs.filter(status__in=['on_time', 'late'])
            else:
                history_qs = history_qs.filter(status=history_status)
        if history_type:
            history_qs = history_qs.filter(type=history_type)
            
        context['recent_attendance'] = history_qs.order_by('-date', '-check_in_time')
        context['history_date_from'] = history_date_from
        context['history_date_to'] = history_date_to
        context['history_status'] = history_status
        context['history_type'] = history_type
        
        context['todays_field_visits'] = Attendance.objects.filter(
            employee=employee, 
            date=timezone.localdate(), 
            attendance_type='field_visit',
            is_expired=False
        ).order_by('-check_in_time')
        
        # Periodic background location syncs (Auto Sync Track)
        sync_date_str = self.request.GET.get('sync_date')
        if sync_date_str:
            try:
                sync_date = datetime.strptime(sync_date_str, '%Y-%m-%d').date()
            except ValueError:
                sync_date = timezone.localdate()
        else:
            sync_date = timezone.localdate()
            
        tz = timezone.get_current_timezone()
        sync_start_dt = timezone.make_aware(datetime.combine(sync_date, datetime.min.time()), tz)
        sync_end_dt = timezone.make_aware(datetime.combine(sync_date, datetime.max.time()), tz)
        
        sync_time_from = self.request.GET.get('sync_time_from')
        sync_time_to = self.request.GET.get('sync_time_to')
        
        if sync_time_from:
            try:
                stf = datetime.strptime(sync_time_from, '%H:%M').time()
                sync_start_dt = timezone.make_aware(datetime.combine(sync_date, stf), tz)
            except ValueError:
                pass
        if sync_time_to:
            try:
                stt = datetime.strptime(sync_time_to, '%H:%M').time()
                sync_end_dt = timezone.make_aware(datetime.combine(sync_date, stt), tz)
            except ValueError:
                pass
                
        sync_qs = EmployeeLocationSync.objects.filter(
            employee=employee,
            timestamp__range=(sync_start_dt, sync_end_dt)
        ).order_by('timestamp')
        
        context['sync_date'] = sync_date.strftime('%Y-%m-%d')
        context['sync_time_from'] = sync_time_from or ''
        context['sync_time_to'] = sync_time_to or ''
        context['location_syncs'] = sync_qs
        
        return context

class ToggleStatusView(AdminRequiredMixin, View):
    def post(self, request, pk):
        employee = get_object_or_404(EmployeeProfile, pk=pk)
        employee.is_active = not employee.is_active
        employee.save()
        return render(request, 'employees/partials/status_badge.html', {'employee': employee})

class EmployeeDocumentCreateView(AdminRequiredMixin, CreateView):
    model = EmployeeDocument
    form_class = EmployeeDocumentForm
    template_name = 'employees/document_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.employee = get_object_or_404(EmployeeProfile, pk=kwargs['employee_pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['employee'] = self.employee
        return context

    def form_valid(self, form):
        form.instance.employee = self.employee
        messages.success(self.request, 'Document uploaded successfully.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('employees:employee_detail', kwargs={'pk': self.employee.pk})

class EmployeeDocumentEditView(AdminRequiredMixin, UpdateView):
    model = EmployeeDocument
    form_class = EmployeeDocumentForm
    template_name = 'employees/document_form.html'

    def form_valid(self, form):
        messages.success(self.request, 'Document updated successfully.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('employees:employee_detail', kwargs={'pk': self.object.employee.pk})

class EmployeeDocumentDeleteView(AdminRequiredMixin, View):
    def post(self, request, pk):
        doc = get_object_or_404(EmployeeDocument, pk=pk)
        employee_pk = doc.employee.pk
        doc.delete()
        messages.success(request, 'Document deleted successfully.')
        return redirect('employees:employee_detail', pk=employee_pk)


# ==========================================
# PHASE 2: EMPLOYEE MASTER (SSOT) VIEWS
# ==========================================

from apps.employees.models import Employee, Department, Designation, EmployeeStatus, EmploymentHistory
from apps.employees.forms import EmployeeMasterForm, DepartmentForm, DesignationForm
from apps.notifications.models import log_audit


class EmployeeMasterListView(AdminRequiredMixin, ListView):
    model = Employee
    template_name = 'employees/master_list.html'
    context_object_name = 'employees'
    paginate_by = 20

    def get_queryset(self):
        queryset = Employee.objects.select_related(
            'branch', 'department', 'designation', 'reporting_manager', 'user'
        ).prefetch_related('direct_reports', 'employment_history')

        search = self.request.GET.get('search', '').strip()
        status_filter = self.request.GET.get('status', '').strip()
        dept_filter = self.request.GET.get('department', '').strip()
        branch_filter = self.request.GET.get('branch', '').strip()
        desig_filter = self.request.GET.get('designation', '').strip()

        if search:
            queryset = queryset.filter(
                Q(first_name__icontains=search) |
                Q(last_name__icontains=search) |
                Q(employee_number__icontains=search) |
                Q(personal_email__icontains=search) |
                Q(phone__icontains=search)
            )

        if status_filter:
            queryset = queryset.filter(status=status_filter)
        if dept_filter:
            queryset = queryset.filter(department_id=dept_filter)
        if branch_filter:
            queryset = queryset.filter(branch_id=branch_filter)
        if desig_filter:
            queryset = queryset.filter(designation_id=desig_filter)

        return queryset.order_by('-created_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search'] = self.request.GET.get('search', '')
        context['status_filter'] = self.request.GET.get('status', '')
        context['dept_filter'] = self.request.GET.get('department', '')
        context['branch_filter'] = self.request.GET.get('branch', '')
        context['desig_filter'] = self.request.GET.get('designation', '')

        context['departments'] = Department.objects.filter(is_active=True)
        context['designations'] = Designation.objects.filter(is_active=True)
        from apps.branches.utils import get_cached_branches
        context['branches'] = get_cached_branches()
        context['statuses'] = EmployeeStatus.choices
        return context

    def render_to_response(self, context, **response_kwargs):
        if self.request.headers.get('HX-Request') and not self.request.headers.get('HX-Target') == 'modal-container':
            return render(self.request, 'employees/partials/master_table.html', context)
        return super().render_to_response(context, **response_kwargs)


class EmployeeMasterDetailView(AdminRequiredMixin, DetailView):
    model = Employee
    template_name = 'employees/master_detail.html'
    context_object_name = 'employee'

    def get_queryset(self):
        return Employee.objects.select_related(
            'branch', 'department', 'designation', 'reporting_manager', 'user'
        ).prefetch_related(
            'direct_reports',
            'employment_history__approved_by',
            'documents__uploaded_by',
            'asset_assignments__asset'
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['active_tab'] = self.request.GET.get('tab', 'identity')
        context['documents'] = self.object.documents.all()
        context['active_documents'] = self.object.documents.filter(is_active=True)
        context['asset_assignments'] = self.object.asset_assignments.select_related('asset').all()
        context['active_asset_assignments'] = self.object.asset_assignments.filter(returned_date__isnull=True).select_related('asset')
        return context


class EmployeeMasterCreateView(AdminRequiredMixin, CreateView):
    model = Employee
    form_class = EmployeeMasterForm
    template_name = 'employees/master_form_modal.html'
    success_url = reverse_lazy('employees:master_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        employee = self.object

        # Audit log creation
        log_audit(
            actor=self.request.user,
            action='employee_created',
            target=employee,
            summary=f"Created Employee Master {employee.employee_number} ({employee.get_full_name()})"
        )

        # Initial EmploymentHistory entry
        EmploymentHistory.objects.create(
            employee=employee,
            field_changed='status',
            old_value='',
            new_value=employee.get_status_display(),
            reason='Initial Master record creation',
            approved_by=self.request.user,
            effective_date=employee.joined_date or timezone.now().date()
        )

        if self.request.headers.get('HX-Request'):
            messages.success(self.request, f"Employee {employee.get_full_name()} created successfully.")
            response = render(self.request, 'employees/partials/form_success_htmx.html', {
                'message': f"Employee {employee.get_full_name()} created.",
                'redirect_url': reverse_lazy('employees:master_list')
            })
            response['HX-Redirect'] = reverse_lazy('employees:master_list')
            return response

        messages.success(self.request, f"Employee {employee.get_full_name()} created successfully.")
        return response


class EmployeeMasterEditView(AdminRequiredMixin, UpdateView):
    model = Employee
    form_class = EmployeeMasterForm
    template_name = 'employees/master_form_modal.html'

    def get_queryset(self):
        return Employee.objects.select_related('branch', 'department', 'designation', 'reporting_manager', 'user')

    def form_valid(self, form):
        old_instance = Employee.objects.get(pk=self.object.pk)
        old_status = old_instance.status
        old_dept = old_instance.department
        old_desig = old_instance.designation
        old_branch = old_instance.branch
        old_mgr = old_instance.reporting_manager

        response = super().form_valid(form)
        employee = self.object
        reason_text = self.request.POST.get('change_reason', 'Admin update')

        # Track status change history
        if old_status != employee.status:
            EmploymentHistory.objects.create(
                employee=employee,
                field_changed='status',
                old_value=dict(EmployeeStatus.choices).get(old_status, old_status),
                new_value=employee.get_status_display(),
                reason=reason_text,
                approved_by=self.request.user,
                effective_date=timezone.now().date()
            )
            log_audit(
                actor=self.request.user,
                action='employee_status_changed',
                target=employee,
                summary=f"Changed status from {old_status} to {employee.status}"
            )

        # Track org change history
        org_changed = False
        if old_dept != employee.department or old_desig != employee.designation or old_branch != employee.branch or old_mgr != employee.reporting_manager:
            org_changed = True
            changes_desc = []
            if old_dept != employee.department:
                changes_desc.append(f"Dept: {old_dept} -> {employee.department}")
            if old_desig != employee.designation:
                changes_desc.append(f"Designation: {old_desig} -> {employee.designation}")
            if old_branch != employee.branch:
                changes_desc.append(f"Branch: {old_branch} -> {employee.branch}")
            if old_mgr != employee.reporting_manager:
                changes_desc.append(f"Manager: {old_mgr} -> {employee.reporting_manager}")

            EmploymentHistory.objects.create(
                employee=employee,
                field_changed='organization',
                old_value=f"Dept: {old_dept}, Desig: {old_desig}, Branch: {old_branch}, Mgr: {old_mgr}",
                new_value=f"Dept: {employee.department}, Desig: {employee.designation}, Branch: {employee.branch}, Mgr: {employee.reporting_manager}",
                reason=reason_text,
                approved_by=self.request.user,
                effective_date=timezone.now().date()
            )
            log_audit(
                actor=self.request.user,
                action='employee_org_changed',
                target=employee,
                summary=f"Updated org details: {'; '.join(changes_desc)}"
            )

        if self.request.headers.get('HX-Request'):
            messages.success(self.request, f"Employee {employee.get_full_name()} updated.")
            response = render(self.request, 'employees/partials/form_success_htmx.html', {
                'message': f"Employee {employee.get_full_name()} updated.",
                'redirect_url': reverse_lazy('employees:master_detail', kwargs={'pk': employee.pk})
            })
            response['HX-Redirect'] = reverse_lazy('employees:master_detail', kwargs={'pk': employee.pk})
            return response

        messages.success(self.request, f"Employee {employee.get_full_name()} updated.")
        return response

    def get_success_url(self):
        return reverse_lazy('employees:master_detail', kwargs={'pk': self.object.pk})


class EmployeeMasterArchiveView(AdminRequiredMixin, View):
    def post(self, request, pk):
        employee = get_object_or_404(Employee, pk=pk)
        old_status = employee.status
        employee.delete()  # soft delete -> sets status to archived
        
        EmploymentHistory.objects.create(
            employee=employee,
            field_changed='status',
            old_value=old_status,
            new_value=EmployeeStatus.ARCHIVED,
            reason='Archived via Admin Action',
            approved_by=request.user,
            effective_date=timezone.now().date()
        )
        log_audit(
            actor=request.user,
            action='employee_status_changed',
            target=employee,
            summary=f"Archived employee {employee.employee_number}"
        )

        messages.success(request, f"Employee {employee.get_full_name()} has been archived.")
        if request.headers.get('HX-Request'):
            response = render(request, 'employees/partials/form_success_htmx.html', {
                'redirect_url': reverse_lazy('employees:master_list')
            })
            response['HX-Redirect'] = reverse_lazy('employees:master_list')
            return response

        return redirect('employees:master_list')


# Document Management & Asset Assignment Views (Phase 2 Step 3)
from apps.employees.models import Asset, AssetAssignment, DocumentDownloadLog, DocumentType, SENSITIVE_DOCUMENT_TYPES
from apps.employees.forms import EmployeeDocumentForm, AssetForm, AssetAssignmentForm, AssetReturnForm

class EmployeeDocumentUploadView(RoleRequiredMixin, View):
    allowed_roles = ['admin', 'manager']

    def get(self, request, pk):
        employee = get_object_or_404(Employee, pk=pk)
        form = EmployeeDocumentForm()
        return render(request, 'employees/partials/document_upload_modal.html', {
            'employee': employee,
            'form': form
        })

    def post(self, request, pk):
        employee = get_object_or_404(Employee, pk=pk)
        form = EmployeeDocumentForm(request.POST, request.FILES)
        if form.is_valid():
            doc = form.save(commit=False)
            doc.employee_master = employee
            doc.uploaded_by = request.user
            doc.save()

            log_audit(actor=request.user, action='document_uploaded', target=doc, summary=f"Uploaded {doc.get_document_type_display()} v{doc.version} for {employee.get_full_name()}")
            messages.success(request, f"Document {doc.get_document_type_display()} v{doc.version} uploaded successfully.")

            if request.headers.get('HX-Request'):
                response = render(request, 'employees/partials/form_success_htmx.html', {
                    'redirect_url': reverse('employees:master_detail', kwargs={'pk': employee.pk})
                })
                response['HX-Redirect'] = reverse('employees:master_detail', kwargs={'pk': employee.pk})
                return response
            return redirect('employees:master_detail', pk=employee.pk)

        return render(request, 'employees/partials/document_upload_modal.html', {
            'employee': employee,
            'form': form
        })


class EmployeeDocumentDownloadView(RoleRequiredMixin, View):
    allowed_roles = ['admin', 'manager', 'staff']

    def get(self, request, pk):
        doc = get_object_or_404(EmployeeDocument.objects.select_related('employee_master', 'employee_master__reporting_manager'), pk=pk)

        # Sensitive RBAC check
        if doc.is_sensitive():
            user = request.user
            user_role = getattr(user, 'role', '')
            is_hr_admin = user_role in ['admin', 'hr'] or user.is_superuser
            is_self = doc.employee_master and doc.employee_master.user_id == user.id
            is_manager = doc.employee_master and doc.employee_master.reporting_manager and doc.employee_master.reporting_manager.user_id == user.id

            if not (is_hr_admin or is_self or is_manager):
                log_audit(actor=user, action='document_access_denied', target=doc, summary=f"Unauthorized download attempt for document {doc.pk}")
                return HttpResponseForbidden("Access denied: You do not have permission to view sensitive documents of this employee.")

        DocumentDownloadLog.objects.create(
            document=doc,
            downloaded_by=request.user,
            ip_address=get_client_ip(request)
        )
        log_audit(actor=request.user, action='document_downloaded', target=doc, summary=f"Downloaded document {doc.title or doc.get_document_type_display()} for {doc.employee_master}")

        if not doc.file or not doc.file.storage.exists(doc.file.name):
            messages.error(request, "Document file not found.")
            return redirect('employees:master_detail', pk=doc.employee_master_id or 1)

        response = HttpResponse(doc.file.read(), content_type='application/octet-stream')
        filename = doc.file.name.split('/')[-1]
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response


class AssetListView(RoleRequiredMixin, ListView):
    allowed_roles = ['admin', 'manager']
    model = Asset
    template_name = 'employees/asset_list.html'
    context_object_name = 'assets'

    def get_queryset(self):
        qs = Asset.objects.prefetch_related('assignments__employee')
        type_filter = self.request.GET.get('type')
        q = self.request.GET.get('q')
        if type_filter:
            qs = qs.filter(asset_type=type_filter)
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(asset_tag__icontains=q) | Q(serial_number__icontains=q))
        return qs


class AssetCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = ['admin', 'manager']
    model = Asset
    form_class = AssetForm
    template_name = 'employees/partials/asset_form_modal.html'

    def form_valid(self, form):
        response = super().form_valid(form)
        log_audit(actor=self.request.user, action='asset_created', target=self.object, summary=f"Created asset {self.object.asset_tag}")
        messages.success(self.request, f"Asset {self.object.asset_tag} created.")
        if self.request.headers.get('HX-Request'):
            res = render(self.request, 'employees/partials/form_success_htmx.html', {'redirect_url': reverse('employees:asset_list')})
            res['HX-Redirect'] = reverse('employees:asset_list')
            return res
        return response

    def get_success_url(self):
        return reverse('employees:asset_list')


class AssetAssignView(RoleRequiredMixin, View):
    allowed_roles = ['admin', 'manager']

    def get(self, request, pk):
        employee = get_object_or_404(Employee, pk=pk)
        form = AssetAssignmentForm()
        return render(request, 'employees/partials/asset_assign_modal.html', {'employee': employee, 'form': form})

    def post(self, request, pk):
        employee = get_object_or_404(Employee, pk=pk)
        form = AssetAssignmentForm(request.POST)
        if form.is_valid():
            assignment = form.save(commit=False)
            assignment.employee = employee
            assignment.assigned_by = request.user
            assignment.save()

            log_audit(actor=request.user, action='asset_assigned', target=assignment, summary=f"Assigned asset {assignment.asset.asset_tag} to {employee.get_full_name()}")
            messages.success(request, f"Asset {assignment.asset.asset_tag} assigned to {employee.get_full_name()}.")

            if request.headers.get('HX-Request'):
                res = render(request, 'employees/partials/form_success_htmx.html', {'redirect_url': reverse('employees:master_detail', kwargs={'pk': employee.pk})})
                res['HX-Redirect'] = reverse('employees:master_detail', kwargs={'pk': employee.pk})
                return res
            return redirect('employees:master_detail', pk=employee.pk)

        return render(request, 'employees/partials/asset_assign_modal.html', {'employee': employee, 'form': form})


class AssetReturnView(RoleRequiredMixin, View):
    allowed_roles = ['admin', 'manager']

    def get(self, request, pk):
        assignment = get_object_or_404(AssetAssignment, pk=pk)
        form = AssetReturnForm(instance=assignment, initial={'returned_date': timezone.localdate()})
        return render(request, 'employees/partials/asset_return_modal.html', {'assignment': assignment, 'form': form})

    def post(self, request, pk):
        assignment = get_object_or_404(AssetAssignment, pk=pk)
        form = AssetReturnForm(request.POST, instance=assignment)
        if form.is_valid():
            updated = form.save()
            log_audit(actor=request.user, action='asset_returned', target=updated, summary=f"Returned asset {updated.asset.asset_tag} from {updated.employee.get_full_name()}")
            messages.success(request, f"Asset {updated.asset.asset_tag} returned.")

            if request.headers.get('HX-Request'):
                res = render(request, 'employees/partials/form_success_htmx.html', {'redirect_url': reverse('employees:master_detail', kwargs={'pk': updated.employee_id})})
                res['HX-Redirect'] = reverse('employees:master_detail', kwargs={'pk': updated.employee_id})
                return res
            return redirect('employees:master_detail', pk=updated.employee_id)

        return render(request, 'employees/partials/asset_return_modal.html', {'assignment': assignment, 'form': form})


# ==========================================
# LIFECYCLE STATE MACHINE VIEWS
# ==========================================

from apps.employees.models import LifecycleTransitionRequest
from apps.employees.forms import LifecycleActionForm, ReviewTransitionForm
from apps.employees.lifecycle import is_low_risk, is_valid_transition, describe_allowed, TRANSITION_MAP
from django.contrib.auth import get_user_model
from django.utils import timezone as tz


def _notify_admins(request_obj):
    """Send in-app notification to all admin users about a pending lifecycle request."""
    User = get_user_model()
    admins = User.objects.filter(role='admin', is_active=True)
    from apps.notifications.models import Notification
    emp = request_obj.employee
    for admin in admins:
        Notification.objects.create(
            recipient=admin,
            title=f"Lifecycle request: {emp.get_full_name()}",
            message=(
                f"{request_obj.requested_by} requests to move {emp.get_full_name()} "
                f"from '{request_obj.from_status}' to '{request_obj.to_status}'. "
                f"Reason: {request_obj.reason[:200]}"
            ),
            notif_type='lifecycle_request',
        )


def _notify_requester(request_obj):
    """Notify the original requester when their request is reviewed."""
    if not request_obj.requested_by:
        return
    from apps.notifications.models import Notification
    emp = request_obj.employee
    verdict = request_obj.review_status  # 'approved' or 'rejected'
    note = f" Note: {request_obj.review_note}" if request_obj.review_note else ''
    Notification.objects.create(
        recipient=request_obj.requested_by,
        title=f"Lifecycle request {verdict}: {emp.get_full_name()}",
        message=(
            f"Your request to move {emp.get_full_name()} "
            f"from '{request_obj.from_status}' to '{request_obj.to_status}' "
            f"was {verdict} by {request_obj.reviewed_by}.{note}"
        ),
        notif_type='lifecycle_reviewed',
    )


def _apply_transition(employee, req_obj, actor):
    """
    Apply an approved / low-risk transition:
    - Updates employee.status (bypassing clean() state-machine guard by writing directly)
    - Optionally applies new_department / new_designation
    - Creates EmploymentHistory entry
    - Logs audit
    """
    old_status = employee.status
    new_status = req_obj.to_status if req_obj else employee.status

    # Apply org changes if bundled
    changes_desc = []
    if req_obj and req_obj.new_department:
        old_dept = employee.department
        employee.department = req_obj.new_department
        changes_desc.append(f"Dept: {old_dept} → {req_obj.new_department}")
    if req_obj and req_obj.new_designation:
        old_desig = employee.designation
        employee.designation = req_obj.new_designation
        changes_desc.append(f"Designation: {old_desig} → {req_obj.new_designation}")

    # Bypass clean() status-machine check by using update() — we've already
    # validated the transition before calling _apply_transition.
    effective = req_obj.effective_date if req_obj else tz.now().date()
    reason = req_obj.reason if req_obj else 'Direct lifecycle action'

    Employee.objects.filter(pk=employee.pk).update(
        status=new_status,
        department=employee.department,
        designation=employee.designation,
        updated_at=tz.now(),
    )
    employee.refresh_from_db()

    EmploymentHistory.objects.create(
        employee=employee,
        field_changed='status',
        old_value=dict(EmployeeStatus.choices).get(old_status, old_status),
        new_value=dict(EmployeeStatus.choices).get(new_status, new_status),
        reason=reason,
        approved_by=actor,
        effective_date=effective,
    )
    if changes_desc:
        EmploymentHistory.objects.create(
            employee=employee,
            field_changed='organization',
            old_value='',
            new_value='; '.join(changes_desc),
            reason=reason,
            approved_by=actor,
            effective_date=effective,
        )
    log_audit(
        actor=actor,
        action='lifecycle_transition_applied',
        target=employee,
        summary=f"Status: {old_status} → {new_status}"
    )


class LifecycleActionView(AdminRequiredMixin, View):
    """
    POST: Initiate a lifecycle transition from master_detail page.
    LOW_RISK  → apply immediately.
    HIGH_RISK → create LifecycleTransitionRequest (pending), notify admins.
    """
    def get(self, request, pk):
        employee = get_object_or_404(Employee, pk=pk)
        to_status = request.GET.get('to_status', '')
        if not to_status or not is_valid_transition(employee.status, to_status):
            return HttpResponse("Invalid transition.", status=400)
        form = LifecycleActionForm(to_status=to_status, initial={
            'to_status': to_status,
            'effective_date': tz.now().date(),
        })
        return render(request, 'employees/partials/lifecycle_action_modal.html', {
            'employee': employee,
            'to_status': to_status,
            'to_status_display': dict(EmployeeStatus.choices).get(to_status, to_status),
            'form': form,
            'is_high_risk': not is_low_risk(employee.status, to_status),
        })

    def post(self, request, pk):
        employee = get_object_or_404(Employee, pk=pk)
        to_status = request.POST.get('to_status', '')

        if not to_status or not is_valid_transition(employee.status, to_status):
            allowed = describe_allowed(employee.status)
            if request.headers.get('HX-Request'):
                return HttpResponse(
                    f"<p class='text-rose-500 text-sm font-semibold'>Invalid transition from '{employee.status}' to '{to_status}'. Allowed: {allowed}</p>",
                    status=422
                )
            messages.error(request, f"Invalid transition: {allowed}")
            return redirect('employees:master_detail', pk=pk)

        form = LifecycleActionForm(request.POST, to_status=to_status)
        if not form.is_valid():
            return render(request, 'employees/partials/lifecycle_action_modal.html', {
                'employee': employee,
                'to_status': to_status,
                'to_status_display': dict(EmployeeStatus.choices).get(to_status, to_status),
                'form': form,
                'is_high_risk': not is_low_risk(employee.status, to_status),
            })

        cd = form.cleaned_data
        from_status = employee.status

        if is_low_risk(from_status, to_status):
            # Build a fake req_obj-like namespace for _apply_transition
            class _FakeReq:
                pass
            fake = _FakeReq()
            fake.to_status = to_status
            fake.reason = cd['reason']
            fake.effective_date = cd['effective_date']
            fake.new_department = cd.get('new_department')
            fake.new_designation = cd.get('new_designation')
            _apply_transition(employee, fake, request.user)
            log_audit(
                actor=request.user,
                action='lifecycle_transition_applied',
                target=employee,
                summary=f"LOW_RISK: {from_status} → {to_status}"
            )
            messages.success(request, f"Status changed: {from_status} → {to_status}")
        else:
            # HIGH_RISK: queue for admin approval
            req = LifecycleTransitionRequest.objects.create(
                employee=employee,
                from_status=from_status,
                to_status=to_status,
                reason=cd['reason'],
                new_department=cd.get('new_department'),
                new_designation=cd.get('new_designation'),
                requested_by=request.user,
                effective_date=cd['effective_date'],
                review_status=LifecycleTransitionRequest.ReviewStatus.PENDING,
            )
            _notify_admins(req)
            log_audit(
                actor=request.user,
                action='lifecycle_transition_requested',
                target=employee,
                summary=f"HIGH_RISK pending: {from_status} → {to_status}"
            )
            messages.success(request, f"Transition request submitted for admin approval: {from_status} → {to_status}")

        if request.headers.get('HX-Request'):
            res = render(request, 'employees/partials/form_success_htmx.html', {
                'redirect_url': reverse('employees:master_detail', kwargs={'pk': pk})
            })
            res['HX-Redirect'] = reverse('employees:master_detail', kwargs={'pk': pk})
            return res
        return redirect('employees:master_detail', pk=pk)


class LifecyclePendingListView(AdminRequiredMixin, ListView):
    """Admin queue: all lifecycle transition requests (default: pending)."""
    model = LifecycleTransitionRequest
    template_name = 'employees/lifecycle_requests.html'
    context_object_name = 'requests'
    paginate_by = 30

    def get_queryset(self):
        qs = LifecycleTransitionRequest.objects.select_related(
            'employee', 'requested_by', 'reviewed_by', 'new_department', 'new_designation'
        )
        status_filter = self.request.GET.get('status', 'pending')
        if status_filter in ('pending', 'approved', 'rejected'):
            qs = qs.filter(review_status=status_filter)
        return qs.order_by('-requested_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['status_filter'] = self.request.GET.get('status', 'pending')
        context['pending_count'] = LifecycleTransitionRequest.objects.filter(
            review_status=LifecycleTransitionRequest.ReviewStatus.PENDING
        ).count()
        context['review_form'] = ReviewTransitionForm()
        return context

    def render_to_response(self, context, **response_kwargs):
        if self.request.headers.get('HX-Request'):
            return render(self.request, 'employees/partials/lifecycle_requests_table.html', context)
        return super().render_to_response(context, **response_kwargs)


class LifecycleReviewView(AdminRequiredMixin, View):
    """
    POST: Admin approves or rejects a LifecycleTransitionRequest.
    'action' POST param must be 'approve' or 'reject'.
    Returns updated row partial (htmx swap outerHTML).
    """
    def post(self, request, req_pk):
        ltr = get_object_or_404(LifecycleTransitionRequest, pk=req_pk)
        if not ltr.is_pending():
            if request.headers.get('HX-Request'):
                return render(request, 'employees/partials/lifecycle_request_row.html', {'req': ltr})
            return redirect('employees:lifecycle_requests')

        action = request.POST.get('action', '')
        form = ReviewTransitionForm(request.POST)
        # review_note is optional, form is always valid
        review_note = request.POST.get('review_note', '').strip()

        if action == 'approve':
            _apply_transition(ltr.employee, ltr, request.user)
            ltr.review_status = LifecycleTransitionRequest.ReviewStatus.APPROVED
            ltr.reviewed_by = request.user
            ltr.reviewed_at = tz.now()
            ltr.review_note = review_note
            ltr.save(update_fields=['review_status', 'reviewed_by', 'reviewed_at', 'review_note'])
            log_audit(
                actor=request.user,
                action='lifecycle_transition_approved',
                target=ltr.employee,
                summary=f"Approved: {ltr.from_status} → {ltr.to_status}"
            )
            _notify_requester(ltr)
            messages.success(request, f"Approved: {ltr.employee.get_full_name()} {ltr.from_status} → {ltr.to_status}")

        elif action == 'reject':
            ltr.review_status = LifecycleTransitionRequest.ReviewStatus.REJECTED
            ltr.reviewed_by = request.user
            ltr.reviewed_at = tz.now()
            ltr.review_note = review_note
            ltr.save(update_fields=['review_status', 'reviewed_by', 'reviewed_at', 'review_note'])
            log_audit(
                actor=request.user,
                action='lifecycle_transition_rejected',
                target=ltr.employee,
                summary=f"Rejected: {ltr.from_status} → {ltr.to_status}"
            )
            _notify_requester(ltr)
            messages.success(request, f"Rejected: {ltr.employee.get_full_name()} transition to {ltr.to_status}")

        else:
            if request.headers.get('HX-Request'):
                return HttpResponse("<p class='text-rose-500 text-sm'>Invalid action.</p>", status=400)
            return redirect('employees:lifecycle_requests')

        if request.headers.get('HX-Request'):
            return render(request, 'employees/partials/lifecycle_request_row.html', {'req': ltr})
        return redirect('employees:lifecycle_requests')
