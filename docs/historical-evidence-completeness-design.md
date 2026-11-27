# Historical Evidence Completeness & Retention Provenance Design

**Status:** PROPOSED — Implementation **BLOCKED** pending explicit schema, retention, and operational approval.  
**Scope:** Design specification only (no model edits, no migrations, no historical backfill, no retention execution).

---

## 1. Audit of Current Producers, Consumers & Failure Reproduction

### 1.1 Current Behavior (`no attendance -> history_unavailable`)
In `apps/attendance/reporting_service.py` (`evaluate_day_evidence_state`, lines 695–805):
- Every employee-day without a usable `Attendance` row evaluates `coverage` (`cov_state`).
- Because no durable coverage ledger exists, `coverage_state` defaults to `'unknown'` (`coverage_source='no_coverage_ledger'`, `reason_code='coverage_unknown'`).
- On an employed working day (not holiday, not approved leave, not future, not pending shift), `coverage_unknown` resolves to `history_unavailable` (`absent=0`, `needs_review=True`).

### 1.2 Operational Workflows Blocked
1. **Daily Absence & Leave Deduction (`apps/attendance/management/commands/mark_daily_absences.py`)**: Skips absence confirmation and default-leave deduction whenever `status == 'history_unavailable'`, incrementing `review_needed_count`.
2. **Payroll Preflight (`apps/payroll/services.py`, lines 503–522)**: `PayrollService.sync_payroll_inputs` rejects the entire `PayrollRun` atomically with `ValidationError("Cannot sync payroll: unavailable or expired attendance history requires review...")` if any employee-day in `[period_start, period_end]` is `history_unavailable`.
3. **Reporting & Exports (`apps/attendance/reporting_service.py`, `apps/admin_panel/views.py`, `apps/attendance/pdf_export.py`)**: Surfaces `History Unavailable` / `Review Needed` instead of `Absent` on working days with no punch.

### 1.3 Conflicting Destructive Retention Behavior (Do Not Execute)
In `apps/attendance/retention.py` and `apps/attendance/management/commands/run_retention.py`:
- `mark_expired_records()` sets `is_expired=True, expired_at=now` on `Attendance` rows with `date < today - 3 months`, without recording per-employee-day retention/coverage provenance.
- `delete_old_expired_records()` **permanently deletes** `AttendanceLocation` and `Attendance` rows with `is_expired=True, expired_at < now - 2 months`. Once purged, a previously present or expired day is indistinguishable from an unproven gap.

---

## 2. Separation of Concerns

- **Geographic GPS Coverage** (`AttendanceLocation`, `EmployeeLocationSync`, `accuracy`, `is_outside_geofence`): Measures spatial fix quality for a recorded event. It NEVER attests whether an employee-day's attendance history is complete.
- **Attendance-History Completeness** (`AttendanceCoverageRecord` / `RetentionRunLog`): Attests whether all expected attendance ingestion, offline sync grace, and retention transitions for `(employee, date)` are accounted for. Running `mark_daily_absences` or observing zero `Attendance` rows is **not** independent proof of completeness.

---

## 3. Independent Proof & State Machine

An employee-day `(employee_id, date)` must resolve to one explicit `coverage_state`:

| `coverage_state` | Meaning | Source of Proof |
|---|---|---|
| `complete` | All expected ingestion/sync windows closed with no missing/purged evidence | Explicit coverage attestation created after shift end + offline sync grace (`ATTENDANCE_SYNC_GRACE_MINUTES` / 72h offline window) with no unclosed activity/sync anomalies |
| `incomplete_activity` | Activity or sync evidence indicates a punch existed or failed to land | `AttendanceActivityLog` / `EmployeeLocationSync` / failed `SyncLog` / `is_expired=True` without retention tombstone |
| `purged_retained` | Raw `Attendance` rows were purged by retention, but summary/provenance survives | `RetentionRunLog` + surviving `AttendanceCoverageRecord` tombstone (`purged_at`, `retention_run_id`, `pre_purge_outcome`) |
| `unknown` | No independent completeness attestation and no surviving row (default for historical unproven dates) | Absence of attestation (never inferred from other employees or free-text notes) |

### 3.1 Edge Cases
- **Late Offline Check-in After Absence Processing**:
  - `AttendanceTransactionService.check_in` / `bulk_sync` (within the 72h offline window) writes the `Attendance` row and emits a coverage-reconciliation event updating `AttendanceCoverageRecord` for `(employee, localdate(client_event_time))` to `reconciled_late_offline`, reversing any provisional `AttendanceAbsentLog` / leave deduction only via an explicit audited reversal record.
- **Retention Purge Preservation**:
  - Before `delete_old_expired_records()` may purge any `Attendance` row, it MUST first persist a `RetentionRunLog` and update/create `AttendanceCoverageRecord` tombstones (`coverage_state='purged_retained'`, `pre_purge_status`, `retention_run_id`, `expiry_reason`) inside the same atomic transaction.
