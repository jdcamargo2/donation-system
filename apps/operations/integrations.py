"""Small extension registry owned by Operations, without integration imports."""

_project_detail_extensions = []


def register_project_detail_extension(extension):
    """
    PRE: extension is a callable accepting (project, user) and returning a context dict.
    POST: registers it once for internal project-detail composition.
    """
    if extension not in _project_detail_extensions:
        _project_detail_extensions.append(extension)


def get_project_detail_integration_context(project, user):
    """
    PRE: project is persisted and user is the authenticated viewer.
    POST: merges enabled integration contexts without Operations importing integration modules.
    """
    context = {}
    for extension in _project_detail_extensions:
        context.update(extension(project, user))
    return context
