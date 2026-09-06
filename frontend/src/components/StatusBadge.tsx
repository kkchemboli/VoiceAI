import React from 'react';

interface StatusBadgeProps {
  status: string;
  type?: 'completed' | 'missed' | 'incoming' | 'neutral';
}

export const StatusBadge: React.FC<StatusBadgeProps> = ({ status, type }) => {
  const getBadgeClass = () => {
    switch (type || status.toLowerCase()) {
      case 'completed': return 'status-badge completed';
      case 'missed': return 'status-badge';
      case 'incoming': return 'status-badge incoming';
      case 'positive': return 'status-badge completed';
      case 'neutral': return 'status-badge incoming';
      default: return 'status-badge';
    }
  };

  return <span className={getBadgeClass()}>{status}</span>;
};
