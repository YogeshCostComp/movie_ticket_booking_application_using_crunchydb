/**
 * SRE Agent Orchestrator — Frontend JavaScript
 * Handles WebSocket, Chat, and Pipeline visualization.
 */

// ── State ──────────────────────────────────────────────────────────
let ws = null;
let reconnectTimer = null;
let agentTimer = null;
let agentStartTime = null;
const AGENT_ICONS = {
    log_agent: '📋', health_agent: '🏥', monitoring_agent: '📡',
    runbook_agent: '📕', trace_agent: '🔗', dashboard_agent: '📊',
    deployment_agent: '🚀', orchestrator: '🧠'
};

// ── Elements ───────────────────────────────────────────────────────
const chatMessages = document.getElementById('chatMessages');
const chatInput = document.getElementById('chatInput');
const btnSend = document.getElementById('btnSend');
const btnClear = document.getElementById('btnClear');
const btnClearPipeline = document.getElementById('btnClearPipeline');
const connectionStatus = document.getElementById('connectionStatus');
const statusText = connectionStatus.querySelector('.status-text');
const mcpBadge = document.getElementById('mcpBadge');
const pipelineSteps = document.getElementById('pipelineSteps');
const pipelineHistory = document.getElementById('pipelineHistory');
const activeAgentCard = document.getElementById('activeAgentCard');
const activeAgentIcon = document.getElementById('activeAgentIcon');
const activeAgentName = document.getElementById('activeAgentName');
const activeAgentId = document.getElementById('activeAgentId');
const activeAgentStatus = document.getElementById('activeAgentStatus');
const activeAgentTimerEl = document.getElementById('activeAgentTimer');

