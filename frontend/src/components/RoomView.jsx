import React, { useEffect, useMemo, useRef, useState, useCallback } from 'react';
import { ASSETS, SCENE_NATIVE, AGENT_SEATS, AGENTS } from '../config/constants';
import AgentCard from './AgentCard';
import { getModelIcon } from '../utils/modelIcons';

/**
 * Custom hook to load an image
 */
function useImage(src) {
  const [img, setImg] = useState(null);
  useEffect(() => {
    if (!src) {
      setImg(null);
      return;
    }
    // Reset image state when backend changes
    setImg(null);
    const image = new Image();
    image.src = src;
    image.onload = () => setImg(image);
    image.onerror = () => {
      console.error(`Failed to load image: ${src}`);
      setImg(null);
    };
    // Cleanup: cancel loading if backend changes
    return () => {
      image.onload = null;
      image.onerror = null;
    };
  }, [src]);
  return img;
}

/**
 * Get rank medal/trophy for display
 */
function getRankMedal(rank) {
  if (rank === 1) return '🏆';
  if (rank === 2) return '🥈';
  if (rank === 3) return '🥉';
  return null;
}

// PM（打手势者，index 0）前臂+手 区域——锚定到座位[0]（跨缩放稳定）：
//   dx：框左边相对座位x的水平偏移（手在头右侧）；dyPx：框底相对座位点的像素偏移；w/h：框宽高（场景比例）
const PM_HAND = { dx: 0.022, dyPx: 58, w: 0.042, h: 0.065 };

/**
 * 从气泡的时间戳推断交易日（demo 的时间戳是交易日的毫秒数）。
 */
function deriveDate(bubble) {
  if (!bubble) return null;
  if (bubble.date) return bubble.date;
  const ts = bubble.timestamp ?? bubble.ts;
  if (typeof ts === 'number' && ts > 1e9) {
    return new Date(ts).toISOString().slice(0, 10);
  }
  if (typeof ts === 'string' && ts.length >= 10) {
    return ts.slice(0, 10);
  }
  return null;
}

/**
 * Room View Component
 * Displays the conference room with agents, speech bubbles, and agent cards
 * Supports click and hover (1.5s) to show agent performance cards
 * Supports replay mode - completely independent from live mode
 */
