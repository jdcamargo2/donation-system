from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_countries.fields import CountryField

from .choices import (
    BUDGET_CATEGORY_CHOICES,
    CURRENCY_CHOICES,
    DONATION_TYPE_CHOICES,
    EXPENSE_CATEGORY_CHOICES,
    INSTITUTION_TYPE_CHOICES,
    OPERATING_CURRENCY,
    PAYMENT_METHOD_CHOICES,
)


ZERO_MONEY = Decimal('0.00')
OPERATIONAL_CODE_WIDTH = 6
OPERATIONAL_CODE_PREFIXES = {
    'project': 'PRJ',
    'donation': 'DON',
    'fund_allocation': 'ASG',
    'expense': 'GAS',
    'expense_request': 'SGS',
}


class OperationalCodeSequence(models.Model):
    namespace = models.CharField(max_length=32, primary_key=True)
    prefix = models.CharField(max_length=3, unique=True)
    next_value = models.PositiveBigIntegerField(default=1)

    class Meta:
        verbose_name = _('secuencia de código operativo')
        verbose_name_plural = _('secuencias de códigos operativos')

    def __str__(self):
        return f'{self.prefix}: {self.next_value}'


# PRE: namespace is supported, prefix matches it, and the caller has opened transaction.atomic().
# POST: locks one sequence row, advances it once, and returns the reserved six-digit code.
def reserve_operational_code(*, namespace, prefix):
    expected_prefix = OPERATIONAL_CODE_PREFIXES.get(namespace)
    if expected_prefix is None:
        raise ValidationError({'namespace': _('El namespace de código operativo no está soportado.')})
    if prefix != expected_prefix:
        raise ValidationError({'prefix': _('El prefijo no corresponde al namespace operativo.')})
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('La reserva de códigos operativos exige transaction.atomic().')

    try:
        sequence = OperationalCodeSequence.objects.select_for_update().get(namespace=namespace)
    except OperationalCodeSequence.DoesNotExist as exc:
        raise RuntimeError(f'Falta inicializar la secuencia operativa {namespace!r}.') from exc
    if sequence.prefix != prefix or sequence.next_value < 1:
        raise RuntimeError(f'La secuencia operativa {namespace!r} es inconsistente.')

    reserved_value = sequence.next_value
    sequence.next_value = reserved_value + 1
    sequence.save(update_fields=('next_value',))
    return f'{prefix}-{reserved_value:0{OPERATIONAL_CODE_WIDTH}d}'


# PRE: instance is persisted and proposed_code is the code submitted for its update.
# POST: raises ValidationError if the persisted human identifier would change.
def ensure_operational_code_is_immutable(instance, proposed_code):
    if not instance.pk:
        return
    persisted_code = type(instance).objects.only('code').get(pk=instance.pk).code
    if proposed_code != persisted_code:
        raise ValidationError({'code': _('El código operativo no puede modificarse.')})


class Institution(models.Model):
    class Role(models.TextChoices):
        DONOR = 'donor', _('Donante')
        RECEIVER = 'receiver', _('Receptora')
        EXECUTOR = 'executor', _('Ejecutora')
        ALLY = 'ally', _('Aliada')
        SUPERVISOR = 'supervisor', _('Supervisora')

    class Status(models.TextChoices):
        ACTIVE = 'active', _('Activo')
        INACTIVE = 'inactive', _('Inactivo')

    name = models.CharField(max_length=180)
    institution_type = models.CharField(max_length=40, choices=INSTITUTION_TYPE_CHOICES)
    role = models.CharField(max_length=20, choices=Role.choices)
    country = CountryField(verbose_name=_('País'), default='VE')
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=60, blank=True)
    responsible_person = models.CharField(max_length=120, blank=True)
    legal_document = models.FileField(upload_to='institution_documents/%Y/%m/', blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = _('institución')
        verbose_name_plural = _('instituciones')

    def __str__(self):
        return self.name


class ProjectDeletionForbiddenError(ValidationError):
    """Raised when application code attempts to delete a project."""


class ProjectQuerySet(models.QuerySet):
    """Query operations that permanently reject Project deletion."""

    def delete(self):
        """
        PRE: queryset targets persisted projects.
        POST: always rejects bulk deletion without changing project rows.
        """
        raise ProjectDeletionForbiddenError(_('Los proyectos no pueden eliminarse.'))


class Project(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'active', _('Activo')
        CLOSED = 'closed', _('Cerrado')

    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=180)
    description = models.TextField(blank=True)
    objective = models.TextField(blank=True)
    location = models.CharField(max_length=160, blank=True)
    estimated_budget = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO_MONEY)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    is_public = models.BooleanField(default=False)
    terminal_reason = models.TextField(blank=True, editable=False)
    terminal_at = models.DateTimeField(null=True, blank=True, editable=False)
    terminal_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name='terminal_projects',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    objects = ProjectQuerySet.as_manager()

    class Meta:
        ordering = ['code']
        verbose_name = _('proyecto')
        verbose_name_plural = _('proyectos')
        permissions = [
            (
                'manage_project_publication',
                _('Puede publicar y retirar proyectos del portal público'),
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(estimated_budget__gte=ZERO_MONEY),
                name='operations_project_budget_gte_zero',
            ),
        ]

    def __str__(self):
        return f'{self.code} - {self.name}'

    def save(self, *args, **kwargs):
        # PRE: explicit codes are supplied only by trusted fixtures, migrations, or seed data.
        # POST: creates with one reserved PRJ code or preserves the existing code on update.
        ensure_operational_code_is_immutable(self, self.code)
        if self.code:
            return super().save(*args, **kwargs)
        with transaction.atomic():
            self.code = reserve_operational_code(namespace='project', prefix='PRJ')
            return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """
        PRE: self is a project targeted for instance deletion.
        POST: always rejects deletion and preserves the project.
        """
        raise ProjectDeletionForbiddenError(_('Los proyectos no pueden eliminarse.'))

    def clean(self):
        errors = {}
        if self.estimated_budget is not None and self.estimated_budget < ZERO_MONEY:
            errors['estimated_budget'] = _('El presupuesto estimado no puede ser negativo.')
        if self.start_date and self.end_date and self.end_date < self.start_date:
            errors['end_date'] = _('La fecha de cierre no puede ser anterior a la fecha de inicio.')
        if errors:
            raise ValidationError(errors)

    @property
    def funded_amount(self):
        return self.operating_allocations().aggregate(
            total=Sum('amount')
        )['total'] or ZERO_MONEY

    @property
    def executed_amount(self):
        return self.operating_expenses().aggregate(total=Sum('amount'))['total'] or ZERO_MONEY

    def operating_allocations(self):
        """
        PRE: self is a persisted Project with related allocations and donations.
        POST: returns only non-annulled allocations funded by the operating currency.
        """
        return self.allocations.filter(
            donation__currency=OPERATING_CURRENCY,
        ).exclude(status=FundAllocation.Status.ANNULLED)

    def operating_expenses(self):
        """
        PRE: self is a persisted Project with related allocations, donations, and expenses.
        POST: returns only effective expenses whose own and funding currencies are operating currency.
        """
        return Expense.objects.filter(
            allocation__project=self,
            currency=OPERATING_CURRENCY,
            allocation__donation__currency=OPERATING_CURRENCY,
        ).exclude(
            allocation__status=FundAllocation.Status.ANNULLED,
        ).exclude(status__in=Expense.non_executing_statuses())


