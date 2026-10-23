import sqlite3
import json
import sys
from collections import defaultdict

def run_audit():
    c_before = sqlite3.connect("before_seed.sqlite3")
    c_cur = sqlite3.connect("db.sqlite3")
    
    # 1. Employee mapping between databases
    # before_seed: employees_employeeprofile (id, employee_id, full_name, user_id, phone)
    b_cur = c_before.cursor()
    b_cur.execute("SELECT id, employee_id, full_name, user_id, phone FROM employees_employeeprofile")
    b_employees = {r[0]: {'employee_id': r[1], 'full_name': r[2], 'user_id': r[3], 'phone': r[4]} for r in b_cur.fetchall()}
    
    cur_cur = c_cur.cursor()
    cur_cur.execute("SELECT id, employee_id, full_name, user_id, phone FROM employees_employeeprofile")
    cur_employees = {r[0]: {'employee_id': r[1], 'full_name': r[2], 'user_id': r[3], 'phone': r[4]} for r in cur_cur.fetchall()}
    
    # Map by employee_id code (e.g. EMP-2026-001)
    b_emp_by_code = {v['employee_id']: k for k, v in b_employees.items() if v['employee_id']}
    cur_emp_by_code = {v['employee_id']: k for k, v in cur_employees.items() if v['employee_id']}

    # 2. Extract all attendance from before_seed.sqlite3
    b_cur.execute("""
        SELECT id, employee_id, date, check_in_time, check_out_time, type, status, note, total_hours, created_at,
               attendance_type, client_name, site_address, visit_title, is_early_checkout, overtime_minutes, photo
        FROM attendance_attendance
    """)
    b_cols = [col[0] for col in b_cur.description]
    b_records = [dict(zip(b_cols, row)) for row in b_cur.fetchall()]
    
    # 3. Extract all attendance from db.sqlite3
    cur_cur.execute("""
        SELECT id, employee_id, date, check_in_time, check_out_time, type, status, note, total_hours, created_at,
               attendance_type, client_name, site_address, visit_title, is_early_checkout, overtime_minutes, photo,
               sync_uuid, client_event_time, synced_at
        FROM attendance_attendance
    """)
    cur_cols = [col[0] for col in cur_cur.description]
    cur_records = [dict(zip(cur_cols, row)) for row in cur_cur.fetchall()]

    print(f"Total attendance records: before_seed={len(b_records)}, current_db={len(cur_records)}")

    # 4. Locations in both DBs
    b_cur.execute("SELECT attendance_id, count(*), group_concat(address, ' | ') FROM attendance_attendancelocation GROUP BY attendance_id")
    b_loc_map = {r[0]: {'count': r[1], 'addresses': r[2]} for r in b_cur.fetchall()}

    cur_cur.execute("SELECT attendance_id, count(*), group_concat(address, ' | ') FROM attendance_attendancelocation GROUP BY attendance_id")
    cur_loc_map = {r[0]: {'count': r[1], 'addresses': r[2]} for r in cur_cur.fetchall()}

    # 5. Overtime, corrections, forgot checkout in current DB
    cur_cur.execute("SELECT attendance_id, id, ot_minutes, status FROM attendance_overtimerequest")
    cur_ot_map = {r[0]: {'id': r[1], 'minutes': r[2], 'status': r[3]} for r in cur_cur.fetchall()}

    cur_cur.execute("SELECT attendance_id, id, reason, status FROM attendance_attendancecorrectionrequest")
    cur_corr_map = {r[0]: {'id': r[1], 'reason': r[2], 'status': r[3]} for r in cur_cur.fetchall()}

    cur_cur.execute("SELECT attendance_id, id, reason, status FROM attendance_forgotcheckoutrequest")
    cur_forgot_map = {r[0]: {'id': r[1], 'reason': r[2], 'status': r[3]} for r in cur_cur.fetchall()}

    # Let's index before_seed records by content signature:
    # Signature: (emp_code, date, str(check_in_time)[:16])
    def make_sig(r, emp_map):
        emp_info = emp_map.get(r['employee_id'], {})
        emp_code = emp_info.get('employee_id') or f"id_{r['employee_id']}"
        ci = str(r['check_in_time'])[:16] if r['check_in_time'] else 'None'
        return (emp_code, str(r['date']), ci)

    b_sig_map = {}
    for r in b_records:
        sig = make_sig(r, b_employees)
        b_sig_map[sig] = r

    # Classify each current_db record
    confirmed_real = []
    confirmed_seed = []
    uncertain = []

    # Known seed markers
    # from seed_operational_data.py:
    # note in ("Standard site & project operations", "Regular office shift")
    # client_name == "Signtech Commercial HVAC Clients"
    # visit_title == "HVAC Site Inspection & Ducting"
    # address in ("Dhaka HVAC Project Site", "Baitul Mukarram, Dhaka")
    # check_in_time has seconds == 00, check_out_time has seconds == 00
    # and date between start_date and end_date

    for r in cur_records:
        cur_id = r['id']
        sig = make_sig(r, cur_employees)
        note = r.get('note') or ''
        client = r.get('client_name') or ''
        visit = r.get('visit_title') or ''
        loc_info = cur_loc_map.get(cur_id, {})
        addresses = loc_info.get('addresses') or ''

        # Match against before_seed
        if sig in b_sig_map:
            # Matched exact employee, date, and check_in time!
            b_match = b_sig_map[sig]
            confirmed_real.append({
                'id': cur_id,
                'before_id': b_match['id'],
                'employee': cur_employees.get(r['employee_id'], {}).get('full_name'),
                'date': str(r['date']),
                'check_in': str(r['check_in_time']),
                'reason': 'Exact match with before_seed.sqlite3 (employee_code, date, check_in_time)'
            })
        else:
            # Does this record match seed generator patterns?
            is_seed_pattern = (
                note in ["Standard site & project operations", "Regular office shift"]
                or client == "Signtech Commercial HVAC Clients"
                or visit == "HVAC Site Inspection & Ducting"
                or "Dhaka HVAC Project Site" in addresses
            )
            
            # Check if this record is on or between Oct 9 and Oct 23, 2026
            r_date = str(r['date'])
            if '2026-10-09' <= r_date <= '2026-10-24':
                uncertain.append({
                    'id': cur_id,
                    'employee': cur_employees.get(r['employee_id'], {}).get('full_name'),
                    'date': r_date,
                    'check_in': str(r['check_in_time']),
                    'check_out': str(r['check_out_time']),
                    'note': note,
                    'client': client,
                    'is_seed_pattern': is_seed_pattern,
                    'reason': 'Activity between Oct 9-23, 2026 requiring investigation'
                })
            elif is_seed_pattern:
                confirmed_seed.append({
                    'id': cur_id,
                    'employee': cur_employees.get(r['employee_id'], {}).get('full_name'),
                    'date': r_date,
                    'check_in': str(r['check_in_time']),
                    'check_out': str(r['check_out_time']),
                    'note': note,
                    'client': client,
                    'addresses': addresses,
                    'reason': 'Matches deterministic seed_operational_data signature (note/client/address)'
                })
            else:
                # Check if it might be an un-matched real record (e.g. slight time drift or ID re-assignment)
                # Let's search if before_seed had an attendance on this date for this employee
                emp_code = cur_employees.get(r['employee_id'], {}).get('employee_id')
                b_emp_id = b_emp_by_code.get(emp_code)
                b_same_day = [br for br in b_records if br['employee_id'] == b_emp_id and str(br['date']) == r_date]
                if b_same_day:
                    uncertain.append({
                        'id': cur_id,
                        'employee': cur_employees.get(r['employee_id'], {}).get('full_name'),
                        'date': r_date,
                        'check_in': str(r['check_in_time']),
                        'before_same_day_id': [x['id'] for x in b_same_day],
                        'before_same_day_ci': [str(x['check_in_time']) for x in b_same_day],
                        'reason': 'Same employee and date in before_seed but check_in_time differs'
                    })
                else:
                    uncertain.append({
                        'id': cur_id,
                        'employee': cur_employees.get(r['employee_id'], {}).get('full_name'),
                        'date': r_date,
                        'check_in': str(r['check_in_time']),
                        'note': note,
                        'reason': 'Not in before_seed and does not match explicit seed note'
                    })

    print(f"\nClassification results:")
    print(f"  Confirmed Real: {len(confirmed_real)}")
    print(f"  Confirmed Seed: {len(confirmed_seed)}")
    print(f"  Uncertain / Investigation: {len(uncertain)}")

    # Let's inspect uncertain records
    if uncertain:
        print(f"\nFound {len(uncertain)} uncertain records.")

    # Let's check candidate seed IDs summary
    seed_ids = [s['id'] for s in confirmed_seed]
    print(f"\nConfirmed Seed ID range: min={min(seed_ids) if seed_ids else None}, max={max(seed_ids) if seed_ids else None}, count={len(seed_ids)}")

    # Check related entities impacted by candidate seed records
    ot_impacted = [cur_ot_map[sid] for sid in seed_ids if sid in cur_ot_map]
    corr_impacted = [cur_corr_map[sid] for sid in seed_ids if sid in cur_corr_map]
    forgot_impacted = [cur_forgot_map[sid] for sid in seed_ids if sid in cur_forgot_map]

    print(f"\nRelated entities attached to Confirmed Seed records:")
    print(f"  OvertimeRequests attached to seed: {len(ot_impacted)}")
    print(f"  CorrectionRequests attached to seed: {len(corr_impacted)}")
    print(f"  ForgotCheckoutRequests attached to seed: {len(forgot_impacted)}")

    # Save full audit JSON
    audit_summary = {
        'total_before_seed': len(b_records),
        'total_current_db': len(cur_records),
        'confirmed_real_count': len(confirmed_real),
        'confirmed_seed_count': len(confirmed_seed),
        'uncertain_count': len(uncertain),
        'uncertain_records': uncertain,
        'seed_id_sample': seed_ids[:20],
        'seed_id_range': [min(seed_ids) if seed_ids else None, max(seed_ids) if seed_ids else None],
        'ot_impacted_count': len(ot_impacted),
        'corr_impacted_count': len(corr_impacted),
        'forgot_impacted_count': len(forgot_impacted),
    }

    with open("scratch_audit_results.json", "w", encoding="utf-8") as f:
        json.dump(audit_summary, f, indent=2, ensure_ascii=False)
    print("\nWrote scratch_audit_results.json")

run_audit()
