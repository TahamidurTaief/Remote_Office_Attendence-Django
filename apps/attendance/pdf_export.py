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
    KeepTogether,
    HRFlowable,
)
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

BD_TZ = ZoneInfo('Asia/Dhaka')

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
        shaping=shaping
    )
    subtitle_style = ParagraphStyle(
        'RepSubtitle',
        parent=styles['Normal'],
        fontName=reg_font,
        fontSize=8,
        leading=11,
        textColor=colors.HexColor('#475569'),
        shaping=shaping
    )
    meta_label_style = ParagraphStyle(
        'RepMetaLabel',
        parent=styles['Normal'],
        fontName=bold_font,
        fontSize=7.5,
        leading=10,
        textColor=colors.HexColor('#1E293B'),
        shaping=shaping
    )
    meta_val_style = ParagraphStyle(
        'RepMetaVal',
        parent=styles['Normal'],
        fontName=reg_font,
        fontSize=7.5,
        leading=10,
        textColor=colors.HexColor('#334155'),
        shaping=shaping
    )
    th_style = ParagraphStyle(
        'RepTH',
        parent=styles['Normal'],
        fontName=bold_font,
        fontSize=7.5,
        leading=9.5,
        alignment=1,
        textColor=colors.HexColor('#1E293B'),
        shaping=shaping
    )
    th_left_style = ParagraphStyle(
        'RepTHLeft',
        parent=th_style,
        alignment=0,
        shaping=shaping
    )
    td_left = ParagraphStyle(
        'RepTDLeft',
        parent=styles['Normal'],
        fontName=reg_font,
        fontSize=7,
        leading=9,
        alignment=0,
        textColor=colors.HexColor('#0F172A'),
        shaping=shaping
    )
    td_center = ParagraphStyle(
        'RepTDCenter',
        parent=styles['Normal'],
        fontName=reg_font,
        fontSize=7,
        leading=9,
        alignment=1,
        textColor=colors.HexColor('#0F172A'),
        shaping=shaping
    )
    td_right = ParagraphStyle(
        'RepTDRight',
        parent=styles['Normal'],
        fontName=reg_font,
        fontSize=7,
        leading=9,
        alignment=2,
        textColor=colors.HexColor('#0F172A'),
        shaping=shaping
    )
    badge_style = ParagraphStyle(
        'RepBadge',
        parent=styles['Normal'],
        fontName=bold_font,
        fontSize=6.5,
        leading=8.5,
        alignment=1,
        shaping=shaping
    )
    appendix_style = ParagraphStyle(
        'RepAppendix',
        parent=styles['Normal'],
        fontName=reg_font,
        fontSize=7,
        leading=9.5,
        alignment=0,
        textColor=colors.HexColor('#334155'),
        shaping=shaping
    )

    return {
        'title': title_style,
        'subtitle': subtitle_style,
        'meta_label': meta_label_style,
        'meta_val': meta_val_style,
        'th': th_style,
        'th_left': th_left_style,
        'td_left': td_left,
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

    # Landscape A4: 841.89 x 595.27 pt
    # Available doc.width = 841.89 - (28 * 2) = 785.89 pt
    left_margin = 28
    right_margin = 28
    top_margin = 26
    bottom_margin = 32

    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=left_margin,
        rightMargin=right_margin,
        topMargin=top_margin,
        bottomMargin=bottom_margin,
    )

    avail_w = doc.width

    # Assert and allocate strictly bounded column widths (10 columns):
    # SN (26) | Emp+Dept (152) | ID (60) | Date (64) | In (54) | Out (54) | Hours (46) | Type (68) | Status (64) | Remarks (Remainder)
    fixed_sum = 26 + 152 + 60 + 64 + 54 + 54 + 46 + 68 + 64  # 588 pt
    remarks_w = round(avail_w - fixed_sum, 2)                 # ~197.89 pt
    col_widths = [26, 152, 60, 64, 54, 54, 46, 68, 64, remarks_w]

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

    if not records:
        empty_cell = Paragraph(
            "<font color='#64748B'><i>No attendance records found for the selected criteria.</i></font>",
            styles['td_center']
        )
        empty_row = [empty_cell] + [Paragraph("", styles['td_center'])] * 9
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
            co_times = [s.check_out_time for s in cis if s and s.check_out_time]
            earliest_ci = min(ci_times) if ci_times else None
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
                type_str = f"Check In / Field ({len(cis)}+{len(fvs)})"
            elif len(cis) > 1:
                type_str = f"Check In ({len(cis)} Sessions)"
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

            # Status Badge color formatting
            status_text = r.get('status_display', 'Present')
            st_lower = r.get('status', '').lower()
            if 'late' in st_lower:
                status_html = f"<font color='#B45309'><b>{html.escape(status_text)}</b></font>"
            elif 'absent' in st_lower or r.get('is_absent'):
                status_html = f"<font color='#DC2626'><b>{html.escape(status_text)}</b></font>"
            elif 'leave' in st_lower:
                status_html = f"<font color='#2563EB'><b>{html.escape(status_text)}</b></font>"
            elif 'holiday' in st_lower:
                status_html = f"<font color='#64748B'>{html.escape(status_text)}</font>"
            elif has_open_session:
                status_html = f"<font color='#0D9488'><b>{html.escape(status_text)}</b></font>"
            else:
                status_html = f"<font color='#16A34A'><b>{html.escape(status_text)}</b></font>"

            # Employee + Department
            dept_name = get_employee_department_name(emp)
            emp_display = f"<b>{html.escape(emp.full_name)}</b>"
            if dept_name:
                emp_display += f"<br/><font color='#64748B' size='6.5'>{html.escape(dept_name)}</font>"

            # Remarks & Appendix Management
            raw_notes = (r.get('notes') or '').strip()
            loc_str = ''
            if r.get('location') and hasattr(r['location'], 'address') and r['location'].address:
                loc_str = r['location'].address.strip()

            full_remarks = raw_notes
            if loc_str and loc_str not in full_remarks:
                full_remarks = f"{full_remarks} ({loc_str})" if full_remarks else loc_str

            if not full_remarks:
                remarks_html = "<font color='#94A3B8'>—</font>"
            elif len(full_remarks) > 75:
                note_num = len(appendix_notes) + 1
                appendix_notes.append({
                    'num': note_num,
                    'emp': f"{emp.full_name} ({emp.employee_id})",
                    'date': str(r.get('date', '')),
                    'text': full_remarks
                })
                snippet = full_remarks[:65].rstrip() + "..."
                remarks_html = f"{html.escape(snippet)} <font color='#2563EB'><b>[Note {note_num}]</b></font>"
            else:
                remarks_html = html.escape(full_remarks)

            row_cells = [
                Paragraph(str(idx), styles['td_center']),
                Paragraph(emp_display, styles['td_left']),
                Paragraph(html.escape(emp.employee_id or '—'), styles['td_center']),
                Paragraph(str(r.get('date', '')), styles['td_center']),
                Paragraph(in_str, styles['td_center']),
                Paragraph(out_str, styles['td_center']),
                Paragraph(hrs_str, styles['td_right']),
                Paragraph(html.escape(type_str), styles['td_center']),
                Paragraph(status_html, styles['td_center']),
                Paragraph(remarks_html, styles['td_left']),
            ]
            table_data.append(row_cells)

            # Alternating soft zebra rows
            row_bg = colors.HexColor('#FFFFFF') if idx % 2 != 0 else colors.HexColor('#F8FAFC')
            t_style.append(('BACKGROUND', (0, idx), (-1, idx), row_bg))

    register_table = Table(table_data, colWidths=col_widths, repeatRows=1)
    register_table.setStyle(TableStyle(t_style))
    story.append(register_table)

    # ── Numbered Appendix for Long Notes ─────────────────────────────────────
    if appendix_notes:
        story.append(Spacer(1, 14))
        story.append(Paragraph("<b>Notes & Remarks Appendix</b>", styles['meta_label']))
        story.append(Spacer(1, 4))

        app_col_w = [48, 170, avail_w - 218]
        app_data = [[
            Paragraph("<b>Ref</b>", styles['th']),
            Paragraph("<b>Employee & Date</b>", styles['th_left']),
            Paragraph("<b>Complete Note / Policy Remarks</b>", styles['th_left']),
        ]]
        for note in appendix_notes:
            app_data.append([
                Paragraph(f"<b>[Note {note['num']}]</b>", styles['td_center']),
                Paragraph(f"<b>{html.escape(note['emp'])}</b><br/><font color='#64748B'>{html.escape(note['date'])}</font>", styles['td_left']),
                Paragraph(html.escape(note['text']), styles['appendix']),
            ])

        app_table = Table(app_data, colWidths=app_col_w, repeatRows=1)
        app_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F1F5F9')),
            ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#CBD5E1')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ]))
        story.append(KeepTogether(app_table))

    doc.build(story, canvasmaker=NumberedCanvas)
    return buffer.getvalue()


