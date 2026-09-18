// Turnkey post-build check for the GPU cross-seed linking property.
// Asserts three things and exits non-zero on failure:
//   1 ROTATION  distinct renderer strings across distinct seeds > 1
//   2 PINNED    a repeated seed yields a byte-identical identity
//   3 COHERENT  cores/memory rotate too, so rotation is not GPU-only
// Usage: node gpupool-check.mjs [binaryPath] [seedCount]
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs'; import os from 'node:os'; import path from 'node:path';
const BIN=process.argv[2]||'/tmp/macbin/apostate-152.0.7977.83-macos-arm64/Chromium.app/Contents/MacOS/Chromium';
const SEEDS=Number(process.argv[3]||10);
const PORT=18088;
const srv=http.createServer((q,s)=>{
  if(q.method==='POST'){let b='';q.on('data',c=>b+=c);q.on('end',()=>{s.end('ok');srv.emit('p',JSON.parse(b));});return;}
  s.writeHead(200,{'content-type':'text/html'});
  s.end(`<!doctype html><meta charset=utf-8><script>
  const c=document.createElement('canvas');
  let g=null; try{g=c.getContext('webgl2')||c.getContext('webgl');}catch(e){}
  let d=null; try{d=g&&g.getExtension('WEBGL_debug_renderer_info');}catch(e){}
  fetch('/c',{method:'POST',body:JSON.stringify({
    renderer:d?g.getParameter(d.UNMASKED_RENDERER_WEBGL):null,
    vendor:d?g.getParameter(d.UNMASKED_VENDOR_WEBGL):null,
    cores:navigator.hardwareConcurrency, mem:navigator.deviceMemory,
    screen:[screen.width,screen.height]})});
  </script>`);
});
await new Promise(r=>srv.listen(PORT,'127.0.0.1',r));
async function run(seed){
  const u=fs.mkdtempSync(path.join(os.tmpdir(),'gp-'));
  const args=[`--user-data-dir=${u}`,'--no-first-run','--no-default-browser-check',
    '--disable-search-engine-choice-screen','--headless=new'];
  if(seed) args.push(`--fingerprint=${seed}`);
  args.push(`http://127.0.0.1:${PORT}/`);
  const p=new Promise(r=>srv.once('p',r));
  const proc=spawn(BIN,args,{stdio:['ignore','pipe','pipe']});
  const r=await Promise.race([p,new Promise(res=>setTimeout(()=>res(null),30000))]);
  try{proc.kill('SIGTERM');}catch(e){}
  await new Promise(x=>setTimeout(x,120));
  return r;
}
const bySeed=[];
for(let i=0;i<SEEDS;i++){ const s='seed'+String(i).padStart(3,'0'); const r=await run(s); if(r) bySeed.push({seed:s,...r}); }
const pinA=await run('pinned-identical'); const pinB=await run('pinned-identical');
srv.close();
const uniq=a=>[...new Set(a)];
const renderers=uniq(bySeed.map(x=>x.renderer));
const cores=uniq(bySeed.map(x=>x.cores)); const mems=uniq(bySeed.map(x=>x.mem));
const idOf=x=>x?JSON.stringify([x.renderer,x.cores,x.mem,x.screen]):'null';
const fails=[];
console.log('binary: '+BIN); console.log('seeds sampled: '+bySeed.length+'\n');
console.log('1 ROTATION  distinct renderer strings across seeds: '+renderers.length);
console.log('      (a linux HOST defaults to a windows PERSONA, so D3D11 strings here are correct)');
renderers.forEach(r=>console.log('      '+JSON.stringify(r)));
if(renderers.length<=1) fails.push('ROTATION: only '+renderers.length+' renderer string across '+bySeed.length+' seeds. Since patch 0102 the drawn anchor follows the CLAIMED platform and the one-member software anchor is never drawn, so a one-member pool is no longer a legal explanation on a default launch -- if --fingerprint-explain still says "has one measured member", the software anchor is being drawn and that is the bug. Otherwise the catalogue offers several identities and selection is pinning one: check that the identity draw is keyed on the seed and not on the host. Expected pools: a windows persona draws 15 identities across the two D3D11 anchors, macos 12, linux 11.');
console.log('\n2 PINNED    same seed twice -> '+(idOf(pinA)===idOf(pinB)?'identical':'DIFFERENT'));
console.log('      '+idOf(pinA)); if(idOf(pinA)!==idOf(pinB)) console.log('      '+idOf(pinB));
if(idOf(pinA)!==idOf(pinB)) fails.push('PINNED: a repeated seed did not reproduce its identity. Determinism is broken, which is worse than any rotation problem.');
console.log('\n3 COHERENT  distinct cores: '+cores.length+' '+JSON.stringify(cores)+'   distinct memory: '+mems.length+' '+JSON.stringify(mems));
if(cores.length<=1&&mems.length<=1) fails.push('COHERENT: neither cores nor memory rotated, so this run tells you nothing about the GPU axis specifically.');
console.log('\n'+(fails.length?'FAIL':'PASS'));
fails.forEach(f=>console.log('  - '+f));
process.exitCode=fails.length?1:0;
setTimeout(()=>process.exit(process.exitCode||0),200);
