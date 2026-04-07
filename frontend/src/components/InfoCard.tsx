import React from 'react';

interface InfoCardProps {
  icon: React.ReactNode;
  title: string;
  value: string | number;
  iconStyle?: React.CSSProperties;
}

export const InfoCard: React.FC<InfoCardProps> = ({ icon, title, value, iconStyle }) => (
  <div className="info-card">
    <div className="icon-box" style={iconStyle}>{icon}</div>
    <div className="info-content">
      <h3>{title}</h3>
      <p>{value}</p>
    </div>
  </div>
);
