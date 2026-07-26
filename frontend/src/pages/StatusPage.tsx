import React from 'react';
import ServicesStatus from '../components/ServicesStatus';

const StatusPage: React.FC = () => {
  return (
    <div className="scrollable-page animate-fade-in">
      <div className="settings-page page-container">
      <header className="page-header">
        <h1>Статус служб</h1>
        <p>Мониторинг и управление фоновыми процессами операционной системы (FastAPI, Ollama).</p>
      </header>

      <div className="settings-content">
        <ServicesStatus />
      </div>
      </div>
    </div>
  );
};

export default StatusPage;
