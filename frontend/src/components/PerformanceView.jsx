import React from 'react';
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

const QUALITY_COLORS = {
  weighted: '#111111',
  winRate: '#0066FF',
  rm: '#0F9D58',
  grounding: '#F4B400',
  audit: '#DB4437',
  presentation: '#7E57C2',
};

function formatPercent(value) {
  if (value == null || Number.isNaN(value)) return 'N/A';
  return `${(value * 100).toFixed(1)}%`;
}

function getRankBadgeClass(rank) {
  if (rank === 1) return 'first';
  if (rank === 2) return 'second';
  if (rank === 3) return 'third';
  return '';
}

function buildRadarData(agent) {
  const breakdown = agent.scoreBreakdown || {};
  return [
    { metric: 'Win Rate', value: breakdown.winRate ?? 0 },
    { metric: 'RM', value: breakdown.rm ?? 0 },
    { metric: 'Grounding', value: breakdown.grounding ?? 0 },
    { metric: 'Audit', value: breakdown.audit ?? 0 },
    { metric: 'Presentation', value: breakdown.presentation ?? 0 },
  ];
}

function buildTrendData(agent) {
  const history = Array.isArray(agent.performanceHistory) ? agent.performanceHistory : [];
  return history
    .slice(-12)
    .map(point => ({
      date: point.date,
      weighted: point.weighted_score,
      winRate: point.win_rate,
      rm: point.rm,
      grounding: point.grounding,
      audit: point.audit,
      presentation: point.presentation,
    }));
}

function CustomTrendTooltip({ active, payload, label }) {
  if (!active || !payload || payload.length === 0) return null;

  return (
    <div style={{
      background: '#ffffff',
      border: '1px solid #d9d9d9',
      padding: '10px 12px',
      boxShadow: '0 4px 16px rgba(0, 0, 0, 0.08)',
      fontSize: 11,
    }}>
      <div style={{ fontWeight: 700, marginBottom: 6 }}>{label}</div>
      {payload.map(item => (
        <div key={item.dataKey} style={{ color: item.color, marginBottom: 2 }}>
          {item.name}: {formatPercent(item.value)}
        </div>
      ))}
    </div>
  );
}

/**
 * Performance View Component
 * Displays multi-dimensional leaderboard, radar scorecards, and trend lines.
 */
