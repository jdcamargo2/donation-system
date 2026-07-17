import json

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpResponseForbidden, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import FormView

from ..forms import ProjectMilestoneForm, TerminalActionConfirmationForm
from ..models import Project, ProjectMilestone
from ..services import (
    complete_project_milestone,
    create_project_milestone,
    delete_project_milestone,
    move_project_milestone_down,
    move_project_milestone_up,
    reopen_project_milestone,
    update_project_milestone,
)
from .common import OperationsPermissionRequiredMixin, add_service_errors_to_form
from .project_milestone_context import (
    build_project_milestone_context,
    get_project_for_milestone_partial,
)


PROJECT_MILESTONES_ANCHOR = '#project-milestones'
MILESTONE_ITEM_RESPONSE = 'item_with_progress'
MILESTONE_LIST_RESPONSE = 'list_only'
MILESTONE_LIST_WITH_PROGRESS_RESPONSE = 'list_with_progress'
MILESTONE_RESPONSE_TEMPLATES = {
    MILESTONE_ITEM_RESPONSE: 'web/includes/project_milestone_item_response.html',
    MILESTONE_LIST_RESPONSE: 'web/includes/project_milestone_list_response.html',
    MILESTONE_LIST_WITH_PROGRESS_RESPONSE: (
        'web/includes/project_milestone_list_response.html'
    ),
}


# PRE: project_id identifies the project to show after a milestone action.
# POST: returns the canonical project detail URL with the future milestones anchor.
def _project_milestones_url(project_id):
    return f'{reverse("project_detail", args=[project_id])}{PROJECT_MILESTONES_ANCHOR}'


# PRE: error is a domain ValidationError raised by a POST-only milestone action.
# POST: returns a readable HTTP 403 without swallowing or exposing internal details.
def _milestone_forbidden_response(error):
    message = error.messages[0] if error.messages else _('La operación sobre el hito no está permitida.')
    return HttpResponseForbidden(message)


# PRE: request is the request that reached an existing milestone endpoint.
# POST: returns whether HTMX explicitly requested a component response.
def _is_htmx_request(request):
    return request.headers.get('HX-Request') == 'true'


# PRE: the milestone mutation succeeded and response_contract matches its affected UI fragment.
# POST: returns only that fragment plus required OOB progress and toast, or the anchored redirect.
def _milestone_action_response(
    request,
    *,
    project_id,
    response_contract,
    milestone_id=None,
    message,
    message_level=messages.SUCCESS,
    toast_type='success',
):
    if not _is_htmx_request(request):
        messages.add_message(request, message_level, message)
        return HttpResponseRedirect(_project_milestones_url(project_id))

    if response_contract not in MILESTONE_RESPONSE_TEMPLATES:
        raise AssertionError('La respuesta HTMX requiere un contrato de fragmento válido.')

    project = get_project_for_milestone_partial(project_id)
    context = build_project_milestone_context(project, request.user)
    if response_contract == MILESTONE_ITEM_RESPONSE:
        milestones = context['project_milestones']
        affected_index = next(
            (index for index, item in enumerate(milestones) if item.pk == milestone_id),
            None,
        )
        if affected_index is None:
            raise AssertionError('El hito afectado debe existir para reemplazar su fila.')
        context.update(
            {
                'milestone': milestones[affected_index],
                'milestone_is_first': affected_index == 0,
                'milestone_is_last': affected_index == len(milestones) - 1,
            }
        )
    context['milestone_progress_oob'] = (
        response_contract == MILESTONE_LIST_WITH_PROGRESS_RESPONSE
    )
    response = render(
        request,
        MILESTONE_RESPONSE_TEMPLATES[response_contract],
        context,
    )
    response.headers['HX-Trigger'] = json.dumps(
        {'milestoneToast': {'type': toast_type, 'message': str(message)}}
    )
    return response


