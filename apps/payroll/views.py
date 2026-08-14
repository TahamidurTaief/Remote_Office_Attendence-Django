import calendar
from datetime import date, datetime
from decimal import Decimal
from django.shortcuts import render, get_object_or_404, redirect
from django.views import View
from django.views.generic import ListView, DetailView, TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import models, transaction
from django.db.models import Sum, Count, Q
from django.http import HttpResponse, JsonResponse, HttpResponseBadRequest
from django.contrib import messages

from apps.accounts.mixins import RoleRequiredMixin, AdminRequiredMixin
from apps.payroll.models import (
    PayrollRun,
    PayrollRunStatus,
    EmployeePayrollCalculation,
    PayrollAdjustment,
    PayrollWorkflowAudit,
    SalaryComponent,
    SalaryComponentType,
    PaymentMode,
    SalaryStructure,
    SalaryStructureComponent,
    EmployeeSalaryAssignment,
    SalaryComponentValueType
)
from apps.payroll.services import PayrollService
from apps.payroll.reports import (
    generate_payslip_pdf,
    export_payroll_register_excel,
    export_payroll_register_csv,
    export_payroll_register_pdf,
    export_bank_report_excel,
    export_bank_report_csv,
    export_bank_report_pdf,
    export_cash_report_excel,
    export_cash_report_csv,
    export_cash_report_pdf
)
from apps.employees.models import Employee, Department
from apps.branches.models import Branch

PAYROLL_MANAGER_ROLES = ['admin', 'system_owner', 'hr', 'finance', 'accounts']


class PayrollManagerMixin(RoleRequiredMixin):
    allowed_roles = PAYROLL_MANAGER_ROLES


def is_payroll_manager(user):
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    user_roles = [a.role.code for a in user.role_assignments.select_related('role').filter(role__is_active=True)]
    if not user_roles and hasattr(user, 'role'):
        user_roles = [user.role]
    return any(r in PAYROLL_MANAGER_ROLES for r in user_roles)


class PayrollRunListView(PayrollManagerMixin, ListView):
    model = PayrollRun
    template_name = 'payroll/payroll_run_list.html'
    context_object_name = 'runs'

    def get_queryset(self):
        qs = PayrollRun.objects.annotate(
            total_calc_count=Count('calculations'),
            agg_gross=Sum('calculations__gross_salary'),
            agg_net=Sum('calculations__net_payable'),
            agg_bank=Sum('calculations__bank_payable'),
            agg_cash=Sum('calculations__cash_payable'),
        ).order_by('-period_start')

        year = self.request.GET.get('year')
        status = self.request.GET.get('status')
        if year and year.isdigit():
            qs = qs.filter(period_start__year=int(year))
        if status:
            qs = qs.filter(status=status)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        today = date.today()
        ctx['current_year'] = today.year
        ctx['current_month'] = today.month
        ctx['years'] = [today.year - 1, today.year, today.year + 1]
        ctx['months'] = [(i, calendar.month_name[i]) for i in range(1, 13)]
        ctx['selected_year'] = self.request.GET.get('year', '')
        ctx['selected_status'] = self.request.GET.get('status', '')
        ctx['statuses'] = PayrollRunStatus.choices
        return ctx


class PayrollRunCreateView(PayrollManagerMixin, View):
    def post(self, request):
        month = request.POST.get('month')
        year = request.POST.get('year')
        name = request.POST.get('name', '').strip()

        if not month or not year or not month.isdigit() or not year.isdigit():
            messages.error(request, "Valid month and year are required.")
            return redirect('payroll:payroll_run_list')

        m_int = int(month)
        y_int = int(year)
        _, last_day = calendar.monthrange(y_int, m_int)
        period_start = date(y_int, m_int, 1)
        period_end = date(y_int, m_int, last_day)

        if not name:
            name = f"{calendar.month_name[m_int]} {y_int} Payroll"

        payroll_run = PayrollRun.objects.create(
            name=name,
            period_start=period_start,
            period_end=period_end,
            status=PayrollRunStatus.DRAFT
        )

        # Log creation audit
        PayrollWorkflowAudit.objects.create(
            payroll_run=payroll_run,
            from_status='None',
            to_status=PayrollRunStatus.DRAFT,
            action_by=request.user,
            note='Initial Payroll Run created',
            snapshot_data={}
        )

        messages.success(request, f"Payroll run '{name}' created in Draft state.")
        return redirect('payroll:payroll_run_detail', pk=payroll_run.pk)


