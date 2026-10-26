#!/usr/bin/env python3
"""
FieldTrack Attendance Photo Pipeline Benchmark Harness
Measures:
1. Browser preparation latency (ms)
2. Payload bytes reduction (original vs prepared)
3. Server response latency (upload + ImageKit processing) before vs after
4. Server ImageKit / PIL processing time separately
"""

import os
import sys
import time
import io
import json
import uuid
import math
import random
from PIL import Image, ImageDraw

# Setup Django environment
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'fieldtrack.settings')
import django
django.setup()

from django.test import Client
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from apps.employees.models import EmployeeProfile
from apps.branches.models import Branch
from apps.attendance.models import Attendance, AttendancePolicy
from apps.attendance.transaction_service import AttendanceTransactionService

User = get_user_model()


def generate_realistic_photo(width, height, target_mb=3.5):
    """
    Generates a realistic camera photo with varied color gradients, patterns,
    and texture so that JPEG compression yields an actual 2.5-5MB file.
    """
    img = Image.new('RGB', (width, height), color=(120, 150, 180))
    draw = ImageDraw.Draw(img)

    # Draw complex intersecting shapes and color patterns
    random.seed(42)
    step = max(20, width // 60)
    for y in range(0, height, step):
        r = int(128 + 120 * math.sin(y / 150.0))
        g = int(128 + 120 * math.cos(y / 200.0))
        b = int(128 + 120 * math.sin(y / 300.0))
        draw.line([(0, y), (width, y)], fill=(r, g, b), width=step)

    # Draw diagonal textured lines
    for i in range(0, width + height, step * 2):
        draw.line([(0, i), (i, 0)], fill=(random.randint(50, 220), random.randint(50, 220), random.randint(50, 220)), width=3)

    # Add realistic photographic noise / entropy
    noise_bytes = os.urandom(width * height * 3)
    noise_img = Image.frombytes('RGB', (width, height), noise_bytes)
    img = Image.blend(img, noise_img, 0.12)

    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=88)
    buf.seek(0)
    return img, buf.getvalue()


