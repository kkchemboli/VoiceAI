import React from 'react';
import './ApiCredentialsView.css';

export const ApiCredentialsView: React.FC = () => {
  const sections = [
    {
      title: 'LiveKit',
      fields: [
        { label: 'LIVEKIT URL', placeholder: '' },
        { label: 'SIP TRUNK ID', placeholder: '' },
        { label: 'API KEY', placeholder: '' },
        { label: 'API SECRET', placeholder: '' },
      ]
    },
    {
      title: 'AI Providers',
      fields: [
        { label: 'OPENAI API KEY', placeholder: '' },
        { label: 'SARVAM API KEY', placeholder: '' },
      ]
    },
    {
      title: 'Integrations',
      fields: [
        { label: 'CAL.COM API KEY', placeholder: '' },
        { label: 'CAL.COM EVENT TYPE ID', placeholder: '' },
      ]
    }
  ];

  return (
    <div className="main-container">
      <header className="view-header">
        <h1 className="title">API Credentials</h1>
        <p className="subtitle">Credentials here override .env values at runtime. Never share this page.</p>
      </header>

      <div className="credentials-list">
        {sections.map((section, idx) => (
          <div key={idx} className="settings-card">
            <h2 className="card-title">{section.title}</h2>
            <div className="fields-grid">
              {section.fields.map((field, fIdx) => (
                <div key={fIdx} className="input-group-alt">
                  <label className="field-label">{field.label}</label>
                  <input 
                    type="password" 
                    className="input-field-alt" 
                    placeholder={field.placeholder} 
                  />
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};
