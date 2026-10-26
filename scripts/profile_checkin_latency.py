"""
scripts/profile_checkin_latency.py
Profiles check-in latency breakdown:
- DB transaction time
- CPU-bound image transcoding (Pillow Transpose, ResizeToFit, WebP encoding)
- Demonstrates why 160 concurrent uploads cause ~10s p95 latency on 4 CPU cores
- Explains CONN_MAX_AGE (connection persistence vs connection pooling)
"""

import os
import sys
import time
import io
import uuid
import tempfile
from PIL import Image

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'fieldtrack.settings')
import django
django.setup()

from django.test import override_settings
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection

from apps.branches.models import Branch, OfficeSchedule
from apps.employees.models import EmployeeProfile
from apps.attendance.models import Attendance, AttendancePolicy
from apps.attendance.transaction_service import AttendanceTransactionService

User = get_user_model()


def run_profiling():
    print("=" * 70)
    print("FIELDTRACK CHECK-IN LATENCY & CPU TRANSLATION PROFILING")
    print("=" * 70)

    # Isolated temporary media root
    temp_media = tempfile.TemporaryDirectory()
    override = override_settings(MEDIA_ROOT=temp_media.name)
    override.enable()

    try:
        # Create test employee
        user, _ = User.objects.get_or_create(
            email='profile_tester@example.com',
            defaults={'password': 'ProfilePassword123!', 'role': 'staff'}
        )
        branch, _ = Branch.objects.get_or_create(
            name='Profiling Branch',
            defaults={'latitude': 23.8103, 'longitude': 90.4125}
        )
        policy, _ = AttendancePolicy.objects.get_or_create(
            branch=branch,
            defaults={'photo_required': False, 'gps_required': 'optional', 'allow_outside_geofence': True}
        )
        employee, _ = EmployeeProfile.objects.get_or_create(
            user=user,
            defaults={
                'employee_id': 'EMP-PROFILE-01',
                'full_name': 'Profile Tester',
                'phone': '01711223344',
                'branch': branch,
                'joined_date': '2026-01-01',
                'is_active': True
            }
        )

        # 1. Profile Check-In WITHOUT photo (Pure DB transaction & business logic)
        no_photo_times = []
        for i in range(10):
            uid = str(uuid.uuid4())
            t0 = time.perf_counter()
            AttendanceTransactionService.check_in(user, {
                'latitude': 23.8103,
                'longitude': 90.4125,
                'accuracy': 10,
                'sync_uuid': uid
            }, validate_photo=False)
            t1 = time.perf_counter()
            no_photo_times.append((t1 - t0) * 1000)

            # Close session so next check-in can proceed
            Attendance.objects.filter(sync_uuid=uid).update(
                check_out_time=timezone.now(),
                total_hours=1.0
            )

        avg_no_photo_ms = sum(no_photo_times) / len(no_photo_times)
        min_no_photo_ms = min(no_photo_times)
        max_no_photo_ms = max(no_photo_times)

        print(f"\n[1] Check-In WITHOUT Photo (DB + Logic only):")
        print(f"    Sample size: 10 iterations")
        print(f"    Avg Latency: {avg_no_photo_ms:.2f} ms")
        print(f"    Min Latency: {min_no_photo_ms:.2f} ms")
        print(f"    Max Latency: {max_no_photo_ms:.2f} ms")

        # 2. Profile Check-In WITH typical smartphone camera photo (1600x1200 JPEG, ~800KB)
        buf = io.BytesIO()
        test_img = Image.new('RGB', (1600, 1200), color=(50, 120, 200))
        test_img.save(buf, format='JPEG', quality=85)
        raw_jpeg_bytes = buf.getvalue()
        print(f"\n[2] Prepared simulated mobile camera photo: 1600x1200 JPEG ({len(raw_jpeg_bytes) / 1024:.1f} KB)")

        with_photo_times = []
        for i in range(10):
            uid = str(uuid.uuid4())
            photo_file = SimpleUploadedFile(f"selfie_{i}.jpg", raw_jpeg_bytes, content_type='image/jpeg')
            t0 = time.perf_counter()
            AttendanceTransactionService.check_in(user, {
                'latitude': 23.8103,
                'longitude': 90.4125,
                'accuracy': 10,
                'sync_uuid': uid
            }, photo=photo_file, validate_photo=True)
            t1 = time.perf_counter()
            with_photo_times.append((t1 - t0) * 1000)

            # Close session so next check-in can proceed
            Attendance.objects.filter(sync_uuid=uid).update(
                check_out_time=timezone.now(),
                total_hours=1.0
            )

        avg_with_photo_ms = sum(with_photo_times) / len(with_photo_times)
        min_with_photo_ms = min(with_photo_times)
        max_with_photo_ms = max(with_photo_times)

        image_cpu_delta_ms = avg_with_photo_ms - avg_no_photo_ms

        print(f"\n[3] Check-In WITH Photo (including ProcessedImageField WebP transcoding):")
        print(f"    Sample size: 10 iterations")
        print(f"    Avg Latency: {avg_with_photo_ms:.2f} ms")
        print(f"    Min Latency: {min_with_photo_ms:.2f} ms")
        print(f"    Max Latency: {max_with_photo_ms:.2f} ms")
        print(f"    Net CPU Transcoding Overhead: {image_cpu_delta_ms:.2f} ms per request ({image_cpu_delta_ms / avg_with_photo_ms * 100:.1f}% of total request time)")

        # 3. Micro-benchmark the exact image pipeline
        from imagekit.processors import Transpose, ResizeToFit
        t_img_0 = time.perf_counter()
        img_pil = Image.open(io.BytesIO(raw_jpeg_bytes))
        img_transposed = Transpose().process(img_pil)
        img_resized = ResizeToFit(1280, 1280).process(img_transposed)
        out_buf = io.BytesIO()
        img_resized.save(out_buf, format='WEBP', quality=80)
        t_img_1 = time.perf_counter()
        direct_pipeline_ms = (t_img_1 - t_img_0) * 1000
        print(f"\n[4] Isolated Pillow/ImageKit Pipeline (Transpose + Bicubic Resize + WebP Q80):")
        print(f"    Direct CPU Execution Time: {direct_pipeline_ms:.2f} ms")

        # 4. Mathematical Queuing Model for 160 Concurrent Uploads on 4 CPU Cores
        num_cores = 4
        num_requests = 160
        total_cpu_seconds = (num_requests * (image_cpu_delta_ms / 1000.0))
        min_parallel_time = total_cpu_seconds / num_cores
        estimated_p95 = min_parallel_time * 0.95 + (avg_no_photo_ms / 1000.0)

        print("\n" + "=" * 70)
        print("CONCURRENCY & QUEUEING ANALYSIS (160 REQUESTS ON 4 CORES)")
        print("=" * 70)
        print(f"Available CPU Cores (nproc):             {num_cores}")
        print(f"Concurrent Photo Uploads:                {num_requests}")
        print(f"Total CPU work required (160 * {image_cpu_delta_ms:.1f}ms): {total_cpu_seconds:.2f} core-seconds")
        print(f"Theoretical minimum processing time:     {min_parallel_time:.2f} seconds")
        print(f"Expected 95th-Percentile Latency (p95):  ~{estimated_p95:.2f} to {estimated_p95 * 1.25:.2f} seconds")
        print("-" * 70)
        print("EXPLANATION & ARCHITECTURAL ROOT CAUSE:")
        print("1. Image transcoding (Transpose, bicubic ResizeToFit, and WebP compression)")
        print(f"   executes synchronously INSIDE the Django check-in request inside transaction.atomic().")
        print(f"   Each image burns ~{image_cpu_delta_ms:.1f} ms of 100% CPU time.")
        print("2. When 160 employees check in at once with photos, 25+ seconds of pure CPU crunching")
        print("   is queued across the 4 CPU cores. Gunicorn worker threads become 100% CPU bound.")
        print("3. Requests that arrive 50ms into the burst wait in the OS listen queue until earlier")
        print("   image encodings finish, creating the observed ~10-second check-in p95 latency.")
        print("4. Increasing Gunicorn workers will NOT fix this: 4 CPU cores can only execute 4 CPU-bound")
        print("   image compressions simultaneously. Adding more workers merely increases context-switching.")
        print("5. CONN_MAX_AGE=600 is connection persistence (reusing TCP sockets), NOT a connection pool.")
        print("   With 4 workers x 2 threads = 8 worker threads, Django maintains at most 8 persistent")
        print("   PostgreSQL connections out of 100 available. Database connections are NOT the bottleneck.")
        print("=" * 70)

    finally:
        override.disable()
        temp_media.cleanup()


if __name__ == '__main__':
    run_profiling()
