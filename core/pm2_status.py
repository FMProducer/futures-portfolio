"""Read the existing PM2 daemon without invoking its auto-starting CLI."""

import json
import os
import shutil
import subprocess
from pathlib import Path


_NODE_RPC = r"""
const path = require('path');
const root = process.argv[1];
const axon = require(require.resolve('pm2-axon', {paths: [root]}));
const rpc = require(require.resolve('pm2-axon-rpc', {paths: [root]}));
const endpoint = require(path.join(root, 'paths.js'))().DAEMON_RPC_PORT;
const sock = axon.socket('req');
const client = new rpc.Client(sock);
const fail = err => { console.error(err.code || err.message); process.exit(1); };
setTimeout(() => fail(new Error('PM2 status timed out')), 4000);
sock.on('error', fail);
sock.on('socket error', fail);
sock.once('connect', () => client.call('getMonitorData', {}, (err, list) => {
  if (err) return fail(err);
  if (!Array.isArray(list)) return fail(new Error('Invalid PM2 status'));
  const safe = list.map(p => {
    const e = p.pm2_env || {};
    return {name: p.name, pid: p.pid, pm_id: p.pm_id, monit: p.monit,
      pm2_env: {status: e.status, args: e.args, restart_time: e.restart_time,
        pm_uptime: e.pm_uptime, pm_cwd: e.pm_cwd, pm_exec_path: e.pm_exec_path,
        exec_interpreter: e.exec_interpreter}};
  });
  process.stdout.write(JSON.stringify(safe), () => process.exit(0));
}));
sock.connect(endpoint);
"""


def read_pm2_status():
    node, pm2 = shutil.which('node'), shutil.which('pm2')
    if not pm2 and os.name == 'nt':
        candidate = Path(os.environ.get('APPDATA', '')) / 'npm' / 'pm2.cmd'
        if candidate.is_file():
            pm2 = str(candidate)
    if not node or not pm2:
        raise FileNotFoundError('Node or PM2 installation is unavailable')
    executable = Path(pm2).resolve()
    root = (executable.parent / 'node_modules' / 'pm2'
            if executable.suffix.lower() in ('.cmd', '.ps1', '.exe')
            else executable.parent.parent)
    result = subprocess.run(
        [node, '-e', _NODE_RPC, str(root)], capture_output=True,
        encoding='utf-8', timeout=6,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    if result.returncode:
        raise RuntimeError(f'PM2 status unavailable: {result.stderr.strip()[:200]}')
    data = json.loads(result.stdout)
    if not isinstance(data, list) or any(
            not isinstance(p, dict) or not isinstance(p.get('name'), str)
            or not isinstance(p.get('pm2_env'), dict) for p in data):
        raise ValueError('Invalid PM2 process list')
    return data
