ROLE_SIGEDON_ADMIN = 'Administrador SIGEDON'
ROLE_FIELD_OPERATOR = 'Operador de campo'
ROLE_EXTERNAL_AUDITOR = 'Auditor externo'
ROLE_PROJECT_COMMITTEE = 'Comité de proyectos'


ROLE_PERMISSION_CODENAMES = {
    ROLE_FIELD_OPERATOR: {
        'view_project',
        'view_projectupdate',
        'add_projectupdate',
        'view_supportingdocument',
        'add_supportingdocument',
    },
    ROLE_EXTERNAL_AUDITOR: {
        'view_institution',
        'view_project',
        'view_donation',
        'view_fundallocation',
        'view_expense',
        'view_supportingdocument',
        'view_projectupdate',
        'view_auditlog',
    },
    ROLE_PROJECT_COMMITTEE: {
        'view_project',
        'view_projectupdate',
        'view_projectdocument',
        'view_projectupdateattachment',
        'view_projectupdatereview',
        'add_projectupdatereview',
        'view_projectupdatereviewdecision',
        'add_projectupdatereviewdecision',
    },
}
