"""
apps/attendance/pdf_export.py

Authoritative PDF export service for Attendance Reports:
- Daily Attendance Register (A4 Landscape)
- Monthly Attendance Summary & Split Daily Matrix (A4 Landscape)
- Individual Employee Monthly Attendance Statement (A4 Landscape)

Design Principles:
- A4 landscape orientation with strictly bounded column widths derived from doc.width
- Light gray table headers (#F1F5F9), thin borders (#CBD5E1 / #E2E8F0), restrained dark text (#0F172A)
- Complete removal of legacy purple headers
- Escaped Paragraph cells for wrapping, dynamic row heights, and zero clipping
- Numbered Appendix for long geofence policy notes and URLs to prevent row blowouts
- Repeat table headers on all page breaks (repeatRows=1)
- Two-pass NumberedCanvas for 'Page X of Y' footers
- Exact calculation & formatting parity with XLSX (timestamps with seconds, multi-session aggregates, explicit open status)
- Verified bilingual font registration (Bengali & Latin support)
"""

import os
import io
import html
import math
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from collections import defaultdict

from django.conf import settings
from django.utils import timezone
from django.http import HttpResponse

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate,
    Table,
    TableStyle,
    Paragraph,
    Spacer,
    PageBreak,
    HRFlowable,
)
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

BD_TZ = ZoneInfo('Asia/Dhaka')

# Pre-computed palette constants (avoids repeated HexColor parsing in 500-row loops)
_C_WHITE = colors.HexColor('#FFFFFF')
_C_SLATE_50 = colors.HexColor('#F8FAFC')
_C_SLATE_100 = colors.HexColor('#F1F5F9')
_C_SLATE_200 = colors.HexColor('#E2E8F0')
_C_SLATE_300 = colors.HexColor('#CBD5E1')
_C_SLATE_400 = colors.HexColor('#94A3B8')
_C_SLATE_500 = colors.HexColor('#64748B')
_C_SLATE_700 = colors.HexColor('#334155')
_C_SLATE_800 = colors.HexColor('#1E293B')
_C_SLATE_900 = colors.HexColor('#0F172A')
_C_GREEN = colors.HexColor('#16A34A')
_C_AMBER = colors.HexColor('#B45309')
_C_RED = colors.HexColor('#DC2626')
_C_BLUE = colors.HexColor('#2563EB')
_C_TEAL = colors.HexColor('#0D9488')

# ── 1. FONT REGISTRATION ─────────────────────────────────────────────────────

FONT_REGULAR = 'Helvetica'
FONT_BOLD = 'Helvetica-Bold'
FONT_REGISTERED = False

def init_pdf_fonts(require_bengali=False):
    """
    Registers Unicode TrueType fonts supporting Bengali & Latin characters.
    Prioritizes repository-bundled fonts in static/fonts/ so the application
    does not depend on host /usr/share/fonts or silently fall back to Helvetica for Bengali.
    """
    global FONT_REGULAR, FONT_BOLD, FONT_REGISTERED
    if FONT_REGISTERED and FONT_REGULAR != 'Helvetica':
        return FONT_REGULAR, FONT_BOLD

    from django.conf import settings
    repo_font_dir = os.path.join(settings.BASE_DIR, 'static', 'fonts')

    candidate_fonts = [
        (os.path.join(repo_font_dir, 'JamrulNormal.ttf'), 'JamrulBundled'),
        (os.path.join(repo_font_dir, 'LikhanNormal.ttf'), 'LikhanBundled'),
        ('/usr/share/fonts/truetype/fonts-beng-extra/JamrulNormal.ttf', 'JamrulSys'),
        ('/usr/share/fonts/truetype/fonts-beng-extra/LikhanNormal.ttf', 'LikhanSys'),
    ]

    for path, name in candidate_fonts:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont(name, path, shapable=True))
                pdfmetrics.registerFontFamily(name, normal=name, bold=name)
                FONT_REGULAR = name
                FONT_BOLD = name
                FONT_REGISTERED = True
                return FONT_REGULAR, FONT_BOLD
            except Exception:
                continue

    if require_bengali:
        raise RuntimeError(
            "No licensed Bengali TrueType font found in static/fonts/ or system paths. "
            "Cannot fall back to Helvetica as it does not render Bengali glyphs."
        )

    FONT_REGULAR = 'Helvetica'
    FONT_BOLD = 'Helvetica-Bold'
    FONT_REGISTERED = True
    return FONT_REGULAR, FONT_BOLD


# ── 2. TWO-PASS NUMBERED CANVAS ──────────────────────────────────────────────

