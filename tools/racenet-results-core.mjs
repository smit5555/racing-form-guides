export function extractRows(n,source){
 const m=n.data?.find(x=>x.meeting?.events)?.meeting;
 if(!m?.events?.length)throw Error('No meeting.events data');
 const expected=new URL(source).pathname.split('/')[3];
 if(m.slug!==expected)throw Error('Meeting identity differs from supplied URL');
 const rows=[],races=[],warnings=[],keys=new Set();
 for(const e of m.events){
  if(!Array.isArray(e.selections)||(!e.selections.length&&!e.isAbandoned))throw Error('Missing selections for race '+e.eventNumber);
  const race_url=new URL('/results/horse-racing/'+m.slug+'/'+e.slug,source).href;
  const statuses={};let started=0;
  for(const s of e.selections){
   const key=[m.id,e.id,s.id].join('|');if(!s.id||keys.has(key))throw Error('Missing or duplicate selection ID');keys.add(key);
   statuses[s.status]=(statuses[s.status]||0)+1;if(s.status!=='SCRATCHED')started++;
   if(!s.competitor?.name)warnings.push('Missing horse name: '+key);
   if(e.isResulted&&s.status!=='SCRATCHED'&&s.startingPrice==null)warnings.push('Missing SP: '+key);
   rows.push({meeting_date:m.meetingDateLocal,track:m.venue?.name??m.name,state:m.state,meeting_id:m.id,meeting_stage:m.meetingStage,race_number:e.eventNumber,race_name:e.name,race_id:e.id,distance:e.distance,track_condition:e.trackCondition?.overall,track_rating:e.trackCondition?.rating,race_status:e.status,result_state:e.resultState,is_resulted:e.isResulted,is_abandoned:e.isAbandoned,horse:s.competitor?.name,competitor_id:s.competitor?.id,selection_id:s.id,saddlecloth:s.competitorNumber,barrier:s.barrierNumber,runner_status:s.status,status_abbreviation:s.statusAbv,finish_position:s.result?.finishPosition,selection_result:s.selectionResult,startingPrice:s.startingPrice,jockey:s.jockey?.name,trainer:s.trainer?.name,weight:s.weight,margin:s.result?.margin,source_url:source,race_url});
  }
  if(e.isResulted&&started!==e.starters)warnings.push('R'+e.eventNumber+': non-scratched count differs from starters');
  if(!e.isResulted&&!e.isAbandoned)warnings.push('R'+e.eventNumber+': not final or abandoned');
  races.push({race_number:e.eventNumber,race_id:e.id,race_name:e.name,race_url,selections:e.selections.length,starters:e.starters,statuses});
 }
 const nums=races.map(r=>r.race_number).sort((a,b)=>a-b);if(nums.some((x,i)=>x!==i+1))warnings.push('Race numbers are not contiguous from 1');
 return {rows,races,warnings,summary:{meeting_id:m.id,meeting_date:m.meetingDateLocal,track:m.venue?.name??m.name,race_count:races.length,selection_count:rows.length,scratched:rows.filter(r=>r.runner_status==='SCRATCHED').length}};
}
export function csv(rows){const cols=Object.keys(rows[0]||{});const q=v=>'"'+String(v??'').replaceAll('"','""')+'"';return '\ufeff'+[cols.map(q).join(','),...rows.map(r=>cols.map(k=>q(r[k])).join(','))].join('\r\n');}
