import React, { useState } from 'react';
import { useNavigate, Navigate } from 'react-router-dom';
import { authApi } from '../services/api';
import { useAuth } from '../contexts/AuthContext';
import './LoginPage.css';

const LoginPage: React.FC = () => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  
  const { login, isAuthenticated } = useAuth();
  const navigate = useNavigate();

  if (isAuthenticated) {
    return <Navigate to="/dashboard" replace />;
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setIsLoading(true);

    try {
      const data = await authApi.login(username, password);
      login(data.access_token, data.role, data.username, data.full_name, data.permissions);
      navigate('/dashboard');
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Ошибка авторизации. Проверьте логин и пароль.');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-header">
          <h1>Offline Sales Analytics</h1>
          <p>Войдите для продолжения работы</p>
        </div>
        
        <form className="login-form" onSubmit={handleSubmit}>
          {error && <div className="login-error">{error}</div>}
          
          <div className="form-group">
            <label htmlFor="username">Имя пользователя</label>
            <input
              type="text"
              id="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="admin или user"
              required
            />
          </div>
          
          <div className="form-group">
            <label htmlFor="password">Пароль</label>
            <input
              type="password"
              id="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Пароль"
              required
            />
          </div>
          
          <button type="submit" className="btn btn--primary btn--lg" disabled={isLoading} style={{ width: '100%', marginTop: '1rem' }}>
            {isLoading ? 'Вход...' : 'Войти'}
          </button>

          <div className="login-hint">
            <p>Первый вход: <strong>admin</strong> / <strong>admin</strong></p>
            <p>Пароль можно сменить в разделе «Пользователи»</p>
          </div>
        </form>
      </div>
    </div>
  );
};

export default LoginPage;
