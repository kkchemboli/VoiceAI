import { useState, type FormEvent } from 'react';
import { login } from '../services/api';
import './LoginView.css';

interface LoginViewProps {
  onAuthenticated: (username: string) => void;
}

/**
 * The login screen for the single admin user.
 *
 * There is no sign-up and no password reset by design: this gates a personal
 * deployment, and an account-creation endpoint would be a way around the gate
 * rather than a feature of it.
 */
export const LoginView: React.FC<LoginViewProps> = ({ onAuthenticated }) => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (submitting) return;

    setError('');
    setSubmitting(true);
    const result = await login(username, password);
    setSubmitting(false);

    if (result.ok) {
      onAuthenticated(username);
    } else {
      setError(result.error || 'Login failed');
    }
  };

  return (
    <div className="login-screen">
      <form className="login-card" onSubmit={handleSubmit}>
        <h1 className="login-title">Expert Institute Agent</h1>
        <p className="login-subtitle">Sign in to continue</p>

        {error && (
          <div className="login-error" role="alert">
            {error}
          </div>
        )}

        <label className="login-label" htmlFor="login-username">
          Username
        </label>
        <input
          id="login-username"
          className="login-input"
          type="text"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoComplete="username"
          autoFocus
          required
        />

        <label className="login-label" htmlFor="login-password">
          Password
        </label>
        <input
          id="login-password"
          className="login-input"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
          required
        />

        <button className="login-button" type="submit" disabled={submitting}>
          {submitting ? 'Signing in...' : 'Sign in'}
        </button>
      </form>
    </div>
  );
};
