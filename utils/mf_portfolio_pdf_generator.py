"""
mf_portfolio_pdf_generator.py
------------------------------   
WinRich Professional Services — Portfolio Performance Report Generator

CHANGES (this version)
-----------------------
Fix 1  Section 2  — Added Bench 3Y column (between Bench 1Y and Bench 5Y).
Fix 2  Section 2a — Total Portfolio row now shows value-weighted XIRR instead of "—".
Fix 3  Section 3  — Improved AMC pie chart readability: distinct colour palette,
                     coloured % labels, cleaner legend with Rs. value on second line,
                     dynamic chart height based on number of AMCs.
"""

from __future__ import annotations
import io, math, os
from datetime import datetime

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable, Image, PageBreak, Paragraph, SimpleDocTemplate,
    Spacer, Table, TableStyle,
)
from reportlab.platypus.flowables import Flowable

# ── Font registration ─────────────────────────────────────────────────────────
_FONT_DIR_GOOGLE  = '/usr/share/fonts/truetype/google-fonts'
_FONT_DIR_LIBSANS = '/usr/share/fonts/truetype/liberation'
_FONT_DIR_DEJAVU  = '/usr/share/fonts/truetype/dejavu'

from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics

def _reg(name, path):
    try:
        pdfmetrics.registerFont(TTFont(name, path))
        return True
    except Exception:
        return False

_reg('Poppins',           f'{_FONT_DIR_GOOGLE}/Poppins-Regular.ttf')
_reg('Poppins-Bold',      f'{_FONT_DIR_GOOGLE}/Poppins-Bold.ttf')
_reg('Poppins-Medium',    f'{_FONT_DIR_GOOGLE}/Poppins-Medium.ttf')
_reg('Poppins-Light',     f'{_FONT_DIR_GOOGLE}/Poppins-Light.ttf')
_reg('Poppins-Italic',    f'{_FONT_DIR_GOOGLE}/Poppins-Italic.ttf')
_reg('LibSans',           f'{_FONT_DIR_LIBSANS}/LiberationSans-Regular.ttf')
_reg('LibSans-Bold',      f'{_FONT_DIR_LIBSANS}/LiberationSans-Bold.ttf')
_reg('LibSans-Italic',    f'{_FONT_DIR_LIBSANS}/LiberationSans-Italic.ttf')
_reg('LibSans-BoldItalic',f'{_FONT_DIR_LIBSANS}/LiberationSans-BoldItalic.ttf')
_reg('DejaVu',            f'{_FONT_DIR_DEJAVU}/DejaVuSans.ttf')
_reg('DejaVu-Bold',       f'{_FONT_DIR_DEJAVU}/DejaVuSans-Bold.ttf')

def _font(preferred, fallback='Helvetica'):
    try:
        pdfmetrics.getFont(preferred)
        return preferred
    except Exception:
        return fallback

F_HEAD   = _font('Poppins-Bold')
F_HEAD_M = _font('Poppins-Medium')
F_BODY   = _font('LibSans')
F_BODY_B = _font('LibSans-Bold')
F_BODY_I = _font('LibSans-Italic')
F_SMALL  = _font('Poppins-Light')
RS = 'Rs.'

NAVY       = colors.HexColor('#1a2a5e')
MID_BLUE   = colors.HexColor('#2e4899')
LIGHT_BLUE = colors.HexColor('#e8edf7')
ALT_ROW    = colors.HexColor('#f2f5fb')
BENCH_BG   = colors.HexColor('#eef0f8')
WHITE      = colors.white
GREY_TEXT  = colors.HexColor('#555555')
LIGHT_GREY = colors.HexColor('#f5f5f5')
GREEN      = colors.HexColor('#1a7a1a')
RED        = colors.HexColor('#cc0000')
RULE_COLOR = colors.HexColor('#c0cce8')
PAGE_W = 7.0


def _mk(name, **kw): return ParagraphStyle(name, **kw)

S = {
    'th':        _mk('th',        fontName=F_HEAD_M, fontSize=7.5, textColor=WHITE, leading=10, alignment=TA_CENTER),
    'th_left':   _mk('th_left',   fontName=F_HEAD_M, fontSize=7.5, textColor=WHITE, leading=10, alignment=TA_LEFT),
    'th_sm':     _mk('th_sm',     fontName=F_HEAD_M, fontSize=6.5, textColor=WHITE, leading=9,  alignment=TA_CENTER),
    'cell':      _mk('cell',      fontName=F_BODY, fontSize=8, leading=10),
    'cell_c':    _mk('cell_c',    fontName=F_BODY, fontSize=8, leading=10, alignment=TA_CENTER),
    'cell_r':    _mk('cell_r',    fontName=F_BODY, fontSize=8, leading=10, alignment=TA_RIGHT),
    'cell_b':    _mk('cell_b',    fontName=F_BODY_B, fontSize=8, leading=10),
    'cell_bc':   _mk('cell_bc',   fontName=F_BODY_B, fontSize=8, leading=10, alignment=TA_CENTER),
    'cell_br':   _mk('cell_br',   fontName=F_BODY_B, fontSize=8, leading=10, alignment=TA_RIGHT),
    'cell_sm':   _mk('cell_sm',   fontName=F_SMALL, fontSize=7, leading=9, textColor=GREY_TEXT),
    'cell_sm_c': _mk('cell_sm_c', fontName=F_SMALL, fontSize=7, leading=9, textColor=GREY_TEXT, alignment=TA_CENTER),
    'cell_it':   _mk('cell_it',   fontName=F_BODY_I, fontSize=7.5, leading=10, textColor=GREY_TEXT),
    'section':   _mk('section',   fontName=F_HEAD, fontSize=11, textColor=NAVY, spaceBefore=14, spaceAfter=4, leading=14),
    'sub_hdr':   _mk('sub_hdr',   fontName=F_HEAD, fontSize=9, textColor=MID_BLUE, spaceBefore=10, spaceAfter=3, leading=12),
    'footnote':  _mk('footnote',  fontName=F_SMALL, fontSize=7, textColor=GREY_TEXT, leading=9, spaceAfter=4),
    'disclaimer':_mk('disclaimer',fontName=F_BODY_I, fontSize=7.5, textColor=GREY_TEXT, leading=11, alignment=TA_JUSTIFY),
    'comment_h': _mk('comment_h', fontName=F_HEAD, fontSize=9.5, textColor=NAVY, spaceBefore=8, spaceAfter=3, leading=12),
    'comment_b': _mk('comment_b', fontName=F_BODY, fontSize=8.5, textColor=colors.HexColor('#222222'), leading=13, spaceAfter=6, alignment=TA_JUSTIFY),
    'intro':     _mk('intro',     fontName=F_BODY, fontSize=9, textColor=colors.HexColor('#333333'), leading=14, spaceAfter=6, alignment=TA_JUSTIFY),
    'intro_b':   _mk('intro_b',   fontName=F_BODY_B, fontSize=9, textColor=colors.HexColor('#333333'), leading=14),
    'kpi_val':   _mk('kpi_val',   fontName=F_HEAD, fontSize=14, textColor=NAVY, leading=17, alignment=TA_CENTER),
    'kpi_lbl':   _mk('kpi_lbl',   fontName=F_SMALL, fontSize=7, textColor=GREY_TEXT, leading=9, alignment=TA_CENTER),
}

