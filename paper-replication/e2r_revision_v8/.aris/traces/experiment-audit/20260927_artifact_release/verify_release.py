"""Independent, offline, read-only artifact verification; output omits secret values."""
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
import zipfile

ROOT = Path('${PROJECT_ROOT}')
PKG = ROOT / 'artifacts/e2r_revision_v6'
TRACE = ROOT / '.aris/traces/experiment-audit/20260927_artifact_release'
def sha(data):
    return hashlib.sha256(data).hexdigest()

files = {str(p.relative_to(PKG)): p for p in PKG.rglob('*') if p.is_file()}
manifest = json.loads((PKG/'MANIFEST.json').read_text())['files']
listed = {r['path']: r for r in manifest}
issues=[]
for row in manifest:
    relative=PurePosixPath(row['path'])
    if relative.is_absolute() or '..' in relative.parts:
        issues.append({'path':row['path'],'issue':'unsafe manifest path'})
        continue
    path=files.get(row['path'])
    if path is None:
        issues.append({'path':row['path'],'issue':'missing file'})
        continue
    raw=path.read_bytes()
    if len(raw)!=row['bytes'] or sha(raw)!=row['sha256']:
        issues.append({'path':row['path'],'issue':'size/hash mismatch'})
integrity={'entries':len(manifest),'unique_paths':len(listed),'file_count':len(files),
           'listed_bytes':sum(x['bytes'] for x in manifest),
           'actual_listed_bytes':sum(files[r['path']].stat().st_size for r in manifest if r['path'] in files),
           'actual_total_bytes':sum(p.stat().st_size for p in files.values()),
           'unlisted_files':sorted(set(files)-set(listed)), 'issues':issues,
           'symlinks':[str(p.relative_to(PKG)) for p in PKG.rglob('*') if p.is_symlink()]}

archive=ROOT/'artifacts/e2r_revision_v6.tar.gz'
sidecar=ROOT/'artifacts/e2r_revision_v6.tar.gz.sha256'
tar_checks={'archive_sha256':sha(archive.read_bytes()),'sidecar_sha256':sidecar.read_text().split()[0],
            'unsafe_members':[],'special_members':[],'duplicate_members':[],
            'file_mismatches':[],'metadata_identities':[]}
with tarfile.open(archive,'r:gz') as tar:
    members=tar.getmembers()
    counts=Counter(m.name for m in members)
    tar_checks['duplicate_members']=[n for n,c in counts.items() if c>1]
    tar_files={}
    identities=set()
    for member in members:
        path=PurePosixPath(member.name)
        if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0]!=PKG.name:
            tar_checks['unsafe_members'].append(member.name)
        if not (member.isfile() or member.isdir()):
            tar_checks['special_members'].append({'name':member.name,'type':repr(member.type)})
        identities.add((member.uid,member.gid,member.uname,member.gname))
        if member.isfile():
            rel=str(path.relative_to(PKG.name))
            data=tar.extractfile(member).read()
            tar_files[rel]=sha(data)
            if rel not in files or data!=files[rel].read_bytes():
                tar_checks['file_mismatches'].append(rel)
    tar_checks.update(member_count=len(members),regular_file_count=len(tar_files),
                      only_in_tar=sorted(set(tar_files)-set(files)),only_in_directory=sorted(set(files)-set(tar_files)),
                      metadata_identities=[{'uid':i[0],'gid':i[1],'uname_class':'empty' if not i[2] else ('anonymous' if i[2] in {'root','anonymous'} else 'identifying'),
                                            'gname_class':'empty' if not i[3] else ('anonymous' if i[3] in {'root','anonymous'} else 'identifying')} for i in sorted(identities)])

# Retrieve the builder's exact sensitive marker literals without echoing their values.
# Add independent broader identity/credential formats and scan every file, including generated metadata.
builder=ast.parse((ROOT/'scripts/e2r/build_anonymous_artifact.py').read_text())
known=[]
for node in ast.walk(builder):
    if isinstance(node,ast.FunctionDef) and node.name=='scrub':
        for stmt in node.body:
            if isinstance(stmt,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='replacements' for t in stmt.targets):
                known=[x[0].encode() for x in ast.literal_eval(stmt.value)]
