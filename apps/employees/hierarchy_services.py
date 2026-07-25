from django.db.models import Count, Q, Avg
from apps.employees.models import Employee, Department, EmployeeStatus
from apps.branches.models import Branch
from django.utils import timezone

class OrgHierarchyService:
    @staticmethod
    def get_direct_reports(employee):
        """Returns direct reports of the employee."""
        return Employee.objects.filter(reporting_manager=employee).select_related(
            'branch', 'department', 'designation', 'user'
        )

    @staticmethod
    def get_all_subordinates(employee):
        """Recursively fetches all subordinates down the tree in a single query by doing in-memory build or recursion."""
        # For reasonable org sizes, we pull all active employees and construct the tree.
        # This avoids recursive DB queries or complex raw SQL CTEs that might fail on SQLite/MySQL/PG difference.
        all_emps = list(Employee.objects.select_related(
            'branch', 'department', 'designation', 'user'
        ).all())
        
        # Build adjacency list
        manager_map = {}
        for emp in all_emps:
            if emp.reporting_manager_id:
                manager_map.setdefault(emp.reporting_manager_id, []).append(emp)
                
        subordinates = []
        queue = [employee.id]
        visited = set()
        
        while queue:
            curr_id = queue.pop(0)
            if curr_id in visited:
                continue
            visited.add(curr_id)
            directs = manager_map.get(curr_id, [])
            for d in directs:
                subordinates.append(d)
                queue.append(d.id)
                
        # Return as queryset
        subordinate_ids = [s.id for s in subordinates]
        return Employee.objects.filter(id__in=subordinate_ids).select_related(
            'branch', 'department', 'designation', 'user'
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
            'branch', 'department', 'designation', 'user'
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
        
        # Maximum org depth
        all_emps = Employee.objects.all()
        max_depth = 0
        for emp in all_emps:
            d = OrgHierarchyService.get_reporting_depth(emp)
            if d > max_depth:
                max_depth = d
                
        return {
            'department_headcounts': dept_counts,
            'branch_headcounts': branch_counts,
            'avg_span_of_control': avg_span,
            'max_depth': max_depth,
            'total_headcount': Employee.objects.exclude(status='archived').count()
        }