def _p(text, style='cell'): return Paragraph(str(text), S[style])

def _fmt_inr(val):
    try:
        v = float(val)
    except (TypeError, ValueError):
        return '—'
    return f"{RS}{v:,.0f}"

def _ret(val, bold=False, color_it=True, style='cell_c'):
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return _p('—', 'cell_sm_c')
    v    = float(val)
    sign = '+' if v >= 0 else ''
    text = f"{sign}{v:.2f}%"
    if color_it:
        col = GREEN if v >= 0 else RED
        markup = (f"<font color='{col.hexval()}'><b>{text}</b></font>" if bold
                  else f"<font color='{col.hexval()}'>{text}</font>")
    else:
        markup = f"<b>{text}</b>" if bold else text
    return Paragraph(markup, S[style])

def _xirr_cell(val, style='cell_bc'):
    if val is None: return _p('—', 'cell_sm_c')
    v   = float(val)
    col = GREEN if v >= 0 else RED
    return Paragraph(f"<font color='{col.hexval()}'><b>{v:.2f}%</b></font>", S[style])

def _base_ts():
    return TableStyle([
        ('BACKGROUND',    (0,0),(-1,0),  NAVY),
        ('TEXTCOLOR',     (0,0),(-1,0),  WHITE),
        ('FONTNAME',      (0,0),(-1,0),  F_HEAD_M),
        ('FONTSIZE',      (0,0),(-1,-1), 8),
        ('VALIGN',        (0,0),(-1,-1), 'MIDDLE'),
        ('GRID',          (0,0),(-1,-1), 0.4, RULE_COLOR),
        ('TOPPADDING',    (0,0),(-1,-1), 4),
        ('BOTTOMPADDING', (0,0),(-1,-1), 4),
        ('LEFTPADDING',   (0,0),(-1,-1), 5),
        ('RIGHTPADDING',  (0,0),(-1,-1), 5),
    ])

def _alt_rows(n, start=1):
    return [('BACKGROUND',(0,i),(-1,i), ALT_ROW if i%2==1 else WHITE) for i in range(start, n)]


class HeaderBanner(Flowable):
    H = 1.65 * inch

    def __init__(self, page_w, company, client_name, report_date,
                 investment_start, prepared_by, risk_profile,
                 reference_benchmark, n_funds, n_amcs, data_as_on, logo_path=None):
        super().__init__()
        self.width=page_w; self.height=self.H; self.company=company
        self.client_name=client_name; self.report_date=report_date
        self.investment_start=investment_start; self.prepared_by=prepared_by
        self.risk_profile=risk_profile; self.reference_benchmark=reference_benchmark
        self.n_funds=n_funds; self.n_amcs=n_amcs; self.data_as_on=data_as_on
        self.logo_path=logo_path

    def draw(self):
        c=self.canv; w=self.width; h=self.height
        c.setFillColor(NAVY); c.rect(0,0,w,h,fill=1,stroke=0)
        c.setFillColor(MID_BLUE); c.rect(0,0,w,3,fill=1,stroke=0)

        logo_resolved = None
        if self.logo_path:
            _here = os.path.dirname(os.path.abspath(__file__))
            _cwd  = os.getcwd()
            candidates = [
                self.logo_path,
                os.path.join(_cwd, self.logo_path),
                os.path.join(os.path.dirname(_cwd), self.logo_path),
                os.path.join(_here, self.logo_path),
                os.path.join(_here, '..', self.logo_path),
            ]
            for candidate in candidates:
                candidate = os.path.normpath(candidate)
                if os.path.exists(candidate):
                    logo_resolved = candidate
                    break

        if logo_resolved:
            try:
                lh = 0.45 * inch
                lw = 1.6  * inch
                c.drawImage(logo_resolved, w - lw - 10, h - lh - 8,
                            width=lw, height=lh,
                            preserveAspectRatio=True, mask='auto')
            except Exception as _logo_err:
                import logging as _lg
                _lg.getLogger(__name__).warning("Logo draw failed: %s", _logo_err)
                logo_resolved = None
        elif self.logo_path:
            import logging as _lg
            _lg.getLogger(__name__).warning(
                "Logo not found. Tried paths relative to: cwd=%s file=%s logo_path=%s",
                os.getcwd(), os.path.abspath(__file__), self.logo_path)

        tx = 12
        c.setFont(F_HEAD, 8)
        c.setFillColor(colors.HexColor('#a0b8e8'))
        c.drawString(tx, h - 16, self.company)

        c.setFont(F_HEAD, 14)
        c.setFillColor(WHITE)
        c.drawString(tx, h - 33, "Client Portfolio Performance Report")

        c.setFont(F_HEAD, 10)
        c.setFillColor(colors.HexColor('#ccd6f0'))
        c.drawString(tx, h - 50, self.client_name)

        c.setFont(F_SMALL, 7.5)
        c.setFillColor(colors.HexColor('#99afd8'))
        meta = [m for m in [
            f"Report Date: {self.report_date}"           if self.report_date       else '',
            f"Investment Start: {self.investment_start}" if self.investment_start  else '',
            f"Prepared by: {self.prepared_by}"           if self.prepared_by       else '',
        ] if m]
        y = h - 63
        for line in meta:
            c.drawString(tx, y, line); y -= 11

        pills = [(k, v) for k, v in [
            ("Risk Profile", self.risk_profile),
            ("No. of Funds", str(self.n_funds) if self.n_funds else ''),
            ("No. of AMCs",  str(self.n_amcs)  if self.n_amcs  else ''),
            ("Data As On",   self.data_as_on),
        ] if v]

        if pills:
            row_h = 0.40 * inch
            c.setFillColor(WHITE)
            c.rect(0, 0, w, row_h, fill=1, stroke=0)
            c.setStrokeColor(RULE_COLOR)
            c.setLineWidth(0.8)
            c.line(0, row_h, w, row_h)
            cell_w = w / len(pills)
            for i, (lbl, val) in enumerate(pills):
                cx = i * cell_w + cell_w / 2
                if i > 0:
                    c.setStrokeColor(RULE_COLOR)
                    c.setLineWidth(0.5)
                    c.line(i * cell_w, 4, i * cell_w, row_h - 4)
                c.setFont(F_SMALL, 6.5)
                c.setFillColor(colors.HexColor('#888888'))
                c.drawCentredString(cx, row_h - 13, lbl)
                val_display = val if len(val) <= 22 else val[:21] + '…'
                c.setFont(F_HEAD_M, 8)
                c.setFillColor(colors.HexColor('#1a2a5e'))
                c.drawCentredString(cx, 5, val_display)