// ── WebSocket ──────────────────────────────────────────────────────
function connect() {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${location.host}/ws`);

    ws.onopen = () => {
        connectionStatus.className = 'connection-status connected';
        statusText.textContent = 'Connected';
        if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    };

    ws.onclose = () => {
        connectionStatus.className = 'connection-status disconnected';
        statusText.textContent = 'Disconnected';
        reconnectTimer = setTimeout(connect, 3000);
    };

    ws.onerror = () => {
        connectionStatus.className = 'connection-status disconnected';
        statusText.textContent = 'Error';
    };

    ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        switch (msg.type) {
            case 'system':
                handleSystem(msg.data);
                break;
            case 'pipeline_event':
                handlePipelineEvent(msg.data);
                break;
            case 'chat_response':
                handleChatResponse(msg.data);
                break;
        }
    };
}

// ── Handlers ───────────────────────────────────────────────────────
function handleSystem(data) {
    // Check MCP status
    fetch('/health')
        .then(r => r.json())
        .then(d => {
            if (d.mcp_server === 'reachable') {
                mcpBadge.className = 'mcp-badge online';
            }
        })
        .catch(() => {});
}

function handlePipelineEvent(data) {
    // Show active agent card
    if (data.agent_type && data.agent_type !== 'orchestrator') {
        showActiveAgent(data);
    }

    // Clear empty state
    const empty = pipelineSteps.querySelector('.pipeline-empty');
    if (empty) empty.remove();

    // Build detail HTML — handle inspect links and cooldown
    let detailHtml = '';
    if (data.inspect_url) {
        const agentIdFromUrl = data.inspect_url.replace('/inspect/', '');
        detailHtml = `
            <div class="step-detail inspect-detail">
                <button class="inspect-link" onclick="openInspectPanel('${agentIdFromUrl}')">
                    🔍 Inspect Live Agent →
                </button>
            </div>`;
        if (data.cooldown_remaining) {
            detailHtml += `
                <div class="step-detail cooldown-timer" data-remaining="${data.cooldown_remaining}">
                    ⏳ Auto-destroy in <span class="countdown">${formatCountdown(data.cooldown_remaining)}</span>
                </div>`;
        }
    } else if (data.detail) {
        detailHtml = `<div class="step-detail">${escapeHtml(data.detail)}</div>`;
    }

    // Add step
    const step = document.createElement('div');
    step.className = 'pipeline-step';
    step.innerHTML = `
        <div class="step-indicator ${data.status}">${getStatusIcon(data.status)}</div>
        <div class="step-body">
            <div class="step-name">${escapeHtml(data.step)}</div>
            ${detailHtml}
            <div class="step-time">${formatTime(data.timestamp)}</div>
        </div>
    `;
    pipelineSteps.appendChild(step);
    pipelineSteps.scrollTop = pipelineSteps.scrollHeight;

    // Start cooldown timer if applicable
    if (data.cooldown_remaining) {
        startCooldownTimer(step.querySelector('.cooldown-timer'));
    }

    // If completed or error for the session, update agent card
    if (data.step.includes('completed') || data.step.includes('Request complete')) {
        finishActiveAgent(data);
    }

    // If agent destroyed after cooldown, update card
    if (data.step.includes('Destroying')) {
        activeAgentStatus.textContent = 'destroyed';
        activeAgentStatus.className = 'agent-card-status destroyed';
    }

    // Persist pipeline to sessionStorage
    saveState();
}

function handleChatResponse(data) {
    // Remove typing indicator
    const typing = chatMessages.querySelector('.typing-message');
    if (typing) typing.remove();

    // Add agent response
    const agentIcon = AGENT_ICONS[data.agent_type] || '🤖';
    addMessage(data.message, 'agent', agentIcon, data.agent_type);
}

// ── Chat Functions ─────────────────────────────────────────────────
function sendMessage() {
    const text = chatInput.value.trim();
    if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;

    // Add user message
    addMessage(text, 'user', '👤');

    // Clear pipeline for new query
    clearPipeline();

    // Start agent timer
    agentStartTime = Date.now();
    startAgentTimer();

    // Show typing indicator
    const typingDiv = document.createElement('div');
    typingDiv.className = 'message agent-message typing-message';
    typingDiv.innerHTML = `
        <div class="message-icon">🤖</div>
        <div class="message-content">
            <div class="typing-indicator">
                <span></span><span></span><span></span>
            </div>
        </div>
    `;
    chatMessages.appendChild(typingDiv);
    chatMessages.scrollTop = chatMessages.scrollHeight;

    // Send over WebSocket
    ws.send(JSON.stringify({ message: text }));

    chatInput.value = '';
    chatInput.style.height = 'auto';
}

function addMessage(content, type, icon, agentType) {
    const div = document.createElement('div');
    div.className = `message ${type}-message`;

    let renderedContent = content;
    if (type === 'agent') {
        // Render markdown for agent responses
        try {
            renderedContent = marked.parse(content);
        } catch (e) {
            renderedContent = `<p>${escapeHtml(content)}</p>`;
        }
    } else {
        renderedContent = `<p>${escapeHtml(content)}</p>`;
    }

    const meta = agentType
        ? `<div class="message-meta"><span class="agent-badge">${AGENT_ICONS[agentType] || '🤖'} ${agentType.replace('_', ' ')}</span></div>`
        : '';

    div.innerHTML = `
        <div class="message-icon">${icon}</div>
        <div class="message-content">
            ${renderedContent}
            ${meta}
        </div>
    `;
    chatMessages.appendChild(div);
    chatMessages.scrollTop = chatMessages.scrollHeight;

    // Add to history if agent response
    if (type === 'agent' && agentType) {
        addHistoryItem(agentType);
    }

    // Persist chat to sessionStorage
    saveState();
}

// ── Pipeline Functions ─────────────────────────────────────────────
function clearPipeline() {
    pipelineSteps.innerHTML = '';
}

function showActiveAgent(data) {
    activeAgentCard.style.display = 'block';
    activeAgentCard.classList.remove('idle');
    activeAgentIcon.textContent = AGENT_ICONS[data.agent_type] || '🤖';
    activeAgentName.textContent = data.agent_type.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
    activeAgentId.textContent = data.agent_id || '';
    activeAgentStatus.textContent = 'active';
    activeAgentStatus.className = 'agent-card-status active';
}

function finishActiveAgent(data) {
    stopAgentTimer();
    activeAgentStatus.textContent = 'done';
    activeAgentStatus.className = 'agent-card-status done';
    activeAgentCard.classList.add('idle');
}

function startAgentTimer() {
    stopAgentTimer();
    agentTimer = setInterval(() => {
        if (agentStartTime) {
            const elapsed = ((Date.now() - agentStartTime) / 1000).toFixed(1);
            activeAgentTimerEl.textContent = `${elapsed}s`;
        }
    }, 100);
}

function stopAgentTimer() {
    if (agentTimer) {
        clearInterval(agentTimer);
        agentTimer = null;
    }
}

function addHistoryItem(agentType) {
    const emptyEl = pipelineHistory.querySelector('.history-empty');
    if (emptyEl) emptyEl.remove();

    const duration = agentStartTime ? ((Date.now() - agentStartTime) / 1000).toFixed(1) : '?';
    const now = new Date();
    const time = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

    const item = document.createElement('div');
    item.className = 'history-item';
    item.innerHTML = `
        <span class="history-icon">${AGENT_ICONS[agentType] || '🤖'}</span>
        <span class="history-text">${agentType.replace(/_/g, ' ')}</span>
        <span class="history-duration">${duration}s</span>
        <span class="history-time">${time}</span>
    `;
    pipelineHistory.insertBefore(item, pipelineHistory.firstChild);

    // Keep max 20 history items
    while (pipelineHistory.children.length > 20) {
        pipelineHistory.removeChild(pipelineHistory.lastChild);
    }
}

// ── Helpers ─────────────────────────────────────────────────────────
function getStatusIcon(status) {
    switch (status) {
        case 'running': return '⏳';
        case 'completed': return '✅';
        case 'error': return '❌';
        default: return '⬜';
    }
}

function formatTime(ts) {
    if (!ts) return '';
    const d = new Date(ts + 'Z');
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function formatCountdown(seconds) {
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${m}:${s.toString().padStart(2, '0')}`;
}

