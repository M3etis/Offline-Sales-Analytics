import React, { createContext, useContext, useState, useMemo, useCallback } from 'react';
import type { ReactNode } from 'react';

interface AuthContextType {
  token: string | null;
  role: string | null;
  username: string | null;
  fullName: string | null;
  permissions: string[];
  login: (token: string, role: string, username: string, fullName: string, permissions: string[]) => void;
  logout: () => void;
  isAuthenticated: boolean;
  isAdmin: boolean;
  hasPermission: (section: string) => boolean;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const isTokenExpired = (token: string): boolean => {
    try {
      const payload = JSON.parse(atob(token.split('.')[1]));
      return payload.exp * 1000 < Date.now();
    } catch {
      return true;
    }
  };

  const [token, setToken] = useState<string | null>(() => {
    const saved = localStorage.getItem('token');
    if (saved && isTokenExpired(saved)) {
      localStorage.removeItem('token');
      localStorage.removeItem('role');
      localStorage.removeItem('username');
      localStorage.removeItem('fullName');
      localStorage.removeItem('permissions');
      return null;
    }
    return saved;
  });
  const [role, setRole] = useState<string | null>(localStorage.getItem('role'));
  const [username, setUsername] = useState<string | null>(localStorage.getItem('username'));
  const [fullName, setFullName] = useState<string | null>(localStorage.getItem('fullName'));
  const [permissions, setPermissions] = useState<string[]>(() => {
    try {
      const saved = localStorage.getItem('permissions');
      return saved ? JSON.parse(saved) : [];
    } catch {
      return [];
    }
  });

  const login = (newToken: string, newRole: string, newUsername: string, newFullName: string, newPermissions: string[]) => {
    localStorage.setItem('token', newToken);
    localStorage.setItem('role', newRole);
    localStorage.setItem('username', newUsername);
    localStorage.setItem('fullName', newFullName);
    localStorage.setItem('permissions', JSON.stringify(newPermissions));
    setToken(newToken);
    setRole(newRole);
    setUsername(newUsername);
    setFullName(newFullName);
    setPermissions(newPermissions);
  };

  const logout = () => {
    localStorage.removeItem('token');
    localStorage.removeItem('role');
    localStorage.removeItem('username');
    localStorage.removeItem('fullName');
    localStorage.removeItem('permissions');
    setToken(null);
    setRole(null);
    setUsername(null);
    setFullName(null);
    setPermissions([]);
  };

  const hasPermission = useCallback((section: string): boolean => {
    if (role === 'admin') return true;
    return permissions.includes(section);
  }, [role, permissions]);

  const value = useMemo(() => ({
    token,
    role,
    username,
    fullName,
    permissions,
    login,
    logout,
    isAuthenticated: !!token,
    isAdmin: role === 'admin',
    hasPermission
  }), [token, role, username, fullName, permissions, login, logout, hasPermission]);

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};
