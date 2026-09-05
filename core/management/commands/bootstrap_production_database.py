import json
import secrets
from urllib.parse import quote

import boto3  # type: ignore[import-untyped]
import psycopg
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from psycopg import sql


class Command(BaseCommand):
    help = "Create the unprivileged production database role and populate runtime secrets"
    requires_system_checks: list[str] = []

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--master-secret-id", required=True)
        parser.add_argument("--database-url-secret-id", required=True)
        parser.add_argument("--django-secret-key-secret-id", required=True)
        parser.add_argument("--database-host", required=True)
        parser.add_argument("--database-port", default=5432, type=int)
        parser.add_argument("--database-name", default="dtc_website")
        parser.add_argument("--application-user", default="website_app")
        parser.add_argument("--region", default="eu-west-1")

    def handle(self, *args: object, **options: object) -> None:
        del args
        if settings.ENVIRONMENT != "production":
            raise CommandError("This command is restricted to the production environment")

        region = str(options["region"])
        host = str(options["database_host"])
        port = int(options["database_port"])
        database_name = str(options["database_name"])
        application_user = str(options["application_user"])
        client = boto3.client("secretsmanager", region_name=region)
        response = client.get_secret_value(SecretId=str(options["master_secret_id"]))
        try:
            master = json.loads(response["SecretString"])
            master_user = str(master["username"])
            master_password = str(master["password"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise CommandError("The RDS-managed master secret is incomplete") from error

        application_password = secrets.token_urlsafe(48)
        django_secret_key = secrets.token_urlsafe(64)
        with psycopg.connect(
            dbname=database_name,
            user=master_user,
            password=master_password,
            host=host,
            port=port,
            sslmode="require",
            connect_timeout=10,
            autocommit=True,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (application_user,))
                role_statement = "ALTER ROLE" if cursor.fetchone() else "CREATE ROLE"
                cursor.execute(
                    sql.SQL(
                        f"{role_statement} {{}} WITH LOGIN CONNECTION LIMIT 40 PASSWORD {{}}"
                    ).format(
                        sql.Identifier(application_user),
                        sql.Literal(application_password),
                    )
                )
                cursor.execute(
                    sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(
                        sql.Identifier(database_name)
                    )
                )
                cursor.execute(
                    sql.SQL("GRANT CONNECT, TEMPORARY ON DATABASE {} TO {}").format(
                        sql.Identifier(database_name), sql.Identifier(application_user)
                    )
                )
                cursor.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
                cursor.execute(
                    sql.SQL("GRANT USAGE, CREATE ON SCHEMA public TO {}").format(
                        sql.Identifier(application_user)
                    )
                )

        encoded_user = quote(application_user, safe="")
        encoded_password = quote(application_password, safe="")
        database_url = (
            f"postgresql://{encoded_user}:{encoded_password}@{host}:{port}/"
            f"{quote(database_name, safe='')}?sslmode=require"
        )
        client.put_secret_value(
            SecretId=str(options["database_url_secret_id"]), SecretString=database_url
        )
        client.put_secret_value(
            SecretId=str(options["django_secret_key_secret_id"]),
            SecretString=django_secret_key,
        )
        self.stdout.write(self.style.SUCCESS("Production database runtime initialized"))