function startCooldownTimer(el) {
    if (!el) return;
    let remaining = parseInt(el.dataset.remaining);
    const countdownSpan = el.querySelector('.countdown');

    const timer = setInterval(() => {
        remaining--;
        if (remaining <= 0) {
            clearInterval(timer);
            countdownSpan.textContent = 'Destroying...';
            el.classList.add('destroying');
        } else {
            countdownSpan.textContent = formatCountdown(remaining);
        }
    }, 1000);
}

// ── Event Listeners ────────────────────────────────────────────────
btnSend.addEventListener('click', sendMessage);

chatInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
});

// Auto-resize textarea
chatInput.addEventListener('input', () => {
    chatInput.style.height = 'auto';
    chatInput.style.height = Math.min(chatInput.scrollHeight, 120) + 'px';
});

btnClear.addEventListener('click', () => {
    chatMessages.innerHTML = '';
    saveState();
});

btnClearPipeline.addEventListener('click', () => {
    clearPipeline();
    pipelineSteps.innerHTML = `
        <div class="pipeline-empty">
            <div class="empty-icon">⚙️</div>
            <p>Pipeline is idle.</p>
            <p class="empty-sub">Send a query and watch agents spin up here.</p>
        </div>
    `;
    activeAgentCard.style.display = 'none';
    saveState();
});

// Hint chips
document.querySelectorAll('.hint-chip').forEach(chip => {
    chip.addEventListener('click', () => {
        chatInput.value = chip.dataset.query;
        chatInput.focus();
    });
});

// ── Inspect Panel ──────────────────────────────────────────────────
let inspectPollTimer = null;

function openInspectPanel(agentId) {
    const overlay = document.getElementById('inspectOverlay');
    overlay.classList.add('open');
    document.getElementById('inspectBody').innerHTML = '<div class="inspect-loading"><div class="inspect-spinner"></div><p>Loading agent data...</p></div>';
    fetchInspectData(agentId);
    inspectPollTimer = setInterval(() => fetchInspectData(agentId), 2000);
}

function closeInspectPanel() {
    document.getElementById('inspectOverlay').classList.remove('open');
    if (inspectPollTimer) { clearInterval(inspectPollTimer); inspectPollTimer = null; }
}