def run_benchmarks():
    print("=" * 80)
    print("FIELDTRACK ATTENDANCE PHOTO PIPELINE PERFORMANCE BENCHMARK")
    print("=" * 80)
    
    # Setup test database user/branch/policy
    branch, _ = Branch.objects.get_or_create(
        name='Benchmark Branch',
        defaults={'latitude': 23.8103, 'longitude': 90.4125}
    )
    user, _ = User.objects.get_or_create(
        email='benchmark_runner@example.com',
        defaults={'password': 'BenchPassword123!'}
    )
    employee, _ = EmployeeProfile.objects.get_or_create(
        user=user,
        defaults={
            'employee_id': 'BENCH-01',
            'full_name': 'Benchmark Employee',
            'phone': '01711223344',
            'joined_date': '2026-01-01',
            'branch': branch,
            'is_active': True
        }
    )
    policy, _ = AttendancePolicy.objects.get_or_create(
        branch=branch,
        defaults={'photo_required': True, 'geofencing_policy': 'disabled'}
    )

    test_scenarios = [
        {
            'name': 'Camera 4K Landscape (4032x3024)',
            'width': 4032,
            'height': 3024,
            'aspect': '4:3 Landscape'
        },
        {
            'name': 'Camera Portrait EXIF (3024x4032)',
            'width': 3024,
            'height': 4032,
            'aspect': '3:4 Portrait'
        },
        {
            'name': 'High-Res Wide Photo (3840x2160)',
            'width': 3840,
            'height': 2160,
            'aspect': '16:9 Landscape'
        }
    ]

    benchmark_rows = []

    for sc in test_scenarios:
        print(f"\nEvaluating: {sc['name']}...")
        img, raw_bytes = generate_realistic_photo(sc['width'], sc['height'])
        raw_size = len(raw_bytes)
        print(f"  Raw image generated: {sc['width']}x{sc['height']}, {raw_size / (1024*1024):.2f} MB ({raw_size:,} bytes)")

        # ── 1. Measure Browser Preparation Helper Simulation ──────────
        # Simulates the browser photo_prep.js pipeline:
        # Aspect-ratio scaling longest edge to 1280px + JPEG 0.8 encode
        longest = max(sc['width'], sc['height'])
        scale = 1280.0 / longest
        target_w = int(round(sc['width'] * scale))
        target_h = int(round(sc['height'] * scale))

        t0 = time.perf_counter()
        # Scale with bilinear/bicubic filter like browser canvas
        resized = img.resize((target_w, target_h), Image.Resampling.BILINEAR)
        prep_buf = io.BytesIO()
        resized.save(prep_buf, format='JPEG', quality=80)
        t1 = time.perf_counter()

        prep_bytes = prep_buf.getvalue()
        prep_size = len(prep_bytes)
        browser_prep_ms = (t1 - t0) * 1000.0

        reduction_pct = ((raw_size - prep_size) / raw_size) * 100.0
        print(f"  Prepared: {target_w}x{target_h}, {prep_size / 1024:.1f} KB ({prep_size:,} bytes) in {browser_prep_ms:.1f} ms")
        print(f"  Payload reduction: -{reduction_pct:.1f}%")

        # ── 2. Measure Server-Side Processing for Raw vs Prepared ─────
        # Benchmark server ImageKit & Transaction Service
        # A. Baseline (Raw Upload):
        raw_upload = SimpleUploadedFile('raw_selfie.jpg', raw_bytes, content_type='image/jpeg')
        raw_uuid = str(uuid.uuid4())
        data_raw = {
            'latitude': 23.8103,
            'longitude': 90.4125,
            'sync_uuid': raw_uuid,
            'client_event_time': '2026-10-26T08:00:00Z'
        }

        # Clear existing sessions for this date to allow check-in
        Attendance.objects.filter(employee=employee).delete()

        st0 = time.perf_counter()
        res_raw = AttendanceTransactionService.check_in(user, data_raw, photo=raw_upload, validate_photo=True)
        st1 = time.perf_counter()
        server_raw_ms = (st1 - st0) * 1000.0

        # B. Optimized (Prepared Upload):
        prep_upload = SimpleUploadedFile('prepared_selfie.jpg', prep_bytes, content_type='image/jpeg')
        prep_uuid = str(uuid.uuid4())
        data_prep = {
            'latitude': 23.8103,
            'longitude': 90.4125,
            'sync_uuid': prep_uuid,
            'client_event_time': '2026-10-26T08:00:00Z'
        }

        Attendance.objects.filter(employee=employee).delete()

        opt0 = time.perf_counter()
        res_prep = AttendanceTransactionService.check_in(user, data_prep, photo=prep_upload, validate_photo=True)
        opt1 = time.perf_counter()
        server_opt_ms = (opt1 - opt0) * 1000.0

        # Calculate estimated 4G/LTE mobile network transfer latency (assuming 4 Mbps uplink):
        # 4 Mbps = 500 KB/s
        raw_uplink_ms = (raw_size / (500 * 1024)) * 1000.0
        opt_uplink_ms = (prep_size / (500 * 1024)) * 1000.0

        total_raw_ms = raw_uplink_ms + server_raw_ms
        total_opt_ms = browser_prep_ms + opt_uplink_ms + server_opt_ms
        total_saved_ms = total_raw_ms - total_opt_ms
        speedup_factor = total_raw_ms / total_opt_ms if total_opt_ms > 0 else 1.0

        benchmark_rows.append({
            'scenario': sc['name'],
            'orig_dims': f"{sc['width']}x{sc['height']}",
            'prep_dims': f"{target_w}x{target_h}",
            'orig_size_mb': raw_size / (1024 * 1024),
            'prep_size_kb': prep_size / 1024,
            'reduction_pct': reduction_pct,
            'browser_prep_ms': browser_prep_ms,
            'server_raw_ms': server_raw_ms,
            'server_opt_ms': server_opt_ms,
            'server_saving_pct': ((server_raw_ms - server_opt_ms) / server_raw_ms) * 100.0,
            'raw_uplink_ms': raw_uplink_ms,
            'opt_uplink_ms': opt_uplink_ms,
            'total_raw_ms': total_raw_ms,
            'total_opt_ms': total_opt_ms,
            'speedup': speedup_factor
        })

    # Print summary table
    print("\n" + "=" * 105)
    print(f"{'Scenario':<34} | {'Raw Size':<10} | {'Prep Size':<10} | {'Payload -%':<10} | {'Prep ms':<8} | {'Server Latency (Raw -> Opt)':<20}")
    print("-" * 105)
    for r in benchmark_rows:
        print(f"{r['scenario']:<34} | {r['orig_size_mb']:>6.2f} MB | {r['prep_size_kb']:>6.1f} KB | {r['reduction_pct']:>8.1f}% | {r['browser_prep_ms']:>6.1f} ms | {r['server_raw_ms']:>5.1f}ms -> {r['server_opt_ms']:>5.1f}ms (-{r['server_saving_pct']:.0f}%)")
    print("=" * 105)

    print("\n" + "=" * 90)
    print("TOTAL END-TO-END LATENCY (Mobile 4G Network Uplink + Server Processing)")
    print("=" * 90)
    for r in benchmark_rows:
        print(f"{r['scenario']}:")
        print(f"  • Before (Raw Upload):     {r['total_raw_ms']:.0f} ms (Uplink: {r['raw_uplink_ms']:.0f}ms + Server: {r['server_raw_ms']:.1f}ms)")
        print(f"  • After (Client Prepared): {r['total_opt_ms']:.0f} ms (Prep: {r['browser_prep_ms']:.1f}ms + Uplink: {r['opt_uplink_ms']:.0f}ms + Server: {r['server_opt_ms']:.1f}ms)")
        print(f"  • Latency Reduction:       -{r['total_raw_ms'] - r['total_opt_ms']:.0f} ms ({r['speedup']:.1f}x faster!)\n")

    # Output JSON summary for artifact reporting
    output_path = os.path.join(os.path.dirname(__file__), 'benchmark_results.json')
    with open(output_path, 'w') as f:
        json.dump(benchmark_rows, f, indent=2)
    print(f"Detailed benchmark results written to: {output_path}")

    # Clean up test records
    Attendance.objects.filter(employee=employee).delete()
    print("Benchmark complete!")


if __name__ == '__main__':
    run_benchmarks()
