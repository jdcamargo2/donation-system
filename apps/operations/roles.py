ROLE_SIGEDON_ADMIN = 'Administrador SIGEDON'
ROLE_FIELD_OPERATOR = 'Operador de campo'
ROLE_EXTERNAL_AUDITOR = 'Auditor externo'
ROLE_PROJECT_COMMITTEE = 'Comité de proyectos'
ROLE_PROJECT_UPDATE_REVIEWER = 'Revisor del Comité'
ROLE_PROJECT_UPDATE_DECIDER = 'Decisor del Comité'


# El grupo legado se conserva para no alterar membresías existentes; la sincronización
# lo deja en solo lectura. La asignación a los roles funcionales nuevos es explícita.
COMMITTEE_READ_PERMISSION_CODENAMES = {
    'view_project',
    'view_projectupdate',
    'view_projectdocument',
    'view_projectupdateattachment',
    'view_projectupdatereview',
    'view_projectupdatereviewdecision',
}


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
        *COMMITTEE_READ_PERMISSION_CODENAMES,
    },
    ROLE_PROJECT_UPDATE_REVIEWER: {
        *COMMITTEE_READ_PERMISSION_CODENAMES,
        'review_projectupdate',
    },
    ROLE_PROJECT_UPDATE_DECIDER: {
        *COMMITTEE_READ_PERMISSION_CODENAMES,
        'decide_projectupdate',
    },
}
