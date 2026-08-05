from django.contrib import messages

from django.core.exceptions import (
    PermissionDenied,
    ValidationError,
)

from django.shortcuts import get_object_or_404

from django.http import HttpResponseRedirect

from django.urls import (
    reverse,
    reverse_lazy,
)

from django.utils.translation import gettext_lazy as _

from django.views import View

from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    FormView,
    ListView,
    UpdateView,
)

from ..forms import (
    ProjectUpdateForProjectForm,
    ProjectUpdateAttachmentForm,
    ProjectUpdateForm,
    ProjectUpdateReviewForm,
    ProjectUpdateReviewDecisionForm,
    ProjectUpdateRemediationAttachmentForm,
    ProjectUpdateRemediationForm,
    ProjectUpdateRemediationResolveForm,
)

from ..models import (
    Project,
    ProjectUpdate,
    ProjectUpdateAttachment,
    ProjectUpdateReview,
    ProjectUpdateReviewDecision,
    ProjectUpdateRemediation,
    ProjectUpdateRemediationAttachment,
)

from ..selectors import (
    decidable_project_update_reviews_for_user,
    resolvable_project_update_remediations_for_user,
    reviewable_project_updates_for_user,
    with_project_update_attachment_count,
)

from ..services import (
    create_project_update_review,
    create_project_update_review_decision,
    create_project_update_remediation,
    update_project_update_remediation,
    add_project_update_remediation_attachment,
    delete_project_update_remediation_attachment,
    submit_project_update_remediation,
    resolve_project_update_remediation,
    ensure_project_allows_operational_mutation,
    ensure_project_update_is_deletable,
    ensure_project_update_is_editable,
    OperationalEntityFinalizedError,
    ProjectUpdateImmutableError,
    ProjectUpdateReviewError,
    ProjectUpdateReviewDecisionError,
    ProjectUpdateRemediationError,
    _create_project_update_attachments,
    project_allows_operational_mutation,
    register_advance,
    publish_project_update,
    delete_project_update_attachment,
    update_project_update,
)

from ..file_access import build_protected_file_actions

from .common import (
    DeleteAuditMixin,
    OperationsPermissionRequiredMixin,
    PaginatedListMixin,
    RouteContextMixin,
    add_service_errors_to_form,
)


class ProjectUpdateListView(
    OperationsPermissionRequiredMixin,
    RouteContextMixin,
    PaginatedListMixin,
    ListView,
):
    permission_required = 'operations.view_projectupdate'
    model = ProjectUpdate
    template_name = 'web/project_update_list.html'
    context_object_name = 'objects'
    route_prefix = 'project_update'
    page_title = _('Avances de proyecto')

    def get_queryset(self):
        return with_project_update_attachment_count(
            ProjectUpdate.objects.select_related('project', 'created_by', 'reported_by')
        ).order_by('-created_at', '-pk')

class ProjectUpdateDetailView(OperationsPermissionRequiredMixin, RouteContextMixin, DetailView):
    permission_required = 'operations.view_projectupdate'
    model = ProjectUpdate
    template_name = 'web/project_update_detail.html'
    route_prefix = 'project_update'
    page_title = _('Avance de proyecto')

    def get_queryset(self):
        """
        PRE: the request has view_projectupdate permission and targets one advance.
        POST: returns the advance with every relation rendered by its detail preloaded.
        """
        return ProjectUpdate.objects.select_related(
            'project',
            'created_by',
            'reported_by',
            'committee_review__reviewed_by',
            'committee_review__decision__remediation',
        ).prefetch_related('attachments')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        mutations_allowed = project_allows_operational_mutation(self.object.project)
        context['project_allows_operational_mutation'] = mutations_allowed
        can_download = (
            user.has_perm('operations.view_project')
            and user.has_perm('operations.view_projectupdateattachment')
        )
        can_delete = (
            mutations_allowed
            and self.object.status == ProjectUpdate.Status.UNPUBLISHED
            and user.has_perm('operations.delete_projectupdateattachment')
        )
        attachments = list(self.object.attachments.all())
        for attachment in attachments:
            attachment.file_actions = build_protected_file_actions(
                file_field=attachment.file,
                file_label=attachment.title or str(attachment),
                uploaded_at=attachment.created_at,
                can_download=can_download,
                preview_url_name='project_update_attachment_preview',
                download_url_name='project_update_attachment_download',
                url_args=(self.object.project_id, self.object.pk, attachment.pk),
                delete_url=reverse('project_update_attachment_delete', args=[attachment.pk])
                if can_delete
                else None,
                can_delete=can_delete,
            )
        context['detail_attachments'] = attachments
        return context