# ── 6. MONTHLY ATTENDANCE SUMMARY & SPLIT MATRIX PDF EXPORT ──────────────────

def render_monthly_attendance_pdf(monthly_data, filters=None, user=None):
    """
    Renders Monthly Attendance Report in A4 Landscape.
    Includes:
    1. Executive Monthly Summary Table (SN | Employee+Dept | Present | Absent | Leave | Late | Field | Hours | OT | Att %)
    2. Split Day Matrix (Days 1–15 on Section A, Days 16–31 on Section B) with repeated headers and guaranteed readability.
    """
    filters = filters or {}
    year = monthly_data.get('year', timezone.localdate().year)
    month = monthly_data.get('month', timezone.localdate().month)
    all_days = monthly_data.get('all_days', [])
    rows = monthly_data.get('rows', [])

    styles = get_report_styles()
    buffer = io.BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=28,
        rightMargin=28,
        topMargin=26,
        bottomMargin=32,
    )
    avail_w = doc.width

    month_name = date(year, month, 1).strftime('%B %Y')
    gen_time_str = timezone.localtime(timezone.now(), BD_TZ).strftime('%d %b %Y, %I:%M:%S %p')

    story = []

    # ── Header ───────────────────────────────────────────────────────────────
    header_table_data = [
        [
            Paragraph(f"<b>FieldTrack</b> — Monthly Attendance Summary", styles['title']),
            Paragraph(f"<b>Timezone:</b> Asia/Dhaka (BST, UTC+6)<br/><b>Generated:</b> {gen_time_str}", styles['subtitle'])
        ],
        [
            Paragraph(
                f"<b>Month:</b> {month_name} &nbsp;|&nbsp; "
                f"<b>Branch:</b> {html.escape(str(filters.get('branch_name', 'All Branches')))} &nbsp;|&nbsp; "
                f"<b>Total Working Days:</b> {len(all_days)}",
                styles['subtitle']
            ),
            Paragraph(
                f"<b>Total Present:</b> {monthly_data.get('total_present', 0)} &nbsp;|&nbsp; "
                f"<b>Total Absent:</b> {monthly_data.get('total_absent', 0)} &nbsp;|&nbsp; "
                f"<b>Total Late:</b> {monthly_data.get('total_late', 0)} &nbsp;|&nbsp; "
                f"<b>Avg Att:</b> {monthly_data.get('avg_att_pct', 0)}%",
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
    # Columns: SN (26) | Emp+Dept (175) | ID (60) | Present (50) | Absent (50) | Leave (50) | Late (50) | Field (50) | Hours (58) | OT (58) | Att % (58)
    sum_widths = [26, 175, 60, 50, 50, 50, 50, 50, 58, 58, 58]
    assert sum(sum_widths) <= avail_w

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

    t_style = [
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F1F5F9')),
        ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#CBD5E1')),
        ('BOX', (0, 0), (-1, -1), 0.6, colors.HexColor('#94A3B8')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 3),
        ('RIGHTPADDING', (0, 0), (-1, -1), 3),
    ]

    for idx, r in enumerate(rows, 1):
        emp = r['employee']
        dept = get_employee_department_name(emp)
        emp_display = f"<b>{html.escape(emp.full_name)}</b>"
        if dept:
            emp_display += f"<br/><font color='#64748B' size='6.5'>{html.escape(dept)}</font>"

        p_cnt = str(r.get('present', 0))
        a_cnt = str(r.get('absent', 0))
        l_cnt = str(r.get('late', 0))
        ol_cnt = str(r.get('on_leave', 0))
        fv_cnt = str(r.get('field_visits', 0))
        tot_hrs = f"{r.get('total_hours', 0.0):.2f}h"
        ot_val = r.get('overtime', '—')
        att_pct = f"{r.get('att_pct', 0)}%"

        sum_table_data.append([
            Paragraph(str(idx), styles['td_center']),
            Paragraph(emp_display, styles['td_left']),
            Paragraph(html.escape(emp.employee_id or '—'), styles['td_center']),
            Paragraph(p_cnt, styles['td_center']),
            Paragraph(f"<font color='#DC2626'><b>{a_cnt}</b></font>" if int(a_cnt) > 0 else a_cnt, styles['td_center']),
            Paragraph(ol_cnt, styles['td_center']),
            Paragraph(f"<font color='#B45309'><b>{l_cnt}</b></font>" if int(l_cnt) > 0 else l_cnt, styles['td_center']),
            Paragraph(fv_cnt, styles['td_center']),
            Paragraph(tot_hrs, styles['td_right']),
            Paragraph(ot_val, styles['td_right']),
            Paragraph(att_pct, styles['td_right']),
        ])
        row_bg = colors.HexColor('#FFFFFF') if idx % 2 != 0 else colors.HexColor('#F8FAFC')
        t_style.append(('BACKGROUND', (0, idx), (-1, idx), row_bg))

    # Summary Footer Row
    tot_hrs_sum = f"{sum(r.get('total_hours', 0.0) for r in rows):.2f}h"
    footer_idx = len(sum_table_data)
    sum_table_data.append([
        Paragraph("", styles['td_center']),
        Paragraph("<b>Monthly Totals</b>", styles['td_left']),
        Paragraph("", styles['td_center']),
        Paragraph(f"<b>{monthly_data.get('total_present', 0)}</b>", styles['td_center']),
        Paragraph(f"<b>{monthly_data.get('total_absent', 0)}</b>", styles['td_center']),
        Paragraph(f"<b>{monthly_data.get('total_on_leave', 0)}</b>", styles['td_center']),
        Paragraph(f"<b>{monthly_data.get('total_late', 0)}</b>", styles['td_center']),
        Paragraph(f"<b>{monthly_data.get('total_field', 0)}</b>", styles['td_center']),
        Paragraph(f"<b>{tot_hrs_sum}</b>", styles['td_right']),
        Paragraph(f"<b>{monthly_data.get('total_overtime', '0h')}</b>", styles['td_right']),
        Paragraph(f"<b>{monthly_data.get('avg_att_pct', 0)}%</b>", styles['td_right']),
    ])
    t_style.append(('BACKGROUND', (0, footer_idx), (-1, footer_idx), colors.HexColor('#E2E8F0')))
    t_style.append(('LINEABOVE', (0, footer_idx), (-1, footer_idx), 1, colors.HexColor('#94A3B8')))

    sum_table = Table(sum_table_data, colWidths=sum_widths, repeatRows=1)
    sum_table.setStyle(TableStyle(t_style))
    story.append(sum_table)

    # ── 2. Split Daily Matrix (Handling 31-day Edge Case cleanly) ─────────────
    if all_days and len(all_days) > 0:
        story.append(PageBreak())
        story.append(Paragraph(f"<b>Monthly Attendance Daily Matrix (Days 1 to 15) — {month_name}</b>", styles['meta_label']))
        story.append(Spacer(1, 6))

        days_part1 = all_days[:15]
        part1_col_w = [24, 130] + [round((avail_w - 154) / len(days_part1), 2)] * len(days_part1)

        m_head1 = [
            Paragraph("<b>SN</b>", styles['th']),
            Paragraph("<b>Employee</b>", styles['th_left'])
        ] + [Paragraph(f"<b>{d.day}</b>", styles['th']) for d in days_part1]

        m_data1 = [m_head1]
        m_style1 = [
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F1F5F9')),
            ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#CBD5E1')),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 2),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            ('LEFTPADDING', (0, 0), (-1, -1), 1),
            ('RIGHTPADDING', (0, 0), (-1, -1), 1),
        ]

        for idx, r in enumerate(rows, 1):
            emp = r['employee']
            row_c = [
                Paragraph(str(idx), styles['td_center']),
                Paragraph(html.escape(emp.full_name), styles['td_left'])
            ]
            day_calcs = r.get('day_calcs', {})
            for d in days_part1:
                dc = day_calcs.get(d, {})
                st = dc.get('status', '').lower()
                m_st = dc.get('monthly_status', '—')
                if dc.get('is_late'):
                    cell_html = f"<font color='#B45309' size='6'><b>L</b></font>"
                elif dc.get('is_present'):
                    cell_html = f"<font color='#16A34A' size='6'><b>P</b></font>"
                elif dc.get('is_absent'):
                    cell_html = f"<font color='#DC2626' size='6'><b>A</b></font>"
                elif 'leave' in st:
                    cell_html = f"<font color='#2563EB' size='6'><b>LV</b></font>"
                elif 'holiday' in st or 'weekend' in st:
                    cell_html = f"<font color='#94A3B8' size='6'>H</font>"
                else:
                    cell_html = f"<font color='#CBD5E1' size='6'>—</font>"
                row_c.append(Paragraph(cell_html, styles['td_center']))

            m_data1.append(row_c)
            row_bg = colors.HexColor('#FFFFFF') if idx % 2 != 0 else colors.HexColor('#F8FAFC')
            m_style1.append(('BACKGROUND', (0, idx), (-1, idx), row_bg))

        matrix_table1 = Table(m_data1, colWidths=part1_col_w, repeatRows=1)
        matrix_table1.setStyle(TableStyle(m_style1))
        story.append(matrix_table1)

        # Part 2: Days 16 to End
        if len(all_days) > 15:
            story.append(PageBreak())
            days_part2 = all_days[15:]
            story.append(Paragraph(f"<b>Monthly Attendance Daily Matrix (Days 16 to {len(all_days)}) & Summary — {month_name}</b>", styles['meta_label']))
            story.append(Spacer(1, 6))

            # Part 2 includes days 16..end plus P, A, L, Tot Hrs, Att %
            summary_cols_w = [26, 26, 26, 38, 38]
            days_part2_w = round((avail_w - 154 - sum(summary_cols_w)) / len(days_part2), 2)
            part2_col_w = [24, 130] + [days_part2_w] * len(days_part2) + summary_cols_w

            m_head2 = [
                Paragraph("<b>SN</b>", styles['th']),
                Paragraph("<b>Employee</b>", styles['th_left'])
            ] + [Paragraph(f"<b>{d.day}</b>", styles['th']) for d in days_part2] + [
                Paragraph("<b>P</b>", styles['th']),
                Paragraph("<b>A</b>", styles['th']),
                Paragraph("<b>L</b>", styles['th']),
                Paragraph("<b>Hours</b>", styles['th']),
                Paragraph("<b>Att%</b>", styles['th']),
            ]

            m_data2 = [m_head2]
            m_style2 = [
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F1F5F9')),
                ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#CBD5E1')),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('TOPPADDING', (0, 0), (-1, -1), 2),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
                ('LEFTPADDING', (0, 0), (-1, -1), 1),
                ('RIGHTPADDING', (0, 0), (-1, -1), 1),
            ]

            for idx, r in enumerate(rows, 1):
                emp = r['employee']
                row_c = [
                    Paragraph(str(idx), styles['td_center']),
                    Paragraph(html.escape(emp.full_name), styles['td_left'])
                ]
                day_calcs = r.get('day_calcs', {})
                for d in days_part2:
                    dc = day_calcs.get(d, {})
                    st = dc.get('status', '').lower()
                    if dc.get('is_late'):
                        cell_html = f"<font color='#B45309' size='6'><b>L</b></font>"
                    elif dc.get('is_present'):
                        cell_html = f"<font color='#16A34A' size='6'><b>P</b></font>"
                    elif dc.get('is_absent'):
                        cell_html = f"<font color='#DC2626' size='6'><b>A</b></font>"
                    elif 'leave' in st:
                        cell_html = f"<font color='#2563EB' size='6'><b>LV</b></font>"
                    elif 'holiday' in st or 'weekend' in st:
                        cell_html = f"<font color='#94A3B8' size='6'>H</font>"
                    else:
                        cell_html = f"<font color='#CBD5E1' size='6'>—</font>"
                    row_c.append(Paragraph(cell_html, styles['td_center']))

                # Add summary numbers
                row_c.append(Paragraph(str(r.get('present', 0)), styles['td_center']))
                row_c.append(Paragraph(str(r.get('absent', 0)), styles['td_center']))
                row_c.append(Paragraph(str(r.get('late', 0)), styles['td_center']))
                row_c.append(Paragraph(f"{r.get('total_hours', 0.0):.1f}h", styles['td_right']))
                row_c.append(Paragraph(f"{r.get('att_pct', 0)}%", styles['td_right']))

                m_data2.append(row_c)
                row_bg = colors.HexColor('#FFFFFF') if idx % 2 != 0 else colors.HexColor('#F8FAFC')
                m_style2.append(('BACKGROUND', (0, idx), (-1, idx), row_bg))

            matrix_table2 = Table(m_data2, colWidths=part2_col_w, repeatRows=1)
            matrix_table2.setStyle(TableStyle(m_style2))
            story.append(matrix_table2)

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

    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=28,
        rightMargin=28,
        topMargin=26,
        bottomMargin=32,
    )
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
        f"<b>Attendance:</b> {emp_row.get('att_pct', 0)}%",
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
        co_times = [s.check_out_time for s in cis if s and s.check_out_time]
        earliest_ci = min(ci_times) if ci_times else None
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
    if appendix_notes:
        story.append(Spacer(1, 14))
        story.append(Paragraph("<b>Notes & Remarks Appendix</b>", styles['meta_label']))
        story.append(Spacer(1, 4))

        app_col_w = [48, 120, avail_w - 168]
        app_data = [[
            Paragraph("<b>Ref</b>", styles['th']),
            Paragraph("<b>Date</b>", styles['th_left']),
            Paragraph("<b>Complete Note / Policy Remarks</b>", styles['th_left']),
        ]]
        for note in appendix_notes:
            app_data.append([
                Paragraph(f"<b>[Note {note['num']}]</b>", styles['td_center']),
                Paragraph(html.escape(note['date']), styles['td_left']),
                Paragraph(html.escape(note['text']), styles['appendix']),
            ])

        app_table = Table(app_data, colWidths=app_col_w, repeatRows=1)
        app_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F1F5F9')),
            ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#CBD5E1')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ]))
        story.append(KeepTogether(app_table))

    doc.build(story, canvasmaker=NumberedCanvas)
    return buffer.getvalue()
