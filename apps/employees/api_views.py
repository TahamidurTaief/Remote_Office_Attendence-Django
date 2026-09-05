from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views import View
from django.utils.decorators import method_decorator
from django.contrib.auth.decorators import login_required
from apps.employees.models import Employee
from apps.employees.hierarchy_services import OrgHierarchyService

def check_employee_access(requesting_user, target_employee):
    if requesting_user.is_superuser:
        return True

    from apps.accounts.engine import PermissionEngine
    eval_res = PermissionEngine.evaluate(requesting_user, 'employees.view')
    if not eval_res.allowed:
        return False

    scope = eval_res.data_scope
    if scope == 'global':
        return True

    req_profile = getattr(requesting_user, 'employee_profile', None)
    if not req_profile:
        return False

    req_master = getattr(req_profile, 'master_employee', None)
    if not req_master:
        return False

    if scope == 'branch':
        return target_employee.branch == req_master.branch
    elif scope == 'department':
        return target_employee.department == req_master.department
    elif scope in ('team', 'own'):
        if target_employee == req_master:
            return True
        return OrgHierarchyService.is_manager_of(req_master, target_employee)

    return False

def check_analytics_access(requesting_user):
    if requesting_user.is_superuser:
        return True

    from apps.accounts.engine import PermissionEngine
    eval_res = PermissionEngine.evaluate(requesting_user, 'employees.view')
    return eval_res.allowed and eval_res.data_scope == 'global'

def serialize_employee(employee):
    avatar_url = ""
    profile = getattr(employee, 'legacy_profile', None)
    if profile and profile.profile_photo:
        avatar_url = profile.profile_photo.url
        
    return {
        'id': employee.id,
        'employee_number': employee.employee_number,
        'first_name': employee.first_name,
        'last_name': employee.last_name,
        'full_name': employee.get_full_name(),
        'role': employee.user.role if employee.user else "",
        'department': employee.department.name if employee.department else "",
        'designation': employee.designation.name if employee.designation else "",
        'avatar': avatar_url
    }

def paginate_queryset(request, queryset):
    from django.core.paginator import Paginator
    page_number = request.GET.get('page', 1)
    page_size = request.GET.get('page_size', 20)
    
    paginator = Paginator(queryset, page_size)
    page_obj = paginator.get_page(page_number)
    
    serialized_data = [serialize_employee(emp) for emp in page_obj]
    
    return {
        'results': serialized_data,
        'count': paginator.count,
        'num_pages': paginator.num_pages,
        'current_page': page_obj.number,
        'has_next': page_obj.has_next(),
        'has_previous': page_obj.has_previous()
    }

@method_decorator(login_required, name='dispatch')
class SubordinatesAPIView(View):
    def get(self, request, pk):
        employee = get_object_or_404(Employee, pk=pk)
        if not check_employee_access(request.user, employee):
            return JsonResponse({'error': 'Permission denied'}, status=403)
            
        subs = OrgHierarchyService.get_all_subordinates(employee)
        subs_list = sorted(list(subs), key=lambda x: x.employee_number)
        data = paginate_queryset(request, subs_list)
        return JsonResponse(data)

@method_decorator(login_required, name='dispatch')
class DirectReportsAPIView(View):
    def get(self, request, pk):
        employee = get_object_or_404(Employee, pk=pk)
        if not check_employee_access(request.user, employee):
            return JsonResponse({'error': 'Permission denied'}, status=403)
            
        directs = OrgHierarchyService.get_direct_reports(employee)
        data = paginate_queryset(request, directs)
        return JsonResponse(data)

@method_decorator(login_required, name='dispatch')
class OrgChainAPIView(View):
    def get(self, request, pk):
        employee = get_object_or_404(Employee, pk=pk)
        if not check_employee_access(request.user, employee):
            return JsonResponse({'error': 'Permission denied'}, status=403)
            
        chain = OrgHierarchyService.get_management_chain(employee)
        serialized_chain = [serialize_employee(emp) for emp in chain]
        return JsonResponse({'results': serialized_chain, 'count': len(serialized_chain)})

@method_decorator(login_required, name='dispatch')
class OrgAnalyticsAPIView(View):
    def get(self, request):
        if not check_analytics_access(request.user):
            return JsonResponse({'error': 'Permission denied'}, status=403)
            
        analytics = OrgHierarchyService.get_org_analytics()
        return JsonResponse(analytics)

@method_decorator(login_required, name='dispatch')
class IsManagerAPIView(View):
    def get(self, request, pk, target_pk):
        manager = get_object_or_404(Employee, pk=pk)
        target = get_object_or_404(Employee, pk=target_pk)
        
        if not check_employee_access(request.user, manager) or not check_employee_access(request.user, target):
            return JsonResponse({'error': 'Permission denied'}, status=403)
            
        is_mgr = OrgHierarchyService.is_manager_of(manager, target)
        return JsonResponse({'is_manager': is_mgr})
