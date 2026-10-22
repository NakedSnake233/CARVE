#!/usr/bin/env python3
"""Create an ephemeral namespace-scoped kubeconfig without printing its token."""
from __future__ import annotations

import argparse,json,os,subprocess
from pathlib import Path

import yaml

ISOLATED_NAMESPACE='e2r-supplement-20260925'
SERVICE_ACCOUNT='e2r-executor'
ADMIN_KUBECONFIG='/etc/rancher/k3s/k3s.yaml'


def run(argv,*,check=True):
    result=subprocess.run(argv,capture_output=True,text=True,timeout=20,shell=False,
                          env={**os.environ,'KUBECONFIG':ADMIN_KUBECONFIG})
    if check and result.returncode!=0:raise RuntimeError(result.stderr.strip() or 'command failed')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',default='${SCOPED_KUBECONFIG}')
    parser.add_argument('--duration',default='6h');args=parser.parse_args()
    out=Path(args.out)
    if out.exists():raise SystemExit('Refusing overwrite existing scoped kubeconfig')
    source=json.loads(run(['kubectl','config','view','--raw','--minify','-o','json']).stdout)
    if len(source.get('clusters',[]))!=1:raise RuntimeError('admin kubeconfig is not minified')
    cluster=source['clusters'][0]['cluster']
    token=run(['kubectl','create','token',SERVICE_ACCOUNT,'-n',ISOLATED_NAMESPACE,
               f'--duration={args.duration}']).stdout.strip()
    if not token:raise RuntimeError('service-account token is empty')
    config={'apiVersion':'v1','kind':'Config','clusters':[{'name':'e2r-cluster','cluster':cluster}],
            'users':[{'name':'e2r-executor','user':{'token':token}}],
            'contexts':[{'name':'e2r-isolated','context':{'cluster':'e2r-cluster',
                'user':'e2r-executor','namespace':ISOLATED_NAMESPACE}}],
            'current-context':'e2r-isolated'}
    descriptor=os.open(out,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(descriptor,'w') as handle:
        handle.write(yaml.safe_dump(config,sort_keys=False))
    base=['kubectl','--kubeconfig',str(out),'auth','can-i']
    checks={
        'get_pods_isolated':base+['get','pods','-n',ISOLATED_NAMESPACE],
        'patch_deployments_isolated':base+['patch','deployments.apps','-n',ISOLATED_NAMESPACE],
        'delete_chaos_isolated':base+['delete','stresschaos.chaos-mesh.org','-n',ISOLATED_NAMESPACE],
        'portforward_isolated':base+['create','pods','--subresource=portforward','-n',ISOLATED_NAMESPACE],
        'get_pods_user_namespace':base+['get','pods','-n','online-boutique'],
        'delete_namespace':base+['delete','namespaces'],
    }
    observed={}
    for name,command in checks.items():
        result=run(command,check=False);answer=result.stdout.strip().lower()
        if answer=='yes' and result.returncode==0:
            observed[name]=True
        elif answer=='no' and result.returncode==1:
            observed[name]=False
        else:
            out.unlink(missing_ok=True);raise RuntimeError(f'RBAC query failed: {name}')
    expected={'get_pods_isolated':True,'patch_deployments_isolated':True,
              'delete_chaos_isolated':True,'get_pods_user_namespace':False,
              'portforward_isolated':True,'delete_namespace':False}
    if observed!=expected:
        out.unlink(missing_ok=True);raise RuntimeError(f'RBAC check mismatch: {observed}')
    print(json.dumps({'kubeconfig':str(out),'mode':oct(out.stat().st_mode & 0o777),
                      'namespace':ISOLATED_NAMESPACE,'service_account':SERVICE_ACCOUNT,
                      'rbac_checks':observed},indent=2))


if __name__=='__main__':main()
