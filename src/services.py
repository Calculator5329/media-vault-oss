"""Render persistent user-service definitions for the approved local vault.

Rendering writes only to an explicit Linux staging directory. Installation and
service-manager changes are a separate visible operation; existing units are
retained before replacement.
"""
import argparse
import json
from pathlib import Path
from .portable import assert_local_state, on_external_root


def quoted(value,exec_argument=False):
    text=str(value)
    if any(c in text for c in ('\n','\r','\x00')):raise ValueError('Invalid unit argument')
    text=text.replace('%','%%')
    if exec_argument:text=text.replace('$','$$')
    return json.dumps(text,ensure_ascii=False)


def render(repo,resources,exports,logs,kit):
    repo=Path(repo).absolute();resources=Path(resources).absolute();logs=Path(logs).absolute();kit=Path(kit).absolute()
    config=json.loads((repo/'vault.config.json').read_text());models=json.loads(resources.read_text())
    python=Path(models['vision_python']).absolute()
    roots=[Path(s).absolute() for s in config['sources']]
    if not roots:raise ValueError('At least one media source is required')
    for path in (repo,resources,logs,kit,python):
        resolved=path.resolve()
        assert_local_state(resolved,'Runtime state')
    commands={
        'imports':[python,'-m','src.jobs','--config',repo/'vault.config.json','--directory',repo/'.catalog','--exports',Path(exports).absolute(),'--watch'],
        'enrichment':[python,'-m','src.enrichment','--resources',resources,'--directory',repo/'.catalog',*[argument for root in roots for argument in ('--source',root)],'--source',Path(exports).absolute(),'--seconds','300','--limit','1000'],
        'viewer':[python,'-m','src.server','--port','8770','--database',repo/'.catalog/catalog.db','--imports',repo/'.catalog/imports.db','--vision-model',models['vision_model']],
    }
    if any(c in str(logs) for c in ('%', '$', '\n', '\r', '\x00', '"')):raise ValueError('Unsupported log directory characters')
    units={}
    for name,args in commands.items():
        units['media-vault-'+name+'.service']='\n'.join([
            '[Unit]','Description=Media Vault '+name,'',
            '[Service]','Type=simple','WorkingDirectory='+str(logs),
            'Environment='+quoted('PYTHONPATH='+str(repo)),
            'Environment='+quoted('MEDIA_VAULT_KIT_PATH='+str(kit)),
            'ExecStart='+' '.join(quoted(a,exec_argument=True) for a in args),
            'Restart=on-failure','RestartSec=30','TimeoutStopSec=30',
            'StandardOutput=append:'+str(logs/(name+'-service.log')),
            'StandardError=append:'+str(logs/(name+'-service.log')),'',
            '[Install]','WantedBy=default.target',''])
    return units


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--repo',type=Path,required=True);p.add_argument('--resources',type=Path,required=True);p.add_argument('--exports',type=Path,required=True);p.add_argument('--logs',type=Path,required=True);p.add_argument('--kit',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    units=render(a.repo,a.resources,a.exports,a.logs,a.kit);output=a.output.resolve()
    if on_external_root(output) or any(output==r or r in output.parents for r in [a.exports.resolve(),*[Path(s).resolve() for s in json.loads((a.repo/'vault.config.json').read_text())['sources']]]):p.error('Unit staging must stay on the local drive outside originals')
    output.mkdir(parents=True,exist_ok=True)
    for name,unit in units.items():
        with (output/name).open('x') as stream:stream.write(unit)
    print(json.dumps({'units':sorted(units),'staged':str(output)}))

if __name__=='__main__':main()
