import React from 'react';
import { Clock, CheckCircle2, XCircle, Loader2, RefreshCw } from 'lucide-react';
import { QueueItem } from '../../services/outboundService';

interface CallQueueStatusProps {
  queue: QueueItem[];
  isLoading: boolean;
  onRefresh: () => void;
}

export const CallQueueStatus: React.FC<CallQueueStatusProps> = ({
  queue,
  isLoading,
  onRefresh
}) => {
  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'success': return <CheckCircle2 size={16} className="text-green" />;
      case 'failed': return <XCircle size={16} className="text-red" />;
      case 'calling': return <Loader2 size={16} className="animate-spin text-blue" />;
      default: return <Clock size={16} className="text-gray" />;
    }
  };

  return (
    <div className="outbound-card animate-slide-up">
      <div className="card-header">
        <Clock className="header-icon" size={20} />
        <h3>Call Queue Status</h3>
        <button 
          className={`refresh-icon-btn ${isLoading ? 'loading' : ''}`} 
          onClick={onRefresh}
          disabled={isLoading}
        >
          <RefreshCw size={16} />
        </button>
      </div>

      <div className="status-table-wrapper">
        <table className="status-table">
          <thead>
            <tr>
              <th>PHONE</th>
              <th>STATUS</th>
              <th>TIME</th>
            </tr>
          </thead>
          <tbody>
            {queue.length > 0 ? (
              queue.map((item) => (
                <tr key={item.id}>
                  <td>{item.phone}</td>
                  <td>
                    <div className="status-cell">
                      {getStatusIcon(item.status)}
                      <span className={`status-text ${item.status}`}>{item.status.toUpperCase()}</span>
                    </div>
                  </td>
              <td>{new Date(item.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</td>
            </tr>
              ))
            ) : (
<tr>
              <td colSpan={3} className="empty-row">No recent activity found.</td>
            </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};