class ProjectMilestone(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='milestones')
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    position = models.PositiveIntegerField()
    is_completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='completed_project_milestones',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_project_milestones',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('position', 'pk')
        verbose_name = _('hito de proyecto')
        verbose_name_plural = _('hitos de proyecto')
        permissions = (
            (
                'complete_projectmilestone',
                _('Puede completar o reabrir hitos de proyecto'),
            ),
            ('reorder_projectmilestone', _('Puede reordenar hitos de proyecto')),
        )
        constraints = (
            models.UniqueConstraint(
                fields=('project', 'position'),
                name='unique_project_milestone_position',
            ),
            models.CheckConstraint(
                condition=models.Q(position__gte=1),
                name='project_milestone_position_gte_1',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        is_completed=False,
                        completed_at__isnull=True,
                        completed_by__isnull=True,
                    )
                    | models.Q(is_completed=True, completed_at__isnull=False)
                ),
                name='project_milestone_completion_consistency',
            ),
        )

    def __str__(self):
        return f'{self.project.code} - {self.title}'

    def clean(self):
        """
        PRE: milestone fields represent a proposed pending or completed state.
        POST: rejects invalid titles, positions, completion metadata, and actor-less new completions.
        """
        errors = {}
        if not self.title or not self.title.strip():
            errors['title'] = _('El título del hito no puede estar vacío.')
        if self.position is not None and self.position < 1:
            errors['position'] = _('La posición del hito debe comenzar en 1.')

        if self.is_completed:
            if self.completed_at is None:
                errors['completed_at'] = _('Un hito completado debe conservar su fecha de completitud.')
            if self.completed_by_id is None and not self._has_historically_removed_completer():
                errors['completed_by'] = _('Se requiere un usuario para completar el hito.')
        else:
            if self.completed_at is not None:
                errors['completed_at'] = _('Un hito pendiente no puede tener fecha de completitud.')
            if self.completed_by_id is not None:
                errors['completed_by'] = _('Un hito pendiente no puede tener usuario de completitud.')

        if errors:
            raise ValidationError(errors)

    def _has_historically_removed_completer(self):
        """
        PRE: self is proposed as completed without a current completed_by actor.
        POST: returns whether the persisted row already represents that valid historical state.
        """
        if not self.pk:
            return False
        return type(self).objects.filter(
            pk=self.pk,
            is_completed=True,
            completed_at__isnull=False,
            completed_by__isnull=True,
        ).exists()


class ProjectUpdate(models.Model):
    class Status(models.TextChoices):
        UNPUBLISHED = 'unpublished', _('No publicado')
        PUBLISHED = 'published', _('Publicado')

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='updates')
    title = models.CharField(max_length=200)
    description = models.TextField()
    update_date = models.DateField(_('fecha del avance'), default=timezone.localdate)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.UNPUBLISHED)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='created_project_updates',
    )
    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='reported_project_updates',
        verbose_name=_('Responsable institucional'),
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = _('avance de proyecto')
        verbose_name_plural = _('avances de proyecto')
        permissions = [
            ('publish_projectupdate', _('Puede publicar avances de proyecto')),
        ]

    def __str__(self):
        project_label = self.project.code if getattr(self.project, 'code', '') else str(self.project)
        return f'{project_label} - {self.title}'

    def clean(self):
        errors = {}
        if not (self.title or '').strip():
            errors['title'] = _('El título del avance no puede estar vacío.')
        if not (self.description or '').strip():
            errors['description'] = _('La descripción del avance no puede estar vacía.')
        if self.status not in self.Status.values:
            errors['status'] = _('El estado del avance no es válido.')
        if (
            self.project_id
            and hasattr(self.project, 'status')
            and self.project.status != Project.Status.ACTIVE
        ):
            errors['project'] = _('Solo los proyectos activos admiten avances.')
        if errors:
            raise ValidationError(errors)


class ProjectUpdateImmutableError(ValidationError):
    """Raised when ordinary mutation targets a published project update or attachment."""


class ProjectUpdateAttachmentQuerySet(models.QuerySet):
    # PRE: queryset targets persisted project-update attachments.
    # POST: returns only when no selected attachment belongs to a PUBLISHED update.
    def _ensure_no_published_attachments(self):
        if self.filter(project_update__status=ProjectUpdate.Status.PUBLISHED).exists():
            raise ProjectUpdateImmutableError(
                _('Los adjuntos de avances publicados no se pueden modificar ni eliminar.')
            )

    # PRE: queryset targets attachments and kwargs do not move them to a PUBLISHED update.
    # POST: updates only attachments belonging to UNPUBLISHED updates; published attachments remain unchanged.
    def update(self, **kwargs):
        self._ensure_no_published_attachments()
        target_project = kwargs.get('project_update', kwargs.get('project_update_id'))
        target_project_id = getattr(target_project, 'pk', target_project)
        if (
            target_project_id is not None
            and ProjectUpdate.objects.filter(
                pk=target_project_id,
                status=ProjectUpdate.Status.PUBLISHED,
            ).exists()
        ):
            raise ProjectUpdateImmutableError(
                _('No se pueden asociar adjuntos a avances publicados.')
            )
        return super().update(**kwargs)

    # PRE: queryset targets persisted project-update attachments.
    # POST: deletes only attachments belonging to UNPUBLISHED updates; published attachments remain unchanged.
    def delete(self):
        self._ensure_no_published_attachments()
        return super().delete()


class ProjectUpdateReview(models.Model):
    project_update = models.OneToOneField(
        ProjectUpdate,
        on_delete=models.PROTECT,
        related_name='committee_review',
        verbose_name=_('Avance de proyecto'),
    )
    observations = models.TextField(_('Observaciones del Comité'))
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        editable=False,
        related_name='project_update_reviews',
        verbose_name=_('Revisado por'),
    )
    reviewed_at = models.DateTimeField(_('Fecha de revisión'), auto_now_add=True, editable=False)

    class Meta:
        ordering = ['-reviewed_at']
        verbose_name = _('revisión documental de avance')
        verbose_name_plural = _('revisiones documentales de avances')
        permissions = [
            ('review_projectupdate', _('Puede registrar revisiones de avances')),
        ]

    def __str__(self):
        return f'Revisión de {self.project_update}'

    def clean(self):
        errors = {}
        if self.observations and not self.observations.strip():
            errors['observations'] = _('Las observaciones del Comité son obligatorias.')
        if self.project_update_id and self.project_update.status != ProjectUpdate.Status.PUBLISHED:
            errors['project_update'] = _('Solo los avances publicados pueden recibir revisión documental.')
        if errors:
            raise ValidationError(errors)


class ProjectUpdateReviewDecision(models.Model):
    class Outcome(models.TextChoices):
        CONFORMING = 'conforming', _('Conforme')
        OBSERVED = 'observed', _('Observado')

    review = models.OneToOneField(
        ProjectUpdateReview,
        on_delete=models.PROTECT,
        related_name='decision',
        verbose_name=_('Revisión del Comité'),
    )
    outcome = models.CharField(_('Resultado'), max_length=20, choices=Outcome.choices)
    rationale = models.TextField(_('Fundamento de la decisión'))
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        editable=False,
        related_name='project_update_review_decisions',
        verbose_name=_('Registrado por'),
    )
    decided_at = models.DateTimeField(_('Fecha de decisión'), auto_now_add=True, editable=False)

    class Meta:
        ordering = ['-decided_at']
        verbose_name = _('resultado de revisión del Comité')
        verbose_name_plural = _('resultados de revisiones del Comité')
        permissions = [
            ('decide_projectupdate', _('Puede registrar decisiones institucionales de avances')),
        ]

    def __str__(self):
        return f'{self.get_outcome_display()} · {self.review}'

    def clean(self):
        errors = {}
        if self.rationale and not self.rationale.strip():
            errors['rationale'] = _('El fundamento de la decisión es obligatorio.')
        if self.review_id and self.review.project_update.status != ProjectUpdate.Status.PUBLISHED:
            errors['review'] = _('La revisión debe pertenecer a un avance publicado.')
        if errors:
            raise ValidationError(errors)


