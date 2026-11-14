"""Remember the server chosen before a screenshot, within the same chat."""
from time import monotonic

gs_seconds_to_wait = 60
_requests = {}


def request_key(event):
    return event.get('self_id'), event.get('group_id'), event.user_id


def remember(key, region):
    now = monotonic()
    for expired in [key for key, (_, deadline) in _requests.items() if deadline <= now]:
        _requests.pop(expired)
    _requests[key] = region, now + gs_seconds_to_wait


def pop_request(key):
    region, deadline = _requests.pop(key, (None, 0))
    return region if deadline > monotonic() else None