class ProjectUpdateReviewCreateView(OperationsPermissionRequiredMixin, FormView):
    permission_required = 'operations.review_projectupdate'
    form_class = ProjectUpdateReviewForm
    template_name = 'web/project_update_review_form.html'

    def dispatch(self, request, *args, **kwargs):
        # PRE: review route targets a PUBLISHED advance without a committee review.
        # POST: loads from reviewable_project_updates_for_user or 404; no mutation on GET.
        # Intentional: stale/ineligible rows 404 (same as expense-request action routes)
        # instead of 403 after an unscoped get_object_or_404.
        if request.user.is_authenticated and request.user.has_perm(self.permission_required):
            self.project_update = get_object_or_404(
                reviewable_project_updates_for_user(request.user).select_related(
                    'project',
                    'reported_by',
                ),
                pk=kwargs['update_pk'],
            )
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['project_update'] = self.project_update
        return context

    def form_valid(self, form):
        """
        PRE: form contains validated observations for a published advance without a review.
        POST: creates the review through the domain service and redirects to its detail.
        """
        try:
            review = create_project_update_review(
                update_id=self.project_update.pk,
                observations=form.cleaned_data['observations'],
                actor=self.request.user,
            )
        except ProjectUpdateReviewError as exc:
            raise PermissionDenied(exc.messages[0]) from exc
        messages.success(self.request, _('Revisión documental del Comité registrada.'))
        return HttpResponseRedirect(reverse('project_update_review_detail', args=[review.pk]))


class ProjectUpdateReviewDetailView(OperationsPermissionRequiredMixin, DetailView):
    permission_required = 'operations.view_projectupdatereview'
    model = ProjectUpdateReview
    template_name = 'web/project_update_review_detail.html'

    def get_queryset(self):
        return ProjectUpdateReview.objects.select_related(
            'project_update__project', 'reviewed_by', 'decision__decided_by'
        )


class ProjectUpdateReviewDecisionCreateView(OperationsPermissionRequiredMixin, FormView):
    permission_required = 'operations.decide_projectupdate'
    form_class = ProjectUpdateReviewDecisionForm
    template_name = 'web/project_update_review_decision_form.html'

    def dispatch(self, request, *args, **kwargs):
        # PRE: decision route targets a review of a PUBLISHED advance without a decision.
        # POST: loads from decidable_project_update_reviews_for_user or 404; no mutation on GET.
        # Intentional: stale/ineligible rows 404 (same as expense-request action routes).
        if request.user.is_authenticated and request.user.has_perm(self.permission_required):
            self.review = get_object_or_404(
                decidable_project_update_reviews_for_user(request.user).select_related(
                    'project_update__project',
                    'reviewed_by',
                ),
                pk=kwargs['review_pk'],
            )
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['review'] = self.review
        return context

    def form_valid(self, form):
        """
        PRE: form contains a valid outcome and rationale for a review without a decision.
        POST: creates the decision through the domain service and redirects to the review detail.
        """
        try:
            create_project_update_review_decision(
                review_id=self.review.pk,
                outcome=form.cleaned_data['outcome'],
                rationale=form.cleaned_data['rationale'],
                actor=self.request.user,
            )
        except ProjectUpdateReviewDecisionError as exc:
            raise PermissionDenied(exc.messages[0]) from exc
        messages.success(self.request, _('Resultado de revisión del Comité registrado.'))
        return HttpResponseRedirect(reverse('project_update_review_detail', args=[self.review.pk]))


class ProjectUpdateReviewDecisionDetailView(OperationsPermissionRequiredMixin, DetailView):
    permission_required = 'operations.view_projectupdatereviewdecision'
    model = ProjectUpdateReviewDecision
    template_name = 'web/project_update_review_decision_detail.html'

    def get_queryset(self):
        return ProjectUpdateReviewDecision.objects.select_related(
            'review__project_update__project', 'decided_by'
        )


class ProjectUpdateRemediationCreateView(OperationsPermissionRequiredMixin, FormView):
    permission_required = 'operations.submit_projectupdateremediation'
    form_class = ProjectUpdateRemediationForm
    template_name = 'web/project_update_remediation_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.decision = get_object_or_404(ProjectUpdateReviewDecision, pk=kwargs['decision_pk'])
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        try:
            remediation = create_project_update_remediation(
                decision_id=self.decision.pk, response=form.cleaned_data['response'], actor=self.request.user
            )
        except ProjectUpdateRemediationError as exc:
            raise PermissionDenied(exc.messages[0]) from exc
        return HttpResponseRedirect(reverse('project_update_remediation_detail', args=[remediation.pk]))