class PayrollRunDetailView(PayrollManagerMixin, DetailView):
    model = PayrollRun
    template_name = 'payroll/payroll_run_detail.html'
    context_object_name = 'run'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        run = self.object

        # Aggregate stats
        calcs = EmployeePayrollCalculation.objects.filter(payroll_run=run).select_related(
            'employee', 'employee__department', 'employee__designation', 'employee__branch'
        )

        totals = calcs.aggregate(
            total_count=Count('id'),
            total_gross=Sum('gross_salary'),
            total_earnings=Sum('total_earnings'),
            total_deductions=Sum('total_deductions'),
            total_net=Sum('net_payable'),
            total_bank=Sum('bank_payable'),
            total_cash=Sum('cash_payable'),
            total_ot_hours=Sum('ot_hours'),
            total_ot_amount=Sum('ot_amount'),
        )

        ctx['totals'] = {
            'count': totals['total_count'] or 0,
            'gross': totals['total_gross'] or Decimal('0.00'),
            'earnings': totals['total_earnings'] or Decimal('0.00'),
            'deductions': totals['total_deductions'] or Decimal('0.00'),
            'net': totals['total_net'] or Decimal('0.00'),
            'bank': totals['total_bank'] or Decimal('0.00'),
            'cash': totals['total_cash'] or Decimal('0.00'),
            'ot_hours': totals['total_ot_hours'] or Decimal('0.00'),
            'ot_amount': totals['total_ot_amount'] or Decimal('0.00'),
        }

        # Unassigned employee warning check
        from apps.employees.models import Employee
        active_emps = Employee.objects.filter(status='active')
        unassigned_emps = []
        for emp in active_emps:
            has_assign = False
            for assign in emp.salary_assignments.all():
                if assign.effective_from <= run.period_end and (assign.effective_to is None or assign.effective_to >= run.period_start):
                    has_assign = True
                    break
            if not has_assign:
                unassigned_emps.append(emp)
        ctx['unassigned_count'] = len(unassigned_emps)
        ctx['unassigned_employees'] = unassigned_emps

        ctx['departments'] = Department.objects.filter(is_active=True)
        ctx['branches'] = Branch.objects.all()
        ctx['payment_modes'] = PaymentMode.choices
        ctx['audits'] = run.workflow_audits.select_related('action_by').order_by('-action_at')[:10]
        ctx['is_locked'] = run.status in [PayrollRunStatus.APPROVED_LOCKED, PayrollRunStatus.DISBURSED]

        # Initial calculation pagination
        search = self.request.GET.get('search', '').strip()
        dept_id = self.request.GET.get('department')
        branch_id = self.request.GET.get('branch')
        mode = self.request.GET.get('payment_mode')

        grid_qs = calcs
        if search:
            grid_qs = grid_qs.filter(
                Q(employee__employee_number__icontains=search) |
                Q(employee__first_name__icontains=search) |
                Q(employee__last_name__icontains=search)
            )
        if dept_id:
            grid_qs = grid_qs.filter(employee__department_id=dept_id)
        if branch_id:
            grid_qs = grid_qs.filter(employee__branch_id=branch_id)
        if mode:
            grid_qs = grid_qs.filter(payment_mode=mode)

        paginator = Paginator(grid_qs.order_by('employee__employee_number'), 25)
        page_number = self.request.GET.get('page', 1)
        ctx['page_obj'] = paginator.get_page(page_number)
        ctx['search'] = search
        ctx['selected_dept'] = dept_id
        ctx['selected_branch'] = branch_id
        ctx['selected_mode'] = mode

        return ctx


class PayrollRunGridPartialView(PayrollManagerMixin, View):
    def get(self, request, pk):
        run = get_object_or_404(PayrollRun, pk=pk)
        calcs = EmployeePayrollCalculation.objects.filter(payroll_run=run).select_related(
            'employee', 'employee__department', 'employee__designation', 'employee__branch'
        )

        search = request.GET.get('search', '').strip()
        dept_id = request.GET.get('department')
        branch_id = request.GET.get('branch')
        mode = request.GET.get('payment_mode')

        if search:
            calcs = calcs.filter(
                Q(employee__employee_number__icontains=search) |
                Q(employee__first_name__icontains=search) |
                Q(employee__last_name__icontains=search)
            )
        if dept_id:
            calcs = calcs.filter(employee__department_id=dept_id)
        if branch_id:
            calcs = calcs.filter(employee__branch_id=branch_id)
        if mode:
            calcs = calcs.filter(payment_mode=mode)

        paginator = Paginator(calcs.order_by('employee__employee_number'), 25)
        page_number = request.GET.get('page', 1)
        page_obj = paginator.get_page(page_number)

        return render(request, 'payroll/payroll_grid_partial.html', {
            'run': run,
            'page_obj': page_obj,
            'is_locked': run.status in [PayrollRunStatus.APPROVED_LOCKED, PayrollRunStatus.DISBURSED],
            'search': search,
            'selected_dept': dept_id,
            'selected_branch': branch_id,
            'selected_mode': mode,
        })


class PayrollRunSyncView(PayrollManagerMixin, View):
    def post(self, request, pk):
        run = get_object_or_404(PayrollRun, pk=pk)
        if run.status in [PayrollRunStatus.APPROVED_LOCKED, PayrollRunStatus.DISBURSED]:
            messages.error(request, "Cannot synchronize an Approved/Locked or Disbursed payroll run.")
            return redirect('payroll:payroll_run_detail', pk=run.pk)

        try:
            calcs = PayrollService.sync_payroll_inputs(run)
            messages.success(request, f"Successfully synchronized attendance and leave metrics for {len(calcs)} employee(s).")
        except ValidationError as e:
            messages.error(request, str(e.message if hasattr(e, 'message') else e))
        except Exception as e:
            messages.error(request, f"Sync error: {str(e)}")

        return redirect('payroll:payroll_run_detail', pk=run.pk)