export default function PerformanceView({ leaderboard }) {
  const rankedAgents = Array.isArray(leaderboard)
    ? leaderboard.filter(agent => agent.agentId !== 'risk_manager' && agent.agentId !== 'portfolio_manager')
    : [];

  return (
    <div>
      <div className="section">
        <div className="section-header">
          <h2 className="section-title">Agent Performance - Multi-Dimensional Leaderboard</h2>
        </div>

        {rankedAgents.length === 0 ? (
          <div className="empty-state">No leaderboard data available</div>
        ) : (
          <div className="table-wrapper">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Rank</th>
                  <th>Agent</th>
                  <th>Weighted Score</th>
                  <th>Win Rate</th>
                  <th>RM</th>
                  <th>Grounding</th>
                  <th>Audit</th>
                  <th>Presentation</th>
                  <th>Total Signals</th>
                </tr>
              </thead>
              <tbody>
                {rankedAgents.map(agent => {
                  const bullTotal = agent.bull?.n || 0;
                  const bearTotal = agent.bear?.n || 0;
                  const totalSignals = bullTotal + bearTotal;
                  const breakdown = agent.scoreBreakdown || {};
                  const weightedScore = agent.weightedScore;

                  return (
                    <tr key={agent.agentId}>
                      <td>
                        <span className={`rank-badge ${getRankBadgeClass(agent.rank)}`}>
                          {agent.rank === 1 ? '★ 1' : agent.rank}
                        </span>
                      </td>
                      <td>
                        <div style={{ fontWeight: 700, color: '#000000' }}>{agent.name}</div>
                        <div style={{ fontSize: 10, color: '#666666' }}>{agent.role}</div>
                      </td>
                      <td style={{ fontWeight: 700, color: '#111111' }}>
                        {formatPercent(weightedScore)}
                      </td>
                      <td style={{ color: QUALITY_COLORS.winRate }}>{formatPercent(agent.winRate)}</td>
                      <td style={{ color: QUALITY_COLORS.rm }}>{formatPercent(breakdown.rm)}</td>
                      <td style={{ color: QUALITY_COLORS.grounding }}>{formatPercent(breakdown.grounding)}</td>
                      <td style={{ color: QUALITY_COLORS.audit }}>{formatPercent(breakdown.audit)}</td>
                      <td style={{ color: QUALITY_COLORS.presentation }}>{formatPercent(breakdown.presentation)}</td>
                      <td style={{ fontWeight: 700 }}>{totalSignals}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {rankedAgents.length > 0 && (
        <div className="section" style={{ marginTop: 32 }}>
          <div className="section-header">
            <h2 className="section-title">Quality Radar & Score Trends</h2>
          </div>

          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(460px, 1fr))',
            gap: 24,
          }}>
            {rankedAgents.map(agent => {
              const radarData = buildRadarData(agent);
              const trendData = buildTrendData(agent);

              return (
                <div
                  key={agent.agentId}
                  style={{
                    border: '1px solid #e0e0e0',
                    background: '#fafafa',
                    padding: 18,
                  }}
                >
                  <div style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'baseline',
                    marginBottom: 16,
                    gap: 12,
                  }}>
                    <div>
                      <div style={{
                        fontWeight: 700,
                        fontSize: 13,
                        letterSpacing: 1,
                        textTransform: 'uppercase',
                        color: '#000000',
                      }}>
                        {agent.name}
                      </div>
                      <div style={{ fontSize: 11, color: '#666666', marginTop: 4 }}>
                        Weighted Score {formatPercent(agent.weightedScore)} ·
                        Quality Count {agent.qualityScores?.count ?? 0}
                      </div>
                    </div>
                    <div style={{
                      fontSize: 11,
                      color: '#666666',
                      textAlign: 'right',
                    }}>
                      <div>Latest Win Rate: {formatPercent(agent.winRate)}</div>
                      <div>Overall Quality: {formatPercent(agent.scoreBreakdown?.overallQuality)}</div>
                    </div>
                  </div>

                  <div style={{
                    display: 'grid',
                    gridTemplateColumns: 'minmax(220px, 0.9fr) minmax(0, 1.3fr)',
                    gap: 16,
                    alignItems: 'stretch',
                  }}>
                    <div style={{ minHeight: 260 }}>
                      <ResponsiveContainer width="100%" height={260}>
                        <RadarChart outerRadius="72%" data={radarData}>
                          <PolarGrid stroke="#d9d9d9" />
                          <PolarAngleAxis dataKey="metric" tick={{ fontSize: 11, fill: '#333333' }} />
                          <PolarRadiusAxis domain={[0, 1]} tickFormatter={value => `${Math.round(value * 100)}%`} tick={{ fontSize: 10 }} />
                          <Radar
                            name="Score"
                            dataKey="value"
                            stroke="#111111"
                            fill="rgba(17, 17, 17, 0.18)"
                            fillOpacity={1}
                            strokeWidth={2}
                          />
                          <Tooltip formatter={value => formatPercent(value)} />
                        </RadarChart>
                      </ResponsiveContainer>
                    </div>

                    <div style={{ minHeight: 260 }}>
                      {trendData.length === 0 ? (
                        <div style={{
                          height: 260,
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          background: '#ffffff',
                          border: '1px dashed #d0d0d0',
                          color: '#888888',
                          fontSize: 12,
                        }}>
                          No performance history yet
                        </div>
                      ) : (
                        <ResponsiveContainer width="100%" height={260}>
                          <LineChart data={trendData} margin={{ top: 8, right: 12, left: -16, bottom: 0 }}>
                            <CartesianGrid strokeDasharray="3 3" stroke="#ededed" />
                            <XAxis dataKey="date" tick={{ fontSize: 10 }} />
                            <YAxis domain={[0, 1]} tickFormatter={value => `${Math.round(value * 100)}%`} tick={{ fontSize: 10 }} />
                            <Tooltip content={<CustomTrendTooltip />} />
                            <Legend wrapperStyle={{ fontSize: 10 }} />
                            <Line type="monotone" dataKey="weighted" name="Weighted" stroke={QUALITY_COLORS.weighted} strokeWidth={2.5} dot={false} />
                            <Line type="monotone" dataKey="winRate" name="Win Rate" stroke={QUALITY_COLORS.winRate} strokeWidth={1.8} dot={false} />
                            <Line type="monotone" dataKey="rm" name="RM" stroke={QUALITY_COLORS.rm} strokeWidth={1.6} dot={false} />
                            <Line type="monotone" dataKey="grounding" name="Grounding" stroke={QUALITY_COLORS.grounding} strokeWidth={1.6} dot={false} />
                            <Line type="monotone" dataKey="audit" name="Audit" stroke={QUALITY_COLORS.audit} strokeWidth={1.6} dot={false} />
                            <Line type="monotone" dataKey="presentation" name="Presentation" stroke={QUALITY_COLORS.presentation} strokeWidth={1.6} dot={false} />
                          </LineChart>
                        </ResponsiveContainer>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {rankedAgents.length > 0 && rankedAgents.some(agent => agent.signals && agent.signals.length > 0) && (
        <div className="section" style={{ marginTop: 32 }}>
          <div className="section-header">
            <h2 className="section-title">Signal History</h2>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(400px, 1fr))', gap: 20 }}>
            {rankedAgents.map(agent => {
              if (!agent.signals || agent.signals.length === 0) return null;

              const sortedSignals = [...agent.signals].sort((a, b) =>
                new Date(b.date).getTime() - new Date(a.date).getTime()
              );

              return (
                <div key={agent.agentId} style={{
                  border: '1px solid #e0e0e0',
                  padding: 16,
                  background: '#fafafa',
                }}>
                  <div style={{
                    fontWeight: 700,
                    fontSize: 12,
                    marginBottom: 12,
                    paddingBottom: 8,
                    borderBottom: '2px solid #000000',
                    letterSpacing: 1,
                    textTransform: 'uppercase',
                  }}>
                    {agent.name}
                  </div>
                  <div style={{
                    maxHeight: 500,
                    overflowY: 'auto',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 8,
                  }}>
                    {sortedSignals.map((signal, idx) => {
                      const signalType = signal.signal.toLowerCase();
                      const isBull = signalType.includes('bull') || signalType === 'long';
                      const isBear = signalType.includes('bear') || signalType === 'short';
                      const isNeutral = signalType.includes('neutral') || signalType === 'hold';
                      const resultStatus = signal.is_correct;
                      const isCorrect = resultStatus === true;
                      const isResultUnknown = resultStatus === 'unknown' || resultStatus === null || typeof resultStatus === 'undefined';
                      const realReturnValue = signal.real_return;
                      const hasRealReturn = typeof realReturnValue === 'number' && Number.isFinite(realReturnValue);
                      const realReturnDisplay = hasRealReturn
                        ? `${realReturnValue >= 0 ? '+' : ''}${(realReturnValue * 100).toFixed(2)}%`
                        : 'Unknown';
                      const realReturnColor = hasRealReturn
                        ? (realReturnValue >= 0 ? '#00C853' : '#FF1744')
                        : '#999999';
                      const statusColor = isResultUnknown ? '#999999' : (isCorrect ? '#00C853' : '#FF1744');
                      const statusSymbol = isResultUnknown ? '?' : (isCorrect ? '✓' : '✗');

                      return (
                        <div key={idx} style={{
                          fontSize: 11,
                          fontFamily: '"Courier New", monospace',
                          lineHeight: 1.4,
                          padding: '8px 10px',
                          background: '#ffffff',
                          border: '1px solid #e0e0e0',
                          display: 'flex',
                          justifyContent: 'space-between',
                          alignItems: 'center',
                        }}>
                          <div style={{ flex: 1 }}>
                            <span style={{
                              color: '#666666',
                              fontSize: 10,
                              marginRight: 10,
                              fontWeight: 600,
                            }}>
                              {signal.date}
                            </span>
                            <span style={{
                              fontWeight: 700,
                              color: isBull ? '#00C853' : isBear ? '#FF1744' : '#999999',
                            }}>
                              {signal.ticker}
                            </span>
                            <span style={{
                              marginLeft: 6,
                              color: isBull ? '#00C853' : isBear ? '#FF1744' : '#999999',
                              fontSize: 12,
                            }}>
                              {isBull ? 'Bull' : isBear ? 'Bear' : 'Neutral'}
                            </span>
                            {!isNeutral && (
                              <span style={{
                                marginLeft: 8,
                                fontSize: 10,
                                color: realReturnColor,
                              }}>
                                {realReturnDisplay}
                              </span>
                            )}
                          </div>
                          {!isNeutral && (
                            <span style={{
                              fontSize: 14,
                              marginLeft: 10,
                              color: statusColor,
                            }}>
                              {statusSymbol}
                            </span>
                          )}
                        </div>
                      );
                    })}
                  </div>
                  <div style={{
                    marginTop: 10,
                    paddingTop: 8,
                    borderTop: '1px solid #e0e0e0',
                    fontSize: 10,
                    color: '#666666',
                    textAlign: 'center',
                  }}>
                    Total: {sortedSignals.length} signals
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
