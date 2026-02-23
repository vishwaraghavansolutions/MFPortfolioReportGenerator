"""
PDF Generator for Portfolio Reports
Simple, robust PDF generation with charts and tables
"""

from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, PageBreak, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use('Agg')  # Use non-GUI backend
import io
from datetime import datetime


class PortfolioPDFGenerator:
    """Generate portfolio reports with charts and tables"""
    
    def __init__(self, company_name="Winrich Professional Services"):
        self.company_name = company_name
        self.styles = getSampleStyleSheet()
        self._create_custom_styles()
    
    def _create_custom_styles(self):
        """Create custom paragraph styles"""
        self.styles.add(ParagraphStyle(
            name='CustomTitle',
            parent=self.styles['Heading1'],
            fontSize=24,
            textColor=colors.HexColor('#1a237e'),
            spaceAfter=30,
            alignment=TA_CENTER
        ))
        
        self.styles.add(ParagraphStyle(
            name='SectionHeader',
            parent=self.styles['Heading2'],
            fontSize=16,
            textColor=colors.HexColor('#283593'),
            spaceAfter=12,
            spaceBefore=20
        ))
    
    def _create_chart_asset_allocation(self, client_alloc, model_alloc):
        """Create asset allocation comparison chart"""
        fig, ax = plt.subplots(figsize=(10, 6))
        
        categories = list(client_alloc.keys())
        client_values = list(client_alloc.values())
        model_values = list(model_alloc.values())
        
        x = range(len(categories))
        width = 0.35
        
        bars1 = ax.bar([i - width/2 for i in x], client_values, width, 
                       label='Client Portfolio', color='#1976d2', alpha=0.8)
        bars2 = ax.bar([i + width/2 for i in x], model_values, width,
                       label='Illustrative Model', color='#64b5f6', alpha=0.8)
        
        ax.set_xlabel('Asset Class', fontsize=12, fontweight='bold')
        ax.set_ylabel('Allocation %', fontsize=12, fontweight='bold')
        ax.set_title('Asset Class Allocation - Client vs Model', 
                    fontsize=14, fontweight='bold', pad=20)
        ax.set_xticks(x)
        ax.set_xticklabels(categories)
        ax.legend(loc='upper right')
        ax.grid(axis='y', alpha=0.3)
        
        for bars in [bars1, bars2]:
            for bar in bars:
                height = bar.get_height()
                ax.text(bar.get_x() + bar.get_width()/2., height,
                       f'{height:.1f}%', ha='center', va='bottom', fontsize=9)
        
        plt.tight_layout()
        
        img_buffer = io.BytesIO()
        plt.savefig(img_buffer, format='png', bbox_inches='tight', dpi=150)
        img_buffer.seek(0)
        plt.close()
        
        return img_buffer
    
    def _create_chart_equity_performance(self, fund_data):
        """Create equity fund performance chart"""
        fig, ax = plt.subplots(figsize=(12, 7))
        
        funds = [f['name'] for f in fund_data]
        xirr_values = [f['xirr'] for f in fund_data]
        benchmark_values = [f.get('benchmark', 0) for f in fund_data]
        
        y_pos = range(len(funds))
        
        bars1 = ax.barh(y_pos, xirr_values, height=0.4, 
                       label='Fund XIRR', color='#1976d2', alpha=0.8)
        bars2 = ax.barh([i + 0.4 for i in y_pos], benchmark_values, height=0.4,
                       label='Category Index', color='#90caf9', alpha=0.8)
        
        ax.set_yticks([i + 0.2 for i in y_pos])
        ax.set_yticklabels(funds, fontsize=9)
        ax.set_xlabel('XIRR / Return %', fontsize=12, fontweight='bold')
        ax.set_title('Equity Fund Performance', fontsize=14, fontweight='bold', pad=20)
        ax.legend(loc='lower right')
        ax.grid(axis='x', alpha=0.3)
        
        for bars in [bars1, bars2]:
            for bar in bars:
                width = bar.get_width()
                if width > 0:
                    ax.text(width, bar.get_y() + bar.get_height()/2.,
                           f'{width:.1f}%', ha='left', va='center', fontsize=8)
        
        plt.tight_layout()
        
        img_buffer = io.BytesIO()
        plt.savefig(img_buffer, format='png', bbox_inches='tight', dpi=150)
        img_buffer.seek(0)
        plt.close()
        
        return img_buffer
    
    def _create_chart_hybrid_performance(self, hybrid_data):
        """Create hybrid fund performance chart"""
        fig, ax = plt.subplots(figsize=(10, 6))
        
        funds = [f['name'] for f in hybrid_data]
        xirr_values = [f['xirr'] for f in hybrid_data]
        
        colors_list = ['#1976d2', '#42a5f5', '#64b5f6', '#90caf9']
        bars = ax.bar(funds, xirr_values, color=colors_list[:len(funds)], alpha=0.8)
        
        ax.set_ylabel('XIRR %', fontsize=12, fontweight='bold')
        ax.set_title('Hybrid Fund Performance', fontsize=14, fontweight='bold', pad=20)
        plt.xticks(rotation=15, ha='right')
        ax.grid(axis='y', alpha=0.3)
        
        for bar in bars:
            height = bar.get_height()
            ax.text(bar.get_x() + bar.get_width()/2., height,
                   f'{height:.1f}%', ha='center', va='bottom', fontsize=10, fontweight='bold')
        
        plt.tight_layout()
        
        img_buffer = io.BytesIO()
        plt.savefig(img_buffer, format='png', bbox_inches='tight', dpi=150)
        img_buffer.seek(0)
        plt.close()
        
        return img_buffer
    
    def _create_chart_amc_concentration(self, amc_data):
        """Create AMC concentration donut chart"""
        fig, ax = plt.subplots(figsize=(10, 8))
        
        labels = list(amc_data.keys())
        sizes = list(amc_data.values())
        
        colors_list = ['#1976d2', '#e53935', '#43a047', '#fb8c00', '#8e24aa',
                      '#00acc1', '#d81b60', '#fdd835', '#5e35b1', '#c0ca33']
        
        wedges, texts, autotexts = ax.pie(sizes, labels=labels, autopct='%1.1f%%',
                                           colors=colors_list[:len(labels)],
                                           startangle=90, pctdistance=0.85)
        
        centre_circle = plt.Circle((0, 0), 0.70, fc='white')
        ax.add_artist(centre_circle)
        
        ax.set_title('AMC Concentration', fontsize=14, fontweight='bold', pad=20)
        
        for text in texts:
            text.set_fontsize(10)
        for autotext in autotexts:
            autotext.set_color('white')
            autotext.set_fontweight('bold')
            autotext.set_fontsize(9)
        
        plt.tight_layout()
        
        img_buffer = io.BytesIO()
        plt.savefig(img_buffer, format='png', bbox_inches='tight', dpi=150)
        img_buffer.seek(0)
        plt.close()
        
        return img_buffer
    
    def _create_table(self, data, col_widths=None):
        """Create a styled table"""
        if not col_widths:
            col_widths = [2*inch] * len(data[0])
        
        table = Table(data, colWidths=col_widths)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1976d2')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('ALIGN', (0, 0), (0, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 11),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.white),
            ('GRID', (0, 0), (-1, -1), 1, colors.grey),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f5f5f5')]),
        ]))
        
        return table
    
    def generate_portfolio_report(self, portfolio_data, output_file):
        """
        Generate portfolio report PDF
        
        Args:
            portfolio_data: dict with portfolio information
            output_file: path to save PDF
        """
        doc = SimpleDocTemplate(output_file, pagesize=letter,
                               topMargin=0.75*inch, bottomMargin=0.75*inch,
                               leftMargin=0.75*inch, rightMargin=0.75*inch)
        
        story = []
        
        # Title
        title = Paragraph("Portfolio Analysis Report", self.styles['CustomTitle'])
        story.append(title)
        story.append(Spacer(1, 0.2*inch))
        
        # Metadata
        meta_data = [
            f"<b>Client Name:</b> {portfolio_data.get('client_name', 'N/A')}",
            f"<b>Report Date:</b> {portfolio_data.get('report_date', datetime.now().strftime('%B %d, %Y'))}",
            f"<b>Prepared By:</b> {self.company_name}"
        ]
        
        for meta in meta_data:
            story.append(Paragraph(meta, self.styles['Normal']))
        
        story.append(Spacer(1, 0.3*inch))
        
        # Summary Table (if provided)
        if portfolio_data.get('summary'):
            story.append(Paragraph("Portfolio Summary", self.styles['SectionHeader']))
            summary_data = [['Metric', 'Value']]
            for key, value in portfolio_data['summary'].items():
                summary_data.append([key, str(value)])
            summary_table = self._create_table(summary_data, [3.5*inch, 3*inch])
            story.append(summary_table)
            story.append(Spacer(1, 0.4*inch))
        
        # Asset Allocation
        if portfolio_data.get('client_allocation') and portfolio_data.get('model_allocation'):
            story.append(Paragraph("Asset Allocation", self.styles['SectionHeader']))
            chart = self._create_chart_asset_allocation(
                portfolio_data['client_allocation'],
                portfolio_data['model_allocation']
            )
            story.append(Image(chart, width=6.5*inch, height=3.9*inch))
            story.append(Spacer(1, 0.3*inch))
            
            # Allocation table
            alloc_data = [['Asset Class', 'Client %', 'Model %', 'Variance']]
            for asset_class in portfolio_data['client_allocation'].keys():
                client_val = portfolio_data['client_allocation'][asset_class]
                model_val = portfolio_data['model_allocation'][asset_class]
                variance = client_val - model_val
                alloc_data.append([
                    asset_class,
                    f"{client_val:.1f}%",
                    f"{model_val:.1f}%",
                    f"{variance:+.1f}%"
                ])
            alloc_table = self._create_table(alloc_data, [2*inch, 1.5*inch, 1.5*inch, 1.5*inch])
            story.append(alloc_table)
            story.append(PageBreak())
        
        # Equity Fundsif portfolio_data.get('equity_funds'):
        story.append(Paragraph("Equity Fund Performance", self.styles['SectionHeader']))
        chart = self._create_chart_equity_performance(portfolio_data['equity_funds'])
        story.append(Image(chart, width=6.5*inch, height=4.5*inch))
        story.append(Spacer(1, 0.3*inch))

        # ── Styles for wrapping text inside table cells ───────────────────
        fund_name_style = ParagraphStyle(
            "FundName",
            fontName="Helvetica",
            fontSize=7.5,
            leading=10,
            wordWrap="LTR",
        )
        index_name_style = ParagraphStyle(
            "IndexName",
            fontName="Helvetica",
            fontSize=7,
            leading=10,
            textColor=colors.HexColor("#4a4a4a"),
            wordWrap="LTR",
        )
        header_style = ParagraphStyle(
            "TableHeader",
            fontName="Helvetica-Bold",
            fontSize=8,
            leading=10,
            textColor=colors.white,
        )
        diff_pos_style = ParagraphStyle(
            "DiffPos",
            fontName="Helvetica-Bold",
            fontSize=7.5,
            leading=10,
            textColor=colors.HexColor("#1a7a1a"),
        )
        diff_neg_style = ParagraphStyle(
            "DiffNeg",
            fontName="Helvetica-Bold",
            fontSize=7.5,
            leading=10,
            textColor=colors.HexColor("#cc0000"),
        )

        # ── Headers ───────────────────────────────────────────────────────
        equity_data = [[
            Paragraph("Fund Name",       header_style),
            Paragraph("Benchmark Index", header_style),
            Paragraph("XIRR %",          header_style),
            Paragraph("3M %",            header_style),
            Paragraph("1Y %",            header_style),
            Paragraph("Difference",      header_style),
        ]]

        # ── Rows ──────────────────────────────────────────────────────────
        for fund in portfolio_data['equity_funds']:
            xirr        = fund.get('xirr', 0) or 0
            bench_1yr   = fund.get('benchmark_return_1yr', None)
            bench_3m    = fund.get('benchmark_return_3m',  None)
            index_name  = fund.get('benchmark_index', '—')
            bench       = bench_1yr or fund.get('benchmark', 0) or 0
            diff        = xirr - bench

            bench_3m_str  = f"{bench_3m:.2f}%"  if bench_3m  is not None else "—"
            bench_1yr_str = f"{bench_1yr:.2f}%" if bench_1yr is not None else "—"
            diff_str      = f"{diff:+.2f}%"
            diff_style    = diff_pos_style if diff >= 0 else diff_neg_style

            equity_data.append([
                Paragraph(fund['name'],  fund_name_style),
                Paragraph(index_name,    index_name_style),
                f"{xirr:.2f}%",
                bench_3m_str,
                bench_1yr_str,
                Paragraph(diff_str,      diff_style),
            ])

        # col widths: Fund Name | Index | XIRR | 3M | 1Y | Diff
        equity_table = self._create_table(
            equity_data,
            [2.2*inch, 1.6*inch, 0.75*inch, 0.75*inch, 0.75*inch, 0.75*inch]
        )
        story.append(equity_table)
        story.append(Spacer(1, 0.4*inch))
        
        # Hybrid Funds
        if portfolio_data.get('hybrid_funds'):
            story.append(Paragraph("Hybrid Fund Performance", self.styles['SectionHeader']))
            chart = self._create_chart_hybrid_performance(portfolio_data['hybrid_funds'])
            story.append(Image(chart, width=6.5*inch, height=3.9*inch))
            story.append(Spacer(1, 0.3*inch))
            
            # Hybrid table
            hybrid_data = [['Fund Name', 'XIRR %']]
            for fund in portfolio_data['hybrid_funds']:
                hybrid_data.append([fund['name'][:55], f"{fund['xirr']:.2f}%"])
            hybrid_table = self._create_table(hybrid_data, [4.5*inch, 1.5*inch])
            story.append(hybrid_table)
            story.append(PageBreak())
        
        # AMC Concentration
        if portfolio_data.get('amc_concentration'):
            story.append(Paragraph("AMC Concentration", self.styles['SectionHeader']))
            chart = self._create_chart_amc_concentration(portfolio_data['amc_concentration'])
            story.append(Image(chart, width=6.5*inch, height=5.2*inch))
            story.append(Spacer(1, 0.3*inch))
            
            # AMC table
            total_funds = sum(portfolio_data['amc_concentration'].values())
            amc_data = [['AMC Name', 'No. of Funds', 'Percentage']]
            for amc, count in sorted(portfolio_data['amc_concentration'].items(), 
                                     key=lambda x: x[1], reverse=True):
                pct = (count / total_funds) * 100
                amc_data.append([amc, str(count), f"{pct:.1f}%"])
            amc_data.append(['TOTAL', str(total_funds), '100.0%'])
            amc_table = self._create_table(amc_data, [3.5*inch, 1.5*inch, 1.5*inch])
            story.append(amc_table)
        
        story.append(Spacer(1, 0.4*inch))
        
        # Disclaimer (optional)
        story.append(Paragraph("Standard Compliance Disclaimer", self.styles['SectionHeader']))
        disclaimer_text = portfolio_data.get('disclaimer', 
            "Investment in securities market is subject to market risks. Read all the related documents carefully before investing. "
            "The securities/funds quoted are for illustration only and are not recommendatory. Past performance is not indicative of future results. "
            "This report is for informational purposes only."
        )
        disclaimer = Paragraph(disclaimer_text, self.styles['Normal'])
        story.append(disclaimer)

        # Build PDF
        doc.build(story)
        return output_file


# Standalone test
if __name__ == "__main__":
    generator = PortfolioPDFGenerator("Test Company")
    
    sample_data = {
        'client_name': 'John Doe',
        'report_date': 'February 15, 2026',
        'summary': {
            'Total Value': '₹50,00,000',
            'Total Funds': '14',
            'Equity Allocation': '70.63%'
        },
        'client_allocation': {'Equity': 70.63, 'Hybrid': 21.90, 'Debt': 7.48},
        'model_allocation': {'Equity': 65.0, 'Hybrid': 20.0, 'Debt': 15.0},
        'equity_funds': [
            {'name': 'ICICI Large Cap', 'xirr': 18.14, 'benchmark': 16.5},
            {'name': 'Parag Parikh Flexi', 'xirr': 14.83, 'benchmark': 13.7}
        ],
        'hybrid_funds': [
            {'name': 'ICICI BAF', 'xirr': 13.3},
            {'name': 'SBI Equity Hybrid', 'xirr': 13.7}
        ],
        'amc_concentration': {'ICICI': 3, 'SBI': 2, 'Parag Parikh': 1}
    }
    
    output = generator.generate_portfolio_report(sample_data, "test_report.pdf")
    print(f"Generated: {output}")