const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

async function check(file, loader, data, updates) {
    let calls = 0;
    let release;
    const nodes = new Map();
    const events = {};
    const document = {
        hidden: false,
        addEventListener: (name, fn) => { events[name] = fn; },
        getElementById: id => {
            if (!nodes.has(id)) nodes.set(id, {textContent: '', addEventListener() {}});
            return nodes.get(id);
        }
    };
    const context = vm.createContext({
        document, console: {error() {}}, AbortSignal,
        MODE: 'paper', TICKER: 'TESTUSDT', REFRESH_SEC: 10,
        fetch: async () => {
            calls++;
            await new Promise(resolve => { release = resolve; });
            return {ok: true, json: async () => data};
        }
    });
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../dashboard/static', file), 'utf8'), context);
    for (const name of updates) context[name] = () => {};
    const first = context[loader]();
    await context[loader]();
    assert.equal(calls, 1, `${file}: requests must not overlap`);
    release();
    await first;
    document.hidden = true;
    await context[loader]();
    assert.equal(calls, 1, `${file}: hidden tabs must not poll`);
    document.hidden = false;
    const next = context[loader]();
    assert.equal(calls, 2, `${file}: polling must resume`);
    release();
    await next;
    if (file === 'ticker.js') {
        assert.equal(nodes.get('log-content').textContent, 'one\ntwo');
        assert.ok(!fs.readFileSync(path.join(__dirname, '../dashboard/static', file), 'utf8').includes('/api/logs/'));
    }
    console.log(`PASS ${file}: no overlap, hidden pause, resume`);
}

(async () => {
    await check('app.js', 'loadOverview', {}, [
        'updateSummary', 'updatePaperHistory', 'updateSwarmCurve',
        'updateAlerts', 'updateBotsTable', 'updatePm2Table']);
    await check('ticker.js', 'loadTickerDetails', {log_tail: ['one', 'two']}, ['updateStats', 'updatePositions']);
    await check('logs.js', 'loadLogs', {lines: ['one'], total: 1}, []);
})().catch(error => { console.error(error); process.exitCode = 1; });
