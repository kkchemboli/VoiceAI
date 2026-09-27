import React from 'react';
import {
  LayoutDashboard,
  Calendar,
  Mic2,
  BookOpen,
  PhoneCall,
  LogOut
} from 'lucide-react';
import './Sidebar.css';

interface SidebarProps {
  currentView: string;
  setCurrentView: (view: any) => void;
  username?: string | null;
  onLogout?: () => void;
}

export const Sidebar: React.FC<SidebarProps> = ({ currentView, setCurrentView, username, onLogout }) => {
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
        { id: 'outbound', label: 'Outbound Call', icon: <PhoneCall size={18} /> },
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
          <h2>Expert Institute Agent</h2>
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

      {onLogout && (
        <div className="sidebar-footer">
          {username && <div className="sidebar-user">{username}</div>}
          <button className="nav-item" onClick={onLogout}>
            <span className="item-icon"><LogOut size={18} /></span>
            <span className="item-label">Sign out</span>
          </button>
        </div>
      )}
    </aside>
  );
};
