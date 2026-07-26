import React, { useEffect, useState } from 'react';
import { Activity, Server, Cpu, RefreshCw } from 'lucide-react';
import { useConfirm } from '../context/ConfirmContext';
import './ServicesStatus.css';

interface StatusResponse {
  backend: boolean;
  ollama: boolean;
}

const ServicesStatus: React.FC = () => {
  const [status, setStatus] = useState<StatusResponse>({ backend: true, ollama: true });
  const [loading, setLoading] = useState<boolean>(true);
  const [restarting, setRestarting] = useState<string | null>(null);
  const confirm = useConfirm();

  const checkStatus = async () => {
    try {
      const res = await fetch('/__internal/services/status');
      if (res.ok) {
        const data = await res.json();
        setStatus(data);
      }
    } catch (e) {
      console.error('Failed to fetch services status', e);
      setStatus({ backend: false, ollama: false });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    checkStatus();
    const interval = setInterval(checkStatus, 5000);
    return () => clearInterval(interval);
  }, []);

  const handleRestart = async (service: 'backend' | 'ollama') => {
    const isConfirmed = await confirm(`Вы уверены, что хотите перезапустить ${service === 'backend' ? 'бэкенд' : 'Ollama'}? Это может прервать текущие операции.`);
    if (!isConfirmed) {
      return;
    }
    
    setRestarting(service);
    try {
      const res = await fetch(`/__internal/services/restart?service=${service}`, {
        method: 'POST',
      });
      if (res.ok) {
        // Optimistically set to false while restarting, then let polling catch it
        setStatus(prev => ({ ...prev, [service]: false }));
      } else {
        alert('Не удалось отправить команду перезапуска.');
      }
    } catch (e) {
      console.error('Restart failed', e);
      alert('Ошибка при выполнении перезапуска.');
    } finally {
      // Wait a bit before clearing the restarting state
      setTimeout(() => setRestarting(null), 3000);
    }
  };

  return (
    <div className="services-status-panel">
      <div className="services-status-header">
        <h3><Activity size={18} /> Состояние служб</h3>
        {loading && <span className="status-loading">Проверка...</span>}
      </div>

      <div className="services-list">
        {/* Backend Status */}
        <div className="service-item">
          <div className="service-info">
            <Server size={18} className="service-icon" />
            <div className="service-text">
              <span className="service-name">Бэкенд (Python/FastAPI)</span>
              <span className={`service-state ${status.backend ? 'online' : 'offline'}`}>
                {status.backend ? 'Онлайн' : 'Офлайн'}
              </span>
            </div>
          </div>
          <button 
            className="btn btn--secondary btn--sm btn-restart" 
            onClick={() => handleRestart('backend')}
            disabled={restarting === 'backend'}
            title="Перезапустить процесс"
          >
            <RefreshCw size={14} className={restarting === 'backend' ? 'spin' : ''} />
            {restarting === 'backend' ? 'Перезапуск...' : (status.backend ? 'Перезапустить' : 'Запустить')}
          </button>
        </div>

        {/* Ollama Status */}
        <div className="service-item">
          <div className="service-info">
            <Cpu size={18} className="service-icon" />
            <div className="service-text">
              <span className="service-name">AI Модель (Ollama)</span>
              <span className={`service-state ${status.ollama ? 'online' : 'offline'}`}>
                {status.ollama ? 'Онлайн' : 'Офлайн'}
              </span>
            </div>
          </div>
          <button
            className="btn btn--secondary btn--sm btn-restart"
            onClick={() => handleRestart('ollama')}
            disabled={restarting === 'ollama'}
            title="Перезапустить процесс"
          >
            <RefreshCw size={14} className={restarting === 'ollama' ? 'spin' : ''} />
            {restarting === 'ollama' ? 'Перезапуск...' : (status.ollama ? 'Перезапустить' : 'Запустить')}
          </button>
        </div>
      </div>
    </div>
  );
};

export default ServicesStatus;
