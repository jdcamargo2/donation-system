"""
Invalidate the Django cache used by public portal ``cache_page`` views.

The public portal wraps list/detail/home/feed views with ``cache_page`` and
does not maintain a project-specific key registry. Per-view cache keys are
backend-dependent and cannot be deleted reliably with wildcards on common
backends (for example LocMem).

For this phase the application-local default cache is cleared after a
successful Project publication lifecycle change (publish, unpublish, or
finish of a public Project). Callers must not invoke this helper when a
domain validation fails.
"""

from django.core.cache import cache


def invalidate_public_portal_cache():
    """
    PRE: a publication lifecycle mutation has already been persisted successfully.
    POST: clears the default Django cache so public portal pages reflect the change.
    """
    cache.clear()
