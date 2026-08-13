"""Expense Request lifecycle services (ER2A–ER2E): create through fulfill and annul."""

from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .financials import get_allocation_reserved_amount, quantize_money
from .models import (
    AuditLog,
    Donation,
    Expense,
    ExpenseRequest,
    ExpenseRequestAttachment,
    ExpenseRequestEvent,
    FundAllocation,
    Project,
    SupportingDocument,
    ZERO_MONEY,
)
from .services import (
    _compensate_stored_upload,
    _create_expense_locked,
    _store_upload_for_field,
    _stored_upload,
    ensure_operational_entity_is_editable,
    log_action,
    log_create,
    log_delete,
    validate_terminal_reason,
    _require_transition_actor,
    _validate_operating_currency,
    _validate_project_is_active_for_execution_or_updates,
)


class ExpenseRequestStateError(ValidationError):
    """Raised when an ExpenseRequest is not in the required lifecycle state."""


class ExpenseRequestPermissionError(PermissionDenied):
    """Raised when the actor lacks permission or ownership for a request action."""


class ExpenseRequestBalanceError(ValidationError):
    """Raised when approval cannot reserve funds against allocation capacity."""


class ExpenseRequestAlreadyDecidedError(ValidationError):
    """Raised when a second decision is attempted on a non-pending request."""


class ExpenseRequestAmountError(ValidationError):
    """Raised when a requested amount is not a positive operational amount."""


class ExpenseRequestAlreadyFulfilledError(ValidationError):
    """Raised when fulfillment is attempted on an already-linked or fulfilled request."""


class ExpenseRequestReservationError(ValidationError):
    """Raised when reservation metadata is missing or malformed for fulfillment/annulment."""


def _ensure_request_permission(actor, codename):
    """
    PRE: actor is the proposed authenticated user; codename is an operations permission.
    POST: returns only when actor.has_perm('operations.<codename>'); otherwise raises.
    """
    _require_transition_actor(actor)
    if not actor.has_perm(f'operations.{codename}'):
        raise ExpenseRequestPermissionError(
            _('No tiene permiso para realizar esta acción sobre la solicitud de gasto.')
        )


def _ensure_request_is_pending(request):
    """
    PRE: request is a locked ExpenseRequest row.
    POST: returns only for PENDING_DECISION; otherwise raises state/decision errors.
    """
    if request.status == ExpenseRequest.Status.PENDING_DECISION:
        return
    if request.status in {
        ExpenseRequest.Status.APPROVED_RESERVED,
        ExpenseRequest.Status.DENIED,
    }:
        raise ExpenseRequestAlreadyDecidedError(
            {'status': _('La solicitud ya fue decidida y no admite esta acción.')}
        )
    raise ExpenseRequestStateError(
        {'status': _('Solo las solicitudes pendientes de decisión admiten esta acción.')}
    )


def _ensure_actor_is_requester(request, actor):
    """
    PRE: request and authenticated actor are available.
    POST: returns only when actor is the original requester.
    """
    if request.requested_by_id != actor.pk:
        raise ExpenseRequestPermissionError(
            _('Solo el solicitante original puede realizar esta acción.')
        )


def _validate_request_amount(amount):
    """
    PRE: amount is the proposed requested_amount.
    POST: returns a quantized positive Decimal or raises ExpenseRequestAmountError.
    """
    try:
        normalized = quantize_money(amount)
    except Exception as exc:
        raise ExpenseRequestAmountError(
            {'requested_amount': _('El monto solicitado debe ser un valor monetario válido.')}
        ) from exc
    if normalized <= ZERO_MONEY:
        raise ExpenseRequestAmountError(
            {'requested_amount': _('El monto solicitado debe ser positivo.')}
        )
    return normalized


def _validate_request_purpose(purpose):
    clean = purpose.strip() if isinstance(purpose, str) else ''
    if not clean:
        raise ValidationError({'purpose': _('El propósito de la solicitud es obligatorio.')})
    return clean


def _validate_request_allocation(allocation):
    """
    PRE: allocation is locked with donation and project relations available.
    POST: returns only when allocation, donation, and project remain operational.
    """
    ensure_operational_entity_is_editable(allocation)
    donation = allocation.donation
    if donation.status == Donation.Status.ANNULLED:
        raise ValidationError(
            {'fund_allocation': _('La donación de la asignación no admite solicitudes operativas.')}
        )
    if donation.status != Donation.Status.RECEIVED:
        raise ValidationError(
            {
                'fund_allocation': _(
                    'Solo asignaciones financiadas por donaciones recibidas admiten solicitudes.'
                )
            }
        )
    _validate_operating_currency(donation.currency, 'fund_allocation')
    _validate_project_is_active_for_execution_or_updates(allocation.project)


def _allocation_available_balance(allocation, *, exclude_request_id=None) -> Decimal:
    """
    PRE: allocation is locked; optional exclude_request_id omits one reservation.
    POST: returns clamped available balance = amount - executed - active reservations.
    """
    executed = allocation.expenses.exclude(
        status__in=Expense.non_executing_statuses()
    ).aggregate(total=Sum('amount'))['total'] or ZERO_MONEY
    reserved = get_allocation_reserved_amount(
        allocation,
        exclude_request_id=exclude_request_id,
    )
    return max(allocation.amount - executed - reserved, ZERO_MONEY)


