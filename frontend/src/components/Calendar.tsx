import React from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import './Calendar.css';

interface CalendarProps {
  currentMonth: Date;
  setCurrentMonth: (date: Date) => void;
  appointments: any[];
}

export const CalendarView: React.FC<CalendarProps> = ({
  currentMonth,
  setCurrentMonth,
  appointments,
}) => {
  const year = currentMonth.getFullYear();
  const month = currentMonth.getMonth();
  const firstDay = new Date(year, month, 1).getDay();
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  
  const days = [];
  const monthName = currentMonth.toLocaleString('default', { month: 'long' });

  // Fill in days
  for (let i = 0; i < firstDay; i++) {
    days.push(null);
  }
  for (let i = 1; i <= daysInMonth; i++) {
    days.push(i);
  }

  const changeMonth = (offset: number) => {
    setCurrentMonth(new Date(year, month + offset, 1));
  };

  // Check if viewing current month
  const today = new Date();
  const isCurrentMonth = today.getMonth() === month && today.getFullYear() === year;
  const currentDay = today.getDate();

  // Process real booking data from appointments (using UTC to match Cal.com storage)
  const bookings: Record<number, number> = {};
  if (Array.isArray(appointments)) {
    appointments.forEach(appt => {
      const timeStr = appt.start || appt.startTime;
      if (!timeStr) return;
      
      const apptDate = new Date(timeStr);
      const apptDay = apptDate.getUTCDate();
      const apptMonth = apptDate.getUTCMonth();
      const apptYear = apptDate.getUTCFullYear();
      
      if (apptMonth === month && apptYear === year) {
        bookings[apptDay] = (bookings[apptDay] || 0) + (appt.seats_booked || 1);
      }
    });
  }

  return (
    <div className="main-container">
      <header className="view-header">
        <h1 className="title">Booking Calendar</h1>
        <p className="subtitle">View confirmed appointments by date</p>
      </header>

      <div className="calendar-card">
        <div className="calendar-nav">
          <button className="nav-btn-alt" onClick={() => changeMonth(-1)}>
            <ChevronLeft size={16} /> Prev
          </button>
          <h2 className="current-month">{monthName} {year}</h2>
          <button className="nav-btn-alt" onClick={() => changeMonth(1)}>
            Next <ChevronRight size={16} />
          </button>
        </div>

        <div className="calendar-full-grid">
          {['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'].map(d => (
            <div key={d} className="calendar-header-day">{d}</div>
          ))}
          {days.map((day, i) => (
            <div 
              key={i} 
              className={`calendar-cell ${isCurrentMonth && day === currentDay ? 'active' : ''} ${!day ? 'empty' : ''}`}
            >
              {day && (
                <>
                  <span className="day-number">{day}</span>
                  {bookings[day] !== undefined && bookings[day] > 0 && (
                    <div className="booking-info">
                      <div className="dot" />
                      <span>{bookings[day]} seat{bookings[day] > 1 ? 's' : ''}</span>
                    </div>
                  )}
                </>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
