import { TrendingUp, TrendingDown } from 'lucide-react';
import './KpiCard.css';

interface KpiCardProps {
  title: string;
  value: string;
  subtitle?: string;
  change?: number | null;
}

export default function KpiCard({ title, value, subtitle, change }: KpiCardProps) {
  const isPositive = change && change > 0;
  const isNegative = change && change < 0;

  return (
    <div className="kpi-card card animate-slide-up">
      <h3 className="kpi-title">{title}</h3>
      <div className="kpi-value">{value}</div>
      {subtitle && <div className="kpi-subtitle" style={{ fontSize: '0.9rem', color: 'var(--text-muted)', marginTop: '-5px', marginBottom: '10px' }}>{subtitle}</div>}
      {change !== undefined && change !== null && (
        <div className={`kpi-change ${isPositive ? 'positive' : isNegative ? 'negative' : ''}`}>
          {isPositive ? <TrendingUp size={16} /> : isNegative ? <TrendingDown size={16} /> : null}
          <span>{Math.abs(change)}% к пред. периоду</span>
        </div>
      )}
    </div>
  );
}
