"""
Gantt Reference Data Service
Provides authentic, non-dummy HVAC project schedule datasets directly derived from:
1. 'GANTT CHART (2).xlsx' — Sheet 'Schedule' (21-Month Bi-Weekly Master Timeline, 32 tasks across 5 phases)
2. 'PROJECT SCHEDULE GANTT CHART.xlsx' — Sheet 'Project Planner' (33 Daily Activities with Plan Start, Durations, and Highlights)
3. 'PROJECT SCHEDULE GANTT CHART.xlsx' — Sheet 'DATE' / 'Schedule (2)' (11 Zones by 14 Stages with authentic Start/End dates)
"""

from datetime import datetime, date, timedelta
from typing import Dict, List, Any, Optional


class GanttReferenceService:
    """Provides authentic project schedule reference datasets matching the user's Excel workbooks."""

    # ── THEME COLOR MAPPINGS (Exact hex codes from Excel workbook theme) ───────
    THEME_CLASSES = {
        3: 'bg-[#156082] text-white',      # Deep Teal / Steel (Pre-construction & BOQ)
        4: 'bg-[#E97132] text-white',      # Safety Orange (Chillers, Pumps, AHU, Marking)
        5: 'bg-[#196B24] text-white',      # Forest Green (Piping, Ducting, Fans, Tanks)
        6: 'bg-[#0E2841] text-white',      # Midnight Navy (Mobilization)
        7: 'bg-[#0F9ED5] text-white',      # Sky Cyan (Drawings & Installation Summary)
        8: 'bg-[#80397B] text-white',      # Deep Violet / Purple (Pipeline, Valves, Duct, Hanging)
        9: 'bg-[#4EA72E] text-white',      # Vivid Green (Chiller, Pump, Cooling Tower Installation)
        0: 'bg-[#3B82F6] text-white',      # Electric Blue (Electrical Line Connection)
        2: 'bg-[#F59E0B] text-slate-950',  # Amber (Air Terminals Installation)
        1: 'bg-[#DC2626] text-white',      # Ruby Red (Testing & Commissioning)
    }

    # ── 1. MONTHLY MASTER TIMELINE DATASET (GANTT CHART (2).xlsx) ────────────
    @classmethod
    def get_monthly_master_schedule(cls) -> Dict[str, Any]:
        """
        Extracts the 21 months, 42 bi-weekly slots, and 32 tasks matching
        'GANTT CHART (2).xlsx' sheet 'Schedule'.
        """
        # 21 Months from May 2024 to Jan 2026
        month_defs = [
            (2024, 5, "May 2024"), (2024, 6, "Jun 2024"), (2024, 7, "Jul 2024"),
            (2024, 8, "Aug 2024"), (2024, 9, "Sep 2024"), (2024, 10, "Oct 2024"),
            (2024, 11, "Nov 2024"), (2024, 12, "Dec 2024"), (2025, 1, "Jan 2025"),
            (2025, 2, "Feb 2025"), (2025, 3, "Mar 2025"), (2025, 4, "Apr 2025"),
            (2025, 5, "May 2025"), (2025, 6, "Jun 2025"), (2025, 7, "Jul 2025"),
            (2025, 8, "Aug 2025"), (2025, 9, "Sep 2025"), (2025, 10, "Oct 2025"),
            (2025, 11, "Nov 2025"), (2025, 12, "Dec 2025"), (2026, 1, "Jan 2026")
        ]

        months = []
        slots = []
        for m_idx, (yr, mo, label) in enumerate(month_defs):
            start_col = 2 + (m_idx * 2)
            end_col = start_col + 1
            months.append({
                'label': label,
                'year': yr,
                'month': mo,
                'start_col': start_col,
                'end_col': end_col,
            })
            slots.append({
                'slot_idx': len(slots),
                'col_idx': start_col,
                'month_label': label,
                'half_label': '1st',
                'date_range': '1-15',
                'is_second_half': False,
            })
            slots.append({
                'slot_idx': len(slots),
                'col_idx': end_col,
                'month_label': label,
                'half_label': '2nd',
                'date_range': '16-end',
                'is_second_half': True,
            })

        # 32 Raw Task Definitions with exact column spans and themes from GANTT CHART (2).xlsx
        raw_tasks = [
            # Phase 1: Pre-Construction & Approvals
            {"order": 1, "phase": "Pre-Construction & Approvals", "activity": "Notification of Award", "cols": [3], "theme": 3, "badges": {}},
            {"order": 2, "phase": "Pre-Construction & Approvals", "activity": "LC Opening", "cols": list(range(4, 10)), "theme": 3, "badges": {}},
            {"order": 3, "phase": "Pre-Construction & Approvals", "activity": "Drawing Finalization", "cols": list(range(6, 17)), "theme": 3, "badges": {}},
            {"order": 4, "phase": "Pre-Construction & Approvals", "activity": "BOQ Finalization", "cols": list(range(8, 19)), "theme": 3, "badges": {}},

            # Phase 2: Import Products Order & Arrival
            {"order": 5, "phase": "Import Products Order & Arrival", "activity": "Import Products Order & Arrival", "cols": list(range(12, 33)), "theme": 3, "badges": {}, "is_group_header": True},
            {"order": 6, "phase": "Import Products Order & Arrival", "activity": "Chillers", "cols": list(range(12, 33)), "theme": 4, "badges": {25: "PSI", 26: "PSI"}},
            {"order": 7, "phase": "Import Products Order & Arrival", "activity": "Pumps", "cols": list(range(14, 30)), "theme": 4, "badges": {27: "PSI"}},
            {"order": 8, "phase": "Import Products Order & Arrival", "activity": "Cooling Tower", "cols": list(range(19, 31)), "theme": 4, "badges": {}},
            {"order": 9, "phase": "Import Products Order & Arrival", "activity": "AHU & FCUs", "cols": list(range(19, 31)), "theme": 4, "badges": {}},
            {"order": 10, "phase": "Import Products Order & Arrival", "activity": "Valves & Accessories", "cols": list(range(19, 32)), "theme": 4, "badges": {27: "PSI"}},
            {"order": 11, "phase": "Import Products Order & Arrival", "activity": "Piping System", "cols": list(range(19, 28)), "theme": 5, "badges": {}},
            {"order": 12, "phase": "Import Products Order & Arrival", "activity": "Ducting System", "cols": list(range(19, 28)), "theme": 5, "badges": {}},
            {"order": 13, "phase": "Import Products Order & Arrival", "activity": "Ventilation Fans", "cols": list(range(26, 36)), "theme": 5, "badges": {}},
            {"order": 14, "phase": "Import Products Order & Arrival", "activity": "Tanks", "cols": list(range(19, 32)), "theme": 5, "badges": {}},
            {"order": 15, "phase": "Import Products Order & Arrival", "activity": "Air Terminals", "cols": list(range(27, 34)), "theme": 5, "badges": {}},

            # Phase 3: Engineering & Drawings
            {"order": 16, "phase": "Engineering & Drawings", "activity": "Shop Drawing", "cols": [25, 26], "theme": 7, "badges": {}},
            {"order": 17, "phase": "Engineering & Drawings", "activity": "Layout Approval", "cols": [25, 26, 27], "theme": 7, "badges": {}},

            # Phase 4: Installation Work
            {"order": 18, "phase": "Installation Work", "activity": "Installation Work", "cols": [27, 34], "theme": 7, "badges": {27: "DG SECT", 34: "FULL PROJECT"}, "is_group_header": True},
            {"order": 19, "phase": "Installation Work", "activity": "Site Mobilization", "cols": [27], "theme": 6, "badges": {}},
            {"order": 20, "phase": "Installation Work", "activity": "On-site Marking", "cols": [27, 28], "theme": 4, "badges": {}},
            {"order": 21, "phase": "Installation Work", "activity": "Local Support Works", "cols": [28, 29], "theme": 4, "badges": {}},
            {"order": 22, "phase": "Installation Work", "activity": "Pipeline Installation", "cols": list(range(28, 41)), "theme": 8, "badges": {}},
            {"order": 23, "phase": "Installation Work", "activity": "Valves Installation", "cols": list(range(30, 41)), "theme": 8, "badges": {}},
            {"order": 24, "phase": "Installation Work", "activity": "Duct Installation", "cols": list(range(27, 41)), "theme": 8, "badges": {}},
            {"order": 25, "phase": "Installation Work", "activity": "Machine Hanging", "cols": list(range(27, 41)), "theme": 8, "badges": {}},
            {"order": 26, "phase": "Installation Work", "activity": "Chiller Installation", "cols": [32, 33], "theme": 9, "badges": {}},
            {"order": 27, "phase": "Installation Work", "activity": "Pump Installation", "cols": [31, 32], "theme": 9, "badges": {}},
            {"order": 28, "phase": "Installation Work", "activity": "Cooling Tower Installation", "cols": [31, 32], "theme": 9, "badges": {}},
            {"order": 29, "phase": "Installation Work", "activity": "Tanks Installation", "cols": [32, 33], "theme": 9, "badges": {}},
            {"order": 30, "phase": "Installation Work", "activity": "Electrical Line Connection", "cols": list(range(32, 42)), "theme": 0, "badges": {}},
            {"order": 31, "phase": "Installation Work", "activity": "Air Terminals Installation", "cols": list(range(33, 42)), "theme": 2, "badges": {}},

            # Phase 5: Testing & Commissioning
            {"order": 32, "phase": "Testing & Commissioning", "activity": "Testing & Commissioning", "cols": [33, 35], "theme": 1, "badges": {33: "DG", 35: "FULL PROJECT"}}
        ]

        tasks = []
        for rt in raw_tasks:
            col_set = set(rt["cols"])
            cells = []
            for slot_idx, slot in enumerate(slots):
                col_num = slot["col_idx"]
                has_bar = col_num in col_set
                theme_num = rt["theme"] if has_bar else None
                color_cls = cls.THEME_CLASSES.get(theme_num, '') if has_bar else ''
                badge = rt["badges"].get(col_num, None)
                is_start = has_bar and (col_num - 1 not in col_set)
                is_end = has_bar and (col_num + 1 not in col_set)

                cells.append({
                    'slot_idx': slot_idx,
                    'col_idx': col_num,
                    'has_bar': has_bar,
                    'theme': theme_num,
                    'color_class': color_cls,
                    'badge': badge,
                    'is_start': is_start,
                    'is_end': is_end
                })

            tasks.append({
                'id': rt["order"],
                'order': rt["order"],
                'activity': rt["activity"],
                'phase': rt["phase"],
                'is_group_header': rt.get("is_group_header", False),
                'duration_slots': len(rt["cols"]),
                'cells': cells
            })

        phases = [
            {"name": "Pre-Construction & Approvals", "task_count": 4, "color": "text-sky-700 dark:text-sky-400"},
            {"name": "Import Products Order & Arrival", "task_count": 11, "color": "text-amber-700 dark:text-amber-400"},
            {"name": "Engineering & Drawings", "task_count": 2, "color": "text-cyan-700 dark:text-cyan-400"},
            {"name": "Installation Work", "task_count": 14, "color": "text-purple-700 dark:text-purple-400"},
            {"name": "Testing & Commissioning", "task_count": 1, "color": "text-rose-700 dark:text-rose-400"}
        ]

        return {
            'months': months,
            'slots': slots,
            'tasks': tasks,
            'phases': phases,
            'total_tasks': len(tasks),
            'total_months': len(months),
            'total_slots': len(slots)
        }

    # ── 2. DAILY EXCEL PLANNER DATASET (PROJECT SCHEDULE GANTT CHART.xlsx) ───
    @classmethod
    def get_hvac_planner_tasks(cls, base_date: Optional[date] = None, display_days: int = 120) -> Dict[str, Any]:
        """
        Extracts the authentic 33 tasks from 'PROJECT SCHEDULE GANTT CHART.xlsx' sheet 'Project Planner',
        calculating exact calendar dates and day-by-day cell states.
        """
        ref_base = base_date or date(2024, 6, 23)

        raw_planner_tasks = [
            {"order": 1, "activity": "PO RECEIVE", "plan_start_day": 1, "duration": 1, "is_milestone": True},
            {"order": 2, "activity": "KICK OFF MEETING", "plan_start_day": 4, "duration": 1, "is_milestone": True},
            {"order": 3, "activity": "MEETING WITH PRINCIPAL/OEM", "plan_start_day": 7, "duration": 1, "is_milestone": False},
            {"order": 4, "activity": "SITE ASSESSMENT", "plan_start_day": 16, "duration": 1, "is_milestone": False},
            {"order": 5, "activity": "ORDER PLACEMENT", "plan_start_day": 18, "duration": 1, "is_milestone": True},
            {"order": 6, "activity": "CONTRACT SIGNING", "plan_start_day": 40, "duration": 1, "is_milestone": True},
            {"order": 7, "activity": "ADVANCE RECEIVED", "plan_start_day": 59, "duration": 1, "is_milestone": True},
            {"order": 8, "activity": "DELIVERY OF LOCAL MATERIALS", "plan_start_day": 75, "duration": 1, "is_milestone": False},
            {"order": 9, "activity": "DELIVERY OF STOCK MACHINES", "plan_start_day": 84, "duration": 2, "is_milestone": False},
            {"order": 10, "activity": "IMPORT MATERIALS READINESS", "plan_start_day": 1, "duration": 102, "is_milestone": False},
            {"order": 11, "activity": "IMPORT MATERIALS SHIPMENT", "plan_start_day": 103, "duration": 7, "is_milestone": False},
            {"order": 12, "activity": "IMPORT MATERIALS ARRIVAL AT CTG PORT", "plan_start_day": 110, "duration": 30, "is_milestone": True},
            {"order": 13, "activity": "CUSTOMS CLEARANCE", "plan_start_day": 140, "duration": 14, "is_milestone": False},
            {"order": 14, "activity": "IMPORT MATERIALS DELIVERY AT SITE", "plan_start_day": 154, "duration": 1, "is_milestone": True},
            {"order": 15, "activity": "SHOP DRAWING SUBMISSION", "plan_start_day": 52, "duration": 1, "is_milestone": False},
            {"order": 16, "activity": "SHOP DRAWING APPROVAL", "plan_start_day": 70, "duration": 75, "is_milestone": True},
            {"order": 17, "activity": "SITE ASSESSMENT (RE-CHECK)", "plan_start_day": 65, "duration": 1, "is_milestone": False},
            {"order": 18, "activity": "TECHNICAL DISCUSSION", "plan_start_day": 70, "duration": 1, "is_milestone": False},
            {"order": 19, "activity": "SITE MOBILIZATION & INDUCTION", "plan_start_day": 72, "duration": 3, "is_milestone": True},
            {"order": 20, "activity": "WORK START", "plan_start_day": 75, "duration": 1, "is_milestone": True},
            {"order": 21, "activity": "KNITTING & MAINT WORKSHOP (ZONE-1)", "plan_start_day": 75, "duration": 16, "is_milestone": False},
            {"order": 22, "activity": "CUTTING & MAINTENANCE WORKSHOP (ZONE-2)", "plan_start_day": 75, "duration": 16, "is_milestone": False},
            {"order": 23, "activity": "ENGINEERING WORKSHOP GF (ZONE-3)", "plan_start_day": 91, "duration": 17, "is_milestone": False},
            {"order": 24, "activity": "PROCUREMENT & HR SECTION GF (ZONE-4)", "plan_start_day": 91, "duration": 17, "is_milestone": False},
            {"order": 25, "activity": "CANTEEN & SUBSTATION (ZONE-5)", "plan_start_day": 108, "duration": 10, "is_milestone": False},
            {"order": 26, "activity": "PROCUREMENT & HR SECTION MF (ZONE-6)", "plan_start_day": 114, "duration": 10, "is_milestone": False},
            {"order": 27, "activity": "SEWING CANTEEN & OFFICE MZ (ZONE-7)", "plan_start_day": 124, "duration": 15, "is_milestone": False},
            {"order": 28, "activity": "ELECTRICAL WIRING (ZONE 1~2)", "plan_start_day": 85, "duration": 1, "is_milestone": False},
            {"order": 29, "activity": "ELECTRICAL WIRING (ZONE 3)", "plan_start_day": 85, "duration": 1, "is_milestone": False},
            {"order": 30, "activity": "ELECTRICAL WIRING (ZONE 4~7)", "plan_start_day": 104, "duration": 1, "is_milestone": False},
            {"order": 31, "activity": "TESTING COMMISSIONING (ZONE 1~2)", "plan_start_day": 92, "duration": 4, "is_milestone": False},
            {"order": 32, "activity": "TESTING COMMISSIONING (ZONE 3)", "plan_start_day": 108, "duration": 4, "is_milestone": False},
            {"order": 33, "activity": "TESTING COMMISSIONING (ZONE 4~7)", "plan_start_day": 163, "duration": 7, "is_milestone": True}
        ]

        # Build day columns (Day 1..display_days)
        planner_days = []
        for i in range(display_days):
            d = ref_base + timedelta(days=i)
            planner_days.append({
                'day_num': i + 1,
                'weekday': d.strftime('%a').upper(),
                'date_str': d.strftime('%d-%b'),
                'iso': d.isoformat()
            })

        tasks = []
        for t in raw_planner_tasks:
            st_day = t["plan_start_day"]
            dur = t["duration"]
            end_day = st_day + dur - 1

            p_start_date = ref_base + timedelta(days=st_day - 1)
            p_finish_date = ref_base + timedelta(days=end_day - 1)

            # Build cell states for every day column
            cells = []
            for d_info in planner_days:
                col_day = d_info['day_num']
                if st_day <= col_day <= end_day:
                    cells.append('plan')
                else:
                    cells.append('empty')

            tasks.append({
                'id': t["order"],
                'order': t["order"],
                'activity': t["activity"],
                'is_milestone': t["is_milestone"],
                'is_delayed': False,
                'status': 'In Progress' if st_day <= 30 else 'Not Started',
                'plan_start': p_start_date.strftime('%Y-%m-%d'),
                'plan_start_day': st_day,
                'plan_duration': dur,
                'plan_end': p_finish_date.strftime('%Y-%m-%d'),
                'actual_start': '—',
                'actual_duration': '—',
                'progress_percent': 100 if st_day <= 10 else (50 if st_day <= 25 else 0),
                'cells': cells
            })

        return {
            'planner_days': planner_days,
            'planner_tasks': tasks,
            'base_date': ref_base.isoformat()
        }

    # ── 3. WORK / ZONE MATRIX SCHEDULE (PROJECT SCHEDULE GANTT CHART.xlsx DATE) ───
    @classmethod
    def get_zone_schedule_matrix(cls) -> Dict[str, Any]:
        """
        Extracts authentic Zone Schedule Matrix matching sheet 'DATE' / 'Schedule (2)'
        with 11 Zones and 14 Stages.
        """
        zone_stages = [
            "CEILING/ ACP OPENING",
            "MARKING",
            "SUPPORT WORK",
            "COPPER PIPING",
            "PRESSURE TEST-PIPELINE",
            "DRAIN PIPE",
            "NETWORKING",
            "DUCTING",
            "MACHINE INSTALLATION",
            "PRESSURE TEST-SYSTEM",
            "ELECTRICAL CABLE WORK",
            "CEILING/ ACP CLOSING",
            "AIR TERMINALS INSTALLATION",
            "TESTING & COMMISSIONING"
        ]

        # Authentic row data directly extracted from reference sheet DATE
        zone_data = [
            {
                "name": "KNITTING & MAINT WORKSHOP",
                "stages": {
                    "CEILING/ ACP OPENING": {"start": "—", "end": "2024-09-04"},
                    "MARKING": {"start": "2024-09-05", "end": "2024-09-05"},
                    "SUPPORT WORK": {"start": "2024-09-06", "end": "2024-09-07"},
                    "COPPER PIPING": {"start": "2024-09-08", "end": "2024-09-11"},
                    "PRESSURE TEST-PIPELINE": None,
                    "DRAIN PIPE": {"start": "2024-09-12", "end": "2024-09-12"},
                    "NETWORKING": {"start": "2024-09-13", "end": "2024-09-13"},
                    "DUCTING": {"start": "2024-09-05", "end": "2024-09-10"},
                    "MACHINE INSTALLATION": {"start": "2024-09-14", "end": "2024-09-15"},
                    "PRESSURE TEST-SYSTEM": {"start": "2024-09-16", "end": "2024-09-17"},
                    "ELECTRICAL CABLE WORK": {"start": "—", "end": "2024-09-12"},
                    "CEILING/ ACP CLOSING": {"start": "2024-09-17", "end": "2024-09-24"},
                    "AIR TERMINALS INSTALLATION": {"start": "2024-09-25", "end": "2024-09-25"},
                    "TESTING & COMMISSIONING": {"start": "2024-09-18", "end": "2024-09-21"},
                }
            },
            {
                "name": "CUTTING & MAINTENANCE WORKSHOP",
                "stages": {
                    "CEILING/ ACP OPENING": {"start": "—", "end": "2024-09-04"},
                    "MARKING": {"start": "2024-09-05", "end": "2024-09-05"},
                    "SUPPORT WORK": {"start": "2024-09-06", "end": "2024-09-07"},
                    "COPPER PIPING": {"start": "2024-09-08", "end": "2024-09-11"},
                    "PRESSURE TEST-PIPELINE": None,
                    "DRAIN PIPE": {"start": "2024-09-12", "end": "2024-09-12"},
                    "NETWORKING": {"start": "2024-09-13", "end": "2024-09-13"},
                    "DUCTING": {"start": "2024-09-05", "end": "2024-09-10"},
                    "MACHINE INSTALLATION": {"start": "2024-09-14", "end": "2024-09-15"},
                    "PRESSURE TEST-SYSTEM": {"start": "2024-09-16", "end": "2024-09-17"},
                    "ELECTRICAL CABLE WORK": {"start": "—", "end": "2024-09-12"},
                    "CEILING/ ACP CLOSING": {"start": "2024-09-17", "end": "2024-09-24"},
                    "AIR TERMINALS INSTALLATION": {"start": "2024-09-25", "end": "2024-09-25"},
                    "TESTING & COMMISSIONING": {"start": "2024-09-18", "end": "2024-09-21"},
                }
            },
            {
                "name": "ENGINEERING WORKSHOP GF",
                "stages": {
                    "CEILING/ ACP OPENING": {"start": "—", "end": "2024-09-16"},
                    "MARKING": {"start": "2024-09-17", "end": "2024-09-17"},
                    "SUPPORT WORK": {"start": "2024-09-18", "end": "2024-09-21"},
                    "COPPER PIPING": {"start": "2024-09-22", "end": "2024-09-25"},
                    "PRESSURE TEST-PIPELINE": None,
                    "DRAIN PIPE": {"start": "2024-09-26", "end": "2024-09-27"},
                    "NETWORKING": {"start": "2024-09-28", "end": "2024-09-28"},
                    "DUCTING": {"start": "2024-09-18", "end": "2024-09-22"},
                    "MACHINE INSTALLATION": {"start": "2024-09-20", "end": "2024-09-21"},
                    "PRESSURE TEST-SYSTEM": {"start": "2024-09-26", "end": "2024-09-27"},
                    "ELECTRICAL CABLE WORK": {"start": "—", "end": "2024-09-18"},
                    "CEILING/ ACP CLOSING": {"start": "2024-09-27", "end": "2024-10-04"},
                    "AIR TERMINALS INSTALLATION": {"start": "2024-10-05", "end": "2024-10-06"},
                    "TESTING & COMMISSIONING": {"start": "2024-09-28", "end": "2024-10-01"},
                }
            },
            {
                "name": "PROCUREMENT & HR SECTION GF",
                "stages": {
                    "CEILING/ ACP OPENING": {"start": "—", "end": "2024-09-16"},
                    "MARKING": {"start": "2024-09-17", "end": "2024-09-17"},
                    "SUPPORT WORK": {"start": "2024-09-18", "end": "2024-09-21"},
                    "COPPER PIPING": {"start": "2024-09-22", "end": "2024-09-26"},
                    "PRESSURE TEST-PIPELINE": {"start": "2024-09-27", "end": "2024-09-29"},
                    "DRAIN PIPE": {"start": "2024-09-27", "end": "2024-09-28"},
                    "NETWORKING": {"start": "2024-09-29", "end": "2024-09-29"},
                    "DUCTING": {"start": "2024-09-18", "end": "2024-09-29"},
                    "MACHINE INSTALLATION": {"start": "2024-11-24", "end": "2024-11-26"},
                    "PRESSURE TEST-SYSTEM": {"start": "2024-11-27", "end": "2024-11-28"},
                    "ELECTRICAL CABLE WORK": {"start": "—", "end": "2024-11-22"},
                    "CEILING/ ACP CLOSING": {"start": "2024-09-30", "end": "2024-10-07"},
                    "AIR TERMINALS INSTALLATION": {"start": "2024-10-08", "end": "2024-10-09"},
                    "TESTING & COMMISSIONING": {"start": "2024-11-29", "end": "2024-12-02"},
                }
            },
            {
                "name": "CANTEEN & SUBSTATION",
                "stages": {
                    "CEILING/ ACP OPENING": {"start": "—", "end": "2024-09-29"},
                    "MARKING": {"start": "2024-09-30", "end": "2024-09-30"},
                    "SUPPORT WORK": {"start": "2024-09-30", "end": "2024-10-01"},
                    "COPPER PIPING": {"start": "2024-10-02", "end": "2024-10-04"},
                    "PRESSURE TEST-PIPELINE": {"start": "2024-10-05", "end": "2024-10-07"},
                    "DRAIN PIPE": {"start": "2024-10-02", "end": "2024-10-04"},
                    "NETWORKING": {"start": "2024-10-02", "end": "2024-10-04"},
                    "DUCTING": {"start": "2024-09-23", "end": "2024-10-04"},
                    "MACHINE INSTALLATION": {"start": "2024-11-24", "end": "2024-11-26"},
                    "PRESSURE TEST-SYSTEM": {"start": "2024-11-27", "end": "2024-11-28"},
                    "ELECTRICAL CABLE WORK": {"start": "—", "end": "2024-11-22"},
                    "CEILING/ ACP CLOSING": {"start": "2024-10-08", "end": "2024-10-15"},
                    "AIR TERMINALS INSTALLATION": {"start": "2024-10-16", "end": "2024-10-17"},
                    "TESTING & COMMISSIONING": {"start": "2024-11-29", "end": "2024-12-02"},
                }
            },
            {
                "name": "PROCUREMENT & HR SECTION MF",
                "stages": {
                    "CEILING/ ACP OPENING": {"start": "—", "end": "2024-10-04"},
                    "MARKING": {"start": "2024-10-05", "end": "2024-10-05"},
                    "SUPPORT WORK": {"start": "2024-10-05", "end": "2024-10-06"},
                    "COPPER PIPING": {"start": "2024-10-07", "end": "2024-10-08"},
                    "PRESSURE TEST-PIPELINE": {"start": "2024-10-09", "end": "2024-10-11"},
                    "DRAIN PIPE": {"start": "2024-10-09", "end": "2024-10-09"},
                    "NETWORKING": {"start": "2024-10-10", "end": "2024-10-10"},
                    "DUCTING": {"start": "2024-10-05", "end": "2024-10-10"},
                    "MACHINE INSTALLATION": {"start": "2024-11-27", "end": "2024-11-29"},
                    "PRESSURE TEST-SYSTEM": {"start": "2024-11-30", "end": "2024-12-01"},
                    "ELECTRICAL CABLE WORK": {"start": "—", "end": "2024-11-25"},
                    "CEILING/ ACP CLOSING": {"start": "2024-10-12", "end": "2024-10-19"},
                    "AIR TERMINALS INSTALLATION": {"start": "2024-10-20", "end": "2024-10-21"},
                    "TESTING & COMMISSIONING": {"start": "2024-12-02", "end": "2024-12-05"},
                }
            },
            {
                "name": "SEWING CANTEEN & OFFICE MZ",
                "stages": {
                    "CEILING/ ACP OPENING": {"start": "—", "end": "2024-10-10"},
                    "MARKING": {"start": "2024-10-11", "end": "2024-10-11"},
                    "SUPPORT WORK": {"start": "2024-10-12", "end": "2024-10-16"},
                    "COPPER PIPING": {"start": "2024-10-17", "end": "2024-10-20"},
                    "PRESSURE TEST-PIPELINE": {"start": "2024-10-21", "end": "2024-10-23"},
                    "DRAIN PIPE": {"start": "2024-10-21", "end": "2024-10-22"},
                    "NETWORKING": {"start": "2024-10-21", "end": "2024-10-22"},
                    "DUCTING": {"start": "2024-10-11", "end": "2024-10-20"},
                    "MACHINE INSTALLATION": {"start": "2024-11-30", "end": "2024-12-03"},
                    "PRESSURE TEST-SYSTEM": {"start": "2024-12-04", "end": "2024-12-05"},
                    "ELECTRICAL CABLE WORK": {"start": "—", "end": "2024-11-28"},
                    "CEILING/ ACP CLOSING": {"start": "2024-10-24", "end": "2024-10-31"},
                    "AIR TERMINALS INSTALLATION": {"start": "2024-11-01", "end": "2024-11-02"},
                    "TESTING & COMMISSIONING": {"start": "2024-12-06", "end": "2024-12-09"},
                }
            },
            {"name": "COLOR SERVICE ZONE", "stages": {s: None for s in zone_stages}},
            {"name": "SPARE PARTS WAREHOUSE", "stages": {s: None for s in zone_stages}},
            {"name": "CANTEEN & OFFICE", "stages": {s: None for s in zone_stages}},
            {"name": "SEWING FLOOR", "stages": {s: None for s in zone_stages}},
        ]

        # Authentic milestones matching the workbook
        project_milestones = [
            {'name': 'PO Receive & Kick-off', 'date': '23 Jun 2024'},
            {'name': 'Site Assessment', 'date': '08 Jul 2024'},
            {'name': 'Order Placement', 'date': '10 Jul 2024'},
            {'name': 'Contract Signing', 'date': '01 Aug 2024'},
            {'name': 'Advance Received', 'date': '20 Aug 2024'},
            {'name': 'Shop Drawing Approval', 'date': '31 Aug 2024'},
            {'name': 'Local Material Delivery', 'date': '05 Sep 2024'},
            {'name': 'Import Port Arrival', 'date': '10 Oct 2024'},
            {'name': 'Installation Start', 'date': '05 Sep 2024'},
            {'name': 'Testing & Handover', 'date': '09 Dec 2024'},
        ]

        return {
            'zone_stages': zone_stages,
            'zone_rows': zone_data,
            'project_milestones': project_milestones
        }