class NumberedCanvas(canvas.Canvas):
    """
    Two-pass canvas that accumulates page states to draw accurate
    'Page X of Y' footers, company watermarks, and running headers.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count):
        self.saveState()
        init_pdf_fonts()
        self.setFont(FONT_REGULAR, 7.5)
        self.setFillColor(colors.HexColor("#64748B"))

        page_width, page_height = self._pagesize

        # Running bottom separator line
        self.setStrokeColor(colors.HexColor("#E2E8F0"))
        self.setLineWidth(0.5)
        self.line(28, 24, page_width - 28, 24)

        # Footer text
        footer_left = "FieldTrack Attendance Management System — Timezone: Asia/Dhaka (BST, UTC+6)"
        footer_right = f"Page {self._pageNumber} of {page_count}"
        self.drawString(28, 13, footer_left)
        self.drawRightString(page_width - 28, 13, footer_right)
        self.restoreState()


# ── 3. TIME & PARITY HELPERS ─────────────────────────────────────────────────

def format_time_with_seconds(dt):
    """
    Canonical time formatter for PDF and XLSX parity.
    Always includes seconds in 24-hour Asia/Dhaka time: HH:MM:SS.
    """
    if not dt:
        return '—'
    if timezone.is_aware(dt):
        local_dt = timezone.localtime(dt, BD_TZ)
    else:
        local_dt = timezone.make_aware(dt, timezone.utc).astimezone(BD_TZ) if timezone.is_naive(dt) else dt
    return local_dt.strftime('%H:%M:%S')


def get_employee_department_name(employee):
    """Safely extracts department string from various model relations."""
    if not employee:
        return ''
    if hasattr(employee, 'canonical_department'):
        dept = employee.canonical_department() if callable(employee.canonical_department) else employee.canonical_department
        if dept:
            return str(dept).strip()
    if hasattr(employee, 'master_employee') and employee.master_employee and employee.master_employee.department:
        return str(employee.master_employee.department.name).strip()
    if hasattr(employee, 'department') and employee.department:
        return str(employee.department).strip()
    return ''


def _esc(val):
    """Escapes user-controlled text before ReportLab Paragraph XML/markup parsing."""
    if val is None:
        return ''
    return html.escape(str(val), quote=True)


def _style_for_text(text, shaped_style, ascii_style):
    """Uses HarfBuzz-shaped style for any non-ASCII (e.g. Bengali/mixed) text, and fast unshaped style for pure ASCII."""
    if text and not text.isascii():
        return shaped_style
    return ascii_style


def _create_pdf_doc(buffer):
    """Creates a standardized A4 Landscape SimpleDocTemplate with footer clearance."""
    return SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=28,
        rightMargin=28,
        topMargin=26,
        bottomMargin=36,
    )


def _build_appendix_flowables(appendix_notes, avail_w, styles, include_employee=True):
    """
    Builds a splittable Notes & Remarks Appendix without KeepTogether so
    multi-page appendices and single notes taller than one page split cleanly
    with repeated headers and zero LayoutError.
    """
    if not appendix_notes:
        return []

    flowables = [
        Spacer(1, 14),
        Paragraph("<b>Notes &amp; Remarks Appendix</b>", styles['meta_label']),
        Spacer(1, 4),
    ]

    if include_employee:
        app_col_w = [48, 170, round(avail_w - 218, 2)]
        emp_col_header = "<b>Employee &amp; Date</b>"
    else:
        app_col_w = [48, 120, round(avail_w - 168, 2)]
        emp_col_header = "<b>Date</b>"

    app_data = [[
        Paragraph("<b>Ref</b>", styles['th']),
        Paragraph(emp_col_header, styles['th_left']),
        Paragraph("<b>Complete Note / Policy Remarks</b>", styles['th_left']),
    ]]
    for note in appendix_notes:
        if include_employee:
            meta_html = (
                f"<b>{_esc(note['emp'])}</b><br/>"
                f"<font color='#64748B'>{_esc(note['date'])}</font>"
            )
        else:
            meta_html = _esc(note['date'])
        note_html = _esc(note['text']).replace('\r\n', '\n').replace('\r', '\n').replace('\n', '<br/>')
        app_data.append([
            Paragraph(f"<b>[Note {note['num']}]</b>", styles['td_center']),
            Paragraph(meta_html, styles['td_left']),
            Paragraph(note_html, styles['appendix']),
        ])

    app_table = Table(app_data, colWidths=app_col_w, repeatRows=1, splitInRow=1)
    app_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F1F5F9')),
        ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#CBD5E1')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
    ]))
    flowables.append(app_table)
    return flowables


# ── 4. STYLES FACTORY ────────────────────────────────────────────────────────

def get_report_styles():
    reg_font, bold_font = init_pdf_fonts()
    styles = getSampleStyleSheet()
    shaping = 1 if (reg_font != 'Helvetica') else 0

    title_style = ParagraphStyle(
        'RepTitle',
        parent=styles['Normal'],
        fontName=bold_font,
        fontSize=13,
        leading=16,
        textColor=colors.HexColor('#0F172A'),
        shaping=shaping,
        splitLongWords=1,
    )
    subtitle_style = ParagraphStyle(
        'RepSubtitle',
        parent=styles['Normal'],
        fontName=reg_font,
        fontSize=8,
        leading=11,
        textColor=colors.HexColor('#475569'),
        shaping=shaping,
        splitLongWords=1,
    )
    meta_label_style = ParagraphStyle(
        'RepMetaLabel',
        parent=styles['Normal'],
        fontName=bold_font,
        fontSize=7.5,
        leading=10,
        textColor=colors.HexColor('#1E293B'),
        shaping=shaping,
        splitLongWords=1,
    )
    meta_val_style = ParagraphStyle(
        'RepMetaVal',
        parent=styles['Normal'],
        fontName=reg_font,
        fontSize=7.5,
        leading=10,
        textColor=colors.HexColor('#334155'),
        shaping=shaping,
        splitLongWords=1,
    )
    th_style = ParagraphStyle(
        'RepTH',
        parent=styles['Normal'],
        fontName=bold_font,
        fontSize=7.5,
        leading=9.5,
        alignment=1,
        textColor=colors.HexColor('#1E293B'),
        shaping=shaping,
        splitLongWords=1,
    )
    th_left_style = ParagraphStyle(
        'RepTHLeft',
        parent=th_style,
        alignment=0,
        shaping=shaping,
        splitLongWords=1,
    )
    td_left = ParagraphStyle(
        'RepTDLeft',
        parent=styles['Normal'],
        fontName=reg_font,
        fontSize=7,
        leading=9,
        alignment=0,
        textColor=_C_SLATE_900,
        shaping=shaping,
        splitLongWords=1,
    )
    td_left_ascii = ParagraphStyle(
        'RepTDLeftAscii',
        parent=td_left,
        shaping=0,
    )
    td_center = ParagraphStyle(
        'RepTDCenter',
        parent=styles['Normal'],
        fontName=reg_font,
        fontSize=7,
        leading=9,
        alignment=1,
        textColor=colors.HexColor('#0F172A'),
        shaping=shaping,
        splitLongWords=1,
    )
    td_right = ParagraphStyle(
        'RepTDRight',
        parent=styles['Normal'],
        fontName=reg_font,
        fontSize=7,
        leading=9,
        alignment=2,
        textColor=colors.HexColor('#0F172A'),
        shaping=shaping,
        splitLongWords=1,
    )
    badge_style = ParagraphStyle(
        'RepBadge',
        parent=styles['Normal'],
        fontName=bold_font,
        fontSize=6.5,
        leading=8.5,
        alignment=1,
        shaping=shaping,
        splitLongWords=1,
    )
    appendix_style = ParagraphStyle(
        'RepAppendix',
        parent=styles['Normal'],
        fontName=reg_font,
        fontSize=7,
        leading=9.5,
        alignment=0,
        textColor=colors.HexColor('#334155'),
        shaping=shaping,
        splitLongWords=1,
    )

    return {
        'title': title_style,
        'subtitle': subtitle_style,
        'meta_label': meta_label_style,
        'meta_val': meta_val_style,
        'th': th_style,
        'th_left': th_left_style,
        'td_left': td_left,
        'td_left_ascii': td_left_ascii,
        'td_center': td_center,
        'td_right': td_right,
        'badge': badge_style,
        'appendix': appendix_style,
        'font_regular': reg_font,
        'font_bold': bold_font,
    }


# ── 5. DAILY ATTENDANCE REGISTER PDF EXPORT ──────────────────────────────────

def render_daily_attendance_pdf(report_data, filters=None, user=None):
    """
    Renders the Authoritative Daily Attendance Register in A4 Landscape.
    Column order: SN | Employee + Department | Employee ID | Date | In | Out | Hours | Type | Status | Remarks.
    Every column width is mathematically derived from doc.width and asserted.
    """
    filters = filters or {}
    start_date = report_data.get('start_date') or report_data.get('report_date') or timezone.localdate()
    end_date = report_data.get('end_date') or start_date
    records = report_data.get('rows', [])

    styles = get_report_styles()
    buffer = io.BytesIO()
    doc = _create_pdf_doc(buffer)
    avail_w = doc.width

    # Assert and allocate strictly bounded column widths (10 columns):
    # SN (28) | Emp+Dept (150) | ID (64) | Date (62) | In (54) | Out (54) | Hours (46) | Type (68) | Status (70) | Remarks (Remainder)
    fixed_sum = 28 + 150 + 64 + 62 + 54 + 54 + 46 + 68 + 70  # 596 pt
    remarks_w = math.floor((avail_w - fixed_sum) * 100) / 100.0  # ~189.88 pt
    col_widths = [28, 150, 64, 62, 54, 54, 46, 68, 70, remarks_w]

    assert sum(col_widths) <= avail_w + 1e-3, f"Total column width {sum(col_widths)} exceeds available width {avail_w}"

    story = []
    appendix_notes = []

    # ── Report Header ────────────────────────────────────────────────────────
    date_display = start_date.strftime('%d %B %Y') if start_date == end_date else f"{start_date.strftime('%d %b %Y')} to {end_date.strftime('%d %b %Y')}"
    gen_time_str = timezone.localtime(timezone.now(), BD_TZ).strftime('%d %b %Y, %I:%M:%S %p')

    header_table_data = [
        [
            Paragraph("<b>FieldTrack</b> — Daily Attendance Register", styles['title']),
            Paragraph(f"<b>Timezone:</b> Asia/Dhaka (BST, UTC+6)<br/><b>Generated:</b> {gen_time_str}", styles['subtitle'])
        ],
        [
            Paragraph(
                f"<b>Date:</b> {date_display} &nbsp;|&nbsp; "
                f"<b>Branch:</b> {html.escape(str(filters.get('branch_name', 'All Branches')))} &nbsp;|&nbsp; "
                f"<b>Department:</b> {html.escape(str(filters.get('department_name', 'All Departments')))} &nbsp;|&nbsp; "
                f"<b>Status:</b> {html.escape(str(filters.get('status', 'All Statuses')).title())}",
                styles['subtitle']
            ),
            Paragraph(
                f"<b>Present:</b> {report_data.get('present', 0)} &nbsp;|&nbsp; "
                f"<b>Late:</b> {report_data.get('late', 0)} &nbsp;|&nbsp; "
                f"<b>Absent:</b> {report_data.get('absent', 0)} &nbsp;|&nbsp; "
                f"<b>Total Hours:</b> {report_data.get('total_hours', 0.0):.2f}h",
                styles['subtitle']
            )
        ]
    ]

    header_table = Table(header_table_data, colWidths=[avail_w * 0.62, avail_w * 0.38])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 1),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 8))

    # ── Table Data Construction ──────────────────────────────────────────────
    table_headers = [
        Paragraph("<b>SN</b>", styles['th']),
        Paragraph("<b>Employee + Department</b>", styles['th_left']),
        Paragraph("<b>Employee ID</b>", styles['th']),
        Paragraph("<b>Date</b>", styles['th']),
        Paragraph("<b>In</b>", styles['th']),
        Paragraph("<b>Out</b>", styles['th']),
        Paragraph("<b>Hours</b>", styles['th']),
        Paragraph("<b>Type</b>", styles['th']),
        Paragraph("<b>Status</b>", styles['th']),
        Paragraph("<b>Remarks</b>", styles['th_left']),
    ]
    table_data = [table_headers]

    reg_font = styles['font_regular']
    bold_font = styles['font_bold']
    td_left_shaped = styles['td_left']
    td_left_ascii = styles['td_left_ascii']
    td_center_style = styles['td_center']

    t_style = [
        ('BACKGROUND', (0, 0), (-1, 0), _C_SLATE_100),
        ('GRID', (0, 0), (-1, -1), 0.4, _C_SLATE_300),
        ('BOX', (0, 0), (-1, -1), 0.6, _C_SLATE_400),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('FONTNAME', (0, 1), (-1, -1), reg_font),
        ('FONTSIZE', (0, 1), (-1, -1), 7),
        ('TEXTCOLOR', (0, 1), (-1, -1), _C_SLATE_900),
        ('ALIGN', (0, 1), (0, -1), 'CENTER'),
        ('ALIGN', (2, 1), (5, -1), 'CENTER'),
        ('ALIGN', (6, 1), (6, -1), 'RIGHT'),
        ('ALIGN', (7, 1), (8, -1), 'CENTER'),
    ]

    if not records:
        empty_cell = Paragraph(
            "<font color='#64748B'><i>No attendance records found for the selected criteria.</i></font>",
            td_center_style
        )
        empty_row = [empty_cell] + [""] * 9
        table_data.append(empty_row)
        t_style.append(('SPAN', (0, 1), (-1, 1)))
        t_style.append(('TOPPADDING', (0, 1), (-1, 1), 16))
        t_style.append(('BOTTOMPADDING', (0, 1), (-1, 1), 16))
    else:
        for idx, r in enumerate(records, 1):
            emp = r['employee']
            ci = r.get('check_in')
            cis = r.get('check_in_sessions') or []
            fvs = r.get('field_visits') or []
            all_sessions = cis + fvs

            ci_times = [s.check_in_time for s in all_sessions if s and s.check_in_time]
            earliest_ci = min(ci_times) if ci_times else None
            if 'is_open_session' in r:
                has_open_session = bool(r.get('is_open_session'))
                latest_co = None if has_open_session else r.get('check_out_time')
            else:
                co_times = [s.check_out_time for s in cis if s and s.check_out_time]
                has_open_session = any(s and s.check_in_time and not s.check_out_time for s in cis)
                latest_co = None if has_open_session else (max(co_times) if co_times else None)

            # In string (with seconds)
            if earliest_ci:
                in_str = format_time_with_seconds(earliest_ci)
            elif r['status'] == 'on_leave':
                in_str = 'Leave'
            elif r['status'] == 'holiday':
                in_str = 'Holiday'
            elif r.get('is_absent'):
                in_str = 'Absent'
            else:
                in_str = '—'

            # Out string: empty ('—') while any office session remains open or for field-only days
            if latest_co and not has_open_session:
                out_str = format_time_with_seconds(latest_co)
            else:
                out_str = '—'

            # Hours (right-aligned numeric total)
            hrs = float(r.get('total_hours') or 0.0)
            hrs_str = f"{hrs:.2f}h" if hrs > 0 else "0.00h"

            # Type
            if len(cis) > 0 and len(fvs) > 0:
                type_str = f"Check In / Field\n({len(cis)}+{len(fvs)})"
            elif len(cis) > 1:
                type_str = f"Check In\n({len(cis)} Sessions)"
            elif len(cis) == 1:
                type_str = ci.get_type_display() if (ci and hasattr(ci, 'get_type_display')) else 'Check In'
            elif len(fvs) > 0:
                type_str = f"Field Visit ({len(fvs)})" if len(fvs) > 1 else "Field Visit"
            elif r['status'] == 'on_leave':
                type_str = 'Leave'
            elif r['status'] == 'holiday':
                type_str = 'Holiday'
            else:
                type_str = '—'

            # Status Badge color formatting (plain string + TableStyle avoids Paragraph XML overhead)
            status_text = r.get('status_display', 'Present')
            st_lower = r.get('status', '').lower()
            status_bold = True
            if 'late' in st_lower:
                status_color = _C_AMBER
            elif 'absent' in st_lower or r.get('is_absent'):
                status_color = _C_RED
            elif 'leave' in st_lower:
                status_color = _C_BLUE
            elif 'holiday' in st_lower:
                status_color = _C_SLATE_500
                status_bold = False
            elif has_open_session:
                status_color = _C_TEAL
            else:
                status_color = _C_GREEN

            if len(status_text) <= 16 and status_text.isascii():
                status_cell = status_text
            else:
                color_hex = '#64748B' if not status_bold else (
                    '#B45309' if status_color == _C_AMBER else
                    '#DC2626' if status_color == _C_RED else
                    '#2563EB' if status_color == _C_BLUE else
                    '#0D9488' if status_color == _C_TEAL else '#16A34A'
                )
                status_cell = Paragraph(
                    f"<font color='{color_hex}'><b>{_esc(status_text)}</b></font>",
                    td_center_style
                )

            if isinstance(status_cell, str):
                t_style.append(('TEXTCOLOR', (8, idx), (8, idx), status_color))
                if status_bold:
                    t_style.append(('FONTNAME', (8, idx), (8, idx), bold_font))

            # Employee + Department
            dept_name = get_employee_department_name(emp)
            emp_name_str = emp.full_name or ''
            emp_display = f"<b>{_esc(emp_name_str)}</b>"
            if dept_name:
                emp_display += f"<br/><font color='#64748B' size='6.5'>{_esc(dept_name)}</font>"
            emp_style = _style_for_text(f"{emp_name_str}{dept_name}", td_left_shaped, td_left_ascii)

            # Employee ID: plain string when short ASCII, Paragraph when long/non-ASCII so it wraps
            raw_emp_id = emp.employee_id or '—'
            if len(raw_emp_id) <= 12 and raw_emp_id.isascii():
                emp_id_cell = raw_emp_id
            else:
                emp_id_cell = Paragraph(
                    _esc(raw_emp_id),
                    _style_for_text(raw_emp_id, td_center_style, td_left_ascii)
                )

            # Remarks & Appendix Management
            raw_notes = (r.get('notes') or '').strip()
            loc_str = ''
            if r.get('location') and hasattr(r['location'], 'address') and r['location'].address:
                loc_str = r['location'].address.strip()

            full_remarks = raw_notes
            if loc_str and loc_str not in full_remarks:
                full_remarks = f"{full_remarks} ({loc_str})" if full_remarks else loc_str

            if not full_remarks:
                remarks_cell = '—'
                t_style.append(('TEXTCOLOR', (9, idx), (9, idx), _C_SLATE_400))
            elif len(full_remarks) > 75:
                note_num = len(appendix_notes) + 1
                appendix_notes.append({
                    'num': note_num,
                    'emp': f"{emp.full_name} ({emp.employee_id})",
                    'date': str(r.get('date', '')),
                    'text': full_remarks
                })
                snippet = full_remarks[:65].rstrip() + "..."
                remarks_html = f"{_esc(snippet)} <font color='#2563EB'><b>[Note {note_num}]</b></font>"
                remarks_cell = Paragraph(remarks_html, _style_for_text(snippet, td_left_shaped, td_left_ascii))
            elif len(full_remarks) <= 34 and full_remarks.isascii() and '\n' not in full_remarks:
                remarks_cell = full_remarks
            else:
                remarks_cell = Paragraph(_esc(full_remarks), _style_for_text(full_remarks, td_left_shaped, td_left_ascii))

            row_cells = [
                str(idx),
                Paragraph(emp_display, emp_style),
                emp_id_cell,
                str(r.get('date', '')),
                in_str,
                out_str,
                hrs_str,
                type_str if (len(type_str) <= 18 and type_str.isascii()) else Paragraph(_esc(type_str), td_center_style),
                status_cell,
                remarks_cell,
            ]
            table_data.append(row_cells)

            # Alternating soft zebra rows
            row_bg = _C_WHITE if idx % 2 != 0 else _C_SLATE_50
            t_style.append(('BACKGROUND', (0, idx), (-1, idx), row_bg))

    register_table = Table(table_data, colWidths=col_widths, repeatRows=1)
    register_table.setStyle(TableStyle(t_style))
    story.append(register_table)

    # ── Numbered Appendix for Long Notes ─────────────────────────────────────
    story.extend(_build_appendix_flowables(appendix_notes, avail_w, styles, include_employee=True))

    doc.build(story, canvasmaker=NumberedCanvas)
    return buffer.getvalue()


# ── 6. MONTHLY ATTENDANCE SUMMARY & SPLIT MATRIX PDF EXPORT ──────────────────

def _monthly_day_cell_style(dc):
    """Returns (code_str, Color, is_bold) for a monthly matrix day cell."""
    st = (dc.get('status') or '').lower()
    if dc.get('is_late'):
        return 'L', _C_AMBER, True
    if dc.get('is_present'):
        return 'P', _C_GREEN, True
    if dc.get('is_absent'):
        return 'A', _C_RED, True
    if 'leave' in st or dc.get('is_on_leave'):
        return 'LV', _C_BLUE, True
    if 'holiday' in st or 'weekend' in st or dc.get('is_holiday'):
        return 'H', _C_SLATE_500, False
    return '—', _C_SLATE_400, False


def _build_monthly_matrix_table(rows, days_slice, avail_w, styles, include_summary=False):
    """
    Builds a single Monthly Daily Matrix section with repeated Legend + header rows,
    repeated employee identity (SN, Full Name, Emp ID), and minimum readable day columns.
    """
    sn_w = 28
    emp_w = 144
    summary_cols_w = [26, 26, 26, 40, 38] if include_summary else []
    fixed_w = sn_w + emp_w + sum(summary_cols_w)
    day_w = math.floor(((avail_w - fixed_w) / len(days_slice)) * 100) / 100.0
    col_widths = [sn_w, emp_w] + [day_w] * len(days_slice) + summary_cols_w
    assert sum(col_widths) <= avail_w + 1e-3, f"Matrix width {sum(col_widths)} exceeds {avail_w}"

    total_cols = len(col_widths)
    legend_text = (
        "<b>Legend:</b> "
        "<font color='#16A34A'><b>P</b></font> = Present &nbsp;|&nbsp; "
        "<font color='#B45309'><b>L</b></font> = Late &nbsp;|&nbsp; "
        "<font color='#DC2626'><b>A</b></font> = Absent &nbsp;|&nbsp; "
        "<font color='#2563EB'><b>LV</b></font> = Leave &nbsp;|&nbsp; "
        "<font color='#64748B'><b>H</b></font> = Holiday / Weekend &nbsp;|&nbsp; "
        "<font color='#94A3B8'><b>—</b></font> = No Record"
    )
    legend_row = [Paragraph(legend_text, styles['appendix'])] + [''] * (total_cols - 1)

    header_row = [
        Paragraph("<b>SN</b>", styles['th']),
        Paragraph("<b>Employee &amp; ID</b>", styles['th_left']),
    ] + [Paragraph(f"<b>{d.day}</b>", styles['th']) for d in days_slice]
    if include_summary:
        header_row.extend([
            Paragraph("<b>P</b>", styles['th']),
            Paragraph("<b>A</b>", styles['th']),
            Paragraph("<b>L</b>", styles['th']),
            Paragraph("<b>Hours</b>", styles['th']),
            Paragraph("<b>Att%</b>", styles['th']),
        ])

    m_data = [legend_row, header_row]
    reg_font = styles['font_regular']
    bold_font = styles['font_bold']

    m_style = [
        ('SPAN', (0, 0), (-1, 0)),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F8FAFC')),
        ('BACKGROUND', (0, 1), (-1, 1), colors.HexColor('#F1F5F9')),
        ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#CBD5E1')),
        ('BOX', (0, 0), (-1, -1), 0.6, colors.HexColor('#94A3B8')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('LEFTPADDING', (0, 0), (-1, -1), 1.5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 1.5),
        ('LEFTPADDING', (0, 0), (0, 0), 4),
        ('FONTNAME', (0, 2), (-1, -1), reg_font),
        ('FONTSIZE', (0, 2), (-1, -1), 6.5),
        ('ALIGN', (0, 2), (0, -1), 'CENTER'),
        ('ALIGN', (2, 2), (-1, -1), 'CENTER'),
    ]

    if not rows:
        empty_cell = Paragraph(
            "<font color='#64748B'><i>No attendance records found for the selected criteria.</i></font>",
            styles['td_center']
        )
        m_data.append([empty_cell] + [''] * (total_cols - 1))
        m_style.append(('SPAN', (0, 2), (-1, 2)))
        m_style.append(('TOPPADDING', (0, 2), (-1, 2), 12))
        m_style.append(('BOTTOMPADDING', (0, 2), (-1, 2), 12))
    else:
        day_start_col = 2
        td_left_shaped = styles['td_left']
        td_left_ascii = styles['td_left_ascii']
        for idx, r in enumerate(rows, 1):
            row_idx = idx + 1  # account for legend (0) and header (1)
            emp = r['employee']
            raw_name = emp.full_name or ''
            raw_eid = getattr(emp, 'employee_id', '') or '—'
            emp_html = (
                f"<b>{_esc(raw_name)}</b><br/>"
                f"<font color='#64748B' size='6'>{_esc(raw_eid)}</font>"
            )
            emp_style = _style_for_text(f"{raw_name}{raw_eid}", td_left_shaped, td_left_ascii)
            row_c = [
                str(idx),
                Paragraph(emp_html, emp_style),
            ]
            day_calcs = r.get('day_calcs', {})
            for d_offset, d in enumerate(days_slice):
                dc = day_calcs.get(d, {})
                code, cell_color, is_bold = _monthly_day_cell_style(dc)
                row_c.append(code)
                col_i = day_start_col + d_offset
                m_style.append(('TEXTCOLOR', (col_i, row_idx), (col_i, row_idx), cell_color))
                if is_bold:
                    m_style.append(('FONTNAME', (col_i, row_idx), (col_i, row_idx), bold_font))

            if include_summary:
                sum_col = day_start_col + len(days_slice)
                row_c.extend([
                    str(r.get('present', 0)),
                    str(r.get('absent', 0)),
                    str(r.get('late', 0)),
                    f"{r.get('total_hours', 0.0):.1f}h",
                    r.get('att_pct_display', '—') if r.get('att_pct') is None else f"{r.get('att_pct', 0)}%",
                ])
                m_style.append(('ALIGN', (sum_col + 3, row_idx), (sum_col + 4, row_idx), 'RIGHT'))

            m_data.append(row_c)
            row_bg = _C_WHITE if idx % 2 != 0 else _C_SLATE_50
            m_style.append(('BACKGROUND', (0, row_idx), (-1, row_idx), row_bg))

    matrix_table = Table(m_data, colWidths=col_widths, repeatRows=2)
    matrix_table.setStyle(TableStyle(m_style))
    return matrix_table


def render_monthly_attendance_pdf(monthly_data, filters=None, user=None):
    """
    Renders Monthly Attendance Report in A4 Landscape.
    Includes:
    1. Executive Monthly Summary Table (SN | Employee+Dept | Emp ID | Present | Absent | Leave | Late | Field | Hours | OT | Att %)
    2. Split Day Matrix (Days 1–15 on Section A, Days 16–28/29/30/31 on Section B) with repeated headers, employee identity, and legends.
    """
    filters = filters or {}
    year = monthly_data.get('year', timezone.localdate().year)
    month = monthly_data.get('month', timezone.localdate().month)
    all_days = monthly_data.get('all_days', [])
    rows = monthly_data.get('rows', [])

    styles = get_report_styles()
    buffer = io.BytesIO()
    doc = _create_pdf_doc(buffer)
    avail_w = doc.width

    month_name = date(year, month, 1).strftime('%B %Y')
    gen_time_str = timezone.localtime(timezone.now(), BD_TZ).strftime('%d %b %Y, %I:%M:%S %p')

    story = []

    # ── Header ───────────────────────────────────────────────────────────────
    header_table_data = [
        [
            Paragraph("<b>FieldTrack</b> — Monthly Attendance Summary", styles['title']),
            Paragraph(f"<b>Timezone:</b> Asia/Dhaka (BST, UTC+6)<br/><b>Generated:</b> {gen_time_str}", styles['subtitle'])
        ],
        [
            Paragraph(
                f"<b>Month:</b> {_esc(month_name)} &nbsp;|&nbsp; "
                f"<b>Branch:</b> {_esc(filters.get('branch_name', 'All Branches'))} &nbsp;|&nbsp; "
                f"<b>Total Working Days:</b> {len(all_days)}",
                styles['subtitle']
            ),
            Paragraph(
                f"<b>Total Present:</b> {monthly_data.get('total_present', 0)} &nbsp;|&nbsp; "
                f"<b>Total Absent:</b> {monthly_data.get('total_absent', 0)} &nbsp;|&nbsp; "
                f"<b>Total Late:</b> {monthly_data.get('total_late', 0)} &nbsp;|&nbsp; "
                f"<b>Avg Att:</b> {monthly_data.get('avg_att_pct_display', '—') if monthly_data.get('avg_att_pct') is None else str(monthly_data.get('avg_att_pct', 0)) + '%'}",
                styles['subtitle']
            )
        ]
    ]
    header_table = Table(header_table_data, colWidths=[avail_w * 0.60, avail_w * 0.40])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 1),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 8))

    # ── 1. Executive Summary Table ───────────────────────────────────────────
    # Fixed columns sum = 28 + 62 + 46*5 + 56 + 58 + 48 = 482 pt; remainder to Employee + Dept
    fixed_sum = 28 + 62 + 46 + 46 + 46 + 46 + 46 + 56 + 58 + 48
    emp_col_w = math.floor((avail_w - fixed_sum) * 100) / 100.0
    sum_widths = [28, emp_col_w, 62, 46, 46, 46, 46, 46, 56, 58, 48]
    assert sum(sum_widths) <= avail_w + 1e-3, f"Summary width {sum(sum_widths)} exceeds {avail_w}"

    sum_headers = [
        Paragraph("<b>SN</b>", styles['th']),
        Paragraph("<b>Employee + Department</b>", styles['th_left']),
        Paragraph("<b>Emp ID</b>", styles['th']),
        Paragraph("<b>Present</b>", styles['th']),
        Paragraph("<b>Absent</b>", styles['th']),
        Paragraph("<b>Leave</b>", styles['th']),
        Paragraph("<b>Late</b>", styles['th']),
        Paragraph("<b>Field</b>", styles['th']),
        Paragraph("<b>Total Hours</b>", styles['th']),
        Paragraph("<b>Overtime</b>", styles['th']),
        Paragraph("<b>Att %</b>", styles['th']),
    ]
    sum_table_data = [sum_headers]

    reg_font = styles['font_regular']
    bold_font = styles['font_bold']
    td_left_shaped = styles['td_left']
    td_left_ascii = styles['td_left_ascii']

    t_style = [
        ('BACKGROUND', (0, 0), (-1, 0), _C_SLATE_100),
        ('GRID', (0, 0), (-1, -1), 0.4, _C_SLATE_300),
        ('BOX', (0, 0), (-1, -1), 0.6, _C_SLATE_400),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 3),
        ('RIGHTPADDING', (0, 0), (-1, -1), 3),
        ('FONTNAME', (0, 1), (-1, -1), reg_font),
        ('FONTSIZE', (0, 1), (-1, -1), 7),
        ('TEXTCOLOR', (0, 1), (-1, -1), _C_SLATE_900),
        ('ALIGN', (0, 1), (0, -1), 'CENTER'),
        ('ALIGN', (2, 1), (7, -1), 'CENTER'),
        ('ALIGN', (8, 1), (10, -1), 'RIGHT'),
    ]

    if not rows:
        empty_cell = Paragraph(
            "<font color='#64748B'><i>No attendance records found for the selected criteria.</i></font>",
            styles['td_center']
        )
        sum_table_data.append([empty_cell] + [""] * (len(sum_widths) - 1))
        t_style.append(('SPAN', (0, 1), (-1, 1)))
        t_style.append(('TOPPADDING', (0, 1), (-1, 1), 14))
        t_style.append(('BOTTOMPADDING', (0, 1), (-1, 1), 14))
    else:
        for idx, r in enumerate(rows, 1):
            emp = r['employee']
            dept = get_employee_department_name(emp)
            raw_name = emp.full_name or ''
            emp_display = f"<b>{_esc(raw_name)}</b>"
            if dept:
                emp_display += f"<br/><font color='#64748B' size='6.5'>{_esc(dept)}</font>"
            emp_style = _style_for_text(f"{raw_name}{dept}", td_left_shaped, td_left_ascii)

            p_cnt = int(r.get('present', 0))
            a_cnt = int(r.get('absent', 0))
            l_cnt = int(r.get('late', 0))
            ol_cnt = int(r.get('on_leave', 0))
            fv_cnt = int(r.get('field_visits', 0))
            tot_hrs = f"{r.get('total_hours', 0.0):.2f}h"
            raw_ot = str(r.get('overtime', '—'))
            att_pct = r.get('att_pct_display', '—') if r.get('att_pct') is None else f"{r.get('att_pct', 0)}%"

            raw_eid = emp.employee_id or '—'
            eid_cell = raw_eid if (len(raw_eid) <= 12 and raw_eid.isascii()) else Paragraph(_esc(raw_eid), styles['td_center'])
            ot_cell = raw_ot if (len(raw_ot) <= 12 and raw_ot.isascii()) else Paragraph(_esc(raw_ot), styles['td_right'])

            if a_cnt > 0:
                t_style.append(('TEXTCOLOR', (4, idx), (4, idx), _C_RED))
                t_style.append(('FONTNAME', (4, idx), (4, idx), bold_font))
            if l_cnt > 0:
                t_style.append(('TEXTCOLOR', (6, idx), (6, idx), _C_AMBER))
                t_style.append(('FONTNAME', (6, idx), (6, idx), bold_font))

            sum_table_data.append([
                str(idx),
                Paragraph(emp_display, emp_style),
                eid_cell,
                str(p_cnt),
                str(a_cnt),
                str(ol_cnt),
                str(l_cnt),
                str(fv_cnt),
                tot_hrs,
                ot_cell,
                att_pct,
            ])
            row_bg = _C_WHITE if idx % 2 != 0 else _C_SLATE_50
            t_style.append(('BACKGROUND', (0, idx), (-1, idx), row_bg))

        # Summary Footer Row
        tot_hrs_sum = f"{sum(r.get('total_hours', 0.0) for r in rows):.2f}h"
        footer_idx = len(sum_table_data)
        sum_table_data.append([
            "",
            "Monthly Totals",
            "",
            str(monthly_data.get('total_present', 0)),
            str(monthly_data.get('total_absent', 0)),
            str(monthly_data.get('total_on_leave', 0)),
            str(monthly_data.get('total_late', 0)),
            str(monthly_data.get('total_field', 0)),
            tot_hrs_sum,
            str(monthly_data.get('total_overtime', '0h')),
            monthly_data.get('avg_att_pct_display', '—') if monthly_data.get('avg_att_pct') is None else f"{monthly_data.get('avg_att_pct', 0)}%",
        ])
        t_style.append(('BACKGROUND', (0, footer_idx), (-1, footer_idx), _C_SLATE_200))
        t_style.append(('FONTNAME', (0, footer_idx), (-1, footer_idx), bold_font))
        t_style.append(('ALIGN', (1, footer_idx), (1, footer_idx), 'LEFT'))
        t_style.append(('LINEABOVE', (0, footer_idx), (-1, footer_idx), 1, _C_SLATE_400))

    sum_table = Table(sum_table_data, colWidths=sum_widths, repeatRows=1)
    sum_table.setStyle(TableStyle(t_style))
    story.append(sum_table)

    # ── 2. Split Daily Matrix (Handling 28/29/30/31-day Months cleanly) ───────
    if all_days:
        min_day_w = 24.0
        max_days_p1 = max(1, int((avail_w - 172) // min_day_w))
        max_days_p2 = max(1, int((avail_w - 172 - 156) // min_day_w))

        # Standard split: Days 1..15 and Days 16..end (with dynamic fallback if narrower)
        split_at = min(15, max_days_p1)
        chunks = [all_days[:split_at]]
        rem_days = all_days[split_at:]
        while rem_days:
            if len(rem_days) <= max_days_p2:
                chunks.append(rem_days)
                break
            chunks.append(rem_days[:max_days_p1])
            rem_days = rem_days[max_days_p1:]

        for chunk_idx, days_slice in enumerate(chunks):
            is_last = (chunk_idx == len(chunks) - 1)
            story.append(PageBreak())
            start_d = days_slice[0].day
            end_d = days_slice[-1].day
            title_suffix = " &amp; Summary" if is_last else ""
            story.append(Paragraph(
                f"<b>Monthly Attendance Daily Matrix (Days {start_d} to {end_d}){title_suffix} — {_esc(month_name)}</b>",
                styles['meta_label']
            ))
            story.append(Spacer(1, 5))
            story.append(_build_monthly_matrix_table(rows, days_slice, avail_w, styles, include_summary=is_last))

    doc.build(story, canvasmaker=NumberedCanvas)
    return buffer.getvalue()


# ── 7. INDIVIDUAL EMPLOYEE ATTENDANCE STATEMENT PDF EXPORT ───────────────────

def render_individual_attendance_pdf(employee, year, month, monthly_data, user=None):
    """
    Renders an Individual Employee Monthly Attendance Statement in A4 Landscape.
    Includes Employee details card, monthly KPI statistics, day-by-day timeline,
    timestamps with seconds, and notes appendix.
    """
    styles = get_report_styles()
    buffer = io.BytesIO()
    doc = _create_pdf_doc(buffer)
    avail_w = doc.width

    month_name = date(year, month, 1).strftime('%B %Y')
    gen_time_str = timezone.localtime(timezone.now(), BD_TZ).strftime('%d %b %Y, %I:%M:%S %p')

    story = []
    appendix_notes = []

    # ── Employee Header Card ─────────────────────────────────────────────────
    dept_name = get_employee_department_name(employee)
    branch_name = employee.branch.name if employee.branch else 'Main Branch'
    shift_str = 'General Shift (09:00 - 18:00)'
    if hasattr(employee, 'master_employee') and employee.master_employee and employee.master_employee.shift:
        shift_str = str(employee.master_employee.shift)

    header_table_data = [
        [
            Paragraph(f"<b>FieldTrack</b> — Individual Attendance Statement", styles['title']),
            Paragraph(f"<b>Timezone:</b> Asia/Dhaka (BST, UTC+6)<br/><b>Generated:</b> {gen_time_str}", styles['subtitle'])
        ],
        [
            Paragraph(
                f"<b>Employee Name:</b> {html.escape(employee.full_name)} &nbsp;|&nbsp; "
                f"<b>Employee ID:</b> {html.escape(employee.employee_id or '—')} &nbsp;|&nbsp; "
                f"<b>Department:</b> {html.escape(dept_name or '—')} &nbsp;|&nbsp; "
                f"<b>Branch:</b> {html.escape(branch_name)}",
                styles['subtitle']
            ),
            Paragraph(
                f"<b>Month Period:</b> {month_name} &nbsp;|&nbsp; "
                f"<b>Shift:</b> {html.escape(shift_str)}",
                styles['subtitle']
            )
        ]
    ]
    header_table = Table(header_table_data, colWidths=[avail_w * 0.65, avail_w * 0.35])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 1),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 6))

    # ── Monthly Summary KPI Strip ────────────────────────────────────────────
    emp_row = monthly_data['rows'][0] if monthly_data.get('rows') else {}
    all_days = monthly_data.get('all_days', [])

    kpi_items = [
        f"<b>Total Days:</b> {len(all_days)}",
        f"<b>Present:</b> {emp_row.get('present', 0)}",
        f"<b>Absent:</b> {emp_row.get('absent', 0)}",
        f"<b>On Leave:</b> {emp_row.get('on_leave', 0)}",
        f"<b>Late:</b> {emp_row.get('late', 0)}",
        f"<b>Field Visits:</b> {emp_row.get('field_visits', 0)}",
        f"<b>Total Hours:</b> {emp_row.get('total_hours', 0.0):.2f}h",
        f"<b>Attendance:</b> {emp_row.get('att_pct_display', '—') if emp_row.get('att_pct') is None else str(emp_row.get('att_pct', 0)) + '%'}",
    ]
    kpi_p = Paragraph(" &nbsp;&nbsp;|&nbsp;&nbsp; ".join(kpi_items), styles['meta_label'])
    kpi_table = Table([[kpi_p]], colWidths=[avail_w])
    kpi_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F1F5F9')),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#CBD5E1')),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(kpi_table)
    story.append(Spacer(1, 8))

    # ── Day-by-Day Register Table ────────────────────────────────────────────
    # Columns: Date (64) | Day (46) | In (60) | Out (60) | Hours (50) | Type (80) | Status (70) | Remarks (Remainder)
    fixed_sum = 64 + 46 + 60 + 60 + 50 + 80 + 70  # 430 pt
    rem_w = round(avail_w - fixed_sum, 2)         # ~355.89 pt
    col_w = [64, 46, 60, 60, 50, 80, 70, rem_w]

    t_head = [
        Paragraph("<b>Date</b>", styles['th']),
        Paragraph("<b>Day</b>", styles['th']),
        Paragraph("<b>In</b>", styles['th']),
        Paragraph("<b>Out</b>", styles['th']),
        Paragraph("<b>Hours</b>", styles['th']),
        Paragraph("<b>Type</b>", styles['th']),
        Paragraph("<b>Status</b>", styles['th']),
        Paragraph("<b>Remarks</b>", styles['th_left']),
    ]
    t_data = [t_head]
    t_style = [
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F1F5F9')),
        ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#CBD5E1')),
        ('BOX', (0, 0), (-1, -1), 0.6, colors.HexColor('#94A3B8')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
    ]

    day_calcs = emp_row.get('day_calcs', {})

    for idx, d in enumerate(all_days, 1):
        dc = day_calcs.get(d, {})
        ci = dc.get('check_in')
        cis = dc.get('check_in_sessions') or []
        fvs = dc.get('field_visits') or []
        all_s = cis + fvs

        ci_times = [s.check_in_time for s in all_s if s and s.check_in_time]
        earliest_ci = min(ci_times) if ci_times else None
        if 'is_open_session' in dc:
            has_open = bool(dc.get('is_open_session'))
            latest_co = None if has_open else dc.get('check_out_time')
        else:
            co_times = [s.check_out_time for s in cis if s and s.check_out_time]
            has_open = any(s and s.check_in_time and not s.check_out_time for s in cis)
            latest_co = None if has_open else (max(co_times) if co_times else None)

        # In string
        if earliest_ci:
            in_str = format_time_with_seconds(earliest_ci)
        elif dc.get('is_on_leave'):
            in_str = 'Leave'
        elif dc.get('is_holiday'):
            in_str = 'Holiday'
        elif dc.get('is_absent'):
            in_str = 'Absent'
        else:
            in_str = '—'

        # Out string: empty ('—') while any office session remains open or for field-only days
        if latest_co and not has_open:
            out_str = format_time_with_seconds(latest_co)
        else:
            out_str = '—'

        hrs = float(dc.get('total_hours') or 0.0)
        hrs_str = f"{hrs:.2f}h" if hrs > 0 else "0.00h"

        # Type
        if len(cis) > 0 and len(fvs) > 0:
            type_str = f"Office / Field ({len(cis)}+{len(fvs)})"
        elif len(cis) > 1:
            type_str = f"Office ({len(cis)} Sessions)"
        elif len(cis) == 1:
            type_str = 'Office'
        elif len(fvs) > 0:
            type_str = f"Field Visit ({len(fvs)})" if len(fvs) > 1 else "Field Visit"
        elif dc.get('is_on_leave'):
            type_str = 'Leave'
        elif dc.get('is_holiday'):
            type_str = 'Holiday'
        else:
            type_str = '—'

        # Status badge
        status_text = dc.get('status_display', 'Present')
        st_lower = dc.get('status', '').lower()
        if 'late' in st_lower or dc.get('is_late'):
            status_html = f"<font color='#B45309'><b>{html.escape(status_text)}</b></font>"
        elif 'absent' in st_lower or dc.get('is_absent'):
            status_html = f"<font color='#DC2626'><b>{html.escape(status_text)}</b></font>"
        elif 'leave' in st_lower or dc.get('is_on_leave'):
            status_html = f"<font color='#2563EB'><b>{html.escape(status_text)}</b></font>"
        elif 'holiday' in st_lower or dc.get('is_holiday'):
            status_html = f"<font color='#64748B'>{html.escape(status_text)}</font>"
        elif has_open:
            status_html = f"<font color='#0D9488'><b>{html.escape(status_text)}</b></font>"
        else:
            status_html = f"<font color='#16A34A'><b>{html.escape(status_text)}</b></font>"

        # Remarks & Appendix
        raw_notes = (dc.get('notes') or (ci.note if ci and hasattr(ci, 'note') and ci.note else '')).strip()
        if not raw_notes and all_s:
            s_notes = [s.note.strip() for s in all_s if s and hasattr(s, 'note') and s.note and s.note.strip()]
            raw_notes = " | ".join(s_notes)
        if not raw_notes:
            remarks_html = "<font color='#94A3B8'>—</font>"
        elif len(raw_notes) > 75:
            note_num = len(appendix_notes) + 1
            appendix_notes.append({
                'num': note_num,
                'date': d.strftime('%Y-%m-%d (%a)'),
                'text': raw_notes
            })
            snippet = raw_notes[:65].rstrip() + "..."
            remarks_html = f"{html.escape(snippet)} <font color='#2563EB'><b>[Note {note_num}]</b></font>"
        else:
            remarks_html = html.escape(raw_notes)

        # Day name & weekend highlight
        day_name = d.strftime('%a')
        is_wknd = dc.get('is_holiday', False)

        row_c = [
            Paragraph(d.strftime('%Y-%m-%d'), styles['td_center']),
            Paragraph(f"<font color='#64748B'>{day_name}</font>" if is_wknd else day_name, styles['td_center']),
            Paragraph(in_str, styles['td_center']),
            Paragraph(out_str, styles['td_center']),
            Paragraph(hrs_str, styles['td_right']),
            Paragraph(html.escape(type_str), styles['td_center']),
            Paragraph(status_html, styles['td_center']),
            Paragraph(remarks_html, styles['td_left']),
        ]
        t_data.append(row_c)

        if is_wknd:
            t_style.append(('BACKGROUND', (0, idx), (-1, idx), colors.HexColor('#F8FAFC')))
        elif idx % 2 == 0:
            t_style.append(('BACKGROUND', (0, idx), (-1, idx), colors.HexColor('#F8FAFC')))
        else:
            t_style.append(('BACKGROUND', (0, idx), (-1, idx), colors.HexColor('#FFFFFF')))

    day_table = Table(t_data, colWidths=col_w, repeatRows=1)
    day_table.setStyle(TableStyle(t_style))
    story.append(day_table)

    # ── Remarks Appendix if long notes exist ─────────────────────────────────
    story.extend(_build_appendix_flowables(appendix_notes, avail_w, styles, include_employee=False))

    doc.build(story, canvasmaker=NumberedCanvas)
    return buffer.getvalue()
