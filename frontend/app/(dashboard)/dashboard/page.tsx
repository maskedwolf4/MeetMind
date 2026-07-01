'use client';

import React, { useState, useEffect, useRef, useCallback, FormEvent } from 'react';
import { useAuth } from '@/lib/auth-context';
import {
  Meeting, ThreadInfo, ChatMessage as ChatMsg,
  listMeetings, getMeeting, createMeeting, addAttendee,
  teamsJoin, importTranscript, processMeeting,
  getOrCreateThread, getMessages, sendMessage,
} from '@/lib/api';

// ================================================================== //
// Dashboard Page — Two-panel layout
// ================================================================== //
export default function DashboardPage() {
  const { user, token, logout } = useAuth();
  const [meetings, setMeetings] = useState<Meeting[]>([]);
  const [selectedMeeting, setSelectedMeeting] = useState<Meeting | null>(null);
  const [showModal, setShowModal] = useState(false);
  const [loading, setLoading] = useState(true);

  const fetchMeetings = useCallback(async () => {
    try {
      const data = await listMeetings();
      setMeetings(data);
    } catch { /* ignore */ }
    setLoading(false);
  }, []);

  useEffect(() => { fetchMeetings(); }, [fetchMeetings]);

  // Poll selected meeting for status changes
  useEffect(() => {
    if (!selectedMeeting || selectedMeeting.status === 'ready' || selectedMeeting.status === 'failed') return;
    const interval = setInterval(async () => {
      try {
        const updated = await getMeeting(selectedMeeting.id);
        setSelectedMeeting(updated);
        if (updated.status === 'ready' || updated.status === 'failed') {
          fetchMeetings();
        }
      } catch { /* ignore */ }
    }, 3000);
    return () => clearInterval(interval);
  }, [selectedMeeting, fetchMeetings]);

  const handleMeetingCreated = (meeting: Meeting) => {
    setShowModal(false);
    setSelectedMeeting(meeting);
    fetchMeetings();
  };

  return (
    <div className="app-layout">
      {/* Sidebar */}
      <aside className="sidebar">
        <div className="sidebar-header">
          <h1 className="sidebar-brand">MeetMind</h1>
          <div className="sidebar-user">
            <div className="avatar">{user?.name?.[0]?.toUpperCase() || 'U'}</div>
            <span className="sidebar-user-name">{user?.name}</span>
            <button className="btn-icon" onClick={logout} title="Sign out" id="logout-btn">
              ⏻
            </button>
          </div>
        </div>

        <button className="btn-new-meeting" onClick={() => setShowModal(true)} id="new-meeting-btn">
          ＋ New Meeting
        </button>

        <div className="meeting-list">
          {loading ? (
            <div className="meeting-list-empty"><div className="spinner" /></div>
          ) : meetings.length === 0 ? (
            <div className="meeting-list-empty">
              <p>No meetings yet</p>
              <p className="text-muted">Create one to get started</p>
            </div>
          ) : (
            <MeetingList
              meetings={meetings}
              selectedId={selectedMeeting?.id}
              onSelect={(m) => setSelectedMeeting(m)}
            />
          )}
        </div>
      </aside>

      {/* Main content */}
      <main className="main-content">
        {!selectedMeeting ? (
          <div className="empty-state">
            <div className="empty-state-icon">💬</div>
            <h2>Select a meeting to start chatting</h2>
            <p>Choose a meeting from the sidebar, or create a new one.</p>
          </div>
        ) : selectedMeeting.status === 'ready' ? (
          <ChatPanel meeting={selectedMeeting} />
        ) : (
          <StatusPanel meeting={selectedMeeting} onRefresh={fetchMeetings} />
        )}
      </main>

      {/* New meeting modal */}
      {showModal && (
        <NewMeetingModal
          onClose={() => setShowModal(false)}
          onCreated={handleMeetingCreated}
        />
      )}
    </div>
  );
}


// ================================================================== //
// Meeting List
// ================================================================== //
function MeetingList({
  meetings, selectedId, onSelect,
}: {
  meetings: Meeting[];
  selectedId?: string;
  onSelect: (m: Meeting) => void;
}) {
  const grouped = groupMeetings(meetings);

  return (
    <>
      {Object.entries(grouped).map(([group, items]) => (
        <div key={group}>
          <div className="meeting-group-label">{group}</div>
          {items.map((m) => (
            <button
              key={m.id}
              className={`meeting-item ${m.id === selectedId ? 'active' : ''}`}
              onClick={() => onSelect(m)}
              id={`meeting-${m.id}`}
            >
              <div className="meeting-item-header">
                <span className="meeting-item-title">
                  {m.title.length > 40 ? m.title.slice(0, 40) + '…' : m.title}
                </span>
                <span className={`status-dot status-dot-${getStatusColor(m.status)}`} />
              </div>
              <div className="meeting-item-meta">
                <span>{formatMeetingDate(m.meeting_datetime)}</span>
                <span className={`source-pill source-${m.source}`}>
                  {getSourceLabel(m.source)}
                </span>
              </div>
            </button>
          ))}
        </div>
      ))}
    </>
  );
}