class ProjectMilestoneObjectMixin:
    """Loads the milestone only for presentation and routing; services reload it for mutation."""

    def get_milestone(self):
        # PRE: self.kwargs contains the route pk and permission dispatch already authorized access.
        # POST: returns the related project eagerly or raises HTTP 404.
        if not hasattr(self, '_milestone'):
            self._milestone = get_object_or_404(
                ProjectMilestone.objects.select_related('project'),
                pk=self.kwargs['pk'],
            )
        return self._milestone

    def get_success_url(self):
        return _project_milestones_url(self.get_milestone().project_id)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        milestone = self.get_milestone()
        context['milestone'] = milestone
        context['project'] = milestone.project
        return context


class ProjectMilestoneAddView(OperationsPermissionRequiredMixin, FormView):
    permission_required = (
        'operations.add_projectmilestone',
        'operations.view_project',
    )
    form_class = ProjectMilestoneForm
    template_name = 'web/project_milestone_form.html'

    def get_project(self):
        # PRE: self.kwargs contains project_pk and permission dispatch authorized project access.
        # POST: returns the URL-selected project or raises HTTP 404.
        if not hasattr(self, '_project'):
            self._project = get_object_or_404(Project, pk=self.kwargs['project_pk'])
        return self._project

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['project'] = self.get_project()
        context['title'] = _('Crear hito de proyecto')
        context['submit_label'] = _('Crear hito')
        return context

    def form_valid(self, form):
        """
        PRE: form contains only validated title and description fields.
        POST: creates through the domain service or redisplays its validation errors.
        """
        try:
            create_project_milestone(
                project_id=self.get_project().pk,
                title=form.cleaned_data['title'],
                description=form.cleaned_data.get('description', ''),
                actor=self.request.user,
            )
        except ValidationError as error:
            add_service_errors_to_form(form, error)
            return self.form_invalid(form)
        messages.success(self.request, _('Hito creado.'))
        return HttpResponseRedirect(_project_milestones_url(self.get_project().pk))


class ProjectMilestoneEditView(
    OperationsPermissionRequiredMixin,
    ProjectMilestoneObjectMixin,
    FormView,
):
    permission_required = (
        'operations.change_projectmilestone',
        'operations.view_project',
    )
    form_class = ProjectMilestoneForm
    template_name = 'web/project_milestone_form.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['instance'] = self.get_milestone()
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = _('Editar hito de proyecto')
        context['submit_label'] = _('Guardar cambios')
        return context

    def form_valid(self, form):
        """
        PRE: form exposes only descriptive fields for the URL-selected milestone.
        POST: updates through the service, preserving ownership/order/completion, or redisplays errors.
        """
        milestone = self.get_milestone()
        had_changes = bool({'title', 'description'} & set(form.changed_data))
        try:
            update_project_milestone(
                milestone.pk,
                title=form.cleaned_data['title'],
                description=form.cleaned_data.get('description', ''),
                actor=self.request.user,
            )
        except ValidationError as error:
            add_service_errors_to_form(form, error)
            return self.form_invalid(form)
        if had_changes:
            messages.success(self.request, _('Hito actualizado.'))
        else:
            messages.info(self.request, _('No se realizaron cambios en el hito.'))
        return HttpResponseRedirect(self.get_success_url())


class ProjectMilestoneCompleteView(
    OperationsPermissionRequiredMixin,
    ProjectMilestoneObjectMixin,
    View,
):
    permission_required = (
        'operations.complete_projectmilestone',
        'operations.view_project',
    )

    def post(self, request, *args, **kwargs):
        """
        PRE: request is POST, user has both permissions, and pk identifies a milestone.
        POST: delegates completion, reports idempotence honestly, and redirects to its project.
        """
        milestone = self.get_milestone()
        was_completed = milestone.is_completed
        try:
            complete_project_milestone(milestone.pk, actor=request.user)
        except ValidationError as error:
            return _milestone_forbidden_response(error)
        if was_completed:
            return _milestone_action_response(
                request,
                project_id=milestone.project_id,
                response_contract=MILESTONE_ITEM_RESPONSE,
                milestone_id=milestone.pk,
                message=_('El hito ya estaba completado.'),
                message_level=messages.INFO,
                toast_type='info',
            )
        return _milestone_action_response(
            request,
            project_id=milestone.project_id,
            response_contract=MILESTONE_ITEM_RESPONSE,
            milestone_id=milestone.pk,
            message=_('Hito completado.'),
        )


