(() => {
    const INITIALIZED_ATTR = 'data-file-upload-preview-initialized';
    const RASTER_MIME_TYPES = new Set([
        'image/jpeg',
        'image/png',
        'image/webp',
        'image/gif',
    ]);

    const DOCUMENT_MIME_TYPES = new Set([
        'application/msword',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'application/vnd.oasis.opendocument.text',
        'text/plain',
        'text/rtf',
        'application/rtf',
    ]);
    const DOCUMENT_EXTENSIONS = new Set([
        'doc', 'docx', 'odt', 'txt', 'rtf',
    ]);

    const SPREADSHEET_MIME_TYPES = new Set([
        'application/vnd.ms-excel',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'application/vnd.oasis.opendocument.spreadsheet',
        'text/csv',
        'application/csv',
    ]);
    const SPREADSHEET_EXTENSIONS = new Set([
        'xls', 'xlsx', 'ods', 'csv',
    ]);

    const ARCHIVE_MIME_TYPES = new Set([
        'application/zip',
        'application/x-zip-compressed',
        'application/x-rar-compressed',
        'application/vnd.rar',
        'application/x-7z-compressed',
        'application/x-tar',
        'application/gzip',
        'application/x-gzip',
    ]);
    const ARCHIVE_EXTENSIONS = new Set([
        'zip', 'rar', '7z', 'tar', 'gz', 'tgz',
    ]);

    const detectDataTransferSupport = () => {
        try {
            const dataTransfer = new DataTransfer();
            const probe = document.createElement('input');
            probe.type = 'file';
            probe.files = dataTransfer.files;
            return true;
        } catch (error) {
            return false;
        }
    };

    const DATA_TRANSFER_SUPPORTED = detectDataTransferSupport();

    const fileExtension = (filename) => {
        const parts = String(filename || '').split('.');
        if (parts.length < 2) return '';
        return parts.pop().toLowerCase();
    };

    const fileIdentity = (file) => (
        `${file.name}\0${file.size}\0${file.type}\0${file.lastModified}`
    );

    // PRE: bytes is a non-negative finite number of octets.
    // POST: returns Spanish human-readable size text (B/KB/MB/GB).
    const formatFileSize = (bytes) => {
        const size = Number(bytes);
        if (!Number.isFinite(size) || size < 0) return '0 B';
        if (size === 0) return '0 B';

        const units = [
            { unit: 'B', value: 1 },
            { unit: 'KB', value: 1024 },
            { unit: 'MB', value: 1024 ** 2 },
            { unit: 'GB', value: 1024 ** 3 },
        ];
        let selected = units[0];
        for (const candidate of units) {
            if (size >= candidate.value) selected = candidate;
        }
        const scaled = size / selected.value;
        const useDecimal = selected.unit !== 'B' && scaled < 10;
        const rounded = useDecimal
            ? Math.round(scaled * 10) / 10
            : Math.round(scaled);

        try {
            const formatted = new Intl.NumberFormat('es', {
                maximumFractionDigits: useDecimal ? 1 : 0,
                minimumFractionDigits: 0,
            }).format(rounded);
            return `${formatted} ${selected.unit}`;
        } catch (error) {
            const fallback = useDecimal
                ? String(rounded).replace('.', ',')
                : String(rounded);
            return `${fallback} ${selected.unit}`;
        }
    };

    const isRasterImage = (file) => {
        const mime = String(file.type || '').toLowerCase();
        if (mime === 'image/svg+xml') return false;
        if (RASTER_MIME_TYPES.has(mime)) return true;
        if (mime.startsWith('image/') && mime !== 'image/jpeg' && mime !== 'image/png'
            && mime !== 'image/webp' && mime !== 'image/gif') {
            return false;
        }
        return ['jpg', 'jpeg', 'png', 'webp', 'gif'].includes(fileExtension(file.name));
    };

    const classifyFile = (file) => {
        const mime = String(file.type || '').toLowerCase();
        const extension = fileExtension(file.name);

        if (isRasterImage(file)) {
            return {
                label: 'Imagen',
                iconClass: 'bi bi-file-earmark-image',
            };
        }
        if (mime === 'application/pdf' || extension === 'pdf') {
            return {
                label: 'PDF',
                iconClass: 'bi bi-file-earmark-pdf',
            };
        }
        if (DOCUMENT_MIME_TYPES.has(mime) || DOCUMENT_EXTENSIONS.has(extension)) {
            return {
                label: 'Documento',
                iconClass: 'bi bi-file-earmark-text',
            };
        }
        if (SPREADSHEET_MIME_TYPES.has(mime) || SPREADSHEET_EXTENSIONS.has(extension)) {
            return {
                label: 'Hoja de cálculo',
                iconClass: 'bi bi-file-earmark-spreadsheet',
            };
        }
        if (ARCHIVE_MIME_TYPES.has(mime) || ARCHIVE_EXTENSIONS.has(extension)) {
            return {
                label: 'Archivo comprimido',
                iconClass: 'bi bi-file-earmark-zip',
            };
        }
        return {
            label: 'Archivo',
            iconClass: 'bi bi-file-earmark',
        };
    };

    const canThumbnail = (file) => isRasterImage(file);

    const createIconElement = (iconClass) => {
        const icon = document.createElement('i');
        icon.className = iconClass;
        icon.setAttribute('aria-hidden', 'true');
        return icon;
    };

    const createPreviewInstance = (wrapper) => {
        if (!(wrapper instanceof Element)) return null;
        if (wrapper.getAttribute(INITIALIZED_ATTR) === 'true') return null;

        const input = wrapper.querySelector('input[type="file"]');
        const list = wrapper.querySelector('[data-file-upload-list]');
        const summary = wrapper.querySelector('[data-file-upload-summary]');
        if (!input || !list || !summary) return null;

        wrapper.setAttribute(INITIALIZED_ATTR, 'true');

        const isMultiple = Boolean(input.multiple);
        let pendingFiles = [];
        const objectUrls = new Map();
        let incrementalSyncEnabled = DATA_TRANSFER_SUPPORTED && isMultiple;
        let syncFailed = false;

        const announce = (message) => {
            summary.textContent = message;
        };

        const revokeObjectUrl = (file) => {
            const key = fileIdentity(file);
            const url = objectUrls.get(key);
            if (!url) return;
            URL.revokeObjectURL(url);
            objectUrls.delete(key);
        };

        const revokeAllObjectUrls = () => {
            objectUrls.forEach((url) => URL.revokeObjectURL(url));
            objectUrls.clear();
        };

        const clearPending = () => {
            revokeAllObjectUrls();
            pendingFiles = [];
        };

        const totalSize = () => pendingFiles.reduce((sum, file) => sum + (file.size || 0), 0);

        const updateSummary = (extraMessage) => {
            if (extraMessage) {
                announce(extraMessage);
                return;
            }
            if (!pendingFiles.length) {
                summary.textContent = '';
                return;
            }
            const count = pendingFiles.length;
            const sizeText = formatFileSize(totalSize());
            const countText = count === 1
                ? '1 archivo seleccionado'
                : `${count} archivos seleccionados`;
            summary.textContent = `${countText} · ${sizeText}`;
        };

        const syncInputFiles = () => {
            if (!isMultiple) {
                return true;
            }
            if (!incrementalSyncEnabled || syncFailed) {
                return false;
            }
            try {
                const dataTransfer = new DataTransfer();
                pendingFiles.forEach((file) => dataTransfer.items.add(file));
                input.files = dataTransfer.files;
                return true;
            } catch (error) {
                incrementalSyncEnabled = false;
                syncFailed = true;
                return false;
            }
        };

        const renderThumbnail = (thumbnail, file, classification) => {
            thumbnail.replaceChildren();
            if (!canThumbnail(file)) {
                thumbnail.appendChild(createIconElement(classification.iconClass));
                return;
            }

            let objectUrl;
            try {
                objectUrl = URL.createObjectURL(file);
            } catch (error) {
                thumbnail.appendChild(createIconElement(classification.iconClass));
                return;
            }

            objectUrls.set(fileIdentity(file), objectUrl);
            const image = document.createElement('img');
            image.alt = '';
            image.src = objectUrl;
            image.addEventListener('error', () => {
                revokeObjectUrl(file);
                thumbnail.replaceChildren();
                thumbnail.appendChild(createIconElement('bi bi-file-earmark-image'));
            }, { once: true });
            thumbnail.appendChild(image);
        };

        const focusAfterRemoval = (removedIndex) => {
            const removeButtons = list.querySelectorAll('[data-file-upload-remove]');
            if (removeButtons.length) {
                const next = removeButtons[Math.min(removedIndex, removeButtons.length - 1)];
                if (next) {
                    next.focus();
                    return;
                }
            }
            input.focus();
        };

        const removeAtIndex = (index) => {
            if (index < 0 || index >= pendingFiles.length) return;

            if (isMultiple && (!incrementalSyncEnabled || syncFailed)) {
                clearPending();
                input.value = '';
                render();
                updateSummary('Se quitaron todos los archivos seleccionados.');
                input.focus();
                return;
            }

            const [removed] = pendingFiles.splice(index, 1);
            if (removed) revokeObjectUrl(removed);

            if (!isMultiple) {
                input.value = '';
            } else {
                const synced = syncInputFiles();
                if (!synced) {
                    clearPending();
                    input.value = '';
                    render();
                    updateSummary('Se quitaron todos los archivos seleccionados.');
                    input.focus();
                    return;
                }
            }

            render();
            if (pendingFiles.length) {
                updateSummary();
            } else {
                summary.textContent = '';
            }
            focusAfterRemoval(index);
        };

        const buildItem = (file, index) => {
            const classification = classifyFile(file);
            const item = document.createElement('div');
            item.className = 'ops-file-upload-item';
            item.setAttribute('data-file-upload-item', '');
            item.setAttribute('role', 'listitem');

            const thumbnail = document.createElement('div');
            thumbnail.className = 'ops-file-upload-thumbnail';
            thumbnail.setAttribute('aria-hidden', 'true');
            renderThumbnail(thumbnail, file, classification);

            const meta = document.createElement('div');
            meta.className = 'ops-file-upload-meta';

            const name = document.createElement('span');
            name.className = 'ops-file-upload-name';
            name.textContent = file.name;
            name.title = file.name;

            const details = document.createElement('span');
            details.className = 'ops-file-upload-details';
            details.textContent = `${classification.label} · ${formatFileSize(file.size)}`;

            meta.appendChild(name);
            meta.appendChild(details);

            const removeButton = document.createElement('button');
            removeButton.type = 'button';
            removeButton.className = 'btn btn-sm btn-outline-secondary ops-file-upload-remove';
            removeButton.setAttribute('data-file-upload-remove', '');
            removeButton.setAttribute('aria-label', `Quitar archivo: ${file.name}`);
            removeButton.appendChild(createIconElement('bi bi-x-lg'));
            removeButton.addEventListener('click', () => removeAtIndex(index));

            item.appendChild(thumbnail);
            item.appendChild(meta);
            item.appendChild(removeButton);
            return item;
        };

        const render = () => {
            revokeAllObjectUrls();
            list.replaceChildren();
            pendingFiles.forEach((file, index) => {
                list.appendChild(buildItem(file, index));
            });
        };

        const adoptNativeSelection = () => {
            clearPending();
            pendingFiles = Array.from(input.files || []);
            render();
            updateSummary();
        };

        const mergeSelection = (selectedFiles) => {
            if (!isMultiple) {
                clearPending();
                pendingFiles = selectedFiles.slice(0, 1);
                render();
                updateSummary();
                return;
            }

            if (!incrementalSyncEnabled || syncFailed) {
                adoptNativeSelection();
                return;
            }

            const known = new Set(pendingFiles.map(fileIdentity));
            let duplicateAnnounced = false;
            selectedFiles.forEach((file) => {
                const identity = fileIdentity(file);
                if (known.has(identity)) {
                    duplicateAnnounced = true;
                    return;
                }
                known.add(identity);
                pendingFiles.push(file);
            });

            const synced = syncInputFiles();
            if (!synced) {
                adoptNativeSelection();
                return;
            }

            render();
            if (duplicateAnnounced) {
                updateSummary('El archivo ya estaba seleccionado.');
                window.setTimeout(() => updateSummary(), 1800);
            } else {
                updateSummary();
            }
        };

        const onChange = () => {
            const selected = Array.from(input.files || []);
            if (!selected.length) {
                if (!isMultiple || !incrementalSyncEnabled || syncFailed) {
                    clearPending();
                    render();
                    summary.textContent = '';
                }
                return;
            }
            mergeSelection(selected);
        };

        const onReset = () => {
            window.setTimeout(() => {
                clearPending();
                render();
                summary.textContent = '';
            }, 0);
        };

        const onPageHide = () => {
            revokeAllObjectUrls();
        };

        input.addEventListener('change', onChange);
        const form = input.closest('form');
        if (form) {
            form.addEventListener('reset', onReset);
        }
        window.addEventListener('pagehide', onPageHide);

        return {
            destroy() {
                clearPending();
                input.removeEventListener('change', onChange);
                if (form) form.removeEventListener('reset', onReset);
                window.removeEventListener('pagehide', onPageHide);
                wrapper.removeAttribute(INITIALIZED_ATTR);
            },
        };
    };

    const initializeFileUploadPreviews = (root = document) => {
        const scope = root instanceof Element || root instanceof Document ? root : document;
        const wrappers = scope.querySelectorAll
            ? scope.querySelectorAll('.ops-file-upload[data-file-upload-preview]')
            : [];
        wrappers.forEach((wrapper) => {
            createPreviewInstance(wrapper);
        });
    };

    const onReady = () => initializeFileUploadPreviews(document);

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', onReady, { once: true });
    } else {
        onReady();
    }

    document.body.addEventListener('htmx:afterSwap', (event) => {
        const target = event.detail && event.detail.target;
        if (target) {
            initializeFileUploadPreviews(target);
        }
    });
})();
