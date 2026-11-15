from datetime import datetime, date, timedelta
from typing import Optional, Tuple


def parse_date(date_str: Optional[str]) -> Optional[date]:
    """Parse date string in various formats."""
    if not date_str:
        return None
    
    formats = ['%Y-%m-%d', '%d.%m.%Y', '%d/%m/%Y', '%Y/%m/%d', '%d-%m-%Y']
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None


def get_default_date_range() -> Tuple[str, str]:
    """Get default date range (last 6 months)."""
    end = date.today()
    start = end - timedelta(days=180)
    return start.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d')


def format_number(value: float, prefix: str = '', suffix: str = '') -> str:
    """Format number for display."""
    if abs(value) >= 1_000_000:
        return f"{prefix}{value/1_000_000:.1f}M{suffix}"
    elif abs(value) >= 1_000:
        return f"{prefix}{value/1_000:.1f}K{suffix}"
    else:
        return f"{prefix}{value:.2f}{suffix}"


def calculate_period_bounds(period: str) -> Tuple[str, str]:
    """Calculate date bounds for named periods."""
    today = date.today()
    
    if period == 'today':
        return today.strftime('%Y-%m-%d'), today.strftime('%Y-%m-%d')
    elif period == 'week':
        start = today - timedelta(days=today.weekday())
        return start.strftime('%Y-%m-%d'), today.strftime('%Y-%m-%d')
    elif period == 'month':
        start = today.replace(day=1)
        return start.strftime('%Y-%m-%d'), today.strftime('%Y-%m-%d')
    elif period == 'quarter':
        quarter_start_month = ((today.month - 1) // 3) * 3 + 1
        start = today.replace(month=quarter_start_month, day=1)
        return start.strftime('%Y-%m-%d'), today.strftime('%Y-%m-%d')
    elif period == 'year':
        start = today.replace(month=1, day=1)
        return start.strftime('%Y-%m-%d'), today.strftime('%Y-%m-%d')
    else:  # all
        return '2000-01-01', '2099-12-31'
