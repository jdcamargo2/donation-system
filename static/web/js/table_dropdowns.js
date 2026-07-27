(() => {
    "use strict";

    const TABLE_DROPDOWN_SELECTOR =
        '.table-responsive [data-bs-toggle="dropdown"]';

    function initTableDropdowns(root = document) {
        if (
            typeof bootstrap === "undefined" ||
            !bootstrap.Dropdown ||
            typeof bootstrap.Dropdown.getOrCreateInstance !== "function"
        ) {
            return;
        }

        const toggles = root.querySelectorAll(TABLE_DROPDOWN_SELECTOR);
        toggles.forEach((toggle) => {
            bootstrap.Dropdown.getOrCreateInstance(toggle, {
                boundary: "viewport",
                popperConfig(defaultConfig) {
                    return {
                        ...defaultConfig,
                        strategy: "fixed",
                    };
                },
            });
        });
    }

    document.addEventListener("DOMContentLoaded", () => {
        initTableDropdowns();
    });
})();
