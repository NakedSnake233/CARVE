"""Read-only release provenance and constant-expression secret audit."""
import ast
import hashlib
import json
from pathlib import Path
import re

ROOT=Path('${PROJECT_ROOT}')
PKG=ROOT/'artifacts/e2r_revision_v7'
TRACE=ROOT/'.aris/traces/experiment-audit/20260927_artifact_release/v7'
sha=lambda b:hashlib.sha256(b).hexdigest()
mapping=json.loads((PKG/'ANONYMIZATION_MAP.json').read_text())['transformed_files']
map_checks=[]
for row in mapping:
    staged=PKG/row['path']
    source=ROOT/row['path']
    map_checks.append({'path':row['path'],'staged_matches':staged.is_file() and sha(staged.read_bytes())==row['staged_sha256'],
                       'private_source_exists':source.is_file(),'private_source_matches':source.is_file() and sha(source.read_bytes())==row['source_sha256']})

# Read original marker values into memory only; reports record safe categories, not reconstructed values.
tree=ast.parse((ROOT/'scripts/e2r/build_anonymous_artifact.py').read_text())
known=[]
for fn in ast.walk(tree):
    if isinstance(fn,ast.FunctionDef) and fn.name=='scrub':
        for stmt in fn.body:
            if isinstance(stmt,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='replacements' for t in stmt.targets):
                known=ast.literal_eval(stmt.value)

def folded(node):
    if isinstance(node,ast.Constant) and isinstance(node.value,(str,bytes)):
        return node.value
    if isinstance(node,ast.BinOp) and isinstance(node.op,ast.Add):
        left,right=folded(node.left),folded(node.right)
        if left is not None and type(left)==type(right):
            return left+right
    return None

matches=[]
parsed=0
for path in PKG.rglob('*.py'):
    try:tree=ast.parse(path.read_text())
    except SyntaxError:continue
    parsed+=1
    for node in ast.walk(tree):
        if not isinstance(node,ast.BinOp):continue
        value=folded(node)
        if value is None:continue
        data=value.encode() if isinstance(value,str) else value
        categories=[entry[2] if len(entry)>2 else 'source_marker_'+str(i) for i,entry in enumerate(known) if entry[0].encode() in data]
        if categories:
            matches.append({'path':str(path.relative_to(PKG)),'line':node.lineno,'categories':categories,'mechanism':'literal constant concatenation'})

unrelocated=[]
for path in PKG.rglob('*.json'):
    data=path.read_bytes()
    if b'${PROJECT_ROOT}/' in data:
        unrelocated.append(str(path.relative_to(PKG)))
result={'transformed_files':len(mapping),'unique_map_paths':len({r['path'] for r in mapping}),
        'map_staged_mismatches':[r for r in map_checks if not r['staged_matches']],
        'map_source_mismatches':[r for r in map_checks if not r['private_source_matches']],
        'map_checks':map_checks,'python_files_parsed':parsed,'folded_sensitive_markers':matches,
        'json_with_unrelocated_project_root':unrelocated}
(TRACE/'provenance_and_folded_markers.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k!='map_checks'},indent=2))
