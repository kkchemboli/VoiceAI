import React, { useRef } from 'react';
import { FileUp, List, Delete, Play, Loader2, X } from 'lucide-react';

interface BatchCallFormProps {
  rawInput: string;
  setRawInput: (val: string) => void;
  numbers: string[];
  isProcessingPDF: boolean;
  onPDFUpload: (file: File) => void;
  onImportText: () => void;
  onTriggerBatch: () => void;
  onRemoveNumber: (num: string) => void;
  isTriggering: boolean;
}

export const BatchCallForm: React.FC<BatchCallFormProps> = ({
  rawInput,
  setRawInput,
  numbers,
  isProcessingPDF,
  onPDFUpload,
  onImportText,
  onTriggerBatch,
  onRemoveNumber,
  isTriggering
}) => {
  const fileInputRef = useRef<HTMLInputElement>(null);

  return (
    <div className="batch-container animate-slide-up">
      <div className="import-section">
        <div className="outbound-card">
          <div className="card-header">
            <FileUp className="header-icon" size={20} />
            <h3>Import Numbers</h3>
          </div>
          
          <div className="upload-zone" onClick={() => fileInputRef.current?.click()}>
            <input 
              type="file" 
              ref={fileInputRef} 
              hidden 
              accept="application/pdf"
              onChange={(e) => e.target.files?.[0] && onPDFUpload(e.target.files[0])}
            />
            {isProcessingPDF ? (
              <Loader2 className="animate-spin" size={32} color="#6366f1" />
            ) : (
              <FileUp size={32} color="#6366f1" />
            )}
            <p>{isProcessingPDF ? 'Analyzing PDF...' : 'Click to upload PDF or drag & drop'}</p>
            <span>Supports extraction of phone numbers from lead lists</span>
          </div>

          <div className="divider"><span>OR</span></div>

          <div className="input-group">
            <label>Paste Numbers Manually</label>
            <textarea
              placeholder="Paste numbers separated by commas or newlines..."
              value={rawInput}
              onChange={(e) => setRawInput(e.target.value)}
              rows={4}
            />
            <button className="secondary-btn" onClick={onImportText}>
              Add to List
            </button>
          </div>
        </div>
      </div>

      <div className="list-section">
        <div className="outbound-card">
          <div className="card-header">
            <List className="header-icon" size={20} />
            <h3>Staged Numbers ({numbers.length})</h3>
          </div>
          
          <div className="numbers-list-container">
            {numbers.length > 0 ? (
              <div className="numbers-grid">
                {numbers.map((num, idx) => (
                  <div key={idx} className="number-tag">
                    <span>{num}</span>
                    <button onClick={() => onRemoveNumber(num)}><X size={14} /></button>
                  </div>
                ))}
              </div>
            ) : (
              <div className="empty-list">
                <Delete size={40} opacity={0.2} />
                <p>No numbers staged yet.</p>
              </div>
            )}
          </div>

          <button 
            className="primary-btn pulse" 
            disabled={numbers.length === 0 || isTriggering}
            onClick={onTriggerBatch}
          >
            {isTriggering ? (
              <><Loader2 className="animate-spin" size={18} /> Batch Starting...</>
            ) : (
              <><Play size={18} /> Start Batch Call</>
            )}
          </button>
        </div>
      </div>
    </div>
  );
};