document.getElementById('inspectClose').addEventListener('click', closeInspectPanel);
document.getElementById('inspectOverlay').addEventListener('click', (e) => {
    if (e.target === e.currentTarget) closeInspectPanel();
});

async function fetchInspectData(agentId) {
    try {
        const resp = await fetch(`/api/agents/${agentId}`);
        if (!resp.ok) {
            renderInspectGone(agentId);
            if (inspectPollTimer) { clearInterval(inspectPollTimer); inspectPollTimer = null; }
            return;
        }
        const data = await resp.json();
        renderInspectData(data);
        if (data.status === 'destroyed') {
            if (inspectPollTimer) { clearInterval(inspectPollTimer); inspectPollTimer = null; }
        }
    } catch (err) {
        console.error('Inspect fetch error:', err);
    }
}

function renderInspectGone(agentId) {
    const badge = document.getElementById('inspectBadge');
    badge.textContent = '✕ DESTROYED';
    badge.className = 'inspect-status-badge destroyed';
    document.getElementById('inspectBody').innerHTML = `
        <div class="inspect-gone">
            <div style="font-size:3rem;margin-bottom:1rem;">💀</div>
            <h3>Agent Destroyed</h3>
            <p>Agent <code>${agentId}</code> has been terminated and garbage-collected.</p>
        </div>`;
}

function renderInspectData(d) {
    const badge = document.getElementById('inspectBadge');
    const isAlive = d.status === 'active' || d.status === 'executing';
    const isDead = d.status === 'destroyed';
    badge.textContent = isAlive ? '● ACTIVE' : isDead ? '✕ DESTROYED' : d.status.toUpperCase();
    badge.className = `inspect-status-badge ${isAlive ? 'active' : isDead ? 'destroyed' : 'cooling_down'}`;

    const objHex = d.python_object_id ? '0x' + d.python_object_id.toString(16).toUpperCase() : '—';
    const created = d.created_at ? new Date(d.created_at + 'Z').toLocaleString() : '—';
    const completed = d.completed_at ? new Date(d.completed_at + 'Z').toLocaleString() : 'Still running...';
    const duration = d.duration_seconds ? d.duration_seconds.toFixed(2) + 's' : 'In progress...';
    const threadInfo = d.thread_name ? `${d.thread_name} (${d.thread_id})` : '—';

    let timelineHtml = '';
    if (d.events && d.events.length > 0) {
        timelineHtml = d.events.map(ev => `
            <div class="inspect-event">
                <div class="inspect-event-dot"></div>
                <div class="inspect-event-body">
                    <div class="inspect-event-step">${escapeHtml(ev.step || '')}</div>
                    ${ev.detail ? `<div class="inspect-event-detail">${escapeHtml(ev.detail)}</div>` : ''}
                    <div class="inspect-event-time">${ev.timestamp ? new Date(ev.timestamp + 'Z').toLocaleTimeString() : ''}</div>
                </div>
            </div>`).join('');
    } else {
        timelineHtml = '<p style="color:#5e6484;padding:0.5rem;">No events yet.</p>';
    }

    document.getElementById('inspectBody').innerHTML = `
        <div class="inspect-banner ${isAlive ? 'alive' : 'dead'}">
            <span style="font-size:1.8rem;">${d.agent_icon || '🤖'}</span>
            <div>
                <div class="inspect-banner-title">${escapeHtml(d.description || d.agent_type)}</div>
                <div class="inspect-banner-sub">${isAlive ? '🟢 Agent is ALIVE — Python object in memory' : isDead ? '🔴 Agent DESTROYED — garbage-collected' : '🟡 ' + d.status}</div>
            </div>
        </div>
        <div class="inspect-grid">
            <div class="inspect-card"><div class="inspect-label">Agent ID</div><div class="inspect-value hl">${d.agent_id}</div></div>
            <div class="inspect-card"><div class="inspect-label">Type</div><div class="inspect-value">${d.agent_type}</div></div>
            <div class="inspect-card"><div class="inspect-label">Python Class</div><div class="inspect-value">${d.python_class || '—'}</div></div>
            <div class="inspect-card"><div class="inspect-label">Object ID (Memory)</div><div class="inspect-value hl">${objHex}</div></div>
            <div class="inspect-card"><div class="inspect-label">Process ID</div><div class="inspect-value">${d.process_id || '—'}</div></div>
            <div class="inspect-card"><div class="inspect-label">Thread</div><div class="inspect-value">${threadInfo}</div></div>
            <div class="inspect-card"><div class="inspect-label">Created</div><div class="inspect-value">${created}</div></div>
            <div class="inspect-card"><div class="inspect-label">Completed</div><div class="inspect-value">${completed}</div></div>
            <div class="inspect-card"><div class="inspect-label">Duration</div><div class="inspect-value">${duration}</div></div>
            <div class="inspect-card"><div class="inspect-label">Action</div><div class="inspect-value">${d.action || '—'}</div></div>
            <div class="inspect-card"><div class="inspect-label">Result</div><div class="inspect-value">${d.result_status || 'pending'}</div></div>
            <div class="inspect-card"><div class="inspect-label">Result Size</div><div class="inspect-value">${d.result_size_bytes ? d.result_size_bytes.toLocaleString() + ' bytes' : '—'}</div></div>
        </div>
        <h4 class="inspect-timeline-title">📜 Lifecycle Event Audit Trail</h4>
        <div class="inspect-timeline">${timelineHtml}</div>
        <div class="inspect-refresh-note"><span class="inspect-live-dot"></span> Auto-refreshing every 2s</div>
    `;
}