def _draw_footer(canvas, doc, company, website='', email=''):
    w, _ = letter
    canvas.saveState()
    canvas.setFont(F_SMALL, 7); canvas.setFillColor(GREY_TEXT)
    canvas.drawString(0.75*inch, 0.42*inch, company)
    if email or website:
        canvas.drawCentredString(w/2, 0.42*inch, ' | '.join(filter(None,[email,website])))
    canvas.drawRightString(w-0.75*inch, 0.42*inch, f"Page {doc.page}")
    canvas.setStrokeColor(RULE_COLOR); canvas.setLineWidth(0.5)
    canvas.line(0.75*inch, 0.52*inch, w-0.75*inch, 0.52*inch)
    canvas.restoreState()


import math as _math

def _safe_float(v):
    if v is None:
        return None
    try:
        f = float(v)
        return None if (_math.isnan(f) or _math.isinf(f)) else f
    except (TypeError, ValueError):
        return None


def _build_commentary_prompt(portfolio_data: dict) -> str:
    d = portfolio_data

    total_cur  = _safe_float(d.get("total_current_value"))
    total_inv  = _safe_float(d.get("total_invested"))
    total_gain = _safe_float(d.get("total_gain"))
    port_xirr  = _safe_float(d.get("portfolio_xirr"))

    if (total_cur is None or total_cur == 0) and d.get("fund_gains"):
        total_cur  = sum(_safe_float(f.get("current_value"))   or 0 for f in d["fund_gains"])
        total_inv  = sum(_safe_float(f.get("amount_invested")) or 0 for f in d["fund_gains"])
        total_gain = (total_cur - total_inv) if total_cur and total_inv else None

    has_fund_data = bool(
        d.get("all_funds") or d.get("fund_gains") or
        d.get("equity_funds") or d.get("hybrid_funds")
    )
    if not has_fund_data:
        raise ValueError(
            f"_build_commentary_prompt: no fund data found. Keys: {list(d.keys())}"
        )

    total_cur  = total_cur  or 0.0
    total_inv  = total_inv  or 0.0
    total_gain = total_gain if total_gain is not None else (total_cur - total_inv)
    xirr_str   = f"{port_xirr:.2f}%" if port_xirr is not None else "N/A"

    header = (
        f"Client: {d.get('client_name', 'N/A')} | "
        f"Report Date: {d.get('report_date', 'N/A')}\n"
        f"Investment Start: {d.get('investment_start', 'N/A')} | "
        f"Invested: Rs.{total_inv:,.0f} | "
        f"Current Value: Rs.{total_cur:,.0f} | "
        f"Gain: Rs.{total_gain:,.0f} | "
        f"Portfolio XIRR: {xirr_str} | "
        f"Funds: {d.get('n_funds', '?')} across {d.get('n_amcs', '?')} AMCs"
    )

    funds_source = list(d.get("all_funds") or [])

    if not funds_source:
        for f in (d.get("equity_funds") or []) + (d.get("hybrid_funds") or []):
            funds_source.append({
                "name":                 f.get("name", "—"),
                "xirr":                 _safe_float(f.get("xirr")),
                "benchmark_index":      f.get("benchmark_index") or "",
                "benchmark_xirr":       _safe_float(f.get("benchmark_xirr") or f.get("benchmark")),
                "benchmark_return_1yr": _safe_float(f.get("benchmark_return_1yr")),
                "benchmark_return_3yr": _safe_float(f.get("benchmark_return_3yr")),
                "benchmark_return_5yr": _safe_float(f.get("benchmark_return_5yr")),
                "winrich_rank":         f.get("winrich_rank", "N/A"),
            })

    if not funds_source:
        for fg in (d.get("fund_gains") or []):
            funds_source.append({
                "name":  fg.get("name", "—"),
                "xirr":  _safe_float(fg.get("xirr")),
                "benchmark_index": "", "benchmark_xirr": None,
                "benchmark_return_1yr": None, "benchmark_return_3yr": None,
                "benchmark_return_5yr": None, "winrich_rank": "N/A",
            })

    fund_lines = []
    for f in funds_source:
        fx     = _safe_float(f.get("xirr"))
        fx_str = f"{fx:.2f}%" if fx is not None else "N/A"
        b1yr   = _safe_float(f.get("benchmark_return_1yr") or f.get("benchmark_xirr"))
        b3yr   = _safe_float(f.get("benchmark_return_3yr"))
        b5yr   = _safe_float(f.get("benchmark_return_5yr"))
        bench_details = ", ".join(filter(None, [
            f"1Y={b1yr:.2f}%" if b1yr is not None else None,
            f"3Y={b3yr:.2f}%" if b3yr is not None else None,
            f"5Y={b5yr:.2f}%" if b5yr is not None else None,
        ])) or "N/A"
        bench_idx = f.get("benchmark_index") or "N/A"
        rank      = f.get("winrich_rank")    or "N/A"
        fund_lines.append(
            f"  {f.get('name', '—')}: "
            f"Your XIRR={fx_str} | "
            f"Benchmark ({bench_idx}) Trailing Returns: {bench_details} | "
            f"WinRich Rank={rank}"
        )

    alloc_lines = []
    for r in (d.get("allocation_rows") or []):
        alloc_lines.append(
            f"  {r.get('asset_class', '')}: "
            f"{r.get('your_allocation', '')} — "
            f"{r.get('funds_in_portfolio', '')}"
        )
    if not alloc_lines:
        for ac, pct in (d.get("client_allocation") or {}).items():
            pct_f = _safe_float(pct)
            if pct_f and pct_f > 0:
                alloc_lines.append(f"  {ac}: {pct_f:.1f}%")

    amc_lines = []
    for amc, v in (d.get("amc_concentration") or {}).items():
        if isinstance(v, dict):
            pct = _safe_float(v.get("pct")) or 0
            val = _safe_float(v.get("value")) or 0
            amc_lines.append(f"  {amc}: {pct:.1f}% (Rs.{val:,.0f})")
        else:
            amc_lines.append(f"  {amc}")

    lines = [
        "You are a professional mutual fund portfolio analyst at WinRich Professional Services.",
        "Write a concise, warm portfolio performance commentary addressed directly to the client.",
        "Use plain text only — no markdown formatting, no bullet points, no asterisks.",
        "",
        "Use EXACTLY these four section headings, each on its own line:",
        "  How Your Portfolio Has Done Overall",
        "  Your Equity Funds",
        "  Your Hybrid Fund",
        "  Key Observations",
        "",
        "Write 3-5 sentences per section.",
        "Reference actual fund names, XIRR values, benchmark index names,",
        "and trailing benchmark returns (1Y/3Y/5Y where available).",
        "Note: Your XIRR is the annualised return from the client's investment date.",
        "Trailing benchmark returns (1Y/3Y/5Y) are market index returns over those calendar periods.",
        "",
        "--- Portfolio Data ---",
        header,
        "",
        "Funds:",
        *fund_lines,
        "",
        "Asset Allocation:",
        *(alloc_lines or ["  N/A"]),
        "",
        "AMC Concentration:",
        *(amc_lines or ["  N/A"]),
        "",
        "Write commentary now.",
    ]
    return "\n".join(lines)


