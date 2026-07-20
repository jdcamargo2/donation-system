class KoboIntegrationError(Exception):
    """Error base de la integración KoboToolbox."""


class KoboConfigurationError(KoboIntegrationError):
    """La integración no tiene la configuración necesaria."""


class KoboAuthenticationError(KoboIntegrationError):
    """Falló la autenticación o validación del origen."""


class KoboAuthorizationError(KoboAuthenticationError):
    """Las credenciales son válidas, pero no autorizan la operación remota."""


class KoboNotFoundError(KoboIntegrationError):
    """El recurso remoto solicitado no existe."""


class KoboRateLimitError(KoboIntegrationError):
    """Kobo limitó temporalmente la solicitud."""


class KoboTransientRemoteError(KoboIntegrationError):
    """Un fallo remoto temporal agotó los reintentos seguros."""


class KoboPermanentRemoteError(KoboIntegrationError):
    """La solicitud remota falló de forma no reintentable."""


class KoboTimeoutError(KoboTransientRemoteError):
    """La solicitud remota agotó su tiempo de espera."""


class KoboInvalidResponseError(KoboIntegrationError):
    """La respuesta remota no cumple el contrato esperado."""


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
