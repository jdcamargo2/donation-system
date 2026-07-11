(() => {
    const moneyOptions = {
        digitGroupSeparator: '.',
        decimalCharacter: ',',
        decimalCharacterAlternative: '.',
        decimalPlaces: 2,
        allowDecimalPadding: 'always',
        minimumValue: '0',
        unformatOnSubmit: true,
        modifyValueOnWheel: false,
        emptyInputBehavior: 'null',
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

    const initializeMoneyInputs = () => {
        const moneyInputs = document.querySelectorAll('.money-input');
        if (!window.AutoNumeric || !moneyInputs.length) return;

        moneyInputs.forEach((input) => {
            if (isAutoNumericManaged(input)) return;
            try {
                new window.AutoNumeric(input, moneyOptions);
                input.dataset.autonumericInitialized = 'true';
            } catch (error) {
                try {
                    window.AutoNumeric.multiple([input], moneyOptions);
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