class ProjectDocument(models.Model):
    class DocumentType(models.TextChoices):
        PROPOSAL = 'proposal', _('Propuesta')
        WORK_PLAN = 'work_plan', _('Plan de trabajo')
        ACTION_PLAN = 'action_plan', _('Plan de acción')
        REPORT = 'report', _('Informe')
        OTHER = 'other', _('Otro')

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='documents')
    document_type = models.CharField(max_length=20, choices=DocumentType.choices)
    title = models.CharField(max_length=200)
    file = models.FileField(upload_to='project_documents/%Y/%m/')
    description = models.TextField(blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='uploaded_project_documents',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = _('documento de proyecto')
        verbose_name_plural = _('documentos de proyecto')

    def __str__(self):
        return self.title


class ProjectUpdateAttachment(models.Model):
    project_update = models.ForeignKey(
        ProjectUpdate,
        on_delete=models.CASCADE,
        related_name='attachments',
    )
    file = models.FileField(upload_to='project_update_attachments/%Y/%m/')
    title = models.CharField(max_length=200, blank=True)
    is_public = models.BooleanField(
        _('público en el portal de transparencia'),
        default=False,
        help_text=_(
            'Solo los adjuntos marcados explícitamente como públicos pueden '
            'aparecer en el portal; el avance y el proyecto padre también deben '
            'ser públicos.'
        ),
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='uploaded_project_update_attachments',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ProjectUpdateAttachmentQuerySet.as_manager()

    class Meta:
        ordering = ['created_at']
        verbose_name = _('adjunto de avance')
        verbose_name_plural = _('adjuntos de avance')

    def __str__(self):
        return self.title or self.file.name.rsplit('/', 1)[-1]

    # PRE: instance refers to a persisted attachment or a valid target ProjectUpdate.
    # POST: returns only when neither its persisted nor proposed parent is PUBLISHED.
    def _ensure_parent_update_is_editable(self):
        if self.pk and self.__class__.objects.filter(
            pk=self.pk,
            project_update__status=ProjectUpdate.Status.PUBLISHED,
        ).exists():
            raise ProjectUpdateImmutableError(
                _('Los adjuntos de avances publicados no se pueden modificar ni eliminar.')
            )
        if ProjectUpdate.objects.filter(
            pk=self.project_update_id,
            status=ProjectUpdate.Status.PUBLISHED,
        ).exists():
            raise ProjectUpdateImmutableError(
                _('No se pueden asociar adjuntos a avances publicados.')
            )

    def save(self, *args, **kwargs):
        """
        PRE: ordinary mutation targets an UNPUBLISHED parent, OR a domain publicity
             transition sets only ``is_public`` via ``_allow_publicity_transition``.
        POST: persists the row without weakening published-update immutability for
              file/title/parent fields.
        """
        if getattr(self, '_allow_publicity_transition', False):
            update_fields = kwargs.get('update_fields')
            if update_fields is not None and set(update_fields) <= {'is_public'}:
                try:
                    return super().save(*args, **kwargs)
                finally:
                    self._allow_publicity_transition = False
            self._allow_publicity_transition = False
            raise ProjectUpdateImmutableError(
                _('La transición de publicidad solo puede actualizar is_public.')
            )
        self._ensure_parent_update_is_editable()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """
        PRE: the attachment is persisted and ordinary deletion is requested.
        POST: deletes only an attachment whose parent update remains UNPUBLISHED.
        """
        self._ensure_parent_update_is_editable()
        return super().delete(*args, **kwargs)


class ProjectUpdateRemediationError(ValidationError):
    """Raised when a remediation or its attachments violate their explicit lifecycle."""


class ProjectUpdateRemediationQuerySet(models.QuerySet):
    def _ensure_draft_only(self):
        if self.exclude(status=ProjectUpdateRemediation.Status.DRAFT).exists():
            raise ProjectUpdateRemediationError(_('Solo las remediaciones en borrador admiten esta operación.'))

    def update(self, **kwargs):
        self._ensure_draft_only()
        return super().update(**kwargs)

    def delete(self):
        self._ensure_draft_only()
        return super().delete()


class ProjectUpdateRemediation(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', _('Borrador')
        SUBMITTED = 'submitted', _('Enviada')
        ACCEPTED = 'accepted', _('Aceptada')
        REJECTED = 'rejected', _('Rechazada')

    decision = models.OneToOneField(
        ProjectUpdateReviewDecision,
        on_delete=models.PROTECT,
        related_name='remediation',
    )
    response = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='created_project_update_remediations')
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='submitted_project_update_remediations')
    submitted_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='resolved_project_update_remediations')
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ProjectUpdateRemediationQuerySet.as_manager()

    class Meta:
        ordering = ['-created_at']
        verbose_name = _('remediación de avance')
        verbose_name_plural = _('remediaciones de avances')
        permissions = [
            ('submit_projectupdateremediation', _('Puede enviar remediaciones de avances')),
            ('resolve_projectupdateremediation', _('Puede resolver remediaciones de avances')),
        ]

    def clean(self):
        errors = {}
        if self.decision_id and self.decision.outcome != ProjectUpdateReviewDecision.Outcome.OBSERVED:
            errors['decision'] = _('Solo las decisiones observadas admiten remediación.')
        if not (self.response or '').strip():
            errors['response'] = _('La respuesta de remediación es obligatoria.')
        submitted = self.submitted_by_id is not None and self.submitted_at is not None
        resolved = self.resolved_by_id is not None and self.resolved_at is not None
        if self.status == self.Status.DRAFT and (submitted or resolved):
            errors['status'] = _('Una remediación en borrador no puede tener metadatos de envío o resolución.')
        if self.status == self.Status.SUBMITTED and (not submitted or resolved):
            errors['status'] = _('Una remediación enviada exige datos de envío y no admite resolución todavía.')
        if self.status in {self.Status.ACCEPTED, self.Status.REJECTED}:
            if not submitted or not resolved:
                errors['status'] = _('Una remediación resuelta exige datos de envío y resolución.')
            if not (self.resolution_notes or '').strip():
                errors['resolution_notes'] = _('Las notas de resolución son obligatorias.')
        if errors:
            raise ValidationError(errors)

    def _ensure_draft_mutation(self):
        if self.pk and self.__class__.objects.exclude(status=self.Status.DRAFT).filter(pk=self.pk).exists():
            raise ProjectUpdateRemediationError(_('Las remediaciones enviadas o resueltas son inmutables.'))
        if self.decision_id and ProjectUpdateReviewDecision.objects.filter(
            pk=self.decision_id,
            outcome=ProjectUpdateReviewDecision.Outcome.CONFORMING,
        ).exists():
            raise ProjectUpdateRemediationError(_('Solo las decisiones observadas admiten remediación.'))

    def save(self, *args, **kwargs):
        if not getattr(self, '_allow_lifecycle_transition', False):
            self._ensure_draft_mutation()
        try:
            return super().save(*args, **kwargs)
        finally:
            self._allow_lifecycle_transition = False

    def delete(self, *args, **kwargs):
        self._ensure_draft_mutation()
        return super().delete(*args, **kwargs)


class ProjectUpdateRemediationAttachmentQuerySet(models.QuerySet):
    def _ensure_draft_only(self):
        if self.exclude(remediation__status=ProjectUpdateRemediation.Status.DRAFT).exists():
            raise ProjectUpdateRemediationError(_('Los adjuntos solo se pueden modificar en remediaciones en borrador.'))

    def update(self, **kwargs):
        self._ensure_draft_only()
        return super().update(**kwargs)

    def delete(self):
        self._ensure_draft_only()
        return super().delete()


