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
    portfolio_trend     : list of dicts  — one per quarter, oldest first
                            { label: str, invested: float, current: float }
                            e.g. [{'label':'Q3 FY24','invested':500000,'current':580000}, ...]
    quarter_labels      : list of str   (optional — skips QoQ if absent)
    quarterly_returns   : list of dicts
                            name, is_benchmark, returns {q0..qN, ttm}
    blended_return      : dict  {q0..qN, ttm}
    commentary          : list of dicts  {heading, body}  (optional)
    disclaimer          : str  (optional)
"""

from __future__ import annotations

import io
import math
from datetime import datetime

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable, Image, PageBreak, Paragraph, SimpleDocTemplate,
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
# AI Commentary
# ══════════════════════════════════════════════════════════════════════════════

def _build_commentary_prompt(portfolio_data: dict) -> str:
    d = portfolio_data
    lines = [
        "You are a professional mutual fund portfolio analyst at WinRich Professional Services.",
        "Write a concise portfolio performance commentary for the following client portfolio.",
        "Use a professional, factual tone. Do NOT use markdown. Use plain text only.",
        "",
        "Structure your response with EXACTLY these 5 sections, each starting with the",
        "section heading on its own line followed by the paragraph body:",
        "  Portfolio Overview",
        "  Equity Portfolio",
        "  Hybrid Portfolio",
        "  Quarter in Review",
        "  Key Observations",
        "",
        "Keep each section to 3-5 sentences. Reference specific fund names, XIRR values,",
        "benchmark comparisons, and quarter labels where available.",
        "",
        "=== PORTFOLIO DATA ===",
        f"Client: {d.get('client_name', 'N/A')}",
        f"Report Date: {d.get('report_date', 'N/A')}",
    ]

    alloc = d.get('client_allocation', {})
    if alloc:
        lines.append("\nAllocation: " +
                     ", ".join(f"{k}: {float(v):.1f}%" for k, v in alloc.items()))

    equity = d.get('equity_funds', [])
    if equity:
        lines.append("\nEquity Funds (XIRR vs Benchmark 1Y):")
        for f in equity:
            xirr  = f.get('xirr')
            b1y   = f.get('benchmark_return_1yr')
            lines.append(
                f"  - {f.get('name','—')}: XIRR="
                f"{'N/A' if xirr is None else f'{xirr:.2f}%'}, "
                f"Benchmark({f.get('benchmark_index','—')}) 1Y="
                f"{'N/A' if b1y is None else f'{b1y:.2f}%'}")

    hybrid = d.get('hybrid_funds', [])
    if hybrid:
        lines.append("\nHybrid Funds (XIRR):")
        for f in hybrid:
            xirr = f.get('xirr')
            lines.append(f"  - {f.get('name','—')}: XIRR="
                         f"{'N/A' if xirr is None else f'{xirr:.2f}%'}")

    blended  = d.get('blended_return', {})
    q_labels = d.get('quarter_labels', [])
    if blended and q_labels:
        lines.append("\nBlended Portfolio QoQ Returns:")
        for i, ql in enumerate(q_labels):
            val = blended.get(f'q{i}')
            lines.append(f"  {ql.replace(chr(10),' ')}: "
                         f"{'N/A' if val is None else f'{val:.2f}%'}")
        ttm = blended.get('ttm')
        if ttm is not None:
            lines.append(f"  TTM: {ttm:.2f}%")

    trend = d.get('portfolio_trend', [])
    if trend:
        lines.append("\nPortfolio Growth (Invested vs Current Value):")
        for t in trend:
            inv = t.get('invested', 0)
            cur = t.get('current',  0)
            gain_pct = ((cur - inv) / inv * 100) if inv > 0 else 0
            lines.append(
                f"  {t.get('label','').replace(chr(10),' ')}: "
                f"Invested=₹{inv/1e5:.1f}L, Current=₹{cur/1e5:.1f}L ({gain_pct:+.1f}%)")

    lines.append("\n=== END OF DATA ===")
    lines.append("Write the commentary now. Use plain text. No bullet points. No markdown.")
    return "\n".join(lines)


def _parse_commentary(raw_text: str) -> list[dict]:
    known_headings = [
        "Portfolio Overview", "Equity Portfolio", "Hybrid Portfolio",
        "Quarter in Review", "Key Observations",
    ]
    blocks, current_heading, current_body = [], None, []

    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue
        matched = next((h for h in known_headings
                        if line.lower().startswith(h.lower())), None)
        if matched:
            if current_heading and current_body:
                blocks.append({'heading': current_heading,
                               'body':    ' '.join(current_body).strip()})
            current_heading = matched
            current_body    = []
            rest = line[len(matched):].strip().lstrip(':').strip()
            if rest:
                current_body.append(rest)
        elif current_heading:
            current_body.append(line)

    if current_heading and current_body:
        blocks.append({'heading': current_heading,
                       'body':    ' '.join(current_body).strip()})
    return blocks


def generate_ai_commentary(portfolio_data: dict) -> list[dict]:
    """
    Call the Anthropic API to generate portfolio commentary.
    Returns list[dict] with {heading, body} — assign to portfolio_data['commentary'].
    """
    try:
        import anthropic
    except ImportError:
        raise ImportError("anthropic package not installed. Run: pip install anthropic")

    client   = anthropic.Anthropic()
    prompt   = _build_commentary_prompt(portfolio_data)
    message  = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    raw_text = message.content[0].text
    blocks   = _parse_commentary(raw_text)
    return blocks or [{'heading': 'Performance Commentary', 'body': raw_text.strip()}]


# ══════════════════════════════════════════════════════════════════════════════
class MFPortfolioPDFGenerator:

    def __init__(self, company_name: str = "WinRich Professional Services"):
        self.company_name = company_name

    def _build_growth_chart(self, trend_data: list[dict]) -> io.BytesIO:
        labels   = [d['label']    for d in trend_data]
        invested = [d['invested'] / 1e5 for d in trend_data]
        current  = [d['current']  / 1e5 for d in trend_data]

        fig, ax = plt.subplots(figsize=(7.5, 3.2))
        fig.patch.set_facecolor('#f7f9fd')
        ax.set_facecolor('#f7f9fd')
        x = range(len(labels))

        ax.plot(x, invested, marker='o', linewidth=2.2, markersize=6,
                color='#2e4899', label='Total Invested', zorder=3)
        ax.plot(x, current,  marker='s', linewidth=2.2, markersize=6,
                color='#1a7a1a', label='Current Value',  zorder=3)
        ax.fill_between(x, invested, current,
                        where=[c >= i for c, i in zip(current, invested)],
                        alpha=0.12, color='#1a7a1a', label='_nolegend_')
        ax.fill_between(x, invested, current,
                        where=[c < i for c, i in zip(current, invested)],
                        alpha=0.12, color='#cc0000', label='_nolegend_')

        for xi, (inv, cur) in enumerate(zip(invested, current)):
            ax.annotate(f"₹{inv:.1f}L", (xi, inv),
                        textcoords="offset points", xytext=(0, -16),
                        ha='center', fontsize=7, color='#2e4899', fontweight='bold')
            ax.annotate(f"₹{cur:.1f}L", (xi, cur),
                        textcoords="offset points", xytext=(0, 7),
                        ha='center', fontsize=7, color='#1a7a1a', fontweight='bold')

        ax.set_xticks(list(x))
        ax.set_xticklabels(labels, fontsize=8.5)
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"₹{v:.0f}L"))
        ax.tick_params(axis='y', labelsize=8)
        ax.set_ylabel("Amount (in Lakhs ₹)", fontsize=8.5, color='#555555')
        ax.set_title("Portfolio Growth — Invested vs Current Value",
                     fontsize=10, fontweight='bold', color='#1a2a5e', pad=10)
        ax.grid(axis='y', linestyle='--', alpha=0.4, color='#c0cce8')
        ax.grid(axis='x', linestyle='', alpha=0)
        for spine in ['top', 'right']:
            ax.spines[spine].set_visible(False)
        for spine in ['left', 'bottom']:
            ax.spines[spine].set_color('#c0cce8')
        ax.legend(loc='upper left', fontsize=8, framealpha=0.6,
                  facecolor='white', edgecolor='#c0cce8')
        plt.tight_layout(pad=1.2)

        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=150, bbox_inches='tight')
        buf.seek(0)
        plt.close(fig)
        return buf

    def _section_growth_chart(self, trend_data: list[dict]) -> list:
        story = [_p("Portfolio Growth — Invested vs Current Value", 'section')]
        buf   = self._build_growth_chart(trend_data)
        story.append(Image(buf, width=6.8 * inch, height=2.9 * inch))
        story.append(_p(
            "Values shown in Indian Lakhs (₹L). "
            "Invested = cumulative TotalInvAmt. Current Value = market value at quarter end.",
            'footnote'))
        return story

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

    def _section_allocation(self, client_alloc: dict, model_alloc: dict) -> list:
        story = [_p("1. Portfolio Allocation Snapshot", 'section')]
        fund_type_map = {
            'Equity': 'Flexi Cap / Large Cap / Mid Cap / ELSS',
            'Hybrid': 'Balanced Advantage / Multi-Asset / BAF',
            'Debt':   'Debt / Liquid / Short Duration / Gilt',
        }
        rows = [[
            _p("Asset Class", 'th_left'), _p("Client %", 'th'),
            _p("Model %", 'th'),          _p("Variance", 'th'),
            _p("Fund Type", 'th_left'),
        ]]
        for ac in client_alloc:
            cv   = float(client_alloc.get(ac, 0))
            mv   = float(model_alloc.get(ac, 0))
            diff = cv - mv
            sign = '+' if diff >= 0 else ''
            diff_col = (GREY_TEXT if abs(diff) < 0.01 else GREEN if diff > 0 else RED)
            rows.append([
                _p(ac, 'cell_b'),
                _p(f"{cv:.2f}%", 'cell_bc'),
                _p(f"{mv:.2f}%", 'cell_bc'),
                Paragraph(f"<font color='{diff_col.hexval()}'>{sign}{diff:.2f}%</font>",
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

    def _section_equity(self, equity_funds: list) -> list:
        story = [_p("2. Equity Fund Performance vs Benchmark", 'section')]
        rows  = [[
            _p("Fund Name",       'th_left'), _p("Benchmark Index", 'th_left'),
            _p("XIRR",            'th'),      _p("Bench 1M",        'th'),
            _p("Bench 3M",        'th'),      _p("Bench 1Y",        'th'),
            _p("Bench 3Y",        'th'),      _p("Bench 5Y",        'th'),
        ]]
        for f in equity_funds:
            name = f.get('name', '—')
            for suffix in [
                ' (Erstwhile Kotak Standard Multicap Fund - Gr)',
                ' (Erstwhile Kotak Emerging Equity Scheme)',
                ' - Regular Plan - Growth', ' - Regular Growth',
                ' - Regular Plan', ' Regular Growth', ' - Growth',
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
        ts.add('ALIGN', (0, 0), (1, -1), 'LEFT')
        ts.add('BACKGROUND', (2, 1), (2, -1), colors.HexColor('#eef2fb'))
        for cmd in _alt_rows(len(rows)):
            ts.add(*cmd)
        t.setStyle(ts)
        story.append(t)
        story.append(_p(
            "XIRR = annualised return since first investment. "
            "Benchmark columns show point-to-point returns for the given period. "
            "1Y, 3Y, 5Y are CAGR. 1M and 3M are absolute. "
            "Past performance is not indicative of future returns.", 'footnote'))
        return story

    def _section_hybrid(self, hybrid_funds: list) -> list:
        story = [_p("3. Hybrid Fund Performance", 'section')]
        rows  = [[_p("Fund Name", 'th_left'), _p("XIRR", 'th')]]
        for f in hybrid_funds:
            rows.append([_p(f.get('name', '—'), 'cell_b'), _xirr_cell(f.get('xirr'))])
        t  = Table(rows, colWidths=[5.9*inch, 0.9*inch], repeatRows=1)
        ts = _base_ts()
        ts.add('ALIGN', (0, 0), (0, -1), 'LEFT')
        for cmd in _alt_rows(len(rows)):
            ts.add(*cmd)
        t.setStyle(ts)
        story.append(t)
        story.append(_p("XIRR = Extended Internal Rate of Return (since inception).", 'footnote'))
        return story

    def _section_debt(self, debt_funds: list) -> list:
        story = [_p("4. Debt Fund Performance", 'section')]
        rows  = [[_p("Fund Name", 'th_left'), _p("XIRR", 'th')]]
        for f in debt_funds:
            rows.append([_p(f.get('name', '—'), 'cell_b'), _xirr_cell(f.get('xirr'))])
        t  = Table(rows, colWidths=[5.9*inch, 0.9*inch], repeatRows=1)
        ts = _base_ts()
        ts.add('ALIGN', (0, 0), (0, -1), 'LEFT')
        for cmd in _alt_rows(len(rows)):
            ts.add(*cmd)
        t.setStyle(ts)
        story.append(t)
        return story

    def _section_amc(self, amc_data: dict) -> list:
        story      = [_p("5. AMC Concentration", 'section')]
        total      = sum(amc_data.values())
        sorted_amc = sorted(amc_data.items(), key=lambda x: x[1], reverse=True)
        rows = [[
            _p("AMC Name", 'th_left'),
            _p("No. of Funds", 'th'),
            _p("% of Portfolio", 'th'),
        ]]
        for amc, count in sorted_amc:
            pct = (count / total * 100) if total > 0 else 0
            rows.append([
                _p(amc, 'cell'),
                _p(str(count),    'cell_bc'),
                _p(f"{pct:.1f}%", 'cell_bc'),
            ])
        rows.append([_p("Total", 'cell_b'), _p(str(total), 'cell_bc'), _p("100.0%", 'cell_bc')])
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

    def _section_qoq_fund(self, quarterly_rows: list,
                           q_labels: list, footnote: str = None) -> list:
        story  = [_p("6. Quarterly Returns — Fund-Level (QoQ)", 'section')]
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
            r   = entry.get('returns', {})
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
        story.append(_p(footnote or (
            "Returns are annualised XIRR for the quarter window. "
            "TTM = since-inception XIRR as of latest quarter."), 'footnote'))
        return story

    def _section_qoq_portfolio(self, quarterly_rows: list, q_labels: list,
                                blended_return: dict, footnote: str = None) -> list:
        story  = [_p("7. QoQ Portfolio-Level Returns", 'section')]
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
            row      = [_p(entry.get('name', '—'), 'cell_it' if is_bench else 'cell_b')]
            for qk in q_keys:
                row.append(_ret(r.get(qk), color_it=not is_bench))
            row.append(_ret(r.get('ttm'), bold=not is_bench, color_it=not is_bench))
            rows.append(row)
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
        story.append(_p(footnote or (
            "Benchmark rows shown in tinted rows directly below each fund. "
            "Blended return = value-weighted average across all funds."), 'footnote'))
        return story

    def _page_cb(self, canvas, doc):
        _draw_footer(canvas, doc, self.company_name)

    def generate_report(self, portfolio_data: dict, output_file: str) -> str:
        d = portfolio_data
        self.company_name = d.get('company_name', self.company_name)

        doc = SimpleDocTemplate(
            output_file, pagesize=letter,
            topMargin=0.6*inch, bottomMargin=0.75*inch,
            leftMargin=0.75*inch, rightMargin=0.75*inch,
        )
        page_w = letter[0] - 1.5 * inch
        story  = []

        client = d.get('client_name', '—')
        rdate  = d.get('report_date', datetime.now().strftime('%B %d, %Y'))
        story.append(HeaderBanner(page_w, self.company_name, [
            f"Client: {client}   |   Report Date: {rdate}",
            f"Prepared by: {d.get('prepared_by', self.company_name)}",
        ]))
        story.append(Spacer(1, 0.15*inch))

        if d.get('summary'):
            story.extend(self._section_summary(d['summary']))
            story.append(Spacer(1, 0.12*inch))

        if d.get('portfolio_trend'):
            story.extend(self._section_growth_chart(d['portfolio_trend']))
            story.append(Spacer(1, 0.12*inch))

        client_alloc = d.get('client_allocation', {})
        model_alloc  = d.get('model_allocation',  {})
        if client_alloc and model_alloc:
            story.extend(self._section_allocation(client_alloc, model_alloc))
            story.append(Spacer(1, 0.12*inch))

        if d.get('equity_funds'):
            story.extend(self._section_equity(d['equity_funds']))
            story.append(Spacer(1, 0.12*inch))

        if d.get('hybrid_funds'):
            story.extend(self._section_hybrid(d['hybrid_funds']))
            story.append(Spacer(1, 0.12*inch))

        if d.get('debt_funds'):
            story.extend(self._section_debt(d['debt_funds']))
            story.append(Spacer(1, 0.12*inch))

        if d.get('amc_concentration'):
            story.extend(self._section_amc(d['amc_concentration']))
            story.append(Spacer(1, 0.12*inch))

        q_labels       = d.get('quarter_labels',    [])
        quarterly_rows = d.get('quarterly_returns', [])
        blended_return = d.get('blended_return',    {})

        if quarterly_rows and q_labels:
            story.append(PageBreak())
            story.extend(self._section_qoq_fund(quarterly_rows, q_labels))
            story.append(Spacer(1, 0.12*inch))
            if blended_return:
                story.extend(self._section_qoq_portfolio(
                    quarterly_rows, q_labels, blended_return))
                story.append(Spacer(1, 0.12*inch))

        commentary_blocks = d.get('commentary')
        if not commentary_blocks and d.get('_ai_commentary_raw'):
            commentary_blocks = _parse_commentary(d['_ai_commentary_raw'])

        if commentary_blocks:
            story.append(PageBreak())
            story.append(_p("Overall Performance Commentary", 'section'))
            story.append(HRFlowable(width='100%', thickness=1,
                                    color=RULE_COLOR, spaceAfter=6))
            for block in commentary_blocks:
                story.append(_p(block.get('heading', ''), 'comment_h'))
                story.append(_p(block.get('body',    ''), 'comment_b'))

        story.append(Spacer(1, 0.15*inch))
        story.append(HRFlowable(width='100%', thickness=0.5,
                                color=RULE_COLOR, spaceAfter=4))
        disclaimer = d.get('disclaimer', (
            "This report is prepared by WinRich Professional Services for informational "
            "purposes only and does not constitute investment advice. Mutual fund investments "
            "are subject to market risks. Past performance is not indicative of future returns. "
            "Please read all scheme-related documents carefully before investing. "
            "Data sourced from fund houses and AMFI."))
        story.append(Paragraph(f"<b>Disclaimer:</b> {disclaimer}", S['disclaimer']))

        doc.build(story, onFirstPage=self._page_cb, onLaterPages=self._page_cb)
        return output_file