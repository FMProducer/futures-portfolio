import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core import pm2_status


class PM2StatusTests(unittest.TestCase):
    def test_permission_failure_never_calls_pm2_cli(self):
        result = SimpleNamespace(returncode=1, stderr='EPERM', stdout='')
        with patch.object(pm2_status.shutil, 'which', side_effect=['node', 'pm2.cmd']), \
                patch.object(pm2_status.subprocess, 'run', return_value=result) as run:
            with self.assertRaisesRegex(RuntimeError, 'EPERM'):
                pm2_status.read_pm2_status()
        command = run.call_args.args[0]
        self.assertEqual(command[:2], ['node', '-e'])
        self.assertNotIn('shell', run.call_args.kwargs)
        self.assertNotIn('jlist', command)

    def test_success_and_invalid_responses(self):
        for payload, valid in [('[]', True), ('{}', False), ('[{}]', False), ('broken', False)]:
            with self.subTest(payload=payload), \
                    patch.object(pm2_status.shutil, 'which', side_effect=['node', 'pm2.cmd']), \
                    patch.object(pm2_status.subprocess, 'run', return_value=SimpleNamespace(
                        returncode=0, stdout=payload)):
                if valid:
                    self.assertEqual(pm2_status.read_pm2_status(), [])
                else:
                    with self.assertRaises(ValueError):
                        pm2_status.read_pm2_status()

    def test_guard_blocks_unknown_and_live_status(self):
        spec = importlib.util.spec_from_file_location('status_guard', Path(__file__).with_name('conftest.py'))
        guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard)
        config = SimpleNamespace(getoption=lambda _: False)
        cases = [RuntimeError('EPERM'), FileNotFoundError('missing PM2'),
                 subprocess.TimeoutExpired('node', 6),
                 [{'name': 'paper-test', 'pm2_env': {'status': 'online'}}]]
        for case in cases:
            with self.subTest(case=case), patch.object(guard, 'read_pm2_status') as read:
                if isinstance(case, Exception):
                    read.side_effect = case
                else:
                    read.return_value = case
                with self.assertRaises(guard.pytest.exit.Exception):
                    guard.pytest_configure(config)
        with patch.object(guard, 'read_pm2_status', return_value=[]):
            guard.pytest_configure(config)

    def test_node_rpc_success_and_permission_error_cannot_spawn_children(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is not installed')
        for mode in ['denied', 'online']:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / 'paths.js').write_text("module.exports=()=>({DAEMON_RPC_PORT:'" + mode + "'});")
                modules = root / 'node_modules'
                (modules / 'pm2-axon').mkdir(parents=True)
                (modules / 'pm2-axon-rpc').mkdir()
                (modules / 'pm2-axon/index.js').write_text("""
const cp=require('child_process');
for(const key of ['spawn','fork','exec','execFile']) cp[key]=()=>{throw Error('SPAWN FORBIDDEN')};
exports.socket=()=>{const s=new(require('events').EventEmitter)();
s.connect=endpoint=>process.nextTick(()=>endpoint==='denied'
 ?s.emit('socket error',Object.assign(Error('denied'),{code:'EPERM'})):s.emit('connect'));
return s;};
""")
                (modules / 'pm2-axon-rpc/index.js').write_text("""
exports.Client=class {call(method,args,cb){
if(method!=='getMonitorData')throw Error('MUTATION FORBIDDEN');
cb(null,[{name:'paper-test',pid:123,pm2_env:{status:'online',env:{credential:'MUST_NOT_LEAK'}}}]);
}};
""")
                result = subprocess.run([node, '-e', pm2_status._NODE_RPC, str(root)],
                                        capture_output=True, text=True, timeout=6,
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                if mode == 'denied':
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(result.stderr.strip(), 'EPERM')
                    self.assertEqual(result.stdout, '')
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(json.loads(result.stdout)[0]['name'], 'paper-test')
                    self.assertNotIn('MUST_NOT_LEAK', result.stdout)


if __name__ == '__main__':
    unittest.main()