class ProjectUpdateRemediationAttachment(models.Model):
    remediation = models.ForeignKey(ProjectUpdateRemediation, on_delete=models.CASCADE, related_name='attachments')
    file = models.FileField(upload_to='project_update_remediation_attachments/%Y/%m/')
    title = models.CharField(max_length=200, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='uploaded_project_update_remediation_attachments')
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ProjectUpdateRemediationAttachmentQuerySet.as_manager()

    class Meta:
        ordering = ['created_at']
        verbose_name = _('adjunto de remediación')
        verbose_name_plural = _('adjuntos de remediaciones')

    def _ensure_draft_mutation(self):
        if self.pk and self.__class__.objects.exclude(remediation__status=ProjectUpdateRemediation.Status.DRAFT).filter(pk=self.pk).exists():
            raise ProjectUpdateRemediationError(_('Los adjuntos solo se pueden modificar en remediaciones en borrador.'))
        if ProjectUpdateRemediation.objects.exclude(status=ProjectUpdateRemediation.Status.DRAFT).filter(pk=self.remediation_id).exists():
            raise ProjectUpdateRemediationError(_('Los adjuntos solo se pueden crear en remediaciones en borrador.'))

    def save(self, *args, **kwargs):
        self._ensure_draft_mutation()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self._ensure_draft_mutation()
        return super().delete(*args, **kwargs)


class DonationAllocationProgress(models.TextChoices):
    UNALLOCATED = 'unallocated', _('Sin asignar')
    PARTIALLY_ALLOCATED = 'partially_allocated', _('Parcialmente asignada')
    FULLY_ALLOCATED = 'fully_allocated', _('Totalmente asignada')


class AllocationExecutionProgress(models.TextChoices):
    UNEXECUTED = 'unexecuted', _('Sin ejecución')
    PARTIALLY_EXECUTED = 'partially_executed', _('Parcialmente ejecutada')
    FULLY_EXECUTED = 'fully_executed', _('Totalmente ejecutada')


class Donation(models.Model):
    class Status(models.TextChoices):
        REGISTERED = 'registered', _('Registrada')
        RECEIVED = 'received', _('Recibida')
        ANNULLED = 'annulled', _('Anulada')

    code = models.CharField(max_length=40, unique=True)
    donor = models.ForeignKey(Institution, on_delete=models.PROTECT, related_name='donations')
    donation_type = models.CharField(max_length=20, choices=DONATION_TYPE_CHOICES, default='goods')
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(
        max_length=3,
        choices=CURRENCY_CHOICES,
        default=OPERATING_CURRENCY,
        null=False,
        blank=False,
    )
    objective = models.TextField()
    restrictions = models.TextField(blank=True)
    commitment_date = models.DateField(null=True, blank=True)
    received_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.REGISTERED)
    terminal_reason = models.TextField(blank=True, editable=False)
    terminal_at = models.DateTimeField(null=True, blank=True, editable=False)
    terminal_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name='terminal_donations',
    )
    support_reference = models.CharField(max_length=180, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-received_date', '-created_at']
        verbose_name = _('donación')
        verbose_name_plural = _('donaciones')
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=ZERO_MONEY),
                name='operations_donation_amount_gt_zero',
            ),
            models.CheckConstraint(
                condition=models.Q(currency=OPERATING_CURRENCY),
                name='operations_donation_currency_is_usd',
            ),
        ]

    def __str__(self):
        return f'{self.code} - {self.donor}'

    def save(self, *args, **kwargs):
        # PRE: explicit codes are supplied only by trusted fixtures, migrations, or seed data.
        # POST: creates with one reserved DON code or preserves the existing code on update.
        ensure_operational_code_is_immutable(self, self.code)
        if self.code:
            return super().save(*args, **kwargs)
        with transaction.atomic():
            self.code = reserve_operational_code(namespace='donation', prefix='DON')
            return super().save(*args, **kwargs)

    def clean(self):
        """
        PRE: amount/donor may be partially present during form or service validation.
        POST: rejects non-positive amounts; when pk exists, rejects amount below non-annulled
        allocated total; rejects inactive donors on create or donor replacement.
        Limitation: historical inactive donor retention is allowed when donor_id is unchanged;
        create/update donor eligibility remains authoritative in services/forms under concurrency.
        """
        # PRE: amount may be absent when field validation has already rejected submitted data.
        # POST: rejects non-positive numeric amounts without masking field errors for missing or invalid values.
        errors = {}
        if self.amount is not None and self.amount <= ZERO_MONEY:
            errors['amount'] = _('El monto de la donación debe ser positivo.')

        if self.pk and self.amount is not None and 'amount' not in errors:
            allocated_total = self.total_assigned
            if self.amount < allocated_total:
                errors['amount'] = _(
                    'El importe de la donación no puede ser inferior al total ya asignado '
                    '(%(allocated_total)s USD).'
                ) % {'allocated_total': allocated_total}

        if self.donor_id is not None:
            donor_status = None
            if getattr(self, 'donor', None) is not None and self.donor.pk == self.donor_id:
                donor_status = self.donor.status
            else:
                donor_status = (
                    Institution.objects.filter(pk=self.donor_id)
                    .values_list('status', flat=True)
                    .first()
                )
            if donor_status == Institution.Status.INACTIVE:
                previous_donor_id = None
                if self.pk:
                    previous_donor_id = (
                        type(self).objects.filter(pk=self.pk)
                        .values_list('donor_id', flat=True)
                        .first()
                    )
                if previous_donor_id != self.donor_id:
                    if previous_donor_id is None:
                        errors['donor'] = _(
                            'Solo instituciones activas pueden registrar nuevas donaciones.'
                        )
                    else:
                        errors['donor'] = _(
                            'No se puede reemplazar el donante por una institución inactiva.'
                        )

        if errors:
            raise ValidationError(errors)

    @property
    def total_assigned(self):
        if hasattr(self, 'annotated_total_assigned'):
            return self.annotated_total_assigned
        return self.allocations.exclude(status=FundAllocation.Status.ANNULLED).aggregate(
            total=Sum('amount')
        )['total'] or ZERO_MONEY

    @property
    def available_balance(self):
        if hasattr(self, 'annotated_available_balance'):
            return self.annotated_available_balance
        balance = self.amount - self.total_assigned
        return max(balance, ZERO_MONEY)

    @property
    def allocation_progress(self):
        """
        PRE: this persisted donation has a valid positive Decimal amount.
        POST: returns allocation progress derived from non-annulled allocations without mutation.
        """
        assigned = self.total_assigned
        if assigned == ZERO_MONEY:
            return DonationAllocationProgress.UNALLOCATED
        if assigned < self.amount:
            return DonationAllocationProgress.PARTIALLY_ALLOCATED
        return DonationAllocationProgress.FULLY_ALLOCATED

    @property
    def allocation_progress_label(self):
        """
        PRE: allocation_progress returns a declared DonationAllocationProgress value.
        POST: returns its localized display label without changing persisted state.
        """
        return DonationAllocationProgress(self.allocation_progress).label


