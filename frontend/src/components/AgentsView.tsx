import React, { useState } from 'react';
import './AgentsView.css';

interface AgentsViewProps {
  agentPrompt: string;
  setAgentPrompt: (prompt: string) => void;
  openingGreeting: string;
  setOpeningGreeting: (greeting: string) => void;
  outboundAgentPrompt: string;
  setOutboundAgentPrompt: (prompt: string) => void;
  outboundOpeningGreeting: string;
  setOutboundOpeningGreeting: (greeting: string) => void;
  onSave: () => void;
}

export const AgentsView: React.FC<AgentsViewProps> = ({
  agentPrompt,
  setAgentPrompt,
  openingGreeting,
  setOpeningGreeting,
  outboundAgentPrompt,
  setOutboundAgentPrompt,
  outboundOpeningGreeting,
  setOutboundOpeningGreeting,
  onSave
}) => {
  const [activeTab, setActiveTab] = useState<'inbound' | 'outbound'>('inbound');

  return (
    <div className="main-container">
      <header className="view-header">
        <h1 className="title">Agent Settings</h1>
        <p className="subtitle">Configure AI personality and behavior for different call types</p>
      </header>

      <div className="tab-navigation">
        <button 
          className={`tab-btn ${activeTab === 'inbound' ? 'active' : ''}`}
          onClick={() => setActiveTab('inbound')}
        >
          Inbound Calls
        </button>
        <button 
          className={`tab-btn ${activeTab === 'outbound' ? 'active' : ''}`}
          onClick={() => setActiveTab('outbound')}
        >
          Outbound Calls
        </button>
      </div>

      <div className="settings-container">
        {activeTab === 'inbound' ? (
          <>
            <section className="settings-card-alt animate-fade-in">
              <h2 className="card-subtitle">Inbound: Opening Greeting</h2>
              <div className="input-field-group">
                <label className="input-label-small">FIRST LINE (WHEN CUSTOMER CALLS YOU)</label>
                <textarea 
                  className="input-field-dark height-sm" 
                  value={openingGreeting}
                  onChange={(e) => setOpeningGreeting(e.target.value)}
                  placeholder="Namaste! Thanks for calling Expert Institute..."
                />
              </div>
            </section>

            <section className="settings-card-alt animate-fade-in">
              <h2 className="card-subtitle">Inbound: System Prompt</h2>
              <div className="input-field-group">
                <label className="input-label-small">MASTER SYSTEM PROMPT (INBOUND RULES)</label>
                <textarea 
                  className="input-field-dark height-lg" 
                  value={agentPrompt}
                  onChange={(e) => setAgentPrompt(e.target.value)}
                />
              </div>
            </section>
          </>
        ) : (
          <>
            <section className="settings-card-alt animate-fade-in">
              <h2 className="card-subtitle">Outbound: Opening Greeting</h2>
              <div className="input-field-group">
                <label className="input-label-small">
                  FIRST LINE (WHEN YOU CALL CUSTOMER)
                  {!outboundOpeningGreeting && <span className="default-indicator"> — Using Default</span>}
                </label>
                <textarea 
                  className="input-field-dark height-sm" 
                  value={outboundOpeningGreeting}
                  onChange={(e) => setOutboundOpeningGreeting(e.target.value)}
                  placeholder="Leaving this empty will use the default greeting: 'Hi, am I speaking with [Name]?'"
                />
              </div>
            </section>

            <section className="settings-card-alt animate-fade-in">
              <h2 className="card-subtitle">Outbound: System Prompt</h2>
              <div className="input-field-group">
                <label className="input-label-small">
                  MASTER SYSTEM PROMPT (OUTBOUND RULES)
                  {!outboundAgentPrompt && <span className="default-indicator"> — Using Default</span>}
                </label>
                <textarea 
                  className="input-field-dark height-lg" 
                  value={outboundAgentPrompt}
                  onChange={(e) => setOutboundAgentPrompt(e.target.value)}
                  placeholder="Type a custom outbound persona here. Leave empty to use the 'Neha' default script."
                />
                <p className="input-help-text">
                  Note: If empty, the system uses the multi-phase technical training inquiry script by default.
                </p>
              </div>
            </section>
          </>
        )}

        <div className="footer-actions">
          <button className="btn-primary-glow" onClick={onSave}>Save All Settings</button>
        </div>
      </div>
    </div>
  );
};
