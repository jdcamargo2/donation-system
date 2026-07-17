(() => {
    const ACTION_SELECTOR = '[data-confirm-action]';
    const VARIANTS = {
        danger: { icon: 'warning', confirmButtonColor: '#b42318' },
        warning: { icon: 'question', confirmButtonColor: '#b7791f' },
        primary: { icon: 'question', confirmButtonColor: '#1f5fbf' },
    };

    const hasSweetAlert = () => typeof window.Swal?.fire === 'function';

    // PRE: trigger declares the id of a POST form through data-confirm-form.
    // POST: returns that form only when it is a valid POST fallback for the trigger action.
    const getConfirmationForm = (trigger) => {
        const formId = trigger.dataset.confirmForm;
        const form = formId ? document.getElementById(formId) : null;
        if (!(form instanceof HTMLFormElement) || form.method.toLowerCase() !== 'post') {
            return null;
        }
        const formUrl = new URL(form.action, window.location.href).href;
        const triggerUrl = trigger.href
            ? new URL(trigger.href, window.location.href).href
            : formUrl;
        return triggerUrl === formUrl ? form : null;
    };

    // PRE: trigger is an enabled declarative confirmation action backed by a POST form.
    // POST: submits the real form only after confirmation and restores focus on cancellation.
    const confirmAndSubmit = async (trigger, form) => {
        const variant = VARIANTS[trigger.dataset.confirmVariant] || VARIANTS.primary;
        const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        const result = await window.Swal.fire({
            title: trigger.dataset.confirmTitle,
            text: trigger.dataset.confirmText,
            icon: variant.icon,
            showCancelButton: true,
            confirmButtonText: trigger.dataset.confirmConfirmLabel,
            cancelButtonText: 'Cancelar',
            confirmButtonColor: variant.confirmButtonColor,
            focusCancel: true,
            returnFocus: false,
            animation: !reduceMotion,
        });
        if (result.isConfirmed) {
            form.requestSubmit();
            return;
        }
        trigger.focus({ preventScroll: true });
    };

    document.addEventListener('click', (event) => {
        const trigger = event.target.closest?.(ACTION_SELECTOR);
        if (!trigger || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey) {
            return;
        }
        const form = getConfirmationForm(trigger);
        if (!form || !hasSweetAlert()) return;
        event.preventDefault();
        confirmAndSubmit(trigger, form);
    });
})();