class PayrollRunTransitionView(PayrollManagerMixin, View):
    def post(self, request, pk):
        run = get_object_or_404(PayrollRun, pk=pk)
        target_status = request.POST.get('target_status')
        note = request.POST.get('note', '').strip()

        if not target_status:
            messages.error(request, "Target status is required.")
            return redirect('payroll:payroll_run_detail', pk=run.pk)

        try:
            PayrollService.transition_payroll_status(
                payroll_run=run,
                target_status=target_status,
                user=request.user,
                note=note
            )
            messages.success(request, f"Payroll run moved to {run.get_status_display()}.")
        except ValidationError as e:
            messages.error(request, str(e.message if hasattr(e, 'message') else e))

        return redirect('payroll:payroll_run_detail', pk=run.pk)


class PayrollRunReverseView(AdminRequiredMixin, View):
    def post(self, request, pk):
        run = get_object_or_404(PayrollRun, pk=pk)
        note = request.POST.get('note', '').strip()

        try:
            PayrollService.reverse_payroll_run(
                payroll_run=run,
                user=request.user,
                note=note
            )
            messages.success(request, "Payroll run has been reversed back to Draft. Previous calculations saved to audit.")
        except ValidationError as e:
            messages.error(request, str(e.message if hasattr(e, 'message') else e))

        return redirect('payroll:payroll_run_detail', pk=run.pk)


class PayrollAdjustmentModalView(PayrollManagerMixin, View):
    def get(self, request, pk):
        run = get_object_or_404(PayrollRun, pk=pk)
        employee_id = request.GET.get('employee_id')
        employee = get_object_or_404(Employee, pk=employee_id)

        adjustments = PayrollAdjustment.objects.filter(
            payroll_run=run,
            employee=employee
        ).select_related('component', 'created_by').order_by('-created_at')

        components = SalaryComponent.objects.filter(is_active=True).order_by('type', 'name')
        is_locked = run.status in [PayrollRunStatus.APPROVED_LOCKED, PayrollRunStatus.DISBURSED]

        return render(request, 'payroll/adjustments_modal_partial.html', {
            'run': run,
            'employee': employee,
            'adjustments': adjustments,
            'components': components,
            'is_locked': is_locked,
            'component_types': SalaryComponentType.choices
        })


class PayrollAdjustmentAddView(PayrollManagerMixin, View):
    def post(self, request, pk):
        run = get_object_or_404(PayrollRun, pk=pk)
        if run.status in [PayrollRunStatus.APPROVED_LOCKED, PayrollRunStatus.DISBURSED]:
            return HttpResponseBadRequest("Cannot add adjustments to locked or disbursed payroll runs.")

        employee_id = request.POST.get('employee_id')
        component_id = request.POST.get('component_id')
        amount_str = request.POST.get('amount')
        adj_type = request.POST.get('type')
        reason = request.POST.get('reason', '').strip()

        if not employee_id or not component_id or not amount_str or not adj_type:
            return HttpResponseBadRequest("Missing required fields.")

        employee = get_object_or_404(Employee, pk=employee_id)
        component = get_object_or_404(SalaryComponent, pk=component_id)

        try:
            amount = Decimal(amount_str)
            if amount <= Decimal('0.00'):
                return HttpResponseBadRequest("Amount must be positive.")
        except Exception:
            return HttpResponseBadRequest("Invalid amount.")

        # Create Adjustment
        PayrollAdjustment.objects.create(
            payroll_run=run,
            employee=employee,
            component=component,
            amount=amount,
            type=adj_type,
            reason=reason or f"Manual adjustment for {component.name}",
            created_by=request.user
        )

        # Recalculate employee payroll calculation snapshot
        calc = EmployeePayrollCalculation.objects.filter(payroll_run=run, employee=employee).first()
        if calc:
            PayrollService.run_payroll_for_employee(
                payroll_run=run,
                employee=employee,
                unpaid_absent_days=calc.unpaid_absent_days,
                other_deduction=calc.other_deduction,
                ot_hours=calc.ot_hours,
                synced_at=calc.synced_at,
                source_total_present_days=calc.source_total_present_days,
                source_total_approved_leave_days=calc.source_total_approved_leave_days,
                source_total_approved_ot_hours=calc.source_total_approved_ot_hours
            )

        return redirect(f"{request.path.replace('/add/', '/')}?employee_id={employee.pk}")


