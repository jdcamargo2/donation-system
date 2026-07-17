(() => {
    const LOAD_MORE_SELECTOR = '[data-project-update-load-more]';
    const CHUNK_SELECTOR = '[data-project-update-chunk]';
    let pendingPage = null;

    // PRE: HTMX is requesting a declared project-update continuation link.
    // POST: remembers only the requested page needed to identify the inserted fragment.
    document.body.addEventListener('htmx:beforeRequest', (event) => {
        const trigger = event.detail.elt.closest?.(LOAD_MORE_SELECTOR);
        if (!trigger) return;
        pendingPage = trigger.dataset.updatePage;
    });

    // PRE: HTMX inserted the pending page without replacing prior update items.
    // POST: announces the batch and restores focus without changing scroll position.
    document.body.addEventListener('htmx:afterSwap', () => {
        if (!pendingPage) return;
        const chunk = document.querySelector(
            `${CHUNK_SELECTOR}[data-update-page="${pendingPage}"]`,
        );
        if (!chunk) return;
        const addedItems = Array.from(chunk.children).filter((element) => (
            element.matches('[data-project-update-id]')
        ));
        const liveRegion = document.getElementById('project-updates-live');
        if (liveRegion) {
            liveRegion.textContent = `Se añadieron ${addedItems.length} avances.`;
        }
        const focusTarget = addedItems[0]?.querySelector('a')
            || chunk.querySelector(LOAD_MORE_SELECTOR);
        // No animated scrolling is used; preventScroll preserves every motion preference.
        focusTarget?.focus({ preventScroll: true });
        pendingPage = null;
    });
})();