class ProjectUpdateRemediationDetailView(OperationsPermissionRequiredMixin, DetailView):
    permission_required = 'operations.view_projectupdateremediation'
    model = ProjectUpdateRemediation
    template_name = 'web/project_update_remediation_detail.html'

    def get_queryset(self):
        return ProjectUpdateRemediation.objects.select_related(
            'decision__review__project_update__project',
            'created_by',
            'submitted_by',
            'resolved_by',
        ).prefetch_related('attachments')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        mutations_allowed = project_allows_operational_mutation(
            self.object.decision.review.project_update.project
        )
        context['project_allows_operational_mutation'] = mutations_allowed
        can_download = user.has_perm('operations.view_projectupdateremediationattachment')
        can_delete = (
            mutations_allowed
            and self.object.status == ProjectUpdateRemediation.Status.DRAFT
            and user.has_perm('operations.delete_projectupdateremediationattachment')
        )
        attachments = list(self.object.attachments.all())
        for attachment in attachments:
            attachment.file_actions = build_protected_file_actions(
                file_field=attachment.file,
                file_label=attachment.title or str(attachment),
                uploaded_at=attachment.created_at,
                can_download=can_download,
                preview_url_name='project_update_remediation_attachment_preview',
                download_url_name='project_update_remediation_attachment_download',
                url_args=(self.object.pk, attachment.pk),
                delete_url=reverse(
                    'project_update_remediation_attachment_delete',
                    args=[attachment.pk],
                )
                if can_delete
                else None,
                can_delete=can_delete,
            )
        context['detail_attachments'] = attachments
        return context


class ProjectUpdateRemediationUpdateView(OperationsPermissionRequiredMixin, FormView):
    permission_required = 'operations.change_projectupdateremediation'
    form_class = ProjectUpdateRemediationForm
    template_name = 'web/project_update_remediation_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.remediation = get_object_or_404(ProjectUpdateRemediation, pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        return {'response': self.remediation.response}

    def form_valid(self, form):
        try:
            update_project_update_remediation(remediation_id=self.remediation.pk, response=form.cleaned_data['response'], actor=self.request.user)
        except ProjectUpdateRemediationError as exc:
            raise PermissionDenied(exc.messages[0]) from exc
        return HttpResponseRedirect(reverse('project_update_remediation_detail', args=[self.remediation.pk]))


class ProjectUpdateRemediationSubmitView(OperationsPermissionRequiredMixin, View):
    permission_required = 'operations.submit_projectupdateremediation'

    def post(self, request, *args, **kwargs):
        try:
            remediation = submit_project_update_remediation(remediation_id=kwargs['pk'], actor=request.user)
        except ProjectUpdateRemediationError as exc:
            raise PermissionDenied(exc.messages[0]) from exc
        return HttpResponseRedirect(reverse('project_update_remediation_detail', args=[remediation.pk]))


class ProjectUpdateRemediationResolveView(OperationsPermissionRequiredMixin, FormView):
    permission_required = 'operations.resolve_projectupdateremediation'
    form_class = ProjectUpdateRemediationResolveForm
    template_name = 'web/project_update_remediation_resolve_form.html'

    def dispatch(self, request, *args, **kwargs):
        # PRE: resolve route targets a SUBMITTED remediation under a PUBLISHED update.
        # POST: loads from resolvable_project_update_remediations_for_user or 404.
        # Intentional: DRAFT/terminal remediations 404 before the form (service still
        # remains authoritative on POST race conditions).
        if request.user.is_authenticated and request.user.has_perm(self.permission_required):
            self.remediation = get_object_or_404(
                resolvable_project_update_remediations_for_user(request.user).select_related(
                    'decision__review__project_update__project',
                    'submitted_by',
                ),
                pk=kwargs['pk'],
            )
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        try:
            resolve_project_update_remediation(remediation_id=self.remediation.pk, actor=self.request.user, **form.cleaned_data)
        except ProjectUpdateRemediationError as exc:
            raise PermissionDenied(exc.messages[0]) from exc
        return HttpResponseRedirect(reverse('project_update_remediation_detail', args=[self.remediation.pk]))


