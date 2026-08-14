"""Disconnected KoboToolbox capability surface for the public demo edition."""

DEMO_MESSAGE = (
    "KoboToolbox está soportado por SIGEDON, pero la conexión remota "
    "está desactivada en esta edición demo."
)

DEMO_PROVIDER = "KoboToolbox"
DEMO_STATUS = "Modo demostración"
DEMO_ENDPOINT = "https://kobo-demo.example.invalid"
DEMO_ORGANIZATION = "Demo Field Operations"
DEMO_ASSET_UID = "demo_asset_territorial_01"
DEMO_LAST_SYNC = "No disponible en edición demo"
DEMO_DEVICE_ID = "MANGO-FIELD-01"
DEMO_SUPPORTED_FORMS = (
    {"code": "Ficha 1", "title": "Registro territorial"},
    {"code": "Ficha 10", "title": "Microproyecto priorizado"},
    {"code": "Ficha 11", "title": "Seguimiento"},
)


def disconnected_demo_context() -> dict[str, object]:
    """
    PRE: the caller is rendering the internal Kobo capability page while
         KOBO_ENABLED is False.
    POST: returns static synthetic presentation data with no remote I/O,
          no operational actions, and no live Kobo identifiers.
    """
    return {
        "kobo_demo_mode": True,
        "kobo_demo_message": DEMO_MESSAGE,
        "kobo_demo_provider": DEMO_PROVIDER,
        "kobo_demo_status": DEMO_STATUS,
        "kobo_demo_endpoint": DEMO_ENDPOINT,
        "kobo_demo_organization": DEMO_ORGANIZATION,
        "kobo_demo_asset_uid": DEMO_ASSET_UID,
        "kobo_demo_last_sync": DEMO_LAST_SYNC,
        "kobo_demo_device_id": DEMO_DEVICE_ID,
        "kobo_demo_supported_forms": DEMO_SUPPORTED_FORMS,
    }
