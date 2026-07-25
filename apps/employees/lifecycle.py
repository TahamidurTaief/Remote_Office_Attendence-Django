"""
Employee lifecycle state machine — transition map and tier classification.
Import this module in models.py and views.py; do NOT import models here
to avoid circular imports.
"""

# ── Allowed transitions ──────────────────────────────────────────────────────
# Keys are FROM-status values; sets are valid TO-status values.
TRANSITION_MAP: dict[str, set[str]] = {
    'draft':            {'pending_approval', 'active'},
    'pending_approval': {'active', 'draft'},
    'active':           {'probation', 'suspended', 'resigned', 'terminated', 'archived'},
    'probation':        {'confirmed', 'suspended', 'resigned', 'terminated', 'archived'},
    'confirmed':        {'suspended', 'resigned', 'terminated', 'archived'},
    'suspended':        {'active', 'probation', 'confirmed', 'archived'},
    'resigned':         {'archived'},
    'terminated':       {'archived'},
    'archived':         set(),
}

# ── Two-tier classification ───────────────────────────────────────────────────
# LOW_RISK transitions apply immediately (no approval required).
# Everything else listed in TRANSITION_MAP is HIGH_RISK and goes through
# LifecycleTransitionRequest (pending admin approval before status changes).
LOW_RISK_TRANSITIONS: set[tuple[str, str]] = {
    ('draft',         'pending_approval'),
    ('draft',         'active'),
    ('pending_approval', 'draft'),
    ('probation',     'confirmed'),
    ('resigned',      'archived'),
    ('terminated',    'archived'),
}


def get_allowed_targets(from_status: str) -> set[str]:
    """Return the set of valid to-status values for a given from-status."""
    return TRANSITION_MAP.get(from_status, set())


def is_valid_transition(from_status: str, to_status: str) -> bool:
    return to_status in get_allowed_targets(from_status)


def is_low_risk(from_status: str, to_status: str) -> bool:
    return (from_status, to_status) in LOW_RISK_TRANSITIONS


def is_high_risk(from_status: str, to_status: str) -> bool:
    return is_valid_transition(from_status, to_status) and not is_low_risk(from_status, to_status)


def describe_allowed(from_status: str) -> str:
    """Human-readable list of allowed targets for error messages."""
    targets = get_allowed_targets(from_status)
    if not targets:
        return 'none (terminal state)'
    return ', '.join(sorted(targets))