// ================================================================== //
// Chat Panel
// ================================================================== //
function ChatPanel({ meeting }: { meeting: Meeting }) {
  const { user } = useAuth();
  const [thread, setThread] = useState<ThreadInfo | null>(null);
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [streamingContent, setStreamingContent] = useState('');
  const [error, setError] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Load thread + messages
  useEffect(() => {
    (async () => {
      try {
        const t = await getOrCreateThread(meeting.id);
        setThread(t);
        if (t.message_count > 0) {
          const res = await getMessages(t.thread_id);
          setMessages(res.messages);
        }
      } catch (e: any) {
        setError(e.message || 'Failed to load thread');
      }
    })();
  }, [meeting.id]);

  // Auto-scroll
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streamingContent]);

  const handleSend = async (text?: string) => {
    const content = text || input.trim();
    if (!content || !thread || streaming) return;
    setInput('');
    setError('');

    // Optimistically add user message
    const userMsg: ChatMsg = {
      id: `temp-${Date.now()}`,
      role: 'user',
      content,
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, userMsg]);
    setStreaming(true);
    setStreamingContent('');

    try {
      const resp = await sendMessage(thread.thread_id, content);
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: 'Stream error' }));
        setError(err.detail || 'Failed to send message');
        setStreaming(false);
        return;
      }

      const reader = resp.body?.getReader();
      if (!reader) {
        setError('No stream reader');
        setStreaming(false);
        return;
      }

      const decoder = new TextDecoder();
      let accumulated = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const text = decoder.decode(value, { stream: true });
        const lines = text.split('\n');

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const dataStr = line.slice(6);
          try {
            const parsed = JSON.parse(dataStr);
            if (parsed.delta) {
              accumulated += parsed.delta;
              setStreamingContent(accumulated);
            } else if (parsed.done) {
              // Stream complete — add assistant message
              setMessages((prev) => [
                ...prev,
                {
                  id: `assistant-${Date.now()}`,
                  role: 'assistant',
                  content: accumulated,
                  created_at: new Date().toISOString(),
                },
              ]);
              setStreamingContent('');
            } else if (parsed.error) {
              setError(parsed.error);
            }
          } catch { /* skip malformed lines */ }
        }
      }
    } catch (e: any) {
      setError(e.message || 'Stream failed');
    }

    setStreaming(false);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  // Auto-resize textarea
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = Math.min(textareaRef.current.scrollHeight, 120) + 'px';
    }
  }, [input]);

  const suggestions = [
    'What are my action items?',
    'What decisions were made?',
    'What did I commit to?',
    'Summarise this meeting for me',
  ];

  const isEmpty = messages.length === 0 && !streamingContent;

  return (
    <div className="chat-panel">
      {/* Chat header */}
      <div className="chat-header">
        <div>
          <h2 className="chat-title">{meeting.title}</h2>
          <div className="chat-meta">
            <span>{formatMeetingDate(meeting.meeting_datetime)}</span>
            <span className={`source-pill source-${meeting.source}`}>
              {getSourceLabel(meeting.source)}
            </span>
          </div>
        </div>
      </div>

      {/* Messages */}
      <div className="chat-messages">
        {isEmpty ? (
          <div className="chat-welcome">
            <div className="chat-welcome-icon">🧠</div>
            <h3>Ask me anything about &apos;{meeting.title}&apos;</h3>
            <div className="suggestion-chips">
              {suggestions.map((s) => (
                <button
                  key={s}
                  className="suggestion-chip"
                  onClick={() => handleSend(s)}
                  id={`chip-${s.replace(/\s+/g, '-').toLowerCase()}`}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <>
            {messages.map((msg) => (
              <div key={msg.id} className={`message message-${msg.role}`}>
                {msg.role === 'assistant' && (
                  <div className="message-avatar">M</div>
                )}
                <div className="message-bubble">
                  <div className="message-role">
                    {msg.role === 'user' ? user?.name || 'You' : 'MeetMind'}
                  </div>
                  <div className="message-content">{msg.content}</div>
                  <div className="message-time">{formatTime(msg.created_at)}</div>
                </div>
              </div>
            ))}
            {streamingContent && (
              <div className="message message-assistant">
                <div className="message-avatar">M</div>
                <div className="message-bubble streaming">
                  <div className="message-role">MeetMind</div>
                  <div className="message-content">{streamingContent}<span className="cursor-blink">▊</span></div>
                </div>
              </div>
            )}
          </>
        )}
        {error && (
          <div className="chat-error">{error}</div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <div className="chat-input-area">
        <textarea
          ref={textareaRef}
          className="chat-input"
          placeholder="Ask about this meeting..."
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={streaming}
          rows={1}
          id="chat-input"
        />
        <button
          className="btn-send"
          onClick={() => handleSend()}
          disabled={!input.trim() || streaming}
          id="send-btn"
        >
          {streaming ? <span className="spinner" /> : '→'}
        </button>
      </div>
    </div>
  );
}


// ================================================================== //
// Status Panel (processing/awaiting/recording)
// ================================================================== //
function StatusPanel({ meeting, onRefresh }: { meeting: Meeting; onRefresh: () => void }) {
  const steps = [
    { key: 'join', label: 'Bot joining meeting' },
    { key: 'record', label: 'Recording in progress' },
    { key: 'process', label: 'Processing transcript' },
    { key: 'summarize', label: 'Generating summaries' },
  ];

  const statusMap: Record<string, number> = {
    draft: -1,
    awaiting_join: 0,
    recording: 1,
    processing: 2,
    summarizing: 3,
    ready: 4,
    failed: -2,
  };

  const currentStep = statusMap[meeting.status] ?? -1;
  const isFailed = meeting.status === 'failed';

  const handleRetry = async () => {
    try {
      await processMeeting(meeting.id);
      onRefresh();
    } catch { /* ignore */ }
  };

  return (
    <div className="status-panel">
      <h2>Processing: {meeting.title}</h2>
      <div className="status-stepper">
        {steps.map((step, i) => (
          <div
            key={step.key}
            className={`step ${i < currentStep ? 'done' : i === currentStep ? 'active' : ''} ${isFailed ? 'failed' : ''}`}
          >
            <div className="step-icon">
              {i < currentStep ? '✓' : i === currentStep ? <span className="spinner" /> : (i + 1)}
            </div>
            <span className="step-label">{step.label}</span>
          </div>
        ))}
      </div>
      {isFailed && (
        <div className="status-error">
          <p>Processing failed. Please try again.</p>
          <button className="btn-primary" onClick={handleRetry}>
            <span>Retry Processing</span>
          </button>
        </div>
      )}
    </div>
  );
}


// ================================================================== //
// New Meeting Modal
// ================================================================== //
function NewMeetingModal({
  onClose, onCreated,
}: {
  onClose: () => void;
  onCreated: (m: Meeting) => void;
}) {
  const [tab, setTab] = useState<'teams' | 'import' | 'manual'>('import');
  const [title, setTitle] = useState('');
  const [datetime, setDatetime] = useState('');
  const [emails, setEmails] = useState('');
  const [joinUrl, setJoinUrl] = useState('');
  const [transcript, setTranscript] = useState('');
  const [source, setSource] = useState<string>('meet_export');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [statusMsg, setStatusMsg] = useState('');

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError('');
    setStatusMsg('');
    setLoading(true);

    try {
      const meetingDatetime = datetime || new Date().toISOString();
      const actualSource = tab === 'teams' ? 'teams_live' : tab === 'manual' ? 'manual' : source;

      // 1. Create meeting
      setStatusMsg('Creating meeting...');
      const meeting = await createMeeting(title || 'New Meeting', meetingDatetime);

      // 2. Add attendees (skip silently on error)
      if (emails.trim()) {
        const emailList = emails.split(',').map((e) => e.trim()).filter(Boolean);
        for (const email of emailList) {
          await addAttendee(meeting.id, email);
        }
      }

      // 3. Source-specific action
      if (tab === 'teams') {
        setStatusMsg('Joining Teams meeting...');
        try {
          const result = await teamsJoin(meeting.id, joinUrl);
          onCreated(result);
        } catch (err: any) {
          if (err.status === 503) {
            setError('Teams live join is not configured. Use "Import Transcript" instead.');
          } else {
            setError(err.message || 'Failed to join Teams meeting');
          }
          setLoading(false);
          return;
        }
      } else {
        if (!transcript.trim()) {
          setError('Please paste a transcript');
          setLoading(false);
          return;
        }
        setStatusMsg('Importing transcript...');
        const result = await importTranscript(meeting.id, transcript);

        // Trigger processing
        setStatusMsg('Processing transcript...');
        try {
          await processMeeting(meeting.id);
        } catch { /* processing is async, ok to fail here */ }

        onCreated(result);
      }
    } catch (err: any) {
      setError(err.message || 'An error occurred');
    }
    setLoading(false);
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>New Meeting</h2>
          <button className="btn-icon" onClick={onClose}>✕</button>
        </div>

        <div className="modal-tabs">
          <button
            className={`modal-tab ${tab === 'teams' ? 'active' : ''}`}
            onClick={() => setTab('teams')}
          >
            📞 Join Teams Live
          </button>
          <button
            className={`modal-tab ${tab === 'import' ? 'active' : ''}`}
            onClick={() => setTab('import')}
          >
            📥 Import Transcript
          </button>
          <button
            className={`modal-tab ${tab === 'manual' ? 'active' : ''}`}
            onClick={() => setTab('manual')}
          >
            📋 Manual / Demo
          </button>
        </div>

        <form className="modal-form" onSubmit={handleSubmit}>
          <div className="form-group">
            <label>Meeting Title</label>
            <input
              className="input-field"
              placeholder="e.g. Sprint Planning"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              id="modal-title"
            />
          </div>

          <div className="form-group">
            <label>Date & Time</label>
            <input
              type="datetime-local"
              className="input-field"
              value={datetime}
              onChange={(e) => setDatetime(e.target.value)}
              id="modal-datetime"
            />
          </div>

          <div className="form-group">
            <label>Attendee Emails (comma-separated)</label>
            <input
              className="input-field"
              placeholder="alan@demo.com, bob@demo.com"
              value={emails}
              onChange={(e) => setEmails(e.target.value)}
              id="modal-emails"
            />
          </div>

          {tab === 'teams' && (
            <div className="form-group">
              <label>Teams Join URL</label>
              <input
                className="input-field"
                placeholder="https://teams.microsoft.com/l/meetup-join/..."
                value={joinUrl}
                onChange={(e) => setJoinUrl(e.target.value)}
                required
                id="modal-join-url"
              />
            </div>
          )}

          {tab === 'import' && (
            <div className="form-group">
              <label>Source</label>
              <select
                className="input-field"
                value={source}
                onChange={(e) => setSource(e.target.value)}
                id="modal-source"
              >
                <option value="teams_export">Teams Export</option>
                <option value="meet_export">Google Meet</option>
                <option value="manual">Other</option>
              </select>
            </div>
          )}

          {(tab === 'import' || tab === 'manual') && (
            <div className="form-group">
              <label>Transcript</label>
              <textarea
                className="textarea-field"
                placeholder="Paste your meeting transcript here..."
                value={transcript}
                onChange={(e) => setTranscript(e.target.value)}
                rows={8}
                id="modal-transcript"
              />
            </div>
          )}

          {error && <div className="error-message" id="modal-error">{error}</div>}
          {statusMsg && !error && <div className="status-message">{statusMsg}</div>}

          <button
            type="submit"
            className="btn-primary"
            disabled={loading}
            id="modal-submit"
          >
            <span>
              {loading ? <span className="spinner" /> : tab === 'teams' ? '🚀 Join Meeting' : '📥 Import & Process'}
            </span>
          </button>
        </form>
      </div>
    </div>
  );
}


// ================================================================== //
// Helpers
// ================================================================== //
function formatMeetingDate(iso: string): string {
  const d = new Date(iso);
  const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const time = d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true });
  return `${days[d.getDay()]} ${d.getDate()} ${months[d.getMonth()]} · ${time}`;
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true });
}

function getSourceLabel(source: string): string {
  const map: Record<string, string> = {
    teams_live: 'Teams Live',
    teams_export: 'Teams Export',
    meet_export: 'Meet',
    manual: 'Manual',
  };
  return map[source] || source;
}

function getStatusColor(status: string): string {
  if (['draft', 'awaiting_join'].includes(status)) return 'grey';
  if (['recording', 'processing', 'summarizing'].includes(status)) return 'yellow';
  if (status === 'ready') return 'green';
  if (status === 'failed') return 'red';
  return 'grey';
}

function groupMeetings(meetings: Meeting[]): Record<string, Meeting[]> {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const weekAgo = new Date(today.getTime() - 7 * 24 * 60 * 60 * 1000);

  const groups: Record<string, Meeting[]> = {};

  for (const m of meetings) {
    const d = new Date(m.meeting_datetime);
    let group: string;
    if (d >= today) group = 'Today';
    else if (d >= weekAgo) group = 'This Week';
    else group = 'Earlier';

    if (!groups[group]) groups[group] = [];
    groups[group].push(m);
  }

  return groups;
}