class PayrollAdjustmentDeleteView(PayrollManagerMixin, View):
    def post(self, request, pk, adj_pk):
        run = get_object_or_404(PayrollRun, pk=pk)
        if run.status in [PayrollRunStatus.APPROVED_LOCKED, PayrollRunStatus.DISBURSED]:
            return HttpResponseBadRequest("Cannot delete adjustments from locked or disbursed payroll runs.")

        adjustment = get_object_or_404(PayrollAdjustment, pk=adj_pk, payroll_run=run)
        employee = adjustment.employee
        adjustment.delete()

        # Live recalculation
        calc = EmployeePayrollCalculation.objects.filter(payroll_run=run, employee=employee).first()
        if calc:
            PayrollService.run_payroll_for_employee(
                payroll_run=run,
                employee=employee,
                unpaid_absent_days=calc.unpaid_absent_days,
                other_deduction=calc.other_deduction,
                ot_hours=calc.ot_hours,
                synced_at=calc.synced_at,
                source_total_present_days=calc.source_total_present_days,
                source_total_approved_leave_days=calc.source_total_approved_leave_days,
                source_total_approved_ot_hours=calc.source_total_approved_ot_hours
            )

        return redirect(f"/payroll/runs/{run.pk}/adjustments/?employee_id={employee.pk}")


class EmployeePayslipDetailView(LoginRequiredMixin, View):
    def get(self, request, pk):
        calc = get_object_or_404(
            EmployeePayrollCalculation.objects.select_related(
                'payroll_run', 'employee', 'employee__department', 'employee__designation', 'employee__branch',
                'employee__user', 'employee__legacy_profile__user'
            ),
            pk=pk
        )

        # Check authorization
        if not is_payroll_manager(request.user):
            # Must own the calculation
            user_owns = False
            if calc.employee.user == request.user:
                user_owns = True
            elif getattr(calc.employee, 'legacy_profile', None) and calc.employee.legacy_profile.user == request.user:
                user_owns = True
            elif getattr(request.user, 'employee_master', None) and request.user.employee_master == calc.employee:
                user_owns = True

            if not user_owns:
                raise PermissionDenied("You are not authorized to view this payslip.")

        # Breakdown parse
        snapshot = calc.structure_snapshot or {}
        components = snapshot.get('components', [])
        earnings = [c for c in components if c.get('type') == 'earning']
        deductions = [c for c in components if c.get('type') == 'deduction']

        return render(request, 'payroll/payslip_detail.html', {
            'calc': calc,
            'run': calc.payroll_run,
            'employee': calc.employee,
            'earnings': earnings,
            'deductions': deductions,
            'is_manager': is_payroll_manager(request.user),
        })


class EmployeePayslipPDFView(LoginRequiredMixin, View):
    def get(self, request, pk):
        calc = get_object_or_404(
            EmployeePayrollCalculation.objects.select_related(
                'payroll_run', 'employee', 'employee__department', 'employee__designation', 'employee__branch',
                'employee__user', 'employee__legacy_profile__user'
            ),
            pk=pk
        )

        # Authorization check
        if not is_payroll_manager(request.user):
            user_owns = False
            if calc.employee.user == request.user:
                user_owns = True
            elif getattr(calc.employee, 'legacy_profile', None) and calc.employee.legacy_profile.user == request.user:
                user_owns = True
            elif getattr(request.user, 'employee_master', None) and request.user.employee_master == calc.employee:
                user_owns = True

            if not user_owns:
                raise PermissionDenied("You are not authorized to download this payslip.")

        pdf_bytes = generate_payslip_pdf(calc)
        filename = f"Payslip_{calc.employee.employee_number}_{calc.payroll_run.period_start.strftime('%b_%Y')}.pdf"
        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="{filename}"'
        return response


class MyPayslipsView(LoginRequiredMixin, ListView):
    model = EmployeePayrollCalculation
    template_name = 'payroll/my_payslips.html'
    context_object_name = 'payslips'

    def get_queryset(self):
        user = self.request.user
        emp = getattr(user, 'employee_master', None)
        if not emp and hasattr(user, 'employee_profile') and user.employee_profile.master_employee:
            emp = user.employee_profile.master_employee

        if not emp:
            # Fallback search by email / phone
            emp = Employee.objects.filter(Q(personal_email=user.email) | Q(phone=user.phone)).first()

        if not emp:
            return EmployeePayrollCalculation.objects.none()

        return EmployeePayrollCalculation.objects.filter(
            employee=emp
        ).select_related('payroll_run', 'employee').order_by('-payroll_run__period_start')


class PayrollRegisterView(PayrollManagerMixin, DetailView):
    model = PayrollRun
    template_name = 'payroll/payroll_register.html'
    context_object_name = 'run'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        run = self.object
        calcs = EmployeePayrollCalculation.objects.filter(payroll_run=run).select_related(
            'employee', 'employee__department', 'employee__designation'
        ).order_by('employee__employee_number')

        totals = calcs.aggregate(
            tot_gross=Sum('gross_salary'),
            tot_earnings=Sum('total_earnings'),
            tot_deductions=Sum('total_deductions'),
            tot_net=Sum('net_payable'),
            tot_bank=Sum('bank_payable'),
            tot_cash=Sum('cash_payable'),
            tot_ot_hours=Sum('ot_hours'),
            tot_ot_amt=Sum('ot_amount'),
            tot_abs_ded=Sum('absence_deduction'),
        )

        ctx['calculations'] = calcs
        ctx['totals'] = totals
        return ctx


