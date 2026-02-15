"""
Utility functions for PDF generation
Helper functions for formatting, calculations, and data processing
"""


def format_in_indian(number):
    """
    Format number in Indian numbering system (lakhs and crores)
    
    Args:
        number: int or float to format
        
    Returns:
        str: Formatted string like "1,23,456" or "12,34,567"
        
    Examples:
        format_in_indian(1234567) -> "12,34,567"
        format_in_indian(123456) -> "1,23,456"
        format_in_indian(1234) -> "1,234"
    """
    if number is None:
        return "0"
    
    # Convert to string and handle negative numbers
    is_negative = True if int(number) < 0 else False
    number = abs(number)
    
    # Handle decimal numbers
    if isinstance(number, float):
        decimal_part = str(number).split('.')
        if len(decimal_part) == 2:
            integer_part = int(decimal_part[0])
            decimal_str = decimal_part[1]
            formatted = _format_integer_indian(integer_part)
            return f"{'-' if is_negative else ''}{formatted}.{decimal_str}"
    
    # Format integer part
    formatted = _format_integer_indian(int(number))
    return f"{'-' if is_negative else ''}{formatted}"


def _format_integer_indian(num):
    """Helper function to format integer part in Indian style"""
    s = str(num)
    if len(s) <= 3:
        return s
    
    # Split into last 3 digits and the rest
    last_three = s[-3:]
    remaining = s[:-3]
    
    # Add commas every 2 digits for the remaining part
    formatted_remaining = ''
    while remaining:
        if len(remaining) <= 2:
            formatted_remaining = remaining + formatted_remaining
            break
        formatted_remaining = ',' + remaining[-2:] + formatted_remaining
        remaining = remaining[:-2]
    
    return formatted_remaining + ',' + last_three


def format_currency_indian(amount, prefix='₹'):
    """
    Format currency in Indian style with ₹ symbol
    
    Args:
        amount: Number to format
        prefix: Currency symbol (default: ₹)
        
    Returns:
        str: Formatted currency like "₹1,23,456"
    """
    formatted = format_in_indian(amount)
    return f"{prefix}{formatted}"


def format_lakhs(number):
    """
    Format number in lakhs
    
    Args:
        number: Number to format
        
    Returns:
        str: Formatted string like "12.35 L" or "1.23 Cr"
    """
    if number is None or number == 0:
        return "0"
    
    abs_num = abs(number)
    sign = '-' if number < 0 else ''
    
    if abs_num >= 10000000:  # 1 crore
        return f"{sign}{abs_num/10000000:.2f} Cr"
    elif abs_num >= 100000:  # 1 lakh
        return f"{sign}{abs_num/100000:.2f} L"
    elif abs_num >= 1000:  # 1 thousand
        return f"{sign}{abs_num/1000:.2f} K"
    else:
        return f"{sign}{abs_num:.2f}"


def format_percentage(value, decimal_places=2):
    """
    Format number as percentage
    
    Args:
        value: Number to format
        decimal_places: Number of decimal places (default: 2)
        
    Returns:
        str: Formatted percentage like "15.23%"
    """
    if value is None:
        return "0.00%"
    return f"{value:.{decimal_places}f}%"


def calculate_xirr_simple(cashflows, dates=None):
    """
    Simple XIRR calculation approximation
    Note: For accurate XIRR, use the xirr library
    
    Args:
        cashflows: List of cashflow amounts (negative for investments, positive for returns)
        dates: List of dates (optional)
        
    Returns:
        float: Approximate XIRR percentage
    """
    # This is a simplified version
    # For production, use: from xirr import xirr
    total_invested = sum([cf for cf in cashflows if cf < 0])
    total_returned = sum([cf for cf in cashflows if cf > 0])
    
    if total_invested == 0:
        return 0
    
    # Simple return calculation
    simple_return = ((total_returned / abs(total_invested)) - 1) * 100
    return simple_return


def format_fund_name(name, max_length=50):
    """
    Shorten fund name if too long
    
    Args:
        name: Full fund name
        max_length: Maximum length
        
    Returns:
        str: Shortened name if needed
    """
    if len(name) <= max_length:
        return name
    return name[:max_length-3] + "..."