class ProjectUpdateRemediationAttachmentCreateView(OperationsPermissionRequiredMixin, FormView):
    permission_required = 'operations.add_projectupdateremediationattachment'
    form_class = ProjectUpdateRemediationAttachmentForm
    template_name = 'web/project_update_remediation_attachment_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.remediation = get_object_or_404(ProjectUpdateRemediation, pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        try:
            add_project_update_remediation_attachment(remediation_id=self.remediation.pk, actor=self.request.user, **form.cleaned_data)
        except ProjectUpdateRemediationError as exc:
            raise PermissionDenied(exc.messages[0]) from exc
        return HttpResponseRedirect(reverse('project_update_remediation_detail', args=[self.remediation.pk]))


class ProjectUpdateRemediationAttachmentDeleteView(OperationsPermissionRequiredMixin, View):
    permission_required = 'operations.delete_projectupdateremediationattachment'

    def post(self, request, *args, **kwargs):
        try:
            remediation_id = delete_project_update_remediation_attachment(attachment_id=kwargs['pk'], actor=request.user)
        except ProjectUpdateRemediationError as exc:
            raise PermissionDenied(exc.messages[0]) from exc
        return HttpResponseRedirect(reverse('project_update_remediation_detail', args=[remediation_id]))


class ProjectUpdateCreateView(OperationsPermissionRequiredMixin, RouteContextMixin, CreateView):
    permission_required = 'operations.add_projectupdate'
    model = ProjectUpdate
    form_class = ProjectUpdateForm
    template_name = 'web/object_form.html'
    success_url = reverse_lazy('project_update_list')
    route_prefix = 'project_update'
    page_title = _('Nuevo avance de proyecto')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        self.object = register_advance(
            project_id=form.cleaned_data['project'].pk,
            title=form.cleaned_data['title'],
            description=form.cleaned_data['description'],
            update_date=form.cleaned_data['update_date'],
            attachments=form.cleaned_data.get('attachments', ()),
            created_by=self.request.user if self.request.user.is_authenticated else None,
            reported_by=form.cleaned_data['reported_by'],
        )
        messages.success(self.request, _('Avance de proyecto registrado.'))
        return HttpResponseRedirect(self.get_success_url())


class ProjectUpdateCreateForProjectView(OperationsPermissionRequiredMixin, RouteContextMixin, CreateView):
    permission_required = 'operations.add_projectupdate'
    model = ProjectUpdate
    form_class = ProjectUpdateForProjectForm
    template_name = 'web/project_update_form.html'
    route_prefix = 'project_update'
    page_title = _('Registrar avance')

    def dispatch(self, request, *args, **kwargs):
        # PRE: route targets a project and permission handling remains authoritative.
        # POST: CLOSED projects return 403 before form binding or advance creation.
        self.project = get_object_or_404(Project, pk=kwargs['project_pk'])
        if request.user.is_authenticated and request.user.has_perm(self.permission_required):
            try:
                ensure_project_allows_operational_mutation(self.project)
            except OperationalEntityFinalizedError as exc:
                raise PermissionDenied(exc.messages[0]) from exc
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['project'] = self.project
        context['cancel_url'] = reverse('project_detail', args=[self.project.pk])
        return context

    def form_valid(self, form):
        try:
            self.object = register_advance(
                project_id=self.project.pk,
                title=form.cleaned_data['title'],
                description=form.cleaned_data['description'],
                update_date=form.cleaned_data['update_date'],
                attachments=form.cleaned_data.get('attachments', ()),
                created_by=self.request.user if self.request.user.is_authenticated else None,
                reported_by=form.cleaned_data['reported_by'],
            )
        except ValidationError as error:
            add_service_errors_to_form(form, error)
            return self.form_invalid(form)
        messages.success(self.request, _('Avance de proyecto registrado.'))
        return HttpResponseRedirect(self.get_success_url())

    def get_success_url(self):
        return reverse('project_detail', args=[self.project.pk])