- **Historical Data Preservation**:
  - Unattested historical dates remain `unknown` (`history_unavailable`). No historical backfill or synthetic completeness records may be generated.

---

## 4. Exact Schema Proposal (Requires Approval; No Migrations Generated)

### 4.1 `AttendanceCoverageRecord` (`apps/attendance/models.py`)
- `employee`: `ForeignKey('employees.EmployeeProfile', on_delete=models.CASCADE, db_index=True)`
- `date`: `DateField(db_index=True)`
- `coverage_state`: `CharField(max_length=24, choices=[('complete', 'Complete'), ('incomplete_activity', 'Incomplete Activity'), ('purged_retained', 'Purged Retained'), ('unknown', 'Unknown')])`
- `attestation_source`: `CharField(max_length=40)` (e.g., `'ingestion_reconciler'`, `'retention_tombstone'`, `'late_offline_sync'`)
- `offline_grace_closed_at`: `DateTimeField(null=True, blank=True)`
- `retention_run`: `ForeignKey('RetentionRunLog', null=True, blank=True, on_delete=models.PROTECT)`
- `expiry_reason`: `CharField(max_length=64, blank=True)` (e.g., `'age_3m_expired'`, `'age_5m_purged'`)
- `pre_purge_status`: `CharField(max_length=32, blank=True)`
- `attested_at`: `DateTimeField(auto_now_add=True)`
- `updated_at`: `DateTimeField(auto_now=True)`
- `Meta`: `UniqueConstraint(fields=['employee', 'date'], name='uniq_att_coverage_emp_date')`

### 4.2 `RetentionRunLog` (`apps/attendance/models.py`)
- `run_uuid`: `UUIDField(default=uuid.uuid4, unique=True, editable=False)`
- `started_at`: `DateTimeField(auto_now_add=True)`
- `completed_at`: `DateTimeField(null=True, blank=True)`
- `cutoff_expired_date`: `DateField()`
- `cutoff_purged_before`: `DateTimeField()`
- `expired_count`: `PositiveIntegerField(default=0)`
- `purged_attendance_count`: `PositiveIntegerField(default=0)`
- `purged_location_count`: `PositiveIntegerField(default=0)`

---

## 5. Producer / Consumer Changes

1. **Producers**:
   - **Daily Coverage Reconciler** (new cron command running after shift end + offline grace): Evaluates each `(employee, date)` for pending `SyncLog` / `AttendanceActivityLog` / `EmployeeLocationSync` anomalies and writes `AttendanceCoverageRecord(coverage_state='complete' | 'incomplete_activity')`.
   - **Late Offline Sync Hook** (`AttendanceTransactionService.check_in` / `field_visit` when `client_time` is supplied): Updates `AttendanceCoverageRecord` for `(emp_locked, today)` so late offline punches are never overwritten by stale absence state.
   - **Retention Job** (`apps/attendance/retention.py`): Writes `RetentionRunLog` + `AttendanceCoverageRecord` tombstones (`purged_retained`) before any deletion; hard-blocks `delete_old_expired_records()` if tombstone creation fails.
2. **Consumers**:
   - **`reporting_service.py`**: Bulk-fetches `AttendanceCoverageRecord` for `(employee_ids, date_range)` (1 query) and passes `coverage={'state': rec.coverage_state, 'source': rec.attestation_source}` into `evaluate_day_evidence_state`.
   - **`mark_daily_absences.py`**: Only marks absent when `coverage_state == 'complete'` and `reason_code == 'confirmed_absent'`.
   - **`PayrollService.sync_payroll_inputs`**: Treats `purged_retained` with valid `pre_purge_status` according to payroll policy, while keeping `unknown` / `incomplete_activity` blocked for HR review.

---

## 6. Rollout, Rollback & Approval Checklist

### Rollout
1. Approve and apply migrations for `AttendanceCoverageRecord` and `RetentionRunLog` (additive, nullable/defaulted, zero downtime).
2. Deploy retention tombstone protection first (while keeping `delete_old_expired_records` disabled or gated behind a feature flag).
3. Enable daily coverage reconciler for dates `>= activation_date` only (no historical backfill).
4. Enable `reporting_service.py` consumer lookup.

### Rollback
- Disable coverage reconciler cron and feature-flag `reporting_service.py` to fall back to `coverage=None` (`unknown`). Additive tables require no destructive rollback.

### Approval Checklist
- [ ] Approve `AttendanceCoverageRecord` and `RetentionRunLog` schema & migrations.
- [ ] Approve disabling/gating `delete_old_expired_records()` until tombstone writes are verified.
- [ ] Approve activation date (`>= activation_date`) and confirm no historical backfill.
- [ ] Approve late-offline-checkin vs `AttendanceAbsentLog` reversal policy.