def _json_safe_money(value):
    if value is None:
        return None
    return str(quantize_money(value))


def _record_expense_request_event(
    *,
    expense_request,
    event_type,
    actor,
    from_status,
    to_status,
    allocation_balance_before,
    allocation_balance_after,
    reason='',
    expense=None,
    metadata=None,
    reserved_amount=None,
    executed_amount=None,
    released_amount=None,
):
    """
    PRE: surrounding transaction is open; snapshot amounts are Decimal-compatible.
    POST: inserts one append-only ExpenseRequestEvent with JSON-safe metadata.
    """
    return ExpenseRequestEvent.objects.create(
        expense_request=expense_request,
        event_type=event_type,
        actor=actor,
        from_status=from_status or '',
        to_status=to_status or '',
        requested_amount=quantize_money(expense_request.requested_amount),
        reserved_amount=(
            quantize_money(reserved_amount)
            if reserved_amount is not None
            else None
        ),
        executed_amount=(
            quantize_money(executed_amount)
            if executed_amount is not None
            else None
        ),
        released_amount=(
            quantize_money(released_amount)
            if released_amount is not None
            else None
        ),
        allocation_balance_before=quantize_money(allocation_balance_before),
        allocation_balance_after=quantize_money(allocation_balance_after),
        reason=reason or '',
        expense=expense,
        metadata=metadata or {},
    )


def _record_expense_request_audit(
    *,
    actor,
    action,
    expense_request,
    summary,
):
    """
    PRE: expense_request is persisted; summary is human-readable and non-secret.
    POST: creates one AuditLog entry via the authorized helper.
    """
    return log_action(actor, action, expense_request, summary)


def _lock_allocation_parents(allocation_id):
    """
    PRE: allocation_id identifies a FundAllocation.
    POST: locks Donation then FundAllocation then Project in canonical order; returns locked allocation.
    """
    allocation_reference = FundAllocation.objects.only('donation_id', 'project_id').get(
        pk=allocation_id
    )
    Donation.objects.select_for_update().get(pk=allocation_reference.donation_id)
    locked_allocation = (
        FundAllocation.objects.select_for_update()
        .select_related('donation', 'project')
        .get(pk=allocation_id)
    )
    Project.objects.select_for_update().get(pk=locked_allocation.project_id)
    return locked_allocation


def _lock_allocations_by_ids(allocation_ids):
    """
    PRE: allocation_ids is a non-empty set of FundAllocation primary keys.
    POST: locks related donations then allocations then projects by ascending pk; returns {pk: allocation}.
    """
    donation_ids = FundAllocation.objects.filter(pk__in=allocation_ids).values_list(
        'donation_id', flat=True
    )
    list(Donation.objects.select_for_update().filter(pk__in=donation_ids).order_by('pk'))
    locked = {
        item.pk: item
        for item in FundAllocation.objects.select_for_update()
        .filter(pk__in=allocation_ids)
        .select_related('donation', 'project')
        .order_by('pk')
    }
    project_ids = {item.project_id for item in locked.values()}
    list(Project.objects.select_for_update().filter(pk__in=project_ids).order_by('pk'))
    return locked



# PRE: fund_allocation is operational; actor has add_expenserequest; amount/purpose/date are proposed.
# POST: creates PENDING_DECISION request with SGS code, CREATED event, and AuditLog; no reservation.
def create_expense_request(
    *,
    fund_allocation,
    requested_amount,
    purpose,
    requested_date,
    actor,
) -> ExpenseRequest:
    _ensure_request_permission(actor, 'add_expenserequest')
    amount = _validate_request_amount(requested_amount)
    clean_purpose = _validate_request_purpose(purpose)

    with transaction.atomic():
        locked_allocation = _lock_allocation_parents(fund_allocation.pk)
        _validate_request_allocation(locked_allocation)
        balance = _allocation_available_balance(locked_allocation)

        expense_request = ExpenseRequest(
            fund_allocation=locked_allocation,
            requested_by=actor,
            requested_amount=amount,
            purpose=clean_purpose,
            requested_date=requested_date,
            status=ExpenseRequest.Status.PENDING_DECISION,
        )
        expense_request.full_clean()
        expense_request.save()

        _record_expense_request_event(
            expense_request=expense_request,
            event_type=ExpenseRequestEvent.EventType.CREATED,
            actor=actor,
            from_status='',
            to_status=ExpenseRequest.Status.PENDING_DECISION,
            allocation_balance_before=balance,
            allocation_balance_after=balance,
            metadata={
                'allocation_code': locked_allocation.code,
                'requested_amount': _json_safe_money(amount),
            },
        )
        _record_expense_request_audit(
            actor=actor,
            action=AuditLog.Action.CREATED,
            expense_request=expense_request,
            summary=_(
                'Solicitud %(code)s creada por %(actor)s sobre asignación %(allocation)s '
                'por %(amount)s USD (pendiente de decisión; sin reserva).'
            )
            % {
                'code': expense_request.code,
                'actor': actor.get_username(),
                'allocation': locked_allocation.code,
                'amount': amount,
            },
        )
        return ExpenseRequest.objects.select_related(
            'fund_allocation',
            'requested_by',
        ).get(pk=expense_request.pk)


