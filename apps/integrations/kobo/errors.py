class KoboIntegrationError(Exception):
    """Error base de la integración KoboToolbox."""


class KoboConfigurationError(KoboIntegrationError):
    """La integración no tiene la configuración necesaria."""


class KoboAuthenticationError(KoboIntegrationError):
    """Falló la autenticación o validación del origen."""


class KoboPayloadError(KoboIntegrationError):
    """El payload recibido no cumple el contrato esperado."""


class KoboNormalizationError(KoboPayloadError):
    """Un valor Kobo no satisface un contrato de normalización canónico."""


class KoboUnsupportedFormError(KoboPayloadError):
    """La ficha Kobo no pertenece al conjunto explícitamente soportado."""


class KoboProcessingError(KoboIntegrationError):
    """La submission no pudo completar su procesamiento."""


class KoboAttachmentError(KoboIntegrationError):
    """Un archivo adjunto no pudo validarse o procesarse."""
