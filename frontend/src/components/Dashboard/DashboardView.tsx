import React, { useState } from 'react';
import { Activity, Phone, Clock, RefreshCw, X } from 'lucide-react';
import './DashboardView.css';

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

interface DashboardViewProps {
  filteredCalls: CallLog[];
  totalCalls: number;
  onRefresh: () => void;
}

export const DashboardView: React.FC<DashboardViewProps> = ({
  filteredCalls,
  totalCalls,
  onRefresh
}) => {
  const [selectedCall, setSelectedCall] = useState<CallLog | null>(null);

  // Helper to parse duration (handles "5m 20s" and "5:20" formats)
  const parseDurationSeconds = (duration: string | undefined): number => {
    if (!duration) return 0;
    // Handle "5m 20s" format
    const match = duration.match(/(\d+)\s*m\s*(\d+)?\s*s/);
    if (match) {
      return parseInt(match[1]) * 60 + (match[2] ? parseInt(match[2]) : 0);
    }
    // Handle "5:20" or "mm:ss" format
    const parts = duration.split(':').map(Number);
    if (parts.length === 2) {
      return (parts[0] || 0) * 60 + (parts[1] || 0);
    }
    return 0;
  };

  // Calculate real stats
  const bookings = filteredCalls.filter(c => c.status === 'booked').length;
  
  // Calculate average duration in seconds
  const totalSeconds = filteredCalls.reduce((acc, call) => {
    return acc + parseDurationSeconds(call.duration);
  }, 0);
  const avgDuration = filteredCalls.length > 0 
    ? Math.round(totalSeconds / filteredCalls.length) 
    : 0;

  const bookingRate = totalCalls > 0 
    ? Math.round((bookings / totalCalls) * 100) 
    : 0;

  const stats = [
    { title: 'TOTAL CALLS', value: totalCalls.toString(), subtitle: 'All time', icon: <Phone size={18} /> },
    { title: 'BOOKINGS MADE', value: bookings.toString(), subtitle: 'Today', icon: <Activity size={18} /> },
    { title: 'AVG DURATION', value: `${avgDuration}s`, subtitle: 'Seconds per call', icon: <Clock size={18} /> },
  ];

  return (
    <div className="main-container">
      <header className="dashboard-header">
        <h1 className="title">Dashboard</h1>
        <p className="subtitle">Real-time overview of your AI voice agent performance</p>
      </header>

      <section className="stats-grid">
        {stats.map((stat, index) => (
          <div key={index} className="stat-card">
            <h3 className="stat-title">{stat.title}</h3>
            <div className="stat-value">{stat.value}</div>
            <p className="stat-subtitle">{stat.subtitle}</p>
          </div>
        ))}
      </section>

      <section className="recent-calls-section">
        <div className="section-header">
          <h2 className="section-title">Recent Calls</h2>
          <button className="refresh-btn" onClick={onRefresh}>
            <RefreshCw size={14} /> Refresh
          </button>
        </div>

        <div className="table-container">
          <table className="calls-table">
            <thead>
              <tr>
                <th>DATE</th>
                <th>PHONE</th>
                <th>DURATION</th>
                <th>STATUS</th>
                <th>ACTIONS</th>
              </tr>
            </thead>
            <tbody>
              {filteredCalls.length > 0 ? (
                filteredCalls.map(call => (
                  <tr key={call.id}>
                    <td>{call.date}</td>
                    <td>{call.phone}</td>
                    <td>{call.duration}</td>
                    <td>
                      <span className={`status-tag ${call.status}`}>
                        {call.status.toUpperCase()}
                      </span>
                    </td>
                    <td>
                      <button className="action-btn" onClick={() => setSelectedCall(call)}>View</button>
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={5} className="loading-state">
                    No calls found for today.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      {selectedCall && (
        <div className="modal-overlay" onClick={() => setSelectedCall(null)}>
          <div className="modal-content" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h3>Call Details</h3>
              <button className="modal-close" onClick={() => setSelectedCall(null)}>
                <X size={18} />
              </button>
            </div>
            <div className="modal-body">
              <div className="detail-row">
                <span className="detail-label">Customer</span>
                <span className="detail-value">{selectedCall.customer || 'N/A'}</span>
              </div>
              <div className="detail-row">
                <span className="detail-label">Phone</span>
                <span className="detail-value">{selectedCall.phone}</span>
              </div>
              <div className="detail-row">
                <span className="detail-label">Date & Time</span>
                <span className="detail-value">{selectedCall.date} {selectedCall.time}</span>
              </div>
              <div className="detail-row">
                <span className="detail-label">Duration</span>
                <span className="detail-value">{selectedCall.duration}</span>
              </div>
              <div className="detail-row">
                <span className="detail-label">Status</span>
                <span className="detail-value">{selectedCall.status}</span>
              </div>
              <div className="detail-section">
                <span className="detail-label">Summary</span>
                <div className="summary-content">{selectedCall.summary || 'No summary available'}</div>
              </div>
              {selectedCall.transcript && (
                <div className="detail-section">
                  <span className="detail-label">Transcript</span>
                  <div className="summary-content transcript">{selectedCall.transcript}</div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