class FundAllocation(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'active', _('Activa')
        FINISHED = 'finished', _('Finalizada')
        ANNULLED = 'annulled', _('Anulada')

    code = models.CharField(max_length=40, unique=True, editable=False)
    donation = models.ForeignKey(Donation, on_delete=models.PROTECT, related_name='allocations')
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name='allocations')
    budget_category = models.CharField(max_length=40, choices=BUDGET_CATEGORY_CHOICES)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    responsible_person = models.CharField(max_length=120, blank=True)
    allocation_date = models.DateField()
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.ACTIVE)
    terminal_reason = models.TextField(blank=True, editable=False)
    terminal_at = models.DateTimeField(null=True, blank=True, editable=False)
    terminal_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name='terminal_allocations',
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-allocation_date', '-created_at']
        verbose_name = _('asignación de fondos')
        verbose_name_plural = _('asignaciones de fondos')
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=ZERO_MONEY),
                name='operations_allocation_amount_gt_zero',
            ),
        ]

    def __str__(self):
        return f'{self.code} - {self.project.name}: {self.amount}'

    def save(self, *args, **kwargs):
        # PRE: explicit codes are supplied only by trusted fixtures, migrations, or seed data.
        # POST: creates with one reserved ASG code or preserves the existing code on update.
        ensure_operational_code_is_immutable(self, self.code)
        if self.code:
            return super().save(*args, **kwargs)
        with transaction.atomic():
            self.code = reserve_operational_code(namespace='fund_allocation', prefix='ASG')
            return super().save(*args, **kwargs)

    def clean(self):
        # PRE: amount may be absent when field validation has already rejected submitted data.
        # POST: validates only present numeric amounts and preserves prior field-level validation errors.
        errors = {}
        if self.amount is not None and self.amount <= ZERO_MONEY:
            errors['amount'] = _('El monto de la asignación debe ser positivo.')
        if (
            self.amount is not None
            and self.donation_id
            and self.amount > self.donation_available_before_this_allocation()
        ):
            errors['amount'] = _('El monto de la asignación excede el saldo disponible de la donación.')
        if errors:
            raise ValidationError(errors)

    # PRE: self.donation is set and self.amount contains the proposed allocation amount.
    # POST: returns the donation balance that can be assigned to this allocation, including its current amount on updates.
    def donation_available_before_this_allocation(self):
        existing_amount = ZERO_MONEY
        if self.pk:
            existing_amount = FundAllocation.objects.filter(pk=self.pk).values_list('amount', flat=True).first() or ZERO_MONEY
        return self.donation.available_balance + existing_amount

    @property
    def executed_amount(self):
        if hasattr(self, 'annotated_executed_amount'):
            return self.annotated_executed_amount
        return self.expenses.exclude(
            status__in=Expense.non_executing_statuses()
        ).aggregate(total=Sum('amount'))['total'] or ZERO_MONEY

    @property
    def reserved_amount(self):
        # PRE: this allocation may carry list annotations or require an authoritative aggregation.
        # POST: returns active APPROVED_RESERVED reserved total; never None.
        if hasattr(self, 'annotated_reserved_amount'):
            annotated = self.annotated_reserved_amount
            return annotated if annotated is not None else ZERO_MONEY
        from .financials import get_allocation_reserved_amount

        return get_allocation_reserved_amount(self)

    @property
    def available_balance(self):
        if hasattr(self, 'annotated_available_balance'):
            return self.annotated_available_balance
        balance = self.amount - self.executed_amount - self.reserved_amount
        return max(balance, ZERO_MONEY)

    @property
    def execution_progress(self):
        """
        PRE: this persisted allocation has a valid positive Decimal amount.
        POST: returns execution progress derived from effective expenses without mutation.
        """
        executed = self.executed_amount
        if executed == ZERO_MONEY:
            return AllocationExecutionProgress.UNEXECUTED
        if executed < self.amount:
            return AllocationExecutionProgress.PARTIALLY_EXECUTED
        return AllocationExecutionProgress.FULLY_EXECUTED

    @property
    def execution_progress_label(self):
        """
        PRE: execution_progress returns a declared AllocationExecutionProgress value.
        POST: returns its localized display label without changing persisted state.
        """
        return AllocationExecutionProgress(self.execution_progress).label


class Expense(models.Model):
    class Status(models.TextChoices):
        REGISTERED = 'registered', _('Registrado')
        ANNULLED = 'annulled', _('Anulado')

    code = models.CharField(max_length=40, unique=True, editable=False)
    allocation = models.ForeignKey(FundAllocation, on_delete=models.PROTECT, related_name='expenses')
    expense_date = models.DateField()
    category = models.CharField(max_length=40, choices=EXPENSE_CATEGORY_CHOICES)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(
        max_length=3,
        choices=CURRENCY_CHOICES,
        default=OPERATING_CURRENCY,
        null=False,
        blank=False,
    )
    reason = models.CharField(max_length=220)
    provider_or_recipient = models.CharField(max_length=160)
    payment_method = models.CharField(max_length=40, choices=PAYMENT_METHOD_CHOICES)
    description = models.TextField(blank=True)
    observations = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.REGISTERED)
    terminal_reason = models.TextField(blank=True, editable=False)
    terminal_at = models.DateTimeField(null=True, blank=True, editable=False)
    terminal_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name='terminal_expenses',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-expense_date', '-created_at']
        verbose_name = _('gasto')
        verbose_name_plural = _('gastos')
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=ZERO_MONEY),
                name='operations_expense_amount_gt_zero',
            ),
            models.CheckConstraint(
                condition=models.Q(currency=OPERATING_CURRENCY),
                name='operations_expense_currency_is_usd',
            ),
        ]

    def __str__(self):
        return f'{self.reason} - {self.amount}'

    def save(self, *args, **kwargs):
        # PRE: explicit codes are supplied only by trusted fixtures, migrations, or seed data.
        # POST: creates with one reserved GAS code or preserves the existing code on update.
        ensure_operational_code_is_immutable(self, self.code)
        if self.code:
            return super().save(*args, **kwargs)
        with transaction.atomic():
            self.code = reserve_operational_code(namespace='expense', prefix='GAS')
            return super().save(*args, **kwargs)

    def clean(self):
        # PRE: amount may be absent during partial validation and allocation may be selected.
        # POST: rejects invalid present amounts without masking field errors or duplicating balance formulas.
        errors = {}
        if self.amount is not None and self.amount <= ZERO_MONEY:
            errors['amount'] = _('El monto del gasto debe ser positivo.')
        if (
            self.amount is not None
            and self.allocation_id
            and self.amount > self.allocation_available_before_this_expense()
        ):
            errors['amount'] = _('El monto del gasto excede el saldo disponible de la asignación.')
        if errors:
            raise ValidationError(errors)

    @classmethod
    def non_executing_statuses(cls):
        """
        PRE: Expense status choices are loaded.
        POST: returns terminal states excluded from execution totals as a tuple.
        """
        return (cls.Status.ANNULLED,)

    # PRE: self.allocation is set and self.amount contains the proposed expense amount.
    # POST: returns the allocation balance that can be executed by this expense, including its current amount on updates.
    def allocation_available_before_this_expense(self):
        existing_amount = ZERO_MONEY
        if self.pk:
            existing_amount = Expense.objects.filter(pk=self.pk).values_list('amount', flat=True).first() or ZERO_MONEY
        return self.allocation.available_balance + existing_amount

    # PRE: the expense has been saved before checking existing related documents.
    # POST: returns True only when at least one protected supporting document exists.
    def has_required_support(self):
        return self.supporting_documents.exists()


class SupportingDocument(models.Model):
    expense = models.ForeignKey(Expense, on_delete=models.CASCADE, related_name='supporting_documents')
    title = models.CharField(max_length=160)
    document = models.FileField(upload_to='supporting_documents/%Y/%m/')
    uploaded_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-uploaded_at']
        verbose_name = _('documento soporte')
        verbose_name_plural = _('documentos soporte')

    def __str__(self):
        return self.title