# PRE: request is PENDING_DECISION owned by actor with change_expenserequest.
# POST: updates editable fields, writes UPDATED event with diffs and AuditLog; no reservation change.
def update_expense_request(
    request,
    *,
    fund_allocation,
    requested_amount,
    purpose,
    requested_date,
    actor,
) -> ExpenseRequest:
    _ensure_request_permission(actor, 'change_expenserequest')
    amount = _validate_request_amount(requested_amount)
    clean_purpose = _validate_request_purpose(purpose)

    with transaction.atomic():
        request_reference = ExpenseRequest.objects.only('fund_allocation_id').get(pk=request.pk)
        allocation_ids = {request_reference.fund_allocation_id, fund_allocation.pk}
        locked_allocations = _lock_allocations_by_ids(allocation_ids)
        locked_request = ExpenseRequest.objects.select_for_update().select_related(
            'fund_allocation',
            'requested_by',
        ).get(pk=request.pk)
        _ensure_request_is_pending(locked_request)
        _ensure_actor_is_requester(locked_request, actor)

        previous_allocation = locked_allocations[locked_request.fund_allocation_id]
        new_allocation = locked_allocations[fund_allocation.pk]
        _validate_request_allocation(new_allocation)

        previous = {
            'allocation_code': previous_allocation.code,
            'allocation_id': previous_allocation.pk,
            'requested_amount': _json_safe_money(locked_request.requested_amount),
            'purpose': locked_request.purpose,
            'requested_date': locked_request.requested_date.isoformat(),
        }
        balance_before = _allocation_available_balance(previous_allocation)
        balance_after_source = balance_before
        balance_target = _allocation_available_balance(new_allocation)

        locked_request.fund_allocation = new_allocation
        locked_request.requested_amount = amount
        locked_request.purpose = clean_purpose
        locked_request.requested_date = requested_date
        locked_request.full_clean()
        locked_request.save(
            update_fields=(
                'fund_allocation',
                'requested_amount',
                'purpose',
                'requested_date',
                'updated_at',
            )
        )

        new_snapshot = {
            'allocation_code': new_allocation.code,
            'allocation_id': new_allocation.pk,
            'requested_amount': _json_safe_money(amount),
            'purpose': clean_purpose,
            'requested_date': requested_date.isoformat(),
        }
        metadata = {
            'previous': previous,
            'new': new_snapshot,
        }
        if previous_allocation.pk != new_allocation.pk:
            metadata['source_allocation_balance'] = _json_safe_money(balance_after_source)
            metadata['target_allocation_balance'] = _json_safe_money(balance_target)

        # No reservation exists while pending: balances are unchanged by the edit itself.
        snapshot_balance = (
            balance_target if previous_allocation.pk != new_allocation.pk else balance_before
        )
        _record_expense_request_event(
            expense_request=locked_request,
            event_type=ExpenseRequestEvent.EventType.UPDATED,
            actor=actor,
            from_status=ExpenseRequest.Status.PENDING_DECISION,
            to_status=ExpenseRequest.Status.PENDING_DECISION,
            allocation_balance_before=snapshot_balance,
            allocation_balance_after=snapshot_balance,
            metadata=metadata,
        )
        _record_expense_request_audit(
            actor=actor,
            action=AuditLog.Action.UPDATED,
            expense_request=locked_request,
            summary=_(
                'Solicitud %(code)s actualizada por %(actor)s. '
                'Asignación %(prev_alloc)s→%(new_alloc)s; monto %(prev_amount)s→%(new_amount)s USD.'
            )
            % {
                'code': locked_request.code,
                'actor': actor.get_username(),
                'prev_alloc': previous['allocation_code'],
                'new_alloc': new_snapshot['allocation_code'],
                'prev_amount': previous['requested_amount'],
                'new_amount': new_snapshot['requested_amount'],
            },
        )
        return ExpenseRequest.objects.select_related(
            'fund_allocation',
            'requested_by',
        ).get(pk=locked_request.pk)


