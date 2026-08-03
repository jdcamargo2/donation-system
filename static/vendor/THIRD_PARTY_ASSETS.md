# Third-party UI assets (vendored)

SIGEDON serves core panel UI libraries from the repository via Django static
files. Runtime pages must not depend on public CDNs for these assets.

## Inventory

| Library | Version | Upstream | License | Local path | Runtime files |
| --- | --- | --- | --- | --- | --- |
| Bootstrap | 5.3.3 | https://github.com/twbs/bootstrap | MIT | `static/vendor/bootstrap/5.3.3/` | `css/bootstrap.min.css`, `js/bootstrap.bundle.min.js` (includes Popper), `LICENSE` |
| Bootstrap Icons | 1.11.3 | https://github.com/twbs/icons | MIT | `static/vendor/bootstrap-icons/1.11.3/` | `font/bootstrap-icons.min.css`, `font/fonts/bootstrap-icons.woff`, `font/fonts/bootstrap-icons.woff2`, `LICENSE` |
| SweetAlert2 | 11.26.25 | https://github.com/sweetalert2/sweetalert2 | MIT | `static/vendor/sweetalert2/11.26.25/` | `sweetalert2.min.css`, `sweetalert2.all.min.js` (global `Swal`), `LICENSE` |

Pinned SweetAlert2 `11.26.25` matches what jsDelivr served for the previous
template reference `sweetalert2@11` at the time of vendoring.

## Source artifacts

Official npm distributables (`bootstrap@5.3.3`, `bootstrap-icons@1.11.3`,
`sweetalert2@11.26.25`). Only minified runtime files and upstream license
notices are committed. Source maps are not committed; trailing
`sourceMappingURL` comments were removed from minified CSS/JS (including
`vendor/autonumeric/autoNumeric.min.js`) so Manifest/WhiteNoise collectstatic
does not fail on missing `.map` files and browsers do not request 404 maps.

## Already-local vendors (unchanged)

| Library | Local path |
| --- | --- |
| HTMX 2.0.10 | `static/vendor/htmx/` |
| flatpickr | `static/vendor/flatpickr/` |
| autoNumeric | `static/vendor/autonumeric/` |

## Upgrade procedure

1. Obtain the official distribution for the target version.
2. Verify version and license.
3. Replace the versioned directory under `static/vendor/<library>/<version>/`.
4. Update `{% static %}` references in `templates/base.html` and
   `templates/registration/auth_base.html`.
5. Update sentinels in `core/management/commands/verify_deployment_assets.py`
   and this file.
6. Run `verify_deployment_assets` and focused UI/static resilience tests.
7. Remove the unused previous version directory.

Do not introduce a frontend build pipeline for these assets.