class ExpenseRequest(models.Model):
    class Status(models.TextChoices):
        PENDING_DECISION = 'pending_decision', _('Pendiente de decisión')
        APPROVED_RESERVED = (
            'approved_reserved',
            _('Aprobada · Fondos reservados'),
        )
        DENIED = 'denied', _('Denegada')
        WITHDRAWN = 'withdrawn', _('Retirada')
        FULFILLED = 'fulfilled', _('Gasto registrado')
        ANNULLED = 'annulled', _('Anulada')

    code = models.CharField(max_length=40, unique=True, editable=False)
    fund_allocation = models.ForeignKey(
        FundAllocation,
        on_delete=models.PROTECT,
        related_name='expense_requests',
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='expense_requests',
    )
    requested_amount = models.DecimalField(max_digits=14, decimal_places=2)
    purpose = models.CharField(max_length=220)
    requested_date = models.DateField()
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.PENDING_DECISION,
    )
    decision_note = models.TextField(blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name='decided_expense_requests',
    )
    decided_at = models.DateTimeField(null=True, blank=True, editable=False)
    reserved_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        editable=False,
    )
    reserved_at = models.DateTimeField(null=True, blank=True, editable=False)
    expense = models.OneToOneField(
        Expense,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='source_expense_request',
    )
    terminal_reason = models.TextField(blank=True, editable=False)
    terminal_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name='terminal_expense_requests',
    )
    terminal_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-requested_date', '-created_at']
        verbose_name = _('solicitud de gasto')
        verbose_name_plural = _('solicitudes de gasto')
        permissions = [
            (
                'decide_expenserequest',
                _('Puede aprobar o denegar solicitudes de gasto'),
            ),
            (
                'fulfill_expenserequest',
                _('Puede registrar el gasto de una solicitud aprobada'),
            ),
            (
                'withdraw_expenserequest',
                _('Puede retirar solicitudes de gasto propias'),
            ),
            (
                'annul_expenserequest',
                _('Puede anular solicitudes de gasto'),
            ),
        ]
        indexes = [
            models.Index(fields=['status'], name='ops_expreq_status_idx'),
            models.Index(
                fields=['fund_allocation', 'status'],
                name='ops_expreq_alloc_status_idx',
            ),
            models.Index(
                fields=['requested_by', 'status'],
                name='ops_expreq_req_by_status_idx',
            ),
            models.Index(fields=['-requested_date'], name='ops_expreq_req_date_idx'),
            models.Index(fields=['-decided_at'], name='ops_expreq_decided_at_idx'),
            models.Index(fields=['-created_at'], name='ops_expreq_created_at_idx'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(requested_amount__gt=ZERO_MONEY),
                name='operations_expenserequest_requested_amount_gt_zero',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(reserved_amount__isnull=True)
                    | models.Q(reserved_amount__gte=ZERO_MONEY)
                ),
                name='operations_expenserequest_reserved_amount_gte_zero',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(reserved_amount__isnull=True)
                    | models.Q(reserved_amount__lte=models.F('requested_amount'))
                ),
                name='operations_expenserequest_reserved_lte_requested',
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(status='fulfilled')
                    | models.Q(expense__isnull=False)
                ),
                name='operations_expenserequest_fulfilled_requires_expense',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status='fulfilled')
                    | models.Q(expense__isnull=True)
                ),
                name='operations_expenserequest_non_fulfilled_no_expense',
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(status__in=['approved_reserved', 'fulfilled'])
                    | (
                        models.Q(decided_by__isnull=False)
                        & models.Q(decided_at__isnull=False)
                        & models.Q(reserved_amount__isnull=False)
                        & models.Q(reserved_at__isnull=False)
                    )
                ),
                name='operations_expenserequest_approved_fulfilled_meta',
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(status='denied')
                    | (
                        models.Q(decided_by__isnull=False)
                        & models.Q(decided_at__isnull=False)
                        & ~models.Q(decision_note='')
                    )
                ),
                name='operations_expenserequest_denied_requires_decision',
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(status__in=['withdrawn', 'annulled'])
                    | (
                        models.Q(terminal_by__isnull=False)
                        & models.Q(terminal_at__isnull=False)
                        & ~models.Q(terminal_reason='')
                    )
                ),
                name='operations_expenserequest_terminal_requires_meta',
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(status='pending_decision')
                    | (
                        models.Q(decided_by__isnull=True)
                        & models.Q(decided_at__isnull=True)
                        & models.Q(decision_note='')
                        & models.Q(reserved_amount__isnull=True)
                        & models.Q(reserved_at__isnull=True)
                        & models.Q(expense__isnull=True)
                        & models.Q(terminal_by__isnull=True)
                        & models.Q(terminal_at__isnull=True)
                        & models.Q(terminal_reason='')
                    )
                ),
                name='operations_expenserequest_pending_clean_slate',
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(status='denied')
                    | (
                        models.Q(reserved_amount__isnull=True)
                        & models.Q(reserved_at__isnull=True)
                        & models.Q(expense__isnull=True)
                        & models.Q(terminal_by__isnull=True)
                        & models.Q(terminal_at__isnull=True)
                        & models.Q(terminal_reason='')
                    )
                ),
                name='operations_expenserequest_denied_no_reservation',
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(status='withdrawn')
                    | (
                        models.Q(decided_by__isnull=True)
                        & models.Q(decided_at__isnull=True)
                        & models.Q(decision_note='')
                        & models.Q(reserved_amount__isnull=True)
                        & models.Q(reserved_at__isnull=True)
                        & models.Q(expense__isnull=True)
                    )
                ),
                name='operations_expenserequest_withdrawn_no_decision',
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(status='approved_reserved')
                    | (
                        models.Q(expense__isnull=True)
                        & models.Q(terminal_by__isnull=True)
                        & models.Q(terminal_at__isnull=True)
                        & models.Q(terminal_reason='')
                    )
                ),
                name='operations_expenserequest_approved_no_terminal',
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(status='fulfilled')
                    | (
                        models.Q(terminal_by__isnull=True)
                        & models.Q(terminal_at__isnull=True)
                        & models.Q(terminal_reason='')
                    )
                ),
                name='operations_expenserequest_fulfilled_no_terminal',
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(status='annulled')
                    | models.Q(expense__isnull=True)
                ),
                name='operations_expenserequest_annulled_no_expense',
            ),
        ]

    def __str__(self):
        return f'{self.code} · {self.get_status_display()}'

    def save(self, *args, **kwargs):
        # PRE: explicit codes are supplied only by trusted fixtures, migrations, or seed data.
        # POST: creates with one reserved SGS code or preserves the existing code on update.
        ensure_operational_code_is_immutable(self, self.code)
        if self.code:
            return super().save(*args, **kwargs)
        with transaction.atomic():
            self.code = reserve_operational_code(
                namespace='expense_request',
                prefix='SGS',
            )
            return super().save(*args, **kwargs)

    def clean(self):
        # PRE: numeric and status metadata may be partially present during form validation.
        # POST: rejects inconsistent present values without enforcing actor authorization.
        errors = {}
        if self.requested_amount is not None and self.requested_amount <= ZERO_MONEY:
            errors['requested_amount'] = _('El monto solicitado debe ser positivo.')
        if self.reserved_amount is not None and self.reserved_amount < ZERO_MONEY:
            errors['reserved_amount'] = _('El monto reservado no puede ser negativo.')
        if (
            self.reserved_amount is not None
            and self.requested_amount is not None
            and self.reserved_amount > self.requested_amount
        ):
            errors['reserved_amount'] = _(
                'El monto reservado no puede superar el monto solicitado.'
            )

        has_decision = self.decided_by_id is not None or self.decided_at is not None
        has_reservation = (
            self.reserved_amount is not None or self.reserved_at is not None
        )
        has_terminal = (
            self.terminal_by_id is not None
            or self.terminal_at is not None
            or bool((self.terminal_reason or '').strip())
        )
        decision_note_present = bool((self.decision_note or '').strip())
        terminal_reason_present = bool((self.terminal_reason or '').strip())

        if self.status == self.Status.PENDING_DECISION:
            if has_decision or decision_note_present or has_reservation or self.expense_id or has_terminal:
                errors['status'] = _(
                    'Una solicitud pendiente no admite metadatos de decisión, reserva, gasto o cierre.'
                )
        elif self.status == self.Status.APPROVED_RESERVED:
            if self.decided_by_id is None or self.decided_at is None:
                errors['status'] = _('Una solicitud aprobada exige datos de decisión.')
            if self.reserved_amount is None or self.reserved_at is None:
                errors['reserved_amount'] = _(
                    'Una solicitud aprobada exige metadatos de reserva.'
                )
            if self.expense_id or has_terminal:
                errors['status'] = _(
                    'Una solicitud aprobada con reserva no admite gasto enlazado ni cierre.'
                )
        elif self.status == self.Status.DENIED:
            if self.decided_by_id is None or self.decided_at is None:
                errors['status'] = _('Una solicitud denegada exige datos de decisión.')
            if not decision_note_present:
                errors['decision_note'] = _(
                    'La denegación exige una nota de decisión significativa.'
                )
            if has_reservation or self.expense_id or has_terminal:
                errors['status'] = _(
                    'Una solicitud denegada no admite reserva, gasto enlazado ni cierre.'
                )
        elif self.status == self.Status.WITHDRAWN:
            if self.terminal_by_id is None or self.terminal_at is None:
                errors['status'] = _('Una solicitud retirada exige metadatos de cierre.')
            if not terminal_reason_present:
                errors['terminal_reason'] = _(
                    'El retiro exige un motivo de cierre significativo.'
                )
            if has_decision or decision_note_present or has_reservation or self.expense_id:
                errors['status'] = _(
                    'Una solicitud retirada no admite decisión, reserva ni gasto enlazado.'
                )
        elif self.status == self.Status.FULFILLED:
            if self.expense_id is None:
                errors['expense'] = _('Una solicitud cumplida exige un gasto enlazado.')
            if self.decided_by_id is None or self.decided_at is None:
                errors['status'] = _('Una solicitud cumplida exige datos de decisión.')
            if self.reserved_amount is None or self.reserved_at is None:
                errors['reserved_amount'] = _(
                    'Una solicitud cumplida exige metadatos de reserva.'
                )
            if has_terminal:
                errors['status'] = _('Una solicitud cumplida no admite metadatos de cierre.')
        elif self.status == self.Status.ANNULLED:
            if self.terminal_by_id is None or self.terminal_at is None:
                errors['status'] = _('Una solicitud anulada exige metadatos de cierre.')
            if not terminal_reason_present:
                errors['terminal_reason'] = _(
                    'La anulación exige un motivo de cierre significativo.'
                )
            if self.expense_id:
                errors['expense'] = _('Una solicitud anulada no puede enlazar un gasto.')

        if self.status != self.Status.FULFILLED and self.expense_id:
            errors['expense'] = _(
                'Solo una solicitud cumplida puede enlazar un gasto.'
            )

        if errors:
            raise ValidationError(errors)

    @classmethod
    def open_financial_statuses(cls):
        """
        PRE: ExpenseRequest status choices are loaded.
        POST: returns statuses that block financial scope closure (allocation finish / project close).
        """
        return (
            cls.Status.PENDING_DECISION,
            cls.Status.APPROVED_RESERVED,
        )

    @property
    def is_pending_decision(self):
        return self.status == self.Status.PENDING_DECISION

    @property
    def has_active_reservation(self):
        return (
            self.status == self.Status.APPROVED_RESERVED
            and self.reserved_amount is not None
        )

    @property
    def is_terminal(self):
        return self.status in {
            self.Status.DENIED,
            self.Status.WITHDRAWN,
            self.Status.FULFILLED,
            self.Status.ANNULLED,
        }

    @property
    def currency(self):
        return OPERATING_CURRENCY


