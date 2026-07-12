(() => {
    const MONEY_OPTIONS = {
        digitGroupSeparator: '.',
        decimalCharacter: ',',
        decimalCharacterAlternative: '.',
        decimalPlaces: 2,
        decimalPlacesShownOnFocus: 2,
        decimalPlacesShownOnBlur: 2,
        minimumValue: '0',
        unformatOnSubmit: true,
        modifyValueOnWheel: false,
        showWarnings: true,
    };

    const isAutoNumericManaged = (input) => {
        if (!window.AutoNumeric || typeof window.AutoNumeric.isManagedByAutoNumeric !== 'function') {
            return input.dataset.autonumericInitialized === 'true';
        }
        return window.AutoNumeric.isManagedByAutoNumeric(input);
    };

    // PRE: value is empty, yyyy-mm-dd, or the visible dd/mm/yyyy operations date.
    // POST: returns a local Date for valid input or undefined so Flatpickr can reject it.
    const parseOperationalDate = (value) => {
        if (!value) return undefined;

        const spanishDate = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(value);
        const canonicalValue = spanishDate
            ? `${spanishDate[3]}-${spanishDate[2]}-${spanishDate[1]}`
            : value;
        return window.flatpickr.parseDate(canonicalValue, 'Y-m-d');
    };

    const initializeDatepickers = () => {
        const dateInputs = document.querySelectorAll('.datepicker');
        if (!window.flatpickr || !dateInputs.length) return;

        const spanishLocale = window.flatpickr.l10ns?.es || 'es';
        dateInputs.forEach((input) => {
            if (input.dataset.flatpickrInitialized === 'true') return;
            window.flatpickr(input, {
                dateFormat: 'Y-m-d',
                altInput: true,
                altFormat: 'd/m/Y',
                locale: spanishLocale,
                allowInput: true,
                disableMobile: true,
                parseDate: parseOperationalDate,
            });
            input.dataset.flatpickrInitialized = 'true';
        });
    };

    // PRE: root is a Document or Element containing optional monetary inputs.
    // POST: initializes each money input at most once and leaves the page usable when AutoNumeric is unavailable.
    const initializeMoneyInputs = (root = document) => {
        const moneyInputs = root === document
            ? document.querySelectorAll('.money-input')
            : root.querySelectorAll('.js-money-input');
        if (!window.AutoNumeric || !moneyInputs.length) return;

        moneyInputs.forEach((input) => {
            if (!input.matches('.js-money-input')) return;
            if (isAutoNumericManaged(input)) return;
            try {
                new window.AutoNumeric(input, MONEY_OPTIONS);
                input.dataset.autonumericInitialized = 'true';
            } catch (error) {
                try {
                    window.AutoNumeric.multiple([input], MONEY_OPTIONS);
                    input.dataset.autonumericInitialized = 'true';
                } catch (fallbackError) {
                    input.dataset.autonumericInitialized = 'false';
                }
            }
        });
    };

    const initializeOperationalForms = () => {
        initializeDatepickers();
        initializeMoneyInputs();
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initializeOperationalForms, { once: true });
    } else {
        initializeOperationalForms();
    }
})();
