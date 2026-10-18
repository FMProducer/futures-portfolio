"""Offline append-only research attempt inventory, not an admission authority."""
import argparse
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3


def encode(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def artifacts(paths):
    result={}
    for role,path in paths.items():
        if not isinstance(role,str) or not role: raise ValueError('missing artifact role')
        path=Path(path).resolve()
        data=path.read_bytes()
        result[role]=dict(path=str(path),sha256=hashlib.sha256(data).hexdigest(),bytes=len(data))
    return result


class Registry:
    def __init__(self,path):
        self.path=Path(path).resolve()
        if self.path.exists():
            with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True)) as db:
                table=db.execute("SELECT 1 FROM sqlite_master WHERE name='research_registry'").fetchone()
                if not table or db.execute('SELECT version FROM research_registry').fetchall()!=[('v1',)]:
                    raise ValueError('foreign database; use a dedicated research registry')
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                db.execute('CREATE TABLE IF NOT EXISTS research_registry(version TEXT PRIMARY KEY)')
                db.execute("INSERT OR IGNORE INTO research_registry VALUES('v1')")
                db.execute('CREATE TABLE IF NOT EXISTS trials(id TEXT PRIMARY KEY,snapshot TEXT NOT NULL,started_utc TEXT NOT NULL)')
                db.execute('CREATE TABLE IF NOT EXISTS outcomes(trial_id TEXT PRIMARY KEY REFERENCES trials(id),body TEXT NOT NULL,finished_utc TEXT NOT NULL)')
                for table in ('research_registry','trials','outcomes'):
                    for operation in ('UPDATE','DELETE'):
                        db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'immutable research record'); END")
                db.commit()
            except BaseException:
                db.rollback()
                raise

    @contextmanager
    def connection(self):
        with closing(sqlite3.connect(self.path,timeout=15,isolation_level=None)) as db:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('PRAGMA foreign_keys=ON')
            yield db

    def start(self,identity,files,metadata,*,mode):
        if not isinstance(identity,str) or not identity or mode not in ('PROSPECTIVE','RETROSPECTIVE'):
            raise ValueError('invalid trial identity or recording mode')
        if not {'code','config','data'}<=set(files):
            raise ValueError('code/config/data artifacts required')
        request=encode(dict(mode=mode,artifacts=artifacts(files),metadata=metadata))
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                old=db.execute('SELECT snapshot FROM trials WHERE id=?',(identity,)).fetchone()
                if old:
                    if old[0]!=request: raise ValueError('conflicting trial registration')
                else:
                    db.execute('INSERT INTO trials VALUES(?,?,?)',(identity,request,datetime.now(timezone.utc).isoformat()))
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def finish(self,identity,status,*,reason=None,evidence=None):
        if status not in ('COMPLETED','FAILED','ABORTED'):
            raise ValueError('invalid trial outcome; numerical PASS is not a registry status')
        if status!='COMPLETED' and not reason: raise ValueError('failed/aborted outcome requires reason')
        if status=='COMPLETED' and not evidence: raise ValueError('completed outcome requires evidence')
        request=encode(dict(status=status,reason=reason,evidence=artifacts(evidence or {})))
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                trial=db.execute('SELECT snapshot FROM trials WHERE id=?',(identity,)).fetchone()
                if not trial: raise ValueError('unknown trial')
                old=db.execute('SELECT body FROM outcomes WHERE trial_id=?',(identity,)).fetchone()
                if old:
                    if old[0]!=request: raise ValueError('conflicting trial outcome')
                else:
                    if status=='COMPLETED':
                        expected=json.loads(trial[0])['artifacts']
                        if artifacts({k:v['path'] for k,v in expected.items()})!=expected:
                            raise ValueError('registered artifacts changed; record failure instead')
                    db.execute('INSERT INTO outcomes VALUES(?,?,?)',(identity,request,datetime.now(timezone.utc).isoformat()))
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def report(self):
        with self.connection() as db:
            db.execute('BEGIN')
            rows=db.execute('SELECT t.id,t.snapshot,t.started_utc,o.body,o.finished_utc FROM trials t LEFT JOIN outcomes o ON o.trial_id=t.id ORDER BY t.started_utc,t.id').fetchall()
            db.commit()
        trials=[]
        counts={key:0 for key in ('INCOMPLETE','COMPLETED','FAILED','ABORTED')}
        reuse={}
        for identity,snapshot,started,outcome,finished in rows:
            snapshot=json.loads(snapshot)
            body=json.loads(outcome) if outcome else dict(status='INCOMPLETE',reason='no outcome recorded')
            counts[body['status']]+=1
            digest=snapshot['artifacts']['data']['sha256']
            reuse[digest]=reuse.get(digest,0)+1
            trials.append(dict(id=identity,snapshot=snapshot,started_utc=started,outcome=body,finished_utc=finished))
        return dict(trials=trials,total=len(trials),counts=counts,data_hash_occurrences=reuse,
            oos_status='NOT_ESTABLISHED',admission_ready=False,trading_authorized=False,
            limitations=['records only explicitly registered attempts; not proof all experiments were registered',
                         'PROSPECTIVE is a declaration, not proof code ran from a frozen snapshot',
                         'hash equality cannot detect a temporary overlay reverted before finish',
                         'file identity is not semantic time-window overlap or OOS independence',
                         'local SQLite immutability is not independent tamper evidence'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,required=True)
    sub=parser.add_subparsers(dest='action',required=True)
    start=sub.add_parser('start')
    start.add_argument('--id',required=True)
    start.add_argument('--manifest',type=Path,required=True,help='JSON: files, metadata, mode')
    finish=sub.add_parser('finish')
    finish.add_argument('--id',required=True)
    finish.add_argument('--outcome',type=Path,required=True,help='JSON: status, reason, evidence')
    report=sub.add_parser('report')
    report.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    registry=Registry(args.database)
    if args.action=='start':
        value=json.loads(args.manifest.read_text(encoding='utf-8-sig'))
        registry.start(args.id,value['files'],value['metadata'],mode=value['mode'])
    elif args.action=='finish':
        value=json.loads(args.outcome.read_text(encoding='utf-8-sig'))
        registry.finish(args.id,value['status'],reason=value.get('reason'),evidence=value.get('evidence'))
    else:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        with args.output.open('x',encoding='utf-8') as stream: json.dump(registry.report(),stream,indent=2)


if __name__=='__main__': main()
