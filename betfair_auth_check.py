"""One-shot certificate login and read-only horse market check; no retries."""
import json
import os
from pathlib import Path
import ssl
import tempfile
import urllib.error
import urllib.parse
import urllib.request


class CheckFailed(Exception):
    pass


def post(url, payload, headers, context):
    request = urllib.request.Request(url, data=payload, headers=headers, method='POST')
    # Do not forward credentials to a redirect target.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, hdrs, newurl):
            return None
    opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=context))
    try:
        with opener.open(request, timeout=30) as response:
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise CheckFailed('Unexpected response size; stopped without retry.')
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        raise CheckFailed(f'HTTP {exc.code}; stopped without retry. Response body withheld.') from None
    except (ValueError, urllib.error.URLError, TimeoutError):
        raise CheckFailed('Network or non-JSON response; stopped without retry. Details withheld.') from None


def run():
    names = ['BETFAIR_APP_KEY', 'BETFAIR_USERNAME', 'BETFAIR_PASSWORD',
             'BETFAIR_CERT', 'BETFAIR_PRIVATE_KEY']
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        raise CheckFailed('Missing secrets: ' + ', '.join(missing))
    app_key = os.environ['BETFAIR_APP_KEY'].strip()
    with tempfile.TemporaryDirectory(prefix='betfair-auth-') as directory:
        cert = Path(directory) / 'client.crt'
        key = Path(directory) / 'client.key'
        for path, name in [(cert, 'BETFAIR_CERT'), (key, 'BETFAIR_PRIVATE_KEY')]:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w', encoding='ascii') as handle:
                handle.write(os.environ[name].replace('\r\n', '\n').strip() + '\n')
        context = ssl.create_default_context()
        try:
            context.load_cert_chain(str(cert), str(key))
        except (ssl.SSLError, ValueError, OSError):
            raise CheckFailed('Certificate/key could not be loaded. Check complete PEM contents and matching pair.') from None
        login = post(
            'https://identitysso-cert.betfair.com.au/api/certlogin',
            urllib.parse.urlencode({'username': os.environ['BETFAIR_USERNAME'],
                                    'password': os.environ['BETFAIR_PASSWORD']}).encode(),
            {'X-Application': app_key, 'Content-Type': 'application/x-www-form-urlencoded'}, context)
        if not isinstance(login, dict) or login.get('loginStatus') != 'SUCCESS' or not login.get('sessionToken'):
            status = login.get('loginStatus') if isinstance(login, dict) else None
            allowed = {'CERT_AUTH_REQUIRED', 'INVALID_USERNAME_OR_PASSWORD', 'INVALID_PASSWORD',
                       'BETTING_RESTRICTED_LOCATION', 'ACCOUNT_NOW_LOCKED', 'ACCOUNT_PENDING_PASSWORD_CHANGE',
                       'CONTACT_VERIFICATION_REQUIRED', 'KYC_SUSPEND', 'ACTIONS_REQUIRED'}
            raise CheckFailed('Login failed: ' + (status if status in allowed else 'status withheld') + '. No retries.')
        token = login['sessionToken']
        try:
            result = post('https://api.betfair.com/exchange/betting/json-rpc/v1',
                          json.dumps({'jsonrpc': '2.0', 'method': 'SportsAPING/v1.0/listEventTypes',
                                      'params': {'filter': {'eventTypeIds': ['7'], 'marketCountries': ['AU']}},
                                      'id': 1}).encode(),
                          {'X-Application': app_key, 'X-Authentication': token,
                           'Content-Type': 'application/json'}, ssl.create_default_context())
            if not isinstance(result, dict) or 'error' in result or not isinstance(result.get('result'), list):
                raise CheckFailed('Login succeeded, but market query failed. Raw response withheld; no retries.')
            count = sum(item.get('marketCount', 0) for item in result['result'])
            print(f'PASS: certificate login and read-only market query succeeded. Australian horse markets: {count}.')
            print('No bets placed. No odds collected. Configured for the delayed key supplied by the user.')
        finally:
            try:
                post('https://identitysso.betfair.com.au/api/logout', b'',
                     {'X-Application': app_key, 'X-Authentication': token,
                      'Content-Type': 'application/x-www-form-urlencoded'}, ssl.create_default_context())
            except CheckFailed:
                print('Logout not confirmed; session token remains undisclosed and will expire.')


if __name__ == '__main__':
    try:
        run()
    except CheckFailed as exc:
        print('STOPPED: ' + str(exc))
        raise SystemExit(1)
    except Exception:
        print('STOPPED: unexpected local error; details withheld to protect credentials. No retries.')
        raise SystemExit(1)