# PRE: request is PENDING_DECISION owned by actor with withdraw_expenserequest; reason is mandatory.
# POST: transitions to WITHDRAWN with terminal metadata, event, and audit; no financial effect.
def withdraw_expense_request(request, *, reason, actor) -> ExpenseRequest:
    _ensure_request_permission(actor, 'withdraw_expenserequest')
    clean_reason = validate_terminal_reason(reason)

    with transaction.atomic():
        allocation_id = ExpenseRequest.objects.only('fund_allocation_id').get(pk=request.pk).fund_allocation_id
        locked_allocation = _lock_allocation_parents(allocation_id)
        locked_request = ExpenseRequest.objects.select_for_update().get(pk=request.pk)
        _ensure_request_is_pending(locked_request)
        _ensure_actor_is_requester(locked_request, actor)

        balance = _allocation_available_balance(locked_allocation)
        now = timezone.now()
        locked_request.status = ExpenseRequest.Status.WITHDRAWN
        locked_request.terminal_reason = clean_reason
        locked_request.terminal_by = actor
        locked_request.terminal_at = now
        locked_request.full_clean()
        locked_request.save(
            update_fields=(
                'status',
                'terminal_reason',
                'terminal_by',
                'terminal_at',
                'updated_at',
            )
        )
        _record_expense_request_event(
            expense_request=locked_request,
            event_type=ExpenseRequestEvent.EventType.WITHDRAWN,
            actor=actor,
            from_status=ExpenseRequest.Status.PENDING_DECISION,
            to_status=ExpenseRequest.Status.WITHDRAWN,
            allocation_balance_before=balance,
            allocation_balance_after=balance,
            reason=clean_reason,
        )
        _record_expense_request_audit(
            actor=actor,
            action=AuditLog.Action.ANNULLED,
            expense_request=locked_request,
            summary=_(
                'Solicitud %(code)s retirada por %(actor)s sobre asignación %(allocation)s. '
                'Motivo: %(reason)s'
            )
            % {
                'code': locked_request.code,
                'actor': actor.get_username(),
                'allocation': locked_allocation.code,
                'reason': clean_reason,
            },
        )
        return ExpenseRequest.objects.select_related(
            'fund_allocation',
            'requested_by',
            'terminal_by',
        ).get(pk=locked_request.pk)


# PRE: actor has decide_expenserequest; request is PENDING_DECISION; decision_note is mandatory.
# POST: transitions to DENIED with decision metadata, event, and audit; no reservation or balance change.
def deny_expense_request(request, *, decision_note, actor) -> ExpenseRequest:
    _ensure_request_permission(actor, 'decide_expenserequest')
    clean_note = validate_terminal_reason(decision_note)

    with transaction.atomic():
        allocation_id = ExpenseRequest.objects.only('fund_allocation_id').get(pk=request.pk).fund_allocation_id
        locked_allocation = _lock_allocation_parents(allocation_id)
        locked_request = ExpenseRequest.objects.select_for_update().get(pk=request.pk)
        _ensure_request_is_pending(locked_request)

        balance = _allocation_available_balance(locked_allocation)
        now = timezone.now()
        locked_request.status = ExpenseRequest.Status.DENIED
        locked_request.decision_note = clean_note
        locked_request.decided_by = actor
        locked_request.decided_at = now
        locked_request.full_clean()
        locked_request.save(
            update_fields=(
                'status',
                'decision_note',
                'decided_by',
                'decided_at',
                'updated_at',
            )
        )
        _record_expense_request_event(
            expense_request=locked_request,
            event_type=ExpenseRequestEvent.EventType.DENIED,
            actor=actor,
            from_status=ExpenseRequest.Status.PENDING_DECISION,
            to_status=ExpenseRequest.Status.DENIED,
            allocation_balance_before=balance,
            allocation_balance_after=balance,
            reason=clean_note,
        )
        _record_expense_request_audit(
            actor=actor,
            action=AuditLog.Action.REJECTED,
            expense_request=locked_request,
            summary=_(
                'Solicitud %(code)s denegada por %(actor)s sobre asignación %(allocation)s '
                '(%(amount)s USD). Motivo: %(reason)s'
            )
            % {
                'code': locked_request.code,
                'actor': actor.get_username(),
                'allocation': locked_allocation.code,
                'amount': locked_request.requested_amount,
                'reason': clean_note,
            },
        )
        return ExpenseRequest.objects.select_related(
            'fund_allocation',
            'requested_by',
            'decided_by',
        ).get(pk=locked_request.pk)


