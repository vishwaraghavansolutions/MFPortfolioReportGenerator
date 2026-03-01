"""
mf_portfolio_pdf_generator.py
------------------------------
WinRich Professional Services — Portfolio Performance Report Generator

Accepts portfolio_data dict with these keys:
    company_name        : str  (optional)
    client_name         : str
    report_date         : str
    prepared_by         : str  (optional)
    summary             : dict  {label: value, ...}
    client_allocation   : dict  {Equity: float, Hybrid: float, Debt: float}
    model_allocation    : dict  {Equity: float, Hybrid: float, Debt: float}
    equity_funds        : list of dicts
                            name, xirr, benchmark_index,
                            benchmark_return_1m, benchmark_return_3m,
                            benchmark_return_1yr, benchmark_return_3yr,
                            benchmark_return_5yr
    hybrid_funds        : list of dicts  {name, xirr}
    debt_funds          : list of dicts  {name, xirr}  (optional)
    amc_concentration   : dict  {amc_name: fund_count}
    quarter_labels      : list of str   (optional — skips QoQ if absent)
    quarterly_returns   : list of dicts
                            name, is_benchmark, returns {q0..qN, ttm}
    blended_return      : dict  {q0..qN, ttm}
    commentary          : list of dicts  {heading, body}  (optional)
    disclaimer          : str  (optional)
"""

from __future__ import annotations

import math
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable, PageBreak, Paragraph, SimpleDocTemplate,
    Spacer, Table, TableStyle,
)
from reportlab.platypus.flowables import Flowable


# ── Brand palette ─────────────────────────────────────────────────────────────
NAVY       = colors.HexColor('#1a2a5e')
MID_BLUE   = colors.HexColor('#2e4899')
ALT_ROW    = colors.HexColor('#f2f5fb')
BENCH_BG   = colors.HexColor('#e8edf7')
WHITE      = colors.white
GREY_TEXT  = colors.HexColor('#555555')
GREEN      = colors.HexColor('#1a7a1a')
RED        = colors.HexColor('#cc0000')
RULE_COLOR = colors.HexColor('#c0cce8')


# ── Paragraph styles ──────────────────────────────────────────────────────────
def _mk(name, **kw):
    return ParagraphStyle(name, **kw)


S = {
    'th':         _mk('th',         fontName='Helvetica-Bold', fontSize=8,
                                    textColor=WHITE, leading=10, alignment=TA_CENTER),
    'th_left':    _mk('th_left',    fontName='Helvetica-Bold', fontSize=8,
                                    textColor=WHITE, leading=10, alignment=TA_LEFT),
    'cell':       _mk('cell',       fontName='Helvetica',       fontSize=7.5, leading=10),
    'cell_c':     _mk('cell_c',     fontName='Helvetica',       fontSize=7.5,
                                    leading=10, alignment=TA_CENTER),
    'cell_b':     _mk('cell_b',     fontName='Helvetica-Bold',  fontSize=7.5, leading=10),
    'cell_bc':    _mk('cell_bc',    fontName='Helvetica-Bold',  fontSize=7.5,
                                    leading=10, alignment=TA_CENTER),
    'cell_sm':    _mk('cell_sm',    fontName='Helvetica',       fontSize=7,
                                    leading=9,  textColor=GREY_TEXT),
    'cell_sm_c':  _mk('cell_sm_c',  fontName='Helvetica',       fontSize=7,
                                    leading=9,  textColor=GREY_TEXT, alignment=TA_CENTER),
    'cell_it':    _mk('cell_it',    fontName='Helvetica-Oblique', fontSize=7.5,
                                    leading=10, textColor=GREY_TEXT),
    'cell_it_c':  _mk('cell_it_c',  fontName='Helvetica-Oblique', fontSize=7.5,
                                    leading=10, textColor=GREY_TEXT, alignment=TA_CENTER),
    'section':    _mk('section',    fontName='Helvetica-Bold',  fontSize=11,
                                    textColor=NAVY, spaceBefore=14, spaceAfter=5, leading=14),
    'footnote':   _mk('footnote',   fontName='Helvetica-Oblique', fontSize=7,
                                    textColor=GREY_TEXT, leading=9, spaceAfter=4),
    'disclaimer': _mk('disclaimer', fontName='Helvetica-Oblique', fontSize=7.5,
                                    textColor=GREY_TEXT, leading=11, alignment=TA_JUSTIFY),
    'comment_h':  _mk('comment_h',  fontName='Helvetica-Bold',  fontSize=9.5,
                                    textColor=NAVY, spaceBefore=8, spaceAfter=3, leading=12),
    'comment_b':  _mk('comment_b',  fontName='Helvetica',       fontSize=8.5,
                                    textColor=colors.HexColor('#222222'),
                                    leading=13, spaceAfter=4, alignment=TA_JUSTIFY),
}


