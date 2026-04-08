import React, { useState } from 'react';
import { RefreshCw, FileText, X } from 'lucide-react';
import './CallLogsView.css';

interface CallLog {
  id: number;
  date: string;
  time: string;
  phone: string;
  customer: string;
  duration: string;
  status: string;
  summary: string;
  transcript?: string;
}

interface CallLogsViewProps {
  logs: CallLog[];
  onRefresh: () => void;
}

export const CallLogsView: React.FC<CallLogsViewProps> = ({ logs, onRefresh }) => {
  const [selectedLog, setSelectedLog] = useState<CallLog | null>(null);

  return (
    <div className="main-container">
      <header className="view-header">
        <div className="header-content">
          <div>
            <h1 className="title">Call Logs</h1>
            <p className="subtitle">Full history of all incoming calls and transcripts</p>
          </div>
          <button className="refresh-btn" onClick={onRefresh}>
            <RefreshCw size={14} /> Refresh
          </button>
        </div>
      </header>

      <div className="logs-card">
        <div className="table-container">
          <table className="logs-table">
            <thead>
              <tr>
                <th>DATE & TIME</th>
                <th>PHONE</th>
                <th>DURATION</th>
                <th>STATUS</th>
                <th>SUMMARY</th>
                <th>ACTIONS</th>
              </tr>
            </thead>
            <tbody>
              {logs.map(log => (
                <tr key={log.id}>
                  <td className="text-secondary">{log.date} {log.time}</td>
                  <td className="text-primary">{log.phone}</td>
                  <td className="text-primary">{log.duration}</td>
                  <td>
                    <span className={`status-pill ${log.status}`}>
                      {log.status?.toLowerCase() === 'booked' && '✓ '}
                      {log.status.charAt(0).toUpperCase() + log.status.slice(1)}
                    </span>
                  </td>
                  <td className="text-secondary summary-cell" onClick={() => setSelectedLog(log)}>
                    {log.summary}
                  </td>
                  <td>
                    <div className="action-group">
                      <button className="icon-action-btn" onClick={() => setSelectedLog(log)}>
                        <FileText size={14} /> View
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {selectedLog && (
        <div className="modal-overlay" onClick={() => setSelectedLog(null)}>
          <div className="modal-content" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h3>Call Details</h3>
              <button className="modal-close" onClick={() => setSelectedLog(null)}>
                <X size={18} />
              </button>
            </div>
            <div className="modal-body">
              <div className="detail-row">
                <span className="detail-label">Customer</span>
                <span className="detail-value">{selectedLog.customer || 'N/A'}</span>
              </div>
              <div className="detail-row">
                <span className="detail-label">Phone</span>
                <span className="detail-value">{selectedLog.phone}</span>
              </div>
              <div className="detail-row">
                <span className="detail-label">Date & Time</span>
                <span className="detail-value">{selectedLog.date} {selectedLog.time}</span>
              </div>
              <div className="detail-row">
                <span className="detail-label">Duration</span>
                <span className="detail-value">{selectedLog.duration}</span>
              </div>
              <div className="detail-row">
                <span className="detail-label">Status</span>
                <span className="detail-value">{selectedLog.status}</span>
              </div>
              <div className="detail-section">
                <span className="detail-label">Summary</span>
                <div className="summary-content">{selectedLog.summary || 'No summary available'}</div>
              </div>
              {selectedLog.transcript && (
                <div className="detail-section">
                  <span className="detail-label">Transcript</span>
                  <div className="summary-content transcript">{selectedLog.transcript}</div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
