import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { authApi } from '../shared/api/auth.api';
import { apiErrorMessage } from '../shared/api/errors';
import { useAuth } from '../shared/providers/AuthContext';
import { Alert, Button, Field, Input } from '../shared/ui';
import { BrandMark } from '../components/shell/BrandMark';
import { PRODUCT_NAME } from '../components/shell/nav';
import './LoginPage.css';

export default function LoginPage() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const { login } = useAuth();
  const navigate = useNavigate();

  useEffect(() => {
    document.title = `Вход · ${PRODUCT_NAME}`;
  }, []);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError('');
    setIsLoading(true);
    try {
      const data = await authApi.login(username.trim(), password);
      login(data.access_token, data.role, data.username, data.full_name, data.permissions);
      // «/» ведёт в первый раздел, доступный этому пользователю
      navigate('/');
    } catch (err: unknown) {
      setError(apiErrorMessage(err, 'Неверный логин или пароль'));
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="login">
      <div className="login__column">
        <div className="login__brand">
          <BrandMark size={24} />
          <span>{PRODUCT_NAME}</span>
        </div>

        <div className="login__intro">
          <h1 className="login__title">Вход</h1>
          <p className="login__lead">Данные и модель работают на этом компьютере</p>
        </div>

        <form className="login__form" onSubmit={handleSubmit} noValidate>
          {error && <Alert tone="negative">{error}</Alert>}

          <Field label="Логин">
            {(id) => (
              <Input
                id={id}
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                autoCapitalize="none"
                spellCheck={false}
                required
                autoFocus
              />
            )}
          </Field>

          <Field label="Пароль">
            {(id) => (
              <Input id={id} type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
            )}
          </Field>

          <Button type="submit" variant="primary" size="lg" block loading={isLoading} disabled={!username.trim() || !password}>
            Войти
          </Button>
        </form>

        <p className="login__hint">
          Первый вход: <span className="mono">admin</span> / <span className="mono">admin</span>. Пароль меняется в разделе «Пользователи»
        </p>
      </div>
    </div>
  );
}
