from django.core.files.storage import InMemoryStorage
from django.db import connection


class StubAttachmentClient:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.in_atomic_flags = []

    def download_attachment(self, url):
        # PRE: a pending attachment supplies its source URL.
        # POST: records the URL and returns or raises the next configured outcome.
        self.calls.append(url)
        self.in_atomic_flags.append(connection.in_atomic_block)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class RecordingAttachmentStorage(InMemoryStorage):
    def __init__(self, *, fail_delete=False):
        super().__init__()
        self.fail_delete = fail_delete
        self.saved = []
        self.deleted = []

    def save(self, name, content, max_length=None):
        self.saved.append((name, connection.in_atomic_block))
        return super().save(name, content, max_length)

    def delete(self, name):
        self.deleted.append((name, connection.in_atomic_block))
        if self.fail_delete:
            raise OSError("storage delete failed")
        return super().delete(name)