def _parse_commentary(raw):
    headings = [
        "How Your Portfolio Has Done Overall","Your Equity Funds",
        "Your Hybrid Fund","Your Hybrid Funds","Key Observations",
        "Portfolio Overview","Equity Portfolio","Hybrid Portfolio","Quarter in Review",
    ]
    blocks, cur_h, cur_b = [], None, []
    for line in raw.splitlines():
        line = line.strip()
        if not line: continue
        matched = next((h for h in headings if line.lower().startswith(h.lower())), None)
        if matched:
            if cur_h and cur_b: blocks.append({'heading':cur_h,'body':' '.join(cur_b).strip()})
            cur_h=matched; cur_b=[]
            rest = line[len(matched):].strip().lstrip(':').strip()
            if rest: cur_b.append(rest)
        elif cur_h:
            cur_b.append(line)
    if cur_h and cur_b: blocks.append({'heading':cur_h,'body':' '.join(cur_b).strip()})
    return blocks


def generate_ai_commentary(portfolio_data):
    """Call Claude API to generate portfolio commentary. Requires ANTHROPIC_API_KEY."""
    try:
        import anthropic
    except ImportError:
        raise ImportError("Run: pip install anthropic")
    client  = anthropic.Anthropic()
    message = client.messages.create(
        model="claude-sonnet-4-20250514", max_tokens=1800,
        messages=[{"role":"user","content":_build_commentary_prompt(portfolio_data)}],
    )
    raw    = message.content[0].text
    blocks = _parse_commentary(raw)
    return blocks or [{'heading':'Performance Commentary','body':raw.strip()}]


