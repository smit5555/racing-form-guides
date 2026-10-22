"""Bounded May/June source diagnosis. No guessed URL variants or block bypass."""
import argparse
import datetime as dt
import hashlib
import json
import logging
from pathlib import Path
import re
from urllib.parse import parse_qs,urlsplit,urljoin

from download_forms import (Client,Links,StopRun,direct_meetings,form_unavailable,
                            historical_form_link,is_tab_meeting,validate_direct_identity,validate_html)

TARGETS={'June':(dt.date(2026,6,1),'NSW','Corowa'),
         'May':(dt.date(2026,5,1),'NSW','Wagga')}


def signature(url):
    p=urlsplit(url)
    return p.scheme.lower(),p.netloc.lower(),p.path.lower(),sorted(parse_qs(p.query,keep_blank_values=True).items())


def validate_guide(body,venue,date):
    if form_unavailable(body):return 'unavailable'
    validate_direct_identity(body,venue,date)
    if not re.search(r'Race\s*1\b',body,re.I) or 'Prizemoney' not in body:
        raise StopRun('Expected pre-race form missing; no results substitution')
    return 'available' if is_tab_meeting(body) else 'non_tab'


def run(month,output,client=None):
    if month not in ('June','May','both'):raise ValueError('Choose June, May or both')
    out=Path(output);out.mkdir(parents=True,exist_ok=False)
    (out/'source-pages').mkdir();(out/'verified-guides').mkdir()
    client=client or Client()  # Same destination, agent, 20-second delays and block stopping.
    report={'status':'running','targets':[],'requests':[],
            'scope':'One recorded NSW meeting per month. Not complete month/state coverage.'}
    error=None
    def get(url,label):
        if len(report['requests'])>=6:raise StopRun('Diagnostic request cap reached')
        record={'url':url,'phase':label};report['requests'].append(record)
        logging.info('%s: %s',label,url)
        body=client.get(url)
        file=out/'source-pages'/(label+'.html')
        file.write_text(body,encoding='utf-8',newline='')
        record.update(file=str(file.relative_to(out)),sha256=hashlib.sha256(body.encode()).hexdigest())
        return body
    try:
        for name in (('June','May') if month=='both' else (month,)):
            date,state,venue=TARGETS[name]
            form_url=direct_meetings(venue,state,date)[0][0]
            item={'month':name,'date':date.isoformat(),'state':state,'venue':venue,'direct_url':form_url}
            report['targets'].append(item)
            body=get(form_url,name+'-direct-form')
            status=validate_guide(body,venue,date);item['direct_status']=status
            chosen_url=form_url
            if status=='unavailable':
                # Inspect the normal official Results tab solely to discover its
                # matching advertised Form link; never save results as a guide.
                results_url=form_url.replace('/Form.aspx?','/Results.aspx?')
                results=get(results_url,name+'-source-links')
                advertised=historical_form_link(results,results_url)
                item['advertised_form_url']=advertised
                if signature(advertised)==signature(form_url):
                    item['status']='unavailable_no_distinct_advertised_form_link'
                    continue
                body=get(advertised,name+'-advertised-form')
                status=validate_guide(body,venue,date);chosen_url=advertised
                item['advertised_status']=status
            item['status']=status
            if status=='available':
                path=out/'verified-guides'/f'{state}_{date:%Y%m%d}_{venue.replace(" ","_")}.html'
                path.write_text(body,encoding='utf-8',newline='')
                item.update(guide=str(path.relative_to(out)),guide_url=chosen_url,guide_sha256=hashlib.sha256(body.encode()).hexdigest())
    except (StopRun,OSError,ValueError) as exc:
        error=str(exc);report['status']='stopped';report['stopped_reason']=error
    finally:
        if report['status']=='running':report['status']='processed'
        (out/'diagnosis.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        lines=['# May / June guide-source check','',
               'This checks two sample meetings at most. Success does not prove complete historical coverage.',
               'Green workflow status means the check ran, not that guides were available.',
               'No alternative hostname, proxy, session trick, guessed parameter or results-as-form substitution is used.','']
        for item in report['targets']:lines.append(f"{item['month']} — {item['venue']} {item['date']}: {item.get('status','stopped during check')}")
        if error:lines+=['','STOP: '+error,'Do not retry automatically. Inspect the saved evidence first.']
        lines+=['','See diagnosis.json and source-pages/. Only validated TAB pre-race guides enter verified-guides/.',
                'Do not run full May/June ranges until the sample results have been reviewed.']
        (out/'READ_FIRST.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    if error:raise StopRun(error)
    return report


def check_saved_all_form(saved,month,output,client=None):
    """Inspect cached source links, then request at most one advertised All Form per month."""
    if month not in ('June','May','both'):raise ValueError('Choose June, May or both')
    saved=Path(saved).resolve()
    previous=json.loads((saved/'diagnosis.json').read_text(encoding='utf-8'))
    if previous['status']!='processed':raise StopRun('Previous diagnostic stopped; inspect before further requests')
    out=Path(output);out.mkdir(parents=True,exist_ok=False)
    (out/'source-pages').mkdir();(out/'checked-guides').mkdir()
    client=client or Client();report={'status':'running','targets':[],'requests':[],
        'scope':'Two cached-source All Form samples at most. No repeat of previous six requests.'}
    error=None
    try:
        for name in (('June','May') if month=='both' else (month,)):
            date,state,venue=TARGETS[name]
            item={'month':name,'date':date.isoformat(),'state':state,'venue':venue};report['targets'].append(item)
            source=next(r for r in previous['requests'] if r['phase']==name+'-source-links')
            path=(saved/source['file']).resolve()
            if not path.is_relative_to(saved):raise StopRun('Invalid cached source path')
            body=path.read_bytes().decode('utf-8-sig')
            if hashlib.sha256(body.encode()).hexdigest()!=source['sha256']:raise StopRun('Cached source hash mismatch')
            validate_html(body)
            key=parse_qs(urlsplit(direct_meetings(venue,state,date)[0][0]).query).get('Key')
            if parse_qs(urlsplit(source['url']).query).get('Key')!=key:raise StopRun('Cached source Key mismatch')
            links=Links();links.feed(body)
            advertised={urljoin(source['url'],h) for h in links.links}
            advertised={u for u in advertised if urlsplit(u).scheme=='https' and urlsplit(u).netloc=='racingaustralia.horse'
                and urlsplit(u).path.lower()=='/freefields/allform.aspx' and parse_qs(urlsplit(u).query).get('Key')==key}
            if len(advertised)!=1:raise StopRun('No unique matching All Form link in cached source')
            url=advertised.pop();item.update(advertised_all_form_url=url,cached_source_sha256=source['sha256'])
            request={'url':url,'phase':name+'-all-form'};report['requests'].append(request)
            logging.info('Advertised All Form check: %s',url)
            page=client.get(url)
            raw=out/'source-pages'/(name+'-all-form.html');raw.write_text(page,encoding='utf-8',newline='')
            request.update(file=str(raw.relative_to(out)),sha256=hashlib.sha256(page.encode()).hexdigest())
            status=validate_guide(page,venue,date);item['status']=status
            if status=='available':
                guide=out/'checked-guides'/f'{state}_{date:%Y%m%d}_{venue.replace(" ","_")}.html'
                guide.write_text(page,encoding='utf-8',newline='')
                from rules_v2.guides import parse_guide
                try:
                    races=parse_guide(guide)
                    item['model_parser']={'parsed_races':len(races),'races_without_parser_exclusions':sum(not r['exclusions'] for r in races),
                        'same_day_future_form_races':sum(any('Same-day/future' in reason for reason in r['exclusions']) for r in races)}
                except ValueError as exc:item['model_parser']={'needs_adapter_review':str(exc)}
    except (StopRun,OSError,ValueError,KeyError,StopIteration) as exc:
        error=str(exc) or 'Required cached source missing';report.update(status='stopped',stopped_reason=error)
    finally:
        if report['status']=='running':report['status']='processed'
        (out/'diagnosis.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        lines=['# May/June advertised All Form check','',
            'Uses the previously saved source links; only the two distinct advertised All Form pages can be requested.',
            'Results pages and Recent Form are not fetched again. Checked guides still require parser and historical-date review.','']
        lines += [f"{i['month']} {i['venue']} {i['date']}: {i.get('status','stopped')}" for i in report['targets']]
        if error:lines+=['','STOP: '+error,'No retries or additional source requests.']
        lines+=['','Send this diagnostic ZIP for review before requesting full months.']
        (out/'READ_FIRST.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    if error:raise StopRun(error)
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--month',choices=('June','May','both'),default='both')
    p.add_argument('--output',default='historical-guide-check')
    p.add_argument('--saved-check-folder',help='Optional earlier diagnostic folder: check only its advertised All Form links')
    a=p.parse_args();logging.basicConfig(level=logging.INFO,format='%(levelname)s %(message)s')
    try:
        if a.saved_check_folder:check_saved_all_form(a.saved_check_folder,a.month,a.output)
        else:run(a.month,a.output)
    except (StopRun,OSError,ValueError) as exc:p.exit(1,str(exc)+'\n')


if __name__=='__main__':main()
