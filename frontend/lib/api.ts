/**
 * MeetMind — Typed API client.
 *
 * All backend calls go through this module for consistent auth handling.
 * JWT is stored in localStorage and attached as Bearer token.
 * Handles 401 by clearing auth state. Returns {error} for 403/404.
 */

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

// ------------------------------------------------------------------ //
// Types
// ------------------------------------------------------------------ //

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
  token_type?: string;
}

export interface Meeting {
  id: string;
  title: string;
  meeting_datetime: string;
  created_by: string;
  source: string;
  external_meeting_id: string | null;
  raw_transcript: string | null;
  summary: string | null;
  status: string;
  created_at: string;
}

export interface ThreadInfo {
  thread_id: string;
  meeting_id: string;
  user_id: string;
  created_at: string;
  message_count: number;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
}

export interface MessagesResponse {
  messages: ChatMessage[];
  has_more: boolean;
}

export interface ApiResult<T> {
  data?: T;
  error?: string;
  status?: number;
}

// ------------------------------------------------------------------ //
// Core fetch wrapper
// ------------------------------------------------------------------ //

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = 'ApiError';
  }
}

function getToken(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem('meetmind_access_token');
}

async function apiFetch<T>(
  endpoint: string,
  options: RequestInit & { token?: string; skipAuth?: boolean } = {}
): Promise<T> {
  const { token: explicitToken, skipAuth, ...rest } = options;
  const tkn = explicitToken || getToken();

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...((rest.headers as Record<string, string>) || {}),
  };

  if (tkn && !skipAuth) {
    headers['Authorization'] = `Bearer ${tkn}`;
  }

  const res = await fetch(`${API_URL}${endpoint}`, {
    ...rest,
    headers,
    credentials: 'include',
  });

  if (res.status === 401) {
    // Token expired — clear auth state
    if (typeof window !== 'undefined') {
      localStorage.removeItem('meetmind_access_token');
      localStorage.removeItem('meetmind_refresh_token');
      window.location.href = '/login';
    }
    throw new ApiError(401, 'Session expired');
  }

  if (!res.ok) {
    const errorBody = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(res.status, errorBody.detail || 'An error occurred');
  }

  return res.json();
}

/** Wraps apiFetch and catches errors into {error} instead of throwing */
async function safeApiFetch<T>(
  endpoint: string,
  options: RequestInit & { token?: string } = {}
): Promise<ApiResult<T>> {
  try {
    const data = await apiFetch<T>(endpoint, options);
    return { data };
  } catch (err) {
    if (err instanceof ApiError) {
      return { error: err.message, status: err.status };
    }
    return { error: 'Network error' };
  }
}

// ------------------------------------------------------------------ //
// Auth
// ------------------------------------------------------------------ //

export async function register(name: string, email: string, password: string): Promise<AuthResponse> {
  return apiFetch<AuthResponse>('/auth/register', {
    method: 'POST',
    body: JSON.stringify({ name, email, password }),
    skipAuth: true,
  });
}

export async function login(email: string, password: string): Promise<AuthResponse> {
  return apiFetch<AuthResponse>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
    skipAuth: true,
  });
}

export async function refreshToken(refresh_token: string): Promise<{ access_token: string; refresh_token: string }> {
  return apiFetch('/auth/refresh', {
    method: 'POST',
    body: JSON.stringify({ refresh_token }),
    skipAuth: true,
  });
}

export async function getMe(token: string): Promise<User> {
  return apiFetch<User>('/auth/me', { token });
}

// ------------------------------------------------------------------ //
// Meetings
// ------------------------------------------------------------------ //

export async function listMeetings(): Promise<Meeting[]> {
  return apiFetch<Meeting[]>('/meetings');
}

export async function getMeeting(meetingId: string): Promise<Meeting> {
  return apiFetch<Meeting>(`/meetings/${meetingId}`);
}

export async function createMeeting(
  title: string,
  meeting_datetime: string,
  source?: string
): Promise<Meeting> {
  return apiFetch<Meeting>('/meetings', {
    method: 'POST',
    body: JSON.stringify({ title, meeting_datetime }),
  });
}

export async function addAttendee(meetingId: string, email: string): Promise<ApiResult<any>> {
  return safeApiFetch(`/meetings/${meetingId}/attendees`, {
    method: 'POST',
    body: JSON.stringify({ email }),
  });
}

export async function teamsJoin(meetingId: string, teams_join_url: string): Promise<Meeting> {
  return apiFetch<Meeting>(`/meetings/${meetingId}/teams/join`, {
    method: 'POST',
    body: JSON.stringify({ teams_join_url }),
  });
}

export async function importTranscript(meetingId: string, transcript_text: string): Promise<Meeting> {
  return apiFetch<Meeting>(`/meetings/${meetingId}/meet/import`, {
    method: 'POST',
    body: JSON.stringify({ transcript_text }),
  });
}

export async function processMeeting(meetingId: string): Promise<Meeting> {
  return apiFetch<Meeting>(`/meetings/${meetingId}/process`, {
    method: 'POST',
  });
}

// ------------------------------------------------------------------ //
// Threads & Chat
// ------------------------------------------------------------------ //

export async function getOrCreateThread(meetingId: string): Promise<ThreadInfo> {
  return apiFetch<ThreadInfo>(`/meetings/${meetingId}/thread`);
}

export async function getMessages(
  threadId: string,
  limit: number = 50,
  before?: string
): Promise<MessagesResponse> {
  let url = `/threads/${threadId}/messages?limit=${limit}`;
  if (before) url += `&before=${before}`;
  return apiFetch<MessagesResponse>(url);
}

/**
 * Send a message to a thread and get back a streaming Response.
 * Use fetch directly (not apiFetch) for streaming support.
 */
export function sendMessage(threadId: string, content: string): Promise<Response> {
  const tkn = getToken();
  return fetch(`${API_URL}/threads/${threadId}/messages`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(tkn ? { Authorization: `Bearer ${tkn}` } : {}),
    },
    credentials: 'include',
    body: JSON.stringify({ content }),
  });
}
