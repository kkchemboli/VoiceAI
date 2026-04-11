import React, { useState, useEffect, useRef } from 'react';
import { BookOpen, Save, Globe, Loader2, FileText, Database, Layers, Upload, Trash2 } from 'lucide-react';
import './KnowledgeBaseView.css';

interface KnowledgeStatus {
  files: Array<{ name: string; type: string; format: string; size: number }>;
  sheet_url: string;
  total_files: number;
}

export const KnowledgeBaseView: React.FC = () => {
  const [enContent, setEnContent] = useState<string>('');
  const [hiContent, setHiContent] = useState<string>('');
  const [activeTab, setActiveTab] = useState<'en' | 'hi'>('en');
  const [loading, setLoading] = useState<boolean>(true);
  const [saving, setSaving] = useState<boolean>(false);
  const [status, setStatus] = useState<KnowledgeStatus | null>(null);
  const [isUploading, setIsUploading] = useState<boolean>(false);
  const [message, setMessage] = useState<{ type: 'success' | 'error', text: string } | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const API_BASE = import.meta.env.VITE_API_URL || '';

  const fetchKnowledge = async () => {
    setLoading(true);
    
    // Use individual try-catch for each fetch to prevent total failure
    const fetchPart = async (url: string, fallback: any = { content: '' }) => {
      try {
        const res = await fetch(url);
        if (!res.ok) return fallback;
        return await res.json();
      } catch (err) {
        console.warn(`Failed to fetch from ${url}:`, err);
        return fallback;
      }
    };

    try {
      const [enData, hiData, statusData] = await Promise.all([
        fetchPart(`${API_BASE}/api/knowledge?lang=en`),
        fetchPart(`${API_BASE}/api/knowledge?lang=hi`),
        fetchPart(`${API_BASE}/api/knowledge/status`, null)
      ]);
      
      setEnContent(enData.content || '');
      setHiContent(hiData.content || '');
      setStatus(statusData);
      
      if (!enData.content && !hiData.content && !statusData) {
        setMessage({ type: 'error', text: 'Some parts of the knowledge base could not be loaded.' });
      }
    } catch (err) {
      console.error('Critical failure in fetchKnowledge:', err);
      setMessage({ type: 'error', text: 'Connection error while loading knowledge base.' });
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

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    if (!file.name.endsWith('.pdf') && !file.name.endsWith('.txt')) {
      setMessage({ type: 'error', text: 'Only PDF and TXT files are allowed.' });
      return;
    }

    setIsUploading(true);
    const formData = new FormData();
    formData.append('file', file);

    try {
      const response = await fetch(`${API_BASE}/api/knowledge/upload`, {
        method: 'POST',
        body: formData,
      });

      if (response.ok) {
        setMessage({ type: 'success', text: `Uploaded ${file.name} successfully!` });
        fetchKnowledge(); // Refresh status list
      } else {
        const err = await response.json();
        setMessage({ type: 'error', text: err.detail || 'Upload failed.' });
      }
    } catch (err) {
      setMessage({ type: 'error', text: 'Network error during upload.' });
    } finally {
      setIsUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const handleDeleteFile = async (filename: string) => {
    if (!window.confirm(`Are you sure you want to delete ${filename}? Neha will lose access to this info.`)) return;

    try {
      const response = await fetch(`${API_BASE}/api/knowledge/file/${filename}`, {
        method: 'DELETE',
      });

      if (response.ok) {
        setMessage({ type: 'success', text: `Deleted ${filename}.` });
        fetchKnowledge(); // Refresh
      } else {
        alert('Failed to delete file.');
      }
    } catch (err) {
      alert('Error connecting to backend.');
    }
  };

  return (
    <div className="main-container">
      <header className="view-header">
        <h1 className="title">Knowledge Base</h1>
        <p className="subtitle">Configure training data and manage transcripts for Neha's intelligence</p>
      </header>

      <div className="tab-navigation">
        <button 
          className={`tab-btn ${activeTab === 'en' ? 'active' : ''}`}
          onClick={() => setActiveTab('en')}
        >
          English (knowledge.txt)
        </button>
        <button 
          className={`tab-btn ${activeTab === 'hi' ? 'active' : ''}`}
          onClick={() => setActiveTab('hi')}
        >
          Hindi (knowledge_hi.txt)
        </button>
      </div>

      <div className="settings-container animate-fade-in">
        <section className="settings-card-alt">
          <h2 className="card-subtitle">
            <BookOpen size={18} color="#6366f1" />
            Knowledge Context ({activeTab.toUpperCase()})
          </h2>
          <div className="input-field-group">
            <label className="input-label-small">MASTER KNOWLEDGE BASE CONTENT</label>
            {loading ? (
              <div className="loading-mini">
                <Loader2 className="animate-spin" size={20} />
                <span>Syncing knowledge...</span>
              </div>
            ) : (
              <textarea
                className="input-field-dark height-lg"
                value={activeTab === 'en' ? enContent : hiContent}
                onChange={(e) => activeTab === 'en' ? setEnContent(e.target.value) : setHiContent(e.target.value)}
                placeholder="Paste or type your knowledge content here..."
              />
            )}
          </div>
        </section>

        <div className="footer-actions">
          {message && (
            <div className={`status-toast ${message.type}`}>
              {message.text}
            </div>
          )}
          <button 
            className="btn-primary-glow" 
            onClick={handleSave}
            disabled={saving}
          >
            {saving ? (
              <><Loader2 className="animate-spin" size={16} /> Saving Changes...</>
            ) : (
              <><Save size={16} /> Save Knowledge Base</>
            )}
          </button>
        </div>
      </div>
    </div>
  );
};