patterns={
    'home_directory':rb'/home/[A-Za-z0-9_.-]+',
    'local_user_marker':rb'anonymous-user',
    'opencode_key':rb'oc_sk_[A-Za-z0-9][A-Za-z0-9_-]{8,}',
    'openai_style_key':rb'\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}',
    'anthropic_style_key':rb'\bsk-ant-[A-Za-z0-9_-]{20,}',
    'github_token':rb'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})',
    'aws_access_key':rb'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b',
    'private_key':rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----',
    'jwt_token':rb'eyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}',
    'kubeconfig_embedded_key':rb'(?m)^\s*client-key-data:\s*[A-Za-z0-9+/=]{20,}',
    'kubeconfig_embedded_cert':rb'(?m)^\s*client-certificate-data:\s*[A-Za-z0-9+/=]{20,}',
}
patterns={k:re.compile(v) for k,v in patterns.items()}
hits=[]
def scan(label,data,checkpoint=False):
    for category,pattern in patterns.items():
        matches=list(pattern.finditer(data))
        if matches:
            hits.append({'path':label,'category':category,'count':len(matches),
                         'lines':sorted(set(data[:m.start()].count(b'\n')+1 for m in matches))[:100],
                         'checkpoint':checkpoint})
    matching=[i for i,m in enumerate(known) if m in data]
    if matching:
        hits.append({'path':label,'category':'exact_sensitive_marker','marker_indices':matching,'checkpoint':checkpoint})
checkpoints=[]
for rel,path in files.items():
    binary=path.suffix.lower() in {'.pt','.pth','.pkl','.pickle','.ckpt','.bin'}
    data=path.read_bytes()
    scan(rel,data,binary)
    if binary:
        entry={'path':rel,'bytes':len(data),'sha256':sha(data),'zip_members':0}
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as z:
                for info in z.infolist():
                    if not info.is_dir():
                        scan(rel+'::'+info.filename,z.read(info),True)
                        entry['zip_members']+=1
        checkpoints.append(entry)
secret_names=[]
for rel in files:
    path=PurePosixPath(rel)
    if path.name in {'llm_rca_config.json','llm_config.json','id_rsa','id_ed25519','id_ecdsa','authorized_keys','known_hosts','.env'} or '.ssh' in path.parts or '.kube' in path.parts or path.suffix in {'.kubeconfig','.pem','.key'}:
        secret_names.append(rel)

mf=PKG/'experiments/e2r/runs/full_data_v2/manifest.json'
data=json.loads(mf.read_text())
observations=[]
obs_issues=[]
for row in data['cases']:
    rel=Path(row['observation_file'])
    path=(mf.parent/rel).resolve()
    if rel.is_absolute() or not path.is_relative_to(PKG) or not path.is_file():
        obs_issues.append({'case_id':row['case_id'],'observation_file':str(rel)})
        continue
    obs=json.loads(path.read_text())
    observations.append(str(path.relative_to(PKG)))
    if obs.get('case_id')!=row['case_id']:
        obs_issues.append({'case_id':row['case_id'],'issue':'case identity mismatch'})
observation_check={'cases':len(data['cases']),'unique_case_ids':len({r['case_id'] for r in data['cases']}),
                   'resolved':len(observations),'unique_resolved':len(set(observations)),'issues':obs_issues}

result={'manifest':integrity,'tar':tar_checks,'scan':{'all_files_scanned':len(files),'patterns':list(patterns),
        'exact_sensitive_marker_count':len(known),'hits':hits,'secret_filename_hits':secret_names,
        'checkpoint_count':len(checkpoints),'checkpoint_zip_member_count':sum(c['zip_members'] for c in checkpoints),
        'checkpoints':checkpoints},'observations':observation_check}
(TRACE/'deterministic_checks.json').write_text(json.dumps(result,indent=2)+'\n')
(TRACE/'audited_input_hashes.json').write_text(json.dumps({**{p:sha(f.read_bytes()) for p,f in files.items()},
    '../e2r_revision_v6.tar.gz':tar_checks['archive_sha256'],'../e2r_revision_v6.tar.gz.sha256':sha(sidecar.read_bytes())},indent=2)+'\n')
print(json.dumps({'manifest':integrity,'tar':tar_checks,'scan':{k:v for k,v in result['scan'].items() if k!='checkpoints'},'observations':observation_check},indent=2))
