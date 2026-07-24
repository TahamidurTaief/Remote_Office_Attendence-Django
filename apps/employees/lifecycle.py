"""
Employee lifecycle state machine — transition map and tier classification.
Import this module in models.py and views.py; do NOT import models here
to avoid circular imports.
"""

# ── Allowed transitions ──────────────────────────────────────────────────────
# Keys are FROM-status values; sets are valid TO-status values.
TRANSITION_MAP: dict[str, set[str]] = {
    'draft':            {'pending_approval'},
    'pending_approval': {'active', 'draft'},
    'active':           {'probation', 'transferred', 'promoted', 'demoted', 'notice_period', 'terminated'},
    'probation':        {'confirmed', 'terminated'},
    'confirmed':        {'transferred', 'promoted', 'demoted', 'notice_period', 'terminated', 'retired'},
    'transferred':      {'active', 'notice_period', 'terminated'},
    'promoted':         {'active', 'notice_period', 'terminated'},
    'demoted':          {'active', 'notice_period', 'terminated'},
    'notice_period':    {'resigned', 'terminated'},
    'resigned':         {'archived'},
    'terminated':       {'archived'},
    'retired':          {'archived'},
    'archived':         set(),
}

# ── Two-tier classification ───────────────────────────────────────────────────
# LOW_RISK transitions apply immediately (no approval required).
# Everything else listed in TRANSITION_MAP is HIGH_RISK and goes through
# LifecycleTransitionRequest (pending admin approval before status changes).
LOW_RISK_TRANSITIONS: set[tuple[str, str]] = {
    ('draft',         'pending_approval'),
    ('probation',     'confirmed'),
    ('notice_period', 'resigned'),
    ('resigned',      'archived'),
    ('terminated',    'archived'),
    ('retired',       'archived'),
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
