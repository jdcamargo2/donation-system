(() => {
    const PANEL_SELECTOR = '[data-milestone-panel]';
    const CONFIRM_FORM_SELECTOR = 'form[data-confirm-kind]';
    let pendingFocus = null;

    const hasSweetAlert = () => typeof window.Swal?.fire === 'function';

    // PRE: xhr is a failed HTMX request for a milestone action.
    // POST: returns a concise server/domain message without exposing an entire HTML error page.
    const getResponseErrorMessage = (xhr) => {
        const responseText = xhr.responseText?.trim() || '';
        const looksLikeHtml = /^\s*<(?:!doctype|html)/i.test(responseText);
        if (responseText && !looksLikeHtml) return responseText;
        const messagesByStatus = {
            403: 'No tiene permisos o el estado del proyecto impide esta operación.',
            404: 'El hito solicitado ya no está disponible.',
            405: 'El método usado para esta operación no está permitido.',
        };
        return messagesByStatus[xhr.status] || 'No fue posible actualizar el hito.';
    };

    // PRE: text is a user-facing milestone result and icon is a SweetAlert2 severity.
    // POST: announces the result accessibly and uses a non-blocking toast when available.
    const announceResult = (text, icon = 'success') => {
        const feedback = document.querySelector(`${PANEL_SELECTOR} [data-milestone-feedback]`);
        if (feedback) feedback.textContent = text;
        if (!hasSweetAlert()) return;
        window.Swal.fire({
            toast: true,
            position: 'top-end',
            icon,
            title: text,
            showConfirmButton: false,
            timer: 3200,
            timerProgressBar: true,
        });
    };

    // PRE: HTMX and SweetAlert2 are both available on the project detail page.
    // POST: exposes inline confirmation forms while retaining server confirmation links otherwise.
    const synchronizeConfirmationFallbacks = () => {
        document.documentElement.classList.toggle(
            'htmx-milestones-enabled',
            Boolean(window.htmx && hasSweetAlert()),
        );
    };

    // PRE: the principal item/list swap finished and pendingFocus describes its initiating action.
    // POST: restores focus to the affected row, a surviving neighbor, or the section heading.
    const restoreMilestoneFocus = () => {
        const focusMilestoneId = pendingFocus?.action === 'delete'
            ? pendingFocus.nextMilestoneId || pendingFocus.previousMilestoneId
            : pendingFocus?.milestoneId;
        const milestone = focusMilestoneId
            ? document.querySelector(`[data-milestone-id="${focusMilestoneId}"]`)
            : null;
        const focusTarget = pendingFocus?.action === 'move'
            ? milestone?.querySelector('.ops-milestone-title')
            : milestone?.querySelector(
                '.ops-milestone-check-button, [data-bs-toggle="dropdown"], .ops-milestone-title',
            );
        const resolvedFocusTarget = focusTarget || document.querySelector('#project-milestones-title');
        if (resolvedFocusTarget) {
            if (!resolvedFocusTarget.matches('button, a, input, select, textarea')) {
                resolvedFocusTarget.setAttribute('tabindex', '-1');
            }
            resolvedFocusTarget.focus({ preventScroll: true });
        }
        pendingFocus = null;
    };

    document.addEventListener('DOMContentLoaded', synchronizeConfirmationFallbacks, {
        once: true,
    });

    document.body.addEventListener('htmx:beforeRequest', (event) => {
        const actionElement = event.detail.elt.closest?.('[data-milestone-action]');
        if (!actionElement) return;
        const milestone = actionElement.closest('[data-milestone-id]');
        const action = actionElement.dataset.milestoneAction;
        actionElement.closest(PANEL_SELECTOR)?.setAttribute('aria-busy', 'true');
        pendingFocus = {
            action,
            milestoneId: milestone?.dataset.milestoneId,
            nextMilestoneId: milestone?.nextElementSibling?.dataset.milestoneId,
            previousMilestoneId: milestone?.previousElementSibling?.dataset.milestoneId,
            expectedTargetId: action === 'complete' || action === 'reopen'
                ? milestone?.id
                : 'project-milestone-list',
        };
    });

    document.body.addEventListener('htmx:confirm', async (event) => {
        const form = event.detail.elt.closest?.(CONFIRM_FORM_SELECTOR);
        if (!form) return;
        event.preventDefault();
        if (!hasSweetAlert()) return;

        const confirmation = await window.Swal.fire({
            title: form.dataset.confirmTitle,
            text: form.dataset.confirmText,
            icon: form.dataset.confirmKind === 'delete' ? 'warning' : 'question',
            showCancelButton: true,
            confirmButtonText: form.dataset.confirmButton,
            cancelButtonText: 'Cancelar',
            confirmButtonColor: form.dataset.confirmKind === 'delete' ? '#b42318' : '#1f5fbf',
            focusCancel: true,
        });
        if (confirmation.isConfirmed) event.detail.issueRequest(true);
    });

    document.body.addEventListener('htmx:afterSwap', (event) => {
        const swappedId = event.detail.target?.id || event.detail.elt?.id;
        if (!pendingFocus || swappedId !== pendingFocus.expectedTargetId) return;
        synchronizeConfirmationFallbacks();
        restoreMilestoneFocus();
    });

    document.body.addEventListener('htmx:afterRequest', (event) => {
        if (!event.detail.elt.closest?.(PANEL_SELECTOR)) return;
        document.querySelector(PANEL_SELECTOR)?.removeAttribute('aria-busy');
    });

    document.body.addEventListener('milestoneToast', (event) => {
        const detail = event.detail || {};
        announceResult(detail.message || 'Hito actualizado.', detail.type || 'success');
    });

    document.body.addEventListener('htmx:responseError', (event) => {
        if (!event.detail.elt.closest?.(PANEL_SELECTOR)) return;
        announceResult(getResponseErrorMessage(event.detail.xhr), 'error');
        document.querySelector(PANEL_SELECTOR)?.removeAttribute('aria-busy');
        pendingFocus = null;
    });

    document.body.addEventListener('htmx:sendError', (event) => {
        if (!event.detail.elt.closest?.(PANEL_SELECTOR)) return;
        announceResult(
            'No fue posible conectar con el servidor. Verifique la red e inténtelo nuevamente.',
            'error',
        );
        document.querySelector(PANEL_SELECTOR)?.removeAttribute('aria-busy');
        pendingFocus = null;
    });
})();
