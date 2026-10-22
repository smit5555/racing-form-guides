"""Strict unavailable-result recovery and verified partial-result reuse."""
import datetime as dt
import hashlib
import json
from pathlib import Path
from urllib.parse import quote
from rules_v2.results import Client, SavedResults, StopResults, result_page
from rules_v2.guides import Tree
from rules_v2.run import now

BLOCK_MARKERS=('captcha','access denied','verify you are human','checking your browser','cf-chl-',
               'unusual traffic','request blocked','just a moment','incapsula','challenge-platform',
               'enable javascript and cookies')


def parse_result(body,identity):
    """Only a verified matching meeting's explicit unavailable notice is recoverable."""
    if any(marker in body.lower() for marker in BLOCK_MARKERS):
        raise StopResults('CAPTCHA/block detected in result page; no further meetings')
    try:return result_page(body,identity),False
    except StopResults as exc:
        # result_page checks heading, date and the page's own state/venue Key
        # before reaching this specific no-table error. Other failures still stop.
        if (str(exc)=='No recognised official result tables'
            and 'results for this meeting are not currently available' in body.lower()
            and Tree(body).root.find('race-title') is None):
            return {},True
        raise


class ResumeResults(SavedResults):
    """Reuse verified archive pages, then request only meetings not in the archive."""
    resuming=True
    def __init__(self,path,meetings):
        super().__init__(path)
        self.live=Client()
        archive_sha=hashlib.sha256(Path(path).read_bytes()).hexdigest()
        try:
            if 'summary.json' in self.archive.namelist():
                prior=json.loads(self.archive.read('summary.json'))
                reason=prior.get('stopped_reason')
                if prior.get('status')=='stopped' and reason!='No recognised official result tables':
                    raise ValueError('Saved run stopped on an access/parser error; inspect it before any resume request')
            for (date,state,venue),identity in meetings.items():
                day=dt.date.fromisoformat(date)
                key=f'{day.year}{day.strftime("%b")}{day.day:02d},{state},{venue}'
                url='https://racingaustralia.horse/FreeFields/Results.aspx?Key='+quote(key,safe='')
                name=f'official-pages/{state}_{date}_{venue.replace("/","_")}.html'
                if url in self.sources or name not in self.archive.namelist():continue
                # Older code wrote the failed page before recording source metadata.
                # Accept that extra page ONLY if identity and explicit unavailable
                # status pass inspection. Never silently refetch an unknown/block page.
                body=self.archive.read(name).decode('utf-8-sig')
                _,unavailable=parse_result(body,identity)
                if not unavailable:raise ValueError('Unindexed saved page needs review before resume')
                self.sources[url]=dict(url=url,date=date,state=state,meeting=venue,retrieved_utc=None,
                    body_sha256=hashlib.sha256(body.encode()).hexdigest(),source_status='explicit_unavailable',
                    recovered_from_archive_sha256=archive_sha,original_source_manifest_entry_missing=True,
                    recovery_verified_utc=now())
        except Exception:
            self.close();raise

    def get(self,url):
        if url in self.sources:return super().get(url)
        return self.live.get(url)

