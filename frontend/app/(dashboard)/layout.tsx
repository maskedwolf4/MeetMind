'use client';

import { useAuth } from '@/lib/auth-context';
import { useRouter } from 'next/navigation';
import { useEffect } from 'react';

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const { user, loading, logout } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !user) {
      router.push('/login');
    }
  }, [user, loading, router]);

  if (loading) {
    return (
      <div className="redirect-page">
        <div className="spinner-large" />
      </div>
    );
  }

  if (!user) {
    return null;
  }

  return (
    <div className="dashboard-page">
      <nav className="dashboard-nav">
        <span className="brand">MeetMind</span>
        <div className="nav-user">
          <span className="nav-user-name">{user.name}</span>
          <button className="btn-logout" onClick={logout} id="logout-btn">
            Sign Out
          </button>
        </div>
      </nav>
      {children}
    </div>
  );
}
