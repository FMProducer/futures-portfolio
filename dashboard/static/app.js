/**
 * Prosperous Bot Dashboard — Main JS
 * Dashboard page logic: auto-refresh, PM2 control, alerts
 */

// ─── State ────────────────────────────────────────────────────

let refreshTimer = null;
let paperHistory = null;
let historySort = {key: 'ticker', direction: 1};

// ─── Init ─────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    loadOverview();
    startAutoRefresh();
});

function startAutoRefresh() {
    if (refreshTimer) clearInterval(refreshTimer);
    refreshTimer = setInterval(loadOverview, REFRESH_SEC * 1000);
}

// ─── API calls ────────────────────────────────────────────────

async function apiGet(url) {
    try {
        const resp = await fetch(url);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        return await resp.json();
    } catch (e) {
        console.error('API error:', e);
        return null;
    }
}

async function apiPost(url, data) {
    try {
        const resp = await fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRF-Token': CSRF_TOKEN,
            },
            body: JSON.stringify(data),
        });
        return await resp.json();
    } catch (e) {
        console.error('API error:', e);
        return { error: e.message };
    }
}

// ─── Load overview ────────────────────────────────────────────

async function loadOverview() {
    const data = await apiGet('/api/overview');
    if (!data) return;

    updateSummary(data.summary, data.supervisor);
    updatePaperHistory(data.paper_history);
    updateSwarmCurve(data.paper_curve);
    updateAlerts(data.alerts);
    updateBotsTable('real-bots-table', data.real_bots);
    updateBotsTable('paper-bots-table', data.paper_bots);
    updatePm2Table(data.pm2_processes);
}

function updateSwarmCurve(data) {
    const status = document.getElementById('swarm-curve-status');
    const svg = document.getElementById('swarm-curve');
    svg.replaceChildren();
    if (!data || data.error || !data.points.length) {
        status.textContent = 'График недоступен: ' + (data?.error || 'нет наблюдений');
        return;
    }
    const points = data.points;
    const valid = points.filter(p => p[1] != null);
    if (!valid.length) { status.textContent = 'Нет корректных значений'; return; }
    const values = valid.map(p => p[1]);
    const lo = Math.min(0, ...values), hi = Math.max(1, ...values);
    const start = points[0][0], end = points[points.length - 1][0];
    const x = t => 85 + (t - start) / Math.max(1, end - start) * 890;
    const y = v => 245 - (v - lo) / Math.max(1, hi - lo) * 220;
    const add = (tag, attrs, text) => {
        const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
        for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
        if (text != null) el.textContent = text;
        svg.append(el); return el;
    };
    for (const value of [lo, (lo + hi) / 2, hi]) {
        add('line', {x1:85, x2:975, y1:y(value), y2:y(value), stroke:'#667085', opacity:0.3});
        add('text', {x:0, y:y(value)+5, fill:'currentColor', 'font-size':14}, formatUsd(value));
    }
    let path = '', connected = false;
    for (const [ts, net, dd, gap] of points) {
        if (net == null) { connected = false; continue; }
        path += `${connected && !gap ? 'L' : 'M'}${x(ts).toFixed(2)},${y(net).toFixed(2)} `;
        connected = true;
    }
    add('path', {d:path, fill:'none', stroke:'#38bdf8', 'stroke-width':2});
    for (const marker of data.markers || []) {
        if (marker.ts < start || marker.ts > end) continue;
        const line = add('line', {x1:x(marker.ts), x2:x(marker.ts), y1:25, y2:245, stroke:'#fbbf24', 'stroke-dasharray':'5 5'});
        const title = document.createElementNS('http://www.w3.org/2000/svg', 'title');
        title.textContent = marker.label; line.append(title);
    }
    for (const [ts, anchor] of [[start, 'start'], [end, 'end']]) {
        add('text', {x:x(ts), y:280, fill:'currentColor', 'text-anchor':anchor, 'font-size':13}, new Date(ts*1000).toLocaleString('ru-RU'));
    }
    const last = valid[valid.length-1];
    status.textContent = `Последняя оценка: ${formatUsd(last[1])}. Наблюдаемая максимальная просадка: ${formatUsd(data.max_drawdown_usdt)}. Точек: ${points.length}. Обновлено: ${new Date(last[0]*1000).toLocaleString('ru-RU')}.`;
    svg.onpointermove = event => {
        const box = svg.getBoundingClientRect();
        const ts = start + (((event.clientX-box.left)/box.width*1000-85)/890)*(end-start);
        const nearest = valid.reduce((a,b) => Math.abs(a[0]-ts)<Math.abs(b[0]-ts) ? a : b);
        svg.setAttribute('aria-label', `${new Date(nearest[0]*1000).toLocaleString('ru-RU')}: ${formatUsd(nearest[1])}, просадка ${formatUsd(nearest[2])}`);
        let title = svg.querySelector('title');
        if (!title) title = add('title', {});
        title.textContent = svg.getAttribute('aria-label');
    };
}

