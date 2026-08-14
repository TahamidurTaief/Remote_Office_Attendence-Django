from django.db.models import Count, Q, Avg
from apps.employees.models import Employee, Department, EmployeeStatus
from apps.branches.models import Branch
from django.utils import timezone

class OrgHierarchyService:
    @staticmethod
    def get_direct_reports(employee):
        """Returns direct reports of the employee."""
        return Employee.objects.filter(reporting_manager=employee).select_related(
            'branch', 'department', 'designation', 'user', 'legacy_profile'
        )

    @staticmethod
    def get_all_subordinates(employee):
        """Recursively fetches all subordinates down the tree level-by-level to avoid loading all active employees into memory."""
        subordinate_ids = []
        current_level_ids = [employee.id]
        depth = 0
        max_depth = 20  # safety cap to prevent infinite loops

        while current_level_ids and depth < max_depth:
            # Query the next level of direct reports
            next_level_ids = list(Employee.objects.filter(
                reporting_manager_id__in=current_level_ids
            ).exclude(status='archived').filter(is_trashed=False).values_list('id', flat=True))
            
            subordinate_ids.extend(next_level_ids)
            current_level_ids = next_level_ids
            depth += 1

        return Employee.objects.filter(id__in=subordinate_ids).select_related(
            'branch', 'department', 'designation', 'user', 'legacy_profile'
        )

    @staticmethod
    def get_management_chain(employee):
        """Returns a list of managers from the direct manager up to the top CEO/executive."""
        chain = []
        curr = employee.reporting_manager
        visited = set()
        while curr:
            if curr.id in visited:
                break # prevent loop
            visited.add(curr.id)
            chain.append(curr)
            curr = curr.reporting_manager
        return chain

    @staticmethod
    def get_reporting_depth(employee):
        """Returns depth level of the employee in the hierarchy tree (CEO = 1, CEO report = 2, etc.)."""
        depth = 1
        curr = employee.reporting_manager
        visited = set()
        while curr:
            if curr.id in visited:
                break
            visited.add(curr.id)
            depth += 1
            curr = curr.reporting_manager
        return depth

    @staticmethod
    def is_manager_of(employee_a, employee_b):
        """Returns True if employee_a is in employee_b's management chain (any level)."""
        if not employee_a or not employee_b:
            return False
        curr = employee_b.reporting_manager
        visited = set()
        while curr:
            if curr.id == employee_a.id:
                return True
            if curr.id in visited:
                break
            visited.add(curr.id)
            curr = curr.reporting_manager
        return False

    @staticmethod
    def get_subordinate_scoped_queryset(manager_employee):
        """Returns all employees that a manager can view/approve (including themselves and their subordinates)."""
        subordinate_ids = list(OrgHierarchyService.get_all_subordinates(manager_employee).values_list('id', flat=True))
        subordinate_ids.append(manager_employee.id)
        return Employee.objects.filter(id__in=subordinate_ids).select_related(
            'branch', 'department', 'designation', 'user', 'legacy_profile'
        )

    @staticmethod
    def get_org_analytics():
        """Returns headcounts per department/branch, average span of control, tree depth stats."""
        # Headcount per department
        dept_counts = list(Department.objects.annotate(
            headcount=Count('employees')
        ).values('name', 'headcount'))
        
        # Headcount per branch
        branch_counts = list(Branch.objects.annotate(
            headcount=Count('master_employees')
        ).values('name', 'headcount'))
        
        # Span of control: reports per manager
        # Find all employees who are reporting managers (count > 0 direct reports)
        managers = Employee.objects.filter(direct_reports__isnull=False).distinct()
        total_managers = managers.count()
        total_reports = Employee.objects.filter(reporting_manager__isnull=False).count()
        avg_span = round(total_reports / total_managers, 2) if total_managers > 0 else 0.0
        
        # Maximum org depth calculated in-memory (0 extra queries)
        all_emps_data = dict(Employee.objects.values_list('id', 'reporting_manager_id'))
        depths = {}
        def compute_depth(emp_id, path=None):
            if path is None:
                path = set()
            if emp_id in depths:
                return depths[emp_id]
            if emp_id in path:
                return 1 # Cycle detected
            parent_id = all_emps_data.get(emp_id)
            if not parent_id:
                depths[emp_id] = 1
            else:
                path.add(emp_id)
                depths[emp_id] = 1 + compute_depth(parent_id, path)
                path.remove(emp_id)
            return depths[emp_id]

        max_depth = 0
        for emp_id in all_emps_data:
            d = compute_depth(emp_id)
            if d > max_depth:
                max_depth = d
                
        return {
            'department_headcounts': dept_counts,
            'branch_headcounts': branch_counts,
            'avg_span_of_control': avg_span,
            'max_depth': max_depth,
            'total_headcount': Employee.objects.exclude(status='archived').filter(is_trashed=False).count()
        }
