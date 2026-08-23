# This replacement keeps the published 0004 migration immutable while giving databases that
# have not recorded it a safe state transition. The physical constraint is repaired by 0006,
# which also handles databases where the published 0004 constraint was already recorded.

import django.core.validators
from django.db import migrations, models

LEGACY_PUBLIC_CONTRACT_DIGEST = "50f875806217865ef35b74f58ed885c4b5c832284391dbea7f84344d3416f66d"
PUBLIC_CONTRACT_DIGEST = "31f505350566bfcde0a30109dadcfb3565042fd395b4c1bd151966f94d361332"
SUPPORTED_PUBLIC_CONTRACT_DIGESTS = (
    PUBLIC_CONTRACT_DIGEST,
    LEGACY_PUBLIC_CONTRACT_DIGEST,
)


class Migration(migrations.Migration):
    replaces = [
        ("content", "0004_remove_contentrelease_content_release_contract_sha_ck_and_more"),
    ]

    dependencies = [
        ("content", "0003_content_document_structured_data"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name="contentrelease",
                    name="public_contracts_sha256",
                    field=models.CharField(
                        default=PUBLIC_CONTRACT_DIGEST,
                        max_length=64,
                        validators=[
                            django.core.validators.RegexValidator(
                                "^[0-9a-f]{64}$", "Enter a lowercase SHA-256 digest."
                            )
                        ],
                    ),
                ),
                migrations.RemoveConstraint(
                    model_name="contentrelease",
                    name="content_release_contract_sha_ck",
                ),
                migrations.AddConstraint(
                    model_name="contentrelease",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            ("public_contracts_sha256__in", SUPPORTED_PUBLIC_CONTRACT_DIGESTS)
                        ),
                        name="content_release_contract_sha_ck",
                    ),
                ),
            ],
        ),
    ]
