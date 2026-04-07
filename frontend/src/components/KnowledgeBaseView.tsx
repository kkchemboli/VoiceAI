import React, { useState, useEffect } from 'react';
import { BookOpen, Save, Globe, Loader2 } from 'lucide-react';
import './KnowledgeBaseView.css';

export const KnowledgeBaseView: React.FC = () => {
  const [enContent, setEnContent] = useState<string>('');
  const [hiContent, setHiContent] = useState<string>('');
  const [activeTab, setActiveTab] = useState<'en' | 'hi'>('en');
  const [loading, setLoading] = useState<boolean>(true);
  const [saving, setSaving] = useState<boolean>(false);
  const [message, setMessage] = useState<{ type: 'success' | 'error', text: string } | null>(null);

  const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

  const fetchKnowledge = async () => {
    setLoading(true);
    try {
      const [enRes, hiRes] = await Promise.all([
        fetch(`${API_BASE}/api/knowledge?lang=en`),
        fetch(`${API_BASE}/api/knowledge?lang=hi`)
      ]);
      
      const enData = await enRes.json();
      const hiData = await hiRes.json();
      
      setEnContent(enData.content || '');
      setHiContent(hiData.content || '');
    } catch (err) {
      console.error('Failed to fetch knowledge base:', err);
      setMessage({ type: 'error', text: 'Failed to load knowledge base content.' });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchKnowledge();
  }, []);

  const handleSave = async () => {
    setSaving(true);
    setMessage(null);
    const content = activeTab === 'en' ? enContent : hiContent;
    
    try {
      const response = await fetch(`${API_BASE}/api/knowledge/${activeTab}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content })
      });
      
      if (response.ok) {
        setMessage({ type: 'success', text: `Knowledge base (${activeTab.toUpperCase()}) saved successfully!` });
      } else {
        const errorData = await response.json();
        setMessage({ type: 'error', text: errorData.detail || 'Failed to save changes.' });
      }
    } catch (err) {
      console.error('Error saving knowledge base:', err);
      setMessage({ type: 'error', text: 'Connection error. Is the backend running?' });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="kb-container view-container">
      <header className="kb-header">
        <h1 className="title">Knowledge Base</h1>
        <p className="subtitle">Edit the data the AI uses to answer customer questions.</p>
      </header>

      <div className="kb-tabs">
        <button 
          className={`kb-tab-btn ${activeTab === 'en' ? 'active' : ''}`}
          onClick={() => setActiveTab('en')}
        >
          <Globe size={16} style={{ marginRight: '8px' }} />
          English (knowledge.txt)
        </button>
        <button 
          className={`kb-tab-btn ${activeTab === 'hi' ? 'active' : ''}`}
          onClick={() => setActiveTab('hi')}
        >
          <Globe size={16} style={{ marginRight: '8px' }} />
          Hindi (knowledge_hi.txt)
        </button>
      </div>

      <div className="kb-editor-card">
        {loading ? (
          <div className="loading-state">
            <Loader2 className="animate-spin" size={32} color="#6366f1" />
            <p>Loading knowledge base...</p>
          </div>
        ) : (
          <>
            <div className="editor-info">
              <BookOpen size={18} color="#6366f1" />
              <span>Editing {activeTab === 'en' ? 'English' : 'Hindi'} Knowledge</span>
            </div>
            
            <textarea
              className="kb-textarea"
              value={activeTab === 'en' ? enContent : hiContent}
              onChange={(e) => activeTab === 'en' ? setEnContent(e.target.value) : setHiContent(e.target.value)}
              placeholder="Paste or type your knowledge content here..."
            />

            <div className="kb-actions">
              {message && (
                <div className={`status-msg ${message.type}`}>
                  {message.text}
                </div>
              )}
              <button 
                className="btn-save-kb" 
                onClick={handleSave}
                disabled={saving}
              >
                {saving ? (
                  <>
                    <Loader2 className="animate-spin" size={16} style={{ marginRight: '8px' }} />
                    Saving...
                  </>
                ) : (
                  <>
                    <Save size={16} style={{ marginRight: '8px' }} />
                    Save Changes
                  </>
                )}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
};
