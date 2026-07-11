from django.utils.translation import gettext_lazy as _


OPERATING_CURRENCY = 'USD'


INSTITUTION_TYPE_CHOICES = [
    ('archdiocese', _('Arquidiócesis')),
    ('parish', _('Parroquia')),
    ('foundation', _('Fundación')),
    ('ngo', _('ONG')),
    ('company', _('Empresa')),
    ('public_entity', _('Entidad pública')),
    ('international_organization', _('Organización internacional')),
    ('other', _('Otra')),
]

DONATION_TYPE_CHOICES = [
    ('money', _('Dinero')),
    ('goods', _('Bienes')),
    ('kits', _('Kits')),
    ('food', _('Alimentos')),
    ('medicine', _('Medicinas')),
    ('equipment', _('Equipos')),
    ('tools', _('Herramientas')),
    ('services', _('Servicios')),
    ('materials', _('Materiales')),
]

OPERATING_CURRENCY_CHOICES = [
    (OPERATING_CURRENCY, _('Dólar estadounidense')),
]

CURRENCY_CHOICES = OPERATING_CURRENCY_CHOICES + [
    ('EUR', _('Euro')),
    ('VES', _('Bolívar venezolano')),
    ('COP', _('Peso colombiano')),
]

BUDGET_CATEGORY_CHOICES = [
    ('infrastructure_supply', _('Infraestructura y Abasto local')),
    ('health_psychosocial', _('Salud y apoyo psicosocial')),
    ('training_entrepreneurship', _('Formación y emprendimiento')),
    ('communication_networks', _('Redes de comunicación')),
    ('institutional_relations', _('Relaciones institucionales')),
]

EXPENSE_CATEGORY_CHOICES = [
    ('food', _('Alimentos')),
    ('medicine', _('Medicinas')),
    ('equipment', _('Equipos')),
    ('transport', _('Transporte')),
    ('logistics', _('Logística')),
    ('services', _('Servicios')),
    ('materials', _('Materiales')),
    ('other', _('Otra')),
]

PAYMENT_METHOD_CHOICES = [
    ('bank_transfer', _('Transferencia bancaria')),
    ('cash', _('Efectivo')),
    ('card', _('Tarjeta')),
    ('check', _('Cheque')),
    ('other', _('Otro')),
]
