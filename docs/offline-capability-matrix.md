# FieldTrack Offline Capability Matrix

This matrix defines the offline operation rules and sync behavior across all FieldTrack system modules.

## Module Capability Matrix

| Module | Offline Create | Offline Edit | Offline Delete | Auto Sync | Priority Order |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Attendance** | Yes | No | No | Yes | 1 (Check-in), 2 (Check-out) |
| **GPS Tracking** | Yes | No | No | Yes | 3 |
| **Leave Management** | Yes | Pending-only | No | Yes | 4 |
| **Expense Management** | Yes | Pending-only | No | Yes | 5 |
| **Daily Report** | Yes | Yes | No | Yes | 7 |
| **Project Update** | Yes | Yes | No | Yes | 6 |
| **Photo Upload** | Yes | No | No | Yes | 8 |

> [!NOTE]
> **Key Operational Principles**:
> 1. **No Offline Deletions**: Deletions are forbidden while offline across all modules to prevent transactional data integrity conflicts.
> 2. **Attendance & GPS**: Strictly create-only offline (Check-in/Check-out timestamps and geolocation snapshots are immutable once captured).
> 3. **Leave & Expense**: Offline editing is limited strictly to items currently in `Pending` status. Approved/rejected records cannot be edited offline.
> 4. **Daily Report & Project Update**: Full offline create and draft edit capability supported.

---

## Sync Engine Queue Priorities

When network connection is restored, pending sync queue records are processed in strict priority order:

1. `check-in` (Priority 1)
2. `check-out` (Priority 2)
3. `gps` (Priority 3)
4. `leave` (Priority 4)
5. `expense` (Priority 5)
6. `project-update` (Priority 6)
7. `daily-report` (Priority 7)
8. `photo-upload` (Priority 8)

## Queue Record Shape (`sync_queue`)

```json
{
  "uuid": "550e8400-e29b-41d4-a716-446655440000",
  "module": "attendance",
  "action": "check-in",
  "payload": {
    "lat": 23.8103,
    "lng": 90.4125,
    "timestamp": "2026-07-24T11:00:00Z"
  },
  "priority": 1,
  "status": "pending",
  "retry_count": 0,
  "created_time": "2026-07-24T11:00:00Z",
  "synced_time": null
}
```
