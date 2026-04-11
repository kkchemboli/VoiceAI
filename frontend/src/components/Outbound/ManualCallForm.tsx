import React from 'react';
import { Phone, ArrowRight, Loader2 } from 'lucide-react';

interface ManualCallFormProps {
  phone: string;
  setPhone: (val: string) => void;
  isTriggering: boolean;
  onCall: () => void;
}

export const ManualCallForm: React.FC<ManualCallFormProps> = ({
  phone,
  setPhone,
  isTriggering,
  onCall
}) => {
  return (
    <div className="outbound-card animate-slide-up">
      <div className="card-header">
        <Phone className="header-icon" size={20} />
        <h3>Single Manual Call</h3>
      </div>
      <p className="card-description">
        Enter a phone number with country code to initiate an immediate agent-led call.
      </p>
      
      <div className="input-group">
        <label htmlFor="phone-input">Phone Number</label>
        <div className="input-with-icon">
          <Phone size={16} className="field-icon" />
          <input
            id="phone-input"
            type="tel"
            placeholder="85914 54670 (India) or +1..."
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
          />
        </div>
      </div>

      <button 
        className="primary-btn" 
        onClick={onCall}
        disabled={isTriggering || !phone}
      >
        {isTriggering ? (
          <><Loader2 className="animate-spin" size={18} /> Connecting...</>
        ) : (
          <><Phone size={18} /> Call Now <ArrowRight size={16} /></>
        )}
      </button>

      <div className="form-tip">
        Tip: 10-digit numbers will be auto-formatted for India (+91). For other countries, include the '+' and country code.
      </div>
    </div>
  );
};
