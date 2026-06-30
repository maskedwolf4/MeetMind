/**
 * API client for MeetMind backend.
 * All requests go through this module to ensure consistent auth handling.
 */

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

interface FetchOptions extends RequestInit {
  token?: string;
}

async function apiFetch<T>(endpoint: string, options: FetchOptions = {}): Promise<T> {
  const { token, headers: customHeaders, ...rest } = options;

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...((customHeaders as Record<string, string>) || {}),
  };

  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const res = await fetch(`${API_URL}${endpoint}`, {
    ...rest,
    headers,
  });

  if (!res.ok) {
    const errorBody = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(res.status, errorBody.detail || 'An error occurred');
  }

  return res.json();
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = 'ApiError';
  }
}

// ---------- Auth ----------

export interface User {
  id: string;
  name: string;
  email: string;
  created_at: string;
}

export interface AuthResponse {
  user: User;
  access_token: string;
  refresh_token: string;
  token_type: string;
}

export interface Meeting {
  id: string;
  title: string;
  meeting_datetime: string;
  created_by: string;
  source: string;
  external_meeting_id: string | null;
  raw_transcript: string | null;
  status: string;
  created_at: string;
}

export async function register(name: string, email: string, password: string): Promise<AuthResponse> {
  return apiFetch<AuthResponse>('/auth/register', {
    method: 'POST',
    body: JSON.stringify({ name, email, password }),
  });
}

export async function login(email: string, password: string): Promise<AuthResponse> {
  return apiFetch<AuthResponse>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  });
}

export async function refreshToken(refresh_token: string): Promise<{ access_token: string; refresh_token: string; token_type: string }> {
  return apiFetch('/auth/refresh', {
    method: 'POST',
    body: JSON.stringify({ refresh_token }),
  });
}

export async function getMe(token: string): Promise<User> {
  return apiFetch<User>('/auth/me', { token });
}

// ---------- Meetings ----------

export async function createMeeting(token: string, title: string, meeting_datetime: string): Promise<Meeting> {
  return apiFetch<Meeting>('/meetings', {
    method: 'POST',
    token,
    body: JSON.stringify({ title, meeting_datetime }),
  });
}

export async function getMeeting(token: string, meetingId: string): Promise<Meeting> {
  return apiFetch<Meeting>(`/meetings/${meetingId}`, { token });
}

export async function teamsJoin(token: string, meetingId: string, teams_join_url: string): Promise<Meeting> {
  return apiFetch<Meeting>(`/meetings/${meetingId}/teams/join`, {
    method: 'POST',
    token,
    body: JSON.stringify({ teams_join_url }),
  });
}

export async function meetImport(token: string, meetingId: string, transcript_text: string): Promise<Meeting> {
  return apiFetch<Meeting>(`/meetings/${meetingId}/meet/import`, {
    method: 'POST',
    token,
    body: JSON.stringify({ transcript_text }),
  });
}
