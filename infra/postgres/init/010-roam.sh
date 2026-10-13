#!/bin/sh
set -eu

export ROAM_OWNER_PASSWORD="$(cat /run/secrets/roam_owner_password)"
export ROAM_APP_PASSWORD="$(cat /run/secrets/roam_app_password)"

psql -X -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<'SQL'
\getenv owner_password ROAM_OWNER_PASSWORD
\getenv app_password ROAM_APP_PASSWORD
CREATE ROLE roam_owner LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'owner_password';
CREATE ROLE roam_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'app_password';
CREATE DATABASE roam OWNER roam_owner;
REVOKE ALL ON DATABASE roam FROM PUBLIC;
GRANT CONNECT ON DATABASE roam TO roam_owner, roam_app;
REVOKE CONNECT ON DATABASE postgres, template1 FROM PUBLIC;
\connect roam
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA roam AUTHORIZATION roam_owner;
GRANT USAGE ON SCHEMA roam TO roam_app;
ALTER DEFAULT PRIVILEGES FOR ROLE roam_owner IN SCHEMA roam GRANT SELECT ON TABLES TO roam_app;
ALTER ROLE roam_owner IN DATABASE roam SET search_path = roam, public;
ALTER ROLE roam_app IN DATABASE roam SET search_path = roam, public;
SQL

unset ROAM_OWNER_PASSWORD ROAM_APP_PASSWORD
