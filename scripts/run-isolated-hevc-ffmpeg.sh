#!/usr/bin/env bash
# Run copied upstream configure/build scripts only inside their private0061 tree.
set -euo pipefail
source "$(dirname "$0")/lib.sh"
if [[ "$(uname -s)" == Linux && -z "${APOSTATE_BUILD_IMAGE_ID:-}" ]]; then
  exec bash "$REPO_ROOT/scripts/in-linux-build-container.sh" scripts/run-isolated-hevc-ffmpeg.sh "$@"
fi
[[ "$(uname -s)" == Linux && -n "${APOSTATE_BUILD_IMAGE_ID:-}" ]] || die 'requires pinned Linux build container'
hevc_work="${1:?usage: run-isolated-hevc-ffmpeg.sh <prepared-private-work> [configure|build|decode]}"
hevc_action="${2:-configure}"
if [[ "$hevc_action" == decode ]]; then
  exec python3 "$REPO_ROOT/scripts/run-hevc-decode-control.py" --work "$hevc_work" --run-name "${3:-decode-control}"
fi
python3 - "$hevc_work" "$hevc_action" <<'PY'
import hashlib,json,os,pathlib,subprocess,sys,time
work=pathlib.Path(sys.argv[1]).resolve(); action=sys.argv[2]
assert action in {'configure','build'}
receipt=json.loads((work/'receipt.json').read_text())
assert receipt['ffmpeg_revision']=='2b68d2babae73714846961fb0ee47e3b3d2e39a9'
assert receipt['chromium_revision']=='79460ebecaa5625e57a5fb679a735659e73dc687'
receipt['image_id']=os.environ['APOSTATE_BUILD_IMAGE_ID']
os.nice(10)
for variant in ['baseline','hevc']:
    source=work/variant/'src'; ffmpeg=source/'third_party/ffmpeg'
    assert subprocess.check_output(['git','-C',str(ffmpeg),'rev-parse','HEAD'],text=True).strip()==receipt['ffmpeg_revision']
    env=dict(os.environ)
    env['PATH']=str(source/'third_party/llvm-build/Release+Asserts/bin')+':'+str(work/variant/'tools')+':'+env['PATH']
    command=['python3',str(source/'media/ffmpeg/scripts/build_ffmpeg.py'),'linux','x64','--branding','Chrome']
    if action=='configure':command.append('--config-only')
    data={'command':command,'started_unix':time.time(),'passed':False}
    receipt['variants'][variant][action]=data
    try:
        with (work/(variant+'-'+action+'.log')).open('w') as log:
            run=subprocess.run(command,cwd=ffmpeg,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=600)
        data['returncode']=run.returncode;data['passed']=run.returncode==0
        assert run.returncode==0, variant+' '+action+' failed'
    finally:
        data['finished_unix']=time.time()
        (work/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps({'work':str(work),'action':action,'passed':True}))
PY
