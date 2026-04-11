import { useOutbound, OutboundTab } from '../../hooks/useOutbound';
import { CallQueueStatus } from './CallQueueStatus';
import { Rocket, ListFilter, Save, Link, Loader2, Play } from 'lucide-react';
import './OutboundView.css';

export const OutboundView: React.FC = () => {
  const outbound = useOutbound();

  const tabs: { id: OutboundTab; label: string; icon: React.ReactNode }[] = [
    { id: 'smart', label: 'Smart Dialer', icon: <Rocket size={18} /> },
    { id: 'queue', label: 'Queue Status', icon: <ListFilter size={18} /> },
  ];

  return (
    <div className="view-container outbound-view">
      <header className="view-header">
        <h1 className="title">Outbound Campaigns</h1>
        <p className="subtitle">Automate your student outreach using Google Sheets and Sequential Dialing.</p>
      </header>

      <div className="outbound-tabs-nav">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            className={`tab-btn ${outbound.activeTab === tab.id ? 'active' : ''}`}
            onClick={() => outbound.setActiveTab(tab.id)}
          >
            {tab.icon}
            <span>{tab.label}</span>
            {tab.id === 'queue' && outbound.queue.some((q: any) => q.status === 'calling') && (
              <span className="live-indicator" />
            )}
          </button>
        ))}
      </div>

      <div className="outbound-content-area">
        {outbound.activeTab === 'smart' && (
          <div className="smart-dialer-container animate-fade-in">
            <section className="settings-card-alt">
              <h2 className="card-subtitle">
                <Link size={20} style={{ marginRight: '8px' }} />
                Google Sheet Integration
              </h2>
              <p className="card-description">Paste your Google Sheet sharing link here. Ensure it is set to "Anyone with link can view".</p>
              
              <div className="input-field-group">
                <label className="input-label-small">SHEET CSV OR SHARING URL</label>
                <div className="input-with-action">
                  <input 
                    type="text"
                    className="input-field-darker"
                    value={outbound.sheetUrl}
                    onChange={(e) => outbound.setSheetUrl(e.target.value)}
                    placeholder="https://docs.google.com/spreadsheets/d/..."
                  />
                  <button 
                    className="btn-action-small" 
                    onClick={outbound.handleSaveSheetUrl}
                    disabled={outbound.isSavingUrl}
                  >
                    {outbound.isSavingUrl ? <Loader2 className="animate-spin" size={16} /> : <Save size={16} />}
                    Save
                  </button>
                </div>
              </div>
            </section>

            <section className="action-card-glow text-center">
              <h3 className="glow-title">Start Your Campaign</h3>
              <p className="glow-description">Neha will call every student in your sheet one-by-one, waiting for each to finish before starting the next.</p>
              
              <button 
                className="btn-massive-outbound"
                onClick={outbound.handleStartBulkCampaign}
                disabled={outbound.isDialing}
              >
                {outbound.isDialing ? (
                  <Loader2 className="animate-spin" size={24} />
                ) : (
                  <Play size={24} fill="currentColor" />
                )}
                <span>{outbound.isDialing ? 'Initiating Campaign...' : 'Start Massive Sequential Dialing'}</span>
              </button>
            </section>
          </div>
        )}

        {outbound.activeTab === 'queue' && (
          <CallQueueStatus 
            queue={outbound.queue}
            isLoading={outbound.isLoadingQueue}
            onRefresh={outbound.refreshQueue}
          />
        )}
      </div>
    </div>
  );
};