class ProjectMilestoneReopenView(
    OperationsPermissionRequiredMixin,
    ProjectMilestoneObjectMixin,
    FormView,
):
    permission_required = (
        'operations.complete_projectmilestone',
        'operations.view_project',
    )
    form_class = TerminalActionConfirmationForm
    template_name = 'web/project_milestone_reopen_confirm.html'

    def form_valid(self, form):
        """
        PRE: confirmation arrived by POST for the URL-selected milestone.
        POST: delegates reopening or redisplays domain errors without mutating on GET.
        """
        milestone = self.get_milestone()
        was_completed = milestone.is_completed
        try:
            reopen_project_milestone(milestone.pk, actor=self.request.user)
        except ValidationError as error:
            if _is_htmx_request(self.request):
                return _milestone_forbidden_response(error)
            add_service_errors_to_form(form, error)
            return self.form_invalid(form)
        if was_completed:
            return _milestone_action_response(
                self.request,
                project_id=milestone.project_id,
                response_contract=MILESTONE_ITEM_RESPONSE,
                milestone_id=milestone.pk,
                message=_('Hito reabierto.'),
            )
        return _milestone_action_response(
            self.request,
            project_id=milestone.project_id,
            response_contract=MILESTONE_ITEM_RESPONSE,
            milestone_id=milestone.pk,
            message=_('El hito ya estaba pendiente.'),
            message_level=messages.INFO,
            toast_type='info',
        )


class ProjectMilestoneDeleteView(
    OperationsPermissionRequiredMixin,
    ProjectMilestoneObjectMixin,
    FormView,
):
    permission_required = (
        'operations.delete_projectmilestone',
        'operations.view_project',
    )
    form_class = TerminalActionConfirmationForm
    template_name = 'web/project_milestone_confirm_delete.html'

    def form_valid(self, form):
        """
        PRE: confirmation arrived by POST and the URL-selected milestone still exists.
        POST: deletes only through the service or redisplays a readable domain error.
        """
        milestone = self.get_milestone()
        project_id = milestone.project_id
        try:
            delete_project_milestone(milestone.pk, actor=self.request.user)
        except ValidationError as error:
            if _is_htmx_request(self.request):
                return _milestone_forbidden_response(error)
            add_service_errors_to_form(form, error)
            return self.form_invalid(form)
        return _milestone_action_response(
            self.request,
            project_id=project_id,
            response_contract=MILESTONE_LIST_WITH_PROGRESS_RESPONSE,
            message=_('Hito eliminado.'),
        )


class ProjectMilestoneMoveView(
    OperationsPermissionRequiredMixin,
    ProjectMilestoneObjectMixin,
    View,
):
    permission_required = (
        'operations.reorder_projectmilestone',
        'operations.view_project',
    )
    move_service = None

    def post(self, request, *args, **kwargs):
        """
        PRE: request is POST, pk identifies a milestone, and move_service is configured.
        POST: delegates one adjacent move and reports a boundary no-op without false success.
        """
        if self.move_service is None:
            raise AssertionError('La vista de movimiento requiere un servicio explícito.')
        milestone = self.get_milestone()
        previous_position = milestone.position
        try:
            moved = self.move_service(milestone.pk, actor=request.user)
        except ValidationError as error:
            return _milestone_forbidden_response(error)
        if moved.position == previous_position:
            return _milestone_action_response(
                request,
                project_id=milestone.project_id,
                response_contract=MILESTONE_LIST_RESPONSE,
                message=_('El hito ya se encuentra en el límite del orden.'),
                message_level=messages.INFO,
                toast_type='info',
            )
        return _milestone_action_response(
            request,
            project_id=milestone.project_id,
            response_contract=MILESTONE_LIST_RESPONSE,
            message=_('Orden de hitos actualizado.'),
        )


class ProjectMilestoneMoveUpView(ProjectMilestoneMoveView):
    move_service = staticmethod(move_project_milestone_up)


class ProjectMilestoneMoveDownView(ProjectMilestoneMoveView):
    move_service = staticmethod(move_project_milestone_down)
