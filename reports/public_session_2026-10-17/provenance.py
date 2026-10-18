"""Record current candidate identity and exact source patch against HEAD."""
import difflib
import hashlib
import json
from pathlib import Path
import subprocess

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
FILES=('rebalance/public_paper.py','rebalance/public_session.py',
       'rebalance/scenarios/test_public_paper.py','rebalance/scenarios/test_public_session.py',
       'rebalance/README.md')


def main():
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    patches=[]
    hashes={}
    for name in FILES:
        data=(ROOT/name).read_bytes()
        hashes[name]=hashlib.sha256(data).hexdigest()
        base=subprocess.run(['git','show',head+':'+name],cwd=ROOT,capture_output=True)
        patches.extend(difflib.unified_diff(
            base.stdout.decode('utf-8').splitlines(keepends=True) if base.returncode == 0 else [],
            data.decode('utf-8').splitlines(keepends=True),
            fromfile='a/'+name if base.returncode == 0 else '/dev/null',tofile='b/'+name))
    patch=''.join(patches)
    (HERE/'code.patch').write_text(patch,encoding='utf-8')
    result=dict(head=head,working_tree_candidate=True,code_sha256=hashes,
                patch_sha256=hashlib.sha256(patch.encode()).hexdigest(),
                inherited_pending_changes='prior public-paper closing fix and prior reports preserved',
                trading_authorized=False)
    (HERE/'candidate.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))


if __name__ == '__main__':
    main()