// ── Session Persistence ────────────────────────────────────────────
function saveState() {
    try {
        // Save chat HTML (skip typing indicators)
        const clone = chatMessages.cloneNode(true);
        const typing = clone.querySelector('.typing-message');
        if (typing) typing.remove();
        sessionStorage.setItem('sre_chat', clone.innerHTML);

        // Save pipeline HTML
        sessionStorage.setItem('sre_pipeline', pipelineSteps.innerHTML);

        // Save history HTML
        sessionStorage.setItem('sre_history', pipelineHistory.innerHTML);

        // Save agent card state
        sessionStorage.setItem('sre_agent_card', JSON.stringify({
            display: activeAgentCard.style.display,
            icon: activeAgentIcon.textContent,
            name: activeAgentName.textContent,
            id: activeAgentId.textContent,
            status: activeAgentStatus.textContent,
            statusClass: activeAgentStatus.className,
            timer: activeAgentTimerEl.textContent,
            idle: activeAgentCard.classList.contains('idle')
        }));
    } catch (e) { /* storage full or unavailable */ }
}

function restoreState() {
    try {
        const chat = sessionStorage.getItem('sre_chat');
        if (chat && chat.trim()) {
            chatMessages.innerHTML = chat;
            chatMessages.scrollTop = chatMessages.scrollHeight;
        }

        const pipeline = sessionStorage.getItem('sre_pipeline');
        if (pipeline && pipeline.trim()) {
            pipelineSteps.innerHTML = pipeline;
            pipelineSteps.scrollTop = pipelineSteps.scrollHeight;
        }

        const history = sessionStorage.getItem('sre_history');
        if (history && history.trim()) {
            pipelineHistory.innerHTML = history;
        }

        const cardJson = sessionStorage.getItem('sre_agent_card');
        if (cardJson) {
            const card = JSON.parse(cardJson);
            if (card.display && card.display !== 'none') {
                activeAgentCard.style.display = card.display;
                activeAgentIcon.textContent = card.icon;
                activeAgentName.textContent = card.name;
                activeAgentId.textContent = card.id;
                activeAgentStatus.textContent = card.status;
                activeAgentStatus.className = card.statusClass;
                activeAgentTimerEl.textContent = card.timer;
                if (card.idle) activeAgentCard.classList.add('idle');
            }
        }
    } catch (e) { /* ignore */ }
}

// ── Initialize ─────────────────────────────────────────────────────
restoreState();
connect();
