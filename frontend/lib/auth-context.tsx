'use client';

import React, { createContext, useContext, useEffect, useState, useCallback } from 'react';
import { User, AuthResponse, getMe, login as apiLogin, register as apiRegister, refreshToken } from '@/lib/api';

interface AuthContextType {
  user: User | null;
  token: string | null;
  refreshTokenValue: string | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (name: string, email: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [refreshTokenValue, setRefreshTokenValue] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Persist tokens to localStorage
  const saveTokens = useCallback((accessToken: string, refreshTk: string) => {
    localStorage.setItem('meetmind_access_token', accessToken);
    localStorage.setItem('meetmind_refresh_token', refreshTk);
    setToken(accessToken);
    setRefreshTokenValue(refreshTk);
  }, []);

  const clearTokens = useCallback(() => {
    localStorage.removeItem('meetmind_access_token');
    localStorage.removeItem('meetmind_refresh_token');
    setToken(null);
    setRefreshTokenValue(null);
    setUser(null);
  }, []);

  // Hydrate from localStorage on mount
  useEffect(() => {
    const stored_access = localStorage.getItem('meetmind_access_token');
    const stored_refresh = localStorage.getItem('meetmind_refresh_token');

    if (stored_access) {
      setToken(stored_access);
      setRefreshTokenValue(stored_refresh);

      getMe(stored_access)
        .then((u) => setUser(u))
        .catch(async () => {
          // Token expired — try refresh
          if (stored_refresh) {
            try {
              const newTokens = await refreshToken(stored_refresh);
              saveTokens(newTokens.access_token, newTokens.refresh_token);
              const u = await getMe(newTokens.access_token);
              setUser(u);
            } catch {
              clearTokens();
            }
          } else {
            clearTokens();
          }
        })
        .finally(() => setLoading(false));
    } else {
      setLoading(false);
    }
  }, [saveTokens, clearTokens]);

  const handleAuthResponse = useCallback((data: AuthResponse) => {
    saveTokens(data.access_token, data.refresh_token);
    setUser(data.user);
  }, [saveTokens]);

  const loginFn = useCallback(async (email: string, password: string) => {
    const data = await apiLogin(email, password);
    handleAuthResponse(data);
  }, [handleAuthResponse]);

  const registerFn = useCallback(async (name: string, email: string, password: string) => {
    const data = await apiRegister(name, email, password);
    handleAuthResponse(data);
  }, [handleAuthResponse]);

  const logout = useCallback(() => {
    clearTokens();
  }, [clearTokens]);

  return (
    <AuthContext.Provider
      value={{
        user,
        token,
        refreshTokenValue,
        loading,
        login: loginFn,
        register: registerFn,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (ctx === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return ctx;
}
