"""Small result contract and fail-closed persisted risk marker, no browser state."""
import json
import os
from pathlib import Path


def result(value):
    out = dict(value)
    code = out.get('error') or out.get('status', '')
    if out.get('risk_control') or code == 'risk_control':
        status = 'risk_control'
    elif out.get('requires_user_login') or code == 'login_required':
        status = 'auth_required'
    elif code == 'cooldown':
        status = 'cooldown'
    elif out.get('success'):
        status = 'ok'
    elif code in {'no_products_extracted','no_product_content','missing_product_title'} or ('items' in out and not out['items'] and not code):
        status = 'not_found'
    else:
        status = 'error'
    fields = dict(out.get('fields') or {})
    for name in ('images','image','shop','product_parameters','good_reviews','bad_reviews','specs'):
        if name in out:
            fields.setdefault(name, 'ok' if out[name] else 'missing')
    if status == 'ok' and any(v != 'ok' for v in fields.values()):
        status = 'partial'
    out.update(ok=status in {'ok','partial'}, status=status, fields=fields)
    if code and status not in {'ok','partial'}:
        out['error_code'] = 'auth_required' if code == 'login_required' else code
    out['data'] = {k:v for k,v in out.items() if k not in {'ok','status','error_code','retry_after_s','data','fields'}}
    return out


class RiskStore:
    def __init__(self, directory):
        self.path = Path(directory) / 'risk-lock.json'

    def load(self):
        try:
            return json.loads(self.path.read_text()).get('blocked') is True
        except FileNotFoundError:
            return False
        except (OSError, ValueError, AttributeError):
            return True

    def save(self, blocked):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_suffix('.tmp')
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, 'w') as file:
            json.dump({'blocked':bool(blocked)}, file)
        temporary.replace(self.path)