def get_color_for_performance(value, threshold_positive=0, threshold_negative=0):
    """
    Get color based on performance value
    
    Args:
        value: Performance value (e.g., return %)
        threshold_positive: Threshold for positive (green)
        threshold_negative: Threshold for negative (red)
        
    Returns:
        str: Hex color code
    """
    if value > threshold_positive:
        return '#4caf50'  # Green
    elif value < threshold_negative:
        return '#f44336'  # Red
    else:
        return '#ff9800'  # Orange


def calculate_portfolio_metrics(holdings):
    """
    Calculate portfolio-level metrics from holdings
    
    Args:
        holdings: List of dicts with 'amount' and 'allocation' keys
        
    Returns:
        dict: Portfolio metrics
    """
    total_value = sum([h.get('amount', 0) for h in holdings])
    num_funds = len(holdings)
    
    # Calculate weighted average return if available
    weighted_return = 0
    if all('return' in h and 'amount' in h for h in holdings):
        weighted_return = sum([h['return'] * h['amount'] for h in holdings]) / total_value if total_value > 0 else 0
    
    return {
        'total_value': total_value,
        'num_funds': num_funds,
        'weighted_return': weighted_return,
        'average_allocation': total_value / num_funds if num_funds > 0 else 0
    }


def validate_portfolio_data(data):
    """
    Validate portfolio data structure
    
    Args:
        data: Portfolio data dict
        
    Returns:
        tuple: (is_valid: bool, errors: list)
    """
    errors = []
    
    # Check required fields
    required_fields = ['client_name', 'report_date', 'client_allocation']
    for field in required_fields:
        if field not in data:
            errors.append(f"Missing required field: {field}")
    
    # Check allocation adds to 100%
    if 'client_allocation' in data:
        total = sum(data['client_allocation'].values())
        if abs(total - 100) > 0.01:
            errors.append(f"Client allocation sums to {total}%, should be 100%")
    
    # Check funds data
    if 'equity_funds' in data:
        for i, fund in enumerate(data['equity_funds']):
            if 'name' not in fund:
                errors.append(f"Equity fund {i} missing 'name'")
            if 'xirr' not in fund:
                errors.append(f"Equity fund {i} missing 'xirr'")
    
    return len(errors) == 0, errors


def create_summary_dict(portfolio_data):
    """
    Create summary dictionary from portfolio data
    
    Args:
        portfolio_data: Full portfolio data
        
    Returns:
        dict: Summary data for table
    """
    summary = {}
    
    if 'client_name' in portfolio_data:
        summary['Client Name'] = portfolio_data['client_name']
    
    if 'report_date' in portfolio_data:
        summary['Report Date'] = portfolio_data['report_date']
    
    if 'client_allocation' in portfolio_data:
        alloc = portfolio_data['client_allocation']
        summary['Equity Allocation'] = format_percentage(alloc.get('Equity', 0))
        summary['Hybrid Allocation'] = format_percentage(alloc.get('Balance (Hybrid)', 0))
        summary['Debt Allocation'] = format_percentage(alloc.get('Debt', 0))
    
    if 'equity_funds' in portfolio_data and 'hybrid_funds' in portfolio_data:
        num_equity = len(portfolio_data['equity_funds'])
        num_hybrid = len(portfolio_data['hybrid_funds'])
        summary['Total Funds'] = f"{num_equity + num_hybrid} ({num_equity} equity + {num_hybrid} hybrid)"
    
    if 'amc_concentration' in portfolio_data:
        summary['Number of AMCs'] = str(len(portfolio_data['amc_concentration']))
    
    return summary


# Example usage
if __name__ == "__main__":
    # Test formatting functions
    print(f"Indian format: {format_in_indian(1234567)}")
    print(f"Currency: {format_currency_indian(1234567)}")
    print(f"Lakhs: {format_lakhs(1234567)}")
    print(f"Percentage: {format_percentage(15.678)}")
    
    # Test validation
    sample_data = {
        'client_name': 'Test Client',
        'report_date': '2026-02-15',
        'client_allocation': {'Equity': 70, 'Balance (Hybrid)': 20, 'Debt': 10}
    }
    is_valid, errors = validate_portfolio_data(sample_data)
    print(f"Validation: {is_valid}, Errors: {errors}")