function updatePaperHistory(history) {
    if (!history) return;
    paperHistory = history;
    const money = value => value == null ? 'неизвестно' : formatUsd(value);
    document.getElementById('paper-history-summary').textContent =
        `На ${new Date(history.as_of * 1000).toLocaleString('ru-RU')}. Сессий: ${history.session_count}. Накопленная оценка: ${money(history.reported_cumulative_net)}. ` +
        `Закрытые по отчётам: ${money(history.reported_closed_net)}; открытые: ${money(history.open_net)}; ` +
        `прерванные: ${money(history.interrupted_net)}. Проверенный Net: не установлен. ` +
        `Всего стартовых аллокаций: ${money(history.capital_allocated)} (не доход и не общий кошелёк). ` +
        `Известные комиссии: ${money(history.fees_recorded)} — уже учтены в результате, повторно не вычитаются.`;
    document.getElementById('paper-history-warning').textContent = history.warning +
        (history.errors.length ? ` Ошибок чтения/покрытия: ${history.errors.length}.` : '');
    const body = document.getElementById('paper-history-rows');
    body.replaceChildren();
    const status = {open: 'Открыта', closed_reported: 'Закрыта по отчёту', interrupted: 'Прервана / нет закрытия'};
    for (const row of [...history.sessions].sort(compareHistoryRows)) {
        const tr = document.createElement('tr');
        if (row.ticker === 'TUSDT') tr.style.fontWeight = 'bold';
        const values = [row.ticker, new Date(row.started_at * 1000).toLocaleString('ru-RU'), status[row.status],
            money(row.initial), money(row.endpoint), money(row.reported_net), row.cycles,
            `${row.observed_maxdd_pct.toFixed(2)}%`, money(row.fees_recorded), money(row.wallet_receipt_residual)];
        for (const value of values) { const td = document.createElement('td'); td.textContent = value; tr.append(td); }
        body.append(tr);
    }
}

function compareHistoryRows(a, b) {
    const x = a[historySort.key], y = b[historySort.key];
    if (x == null || y == null) return x == null ? (y == null ? 0 : 1) : -1;
    const order = typeof x === 'number' ? x - y : String(x).localeCompare(String(y), 'ru');
    return order * historySort.direction || a.ticker.localeCompare(b.ticker) || a.started_at - b.started_at;
}

function sortPaperHistory(key) {
    historySort = {key, direction: historySort.key === key ? -historySort.direction : 1};
    document.querySelectorAll('[data-history-sort]').forEach(button => {
        const active = button.dataset.historySort === key;
        button.parentElement.setAttribute('aria-sort', active ? (historySort.direction === 1 ? 'ascending' : 'descending') : 'none');
    });
    if (paperHistory) updatePaperHistory(paperHistory);
}

// ─── Update summary ───────────────────────────────────────────

function updateSummary(summary, supervisor) {
    // Supervisor
    const supEl = document.getElementById('supervisor-status');
    if (supEl) {
        supEl.textContent = supervisor;
        supEl.className = 'card-value ' + (supervisor === 'online' ? 'pnl-positive' : 'pnl-negative');
    }

    // Total TPV
    const tpvEl = document.getElementById('total-tpv');
    if (tpvEl) tpvEl.textContent = formatUsd(summary.total_tpv);

    // Total PnL
    const pnlEl = document.getElementById('total-pnl');
    if (pnlEl) {
        pnlEl.textContent = `${formatUsd(summary.total_pnl)} (${summary.total_pnl_pct > 0 ? '+' : ''}${summary.total_pnl_pct}%)`;
        pnlEl.className = 'card-value ' + (summary.total_pnl >= 0 ? 'pnl-positive' : 'pnl-negative');
    }

    // Bots count
    const botsEl = document.getElementById('bots-count');
    if (botsEl) botsEl.textContent = `${summary.real_count}R / ${summary.paper_count}P`;
}

// ─── Update alerts ────────────────────────────────────────────

function updateAlerts(alerts) {
    const container = document.getElementById('alerts-container');
    if (!container) return;

    if (!alerts || alerts.length === 0) {
        container.innerHTML = '';
        return;
    }

    container.innerHTML = alerts.map(a => `
        <div class="alert alert-${a.level === 'critical' ? 'danger' : 'warning'}">
            ${a.level === 'critical' ? '🚨' : '⚠️'} ${escapeHtml(a.message)}
        </div>
    `).join('');
}

// ─── Update bots table ────────────────────────────────────────