# PRE: actor has decide_expenserequest; request is PENDING_DECISION; allocation has capacity.
# POST: atomically reserves requested_amount as APPROVED_RESERVED with APPROVED + RESERVATION_CREATED events.
def approve_expense_request(request, *, decision_note='', actor) -> ExpenseRequest:
    _ensure_request_permission(actor, 'decide_expenserequest')
    clean_note = decision_note.strip() if isinstance(decision_note, str) else ''

    with transaction.atomic():
        allocation_id = ExpenseRequest.objects.only('fund_allocation_id').get(pk=request.pk).fund_allocation_id
        locked_allocation = _lock_allocation_parents(allocation_id)
        locked_request = ExpenseRequest.objects.select_for_update().get(pk=request.pk)
        _ensure_request_is_pending(locked_request)
        _validate_request_allocation(locked_allocation)

        balance_before = _allocation_available_balance(
            locked_allocation,
            exclude_request_id=locked_request.pk,
        )
        requested = quantize_money(locked_request.requested_amount)
        if requested > balance_before:
            raise ExpenseRequestBalanceError(
                {
                    'requested_amount': _(
                        'El monto solicitado excede el saldo disponible de la asignación.'
                    )
                }
            )

        now = timezone.now()
        locked_request.status = ExpenseRequest.Status.APPROVED_RESERVED
        locked_request.decision_note = clean_note
        locked_request.decided_by = actor
        locked_request.decided_at = now
        locked_request.reserved_amount = requested
        locked_request.reserved_at = now
        locked_request.full_clean()
        locked_request.save(
            update_fields=(
                'status',
                'decision_note',
                'decided_by',
                'decided_at',
                'reserved_amount',
                'reserved_at',
                'updated_at',
            )
        )

        balance_after = _allocation_available_balance(locked_allocation)
        expected_after = quantize_money(balance_before - requested)
        if balance_after != expected_after:
            # Defensive invariant: reservation must reduce available by exactly requested_amount.
            raise ExpenseRequestBalanceError(
                {'status': _('La reserva no pudo consolidarse de forma consistente.')}
            )

        _record_expense_request_event(
            expense_request=locked_request,
            event_type=ExpenseRequestEvent.EventType.APPROVED,
            actor=actor,
            from_status=ExpenseRequest.Status.PENDING_DECISION,
            to_status=ExpenseRequest.Status.APPROVED_RESERVED,
            allocation_balance_before=balance_before,
            allocation_balance_after=balance_after,
            reason=clean_note,
            reserved_amount=requested,
            metadata={'decision_note': clean_note},
        )
        _record_expense_request_event(
            expense_request=locked_request,
            event_type=ExpenseRequestEvent.EventType.RESERVATION_CREATED,
            actor=actor,
            from_status=ExpenseRequest.Status.APPROVED_RESERVED,
            to_status=ExpenseRequest.Status.APPROVED_RESERVED,
            allocation_balance_before=balance_before,
            allocation_balance_after=balance_after,
            reserved_amount=requested,
            metadata={
                'reserved_amount': _json_safe_money(requested),
                'allocation_code': locked_allocation.code,
            },
        )
        _record_expense_request_audit(
            actor=actor,
            action=AuditLog.Action.VALIDATED,
            expense_request=locked_request,
            summary=_(
                'Solicitud %(code)s aprobada por %(actor)s; reserva de %(amount)s USD sobre '
                'asignación %(allocation)s. Saldo %(before)s→%(after)s USD.'
            )
            % {
                'code': locked_request.code,
                'actor': actor.get_username(),
                'amount': requested,
                'allocation': locked_allocation.code,
                'before': balance_before,
                'after': balance_after,
            },
        )
        return ExpenseRequest.objects.select_related(
            'fund_allocation',
            'requested_by',
            'decided_by',
        ).get(pk=locked_request.pk)


def _ensure_request_is_approved_reserved_for_fulfillment(request):
    """
    PRE: request is a locked ExpenseRequest row proposed for fulfillment.
    POST: returns only for APPROVED_RESERVED with usable reservation and no linked expense.
    """
    if request.status == ExpenseRequest.Status.FULFILLED or request.expense_id is not None:
        raise ExpenseRequestAlreadyFulfilledError(
            {'status': _('La solicitud ya tiene un gasto registrado y no admite cumplimiento.')}
        )
    if request.status != ExpenseRequest.Status.APPROVED_RESERVED:
        raise ExpenseRequestStateError(
            {
                'status': _(
                    'Solo las solicitudes aprobadas con reserva admiten el registro del gasto.'
                )
            }
        )
    if request.reserved_amount is None or request.reserved_amount <= ZERO_MONEY:
        raise ExpenseRequestReservationError(
            {
                'reserved_amount': _(
                    'La solicitud no tiene una reserva activa válida para cumplir.'
                )
            }
        )


def _validate_fulfillment_amount(amount, reserved_amount):
    """
    PRE: amount is the proposed final expense amount; reserved_amount is the locked reservation.
    POST: returns quantized amount when 0 < amount <= reserved_amount.
    """
    try:
        normalized = quantize_money(amount)
    except Exception as exc:
        raise ExpenseRequestAmountError(
            {'amount': _('El monto del gasto debe ser un valor monetario válido.')}
        ) from exc
    if normalized <= ZERO_MONEY:
        raise ExpenseRequestAmountError(
            {'amount': _('El monto del gasto debe ser positivo.')}
        )
    reserved = quantize_money(reserved_amount)
    if normalized > reserved:
        raise ExpenseRequestAmountError(
            {
                'amount': _(
                    'El monto del gasto no puede superar el monto reservado de la solicitud.'
                )
            }
        )
    return normalized


