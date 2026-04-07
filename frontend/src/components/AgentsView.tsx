import React from 'react';
import './AgentsView.css';

interface AgentsViewProps {
  agentPrompt: string;
  setAgentPrompt: (prompt: string) => void;
  openingGreeting: string;
  setOpeningGreeting: (greeting: string) => void;
  onSave: () => void;
}

export const AgentsView: React.FC<AgentsViewProps> = ({
  agentPrompt,
  setAgentPrompt,
  openingGreeting,
  setOpeningGreeting,
  onSave
}) => {
  return (
    <div className="main-container">
      <header className="view-header">
        <h1 className="title">Agent Settings</h1>
        <p className="subtitle">Configure AI personality, opening line, and sensitivity</p>
      </header>

      <div className="settings-container">
        {/* Opening Greeting Section */}
        <section className="settings-card-alt">
          <h2 className="card-subtitle">Opening Greeting</h2>
          <div className="input-field-group">
            <label className="input-label-small">FIRST LINE (WHAT THE AGENT SAYS WHEN A CALL CONNECTS)</label>
            <textarea 
              className="input-field-dark height-sm" 
              value={openingGreeting}
              onChange={(e) => setOpeningGreeting(e.target.value)}
              placeholder="Namaste!..."
            />
            <p className="field-hint">This is the very first thing the agent says. Keep it concise and warm.</p>
          </div>
        </section>

        {/* System Prompt Section */}
        <section className="settings-card-alt">
          <h2 className="card-subtitle">System Prompt</h2>
          <div className="input-field-group">
            <label className="input-label-small">MASTER SYSTEM PROMPT</label>
            <textarea 
              className="input-field-dark height-lg" 
              value={agentPrompt}
              onChange={(e) => setAgentPrompt(e.target.value)}
              placeholder="You are Priya..."
            />
            <p className="field-hint">Date and time context are injected automatically. Do not hardcode today's date.</p>
          </div>
        </section>

        <div className="footer-actions">
          <button className="btn-primary-glow" onClick={onSave}>Save Agent Settings</button>
        </div>
      </div>
    </div>
  );
};
