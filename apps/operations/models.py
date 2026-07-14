from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
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
    PAYMENT_METHOD_CHOICES,
)


ZERO_MONEY = Decimal('0.00')
OPERATIONAL_CODE_WIDTH = 6
OPERATIONAL_CODE_PREFIXES = {
    'project': 'PRJ',
    'donation': 'DON',
    'fund_allocation': 'ASG',
    'expense': 'GAS',
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


class Project(models.Model):
    class Status(models.TextChoices):
        PLANNED = 'planned', _('Planificado')
        ACTIVE = 'active', _('Activo')
        SUSPENDED = 'suspended', _('Suspendido')
        CLOSED = 'closed', _('Cerrado')
        ANNULLED = 'annulled', _('Anulado')

    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=180)
    description = models.TextField(blank=True)
    objective = models.TextField(blank=True)
    responsible_unit = models.CharField(max_length=120, blank=True)
    location = models.CharField(max_length=160, blank=True)
    estimated_budget = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO_MONEY)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLANNED)
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

    class Meta:
        ordering = ['code']
        verbose_name = _('proyecto')
        verbose_name_plural = _('proyectos')
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
        return self.allocations.exclude(status=FundAllocation.Status.ANNULLED).aggregate(
            total=Sum('amount')
        )['total'] or ZERO_MONEY

    @property
    def executed_amount(self):
        return sum((allocation.executed_amount for allocation in self.allocations.all()), ZERO_MONEY)


class ProjectUpdate(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', _('Borrador')
        PUBLISHED = 'published', _('Publicado')

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='updates')
    title = models.CharField(max_length=200)
    description = models.TextField()
    update_date = models.DateField(_('fecha del avance'), default=timezone.localdate)
    progress_percentage = models.PositiveSmallIntegerField(
        _('porcentaje de progreso'),
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.DRAFT)
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
        constraints = [
            models.CheckConstraint(
                condition=models.Q(progress_percentage__gte=0, progress_percentage__lte=100),
                name='project_update_progress_between_0_and_100',
            ),
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
    # POST: updates only attachments belonging to DRAFT updates; published attachments remain unchanged.
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
    # POST: deletes only attachments belonging to DRAFT updates; published attachments remain unchanged.
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
        PRE: the attachment has a valid parent update and ordinary mutation is requested.
        POST: persists only an attachment associated with a DRAFT update.
        """
        self._ensure_parent_update_is_editable()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """
        PRE: the attachment is persisted and ordinary deletion is requested.
        POST: deletes only an attachment whose parent update remains DRAFT.
        """
        self._ensure_parent_update_is_editable()
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
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default='USD')
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
        # PRE: amount may be absent when field validation has already rejected submitted data.
        # POST: rejects non-positive numeric amounts without masking field errors for missing or invalid values.
        if self.amount is not None and self.amount <= ZERO_MONEY:
            raise ValidationError({'amount': _('El monto de la donación debe ser positivo.')})

    @property
    def total_assigned(self):
        return self.allocations.exclude(status=FundAllocation.Status.ANNULLED).aggregate(
            total=Sum('amount')
        )['total'] or ZERO_MONEY

    @property
    def available_balance(self):
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
        return self.expenses.exclude(
            status__in=Expense.non_executing_statuses()
        ).aggregate(total=Sum('amount'))['total'] or ZERO_MONEY

    @property
    def available_balance(self):
        balance = self.amount - self.executed_amount
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
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default='USD')
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