# PRE: actor has fulfill_expenserequest; request is APPROVED_RESERVED; support_file is mandatory;
#      0 < amount <= reserved_amount; remaining Expense fields are operational.
# POST: atomically creates Expense + SupportingDocument, links OneToOne, sets FULFILLED,
#       consumes reservation (partial release when amount < reserved), writes events and audits.
def fulfill_expense_request(
    request,
    *,
    expense_date,
    amount,
    reason,
    provider_or_recipient,
    payment_method,
    description,
    support_file,
    support_title,
    category,
    support_notes='',
    observations='',
    actor,
) -> ExpenseRequest:
    _ensure_request_permission(actor, 'fulfill_expenserequest')
    if not support_file:
        raise ValidationError({'support_file': _('Todo gasto debe tener un documento soporte.')})
    clean_reason = reason.strip() if isinstance(reason, str) else ''
    if not clean_reason:
        raise ValidationError({'reason': _('El motivo del gasto es obligatorio.')})
    clean_provider = (
        provider_or_recipient.strip()
        if isinstance(provider_or_recipient, str)
        else ''
    )
    if not clean_provider:
        raise ValidationError(
            {'provider_or_recipient': _('El proveedor o destinatario es obligatorio.')}
        )
    support_document = SupportingDocument(
        title=support_title or getattr(support_file, 'name', 'soporte')
    )

    with _stored_upload(
        support_document, 'document', support_file
    ) as stored_name, transaction.atomic():
        allocation_id = ExpenseRequest.objects.only('fund_allocation_id').get(
            pk=request.pk
        ).fund_allocation_id
        # Canonical order shared with approve: Donation → FundAllocation → Project → ExpenseRequest.
        locked_allocation = _lock_allocation_parents(allocation_id)
        locked_request = ExpenseRequest.objects.select_for_update().get(pk=request.pk)
        _ensure_request_is_approved_reserved_for_fulfillment(locked_request)
        _validate_request_allocation(locked_allocation)

        executed_amount = _validate_fulfillment_amount(
            amount,
            locked_request.reserved_amount,
        )
        reserved_amount = quantize_money(locked_request.reserved_amount)
        released_amount = quantize_money(reserved_amount - executed_amount)

        balance_before = _allocation_available_balance(locked_allocation)

        # Credit this reservation for Expense.clean() without excluding other active reservations.
        locked_allocation.annotated_reserved_amount = get_allocation_reserved_amount(
            locked_allocation,
            exclude_request_id=locked_request.pk,
        )
        expense = _create_expense_locked(
            allocation=locked_allocation,
            expense_date=expense_date,
            category=category,
            amount=executed_amount,
            reason=clean_reason,
            provider_or_recipient=clean_provider,
            payment_method=payment_method,
            description=description or '',
            observations=observations or '',
            stored_support_name=stored_name,
            support_title=support_title or getattr(support_file, 'name', 'soporte'),
            support_notes=support_notes or '',
            actor=actor,
            reservation_credit=reserved_amount,
            write_expense_audit=True,
        )
        if hasattr(locked_allocation, 'annotated_reserved_amount'):
            delattr(locked_allocation, 'annotated_reserved_amount')

        locked_request.expense = expense
        locked_request.status = ExpenseRequest.Status.FULFILLED
        locked_request.full_clean()
        locked_request.save(
            update_fields=(
                'expense',
                'status',
                'updated_at',
            )
        )

        balance_after = _allocation_available_balance(locked_allocation)
        expected_after = quantize_money(balance_before + released_amount)
        if balance_after != expected_after:
            raise ExpenseRequestBalanceError(
                {'status': _('El cumplimiento no pudo consolidarse de forma consistente.')}
            )

        common_metadata = {
            'request_code': locked_request.code,
            'expense_code': expense.code,
            'allocation_code': locked_allocation.code,
            'requested_amount': _json_safe_money(locked_request.requested_amount),
            'reserved_amount': _json_safe_money(reserved_amount),
            'executed_amount': _json_safe_money(executed_amount),
            'released_amount': _json_safe_money(released_amount),
        }
        _record_expense_request_event(
            expense_request=locked_request,
            event_type=ExpenseRequestEvent.EventType.EXPENSE_REGISTERED,
            actor=actor,
            from_status=ExpenseRequest.Status.APPROVED_RESERVED,
            to_status=ExpenseRequest.Status.FULFILLED,
            allocation_balance_before=balance_before,
            allocation_balance_after=balance_after,
            expense=expense,
            reserved_amount=reserved_amount,
            executed_amount=executed_amount,
            released_amount=released_amount,
            metadata=common_metadata,
        )
        _record_expense_request_event(
            expense_request=locked_request,
            event_type=ExpenseRequestEvent.EventType.RESERVATION_CONSUMED,
            actor=actor,
            from_status=ExpenseRequest.Status.APPROVED_RESERVED,
            to_status=ExpenseRequest.Status.FULFILLED,
            allocation_balance_before=balance_before,
            allocation_balance_after=balance_after,
            expense=expense,
            reserved_amount=reserved_amount,
            executed_amount=executed_amount,
            released_amount=released_amount,
            metadata={
                **common_metadata,
                'reservation_active_after': False,
                'narrative': (
                    'La reserva dejó de estar activa al registrar el gasto final.'
                ),
            },
        )
        if released_amount > ZERO_MONEY:
            _record_expense_request_event(
                expense_request=locked_request,
                event_type=ExpenseRequestEvent.EventType.UNUSED_RESERVATION_RELEASED,
                actor=actor,
                from_status=ExpenseRequest.Status.APPROVED_RESERVED,
                to_status=ExpenseRequest.Status.FULFILLED,
                allocation_balance_before=balance_before,
                allocation_balance_after=balance_after,
                expense=expense,
                reserved_amount=reserved_amount,
                executed_amount=executed_amount,
                released_amount=released_amount,
                metadata={
                    'released_amount': _json_safe_money(released_amount),
                    'expense_code': expense.code,
                    'request_code': locked_request.code,
                },
            )

        release_clause = ''
        if released_amount > ZERO_MONEY:
            release_clause = _(
                ' %(released)s USD de reserva no utilizada liberados.'
            ) % {'released': released_amount}
        _record_expense_request_audit(
            actor=actor,
            action=AuditLog.Action.EXECUTED,
            expense_request=locked_request,
            summary=_(
                'Solicitud %(request)s con reserva aprobada de %(reserved)s USD cumplida: '
                'gasto %(expense)s registrado por %(executed)s USD.%(release)s '
                'Saldo %(before)s→%(after)s USD.'
            )
            % {
                'request': locked_request.code,
                'reserved': reserved_amount,
                'expense': expense.code,
                'executed': executed_amount,
                'release': release_clause,
                'before': balance_before,
                'after': balance_after,
            },
        )
        return ExpenseRequest.objects.select_related(
            'fund_allocation',
            'requested_by',
            'decided_by',
            'expense',
        ).get(pk=locked_request.pk)


