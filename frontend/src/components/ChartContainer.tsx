import type { ReactNode } from 'react';
import './ChartContainer.css';

interface ChartContainerProps {
  title: string;
  children: ReactNode;
  loading?: boolean;
  className?: string;
}

export default function ChartContainer({ title, children, loading = false, className = '' }: ChartContainerProps) {
  return (
    <div className={`chart-container card animate-slide-up ${className}`}>
      <h3 className="chart-title">{title}</h3>
      <div className="chart-content">
        {loading ? (
          <div className="chart-skeleton">
            <div className="spinner"></div>
          </div>
        ) : (
          children
        )}
      </div>
    </div>
  );
}
