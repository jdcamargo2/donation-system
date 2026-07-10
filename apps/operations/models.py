from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum
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


# PRE: model_class stores sequential user-facing codes with the provided prefix.
# POST: returns the first unused code in the format PREFIX-000001.
def _next_sequential_code(model_class, prefix):
    next_number = (model_class.objects.order_by('-id').values_list('id', flat=True).first() or 0) + 1
    while True:
        code = f'{prefix}-{next_number:06d}'
        if not model_class.objects.filter(code=code).exists():
            return code
        next_number += 1


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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['code']
        verbose_name = _('proyecto')
        verbose_name_plural = _('proyectos')

    def __str__(self):
        return f'{self.code} - {self.name}'

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = _next_sequential_code(Project, 'PRJ')
        super().save(*args, **kwargs)

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
        PENDING_REVIEW = 'pending_review', _('Pendiente de revisión')
        APPROVED = 'approved', _('Aprobado')
        REJECTED = 'rejected', _('Rechazado')

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='updates')
    title = models.CharField(max_length=200)
    description = models.TextField()
    evidence = models.FileField(upload_to='project_updates/', blank=True, null=True)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    reviewed_at = models.DateTimeField(blank=True, null=True)
    review_notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='created_project_updates',
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='reviewed_project_updates',
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = _('avance de proyecto')
        verbose_name_plural = _('avances de proyecto')

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
        if self.status in {self.Status.APPROVED, self.Status.REJECTED} and not (self.reviewed_by_id or self.reviewed_at):
            errors['status'] = _('Un avance aprobado o rechazado debe tener revisor o fecha de revisión.')
        if (
            self.project_id
            and not self.pk
            and hasattr(self.project, 'status')
            and self.project.status != Project.Status.ACTIVE
        ):
            errors['project'] = _('Solo se pueden crear avances para proyectos activos.')
        if errors:
            raise ValidationError(errors)


class Donation(models.Model):
    class Status(models.TextChoices):
        REGISTERED = 'registered', _('Registrada')
        COMMITTED = 'committed', _('Comprometida')
        RECEIVED = 'received', _('Recibida')
        PARTIALLY_ALLOCATED = 'partially_allocated', _('Asignada parcialmente')
        FULLY_ALLOCATED = 'fully_allocated', _('Asignada totalmente')
        CLOSED = 'closed', _('Cerrada')
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
    support_reference = models.CharField(max_length=180, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-received_date', '-created_at']
        verbose_name = _('donación')
        verbose_name_plural = _('donaciones')

    def __str__(self):
        return f'{self.code} - {self.donor}'

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = _next_sequential_code(Donation, 'DON')
        super().save(*args, **kwargs)

    def clean(self):
        if self.amount <= ZERO_MONEY:
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


class FundAllocation(models.Model):
    class Status(models.TextChoices):
        CREATED = 'created', _('Creada')
        ACTIVE = 'active', _('En ejecución')
        PARTIALLY_EXECUTED = 'partially_executed', _('Ejecutada parcialmente')
        FULLY_EXECUTED = 'fully_executed', _('Ejecutada totalmente')
        CLOSED = 'closed', _('Cerrada')
        ANNULLED = 'annulled', _('Anulada')

    donation = models.ForeignKey(Donation, on_delete=models.PROTECT, related_name='allocations')
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name='allocations')
    budget_category = models.CharField(max_length=40, choices=BUDGET_CATEGORY_CHOICES)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    responsible_person = models.CharField(max_length=120, blank=True)
    allocation_date = models.DateField()
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.CREATED)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-allocation_date', '-created_at']
        verbose_name = _('asignación de fondos')
        verbose_name_plural = _('asignaciones de fondos')

    def __str__(self):
        return f'{self.donation.code} -> {self.project.code}: {self.amount}'

    def clean(self):
        errors = {}
        if self.amount <= ZERO_MONEY:
            errors['amount'] = _('El monto de la asignación debe ser positivo.')
        if self.donation_id and self.amount > self.donation_available_before_this_allocation():
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
        return self.expenses.exclude(status=Expense.Status.ANNULLED).aggregate(total=Sum('amount'))['total'] or ZERO_MONEY

    @property
    def available_balance(self):
        balance = self.amount - self.executed_amount
        return max(balance, ZERO_MONEY)


class Expense(models.Model):
    class Status(models.TextChoices):
        REGISTERED = 'registered', _('Registrado')
        IN_REVIEW = 'in_review', _('En revisión')
        VALIDATED = 'validated', _('Validado')
        REJECTED = 'rejected', _('Rechazado')
        ANNULLED = 'annulled', _('Anulado')

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
    validated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='validated_expenses',
    )
    validated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-expense_date', '-created_at']
        verbose_name = _('gasto')
        verbose_name_plural = _('gastos')

    def __str__(self):
        return f'{self.reason} - {self.amount}'

    def clean(self):
        errors = {}
        if self.amount <= ZERO_MONEY:
            errors['amount'] = _('El monto del gasto debe ser positivo.')
        if self.allocation_id and self.amount > self.allocation_available_before_this_expense():
            errors['amount'] = _('El monto del gasto excede el saldo disponible de la asignación.')
        if errors:
            raise ValidationError(errors)

    # PRE: self.allocation is set and self.amount contains the proposed expense amount.
    # POST: returns the allocation balance that can be executed by this expense, including its current amount on updates.
    def allocation_available_before_this_expense(self):
        existing_amount = ZERO_MONEY
        if self.pk:
            existing_amount = Expense.objects.filter(pk=self.pk).values_list('amount', flat=True).first() or ZERO_MONEY
        return self.allocation.available_balance + existing_amount

    # PRE: the expense has been saved before checking existing related documents.
    # POST: returns True only when a validated expense has at least one supporting document.
    def has_required_support(self):
        return self.status != self.Status.VALIDATED or self.supporting_documents.exists()


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

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
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
