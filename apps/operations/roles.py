ROLE_SIGEDON_ADMIN = 'Administrador SIGEDON'
ROLE_FIELD_OPERATOR = 'Operador de campo'
ROLE_EXTERNAL_AUDITOR = 'Auditor externo'
ROLE_PROJECT_COMMITTEE = 'Comité de proyectos'


# Comité de proyectos is the single functional committee role.
# Review, decision, and remediation resolution remain distinct permissions and
# workflow actions within that role.
COMMITTEE_READ_PERMISSION_CODENAMES = {
    'view_project',
    'view_projectupdate',
    'view_projectdocument',
    'view_projectupdateattachment',
    'view_projectupdatereview',
    'view_projectupdatereviewdecision',
    'view_projectupdateremediation',
    'view_projectupdateremediationattachment',
}


ROLE_PERMISSION_CODENAMES = {
    ROLE_FIELD_OPERATOR: {
        'view_project',
        'view_projectupdate',
        'add_projectupdate',
        'view_supportingdocument',
        'add_supportingdocument',
        'view_projectupdateremediation',
        'view_projectupdateremediationattachment',
        'add_projectupdateremediation',
        'change_projectupdateremediation',
        'add_projectupdateremediationattachment',
        'delete_projectupdateremediationattachment',
        'submit_projectupdateremediation',
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
        'review_projectupdate',
        'decide_projectupdate',
        'resolve_projectupdateremediation',
    },
}