export default function RoomView({ bubbles, bubbleFor, leaderboard, feed, onJumpToMessage, currentDate }) {
  const canvasRef = useRef(null);
  const containerRef = useRef(null);

  // Agent selection and hover state
  const [selectedAgent, setSelectedAgent] = useState(null);
  const [hoveredAgent, setHoveredAgent] = useState(null);
  const [isClosing, setIsClosing] = useState(false);
  const hoverTimerRef = useRef(null);
  const closeTimerRef = useRef(null);

  // Bubble expansion state
  const [expandedBubbles, setExpandedBubbles] = useState({});

  // Hidden bubbles (locally dismissed)
  const [hiddenBubbles, setHiddenBubbles] = useState({});

  // Handle bubble close
  const handleCloseBubble = (agentId, bubbleKey, e) => {
    e.stopPropagation();
    setHiddenBubbles(prev => ({
      ...prev,
      [bubbleKey]: true
    }));
  };

  // Replay state (must be defined before using in useMemo)
  const [isReplaying, setIsReplaying] = useState(false);
  const [replayBubbles, setReplayBubbles] = useState({});
  const [modeTransition, setModeTransition] = useState(null); // 'entering-replay' | 'exiting-replay' | null
  const [isPaused, setIsPaused] = useState(false);
  const replayTimerRef = useRef(null);
  const replayTimeoutsRef = useRef([]);
  const replayStateRef = useRef({ messages: [], currentIndex: 0 });

  // Background image
  const roomBgSrc = ASSETS.roomBg;

  const bgImg = useImage(roomBgSrc);

  // Calculate scale to fit canvas in container (80% of available space)
  const [scale, setScale] = useState(0.8);

  useEffect(() => {
    const updateScale = () => {
      const container = containerRef.current;
      if (!container) return;

      const { clientWidth, clientHeight } = container;
      if (clientWidth <= 0 || clientHeight <= 0) return;

      const scaleX = clientWidth / SCENE_NATIVE.width;
      const scaleY = clientHeight / SCENE_NATIVE.height;
      const newScale = Math.min(scaleX, scaleY, 1.0) * 0.8; // Scale to 80% of original size
      setScale(Math.max(0.3, newScale));
    };

    updateScale();
    const resizeObserver = new ResizeObserver(updateScale);
    if (containerRef.current) {
      resizeObserver.observe(containerRef.current);
    }
    window.addEventListener('resize', updateScale);

    return () => {
      resizeObserver.disconnect();
      window.removeEventListener('resize', updateScale);
    };
  }, []);

  // Set canvas size
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    canvas.width = SCENE_NATIVE.width;
    canvas.height = SCENE_NATIVE.height;

    const displayWidth = Math.round(SCENE_NATIVE.width * scale);
    const displayHeight = Math.round(SCENE_NATIVE.height * scale);
    canvas.style.width = `${displayWidth}px`;
    canvas.style.height = `${displayHeight}px`;
  }, [scale]);

  // Draw room background
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    ctx.imageSmoothingEnabled = false;

    // Clear canvas first
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // Draw image if loaded
    if (bgImg) {
      ctx.drawImage(bgImg, 0, 0, SCENE_NATIVE.width, SCENE_NATIVE.height);
    }
  }, [bgImg, scale, roomBgSrc]);

  // Determine which agents are speaking
  const speakingAgents = useMemo(() => {
    const speaking = {};
    AGENTS.forEach(agent => {
      const bubble = bubbleFor(agent.name);
      speaking[agent.id] = !!bubble;
    });
    return speaking;
  }, [bubbles, bubbleFor]);

  // Find agent data from leaderboard
  const getAgentData = (agentId) => {
    const agent = AGENTS.find(a => a.id === agentId);
    if (!agent) return null;

    // If no leaderboard data, return agent with default stats
    if (!leaderboard || !Array.isArray(leaderboard)) {
      return {
        ...agent,
        bull: { n: 0, win: 0, unknown: 0 },
        bear: { n: 0, win: 0, unknown: 0 },
        winRate: null,
        signals: [],
        rank: null
      };
    }

    const leaderboardData = leaderboard.find(lb => lb.agentId === agentId);

    // If agent not in leaderboard, return agent with default stats
    if (!leaderboardData) {
      return {
        ...agent,
        bull: { n: 0, win: 0, unknown: 0 },
        bear: { n: 0, win: 0, unknown: 0 },
        winRate: null,
        signals: [],
        rank: null
      };
    }

    // Merge data but preserve the correct avatar from AGENTS config
    return {
      ...agent,
      ...leaderboardData,
      avatar: agent.avatar  // Always use the frontend's avatar URL
    };
  };

  // Get agent rank for display
  const getAgentRank = (agentId) => {
    const agentData = getAgentData(agentId);
    return agentData?.rank || null;
  };

  // Handle agent click
  const handleAgentClick = (agentId) => {
    // Cancel any closing animation
    if (closeTimerRef.current) {
      clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
    setIsClosing(false);

    const agentData = getAgentData(agentId);
    if (agentData) {
      setSelectedAgent(agentData);
    }
  };

  // Handle agent hover
  const handleAgentMouseEnter = (agentId) => {
    setHoveredAgent(agentId);
    // Clear any existing timer
    if (hoverTimerRef.current) {
      clearTimeout(hoverTimerRef.current);
      hoverTimerRef.current = null;
    }
    // Cancel any closing animation
    if (closeTimerRef.current) {
      clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
    setIsClosing(false);

    // If there's already a selected agent, switch immediately
    // Otherwise, show after a short delay (0ms = immediate)
    const agentData = getAgentData(agentId);
    if (agentData) {
      if (selectedAgent) {
        // Already have a card open, switch immediately
        setSelectedAgent(agentData);
      } else {
        // No card open, show after delay (currently 0ms = immediate)
        hoverTimerRef.current = setTimeout(() => {
          setSelectedAgent(agentData);
          hoverTimerRef.current = null;
        }, 0);
      }
    }
  };

  const handleAgentMouseLeave = () => {
    setHoveredAgent(null);
    // Clear timer if mouse leaves before 1.5 seconds
    if (hoverTimerRef.current) {
      clearTimeout(hoverTimerRef.current);
      hoverTimerRef.current = null;
    }
  };

  // Handle closing with animation
  const handleClose = () => {
    setIsClosing(true);
    // Wait for animation to complete before removing
    closeTimerRef.current = setTimeout(() => {
      setSelectedAgent(null);
      setIsClosing(false);
      closeTimerRef.current = null;
    }, 200); // Match the slideUp animation duration
  };

  // Cleanup timer on unmount
  useEffect(() => {
    return () => {
      if (hoverTimerRef.current) {
        clearTimeout(hoverTimerRef.current);
      }
      if (closeTimerRef.current) {
        clearTimeout(closeTimerRef.current);
      }
      // Clean up replay timers
      if (replayTimerRef.current) {
        clearTimeout(replayTimerRef.current);
      }
      replayTimeoutsRef.current.forEach(timeoutId => clearTimeout(timeoutId));
      replayTimeoutsRef.current = [];
    };
  }, []);

  // Show replay button when not in replay mode and has feed history
  const showReplayButton = !isReplaying && feed && feed.length > 0;

  // Start replay with feed data
  const handleReplayClick = useCallback(() => {
    if (!feed || feed.length === 0) {
      return;
    }
    startReplay(feed);
  }, [feed]);

  // Extract agent messages from feed items
  const extractAgentMessages = useCallback((feedItems) => {
    const messages = [];

    feedItems.forEach((item, itemIndex) => {
      if (item.type === 'message' && item.data) {
        const msg = item.data;
        // Skip system messages
        if (msg.agent === 'System') return;
        // Find matching agent
        const agent = AGENTS.find(a =>
          a.id === msg.agentId ||
          a.name === msg.agent
        );
        if (agent) {
          messages.push({
            feedItemId: item.id,
            agentId: agent.id,
            agentName: agent.name,
            content: msg.content,
            timestamp: msg.timestamp
          });
        }
      } else if (item.type === 'conference' && item.data?.messages) {
        item.data.messages.forEach((msg, msgIndex) => {
          if (msg.agent === 'System') return;
          const agent = AGENTS.find(a =>
            a.id === msg.agentId ||
            a.name === msg.agent
          );
          if (agent) {
            messages.push({
              feedItemId: item.id,
              agentId: agent.id,
              agentName: agent.name,
              content: msg.content,
              timestamp: msg.timestamp
            });
          }
        });
      }
    });

    return messages;
  }, []);

  // Show next message in replay
  const showNextMessage = useCallback(() => {
    const { messages, currentIndex } = replayStateRef.current;
    if (currentIndex >= messages.length) {
      // End replay
      setModeTransition('exiting-replay');
      setTimeout(() => {
        setModeTransition(null);
        setIsReplaying(false);
        setIsPaused(false);
        setReplayBubbles({});
        replayStateRef.current = { messages: [], currentIndex: 0 };
      }, 500);
      return;
    }

    const msg = messages[currentIndex];
    const bubbleId = `replay_${msg.agentId}_${currentIndex}`;

    // 字幕条模式：一次只保留一个发言者，避免重叠
    setReplayBubbles({
      [bubbleId]: {
        id: bubbleId,
        feedItemId: msg.feedItemId,
        agentId: msg.agentId,
        agentName: msg.agentName,
        text: msg.content,
        timestamp: msg.timestamp,
        ts: msg.timestamp,
        date: deriveDate({ timestamp: msg.timestamp })
      }
    });

    // Schedule next message
    replayStateRef.current.currentIndex = currentIndex + 1;
    // Wait longer before next bubble to match extended visibility (was 3s)
    const nextTimeout = setTimeout(() => {
      showNextMessage();
    }, 6000);
    replayTimerRef.current = nextTimeout;
    replayTimeoutsRef.current.push(nextTimeout);
  }, []);

  // Start replay with feed data
  const startReplay = useCallback((feedItems) => {
    if (!feedItems || feedItems.length === 0) {
      return;
    }

    const agentMessages = extractAgentMessages(feedItems).reverse();
    if (agentMessages.length === 0) {
      return;
    }

    // Store messages for pause/resume
    replayStateRef.current = { messages: agentMessages, currentIndex: 0 };

    // Start transition animation
    setModeTransition('entering-replay');
    setIsReplaying(true);
    setIsPaused(false);
    setReplayBubbles({});

    // Clear any existing timeouts
    replayTimeoutsRef.current.forEach(timeoutId => clearTimeout(timeoutId));
    replayTimeoutsRef.current = [];

    // Clear transition and start replay after animation completes
    setTimeout(() => {
      setModeTransition(null);
      showNextMessage();
    }, 500);
  }, [extractAgentMessages, showNextMessage]);

  // Pause replay
  const pauseReplay = useCallback(() => {
    if (replayTimerRef.current) {
      clearTimeout(replayTimerRef.current);
      replayTimerRef.current = null;
    }
    setIsPaused(true);
  }, []);

  // Resume replay
  const resumeReplay = useCallback(() => {
    setIsPaused(false);
    showNextMessage();
  }, [showNextMessage]);

  // Stop replay
  const stopReplay = useCallback(() => {
    // Clear all timeouts
    replayTimeoutsRef.current.forEach(timeoutId => clearTimeout(timeoutId));
    replayTimeoutsRef.current = [];

    if (replayTimerRef.current) {
      clearTimeout(replayTimerRef.current);
      replayTimerRef.current = null;
    }

    // Transition out of replay mode
    setModeTransition('exiting-replay');
    // Clear transition and replay state after animation completes
    setTimeout(() => {
      setModeTransition(null);
      setIsReplaying(false);
      setIsPaused(false);
      setReplayBubbles({});
      replayStateRef.current = { messages: [], currentIndex: 0 };
    }, 500);
  }, []);

  // Get bubble for specific agent (supports both live and replay mode)
  const getBubbleForAgent = useCallback((agentName) => {
    if (isReplaying) {
      // Find replay bubble for this agent
      const bubble = Object.values(replayBubbles).find(b => {
        const agent = AGENTS.find(a => a.id === b.agentId);
        return agent && agent.name === agentName;
      });
      return bubble || null;
    } else {
      // Use normal bubbleFor function
      return bubbleFor(agentName);
    }
  }, [isReplaying, replayBubbles, bubbleFor]);

  // 字幕条：在所有 agent 里挑出"最新发言者"，一次只显示一个，彻底避免重叠
  const activeSpeaker = useMemo(() => {
    let best = null;
    for (const agent of AGENTS) {
      const bubble = getBubbleForAgent(agent.name);
      if (!bubble) continue;
      const ts = bubble.ts ?? bubble.timestamp ?? 0;
      if (!best || ts >= best.ts) {
        best = { agent, bubble, ts };
      }
    }
    return best;
  }, [getBubbleForAgent, bubbles, replayBubbles, isReplaying]);

  // 顶部横幅日期：优先用当前发言气泡推断的交易日，回退到外部 currentDate
  const bannerDate = (activeSpeaker && deriveDate(activeSpeaker.bubble)) || currentDate || null;

  // PM 正在 Phase 3 拍板决策 → 触发"举手"动画
  const pmDeciding =
    activeSpeaker?.agent?.id === 'portfolio_manager' &&
    activeSpeaker?.bubble?.phase === 'p3';

  return (
    <div className="room-view">
      {/* Agents Indicator Bar */}
      <div className="room-agents-indicator">
        {AGENTS.map((agent, index) => {
          const rank = getAgentRank(agent.id);
          const medal = rank ? getRankMedal(rank) : null;
          const agentData = getAgentData(agent.id);
          const modelInfo = getModelIcon(agentData?.modelName, agentData?.modelProvider);

          return (
            <React.Fragment key={agent.id}>
              <div
                className={`agent-indicator ${speakingAgents[agent.id] ? 'speaking' : ''} ${hoveredAgent === agent.id ? 'hovered' : ''}`}
                onClick={() => handleAgentClick(agent.id)}
                onMouseEnter={() => handleAgentMouseEnter(agent.id)}
                onMouseLeave={handleAgentMouseLeave}
              >
                <div className="agent-avatar-wrapper">
                  <img
                    src={agent.avatar}
                    alt={agent.name}
                    className="agent-avatar"
                  />
                  <span className="agent-indicator-dot"></span>
                  {medal && (
                    <span className="agent-rank-medal">
                      {medal}
                    </span>
                  )}
                  {modelInfo.logoPath && (
                    <img
                      src={modelInfo.logoPath}
                      alt={modelInfo.provider}
                      className="agent-model-badge"
                      style={{
                        position: 'absolute',
                        top: -12,
                        right: -12,
                        width: 25,
                        height: 25,
                        borderRadius: '50%',
                        border: '2px solid #ffffff',
                        background: '#ffffff',
                        objectFit: 'contain',
                        padding: 2,
                        boxShadow: '0 2px 4px rgba(0,0,0,0.1)',
                        pointerEvents: 'none'
                      }}
                    />
                  )}
                </div>
                <span className="agent-name">{agent.name}</span>
              </div>
              {/* Divider after Risk Manager (index 1) */}
              {index === 1 && (
                <div style={{
                  width: 2,
                  height: 60,
                  background: 'linear-gradient(to bottom, transparent, #333333, transparent)',
                  margin: '0 12px',
                  alignSelf: 'center'
                }} />
              )}
            </React.Fragment>
          );
        })}

        {/* Hint Text */}
        <div className="agent-hint-text">
          Click avatar to view details
        </div>
      </div>

      {/* Room Canvas */}
      <div className="room-canvas-container" ref={containerRef}>
        {/* 顶部日期横幅：始终显示当前交易日 */}
        {bannerDate && (
          <div className="room-date-banner">
            📅 交易日 {bannerDate}
          </div>
        )}

        <div className="room-scene">
          <div className="room-scene-wrapper" style={{ width: Math.round(SCENE_NATIVE.width * scale), height: Math.round(SCENE_NATIVE.height * scale) }}>
            <canvas ref={canvasRef} className="room-canvas" />

            {/* PM 手部摆动：把 PM 前臂+手 这一小块裁出来，绕手腕轻摆，逼近"手在动" */}
            {activeSpeaker?.agent?.id === 'portfolio_manager' && roomBgSrc && AGENT_SEATS[0] && (() => {
              const seat0 = AGENT_SEATS[0];
              const sw = SCENE_NATIVE.width * scale;
              const sh = SCENE_NATIVE.height * scale;
              const left = Math.round((seat0.x + PM_HAND.dx) * sw);
              const bottom = Math.round(seat0.y * sh + PM_HAND.dyPx);
              const width = Math.round(PM_HAND.w * sw);
              const height = Math.round(PM_HAND.h * sh);
              const topFromTop = sh - bottom - height;
              return (
                <div
                  className="pm-hand"
                  style={{
                    left, bottom, width, height,
                    backgroundImage: `url(${roomBgSrc})`,
                    backgroundSize: `${Math.round(sw)}px ${Math.round(sh)}px`,
                    backgroundPosition: `${-left}px ${-Math.round(topFromTop)}px`,
                    backgroundRepeat: 'no-repeat',
                  }}
                />
              );
            })()}

            {/* 当前发言者头顶：讨论气泡 💬 */}
            {activeSpeaker && (() => {
              const idx = AGENTS.findIndex(a => a.id === activeSpeaker.agent.id);
              if (idx < 0 || !AGENT_SEATS[idx]) return null;
              const seat = AGENT_SEATS[idx];
              const scaledWidth = SCENE_NATIVE.width * scale;
              const scaledHeight = SCENE_NATIVE.height * scale;
              const left = Math.round(seat.x * scaledWidth);
              const bottom = Math.round(seat.y * scaledHeight) + 118; // 抬到头顶上方（避免挡脸）
              return (
                <div className="seat-gesture" style={{ left, bottom }}>
                  💬
                </div>
              );
            })()}
          </div>
        </div>

        {/* 底部字幕条：一次只显示当前发言者（头像 + 姓名 + 中文重点 + 日期） */}
        {activeSpeaker && (() => {
          const { agent, bubble } = activeSpeaker;
          const agentData = getAgentData(agent.id);
          const modelInfo = getModelIcon(agentData?.modelName, agentData?.modelProvider);
          const speakerDate = deriveDate(bubble);
          return (
            <div
              className={`room-subtitle-bar${pmDeciding ? ' deciding' : ''}`}
              onClick={() => onJumpToMessage && onJumpToMessage(bubble)}
              title="点击跳转到消息"
            >
              {/* PM 决策时刻：文字徽标（去掉 ✋ 举手 icon） */}
              {pmDeciding && (
                <div className="pm-decide-badge" aria-hidden="true">组合经理 · 拍板决策</div>
              )}
              <img src={agent.avatar} alt={agent.name} className="room-subtitle-avatar" />
              <div className="room-subtitle-body">
                <div className="room-subtitle-head">
                  {modelInfo.logoPath && (
                    <img src={modelInfo.logoPath} alt={modelInfo.provider} className="room-subtitle-model" />
                  )}
                  <span className="room-subtitle-name">{bubble.agentName || agent.name}</span>
                  {speakerDate && <span className="room-subtitle-date">{speakerDate}</span>}
                </div>
                <div className="room-subtitle-text">{bubble.text}</div>
              </div>
            </div>
          );
        })()}

        {/* Agent Card - Dropdown style below indicator bar */}
        {selectedAgent && (
          <>
            {/* Transparent overlay to close card */}
            <div
              className="agent-card-overlay"
              onClick={handleClose}
            />

            {/* Agent Card */}
            <AgentCard
              agent={selectedAgent}
              isClosing={isClosing}
              onClose={handleClose}
            />
          </>
        )}

        {/* Mode Transition Overlay - sweeps in the dark gradient */}
        {modeTransition === 'entering-replay' && (
          <div
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              right: 0,
              bottom: 0,
              background: 'radial-gradient(circle, rgba(0,0,0,0) 0%, rgba(0,0,0,0.3) 100%)',
              pointerEvents: 'none',
              zIndex: 40,
              clipPath: 'inset(0 100% 0 0)',
              animation: 'clipReveal 0.5s ease-out forwards'
            }}
          />
        )}

        {/* Mode Transition Overlay - sweeps out the dark gradient */}
        {modeTransition === 'exiting-replay' && (
          <div
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              right: 0,
              bottom: 0,
              background: 'radial-gradient(circle, rgba(0,0,0,0) 0%, rgba(0,0,0,0.3) 100%)',
              pointerEvents: 'none',
              zIndex: 40,
              clipPath: 'inset(0 0 0 0)',
              animation: 'clipHide 0.5s ease-out forwards'
            }}
          />
        )}

        {/* Replay Button */}
        {showReplayButton && (
          <div className="replay-button-container">
            <button
              className="replay-button"
              onClick={handleReplayClick}
              title="Replay feed history"
            >
              <span className="replay-icon">&#9654;&#9654;</span>
              <span>REPLAY</span>
            </button>
          </div>
        )}

        {/* Replay Mode Background + Indicator */}
        {isReplaying && !modeTransition && (
          <>
            <div
              style={{
                position: 'absolute',
                top: 0,
                left: 0,
                right: 0,
                bottom: 0,
                background: 'radial-gradient(circle, rgba(0,0,0,0) 0%, rgba(0,0,0,0.3) 100%)',
                pointerEvents: 'none',
                zIndex: 40
              }}
            />
            <div className="replay-indicator">
              <span className="replay-status">{isPaused ? 'PAUSED' : 'REPLAY MODE'}</span>
              <button
                className="replay-button"
                onClick={isPaused ? resumeReplay : pauseReplay}
                style={{ padding: '6px 12px' }}
              >
                <span>{isPaused ? '▶' : '⏸'}</span>
              </button>
              <button className="replay-button" onClick={stopReplay} style={{ padding: '6px 12px' }}>
                <span>■</span>
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

