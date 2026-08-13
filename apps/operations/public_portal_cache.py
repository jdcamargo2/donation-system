"""
Invalidate the Django cache used by public portal ``cache_page`` views.

The public portal wraps list/detail/home/feed views with ``cache_page`` and
does not maintain a project-specific key registry. Per-view cache keys are
backend-dependent and cannot be deleted reliably with wildcards on common
backends (for example LocMem).

The application-local default cache is cleared after successful mutations that
can change visible public metrics or listings (project publication lifecycle and
relevant financial changes). Callers must schedule invalidation only for
mutations that will commit; use ``schedule_public_portal_cache_invalidation``
inside ``transaction.atomic`` so rollbacks do not clear the cache.
"""

from django.core.cache import cache
from django.db import transaction


def invalidate_public_portal_cache():
    """
    PRE: a relevant portal-affecting mutation has already been committed (or the
         caller intentionally clears the cache outside a transaction).
    POST: clears the default Django cache so public portal pages reflect the change.
    """
    cache.clear()


def schedule_public_portal_cache_invalidation():
    """
    PRE: caller is inside a successful transactional mutation that may affect
         public portal pages or financial aggregates.
    POST: registers ``invalidate_public_portal_cache`` to run only after the
          outermost transaction commits; rollbacks leave the cache untouched.
    """
    transaction.on_commit(invalidate_public_portal_cache)