class ExpenseRequestAttachmentMutationError(ValidationError):
    """Raised when attachment mutation violates the pending-decision freeze rule."""


class ExpenseRequestAttachmentQuerySet(models.QuerySet):
    # PRE: queryset targets persisted expense-request attachments.
    # POST: returns only when every selected attachment belongs to PENDING_DECISION.
    def _ensure_pending_decision_attachments(self):
        if self.exclude(
            expense_request__status=ExpenseRequest.Status.PENDING_DECISION
        ).exists():
            raise ExpenseRequestAttachmentMutationError(
                _(
                    'Los adjuntos de solicitudes de gasto solo se pueden modificar '
                    'mientras la solicitud esté pendiente de decisión.'
                )
            )

    def update(self, **kwargs):
        self._ensure_pending_decision_attachments()
        target_request = kwargs.get('expense_request', kwargs.get('expense_request_id'))
        target_request_id = getattr(target_request, 'pk', target_request)
        if (
            target_request_id is not None
            and ExpenseRequest.objects.exclude(
                status=ExpenseRequest.Status.PENDING_DECISION
            )
            .filter(pk=target_request_id)
            .exists()
        ):
            raise ExpenseRequestAttachmentMutationError(
                _(
                    'No se pueden asociar adjuntos a solicitudes que no estén '
                    'pendientes de decisión.'
                )
            )
        return super().update(**kwargs)

    def delete(self):
        self._ensure_pending_decision_attachments()
        return super().delete()


class ExpenseRequestAttachment(models.Model):
    expense_request = models.ForeignKey(
        ExpenseRequest,
        on_delete=models.CASCADE,
        related_name='attachments',
    )
    file = models.FileField(upload_to='expense_request_attachments/%Y/%m/')
    title = models.CharField(max_length=160)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='uploaded_expense_request_attachments',
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)

    objects = ExpenseRequestAttachmentQuerySet.as_manager()

    class Meta:
        ordering = ['uploaded_at']
        verbose_name = _('adjunto de solicitud de gasto')
        verbose_name_plural = _('adjuntos de solicitudes de gasto')

    def __str__(self):
        return f'{self.expense_request.code} · {self.title}'

    # PRE: instance refers to a persisted attachment or a valid target ExpenseRequest.
    # POST: returns only when parent remains PENDING_DECISION for create/update/delete.
    def _ensure_parent_is_pending_decision(self):
        if self.pk and self.__class__.objects.exclude(
            expense_request__status=ExpenseRequest.Status.PENDING_DECISION
        ).filter(pk=self.pk).exists():
            raise ExpenseRequestAttachmentMutationError(
                _(
                    'Los adjuntos de solicitudes de gasto solo se pueden modificar '
                    'mientras la solicitud esté pendiente de decisión.'
                )
            )
        if ExpenseRequest.objects.exclude(
            status=ExpenseRequest.Status.PENDING_DECISION
        ).filter(pk=self.expense_request_id).exists():
            raise ExpenseRequestAttachmentMutationError(
                _(
                    'No se pueden asociar adjuntos a solicitudes que no estén '
                    'pendientes de decisión.'
                )
            )

    def save(self, *args, **kwargs):
        """
        PRE: the attachment has a valid parent request and ordinary mutation is requested.
        POST: persists only an attachment associated with a PENDING_DECISION request.
        """
        self._ensure_parent_is_pending_decision()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """
        PRE: the attachment is persisted and ordinary deletion is requested.
        POST: deletes only an attachment whose parent remains PENDING_DECISION.
        """
        self._ensure_parent_is_pending_decision()
        return super().delete(*args, **kwargs)


class ExpenseRequestEventImmutableError(ValidationError):
    """Raised when application code attempts to mutate expense-request event history."""


class ExpenseRequestEventQuerySet(models.QuerySet):
    """Append-only query operations for expense-request event history."""

    def update(self, **kwargs):
        raise ExpenseRequestEventImmutableError(
            _('Los eventos de solicitud de gasto no se pueden modificar.')
        )

    def delete(self):
        raise ExpenseRequestEventImmutableError(
            _('Los eventos de solicitud de gasto no se pueden eliminar.')
        )

    def bulk_update(self, objs, fields, batch_size=None):
        raise ExpenseRequestEventImmutableError(
            _('Los eventos de solicitud de gasto no se pueden modificar en lote.')
        )