def _ensure_request_is_annullable(request):
    """
    PRE: request is a locked ExpenseRequest row proposed for administrative annulment.
    POST: returns only for PENDING_DECISION or APPROVED_RESERVED without linked expense.
    """
    if request.expense_id is not None:
        raise ExpenseRequestStateError(
            {
                'expense': _(
                    'Una solicitud con gasto enlazado no admite anulación administrativa.'
                )
            }
        )
    if request.status in {
        ExpenseRequest.Status.PENDING_DECISION,
        ExpenseRequest.Status.APPROVED_RESERVED,
    }:
        return
    if request.status == ExpenseRequest.Status.ANNULLED:
        raise ExpenseRequestStateError(
            {'status': _('La solicitud ya está anulada.')}
        )
    raise ExpenseRequestStateError(
        {
            'status': _(
                'Solo las solicitudes pendientes o aprobadas con reserva admiten anulación.'
            )
        }
    )


# PRE: actor has annul_expenserequest; request is PENDING_DECISION or APPROVED_RESERVED; reason mandatory.
# POST: transitions to ANNULLED; releases full reservation when previously approved; atomic events/audit.
def annul_expense_request(request, *, reason, actor) -> ExpenseRequest:
    _ensure_request_permission(actor, 'annul_expenserequest')
    clean_reason = validate_terminal_reason(reason)

    with transaction.atomic():
        allocation_id = ExpenseRequest.objects.only('fund_allocation_id').get(
            pk=request.pk
        ).fund_allocation_id
        locked_allocation = _lock_allocation_parents(allocation_id)
        locked_request = ExpenseRequest.objects.select_for_update().get(pk=request.pk)
        _ensure_request_is_annullable(locked_request)

        from_status = locked_request.status
        had_active_reservation = (
            from_status == ExpenseRequest.Status.APPROVED_RESERVED
            and locked_request.reserved_amount is not None
            and locked_request.reserved_amount > ZERO_MONEY
        )
        released_amount = (
            quantize_money(locked_request.reserved_amount)
            if had_active_reservation
            else ZERO_MONEY
        )
        balance_before = _allocation_available_balance(locked_allocation)

        now = timezone.now()
        locked_request.status = ExpenseRequest.Status.ANNULLED
        locked_request.terminal_reason = clean_reason
        locked_request.terminal_by = actor
        locked_request.terminal_at = now
        # Preserve decision/reservation history when annulling an approved reservation.
        locked_request.full_clean()
        locked_request.save(
            update_fields=(
                'status',
                'terminal_reason',
                'terminal_by',
                'terminal_at',
                'updated_at',
            )
        )

        balance_after = _allocation_available_balance(locked_allocation)
        expected_after = quantize_money(balance_before + released_amount)
        if balance_after != expected_after:
            raise ExpenseRequestBalanceError(
                {'status': _('La anulación no pudo consolidarse de forma consistente.')}
            )

        _record_expense_request_event(
            expense_request=locked_request,
            event_type=ExpenseRequestEvent.EventType.ANNULLED,
            actor=actor,
            from_status=from_status,
            to_status=ExpenseRequest.Status.ANNULLED,
            allocation_balance_before=balance_before,
            allocation_balance_after=balance_after,
            reason=clean_reason,
            reserved_amount=locked_request.reserved_amount,
            released_amount=released_amount if had_active_reservation else None,
            metadata={
                'from_status': from_status,
                'had_active_reservation': had_active_reservation,
                'released_amount': (
                    _json_safe_money(released_amount) if had_active_reservation else None
                ),
            },
        )
        if had_active_reservation:
            _record_expense_request_event(
                expense_request=locked_request,
                event_type=ExpenseRequestEvent.EventType.RESERVATION_RELEASED,
                actor=actor,
                from_status=from_status,
                to_status=ExpenseRequest.Status.ANNULLED,
                allocation_balance_before=balance_before,
                allocation_balance_after=balance_after,
                reason=clean_reason,
                reserved_amount=released_amount,
                released_amount=released_amount,
                metadata={
                    'released_amount': _json_safe_money(released_amount),
                    'allocation_code': locked_allocation.code,
                },
            )

        if had_active_reservation:
            summary = _(
                'Solicitud %(code)s anulada por %(actor)s; se liberó la reserva de '
                '%(released)s USD sobre asignación %(allocation)s. '
                'Saldo %(before)s→%(after)s USD. Motivo: %(reason)s'
            ) % {
                'code': locked_request.code,
                'actor': actor.get_username(),
                'released': released_amount,
                'allocation': locked_allocation.code,
                'before': balance_before,
                'after': balance_after,
                'reason': clean_reason,
            }
        else:
            summary = _(
                'Solicitud %(code)s anulada por %(actor)s sobre asignación %(allocation)s '
                '(sin efecto financiero). Motivo: %(reason)s'
            ) % {
                'code': locked_request.code,
                'actor': actor.get_username(),
                'allocation': locked_allocation.code,
                'reason': clean_reason,
            }
        _record_expense_request_audit(
            actor=actor,
            action=AuditLog.Action.ANNULLED,
            expense_request=locked_request,
            summary=summary,
        )
        return ExpenseRequest.objects.select_related(
            'fund_allocation',
            'requested_by',
            'decided_by',
            'terminal_by',
        ).get(pk=locked_request.pk)


