"""Export saved individual Optuna studies without modifying their SQLite databases."""
import argparse
import html
import json
import sqlite3
import tempfile
from pathlib import Path


def export(run):
    import optuna
    from optuna import visualization as vis
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    output = run / 'optuna_visuals'
    output.mkdir(exist_ok=True)
    databases = sorted((run / 'per_ticker').glob('*.db'))
    if not databases:
        raise ValueError(f'No per-ticker databases in {run}')
    rows, errors = [], []
    for database in databases:
        folder = output / database.stem
        folder.mkdir(exist_ok=True)
        links = []
        with tempfile.TemporaryDirectory(prefix='optuna_plot_') as tmp:
            snapshot = Path(tmp) / 'study.db'
            with sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True) as source:
                with sqlite3.connect(snapshot) as destination:
                    source.backup(destination)
            storage = optuna.storages.RDBStorage('sqlite:///' + snapshot.as_posix())
            try:
                studies = optuna.get_all_study_summaries(storage=storage)
                if len(studies) != 1:
                    raise ValueError(f'Expected one study: {database}')
                study = optuna.load_study(study_name=studies[0].study_name, storage=storage)
                complete = study.get_trials(states=(optuna.trial.TrialState.COMPLETE,))
                if not complete:
                    errors.append(f'{database.stem}: no feasible completed trials')
                else:
                    for index, label in enumerate(('net_pct', 'max_dd_pct')):
                        target = lambda trial, i=index: trial.values[i]
                        plots = {
                            f'history_{label}': lambda: vis.plot_optimization_history(study, target=target, target_name=label),
                            f'importance_{label}': lambda: vis.plot_param_importances(study, target=target, target_name=label),
                            f'parallel_{label}': lambda: vis.plot_parallel_coordinate(study, target=target, target_name=label),
                            f'contour_{label}': lambda: vis.plot_contour(study, target=target, target_name=label),
                        }
                        for name, plot in plots.items():
                            try:
                                plot().write_html(folder / f'{name}.html', include_plotlyjs='directory')
                                links.append(name)
                            except Exception as exc:
                                errors.append(f'{database.stem}/{name}: {exc}')
                    try:
                        figure = vis.plot_pareto_front(study, target_names=['Net (%)', 'MaxDD (%)'])
                        best = max(complete, key=lambda t: (t.values[0], -t.values[1]))
                        figure.add_scatter(x=[best.values[0]], y=[best.values[1]], mode='markers',
                                           marker={'size': 15, 'symbol': 'star', 'color': 'red'}, name=f'Recommended trial {best.number}')
                        figure.write_html(folder / 'pareto.html', include_plotlyjs='directory')
                        links.append('pareto')
                    except Exception as exc:
                        errors.append(f'{database.stem}/pareto: {exc}')
                assert len(study.trials) == studies[0].n_trials
            finally:
                storage.remove_session()
                storage.engine.dispose()
        rows.append('<li>' + html.escape(database.stem) + ': ' + ' | '.join(
            f'<a href="{database.stem}/{name}.html">{name}</a>' for name in links) + '</li>')
        print(f'{len(rows)}/{len(databases)} {database.stem}: {len(links)} charts', flush=True)
    (output / 'errors.json').write_text(json.dumps(errors, indent=2), encoding='utf-8')
    (output / 'index.html').write_text('<!doctype html><meta charset="utf-8"><title>Individual Optuna studies</title>'
        '<h1>Individual Optuna studies</h1><p>Completed feasible trials only. Importance is association, not causality.</p><ul>'
        + ''.join(rows) + '</ul><h2>Export notes</h2><pre>' + html.escape('\n'.join(errors)) + '</pre>', encoding='utf-8')
    print(f'Index: {output / "index.html"}; export notes: {len(errors)}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    export(parser.parse_args().run_dir.resolve())
