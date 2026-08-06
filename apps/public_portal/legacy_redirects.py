"""Permanent compatibility redirects from legacy /transparency/** paths.

GET and HEAD only. Never executes public selectors. Never accepts a
user-supplied redirect target. Safe query keys (e.g. page) are preserved.
"""

from django.http import HttpResponsePermanentRedirect
from django.urls import reverse
from django.views import View

# Query keys that may be forwarded to canonical public routes.
_SAFE_QUERY_KEYS = frozenset({'page'})


def _safe_query_string(request):
    """
    PRE: request.query_params may contain arbitrary keys.
    POST: returns a query string with only allowlisted keys, or empty string.
    """
    preserved = []
    for key in sorted(_SAFE_QUERY_KEYS):
        values = request.GET.getlist(key)
        for value in values:
            if value:
                preserved.append(f'{key}={value}')
    if not preserved:
        return ''
    return '?' + '&'.join(preserved)


class LegacyPublicRedirectView(View):
    """
    PRE: named ``target_url_name`` resolves to a canonical public route;
         optional ``pk_kwarg`` / ``update_id_kwarg`` / ``attachment_id_kwarg``
         map path converters into reverse kwargs.
    POST: returns HTTP 301 to the deterministic reverse target; no content
          rendering and no selector execution.
    """

    http_method_names = ['get', 'head', 'options']
    target_url_name = None
    pk_kwarg = None
    update_id_kwarg = None
    attachment_id_kwarg = None

    def get(self, request, *args, **kwargs):
        reverse_kwargs = {}
        if self.pk_kwarg is not None and self.pk_kwarg in kwargs:
            reverse_kwargs['pk'] = kwargs[self.pk_kwarg]
        if self.update_id_kwarg is not None and self.update_id_kwarg in kwargs:
            reverse_kwargs['update_id'] = kwargs[self.update_id_kwarg]
        if self.attachment_id_kwarg is not None and self.attachment_id_kwarg in kwargs:
            reverse_kwargs['attachment_id'] = kwargs[self.attachment_id_kwarg]
        target = reverse(self.target_url_name, kwargs=reverse_kwargs)
        return HttpResponsePermanentRedirect(target + _safe_query_string(request))

    def head(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)
