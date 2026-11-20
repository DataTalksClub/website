# Coolify API tokens for GitHub deploys

The Contabo Coolify control plane runs on contabo-a. GitHub Actions deploy
through its REST API with Sanctum API tokens. Each consumer has its own scoped
token, so one token's expiry or rotation never breaks another repo:

| Consumer | Coolify token name | Abilities | GitHub secret | DataOps secret |
|----------|--------------------|-----------|---------------|----------------|
| `DataTalksClub/website` `deploy-coolify.yml` | `gha-website-deploy-<date>` | read, write, deploy | env `coolify-staging` → `COOLIFY_TOKEN` | `coolify.gha-website-deploy` |
| `DataTalksClub/course-management-platform` `deploy-coolify.yml` | `gha-cmp-deploy-<date>` | read, write, deploy, read:sensitive (reads `docker_compose_raw`) | env `coolify-staging` → `COOLIFY_TOKEN` | `coolify.gha-cmp-deploy` |
| `DataTalksClub/relay` deploy workflows | `gha-relay-deploy-<date>` | read, write, deploy, read:sensitive | repo and env `coolify-staging` → `COOLIFY_TOKEN` | `coolify.gha-relay-deploy` |
| Hetzner `coolify-rolling-web` (production follower), `cmp-release-poller`, aisl tools | `hetzner-ops-<date>` | read, write, deploy, read:sensitive, write:sensitive | `~/.secrets/` and `~/tmp/secrets/coolify-api-169.58.119.163.token` on Hetzner | `coolify.hetzner-ops` |

The current tokens were minted on 2026-11-21 and expire on 2027-11-20.

## Incident: 2026-11-20

The original shared token `agent-setup-2026-10-22` was created with a 30-day
expiry. It expired at 2026-11-20 22:12:50 UTC. The next website deploy (run
44830025858) failed at "Point staging at the pushed release" with a bare
`curl: (22) ... 401`. The production rolling follower and the CMP release
poller on Hetzner used the same token and failed from 22:13 UTC. Nothing
had revoked it; it just expired, and no check looked at the expiry.

## Guards

- `deploy-coolify.yml` runs `python3 -m deploy.coolify_token check` before its
  first Coolify call. A rejected token fails with the cause and this runbook
  instead of a bare curl error.
- `coolify-token-check.yml` runs daily (and on dispatch). It fails when Coolify
  rejects the token, or when the `coolify-staging` variable
  `COOLIFY_TOKEN_EXPIRES_AT` (YYYY-MM-DD) is within 30 days or missing. The red
  run reaches the GitHub Actions failure routine weeks before a deploy breaks.

## Check token expiry

Token metadata only (never select the `token` column):

```bash
ssh contabo-a "sudo -n docker exec coolify-db psql -U coolify -d coolify -c \
  'select id, name, abilities, last_used_at, expires_at from personal_access_tokens order by id;'"
```

## Rotate a token

All four tokens expire on the same day, so rotate them together.

Never print a token. Write it to a mode-600 file, pipe it into the stores, then
delete the file.

1. Mint a token in the Coolify UI (Keys & Tokens → API tokens) on the Root
   Team, with only the abilities in the table and a 1-year expiry. Or use
   `php artisan tinker` in the `coolify` container:
   `session(['currentTeam' => App\Models\Team::find(0)]);
   App\Models\User::find(0)->createToken('<name>', [...abilities], now()->addYear())->plainTextToken`.
2. Update the GitHub secret from the file:
   `gh secret set COOLIFY_TOKEN --env coolify-staging -R <repo> < <file>`.
3. Update the DataOps secret with `dataops secrets set <name> --from-file -`.
   Include every field (`token`, `url`, `notes`): `set` replaces the whole set.
4. For the website, record the expiry:
   `gh variable set COOLIFY_TOKEN_EXPIRES_AT --env coolify-staging -R DataTalksClub/website --body <YYYY-MM-DD>`.
5. Dispatch `coolify-token-check.yml` and confirm it is green. Then rerun or
   dispatch the deploy.
6. Delete the old token row once nothing uses it (check `last_used_at`).
