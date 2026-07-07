import random
import datetime
from django.core.management.base import BaseCommand
from django.utils import timezone
from apps.accounts.models import CustomUser
from apps.branches.models import Branch
from apps.employees.models import EmployeeProfile
from apps.attendance.models import Attendance, AttendanceLocation

class Command(BaseCommand):
    help = 'Seeds the database with initial sample data'

    def handle(self, *args, **kwargs):
        self.stdout.write('Seeding database...')

        # Create Admin
        if not CustomUser.objects.filter(email='admin@fieldtrack.com').exists():
            CustomUser.objects.create_superuser('admin@fieldtrack.com', 'admin123', role='admin')
            self.stdout.write(self.style.SUCCESS('Admin user created.'))

        # Create Branch
        branch, created = Branch.objects.get_or_create(
            name='Dhaka Head Office',
            defaults={
                'address': 'Gulshan, Dhaka',
                'latitude': 23.810300,
                'longitude': 90.412500,
                'radius_meters': 100,
                'is_active': True
            }
        )

        # Create 3 staff users
        for i in range(1, 4):
            email = f'staff{i}@fieldtrack.com'
            phone = f'+880170000000{i}'
            user, u_created = CustomUser.objects.get_or_create(
                email=email,
                defaults={'role': 'staff', 'is_active': True, 'phone': phone}
            )
            if u_created:
                user.set_password('staff123')
                user.save()
            elif not user.phone:
                user.phone = phone
                user.save()

            emp, e_created = EmployeeProfile.objects.get_or_create(
                user=user,
                defaults={
                    'branch': branch,
                    'employee_id': f'EMP-2026-00{i}',
                    'full_name': f'Staff Member {i}',
                    'department': 'Sales',
                    'designation': 'Field Agent',
                    'phone': phone,
                    'joined_date': timezone.localdate() - datetime.timedelta(days=30),
                    'is_active': True
                }
            )

            # Create 5 days of attendance history
            today = timezone.localdate()
            for day_offset in range(5):
                date = today - datetime.timedelta(days=day_offset)
                
                # Check if attendance already exists
                if not Attendance.objects.filter(employee=emp, date=date).exists():
                    is_late = random.choice([True, False, False, False]) # 25% late
                    
                    ci_hour = 9
                    ci_minute = random.randint(16, 45) if is_late else random.choice([0, random.randint(30, 59)]) # Ensure minutes are valid
                    if not is_late and ci_minute >= 30:
                        ci_hour = 8 # 8:30 to 8:59
                        
                    ci_time = datetime.time(ci_hour, ci_minute)
                    check_in = timezone.make_aware(datetime.datetime.combine(date, ci_time))
                    
                    co_time = datetime.time(17, random.randint(0, 30))
                    check_out = timezone.make_aware(datetime.datetime.combine(date, co_time))
                    
                    duration = check_out - check_in
                    total_hours = round(duration.total_seconds() / 3600.0, 2)
                    
                    att = Attendance.objects.create(
                        employee=emp,
                        date=date,
                        check_in_time=check_in,
                        check_out_time=check_out,
                        type=random.choice(['office', 'field']),
                        status='late' if is_late else 'on_time',
                        total_hours=total_hours
                    )
                    
                    AttendanceLocation.objects.create(
                        attendance=att,
                        event='check_in',
                        latitude=branch.latitude,
                        longitude=branch.longitude,
                        address='Dhaka',
                        accuracy=10,
                        timestamp=check_in
                    )

        self.stdout.write(self.style.SUCCESS('Successfully seeded the database.'))