class PayrollRegisterExportView(PayrollManagerMixin, View):
    def get(self, request, pk, format):
        run = get_object_or_404(PayrollRun, pk=pk)
        calcs = EmployeePayrollCalculation.objects.filter(payroll_run=run).select_related(
            'employee', 'employee__department', 'employee__designation'
        ).order_by('employee__employee_number')

        filename_base = f"Payroll_Register_{run.period_start.strftime('%B_%Y')}"

        if format == 'excel':
            content = export_payroll_register_excel(run, calcs)
            response = HttpResponse(content, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            response['Content-Disposition'] = f'attachment; filename="{filename_base}.xlsx"'
            return response
        elif format == 'csv':
            content = export_payroll_register_csv(run, calcs)
            response = HttpResponse(content, content_type='text/csv')
            response['Content-Disposition'] = f'attachment; filename="{filename_base}.csv"'
            return response
        elif format == 'pdf':
            content = export_payroll_register_pdf(run, calcs)
            response = HttpResponse(content, content_type='application/pdf')
            response['Content-Disposition'] = f'inline; filename="{filename_base}.pdf"'
            return response

        return HttpResponseBadRequest("Unsupported format")


class BankReportView(PayrollManagerMixin, DetailView):
    model = PayrollRun
    template_name = 'payroll/bank_report.html'
    context_object_name = 'run'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        run = self.object
        calcs = EmployeePayrollCalculation.objects.filter(
            payroll_run=run,
            bank_payable__gt=Decimal('0.00')
        ).select_related('employee', 'employee__department').order_by('employee__employee_number')

        totals = calcs.aggregate(
            tot_count=Count('id'),
            tot_bank=Sum('bank_payable')
        )
        ctx['calculations'] = calcs
        ctx['totals'] = totals
        return ctx


class BankReportExportView(PayrollManagerMixin, View):
    def get(self, request, pk, format):
        run = get_object_or_404(PayrollRun, pk=pk)
        calcs = EmployeePayrollCalculation.objects.filter(
            payroll_run=run,
            bank_payable__gt=Decimal('0.00')
        ).select_related('employee', 'employee__department').order_by('employee__employee_number')

        filename_base = f"Bank_Salary_Transfer_{run.period_start.strftime('%B_%Y')}"

        if format == 'excel':
            content = export_bank_report_excel(run, calcs)
            response = HttpResponse(content, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            response['Content-Disposition'] = f'attachment; filename="{filename_base}.xlsx"'
            return response
        elif format == 'csv':
            content = export_bank_report_csv(run, calcs)
            response = HttpResponse(content, content_type='text/csv')
            response['Content-Disposition'] = f'attachment; filename="{filename_base}.csv"'
            return response
        elif format == 'pdf':
            content = export_bank_report_pdf(run, calcs)
            response = HttpResponse(content, content_type='application/pdf')
            response['Content-Disposition'] = f'inline; filename="{filename_base}.pdf"'
            return response

        return HttpResponseBadRequest("Unsupported format")


class CashReportView(PayrollManagerMixin, DetailView):
    model = PayrollRun
    template_name = 'payroll/cash_report.html'
    context_object_name = 'run'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        run = self.object
        calcs = EmployeePayrollCalculation.objects.filter(
            payroll_run=run,
            cash_payable__gt=Decimal('0.00')
        ).select_related('employee', 'employee__department').order_by('employee__employee_number')

        totals = calcs.aggregate(
            tot_count=Count('id'),
            tot_cash=Sum('cash_payable')
        )
        ctx['calculations'] = calcs
        ctx['totals'] = totals
        return ctx


class CashReportExportView(PayrollManagerMixin, View):
    def get(self, request, pk, format):
        run = get_object_or_404(PayrollRun, pk=pk)
        calcs = EmployeePayrollCalculation.objects.filter(
            payroll_run=run,
            cash_payable__gt=Decimal('0.00')
        ).select_related('employee', 'employee__department').order_by('employee__employee_number')

        filename_base = f"Cash_Disbursement_{run.period_start.strftime('%B_%Y')}"

        if format == 'excel':
            content = export_cash_report_excel(run, calcs)
            response = HttpResponse(content, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            response['Content-Disposition'] = f'attachment; filename="{filename_base}.xlsx"'
            return response
        elif format == 'csv':
            content = export_cash_report_csv(run, calcs)
            response = HttpResponse(content, content_type='text/csv')
            response['Content-Disposition'] = f'attachment; filename="{filename_base}.csv"'
            return response
        elif format == 'pdf':
            content = export_cash_report_pdf(run, calcs)
            response = HttpResponse(content, content_type='application/pdf')
            response['Content-Disposition'] = f'inline; filename="{filename_base}.pdf"'
            return response

        return HttpResponseBadRequest("Unsupported format")


# --- SALARY COMPONENTS CRUD ---

class SalaryComponentListView(PayrollManagerMixin, ListView):
    model = SalaryComponent
    template_name = 'payroll/salary_components.html'
    context_object_name = 'components'
    paginate_by = 50

    def get_queryset(self):
        qs = SalaryComponent.objects.all().order_by('type', 'code')
        search = self.request.GET.get('search', '').strip()
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(code__icontains=search))
        return qs


class SalaryComponentCreateView(PayrollManagerMixin, View):
    def post(self, request):
        name = request.POST.get('name', '').strip()
        code = request.POST.get('code', '').strip().upper()
        comp_type = request.POST.get('type')
        val_type = request.POST.get('value_type')
        val_str = request.POST.get('value', '0')
        is_pf = request.POST.get('is_pf') == 'on'
        is_active = request.POST.get('is_active', 'on') == 'on'

        if not name or not code or not comp_type or not val_type:
            messages.error(request, "All fields are required.")
            return redirect('payroll:salary_components')

        try:
            val = Decimal(val_str)
        except Exception:
            messages.error(request, "Invalid value amount.")
            return redirect('payroll:salary_components')

        if SalaryComponent.objects.filter(code=code).exists():
            messages.error(request, f"Component with code '{code}' already exists.")
            return redirect('payroll:salary_components')

        SalaryComponent.objects.create(
            name=name,
            code=code,
            type=comp_type,
            value_type=val_type,
            value=val,
            is_pf=is_pf,
            is_active=is_active
        )
        messages.success(request, f"Component '{name}' created successfully.")
        return redirect('payroll:salary_components')


class SalaryComponentUpdateView(PayrollManagerMixin, View):
    def post(self, request, pk):
        comp = get_object_or_404(SalaryComponent, pk=pk)
        
        # Check if component is used in locked payroll runs
        locked_exists = PayrollAdjustment.objects.filter(
            component=comp, 
            payroll_run__status__in=[PayrollRunStatus.APPROVED_LOCKED, PayrollRunStatus.DISBURSED]
        ).exists()
        
        if not locked_exists:
            locked_exists = EmployeePayrollCalculation.objects.filter(
                payroll_run__status__in=[PayrollRunStatus.APPROVED_LOCKED, PayrollRunStatus.DISBURSED],
                employee__salary_assignments__salary_structure__structure_components__salary_component=comp
            ).exists()

        name = request.POST.get('name', '').strip()
        comp_type = request.POST.get('type')
        val_type = request.POST.get('value_type')
        val_str = request.POST.get('value', '0')
        is_pf = request.POST.get('is_pf') == 'on'
        is_active = request.POST.get('is_active') == 'on'

        if locked_exists:
            messages.warning(request, "Component is used in locked payroll runs. Only non-critical fields updated.")
            comp.name = name
            comp.is_active = is_active
            comp.save()
        else:
            try:
                val = Decimal(val_str)
            except Exception:
                messages.error(request, "Invalid value amount.")
                return redirect('payroll:salary_components')

            comp.name = name
            comp.type = comp_type
            comp.value_type = val_type
            comp.value = val
            comp.is_pf = is_pf
            comp.is_active = is_active
            comp.save()

        messages.success(request, f"Component '{comp.code}' updated successfully.")
        return redirect('payroll:salary_components')


class SalaryComponentDeleteView(PayrollManagerMixin, View):
    def post(self, request, pk):
        comp = get_object_or_404(SalaryComponent, pk=pk)
        
        # Check if used in locked payroll runs
        locked_exists = PayrollAdjustment.objects.filter(
            component=comp, 
            payroll_run__status__in=[PayrollRunStatus.APPROVED_LOCKED, PayrollRunStatus.DISBURSED]
        ).exists()
        
        if not locked_exists:
            locked_exists = EmployeePayrollCalculation.objects.filter(
                payroll_run__status__in=[PayrollRunStatus.APPROVED_LOCKED, PayrollRunStatus.DISBURSED],
                employee__salary_assignments__salary_structure__structure_components__salary_component=comp
            ).exists()

        if locked_exists:
            messages.error(request, f"Cannot delete '{comp.code}': It is referenced in locked payroll history.")
        else:
            comp.delete()
            messages.success(request, f"Component '{comp.code}' deleted successfully.")
        
        return redirect('payroll:salary_components')


# --- SALARY STRUCTURES CRUD ---

class SalaryStructureListView(PayrollManagerMixin, ListView):
    model = SalaryStructure
    template_name = 'payroll/salary_structures.html'
    context_object_name = 'structures'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['components'] = SalaryComponent.objects.filter(is_active=True)
        return ctx


class SalaryStructureCreateView(PayrollManagerMixin, View):
    def post(self, request):
        name = request.POST.get('name', '').strip()
        is_active = request.POST.get('is_active', 'on') == 'on'
        
        if not name:
            messages.error(request, "Structure name is required.")
            return redirect('payroll:salary_structures')

        component_ids = request.POST.getlist('component_ids')
        values = request.POST.getlist('component_values')
        val_types = request.POST.getlist('component_value_types')

        pct_earnings_total = Decimal('0.00')
        has_pct_earnings = False
        components_to_create = []

        for cid, val_str, vt in zip(component_ids, values, val_types):
            if not cid:
                continue
            comp = get_object_or_404(SalaryComponent, pk=cid)
            try:
                val = Decimal(val_str)
            except Exception:
                messages.error(request, f"Invalid value for component '{comp.code}'.")
                return redirect('payroll:salary_structures')

            if comp.type == SalaryComponentType.EARNING and vt == SalaryComponentValueType.PERCENTAGE:
                pct_earnings_total += val
                has_pct_earnings = True

            components_to_create.append({
                'component': comp,
                'value': val,
                'value_type': vt
            })

        if has_pct_earnings and pct_earnings_total != Decimal('100.00'):
            messages.error(request, f"Validation failed: Earning percentage components must sum to exactly 100%. Current sum: {pct_earnings_total}%.")
            return redirect('payroll:salary_structures')

        with transaction.atomic():
            struct = SalaryStructure.objects.create(name=name, is_active=is_active)
            for item in components_to_create:
                SalaryStructureComponent.objects.create(
                    salary_structure=struct,
                    salary_component=item['component'],
                    value=item['value'],
                    value_type=item['value_type']
                )

        messages.success(request, f"Salary structure '{name}' created successfully.")
        return redirect('payroll:salary_structures')


class SalaryStructureUpdateView(PayrollManagerMixin, View):
    def post(self, request, pk):
        struct = get_object_or_404(SalaryStructure, pk=pk)
        name = request.POST.get('name', '').strip()
        is_active = request.POST.get('is_active') == 'on'

        if not name:
            messages.error(request, "Structure name is required.")
            return redirect('payroll:salary_structures')

        component_ids = request.POST.getlist('component_ids')
        values = request.POST.getlist('component_values')
        val_types = request.POST.getlist('component_value_types')

        pct_earnings_total = Decimal('0.00')
        has_pct_earnings = False
        components_to_save = []

        for cid, val_str, vt in zip(component_ids, values, val_types):
            if not cid:
                continue
            comp = get_object_or_404(SalaryComponent, pk=cid)
            try:
                val = Decimal(val_str)
            except Exception:
                messages.error(request, f"Invalid value for component '{comp.code}'.")
                return redirect('payroll:salary_structures')

            if comp.type == SalaryComponentType.EARNING and vt == SalaryComponentValueType.PERCENTAGE:
                pct_earnings_total += val
                has_pct_earnings = True

            components_to_save.append({
                'component': comp,
                'value': val,
                'value_type': vt
            })

        if has_pct_earnings and pct_earnings_total != Decimal('100.00'):
            messages.error(request, f"Validation failed: Earning percentage components must sum to exactly 100%. Current sum: {pct_earnings_total}%.")
            return redirect('payroll:salary_structures')

        with transaction.atomic():
            struct.name = name
            struct.is_active = is_active
            struct.save()
            struct.structure_components.all().delete()
            for item in components_to_save:
                SalaryStructureComponent.objects.create(
                    salary_structure=struct,
                    salary_component=item['component'],
                    value=item['value'],
                    value_type=item['value_type']
                )

        messages.success(request, f"Salary structure '{name}' updated successfully.")
        return redirect('payroll:salary_structures')


class SalaryStructureDeleteView(PayrollManagerMixin, View):
    def post(self, request, pk):
        struct = get_object_or_404(SalaryStructure, pk=pk)
        
        if EmployeeSalaryAssignment.objects.filter(salary_structure=struct).exists():
            messages.error(request, f"Cannot delete '{struct.name}': It is assigned to one or more employees.")
        else:
            struct.delete()
            messages.success(request, f"Salary structure '{struct.name}' deleted successfully.")
        return redirect('payroll:salary_structures')


# --- EMPLOYEE SALARY SETUP ---

class EmployeeSalarySetupView(PayrollManagerMixin, ListView):
    model = Employee
    template_name = 'payroll/employee_salary_setup.html'
    context_object_name = 'employees'
    paginate_by = 25

    def get_queryset(self):
        qs = Employee.objects.select_related('department', 'branch').prefetch_related('salary_assignments', 'salary_assignments__salary_structure')
        search = self.request.GET.get('search', '').strip()
        dept_id = self.request.GET.get('department')
        branch_id = self.request.GET.get('branch')
        status = self.request.GET.get('status', 'active')

        if search:
            qs = qs.filter(
                Q(employee_number__icontains=search) |
                Q(first_name__icontains=search) |
                Q(last_name__icontains=search)
            )
        if dept_id:
            qs = qs.filter(department_id=dept_id)
        if branch_id:
            qs = qs.filter(branch_id=branch_id)
        if status == 'active':
            qs = qs.filter(status='active')
        elif status == 'inactive':
            qs = qs.exclude(status='active')

        return qs.order_by('employee_number')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['departments'] = Department.objects.filter(is_active=True)
        ctx['branches'] = Branch.objects.all()
        ctx['structures'] = SalaryStructure.objects.filter(is_active=True)
        ctx['payment_modes'] = PaymentMode.choices
        ctx['selected_dept'] = self.request.GET.get('department', '')
        ctx['selected_branch'] = self.request.GET.get('branch', '')
        ctx['selected_status'] = self.request.GET.get('status', 'active')
        ctx['search'] = self.request.GET.get('search', '')
        
        today = date.today()
        for emp in ctx['employees']:
            active_assign = None
            for assign in emp.salary_assignments.all():
                if assign.effective_from <= today and (assign.effective_to is None or assign.effective_to >= today):
                    active_assign = assign
                    break
            emp.active_salary_assignment = active_assign
            
        return ctx


class EmployeeSalaryAssignmentCreateView(PayrollManagerMixin, View):
    def post(self, request):
        employee_id = request.POST.get('employee_id')
        structure_id = request.POST.get('salary_structure_id')
        gross_salary_str = request.POST.get('gross_salary', '0')
        effective_from_str = request.POST.get('effective_from')
        effective_to_str = request.POST.get('effective_to') or None
        payment_mode = request.POST.get('payment_mode')
        bank_limit_str = request.POST.get('bank_limit', '0')

        employee = get_object_or_404(Employee, pk=employee_id)
        structure = get_object_or_404(SalaryStructure, pk=structure_id)

        try:
            gross_salary = Decimal(gross_salary_str)
            bank_limit = Decimal(bank_limit_str)
            effective_from = datetime.strptime(effective_from_str, '%Y-%m-%d').date()
            effective_to = datetime.strptime(effective_to_str, '%Y-%m-%d').date() if effective_to_str else None
        except Exception:
            messages.error(request, "Invalid numeric or date formats.")
            return redirect('payroll:employee_salary_setup')

        overlaps = EmployeeSalaryAssignment.objects.filter(employee=employee)
        for adj in overlaps:
            start_overlap = (adj.effective_to is None or adj.effective_to >= effective_from)
            end_overlap = (effective_to is None or adj.effective_from <= effective_to)
            if start_overlap and end_overlap:
                messages.error(request, f"Overlapping salary assignment exists for {employee.get_full_name()} during this period.")
                return redirect('payroll:employee_salary_setup')

        EmployeeSalaryAssignment.objects.create(
            employee=employee,
            salary_structure=structure,
            gross_salary=gross_salary,
            effective_from=effective_from,
            effective_to=effective_to,
            payment_mode=payment_mode,
            bank_limit=bank_limit
        )

        messages.success(request, f"Salary setup successfully for {employee.get_full_name()}.")
        return redirect('payroll:employee_salary_setup')


class EmployeeSalaryAssignmentUpdateView(PayrollManagerMixin, View):
    def post(self, request, pk):
        assign = get_object_or_404(EmployeeSalaryAssignment, pk=pk)
        structure_id = request.POST.get('salary_structure_id')
        gross_salary_str = request.POST.get('gross_salary')
        effective_from_str = request.POST.get('effective_from')
        effective_to_str = request.POST.get('effective_to') or None
        payment_mode = request.POST.get('payment_mode')
        bank_limit_str = request.POST.get('bank_limit', '0')

        structure = get_object_or_404(SalaryStructure, pk=structure_id)

        try:
            gross_salary = Decimal(gross_salary_str)
            bank_limit = Decimal(bank_limit_str)
            effective_from = datetime.strptime(effective_from_str, '%Y-%m-%d').date()
            effective_to = datetime.strptime(effective_to_str, '%Y-%m-%d').date() if effective_to_str else None
        except Exception:
            messages.error(request, "Invalid numeric or date formats.")
            return redirect('payroll:employee_salary_setup')

        overlaps = EmployeeSalaryAssignment.objects.filter(employee=assign.employee).exclude(pk=assign.pk)
        for adj in overlaps:
            start_overlap = (adj.effective_to is None or adj.effective_to >= effective_from)
            end_overlap = (effective_to is None or adj.effective_from <= effective_to)
            if start_overlap and end_overlap:
                messages.error(request, f"Overlapping salary assignment exists for {assign.employee.get_full_name()} during this period.")
                return redirect('payroll:employee_salary_setup')

        assign.salary_structure = structure
        assign.gross_salary = gross_salary
        assign.effective_from = effective_from
        assign.effective_to = effective_to
        assign.payment_mode = payment_mode
        assign.bank_limit = bank_limit
        assign.save()

        messages.success(request, f"Salary setup updated successfully for {assign.employee.get_full_name()}.")
        return redirect('payroll:employee_salary_setup')


# --- PAYROLL REPORTS HUB ---

class PayrollReportsHubView(PayrollManagerMixin, TemplateView):
    template_name = 'payroll/reports_hub.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['runs'] = PayrollRun.objects.all().order_by('-period_start')
        return ctx
