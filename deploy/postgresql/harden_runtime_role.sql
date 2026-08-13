-- =============================================================================
-- SIGEDON — Endurecimiento del rol runtime de PostgreSQL
-- =============================================================================
--
-- PROPOSITO
--   Separar el rol propietario/migraciones (sigedon_owner) del rol runtime
--   que usa la aplicacion Django dia a dia (sigedon_app), y limitar
--   explicitamente los privilegios de sigedon_app sobre
--   operations_auditlog y operations_expenserequestevent para reforzar,
--   a nivel de base de datos, que esos registros son append-only
--   (INSERT y SELECT unicamente).
--
-- COMO EJECUTAR
--   * Este script debe ejecutarse conectado como el propietario de la base
--     (sigedon_owner) o como un administrador con privilegios equivalentes
--     (por ejemplo, el rol "postgres"). NO ejecutarlo con el rol runtime.
--   * Es una PLANTILLA: sustituye los nombres entre <angulos> por los
--     valores reales del entorno (base de datos, roles, contrasenas).
--   * NO contiene contrasenas reales. La contrasena de sigedon_app debe
--     definirse fuera de este archivo (gestor de secretos, variable de
--     entorno del pipeline de despliegue, etc.) y nunca versionarse.
--   * Ejecutar con: psql -U <owner> -d <nombre_base> -f harden_runtime_role.sql
--   * Es seguro reejecutar: los GRANT/REVOKE son declarativos y repetibles.
--
-- ALCANCE
--   * Este script NO reemplaza el trigger append-only instalado por la
--     migracion 0018_auditlog_append_only_trigger de apps.operations. Es
--     una capa adicional de defensa en profundidad: incluso si el trigger
--     se deshabilitara accidentalmente, sigedon_app seguiria sin poder
--     mutar operations_auditlog a nivel de privilegios SQL.
--   * Un superusuario de PostgreSQL puede administrar o desactivar estas
--     protecciones (trigger o privilegios). Esa capacidad se reserva para
--     administracion tecnica explicita, nunca para el uso cotidiano de la
--     aplicacion.
--
-- =============================================================================


-- -----------------------------------------------------------------------------
-- 0. Parametros del entorno (ajustar antes de ejecutar)
-- -----------------------------------------------------------------------------
-- Sustituir en todo el archivo:
--   <sigedon_db>     -> nombre real de la base de datos de SIGEDON.
--   sigedon_app      -> nombre real del rol runtime, si difiere.
--   sigedon_owner    -> nombre real del rol propietario/migraciones, si difiere.


-- -----------------------------------------------------------------------------
-- 1. Creacion de roles (si aun no existen)
-- -----------------------------------------------------------------------------
-- sigedon_owner: ejecuta migraciones, es propietario de las tablas y objetos.
-- Puede coincidir con un rol administrativo existente; no requiere ser
-- superusuario, pero si debe poder crear/alterar esquema.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sigedon_owner') THEN
        CREATE ROLE sigedon_owner WITH LOGIN PASSWORD '<reemplazar-fuera-de-este-archivo>';
    END IF;
END
$$;

-- sigedon_app: rol runtime de Django (web, workers, comandos ordinarios).
-- Explicitamente SIN SUPERUSER, SIN CREATEDB, SIN CREATEROLE,
-- SIN REPLICATION y SIN BYPASSRLS.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sigedon_app') THEN
        CREATE ROLE sigedon_app WITH
            LOGIN
            PASSWORD '<reemplazar-fuera-de-este-archivo>'
            NOSUPERUSER
            NOCREATEDB
            NOCREATEROLE
            NOREPLICATION
            NOBYPASSRLS;
    ELSE
        -- Si el rol ya existe, garantiza igualmente la ausencia de privilegios
        -- peligrosos (idempotente frente a roles heredados o mal configurados).
        ALTER ROLE sigedon_app
            NOSUPERUSER
            NOCREATEDB
            NOCREATEROLE
            NOREPLICATION
            NOBYPASSRLS;
    END IF;
END
$$;


-- -----------------------------------------------------------------------------
-- 2. Conexion y esquema
-- -----------------------------------------------------------------------------
GRANT CONNECT ON DATABASE <sigedon_db> TO sigedon_app;
GRANT USAGE ON SCHEMA public TO sigedon_app;


