import React from 'react';

const PHASES = [
  { id: 'p0',  label: 'Memory Clear',         icon: '🧹' },
  { id: 'p1a', label: 'Analyst Analysis',      icon: '🔍' },
  { id: 'p1b', label: 'Risk Assessment',       icon: '🛡️' },
  { id: 'p2a', label: 'Conference',            icon: '💬' },
  { id: 'p3',  label: 'PM Decision',           icon: '⚖️' },
  { id: 'p5',  label: 'Settlement',            icon: '📊' },
];

/**
 * Shows which pipeline phase is currently active for the current trading day.
 * activePhaseId: null means no day in progress (between days).
 * completedPhaseIds: set of phase ids that have finished.
 */
export default function PhaseProgressBar({ activePhaseId, completedPhaseIds = new Set(), currentDate }) {
  if (!currentDate) return null;

  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: 0,
      padding: '8px 16px',
      background: '#f8f8f8',
      borderBottom: '1px solid #e0e0e0',
      overflowX: 'auto',
      flexShrink: 0,
    }}>
      <span style={{ fontSize: 11, color: '#888', marginRight: 10, whiteSpace: 'nowrap', fontFamily: 'monospace' }}>
        {currentDate}
      </span>

      {PHASES.map((phase, idx) => {
        const isActive = phase.id === activePhaseId;
        const isDone   = completedPhaseIds.has(phase.id);

        return (
          <React.Fragment key={phase.id}>
            {idx > 0 && (
              <div style={{
                width: 20,
                height: 1,
                background: isDone || completedPhaseIds.has(PHASES[idx - 1]?.id) ? '#4CAF50' : '#ddd',
                flexShrink: 0,
              }} />
            )}
            <div title={phase.label} style={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              gap: 2,
              padding: '4px 8px',
              borderRadius: 6,
              background: isActive ? '#1a1a2e' : isDone ? '#e8f5e9' : '#f0f0f0',
              border: isActive ? '2px solid #615CED' : isDone ? '1px solid #4CAF50' : '1px solid #ddd',
              minWidth: 64,
              flexShrink: 0,
              transition: 'all 0.3s ease',
              boxShadow: isActive ? '0 0 8px rgba(97,92,237,0.4)' : 'none',
            }}>
              <span style={{ fontSize: 14 }}>{phase.icon}</span>
              <span style={{
                fontSize: 9,
                fontWeight: isActive ? 700 : 500,
                color: isActive ? '#fff' : isDone ? '#4CAF50' : '#888',
                textAlign: 'center',
                lineHeight: 1.2,
                letterSpacing: '0.3px',
                fontFamily: 'monospace',
                whiteSpace: 'nowrap',
              }}>
                {phase.label}
              </span>
              {isActive && (
                <div style={{
                  width: 6, height: 6, borderRadius: '50%',
                  background: '#615CED',
                  animation: 'pulse 1s infinite',
                }} />
              )}
            </div>
          </React.Fragment>
        );
      })}
    </div>
  );
}
