from django.db import migrations, models

LEGACY_PUBLIC_CONTRACT_DIGEST = "50f875806217865ef35b74f58ed885c4b5c832284391dbea7f84344d3416f66d"
PUBLIC_CONTRACT_DIGEST = "31f505350566bfcde0a30109dadcfb3565042fd395b4c1bd151966f94d361332"
SUPPORTED_PUBLIC_CONTRACT_DIGESTS = (
    PUBLIC_CONTRACT_DIGEST,
    LEGACY_PUBLIC_CONTRACT_DIGEST,
)


class Migration(migrations.Migration):
    dependencies = [
        ("content", "0005_repair_content_release_contract_digest"),
    ]

    operations = [
        # The database may still have the old 0003 check (pre-repair path) or the published 0004
        # current-only check (already-recorded path). Both use this stable constraint name.
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
    ]
