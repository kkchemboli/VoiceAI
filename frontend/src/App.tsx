import { useState, useEffect } from 'react';
import { Sidebar } from './components/Sidebar';
import { DashboardView } from './components/Dashboard/DashboardView';
import { MonitorView } from './components/MonitorView';
import { AgentsView } from './components/AgentsView';
import { KnowledgeBaseView } from './components/KnowledgeBaseView';
import { CalendarView } from './components/Calendar';
import { CallLogsView } from './components/CallLogsView';
import { OutboundView } from './components/Outbound/OutboundView';

type View = 'dashboard' | 'monitor' | 'agents' | 'knowledge' | 'calendar' | 'models' | 'api' | 'logs' | 'crm' | 'outbound';

interface CallLog {
  id: number;
  date: string;
  time: string;
  phone: string;
  customer: string;
  duration: string;
  status: string;
  summary: string;
  transcript?: string;
}

interface Appointment {
  start: string;
  attendee: {
    name: string;
    phone: string;
  };
  seats_booked: number;
}

function App() {
  const API_BASE = import.meta.env.VITE_API_URL || '';
  const [currentView, setCurrentView] = useState<View>('dashboard');
  
  // Inbound State
  const [agentPrompt, setAgentPrompt] = useState<string>('');
  const [openingGreeting, setOpeningGreeting] = useState<string>('');
  
  // Outbound State
  const [outboundAgentPrompt, setOutboundAgentPrompt] = useState<string>('');
  const [outboundOpeningGreeting, setOutboundOpeningGreeting] = useState<string>('');
  
  const [fullCallLogs, setFullCallLogs] = useState<CallLog[]>([]);
  const [appointments, setAppointments] = useState<Appointment[]>([]);
  const [currentMonth, setCurrentMonth] = useState<Date>(new Date());
  const [liveCalls] = useState<any[]>([]); // Future implementation

  const refreshData = () => {
    // Fetch Agent Config
    fetch(`${API_BASE}/api/config`)
      .then(res => res.json())
      .then(data => {
        setAgentPrompt(data.system_prompt || "");
        setOpeningGreeting(data.opening_greeting || "");
        setOutboundAgentPrompt(data.outbound_system_prompt || "");
        setOutboundOpeningGreeting(data.outbound_opening_greeting || "");
      })
      .catch(err => console.error("Failed to fetch agent config:", err));

    // Fetch Call Logs
    fetch(`${API_BASE}/api/logs`)
      .then(res => res.json())
      .then(data => {
        setFullCallLogs(data);
      })
      .catch(err => console.error("Failed to fetch call logs:", err));
  };

  // Fetch appointments for a specific month
  const fetchAppointments = (date: Date) => {
    const year = date.getFullYear();
    const month = date.getMonth() + 1;
    fetch(`${API_BASE}/api/appointments?year=${year}&month=${month}`)
      .then(res => res.json())
      .then(data => {
        setAppointments(data);
      })
      .catch(err => console.error("Failed to fetch appointments:", err));
  };

  useEffect(() => {
    refreshData();
    fetchAppointments(currentMonth);
    // Poll every 10 seconds
    const interval = setInterval(() => {
      refreshData();
      fetchAppointments(currentMonth);
    }, 10000);
    return () => clearInterval(interval);
  }, []);

  // Fetch appointments when month changes
  useEffect(() => {
    fetchAppointments(currentMonth);
  }, [currentMonth]);

  const saveAgentSettings = async () => {
    try {
      const response = await fetch(`${API_BASE}/api/config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
          system_prompt: agentPrompt,
          opening_greeting: openingGreeting,
          outbound_system_prompt: outboundAgentPrompt,
          outbound_opening_greeting: outboundOpeningGreeting
        }),
      });
      if (response.ok) {
        alert("Agent settings saved successfully!");
      } else {
        alert("Failed to save agent settings.");
      }
    } catch (err) {
      console.error("Error saving agent settings:", err);
      alert("Error saving agent settings.");
    }
  };

  useEffect(() => {
    if (currentView === 'dashboard') {
      document.body.classList.add('dashboard-active');
    } else {
      document.body.classList.remove('dashboard-active');
    }
  }, [currentView]);

  const formatDate = (date: Date) => {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
  };
  
  const filteredCalls = fullCallLogs.filter((log: CallLog) => log.date === formatDate(new Date()));

  return (
    <div className="app-layout">
      <Sidebar currentView={currentView} setCurrentView={setCurrentView} />
      
      <main className="content-area">
        <div className="bg-watermark">{currentView.toUpperCase()}</div>
        <div className="circuit-bg" />

        {currentView === 'dashboard' && (
          <DashboardView 
            filteredCalls={filteredCalls} 
            totalCalls={fullCallLogs.length}
            onRefresh={refreshData}
          />
        )}

        {currentView === 'calendar' && (
          <CalendarView
            currentMonth={currentMonth}
            setCurrentMonth={setCurrentMonth}
            appointments={appointments}
          />
        )}

        {(currentView === 'logs') && <CallLogsView logs={fullCallLogs} onRefresh={refreshData} />}

        {(currentView === 'monitor') && <MonitorView liveCalls={liveCalls} />}

        {(currentView === 'agents') && (
          <AgentsView
            agentPrompt={agentPrompt}
            setAgentPrompt={setAgentPrompt}
            openingGreeting={openingGreeting}
            setOpeningGreeting={setOpeningGreeting}
            outboundAgentPrompt={outboundAgentPrompt}
            setOutboundAgentPrompt={setOutboundAgentPrompt}
            outboundOpeningGreeting={outboundOpeningGreeting}
            setOutboundOpeningGreeting={setOutboundOpeningGreeting}
            onSave={saveAgentSettings}
          />
        )}

        {currentView === 'knowledge' && <KnowledgeBaseView />}
        
        {currentView === 'outbound' && <OutboundView />}

        {(currentView === 'crm') && (
          <div className="view-container">
            <h1 className="title">CRM Contacts</h1>
            <p className="subtitle">Manage your customer relationships and contact history.</p>
          </div>
        )}
      </main>
    </div>
  );
}

export default App;