function updateBotsTable(tableId, bots) {
    const tbody = document.querySelector(`#${tableId} tbody`);
    if (!tbody) return;

    if (!bots || bots.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--text-muted)">Нет данных</td></tr>';
        return;
    }

    tbody.innerHTML = bots.map(bot => {
        const statusClass = bot.status === 'ok' ? 'badge-ok' :
                           bot.status === 'stale' ? 'badge-stale' :
                           bot.status === 'pnl_guard' ? 'badge-pnl-guard' : 'badge-offline';
        const pnlClass = bot.pnl >= 0 ? 'pnl-positive' : 'pnl-negative';
        const hbText = bot.heartbeat_ok ? '✅' : `⚠️ ${bot.heartbeat_age_sec || '?'}s`;

        return `
            <tr>
                <td><strong><a href="/ticker/${bot.mode}/${bot.ticker}" style="color:var(--text-primary);text-decoration:none">${bot.ticker}</a></strong></td>
                <td>${formatUsd(bot.tpv)}</td>
                <td class="${pnlClass}">${formatUsd(bot.pnl)} (${bot.pnl_pct > 0 ? '+' : ''}${bot.pnl_pct}%)</td>
                <td>${bot.cycles || 0}</td>
                <td>${hbText}</td>
                <td><span class="badge ${statusClass}">${bot.status}</span></td>
                <td>
                    <a href="/ticker/${bot.mode}/${bot.ticker}" class="btn btn-sm btn-outline">📊</a>
                    <a href="/logs/${bot.mode}/${bot.ticker}" class="btn btn-sm btn-outline">📜</a>
                </td>
            </tr>
        `;
    }).join('');
}

// ─── Update PM2 table ─────────────────────────────────────────

function updatePm2Table(processes) {
    const tbody = document.querySelector('#pm2-table tbody');
    if (!tbody) return;

    if (!processes || processes.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text-muted)">PM2 не отвечает</td></tr>';
        return;
    }

    tbody.innerHTML = processes.map(p => {
        const statusClass = p.status === 'online' ? 'badge-ok' : 'badge-offline';
        const memMb = p.memory ? (p.memory / 1024 / 1024).toFixed(1) : '—';

        return `
            <tr>
                <td>${escapeHtml(p.name)}</td>
                <td><span class="badge ${statusClass}">${p.status}</span></td>
                <td>${p.restarts || 0}</td>
                <td>${p.cpu || 0}%</td>
                <td>${memMb} MB</td>
            </tr>
        `;
    }).join('');
}

// ─── PM2 Control ──────────────────────────────────────────────

function pm2Action(action, target) {
    const actionNames = {
        pm2_start: 'Запустить',
        pm2_stop: 'Остановить',
        pm2_restart: 'Перезапустить',
        pm2_delete: 'Удалить',
    };

    showConfirm(
        `${actionNames[action] || action}: ${target}`,
        `Вы уверены? Это действие ${action === 'pm2_stop' || action === 'pm2_delete' ? 'остановит' : 'перезапустит'} процесс.`,
        async () => {
            const result = await apiPost('/api/control/pm2', { action, target });
            if (result.success) {
                setTimeout(loadOverview, 2000);
            } else {
                alert('Ошибка: ' + (result.error || result.stderr || 'Unknown'));
            }
        }
    );
}

// ─── Emergency Stop ───────────────────────────────────────────

function emergencyStop(ticker, mode) {
    showConfirm(
        `🚨 ЭКСТРЕННАЯ ОСТАНОВКА: ${mode} ${ticker}`,
        `Бот будет остановлен. PM2 процесс удалён. Позиции на бирже НЕ закрываются автоматически — убедитесь что они закрыты вручную или через Binance.`,
        async () => {
            const result = await apiPost('/api/control/emergency-stop', { ticker, mode });
            if (result.success) {
                setTimeout(loadOverview, 2000);
            } else {
                alert('Ошибка: ' + (result.error || 'Unknown'));
            }
        }
    );
}

// ─── Modal ────────────────────────────────────────────────────

let confirmCallback = null;

function showConfirm(title, message, callback) {
    document.getElementById('confirm-title').textContent = title;
    document.getElementById('confirm-message').textContent = message;
    confirmCallback = callback;
    document.getElementById('confirm-modal').classList.add('show');
}

function closeModal() {
    document.getElementById('confirm-modal').classList.remove('show');
    confirmCallback = null;
}

document.getElementById('confirm-btn')?.addEventListener('click', () => {
    if (confirmCallback) confirmCallback();
    closeModal();
});

// ─── Nav toggle (mobile) ──────────────────────────────────────

function toggleNav() {
    document.querySelector('.nav-links').classList.toggle('show');
}

// ─── Helpers ──────────────────────────────────────────────────

function formatUsd(value) {
    if (value === null || value === undefined) return '—';
    return '$' + Number(value).toLocaleString('en-US', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    });
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
