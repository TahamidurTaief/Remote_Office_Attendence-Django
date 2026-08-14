import re

def audit_context(request):
    if not request or not hasattr(request, 'path'):
        return {}
    
    path = request.path
    module = ""
    object_type = ""
    object_id = ""
    
    # Check employee path (e.g. /employees/master/123/ or /employees/123/)
    emp_match = re.search(r'/employees/(?:master/)?(\d+)/', path)
    if emp_match:
        module = "employees"
        object_type = "Employee"
        object_id = emp_match.group(1)
    elif path.startswith('/employees/'):
        module = "employees"
        
    proj_match = re.search(r'/projects/(\d+)/', path)
    if proj_match:
        module = "projects"
        object_type = "Project"
        object_id = proj_match.group(1)
    elif path.startswith('/projects/'):
        module = "projects"
        
    task_match = re.search(r'/tasks/(\d+)/', path)
    if task_match:
        module = "projects"
        object_type = "ProjectTask"
        object_id = task_match.group(1)
        
    leave_match = re.search(r'/leave/(\d+)/', path)
    if leave_match:
        module = "leave"
        object_type = "LeaveRequest"
        object_id = leave_match.group(1)
    elif path.startswith('/leave/'):
        module = "leave"
        
    expense_match = re.search(r'/expense/(\d+)/', path)
    if expense_match:
        module = "expense"
        object_type = "Expense"
        object_id = expense_match.group(1)
    elif path.startswith('/expense/'):
        module = "expense"
        
    payroll_match = re.search(r'/payroll/(\d+)/', path)
    if payroll_match:
        module = "payroll"
        object_type = "EmployeePayrollCalculation"
        object_id = payroll_match.group(1)
    elif path.startswith('/payroll/'):
        module = "payroll"
        
    attendance_match = re.search(r'/attendance/(\d+)/', path)
    if attendance_match:
        module = "attendance"
        object_type = "Attendance"
        object_id = attendance_match.group(1)
    elif path.startswith('/attendance/'):
        module = "attendance"

    schedule_match = re.search(r'/schedule/(\d+)/', path)
    if schedule_match:
        module = "schedule"
        object_type = "ScheduleEvent"
        object_id = schedule_match.group(1)
    elif path.startswith('/schedule/'):
        module = "schedule"

    from django.urls import reverse
    try:
        base_url = reverse("audit:activity_list")
        params = []
        if module:
            params.append(f"module={module}")
        if object_id:
            params.append(f"object_id={object_id}")
        if params:
            audit_url = f"{base_url}?{'&'.join(params)}"
        else:
            audit_url = base_url
    except Exception:
        audit_url = "/audit/activity/"
        
    return {
        "current_module": module,
        "current_object_type": object_type,
        "current_object_id": object_id,
        "current_audit_url": audit_url,
    }
