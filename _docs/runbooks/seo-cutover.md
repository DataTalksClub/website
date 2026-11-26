# Apex SEO cutover

The application canonical origin is independent of the serving hostname and of
indexing. The canonical origin must be `https://datatalks.club` even while
`prod.datatalks.club` remains blocked from indexing. See the 2026-11-15 migration
audit §3 row 7 and §6.3–6.4.

After deploying the reviewed application, run from the operator checkout using
the current production web container name (the minimal runtime has no uv):

```sh
uv run --no-sync ssh contabo-a 'sudo docker exec <production-web> python manage.py prepare_public_seo'
uv run --no-sync ssh contabo-a 'sudo docker exec <production-web> python manage.py prepare_public_seo --apply --allow-production-write'
```

The first command previews counts only. The second uses the typed, revisioned,
audited operational-settings writer and repairs imported podcast slugs ending
in `.md`. It preserves source keys, provenance, transcripts and checksums. The
parser normalizes subsequent imports, so fresh source syncs preserve the repair.
No data migration or checked-in content projection is involved. Repeating the
command makes no further changes.

Before cutover leave `DTC_APEX_CUTOVER` unset or `false`. Production defaults to
`NOINDEX=True`: robots.txt disallows `/` and responses carry
`X-Robots-Tag: noindex, nofollow`. Development remains blocked independently.

Only during the separately authorized apex cutover, set
`DTC_APEX_CUTOVER=true` in the production application's persistent environment,
redeploy, and coordinate removal of the apex edge noindex response policy with
the infrastructure owner. This flag switches the application robots body and
public response header together; private surfaces remain noindex. Keep the
`prod.` and `dev.` edge noindex policies. Revert the flag to `false` and redeploy
to block indexing again. This runbook does not authorize DNS or CDN changes.

Verify with curl that representative public pages have one apex canonical;
every sitemap `<loc>` starts with `https://datatalks.club/`; the mentoring
episode is `/podcast/s01/e05-mentoring-in-tech-how-to-find-and-become-a-mentor`
without `.md`; and robots/header blocking still holds before cutover. At
cutover verify the apex robots allows public paths and advertises the apex
sitemap, with no public noindex header, while private routes stay blocked.
No change to `courses.datatalks.club` is part of this procedure.
