import React from 'react';
import { ArrowUpRight } from 'lucide-react';
import { StatusBadge } from './StatusBadge';

interface LogItemProps {
  title: string;
  subtitle: string;
  extra?: string;
  status?: string;
  statusType?: 'completed' | 'missed' | 'incoming' | 'neutral';
  isMessage?: boolean;
}

export const LogItem: React.FC<LogItemProps> = ({ 
  title, 
  subtitle, 
  extra, 
  status, 
  statusType,
  isMessage 
}) => (
  <div className="log-item">
    <div className="log-info">
      <h4>{title}</h4>
      <p>{subtitle}</p>
      {extra && <p style={{ color: '#d4d4d8', marginTop: '0.25rem' }}>"{extra}"</p>}
    </div>
    {status ? (
      <StatusBadge status={status} type={statusType} />
    ) : isMessage ? (
      <ArrowUpRight size={16} style={{ color: '#71717a' }} />
    ) : null}
  </div>
);