def _validate_attachment_title(title: str) -> str:
    """
    PRE: title is the proposed attachment label from a validated form or service caller.
    POST: returns a non-empty trimmed title or raises a field ValidationError.
    """
    clean_title = (title or '').strip()
    if not clean_title:
        raise ValidationError({'title': _('El título del adjunto es obligatorio.')})
    if len(clean_title) > 160:
        raise ValidationError(
            {'title': _('El título del adjunto no puede exceder 160 caracteres.')}
        )
    return clean_title


def add_expense_request_attachments(
    *,
    expense_request_id: int,
    files,
    title: str,
    notes: str = '',
    actor,
) -> list[ExpenseRequestAttachment]:
    """
    PRE: expense_request_id is own PENDING_DECISION; files is a non-empty validated list;
         actor holds add_expenserequestattachment.
    POST: stores files outside the transaction, then creates one audited row per file
          atomically; compensates every stored name if relational confirmation fails.
    """
    _ensure_request_permission(actor, 'add_expenserequestattachment')
    if not files:
        raise ValidationError({'files': _('Debe seleccionar al menos un archivo.')})
    clean_title = _validate_attachment_title(title)
    clean_notes = (notes or '').strip()

    with transaction.atomic():
        locked_request = ExpenseRequest.objects.select_for_update().get(pk=expense_request_id)
        _ensure_actor_is_requester(locked_request, actor)
        _ensure_request_is_pending(locked_request)

    stored_uploads = []
    try:
        for uploaded_file in files:
            draft = ExpenseRequestAttachment(
                expense_request_id=expense_request_id,
                title=clean_title,
            )
            storage, stored_name = _store_upload_for_field(draft, 'file', uploaded_file)
            stored_uploads.append((storage, stored_name))

        with transaction.atomic():
            locked_request = ExpenseRequest.objects.select_for_update().get(
                pk=expense_request_id
            )
            _ensure_actor_is_requester(locked_request, actor)
            _ensure_request_is_pending(locked_request)
            created = []
            for _storage, stored_name in stored_uploads:
                attachment = ExpenseRequestAttachment.objects.create(
                    expense_request=locked_request,
                    file=stored_name,
                    title=clean_title,
                    notes=clean_notes,
                    uploaded_by=actor,
                )
                log_create(actor, attachment, _('Adjunto de solicitud agregado.'))
                created.append(attachment)
            return created
    except Exception:
        for storage, stored_name in stored_uploads:
            _compensate_stored_upload(storage, stored_name)
        raise


def delete_expense_request_attachment(
    *,
    expense_request_id: int,
    attachment_id: int,
    actor,
) -> int:
    """
    PRE: attachment belongs to expense_request_id; actor is the original requester;
         parent remains PENDING_DECISION; actor holds delete_expenserequestattachment.
    POST: audits and deletes the attachment (and its stored file) atomically; returns parent pk.
    """
    _ensure_request_permission(actor, 'delete_expenserequestattachment')
    with transaction.atomic():
        locked_request = ExpenseRequest.objects.select_for_update().get(pk=expense_request_id)
        _ensure_actor_is_requester(locked_request, actor)
        _ensure_request_is_pending(locked_request)
        attachment = ExpenseRequestAttachment.objects.select_for_update().get(
            pk=attachment_id,
            expense_request_id=expense_request_id,
        )
        log_delete(actor, attachment, _('Adjunto de solicitud eliminado.'))
        storage = attachment.file.storage if attachment.file else None
        stored_name = attachment.file.name if attachment.file else ''
        attachment.delete()
        if storage and stored_name:
            _compensate_stored_upload(storage, stored_name)
        return expense_request_id