-- -----------------------------------------------------------------------------
-- 3. Privilegios generales sobre tablas operativas existentes
-- -----------------------------------------------------------------------------
-- Deliberadamente NO se otorga ALL PRIVILEGES. sigedon_app recibe unicamente
-- lo necesario para operar el dominio: SELECT, INSERT, UPDATE, DELETE.
-- No incluye TRUNCATE, TRIGGER, REFERENCES ni privilegios de DDL.
GRANT SELECT, INSERT, UPDATE, DELETE
    ON ALL TABLES IN SCHEMA public
    TO sigedon_app;

GRANT USAGE, SELECT
    ON ALL SEQUENCES IN SCHEMA public
    TO sigedon_app;


-- -----------------------------------------------------------------------------
-- 4. Endurecimiento especifico de tablas append-only
-- -----------------------------------------------------------------------------
-- Se ejecuta DESPUES de los GRANT generales de la seccion 3 para revocar
-- explicitamente lo que ese otorgamiento amplio pudo haber concedido sobre
-- estas tablas. Ambas son append-only: solo INSERT y SELECT deben
-- permanecer disponibles para sigedon_app.
-- Tablas cubiertas (nombres reales de migraciones 0018 / 0029):
--   public.operations_auditlog
--   public.operations_expenserequestevent
GRANT SELECT, INSERT
    ON public.operations_auditlog
    TO sigedon_app;

REVOKE UPDATE, DELETE, TRUNCATE
    ON public.operations_auditlog
    FROM sigedon_app;

-- TRIGGER y REFERENCES no forman parte de los GRANT de la seccion 3, pero se
-- revocan explicitamente por si fueron concedidos por una migracion de
-- privilegios anterior, un rol heredado, PUBLIC, o una configuracion previa.
REVOKE TRIGGER, REFERENCES
    ON public.operations_auditlog
    FROM sigedon_app;

-- Misma postura append-only para ExpenseRequestEvent.
GRANT SELECT, INSERT
    ON public.operations_expenserequestevent
    TO sigedon_app;

REVOKE UPDATE, DELETE, TRUNCATE
    ON public.operations_expenserequestevent
    FROM sigedon_app;

REVOKE TRIGGER, REFERENCES
    ON public.operations_expenserequestevent
    FROM sigedon_app;

-- Revocar privilegios peligrosos tambien desde PUBLIC por si grants
-- heredados del esquema restauran UPDATE/DELETE/TRUNCATE/TRIGGER.
REVOKE UPDATE, DELETE, TRUNCATE, TRIGGER, REFERENCES
    ON public.operations_auditlog
    FROM PUBLIC;

REVOKE UPDATE, DELETE, TRUNCATE, TRIGGER, REFERENCES
    ON public.operations_expenserequestevent
    FROM PUBLIC;


-- -----------------------------------------------------------------------------
-- 5. Privilegios por defecto para objetos futuros
-- -----------------------------------------------------------------------------
-- Cubre tablas y secuencias que sigedon_owner cree en el futuro (nuevas
-- migraciones), para que sigedon_app las use sin requerir GRANT manual
-- adicional cada vez, con el mismo alcance minimo que la seccion 3.
ALTER DEFAULT PRIVILEGES FOR ROLE sigedon_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO sigedon_app;

ALTER DEFAULT PRIVILEGES FOR ROLE sigedon_owner IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO sigedon_app;

-- Si operations_auditlog u operations_expenserequestevent llegaran a
-- recrearse (reconstruccion excepcional), estos privilegios por defecto
-- NO revocan UPDATE/DELETE/TRUNCATE automaticamente: repetir la seccion 4
-- manualmente despues de cualquier recreacion de esas tablas.


-- -----------------------------------------------------------------------------
-- 6. Verificacion posterior
-- -----------------------------------------------------------------------------
-- Confirmar visualmente los privilegios resultantes antes de desplegar:
--   \dp public.operations_auditlog
--   \dp public.operations_expenserequestevent
--   \du sigedon_app
--
-- O bien, desde Django ya configurado con las credenciales de sigedon_app
-- (rol runtime final; un éxito bajo sigedon_owner no valida la separación):
--   python manage.py verify_postgres_security
--
-- El comando exige PostgreSQL, verifica AuditLog y ExpenseRequestEvent
-- append-only (catálogo + sondas con rollback), constraints críticos y
-- privilegios runtime sobre ambas tablas append-only. No repara grants.
-- Ver deploy/postgresql/README.md.