# ── Paragraph helpers ─────────────────────────────────────────────────────────
def _p(text: str, style: str = 'cell') -> Paragraph:
    return Paragraph(str(text), S[style])


def _ret(val, bold: bool = False, color_it: bool = True) -> Paragraph:
    """Numeric % → coloured Paragraph. None → '—'."""
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return _p('—', 'cell_sm_c')
    v    = float(val)
    sign = '+' if v >= 0 else ''
    text = f"{sign}{v:.2f}%"
    if color_it:
        col    = GREEN if v >= 0 else RED
        markup = (f"<font color='{col.hexval()}'><b>{text}</b></font>" if bold
                  else f"<font color='{col.hexval()}'>{text}</font>")
    else:
        markup = f"<b>{text}</b>" if bold else text
    return Paragraph(markup, S['cell_c'])


def _xirr_cell(val) -> Paragraph:
    """XIRR value → bold coloured cell."""
    if val is None:
        return _p('—', 'cell_sm_c')
    v   = float(val)
    col = GREEN if v >= 0 else RED
    return Paragraph(
        f"<font color='{col.hexval()}'><b>{v:.2f}%</b></font>",
        S['cell_bc'])


# ── Base table style ──────────────────────────────────────────────────────────
def _base_ts() -> TableStyle:
    return TableStyle([
        ('BACKGROUND',    (0, 0), (-1, 0),  NAVY),
        ('TEXTCOLOR',     (0, 0), (-1, 0),  WHITE),
        ('FONTNAME',      (0, 0), (-1, 0),  'Helvetica-Bold'),
        ('FONTSIZE',      (0, 0), (-1, -1), 8),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID',          (0, 0), (-1, -1), 0.4, RULE_COLOR),
        ('TOPPADDING',    (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING',   (0, 0), (-1, -1), 5),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 5),
    ])


def _alt_rows(n: int, start: int = 1) -> list:
    return [('BACKGROUND', (0, i), (-1, i), ALT_ROW if i % 2 == 1 else WHITE)
            for i in range(start, n)]


# ── Header banner ─────────────────────────────────────────────────────────────
class HeaderBanner(Flowable):
    H = 1.05 * inch

    def __init__(self, page_w: float, company: str, meta_lines: list[str]):
        super().__init__()
        self.width      = page_w
        self.company    = company
        self.meta_lines = meta_lines
        self.height     = self.H

    def draw(self):
        c = self.canv
        c.setFillColor(NAVY)
        c.rect(0, 0, self.width, self.height, fill=1, stroke=0)
        c.setFillColor(MID_BLUE)
        c.rect(0, 0, self.width, 4, fill=1, stroke=0)

        c.setFont('Helvetica-Bold', 9)
        c.setFillColor(colors.HexColor('#a0b8e8'))
        c.drawString(12, self.height - 16, self.company)

        c.setFont('Helvetica-Bold', 17)
        c.setFillColor(WHITE)
        c.drawString(12, self.height - 38, "Portfolio Performance Report")

        c.setFont('Helvetica', 8)
        c.setFillColor(colors.HexColor('#ccd6f0'))
        y = self.height - 54
        for line in self.meta_lines:
            c.drawString(12, y, line)
            y -= 13


# ── Page footer ───────────────────────────────────────────────────────────────
def _draw_footer(canvas, doc, company: str):
    w, _ = letter
    canvas.saveState()
    canvas.setFont('Helvetica', 7)
    canvas.setFillColor(GREY_TEXT)
    canvas.drawString(0.75 * inch, 0.42 * inch,
                      f"{company} — Portfolio Performance Report")
    canvas.drawRightString(w - 0.75 * inch, 0.42 * inch, f"Page {doc.page}")
    canvas.setStrokeColor(RULE_COLOR)
    canvas.setLineWidth(0.5)
    canvas.line(0.75 * inch, 0.52 * inch, w - 0.75 * inch, 0.52 * inch)
    canvas.restoreState()


# ══════════════════════════════════════════════════════════════════════════════
class MFPortfolioPDFGenerator:

    def __init__(self, company_name: str = "WinRich Professional Services"):
        self.company_name = company_name

    # ── Section: Client Summary ───────────────────────────────────────────────
    def _section_summary(self, summary: dict) -> list:
        story = [_p("Client &amp; Portfolio Summary", 'section')]

        items = list(summary.items())
        mid   = math.ceil(len(items) / 2)
        left, right = items[:mid], items[mid:]

        rows = []
        for i in range(max(len(left), len(right))):
            lk, lv = left[i]  if i < len(left)  else ('', '')
            rk, rv = right[i] if i < len(right) else ('', '')
            rows.append([
                _p(str(lk), 'cell_b'), _p(str(lv), 'cell'),
                _p(str(rk), 'cell_b'), _p(str(rv), 'cell'),
            ])

        t  = Table(rows, colWidths=[1.6*inch, 2.15*inch, 1.6*inch, 2.15*inch])
        ts = TableStyle([
            ('GRID',          (0, 0), (-1, -1), 0.4, RULE_COLOR),
            ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING',    (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('LEFTPADDING',   (0, 0), (-1, -1), 6),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 6),
            ('LINEAFTER',     (1, 0), (1, -1),  1.0, MID_BLUE),
        ])
        for i in range(len(rows)):
            ts.add('BACKGROUND', (0, i), (-1, i), ALT_ROW if i % 2 == 0 else WHITE)
        t.setStyle(ts)
        story.append(t)
        return story

    # ── Section 1: Portfolio Allocation Snapshot ──────────────────────────────
    def _section_allocation(self, client_alloc: dict, model_alloc: dict) -> list:
        story = [_p("1. Portfolio Allocation Snapshot", 'section')]

        fund_type_map = {
            'Equity': 'Flexi Cap / Large Cap / Mid Cap / ELSS',
            'Hybrid': 'Balanced Advantage / Multi-Asset / BAF',
            'Debt':   'Debt / Liquid / Short Duration / Gilt',
        }

        rows = [[
            _p("Asset Class",    'th_left'),
            _p("Client %",       'th'),
            _p("Model %",        'th'),
            _p("Variance",       'th'),
            _p("Fund Type",      'th_left'),
        ]]

        for ac in client_alloc:
            cv   = float(client_alloc.get(ac, 0))
            mv   = float(model_alloc.get(ac, 0))
            diff = cv - mv
            sign = '+' if diff >= 0 else ''
            diff_col = (GREY_TEXT if abs(diff) < 0.01
                        else GREEN if diff > 0 else RED)
            rows.append([
                _p(ac, 'cell_b'),
                _p(f"{cv:.2f}%",         'cell_bc'),
                _p(f"{mv:.2f}%",         'cell_bc'),
                Paragraph(
                    f"<font color='{diff_col.hexval()}'>{sign}{diff:.2f}%</font>",
                    S['cell_bc']),
                _p(fund_type_map.get(ac, '—'), 'cell_sm'),
            ])

        t  = Table(rows, colWidths=[1.2*inch, 1.0*inch, 1.0*inch, 0.9*inch, 3.4*inch])
        ts = _base_ts()
        ts.add('ALIGN', (0, 0), (0, -1), 'LEFT')
        ts.add('ALIGN', (4, 0), (4, -1), 'LEFT')
        for cmd in _alt_rows(len(rows)):
            ts.add(*cmd)
        t.setStyle(ts)
        story.append(t)
        return story

    # ── Section 2: Equity Fund Performance ───────────────────────────────────
    def _section_equity(self, equity_funds: list) -> list:
        story = [_p("2. Equity Fund Performance vs Benchmark", 'section')]

        rows = [[
            _p("Fund Name",       'th_left'),
            _p("Benchmark Index", 'th_left'),
            _p("XIRR",            'th'),
            _p("Bench 1M",        'th'),
            _p("Bench 3M",        'th'),
            _p("Bench 1Y",        'th'),
            _p("Bench 3Y",        'th'),
            _p("Bench 5Y",        'th'),
        ]]

        for f in equity_funds:
            # Clean up long fund names — strip common suffixes
            name = f.get('name', '—')
            for suffix in [
                ' (Erstwhile Kotak Standard Multicap Fund - Gr)',
                ' (Erstwhile Kotak Emerging Equity Scheme)',
                ' - Regular Plan - Growth',
                ' - Regular Growth',
                ' - Regular Plan',
                ' Regular Growth',
                ' - Growth',
            ]:
                name = name.replace(suffix, '')

            rows.append([
                _p(name.strip(), 'cell_b'),
                _p(f.get('benchmark_index') or '—', 'cell_sm'),
                _xirr_cell(f.get('xirr')),
                _ret(f.get('benchmark_return_1m'),  color_it=False),
                _ret(f.get('benchmark_return_3m'),  color_it=False),
                _ret(f.get('benchmark_return_1yr'), color_it=True),
                _ret(f.get('benchmark_return_3yr'), color_it=True),
                _ret(f.get('benchmark_return_5yr'), color_it=True),
            ])

        col_widths = [2.3*inch, 1.25*inch, 0.65*inch,
                      0.55*inch, 0.55*inch, 0.55*inch, 0.55*inch, 0.55*inch]
        t  = Table(rows, colWidths=col_widths, repeatRows=1)
        ts = _base_ts()
        ts.add('ALIGN',      (0, 0), (1, -1), 'LEFT')
        ts.add('BACKGROUND', (2, 1), (2, -1), colors.HexColor('#eef2fb'))
        for cmd in _alt_rows(len(rows)):
            ts.add(*cmd)
        t.setStyle(ts)
        story.append(t)
        story.append(_p(
            "XIRR = annualised return since first investment. "
            "Benchmark columns show point-to-point returns for the given period. "
            "1Y, 3Y, 5Y are CAGR. 1M and 3M are absolute. "
            "Past performance is not indicative of future returns.",
            'footnote'))
        return story

    # ── Section 3: Hybrid Fund Performance ───────────────────────────────────
    def _section_hybrid(self, hybrid_funds: list) -> list:
        story = [_p("3. Hybrid Fund Performance", 'section')]

        rows = [[
            _p("Fund Name", 'th_left'),
            _p("XIRR",      'th'),
        ]]
        for f in hybrid_funds:
            rows.append([
                _p(f.get('name', '—'), 'cell_b'),
                _xirr_cell(f.get('xirr')),
            ])

        t  = Table(rows, colWidths=[5.9*inch, 0.9*inch], repeatRows=1)
        ts = _base_ts()
        ts.add('ALIGN', (0, 0), (0, -1), 'LEFT')
        for cmd in _alt_rows(len(rows)):
            ts.add(*cmd)
        t.setStyle(ts)
        story.append(t)
        story.append(_p(
            "XIRR = Extended Internal Rate of Return (since inception).",
            'footnote'))
        return story

    # ── Section 4: Debt Fund Performance (optional) ───────────────────────────
    def _section_debt(self, debt_funds: list) -> list:
        story = [_p("4. Debt Fund Performance", 'section')]

        rows = [[
            _p("Fund Name", 'th_left'),
            _p("XIRR",      'th'),
        ]]
        for f in debt_funds:
            rows.append([
                _p(f.get('name', '—'), 'cell_b'),
                _xirr_cell(f.get('xirr')),
            ])

        t  = Table(rows, colWidths=[5.9*inch, 0.9*inch], repeatRows=1)
        ts = _base_ts()
        ts.add('ALIGN', (0, 0), (0, -1), 'LEFT')
        for cmd in _alt_rows(len(rows)):
            ts.add(*cmd)
        t.setStyle(ts)
        story.append(t)
        return story

    # ── Section 5: AMC Concentration ─────────────────────────────────────────
    def _section_amc(self, amc_data: dict) -> list:
        story = [_p("5. AMC Concentration", 'section')]

        total      = sum(amc_data.values())
        sorted_amc = sorted(amc_data.items(), key=lambda x: x[1], reverse=True)

        rows = [[
            _p("AMC Name",        'th_left'),
            _p("No. of Funds",    'th'),
            _p("% of Portfolio",  'th'),
        ]]
        for amc, count in sorted_amc:
            pct = (count / total * 100) if total > 0 else 0
            rows.append([
                _p(amc, 'cell'),
                _p(str(count),    'cell_bc'),
                _p(f"{pct:.1f}%", 'cell_bc'),
            ])
        # Total row
        rows.append([
            _p("Total", 'cell_b'),
            _p(str(total), 'cell_bc'),
            _p("100.0%",   'cell_bc'),
        ])

        t  = Table(rows, colWidths=[4.6*inch, 1.1*inch, 1.1*inch], repeatRows=1)
        ts = _base_ts()
        ts.add('ALIGN', (0, 0), (0, -1), 'LEFT')
        for cmd in _alt_rows(len(rows) - 1):
            ts.add(*cmd)
        last = len(rows) - 1
        ts.add('BACKGROUND', (0, last), (-1, last), MID_BLUE)
        ts.add('TEXTCOLOR',  (0, last), (-1, last), WHITE)
        ts.add('FONTNAME',   (0, last), (-1, last), 'Helvetica-Bold')
        ts.add('LINEABOVE',  (0, last), (-1, last), 1.0, NAVY)
        t.setStyle(ts)
        story.append(t)
        return story

    # ── Section 6: QoQ Fund-Level Returns ────────────────────────────────────
    def _section_qoq_fund(self, quarterly_rows: list,
                           q_labels: list, footnote: str = None) -> list:
        story = [_p("6. Quarterly Returns — Fund-Level (QoQ)", 'section')]

        n_q    = len(q_labels)
        q_keys = [f'q{i}' for i in range(n_q)]

        header = [_p("Fund Name", 'th_left')]
        for ql in q_labels:
            header.append(_p(ql.replace('\n', ' '), 'th'))
        header.append(_p("TTM", 'th'))

        rows = [header]
        for entry in quarterly_rows:
            if entry.get('is_benchmark'):
                continue
            r = entry.get('returns', {})
            row = [_p(entry.get('name', '—'), 'cell_b')]
            for qk in q_keys:
                row.append(_ret(r.get(qk)))
            row.append(_ret(r.get('ttm'), bold=True))
            rows.append(row)

        col_widths = [2.6*inch] + [0.72*inch] * n_q + [0.75*inch]
        t  = Table(rows, colWidths=col_widths, repeatRows=1)
        ts = _base_ts()
        ts.add('ALIGN', (0, 0), (0, -1), 'LEFT')
        for cmd in _alt_rows(len(rows)):
            ts.add(*cmd)
        t.setStyle(ts)
        story.append(t)

        fn = footnote or ("Returns are annualised XIRR for the quarter window. "
                          "TTM = since-inception XIRR as of latest quarter.")
        story.append(_p(fn, 'footnote'))
        return story

    # ── Section 7: QoQ Portfolio-Level Returns ────────────────────────────────
    def _section_qoq_portfolio(self, quarterly_rows: list, q_labels: list,
                                blended_return: dict, footnote: str = None) -> list:
        story = [_p("7. QoQ Portfolio-Level Returns", 'section')]

        n_q    = len(q_labels)
        q_keys = [f'q{i}' for i in range(n_q)]

        header = [_p("Fund / Benchmark", 'th_left')]
        for ql in q_labels:
            header.append(_p(ql.replace('\n', ' '), 'th'))
        header.append(_p("TTM", 'th'))

        rows = [header]
        for entry in quarterly_rows:
            r        = entry.get('returns', {})
            is_bench = entry.get('is_benchmark', False)
            name_p   = _p(entry.get('name', '—'), 'cell_it' if is_bench else 'cell_b')
            row      = [name_p]
            for qk in q_keys:
                row.append(_ret(r.get(qk), color_it=not is_bench))
            row.append(_ret(r.get('ttm'), bold=not is_bench, color_it=not is_bench))
            rows.append(row)

        # Blended total row
        if blended_return:
            blend = [_p("Overall Portfolio (Blended)", 'cell_bc')]
            for qk in q_keys:
                blend.append(_ret(blended_return.get(qk), bold=True))
            blend.append(_ret(blended_return.get('ttm'), bold=True))
            rows.append(blend)

        col_widths = [2.6*inch] + [0.72*inch] * n_q + [0.75*inch]
        t  = Table(rows, colWidths=col_widths, repeatRows=1)
        ts = _base_ts()
        ts.add('ALIGN', (0, 0), (0, -1), 'LEFT')

        for i, entry in enumerate(quarterly_rows, start=1):
            if entry.get('is_benchmark'):
                ts.add('BACKGROUND', (0, i), (-1, i), BENCH_BG)
            elif i % 2 == 0:
                ts.add('BACKGROUND', (0, i), (-1, i), ALT_ROW)

        if blended_return:
            last = len(rows) - 1
            ts.add('BACKGROUND', (0, last), (-1, last), NAVY)
            ts.add('TEXTCOLOR',  (0, last), (-1, last), WHITE)
            ts.add('FONTNAME',   (0, last), (-1, last), 'Helvetica-Bold')
            ts.add('LINEABOVE',  (0, last), (-1, last), 1.5, MID_BLUE)

        t.setStyle(ts)
        story.append(t)

        fn = footnote or ("Benchmark rows shown in tinted rows directly below each fund. "
                          "Blended return = value-weighted average across all funds.")
        story.append(_p(fn, 'footnote'))
        return story

    # ── Page callbacks ────────────────────────────────────────────────────────
    def _page_cb(self, canvas, doc):
        _draw_footer(canvas, doc, self.company_name)

    # ── Main entry point ──────────────────────────────────────────────────────
    def generate_report(self, portfolio_data: dict, output_file: str) -> str:
        """
        Build the full PDF report and write to output_file.
        Returns output_file path.
        """
        d = portfolio_data
        self.company_name = d.get('company_name', self.company_name)

        doc = SimpleDocTemplate(
            output_file, pagesize=letter,
            topMargin=0.6 * inch, bottomMargin=0.75 * inch,
            leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        )

        page_w = letter[0] - 1.5 * inch
        story  = []

        # ── Banner ────────────────────────────────────────────────────────────
        client = d.get('client_name', '—')
        rdate  = d.get('report_date', datetime.now().strftime('%B %d, %Y'))
        meta1  = f"Client: {client}   |   Report Date: {rdate}"
        meta2  = f"Prepared by: {d.get('prepared_by', self.company_name)}"
        story.append(HeaderBanner(page_w, self.company_name, [meta1, meta2]))
        story.append(Spacer(1, 0.15 * inch))

        # ── Client Summary ────────────────────────────────────────────────────
        if d.get('summary'):
            story.extend(self._section_summary(d['summary']))
            story.append(Spacer(1, 0.12 * inch))

        # ── 1. Allocation Snapshot ────────────────────────────────────────────
        client_alloc = d.get('client_allocation', {})
        model_alloc  = d.get('model_allocation',  {})
        if client_alloc and model_alloc:
            story.extend(self._section_allocation(client_alloc, model_alloc))
            story.append(Spacer(1, 0.12 * inch))

        # ── 2. Equity Funds ───────────────────────────────────────────────────
        if d.get('equity_funds'):
            story.extend(self._section_equity(d['equity_funds']))
            story.append(Spacer(1, 0.12 * inch))

        # ── 3. Hybrid Funds ───────────────────────────────────────────────────
        if d.get('hybrid_funds'):
            story.extend(self._section_hybrid(d['hybrid_funds']))
            story.append(Spacer(1, 0.12 * inch))

        # ── 4. Debt Funds (optional) ──────────────────────────────────────────
        if d.get('debt_funds'):
            story.extend(self._section_debt(d['debt_funds']))
            story.append(Spacer(1, 0.12 * inch))

        # ── 5. AMC Concentration ──────────────────────────────────────────────
        if d.get('amc_concentration'):
            story.extend(self._section_amc(d['amc_concentration']))
            story.append(Spacer(1, 0.12 * inch))

        # ── QoQ sections — only if quarter data is present ───────────────────
        q_labels        = d.get('quarter_labels',    [])
        quarterly_rows  = d.get('quarterly_returns', [])
        blended_return  = d.get('blended_return',    {})

        if quarterly_rows and q_labels:
            story.append(PageBreak())

            # ── 6. QoQ Fund-Level ─────────────────────────────────────────────
            story.extend(self._section_qoq_fund(quarterly_rows, q_labels))
            story.append(Spacer(1, 0.12 * inch))

            # ── 7. QoQ Portfolio-Level ────────────────────────────────────────
            if blended_return:
                story.extend(self._section_qoq_portfolio(
                    quarterly_rows, q_labels, blended_return))
                story.append(Spacer(1, 0.12 * inch))

        # ── Commentary (optional) ─────────────────────────────────────────────
        if d.get('commentary'):
            story.append(PageBreak())
            story.append(_p("Performance Commentary", 'section'))
            story.append(HRFlowable(width='100%', thickness=1,
                                    color=RULE_COLOR, spaceAfter=6))
            for block in d['commentary']:
                story.append(_p(block.get('heading', ''), 'comment_h'))
                story.append(_p(block.get('body', ''),    'comment_b'))

        # ── Disclaimer ────────────────────────────────────────────────────────
        story.append(Spacer(1, 0.15 * inch))
        story.append(HRFlowable(width='100%', thickness=0.5,
                                color=RULE_COLOR, spaceAfter=4))
        disclaimer = d.get(
            'disclaimer',
            "This report is prepared by WinRich Professional Services for informational "
            "purposes only and does not constitute investment advice. Mutual fund investments "
            "are subject to market risks. Past performance is not indicative of future returns. "
            "Please read all scheme-related documents carefully before investing. "
            "Data sourced from fund houses and AMFI.",
        )
        story.append(Paragraph(f"<b>Disclaimer:</b> {disclaimer}", S['disclaimer']))

        doc.build(story, onFirstPage=self._page_cb, onLaterPages=self._page_cb)
        return output_file


# ══════════════════════════════════════════════════════════════════════════════
# Standalone test
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import numpy as np

    sample = {
        'company_name': 'WinRich Professional Services',
        'client_name':  'A Balamurugan',
        'report_date':  'February 26, 2026',
        'prepared_by':  'WinRich Research Desk',

        'summary': {
            'Client Name':           'A Balamurugan',
            'Email':                 'B********@GMAIL.COM',
            'Mobile':                '******9371',
            'Report Date':           'February 26, 2026',
            'Total Portfolio Value': '₹73,93,846.65',
            'Total Funds':           '13',
            'Equity Allocation':     '54.33%',
            'Hybrid Allocation':     '34.27%',
            'Debt Allocation':       '11.40%',
            'Number of AMCs':        '7',
        },

        'client_allocation': {
            'Equity': np.float64(54.33),
            'Hybrid': np.float64(34.27),
            'Debt':   np.float64(11.40),
        },
        'model_allocation': {
            'Equity': np.float64(65.0),
            'Hybrid': np.float64(20.0),
            'Debt':   np.float64(15.0),
        },

        'equity_funds': [
            {'name': 'Canara Robeco Large Cap Fund - Regular Growth',
             'xirr': 13.09, 'benchmark_index': 'Nifty 50',
             'benchmark_return_1m': -3.04, 'benchmark_return_3m': -1.45,
             'benchmark_return_1yr': 8.97, 'benchmark_return_3yr': 14.08,
             'benchmark_return_5yr': 14.54},
            {'name': 'HDFC Large and Mid Cap Fund - Regular Plan - Growth',
             'xirr': 15.14, 'benchmark_index': 'Nifty LargeMidcap 250',
             'benchmark_return_1m': -3.2,  'benchmark_return_3m': -2.04,
             'benchmark_return_1yr': 8.98, 'benchmark_return_3yr': 19.34,
             'benchmark_return_5yr': 19.01},
            {'name': 'Kotak Flexicap Fund - Growth (Regular Plan) (Erstwhile Kotak Standard Multicap Fund - Gr)',
             'xirr': 17.07, 'benchmark_index': 'Nifty 500',
             'benchmark_return_1m': -3.27, 'benchmark_return_3m': -2.56,
             'benchmark_return_1yr': 7.98, 'benchmark_return_3yr': 16.72,
             'benchmark_return_5yr': 16.54},
            {'name': 'Mirae Asset Large Cap Fund - Regular Plan',
             'xirr': 14.17, 'benchmark_index': 'Nifty 50',
             'benchmark_return_1m': -3.04, 'benchmark_return_3m': -1.45,
             'benchmark_return_1yr': 8.97, 'benchmark_return_3yr': 14.08,
             'benchmark_return_5yr': 14.54},
        ],

        'hybrid_funds': [
            {'name': 'Edelweiss Balanced Advantage Fund - Regular Growth', 'xirr': 11.25},
            {'name': 'ICICI Prudential Balanced Advantage Fund - Growth',   'xirr': 12.50},
            {'name': 'ICICI Prudential Multi-Asset Fund - Growth',          'xirr': 16.78},
        ],

        'amc_concentration': {
            'Canara Robeco Asset Management Company':    1,
            'Edelweiss Asset Management':                1,
            'HDFC Asset Management Company':             1,
            'ICICI Prudential Asset Management Company': 3,
            'Kotak Mahindra Asset Management Company':   2,
            'Mirae Asset Investment Managers (India)':   1,
        },

        'quarter_labels': [
            "Q1 FY25 (Apr-Jun '24)",
            "Q2 FY25 (Jul-Sep '24)",
            "Q3 FY25 (Oct-Dec '24)",
            "Q4 FY25 (Jan-Mar '25)",
        ],

        'quarterly_returns': [
            {'name': 'Canara Robeco Large Cap Fund', 'is_benchmark': False,
             'returns': {'q0': 6.1, 'q1': 5.3, 'q2': 4.8, 'q3': 3.2, 'ttm': 13.09}},
            {'name': 'Kotak Flexicap Fund',          'is_benchmark': False,
             'returns': {'q0': 7.2, 'q1': 6.1, 'q2': 5.9, 'q3': 4.1, 'ttm': 17.07}},
            {'name': 'Mirae Asset Large Cap Fund',   'is_benchmark': False,
             'returns': {'q0': 5.9, 'q1': 5.1, 'q2': 4.6, 'q3': 3.8, 'ttm': 14.17}},
            {'name': 'Edelweiss BAF',                'is_benchmark': False,
             'returns': {'q0': 4.2, 'q1': 3.8, 'q2': 3.5, 'q3': 2.9, 'ttm': 11.25}},
        ],

        'blended_return': {'q0': 5.9, 'q1': 5.1, 'q2': 4.7, 'q3': 3.5, 'ttm': 14.2},
    }

    out = MFPortfolioPDFGenerator().generate_report(
        sample, "/mnt/user-data/outputs/winrich_final.pdf")
    print(f"Generated: {out}")