class MFPortfolioPDFGenerator:

    def __init__(self, company_name="WinRich Professional Services", logo_path=None):
        self.company_name = company_name
        self.logo_path    = logo_path
        self._website = ''
        self._email   = ''

    def _section_intro(self, client_name):
        story = [_p(f"Dear {client_name},", 'intro_b')]
        story.append(_p(
            "Here is your portfolio performance report prepared by WinRich. "
            "This report gives you a clear picture of how your investments have grown, "
            "how each fund has performed against the market, and where your money is currently placed.",
            'intro'))
        story.append(Spacer(1, 0.05*inch))
        rows = [
            [_p("How to Read This Report",'cell_b'), _p('','cell')],
            [_p("XIRR (Since Investment)",'cell_b'),
             _p("The annualised return your money has earned from the day you first invested in that fund. "
                "It accounts for the exact timing of your investments, making it the most accurate "
                "measure of how your money has actually grown.",'cell_sm')],
            [_p("Benchmark XIRR",'cell_b'),
             _p("What the relevant market index returned over the same period — starting from your "
                "investment date. Comparing your fund's XIRR to this tells you whether your fund "
                "did better or worse than simply tracking the market.",'cell_sm')],
            [_p("WinRich Rank",'cell_b'),
             _p("WinRich's own ranking of this fund among all funds in its category "
                "(e.g. 3 / 29 means 3rd out of 29 funds). A lower number is better. "
                "Based on WinRich's proprietary research, updated regularly.",'cell_sm')],
            [_p("AMC Concentration",'cell_b'),
             _p("How your money is spread across different fund houses, based on current market value.",'cell_sm')],
        ]
        t  = Table(rows, colWidths=[1.6*inch, 5.4*inch])
        ts = TableStyle([
            ('BACKGROUND',    (0,0),(-1,0),  LIGHT_BLUE),
            ('SPAN',          (0,0),(-1,0)),
            ('GRID',          (0,0),(-1,-1), 0.4, RULE_COLOR),
            ('VALIGN',        (0,0),(-1,-1), 'TOP'),
            ('TOPPADDING',    (0,0),(-1,-1), 5),
            ('BOTTOMPADDING', (0,0),(-1,-1), 5),
            ('LEFTPADDING',   (0,0),(-1,-1), 7),
            ('RIGHTPADDING',  (0,0),(-1,-1), 7),
        ])
        for i in range(1, len(rows)):
            ts.add('BACKGROUND',(0,i),(-1,i), ALT_ROW if i%2==1 else WHITE)
        t.setStyle(ts)
        story.append(t)
        return story

    def _section_snapshot(self, d):
        story = [_p("1  Portfolio Snapshot", 'section')]

        kpis = [
            (_fmt_inr(d.get('total_current_value')), "Current Portfolio Value"),
            (_fmt_inr(d.get('total_invested')),       "Total Amount Invested"),
            (_fmt_inr(d.get('total_gain')),            "Total Gain"),
            (f"{float(d['portfolio_xirr']):.2f}%" if d.get('portfolio_xirr') else '—',
             "XIRR (Since Investment)"),
            (str(d.get('n_funds','—')), "Total Funds"),
        ]
        val_row = [_p(v,'kpi_val') for v,_ in kpis]
        lbl_row = [_p(l,'kpi_lbl') for _,l in kpis]

        kpi_t = Table([val_row, lbl_row], colWidths=[1.4*inch]*len(kpis))
        kpi_ts = TableStyle([
            ('ALIGN',         (0,0),(-1,-1),'CENTER'),
            ('VALIGN',        (0,0),(-1,-1),'MIDDLE'),
            ('TOPPADDING',    (0,0),(-1,-1), 6),
            ('BOTTOMPADDING', (0,0),(-1,-1), 3),
            ('LEFTPADDING',   (0,0),(-1,-1), 4),
            ('RIGHTPADDING',  (0,0),(-1,-1), 4),
            ('LINEAFTER',     (0,0),(-2,-1), 0.5, RULE_COLOR),
            ('BOX',           (0,0),(-1,-1), 0.5, RULE_COLOR),
            ('BACKGROUND',    (0,0),(-1,-1), LIGHT_GREY),
        ])
        gain = d.get('total_gain')
        if gain is not None:
            kpi_ts.add('TEXTCOLOR',(2,0),(2,0), GREEN if float(gain)>=0 else RED)
        kpi_t.setStyle(kpi_ts)
        story.append(kpi_t)
        story.append(Spacer(1, 0.1*inch))

        story.append(_p("How your money is currently split across asset types:",'cell_sm'))
        story.append(Spacer(1, 0.04*inch))

        alloc_rows = d.get('allocation_rows',[])
        if alloc_rows:
            hdr = [_p("Asset Class",'th_left'),_p("Your Allocation",'th'),_p("Funds in Your Portfolio",'th_left')]
            rows = [hdr]
            for r in alloc_rows:
                rows.append([
                    _p(r.get('asset_class','—'),'cell_b'),
                    _p(r.get('your_allocation','—'),'cell_bc'),
                    _p(r.get('funds_in_portfolio','—'),'cell_sm'),
                ])
            t  = Table(rows, colWidths=[1.1*inch, 1.1*inch, 4.8*inch], repeatRows=1)
            ts = _base_ts()
            ts.add('ALIGN',(0,0),(0,-1),'LEFT'); ts.add('ALIGN',(2,0),(2,-1),'LEFT')
            for cmd in _alt_rows(len(rows)): ts.add(*cmd)
            t.setStyle(ts); story.append(t)
        return story

    # ── FIX 1: Section 2 — added Bench 3Y column ─────────────────────────────
    def _section_fund_performance(self, all_funds):
        story = [_p("2  Fund Performance vs Benchmark", 'section')]
        story.append(_p(
            "XIRR (Since Investment) is the annualised return your money has earned from your investment "
            "date in each fund. Benchmark returns are the relevant market index returns over trailing periods.",
            'footnote'))

        # 8 columns: Fund Name | Benchmark Index | WinRich Rank | Your XIRR | 3M | 1Y | 3Y | 5Y
        # Total must fit within 7.0 in (letter – 2×0.75 in margins)
        col_w = [1.9*inch, 1.3*inch, 0.7*inch, 0.7*inch, 0.6*inch, 0.6*inch, 0.6*inch, 0.6*inch]
        hdr   = [
            _p("Fund Name",        'th_left'),
            _p("Benchmark Index",  'th_left'),
            _p("WinRich\nRank",    'th_sm'),
            _p("Your\nXIRR",       'th_sm'),
            _p("Bench\n3M",        'th_sm'),
            _p("Bench\n1Y",        'th_sm'),
            _p("Bench\n3Y",        'th_sm'),
            _p("Bench\n5Y",        'th_sm'),
        ]
        rows = [hdr]
        for f in all_funds:
            name = f.get('name', '—')
            for sfx in [' (Erstwhile Kotak Standard Multicap Fund - Gr)',
                        ' (Erstwhile Kotak Emerging Equity Scheme)',
                        ' - Regular Plan - Growth', ' - Regular Growth',
                        ' - Regular Plan', ' Regular Growth',
                        ' - Growth', ' - Regular', ' Regular']:
                name = name.replace(sfx, '')
            rows.append([
                _p(name.strip(), 'cell_b'),
                _p(f.get('benchmark_index') or '—', 'cell_sm'),
                _p(str(f.get('winrich_rank') or 'N/A'), 'cell_bc'),
                _xirr_cell(f.get('xirr')),
                _ret(f.get('benchmark_return_3m'),  color_it=False),
                _ret(f.get('benchmark_return_1yr'), color_it=False),
                _ret(f.get('benchmark_return_3yr'), color_it=False),
                _ret(f.get('benchmark_return_5yr'), color_it=False),
            ])

        t  = Table(rows, colWidths=col_w, repeatRows=1)
        ts = _base_ts()
        ts.add('ALIGN', (0,0), (1,-1), 'LEFT')
        # Shade the four benchmark return columns (cols 4-7)
        ts.add('BACKGROUND', (4,1), (7,-1), BENCH_BG)
        for cmd in _alt_rows(len(rows)): ts.add(*cmd)
        t.setStyle(ts)
        story.append(t)
        story.append(_p(
            "<b>Your XIRR</b> = annualised return from your investment date. "
            "<b>Bench 3M / 1Y / 3Y / 5Y</b> = benchmark index return over trailing 3-month, 1-year, 3-year, and 5-year periods. "
            "<b>WinRich Rank</b> = fund's rank among category peers based on risk-adjusted performance. "
            "<b>Note on trailing-period XIRR:</b> The 3M / 1Y / 3Y / 5Y XIRR figures treat your total ongoing SIP amount as a "
            "single lump-sum investment made at the start of each period. This is a simplification — individual monthly SIP "
            "instalments are not modelled separately, so actual SIP returns may vary. "
            "<b>Past performance is not indicative of future returns.</b>",
            'footnote'))
        return story

    # ── FIX 2: Section 2a — value-weighted XIRR in total row ─────────────────
    def _section_fund_gains(self, fund_gains):
        story = [_p("2a  Fund-wise Gains — What Your Money Has Earned", 'sub_hdr')]
        story.append(_p(
            "This table shows the actual rupee gain in each fund along with the absolute return — "
            "total % earned from start to today, without adjusting for time. "
            "Folio Start Date is when your first investment in that fund was made.",
            'footnote'))

        hdr = [
            _p("Fund Name",          'th_left'),
            _p("Folio Start Date",   'th'),
            _p("Amount Invested",    'th'),
            _p("Current Value",      'th'),
            _p("Gain",               'th'),
            _p("Abs. Return",        'th'),
            _p("XIRR",               'th'),
        ]
        rows = [hdr]

        total_inv = total_cur = 0.0
        for f in fund_gains:
            g   = float(f.get('gain') or 0)
            gc  = GREEN if g >= 0 else RED
            inv = float(f.get('amount_invested') or 0)
            cur = float(f.get('current_value')   or 0)
            total_inv += inv; total_cur += cur

            fsd = f.get('folio_start_date') or f.get('FolioStartDate') or '—'
            if hasattr(fsd, 'strftime'):
                fsd = fsd.strftime('%d-%b-%Y')
            else:
                fsd = str(fsd).strip() or '—'

            rows.append([
                _p(f.get('name', '—'), 'cell_b'),
                _p(fsd, 'cell_c'),
                _p(_fmt_inr(inv), 'cell_r'),
                _p(_fmt_inr(cur), 'cell_r'),
                Paragraph(
                    f"<font color='{gc.hexval()}'>"
                    f"{'+' if g>=0 else ''}{_fmt_inr(abs(g))}</font>",
                    S['cell_r']),
                _ret(f.get('abs_return')),
                _xirr_cell(f.get('xirr')),
            ])

        tg   = total_cur - total_inv
        ta   = (tg / total_inv * 100) if total_inv > 0 else 0
        sign = '+' if tg >= 0 else ''

        # Value-weighted portfolio XIRR
        _port_xirr = None
        try:
            _num = sum(
                (float(f.get('xirr') or 0)) * (float(f.get('current_value') or 0))
                for f in fund_gains if f.get('xirr') is not None
            )
            _den = sum(float(f.get('current_value') or 0) for f in fund_gains)
            if _den > 0:
                _port_xirr = _num / _den
        except Exception:
            _port_xirr = None

        if _port_xirr is not None:
            _col = "#eef4ee" if _port_xirr >= 0 else '#cc0000'
            _xirr_total_cell = Paragraph(
                f"<font color='{_col}'><b>{_port_xirr:.2f}%</b></font>",
                S['th']
            )
        else:
            _xirr_total_cell = Paragraph("—", S['th'])

        rows.append([
            Paragraph("<b>Total Portfolio</b>",      S['th_left']),
            Paragraph("",                            S['th']),
            Paragraph(_fmt_inr(total_inv),           S['th']),
            Paragraph(_fmt_inr(total_cur),           S['th']),
            Paragraph(f"{sign}{_fmt_inr(abs(tg))}",  S['th']),
            Paragraph(f"<b>{sign}{ta:.2f}%</b>",    S['th']),
            _xirr_total_cell,
        ])

        col_w = [2.0*inch, 0.85*inch, 0.9*inch, 0.9*inch, 0.9*inch, 0.75*inch, 0.7*inch]
        t  = Table(rows, colWidths=col_w, repeatRows=1)
        ts = _base_ts()
        ts.add('ALIGN', (0,0), (1,-1), 'LEFT')
        ts.add('ALIGN', (2,1), (-1,-1), 'RIGHT')
        for cmd in _alt_rows(len(rows) - 1): ts.add(*cmd)
        last = len(rows) - 1
        ts.add('BACKGROUND', (0,last), (-1,last), NAVY)
        ts.add('LINEABOVE',  (0,last), (-1,last), 1.5, MID_BLUE)
        t.setStyle(ts)
        story.append(t)
        return story

    # ── FIX 3: Section 3 — improved pie chart readability ────────────────────
    def _build_amc_pie(self, amc_data):
        items = sorted(amc_data.items(),
                       key=lambda x: x[1]['pct'] if isinstance(x[1], dict) else x[1],
                       reverse=True)
        labels = [k for k, _ in items]
        sizes  = [
            v['pct'] if isinstance(v, dict) else v / sum(amc_data.values()) * 100
            for _, v in items
        ]
        vals   = [v['value'] if isinstance(v, dict) else 0 for _, v in items]
        n      = len(items)

        # Distinct, high-contrast palette (up to 10 AMCs)
        palette = [
            '#1a2a5e', '#c0392b', '#27ae60', '#e67e22', '#8e44ad',
            '#2980b9', '#16a085', '#d35400', '#2c3e50', '#7f8c8d',
        ]
        wedge_colors = [palette[i % len(palette)] for i in range(n)]

        # Dynamic figure height — grows with number of AMCs
        fig_h = max(3.2, 0.44 * n + 0.9)
        fig, (ax_pie, ax_leg) = plt.subplots(
            1, 2, figsize=(8.0, fig_h),
            gridspec_kw={'width_ratios': [1, 1.5]}
        )
        fig.patch.set_facecolor('white')

        wedges, _ = ax_pie.pie(
            sizes,
            colors=wedge_colors,
            startangle=90,
            wedgeprops=dict(width=0.62, edgecolor='white', linewidth=2.0),
            counterclock=False,
        )
        ax_pie.set_aspect('equal')
        ax_pie.set_title("AMC Concentration", fontsize=9, fontweight='bold',
                         color='#1a2a5e', pad=8)

        # Legend — coloured %, AMC name, Rs. value on second line
        ax_leg.axis('off')
        row_h  = 0.86 / max(n, 1)
        top    = 0.93
        box_w  = 0.055
        box_h  = min(row_h * 0.60, 0.065)
        gap    = 0.014

        for i, (lbl, sz, val) in enumerate(zip(labels, sizes, vals)):
            y_c = top - i * row_h

            # Colour swatch (rounded rectangle)
            ax_leg.add_patch(mpatches.FancyBboxPatch(
                (0.0, y_c - box_h / 2), box_w, box_h,
                boxstyle="round,pad=0.005",
                facecolor=wedge_colors[i], edgecolor='white', linewidth=0.5,
                transform=ax_leg.transAxes, clip_on=False,
            ))

            # Percentage — bold, same colour as wedge
            ax_leg.text(
                box_w + gap, y_c,
                f"{sz:.1f}%",
                transform=ax_leg.transAxes,
                fontsize=8.5, fontweight='bold',
                color=wedge_colors[i], va='center',
            )

            # Shortened AMC name — dark, readable
            short_lbl = (lbl
                         .replace(' Asset Management Company', '')
                         .replace(' Asset Management', '')
                         .replace(' Mutual Fund', '')
                         .replace(' AMC', ''))
            ax_leg.text(
                box_w + gap + 0.092, y_c,
                short_lbl,
                transform=ax_leg.transAxes,
                fontsize=8.0, color='#1a1a1a', va='center',
            )

            # Rs. value — smaller, grey, below the name line
            if val:
                ax_leg.text(
                    box_w + gap, y_c - row_h * 0.40,
                    f"Rs.{val:,.0f}",
                    transform=ax_leg.transAxes,
                    fontsize=6.8, color='#666666', va='center',
                )

        plt.tight_layout(pad=0.5)
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=160, bbox_inches='tight',
                    facecolor='white', edgecolor='none')
        buf.seek(0)
        plt.close(fig)
        return buf

    def _section_amc(self, amc_data):
        story = [_p("3  AMC Concentration", 'section')]
        story.append(_p(
            "This shows how your money is spread across fund houses (AMCs), "
            "based on current market value as on the report date.", 'cell_sm'))
        story.append(Spacer(1, 0.06*inch))
        # Dynamic height — matches the chart
        n_amcs  = len(amc_data)
        chart_h = max(2.9, 0.40 * n_amcs + 0.9)
        story.append(Image(self._build_amc_pie(amc_data),
                           width=PAGE_W*inch, height=chart_h*inch))
        return story

    def _page_cb(self, canvas, doc):
        _draw_footer(canvas, doc, self.company_name, website=self._website, email=self._email)

    def generate_report(self, portfolio_data, output_file):
        d = portfolio_data
        self.company_name = d.get('company_name', self.company_name)
        self._website     = d.get('website', '')
        self._email       = d.get('email',   '')
        logo_path         = d.get('logo_path', self.logo_path)

        doc = SimpleDocTemplate(output_file, pagesize=letter,
            topMargin=0.55*inch, bottomMargin=0.75*inch,
            leftMargin=0.75*inch, rightMargin=0.75*inch)
        page_w = letter[0] - 1.5*inch
        story  = []

        client_name = d.get('client_name','—')
        story.append(HeaderBanner(
            page_w=page_w, company=self.company_name,
            client_name=client_name, report_date=d.get('report_date',''),
            investment_start=d.get('investment_start',''),
            prepared_by=d.get('prepared_by', self.company_name),
            risk_profile=d.get('risk_profile',''),
            reference_benchmark=d.get('reference_benchmark',''),
            n_funds=d.get('n_funds',''), n_amcs=d.get('n_amcs',''),
            data_as_on=d.get('data_as_on', d.get('report_date','')),
            logo_path=logo_path,
        ))
        story.append(Spacer(1, 0.12*inch))
        story.extend(self._section_intro(client_name))
        story.append(Spacer(1, 0.1*inch))
        story.extend(self._section_snapshot(d))
        story.append(Spacer(1, 0.1*inch))

        if d.get('all_funds'):
            story.extend(self._section_fund_performance(d['all_funds']))
            story.append(Spacer(1, 0.08*inch))
        if d.get('fund_gains'):
            story.extend(self._section_fund_gains(d['fund_gains']))
            story.append(Spacer(1, 0.1*inch))
        if d.get('amc_concentration'):
            story.extend(self._section_amc(d['amc_concentration']))
            story.append(Spacer(1, 0.1*inch))

        commentary = d.get('commentary', [])
        if not commentary and d.get('_ai_commentary_raw'):
            commentary = _parse_commentary(d['_ai_commentary_raw'])
        if commentary:
            story.append(PageBreak())
            story.append(_p("Overall Performance Commentary", 'section'))
            story.append(HRFlowable(width='100%', thickness=1, color=RULE_COLOR, spaceAfter=6))
            for block in commentary:
                story.append(_p(block.get('heading',''), 'comment_h'))
                story.append(_p(block.get('body',''),    'comment_b'))

        story.append(Spacer(1, 0.12*inch))
        story.append(HRFlowable(width='100%', thickness=0.5, color=RULE_COLOR, spaceAfter=4))
        disclaimer = d.get('disclaimer',
            "This report is prepared by WinRich Professional Services for informational purposes only "
            "and does not constitute investment advice. Mutual fund investments are subject to market risks. "
            "Past performance is not indicative of future returns. Please read all scheme-related documents "
            "carefully before investing. XIRR figures are based on the client's investment statement. "
            "Benchmark returns are sourced from publicly available index data. "
            "WinRich Rankings are based on WinRich's proprietary research methodology.")
        story.append(Paragraph(f"<b>Disclaimer:</b> {disclaimer}", S['disclaimer']))
        contact = ' | '.join(filter(None, [self._email, self._website]))
        if contact:
            story.append(Spacer(1, 0.05*inch))
            story.append(_p(f"{self.company_name}  |  {contact}", 'cell_sm_c'))

        doc.build(story, onFirstPage=self._page_cb, onLaterPages=self._page_cb)
        return output_file