class ExpenseRequestEvent(models.Model):
    class EventType(models.TextChoices):
        CREATED = 'created', _('Solicitud creada')
        UPDATED = 'updated', _('Solicitud actualizada')
        WITHDRAWN = 'withdrawn', _('Solicitud retirada')
        APPROVED = 'approved', _('Solicitud aprobada')
        DENIED = 'denied', _('Solicitud denegada')
        RESERVATION_CREATED = 'reservation_created', _('Reserva creada')
        ANNULLED = 'annulled', _('Solicitud anulada')
        RESERVATION_RELEASED = (
            'reservation_released',
            _('Reserva liberada'),
        )
        EXPENSE_REGISTERED = (
            'expense_registered',
            _('Gasto registrado'),
        )
        RESERVATION_CONSUMED = (
            'reservation_consumed',
            _('Reserva convertida en ejecución'),
        )
        UNUSED_RESERVATION_RELEASED = (
            'unused_reservation_released',
            _('Reserva no utilizada liberada'),
        )
        LINKED_EXPENSE_ANNULLED = (
            'linked_expense_annulled',
            _('Gasto enlazado anulado'),
        )

    expense_request = models.ForeignKey(
        ExpenseRequest,
        on_delete=models.PROTECT,
        related_name='events',
    )
    event_type = models.CharField(max_length=40, choices=EventType.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='expense_request_events',
    )
    from_status = models.CharField(max_length=30, blank=True)
    to_status = models.CharField(max_length=30, blank=True)
    requested_amount = models.DecimalField(max_digits=14, decimal_places=2)
    reserved_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
    )
    executed_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
    )
    released_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
    )
    allocation_balance_before = models.DecimalField(max_digits=14, decimal_places=2)
    allocation_balance_after = models.DecimalField(max_digits=14, decimal_places=2)
    reason = models.TextField(blank=True)
    # PROTECT (not SET_NULL): append-only events cannot be UPDATEd on expense
    # delete, and the PostgreSQL statement-level trigger rejects even empty
    # SET_NULL statements issued by Django's deletion collector.
    expense = models.ForeignKey(
        Expense,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='expense_request_events',
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ExpenseRequestEventQuerySet.as_manager()

    class Meta:
        ordering = ['created_at', 'pk']
        verbose_name = _('evento de solicitud de gasto')
        verbose_name_plural = _('eventos de solicitudes de gasto')
        indexes = [
            models.Index(
                fields=['expense_request', 'created_at'],
                name='ops_expreq_evt_req_created_idx',
            ),
            models.Index(
                fields=['event_type', 'created_at'],
                name='ops_expreq_evt_type_crtd_idx',
            ),
        ]

    def __str__(self):
        return f'{self.expense_request.code} · {self.get_event_type_display()}'

    def save(self, *args, **kwargs):
        """
        PRE: self represents a new expense-request event with required fields populated.
        POST: inserts the event once; existing rows cannot be modified.
        """
        row_already_exists = (
            self.pk is not None
            and type(self).objects.filter(pk=self.pk).exists()
        )
        if not self._state.adding or row_already_exists or kwargs.get('force_update'):
            raise ExpenseRequestEventImmutableError(
                _('Los eventos de solicitud de gasto existentes no se pueden modificar.')
            )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """
        PRE: self is an expense-request event targeted for instance deletion.
        POST: always rejects deletion and preserves the event.
        """
        raise ExpenseRequestEventImmutableError(
            _('Los eventos de solicitud de gasto no se pueden eliminar.')
        )


class AuditLogImmutableError(ValidationError):
    """Raised when application code attempts to mutate audit history."""


class AuditLogQuerySet(models.QuerySet):
    """Append-only query operations for audit history."""

    def update(self, **kwargs):
        """
        PRE: queryset targets persisted audit events.
        POST: always rejects bulk modification without changing audit history.
        """
        raise AuditLogImmutableError(_('Los registros de auditoría no se pueden modificar.'))

    def delete(self):
        """
        PRE: queryset targets persisted audit events.
        POST: always rejects bulk deletion without changing audit history.
        """
        raise AuditLogImmutableError(_('Los registros de auditoría no se pueden eliminar.'))

    def bulk_update(self, objs, fields, batch_size=None):
        """
        PRE: objs contains existing audit events proposed for bulk modification.
        POST: always rejects bulk modification without changing audit history.
        """
        raise AuditLogImmutableError(_('Los registros de auditoría no se pueden modificar en lote.'))

    def bulk_create(self, objs, batch_size=None, ignore_conflicts=False, update_conflicts=False, update_fields=None, unique_fields=None):
        """
        PRE: objs contains proposed audit events for a bulk insert.
        POST: rejects bulk creation because it bypasses per-event creation guarantees.
        """
        raise AuditLogImmutableError(
            _('La auditoría debe registrarse evento por evento mediante el servicio autorizado.')
        )


class AuditLog(models.Model):
    class Action(models.TextChoices):
        CREATED = 'created', _('Creada')
        UPDATED = 'updated', _('Actualizada')
        VALIDATED = 'validated', _('Validada')
        REJECTED = 'rejected', _('Rechazada')
        ANNULLED = 'annulled', _('Anulada')
        ASSIGNED = 'assigned', _('Asignada')
        EXECUTED = 'executed', _('Ejecutada')
        CLOSED = 'closed', _('Cerrada')
        EXPENSE_CANCELLED = 'expense_cancelled', _('Gasto anulado')
        PUBLISHED = 'published', _('Publicada')
        UNPUBLISHED = 'unpublished', _('Retirado del portal')
        COMPLETED = 'completed', _('Completada')
        REOPENED = 'reopened', _('Reabierta')
        REORDERED = 'reordered', _('Reordenada')
        DELETED = 'deleted', _('Eliminada')

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='audit_logs',
    )
    action = models.CharField(max_length=20, choices=Action.choices)
    model_name = models.CharField(max_length=80)
    entity_id = models.CharField(max_length=80)
    entity_label = models.CharField(max_length=220)
    summary = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    objects = AuditLogQuerySet.as_manager()

    class Meta:
        ordering = ['-created_at']
        verbose_name = _('registro de auditoría')
        verbose_name_plural = _('registros de auditoría')

    @property
    def display_model_name(self):
        legacy_model_names = {
            'Donation': _('Donación'),
            'Project': _('Proyecto'),
            'Institution': _('Institución'),
            'Fund Allocation': _('Asignación de fondos'),
            'FundAllocation': _('Asignación de fondos'),
            'Expense': _('Gasto'),
            'Supporting Document': _('Documento soporte'),
            'SupportingDocument': _('Documento soporte'),
            'Audit Log': _('Registro de auditoría'),
            'AuditLog': _('Registro de auditoría'),
        }
        return legacy_model_names.get(self.model_name, self.model_name)

    @property
    def display_summary(self):
        legacy_summaries = {
            'Institution created.': _('Institución creada.'),
            'Institution updated.': _('Institución actualizada.'),
            'Project created.': _('Proyecto creado.'),
            'Project updated.': _('Proyecto actualizado.'),
            'Donation created.': _('Donación creada.'),
            'Donation updated.': _('Donación actualizada.'),
            'Fund allocation assigned.': _('Asignación de fondos registrada.'),
            'Fund allocation updated.': _('Asignación de fondos actualizada.'),
            'Expense recorded.': _('Gasto registrado.'),
            'Expense updated.': _('Gasto actualizado.'),
            'Record updated.': _('Registro actualizado.'),
        }
        return legacy_summaries.get(self.summary, self.summary)

    def __str__(self):
        return f'{self.get_action_display()} {self.display_model_name} {self.entity_label}'

    def save(self, *args, **kwargs):
        """
        PRE: self represents a new audit event with all required fields populated.
        POST: inserts the event once; existing rows cannot be modified.
        """
        row_already_exists = self.pk is not None and type(self).objects.filter(pk=self.pk).exists()
        if not self._state.adding or row_already_exists or kwargs.get('force_update'):
            raise AuditLogImmutableError(_('Los registros de auditoría existentes no se pueden modificar.'))
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """
        PRE: self is an audit event targeted for instance deletion.
        POST: always rejects deletion and preserves the event.
        """
        raise AuditLogImmutableError(_('Los registros de auditoría no se pueden eliminar.'))
