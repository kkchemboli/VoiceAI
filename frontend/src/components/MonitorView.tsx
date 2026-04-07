import React from 'react';
import { Radio, Activity, Users } from 'lucide-react';
import { InfoCard } from './InfoCard';
import { LogItem } from './LogItem';

interface MonitorViewProps {
  liveCalls: any[];
}

export const MonitorView: React.FC<MonitorViewProps> = ({ liveCalls }) => (
  <main className="main-container">
    <section>
      <div className="tag"><Radio size={16} /> Live Feed</div>
      <h1 className="title">Monitoring</h1>
      <p className="subtitle">Real-time oversight of active AI interactions and performance.</p>
      <div className="info-cards">
        <InfoCard icon={<Activity size={24} />} title="Current Concurrency" value="8 active calls" />
        <InfoCard icon={<Users size={24} />} title="Avg Sentiment" value="82% Positive" />
      </div>
    </section>
    <section>
      <div className="glass contact-form">
        <h3 style={{ marginBottom: '1.5rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          Live Sessions <div className="live-dot" />
        </h3>
        <div className="log-list">
          {liveCalls.map((call) => (
            <LogItem 
              key={call.id} 
              title={call.phone} 
              subtitle={`In progress • ${call.duration}`}
              status={call.sentiment}
              statusType="incoming"
            />
          ))}
        </div>
      </div>
    </section>
  </main>
);