if __name__ == "__main__":
    sample = {
        'company_name':'WinRich Professional Services',
        'logo_path':'assets/winrich-logo.png',
        'client_name':'Niranjan Parthasarathy',
        'report_date':'March 04, 2026',
        'investment_start':'November 16, 2023',
        'prepared_by':'WinRich Research Desk',
        'risk_profile':'Balanced',
        'reference_benchmark':'Nifty 500 TRI',
        'n_funds':5, 'n_amcs':5,
        'data_as_on':'04-Mar-2026',
        'website':'www.winrich.in',
        'email':'support@winrich.in',
        'total_current_value':818668, 'total_invested':755962,
        'total_gain':62706, 'portfolio_xirr':6.15,
        'allocation_rows':[
            {'asset_class':'Equity','your_allocation':'75.88%','funds_in_portfolio':'Franklin Flexi Cap | ICICI Large Cap | Kotak Midcap'},
            {'asset_class':'Hybrid','your_allocation':'22.55%','funds_in_portfolio':'Edelweiss Balanced Advantage Fund'},
            {'asset_class':'Other', 'your_allocation':'2.68%', 'funds_in_portfolio':'Mirae Asset Gold Silver Passive FOF'},
            {'asset_class':'Debt',  'your_allocation':'0.00%', 'funds_in_portfolio':'—'},
        ],
        'all_funds':[
            {'name':'Franklin India Flexi Cap',
             'benchmark_index':'Nifty 500 TRI',       'winrich_rank':'7 / 39',
             'xirr':4.04,
             'benchmark_return_3m':2.1, 'benchmark_return_1yr':12.4,
             'benchmark_return_3yr':14.8, 'benchmark_return_5yr':17.2},
            {'name':'ICICI Pru Large Cap',
             'benchmark_index':'Nifty 100 TRI',       'winrich_rank':'1 / 31',
             'xirr':5.97,
             'benchmark_return_3m':1.8, 'benchmark_return_1yr':11.2,
             'benchmark_return_3yr':13.5, 'benchmark_return_5yr':15.9},
            {'name':'Kotak Midcap',
             'benchmark_index':'Nifty Midcap 150 TRI','winrich_rank':'3 / 29',
             'xirr':7.25,
             'benchmark_return_3m':3.2, 'benchmark_return_1yr':15.6,
             'benchmark_return_3yr':18.1, 'benchmark_return_5yr':22.4},
            {'name':'Edelweiss BAF',
             'benchmark_index':'Nifty 50 Hybrid 65:35 TRI','winrich_rank':'5 / 34',
             'xirr':4.98,
             'benchmark_return_3m':1.5, 'benchmark_return_1yr':10.1,
             'benchmark_return_3yr':11.8, 'benchmark_return_5yr':13.2},
            {'name':'Mirae Asset Gold Silver FOF',
             'benchmark_index':'Domestic Gold & Silver Price','winrich_rank':'No Rank',
             'xirr':271.34,
             'benchmark_return_3m':None, 'benchmark_return_1yr':None,
             'benchmark_return_3yr':None, 'benchmark_return_5yr':None},
        ],
        'fund_gains':[
            {'name':'Franklin India Flexi Cap',  'folio_start_date':'16-Nov-2023','amount_invested':185991,'current_value':196454,'gain':10464,'abs_return':5.63,'xirr':4.04},
            {'name':'ICICI Pru Large Cap',        'folio_start_date':'16-Nov-2023','amount_invested':226989,'current_value':246577,'gain':19588,'abs_return':8.63,'xirr':5.97},
            {'name':'Kotak Midcap',               'folio_start_date':'16-Nov-2023','amount_invested':154992,'current_value':169154,'gain':14162,'abs_return':9.14,'xirr':7.25},
            {'name':'Edelweiss BAF',              'folio_start_date':'16-Nov-2023','amount_invested':172991,'current_value':184517,'gain':11526,'abs_return':6.66,'xirr':4.98},
            {'name':'Mirae Asset Gold Silver FOF','folio_start_date':'12-Sep-2025','amount_invested':14999, 'current_value':21965, 'gain':6966, 'abs_return':46.44,'xirr':271.34},
        ],
        'amc_concentration':{
            'ICICI Prudential AMC':  {'value':246577,'pct':30.1},
            'Franklin Templeton AMC':{'value':196454,'pct':24.0},
            'Edelweiss AMC':         {'value':184517,'pct':22.5},
            'Kotak Mahindra AMC':    {'value':169154,'pct':20.7},
            'Mirae Asset AMC':       {'value':21965, 'pct':2.7},
        },
        'commentary':[
            {'heading':'How Your Portfolio Has Done Overall',
             'body':'Your portfolio is currently worth Rs.8,18,668 against a total investment of Rs.7,55,962, giving you a net gain of Rs.62,706. Your overall XIRR since you started investing in November 2023 is 6.15% per year.'},
            {'heading':'Your Equity Funds',
             'body':'The three equity funds have all earned more than their respective benchmark indices. Kotak Midcap leads with XIRR 7.25%, ahead of its Nifty Midcap 150 TRI 3Y benchmark return of 18.1%. ICICI Pru Large Cap is ranked 1st out of 31 funds in its category.'},
            {'heading':'Your Hybrid Fund',
             'body':'Edelweiss BAF has returned XIRR 4.98%, ahead of its benchmark. It serves as a steady, lower-volatility anchor in your portfolio.'},
            {'heading':'Key Observations',
             'body':'All four actively managed funds are ranked in the top quartile of their categories. Your portfolio is well diversified across 5 fund houses and every fund has outperformed its benchmark since your investment date.'},
        ],
    }
    print("Generating PDF...")
    out = MFPortfolioPDFGenerator().generate_report(sample, "/tmp/winrich_test.pdf")
    print(f"Done: {out}")