// Racenet one-meeting pilot. Node 22+, Playwright 1.56.1.
import fs from 'node:fs';
import crypto from 'node:crypto';
import { chromium, request } from 'playwright';
import { extractRows, csv } from './racenet-results-core.mjs';
const out='racenet-output'; fs.mkdirSync(out,{recursive:true});
const manifest={status:'started',captured_at:new Date().toISOString(),scope:'One supplied meeting URL; no daily coverage claim',warnings:[]};
let browser,client;
try {
 const url=new URL(process.env.RACE_URL || 'https://www.racenet.com.au/results/horse-racing/kalgoorlie-20261001/gordons-pink-gin-maiden-race-1');
 if(url.protocol!=='https:'||!['www.racenet.com.au','racenet.com.au'].includes(url.hostname)||!/^\/results\/horse-racing\/[^/]+\/[^/]+\/?$/.test(url.pathname)||url.username||url.password||url.port||url.search) throw Error('Supply a normal HTTPS Racenet individual race results URL.');
 manifest.source_url=url.href;
 client=await request.newContext({userAgent:'RacenetResultsCollector/1.0 (personal one-meeting pilot)',ignoreHTTPSErrors:false});
 const response=await client.get(url.href,{timeout:45000,maxRedirects:0});
 manifest.http_status=response.status();
 const body=await response.body();fs.writeFileSync(out+'/source.html',body);
 manifest.source_sha256=crypto.createHash('sha256').update(body).digest('hex');
 if(!response.ok())throw Error('HTTP '+response.status()+': stopped; response preserved. No retries.');
 if(body.length>15000000)throw Error('Response too large');
 const html=body.toString('utf8');
 const payload=[...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)].map(x=>x[1]).find(x=>/window\.__NUXT__\s*=/.test(x));
 if(!payload)throw Error('Embedded NUXT data missing; may be a block or changed page. Inspect source.html.');
 // Decode only the embedded script inside a disposable offline browser, never Node eval.
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({offline:true,serviceWorkers:'block',acceptDownloads:false});
 await context.route('**/*',route=>route.abort());
 const page=await context.newPage();
 const data=await Promise.race([page.evaluate(code=>{(0,eval)(code);return window.__NUXT__;},payload),new Promise((_,reject)=>setTimeout(()=>reject(Error('Embedded data decoding timed out')),10000).unref())]);
 fs.writeFileSync(out+'/embedded-data.json',JSON.stringify(data,null,2));
 const result=extractRows(data,url.href);
 Object.assign(manifest,result.summary);manifest.warnings=result.warnings;
 fs.writeFileSync(out+'/results.csv',csv(result.rows));
 fs.writeFileSync(out+'/races.json',JSON.stringify(result.races,null,2));
 manifest.status=result.warnings.length?'needs_review':'captured';
 if(result.warnings.length)process.exitCode=2;
} catch(e){manifest.status='stopped';manifest.error=e.message;process.exitCode=1;}
finally{
 await browser?.close();await client?.dispose();
 fs.writeFileSync(out+'/manifest.json',JSON.stringify(manifest,null,2));
 fs.writeFileSync(out+'/READ-ME.txt','Read manifest.json first. A successful capture proves only this supplied meeting, not all meetings on a date. Raw statuses, negative finish codes and null prices are preserved. source.html is the original HTTP response; its byte hash is in manifest.json. Race URLs are constructed from embedded slugs, not independently fetched. No tote/bookmaker odds replace startingPrice.\n');
 console.log(JSON.stringify(manifest,null,2));
}