class ProjectUpdateUpdateView(OperationsPermissionRequiredMixin, RouteContextMixin, UpdateView):
    permission_required = 'operations.change_projectupdate'
    model = ProjectUpdate
    form_class = ProjectUpdateForm
    template_name = 'web/object_form.html'
    success_url = reverse_lazy('project_update_list')
    route_prefix = 'project_update'
    page_title = _('Editar avance de proyecto')

    def dispatch(self, request, *args, **kwargs):
        # PRE: request targets ordinary editing and permission handling remains authoritative.
        # POST: permits UNPUBLISHED advances only; published advances return 403.
        if request.user.is_authenticated and request.user.has_perm(self.permission_required):
            project_update = get_object_or_404(ProjectUpdate, pk=kwargs['pk'])
            try:
                ensure_project_update_is_editable(project_update)
            except ProjectUpdateImmutableError as exc:
                raise PermissionDenied(exc.messages[0]) from exc
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        """
        PRE: form is valid and the route targets an UNPUBLISHED advance.
        POST: updates through the locked domain service or redisplays domain errors.
        """
        try:
            self.object = update_project_update(
                update_id=self.object.pk,
                project=form.cleaned_data['project'],
                title=form.cleaned_data['title'],
                description=form.cleaned_data['description'],
                update_date=form.cleaned_data['update_date'],
                reported_by=form.cleaned_data['reported_by'],
                actor=self.request.user,
                attachments=form.cleaned_data.get('attachments', ()),
            )
        except ValidationError as error:
            add_service_errors_to_form(form, error)
            return self.form_invalid(form)
        messages.success(self.request, _('Avance de proyecto actualizado.'))
        return HttpResponseRedirect(self.get_success_url())


class ProjectUpdateDeleteView(OperationsPermissionRequiredMixin, DeleteAuditMixin, RouteContextMixin, DeleteView):
    permission_required = 'operations.delete_projectupdate'
    model = ProjectUpdate
    template_name = 'web/object_confirm_delete.html'
    success_url = reverse_lazy('project_update_list')
    route_prefix = 'project_update'
    page_title = _('Eliminar avance de proyecto')
    audit_summary = _('Avance de proyecto eliminado.')

    def dispatch(self, request, *args, **kwargs):
        # PRE: request targets ordinary deletion and permission handling remains authoritative.
        # POST: blocks final advances on GET and POST without deleting or auditing them.
        if request.user.is_authenticated and request.user.has_perm(self.permission_required):
            project_update = get_object_or_404(ProjectUpdate, pk=kwargs['pk'])
            try:
                ensure_project_update_is_deletable(project_update)
            except ProjectUpdateImmutableError as exc:
                raise PermissionDenied(exc.messages[0]) from exc
        return super().dispatch(request, *args, **kwargs)


class ProjectUpdatePublishView(OperationsPermissionRequiredMixin, View):
    permission_required = 'operations.publish_projectupdate'

    def post(self, request, *args, **kwargs):
        """
        PRE: el usuario tiene permiso funcional de publicación y pk identifica un avance.
        POST: publica mediante el servicio de dominio o responde 403 sin mutar.
        """
        try:
            project_update = publish_project_update(kwargs['pk'], request.user)
        except ValidationError as exc:
            raise PermissionDenied(exc.messages[0]) from exc
        messages.success(request, _('Avance de proyecto publicado.'))
        return HttpResponseRedirect(reverse('project_update_detail', args=[project_update.pk]))


class ProjectUpdateAttachmentCreateView(OperationsPermissionRequiredMixin, FormView):
    permission_required = 'operations.add_projectupdateattachment'
    form_class = ProjectUpdateAttachmentForm
    template_name = 'web/project_update_attachment_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.project_update = get_object_or_404(ProjectUpdate, pk=kwargs['update_pk'])
        if request.user.is_authenticated and request.user.has_perm(self.permission_required):
            try:
                ensure_project_update_is_editable(self.project_update)
            except ProjectUpdateImmutableError as exc:
                raise PermissionDenied(exc.messages[0]) from exc
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        # PRE: form.cleaned_data['files'] is a validated non-empty upload list; parent update is UNPUBLISHED.
        # POST: persists one attachment per file via the domain helper, then redirects once to detail.
        try:
            _create_project_update_attachments(
                self.project_update,
                form.cleaned_data['files'],
                self.request.user,
            )
        except ValidationError as exc:
            add_service_errors_to_form(form, exc)
            return self.form_invalid(form)
        return HttpResponseRedirect(reverse('project_update_detail', args=[self.project_update.pk]))


class ProjectUpdateAttachmentDeleteView(OperationsPermissionRequiredMixin, View):
    permission_required = 'operations.delete_projectupdateattachment'

    def post(self, request, *args, **kwargs):
        # PRE: el usuario tiene permiso y pk identifica un adjunto.
        # POST: elimina mediante el servicio solo si el avance padre es UNPUBLISHED.
        try:
            update_id = delete_project_update_attachment(
                attachment_id=kwargs['pk'], actor=request.user
            )
        except ValidationError as exc:
            raise PermissionDenied(exc.messages[0]) from exc
        return HttpResponseRedirect(reverse('project_update_detail', args=[update_id]))
