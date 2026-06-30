'use client';

import React, { useState, FormEvent } from 'react';
import { useAuth } from '@/lib/auth-context';
import { createMeeting, teamsJoin, meetImport, Meeting } from '@/lib/api';

export default function DashboardPage() {
  const { token } = useAuth();

  // Teams join state
  const [teamsTitle, setTeamsTitle] = useState('');
  const [teamsJoinUrl, setTeamsJoinUrl] = useState('');
  const [teamsLoading, setTeamsLoading] = useState(false);
  const [teamsError, setTeamsError] = useState('');
  const [teamsResult, setTeamsResult] = useState<Meeting | null>(null);

  // Meet import state
  const [meetTitle, setMeetTitle] = useState('');
  const [meetTranscript, setMeetTranscript] = useState('');
  const [meetLoading, setMeetLoading] = useState(false);
  const [meetError, setMeetError] = useState('');
  const [meetResult, setMeetResult] = useState<Meeting | null>(null);

  const handleTeamsJoin = async (e: FormEvent) => {
    e.preventDefault();
    if (!token) return;
    setTeamsError('');
    setTeamsResult(null);
    setTeamsLoading(true);

    try {
      // 1. Create a draft meeting
      const meeting = await createMeeting(token, teamsTitle || 'Teams Meeting', new Date().toISOString());
      // 2. Join the Teams meeting
      const result = await teamsJoin(token, meeting.id, teamsJoinUrl);
      setTeamsResult(result);
    } catch (err: unknown) {
      setTeamsError(err instanceof Error ? err.message : 'Failed to join Teams meeting');
    } finally {
      setTeamsLoading(false);
    }
  };

  const handleMeetImport = async (e: FormEvent) => {
    e.preventDefault();
    if (!token) return;
    setMeetError('');
    setMeetResult(null);
    setMeetLoading(true);

    try {
      // 1. Create a draft meeting
      const meeting = await createMeeting(token, meetTitle || 'Meet Recording', new Date().toISOString());
      // 2. Import the transcript
      const result = await meetImport(token, meeting.id, meetTranscript);
      setMeetResult(result);
    } catch (err: unknown) {
      setMeetError(err instanceof Error ? err.message : 'Failed to import transcript');
    } finally {
      setMeetLoading(false);
    }
  };

  const getStatusClass = (status: string) => `status-badge status-${status}`;

  return (
    <div className="dashboard-content">
      <h2>Connect a Meeting</h2>
      <p className="dashboard-subtitle">
        Choose how you want to bring a meeting into MeetMind
      </p>

      <div className="meeting-cards">
        {/* Teams Join Card */}
        <div className="meeting-card glass-card" id="teams-card">
          <h3>
            <span className="icon icon-teams">📞</span>
            Join a Teams Meeting
          </h3>
          <p>
            Paste a Teams meeting join URL and our bot will join the call,
            capture the transcript, and bring it here.
          </p>

          <form className="form-inner" onSubmit={handleTeamsJoin} id="teams-join-form">
            <input
              id="teams-title-input"
              type="text"
              className="input-field"
              placeholder="Meeting title (optional)"
              value={teamsTitle}
              onChange={(e) => setTeamsTitle(e.target.value)}
            />
            <input
              id="teams-url-input"
              type="url"
              className="input-field"
              placeholder="https://teams.microsoft.com/l/meetup-join/..."
              value={teamsJoinUrl}
              onChange={(e) => setTeamsJoinUrl(e.target.value)}
              required
            />
            {teamsError && <div className="error-message" id="teams-error">{teamsError}</div>}
            <button type="submit" className="btn-primary" id="teams-join-btn" disabled={teamsLoading}>
              <span>{teamsLoading ? <span className="spinner" /> : '🚀 Join Meeting'}</span>
            </button>
          </form>

          {teamsResult && (
            <div className="result-box" id="teams-result">
              <h4>Meeting Created</h4>
              <div className="result-row">
                <span className="label">Status</span>
                <span className={getStatusClass(teamsResult.status)}>{teamsResult.status}</span>
              </div>
              <div className="result-row">
                <span className="label">Source</span>
                <span className="value">{teamsResult.source}</span>
              </div>
              <div className="result-row">
                <span className="label">ID</span>
                <span className="value" style={{ fontSize: '0.8rem', fontFamily: 'monospace' }}>
                  {teamsResult.id}
                </span>
              </div>
            </div>
          )}
        </div>

        {/* Meet Import Card */}
        <div className="meeting-card glass-card" id="meet-card">
          <h3>
            <span className="icon icon-meet">📝</span>
            Import a Meet Transcript
          </h3>
          <p>
            Paste an exported Google Meet transcript. The meeting must have
            ended with transcription enabled.
          </p>

          <form className="form-inner" onSubmit={handleMeetImport} id="meet-import-form">
            <input
              id="meet-title-input"
              type="text"
              className="input-field"
              placeholder="Meeting title (optional)"
              value={meetTitle}
              onChange={(e) => setMeetTitle(e.target.value)}
            />
            <textarea
              id="meet-transcript-input"
              className="textarea-field"
              placeholder="Paste your Google Meet transcript here..."
              value={meetTranscript}
              onChange={(e) => setMeetTranscript(e.target.value)}
              required
            />
            {meetError && <div className="error-message" id="meet-error">{meetError}</div>}
            <button type="submit" className="btn-primary" id="meet-import-btn" disabled={meetLoading}>
              <span>{meetLoading ? <span className="spinner" /> : '📥 Import Transcript'}</span>
            </button>
          </form>

          {meetResult && (
            <div className="result-box" id="meet-result">
              <h4>Meeting Created</h4>
              <div className="result-row">
                <span className="label">Status</span>
                <span className={getStatusClass(meetResult.status)}>{meetResult.status}</span>
              </div>
              <div className="result-row">
                <span className="label">Source</span>
                <span className="value">{meetResult.source}</span>
              </div>
              <div className="result-row">
                <span className="label">ID</span>
                <span className="value" style={{ fontSize: '0.8rem', fontFamily: 'monospace' }}>
                  {meetResult.id}
                </span>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
