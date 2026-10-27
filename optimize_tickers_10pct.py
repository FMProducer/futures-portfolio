"""Independent 400-trial searches around a frozen current configuration."""
import argparse
import copy
import importlib.util
import json
import math
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TEMPLATE = ROOT / 'reports/optuna_selected_local400_20261027'


def prepare(run):
    from optimization.optimize_universal import get_path, set_path, load_optimizer_settings
    source = json.loads((ROOT / 'config.json').read_text(encoding='utf-8-sig'))
    tickers = list(dict.fromkeys(s.strip() for s in (ROOT / 'tickers.txt').read_text(encoding='utf-8-sig').splitlines() if s.strip()))
    if not tickers or not all(re.fullmatch(r'[A-Z0-9]+USDT', s) for s in tickers):
        raise ValueError('tickers.txt must contain one USDT symbol per line')
    base = copy.deepcopy(source)
    settings = json.loads((TEMPLATE / 'refined_optimizer_config.json').read_text())
    centers = {}
    for name, spec in settings['parameters'].items():
        path = 'portfolios.0.targets.BASE_LONG.leverage' if name == 'leverage' else name
        value = get_path(source, path)
        if spec['type'] == 'categorical':
            spec.update(enabled=False, value=False)
            set_path(base, path, False)
            continue
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError(f'Invalid numeric source value: {name}')
        low, high = value * .9, value * 1.1
        if spec['type'] == 'int':
            low, high = math.ceil(low), math.floor(high)
            if low > high:
                raise ValueError(f'No integer within +/-10% for {name}')
            set_path(base, path, int(value))
        if name == 'leverage' and not 3 <= low <= high <= 7:
            raise ValueError('The full leverage corridor must remain inside the approved 3..7 range')
        spec.update(low=low, high=high, value=int(value) if spec['type'] == 'int' else value)
        spec['enabled'] = spec['enabled'] and low < high
        spec.pop('step', None)
        centers[name] = {'source_value': value, 'low': low, 'high': high, 'enabled': spec['enabled']}
        if spec['type'] == 'int':
            spec['step'] = 1
    base['funding_model'] = 'legacy_constant'
    run.mkdir(parents=True, exist_ok=False)
    for filename, obj in [('source_config.json', source), ('base_config.json', base),
                          ('refined_optimizer_config.json', settings), ('range_centers.json', centers)]:
        (run / filename).write_text(json.dumps(obj, indent=2), encoding='utf-8')
    (run / 'tickers.txt').write_text('\n'.join(tickers) + '\n', encoding='utf-8')
    load_optimizer_settings(base, run / 'refined_optimizer_config.json')
    runner = (TEMPLATE / 'run_local_only.py').read_text()
    runner = runner.replace("'FUSE=false. Shared leverage=6: it is the only integer within +/-10% of 6.',",
                            "'FUSE=false. Shared integer leverage follows range_centers.json.',")
    (run / 'run_local_only.py').write_text(runner, encoding='utf-8')
    print(f'Prepared {len(tickers)} tickers; 400 trials each. Results: {run}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-only', action='store_true', help='Validate and snapshot inputs without downloading or optimizing')
    parser.add_argument('--resume', type=Path, help='Resume an existing output directory with its frozen inputs')
    args = parser.parse_args()
    run = args.resume.resolve() if args.resume else ROOT / 'reports' / ('ticker_local400_10pct_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    if args.resume:
        if not run.is_relative_to(ROOT / 'reports') or not (run / 'run_local_only.py').is_file():
            raise ValueError('Resume requires an output directory created under project reports')
    else:
        prepare(run)
    if args.prepare_only:
        return
    manifest = run / 'data_manifest.json'
    if not manifest.exists() or json.loads(manifest.read_text())['status'] != 'completed':
        spec = importlib.util.spec_from_file_location('history_download', TEMPLATE / 'prepare_data.py')
        downloader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(downloader)
        downloader.RUN, downloader.DATA = run, run / 'data'
        if args.resume and manifest.exists():
            previous = json.loads(manifest.read_text())
            end = datetime.fromisoformat(previous['end_exclusive_utc'].replace('Z', '+00:00'))
        else:
            server = downloader.fetch('https://fapi.binance.com/fapi/v1/time')
            end = datetime.fromtimestamp(server['serverTime'] / 1000, timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        start = end - timedelta(days=2)
        downloader.START, downloader.END = int(start.timestamp()*1000), int(end.timestamp()*1000)
        downloader.main()
        downloaded = json.loads(manifest.read_text())
        downloaded.update(start_utc=start.strftime('%Y-%m-%dT00:00:00Z'),
                          end_exclusive_utc=end.strftime('%Y-%m-%dT00:00:00Z'))
        manifest.write_text(json.dumps(downloaded, indent=2), encoding='utf-8')
    subprocess.run([sys.executable, '-u', str(run / 'run_local_only.py')], cwd=ROOT, check=True)
    subprocess.run([sys.executable, '-u', str(ROOT / 'export_local_optuna_graphs.py'), '--run-dir', str(run)], cwd=ROOT, check=True)


if __name__ == '__main__':
    main()
