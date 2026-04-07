import React from 'react';
import {
  LayoutDashboard,
  Calendar,
  Mic2,
  BookOpen,
  PhoneCall
} from 'lucide-react';
import './Sidebar.css';

interface SidebarProps {
  currentView: string;
  setCurrentView: (view: any) => void;
}

export const Sidebar: React.FC<SidebarProps> = ({ currentView, setCurrentView }) => {
  const menuGroups = [
    {
      title: 'OVERVIEW',
      items: [
        { id: 'dashboard', label: 'Dashboard', icon: <LayoutDashboard size={18} /> },
        { id: 'calendar', label: 'Calendar', icon: <Calendar size={18} /> },
      ]
    },
    {
      title: 'CONFIGURATION',
      items: [
        { id: 'agents', label: 'Agent Settings', icon: <Mic2 size={18} /> },
        { id: 'knowledge', label: 'Knowledge Base', icon: <BookOpen size={18} /> },
      ]
    },
    {
      title: 'DATA',
      items: [
        { id: 'logs', label: 'Call Logs', icon: <PhoneCall size={18} /> },
      ]
    }
  ];

  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <div className="brand-icon">
          <Mic2 size={24} color="#6366f1" />
        </div>
        <div className="brand-info">
          <h2>Voice Agent</h2>
          <p></p>
        </div>
      </div>

      <nav className="sidebar-nav">
        {menuGroups.map((group, groupIdx) => (
          <div key={groupIdx} className="nav-group">
            <h3 className="group-title">{group.title}</h3>
            <ul className="group-list">
              {group.items.map(item => (
                <li key={item.id}>
                  <button
                    className={`nav-item ${currentView === item.id ? 'active' : ''}`}
                    onClick={() => setCurrentView(item.id)}
                  >
                    <span className="item-icon">{item.icon}</span>
                    <span className="item-label">{item.label}</span>
                    {currentView === item.id && <div className="active-indicator" />}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </nav>
    </aside>
  );
};
