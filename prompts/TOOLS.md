# Tools available to the agent

Operational tooling reference adapted from `openclaw-configurations/TOOLS.md` and trimmed to what's actually injected into this agent's environment by `./start-symphony.sh`.

## General rules

- **All credentials below are read-only.** The agent must NOT attempt writes via any of these services — writes are restricted to git operations on the target repo, opening the PR, and the single Jira comment posting the PR link.
- **PII redaction (WORKFLOW.md invariant #10).** Several services (SendGrid Mail Activity, MongoDB `compucorp.sites`, Loki) return end-user PII — recipient emails, contact names, message bodies. The full JSONL transcript is persisted by the audit. When citing this data in a PR or Jira comment, **redact recipient emails** as `r***@example.com`, do not paste contact names verbatim, and do not include subject lines or message bodies. Quote only the structural evidence (timestamps, status codes, IDs).
- Prefer **time-bounded queries** — start with a 30 min / 1 h window and expand if needed.
- Use `python3`, not `python`.
- Do not echo raw secrets/tokens back into the agent transcript or PR description.
- If a tool you expected isn't available, comment on the Jira ticket asking for access — do not try to work around the absence.

## Quick tool selection guide

| Symptom | First tool |
|---|---|
| App error / 5xx / unexpected behavior | Loki (logs) |
| Slow page / latency | Tempo (traces) |
| Recent code change suspected | GitHub |
| Site → repo / env mapping | MongoDB Atlas (`compucorp.sites`) |
| Edge / WAF / 403/429 weirdness | Cloudflare |
| AWS-native service (CloudWatch, RDS, ALB) | AWS CLI |
| DB-level investigation (locks, slow queries, schema) | RDS direct connection via SSH tunnel |
| Past incidents / runbooks / architecture | Atlassian MCP (Jira / Confluence) |

## Atlassian (Jira / Confluence) — preferred via MCP

**Purpose:** ticket history, comments, runbooks, architecture context. **The agent has the Atlassian MCP configured** — prefer its tools over raw REST when available.

If MCP is unavailable, REST fallback:
```bash
curl -sS -u "$JIRA_USER:$JIRA_TOKEN" -H "Accept: application/json" \
  "$JIRA_URL/rest/api/3/issue/<KEY>"
```

For wiki markup vs ADF quirks (inline images, formatted comments), see `~/projects/jira-sprint-automation/docs/jira/` if accessible.

## GitHub

**Auth:** `$GH_TOKEN` is set by `./start-symphony.sh` to `$OPENCLAW_GH_TOKEN` (bot identity `openclawautomation`) — NOT the operator's personal `gh auth`. All git/gh operations are authenticated as the bot.

**Common patterns:**
- Recent commits in a window: `gh api "repos/<owner>/<repo>/commits?since=<iso>&until=<iso>"`
- Search code: `gh search code 'query' --repo <owner>/<repo>`
- Open PRs touching a file: `gh pr list --repo <owner>/<repo> --search 'path/file.ext in:files'`
- Get repo's default branch: `gh api repos/<owner>/<repo> --jq .default_branch`

## Grafana Loki

**Purpose:** application logs, nginx access logs, 4xx/5xx spikes, upstream failures.

**Auth:** `$LOKI_URL`, `$LOKI_USER`, `$LOKI_TOKEN`. **Production stacks** (e.g. `iona_civiplus_net`, `eppingforest_civiplus_net`) require explicit user approval per query — post a Jira comment before querying production.

**Pattern:**
```bash
curl -sS -u "$LOKI_USER:$LOKI_TOKEN" \
  -G "$LOKI_URL/loki/api/v1/query_range" \
  --data-urlencode 'query={swarm_stack="<stack>"} |~ "<pattern>"' \
  --data-urlencode "start=$(date -u -v-1H +%s)000000000" \
  --data-urlencode "end=$(date -u +%s)000000000"
```

Common labels: `swarm_stack`, `swarm_service`, `container_name`. Narrow by service first, then status codes / error strings.

## Tempo (distributed tracing)

**Purpose:** request traces, slow spans, dependency graphs.

**Auth:** `$TEMPO_TOKEN`. Base URL: `https://api.tempo.io/4/`. Header: `Authorization: Bearer $TEMPO_TOKEN`.

## MongoDB Atlas

**Purpose:** canonical site → repo / env / cluster / image / env_vars resolution via `compucorp.sites` collection.

**Auth:** `$MONGO_HOST`, `$MONGO_USER`, `$MONGO_PASSWORD`, `$MONGO_TLS`, `$MONGO_PORT`, `$MONGO_AUTH_SOURCE`, `$MONGO_DEFAULT_DB`. **Read-only.**

**Pattern (via `mongosh`, if installed locally):**
```bash
mongosh "mongodb+srv://$MONGO_USER:$MONGO_PASSWORD@$MONGO_HOST/$MONGO_DEFAULT_DB?authSource=$MONGO_AUTH_SOURCE&tls=$MONGO_TLS" \
  --quiet --eval 'db.sites.find({_id: "example.org"}, {repository: 1, swarm_cluster: 1, path: 1, env_vars: 1}).pretty()'
```

If `mongosh` isn't available, Python with `pymongo` works equivalently. Useful queries:
- Find a site: `db.sites.find({_id: "<hostname>"})`
- Sites by cluster: `db.sites.find({swarm_cluster: "<cluster>"})`
- Capability check: `db.sites.find({"env_vars.NEWRELIC_ENABLED": "true"})`

## AWS CLI

**Purpose:** CloudWatch logs/metrics for AWS-native services (RDS, ElastiCache, ALB, ECS); S3 reads.

**Auth:** `$AWS_ACCESS_KEY_ID`, `$AWS_SECRET_ACCESS_KEY` are read-only credentials for the default account. Region: `eu-west-2`.

**Client AWS accounts** require `aws sts assume-role` with the client's account ID and a role like `arn:aws:iam::<account-id>:role/CompucorpExternalReadOnly-<client>`. The agent does NOT have a built-in mapping of client → AWS account ID; if you need a client account, post a Jira comment asking for the role ARN.

**Patterns:**
```bash
aws logs start-query --log-group-name <name> --start-time $(date -v-1H +%s) --end-time $(date +%s) \
  --query-string 'fields @timestamp, @message | filter @message like /pattern/'
aws cloudwatch get-metric-statistics --namespace AWS/RDS --metric-name CPUUtilization \
  --start-time <iso> --end-time <iso> --period 60 --statistics Average \
  --dimensions Name=DBInstanceIdentifier,Value=<id>
```

## RDS (direct DB connection via SSH tunnel)

**Purpose:** locks, slow queries, schema/data inspection in DEV / STAGING and (some clients only) CIVIPLUS environments.

**Auth (read-only credentials per environment):**

| Env | Endpoint | User | Password | Local port | Jump host |
|---|---|---|---|---|---|
| DEV | `$RDS_DEV_ENDPOINT` | `$RDS_DEV_USER` | `$RDS_DEV_PASSWORD` | `$RDS_DEV_LOCAL_PORT` | `$RDS_JUMP_HOST_MAIN` |
| STAGING | `$RDS_STAGING_ENDPOINT` | `$RDS_STAGING_USER` | `$RDS_STAGING_PASSWORD` | `$RDS_STAGING_LOCAL_PORT` | `$RDS_JUMP_HOST_MAIN` |
| CIVIPLUS | `$RDS_CIVIPLUS_ENDPOINT` | `$RDS_CIVIPLUS_USER` | `$RDS_CIVIPLUS_PASSWORD` | `$RDS_CIVIPLUS_LOCAL_PORT` | `$RDS_JUMP_HOST_CIVIPLUS` |

**Note:** CIVIPLUS RDS only has access to **some** clients — not all. If a query fails because the client database isn't present, that's expected; comment on Jira to confirm.

**Tunnel pattern:**
```bash
ssh -f -N -L "$RDS_DEV_LOCAL_PORT:$RDS_DEV_ENDPOINT:3306" "$RDS_JUMP_HOST_MAIN" -o StrictHostKeyChecking=accept-new
mysql -h 127.0.0.1 -P "$RDS_DEV_LOCAL_PORT" -u "$RDS_DEV_USER" -p"$RDS_DEV_PASSWORD" -e "SHOW DATABASES;"
```

SSH uses your `~/.ssh/` config and keys. If a tunnel fails (`127.0.0.1:<port>` unreachable), the tunnel isn't up — re-run the ssh command. Kill stale tunnels with `pkill -f "ssh -f -N -L.*$RDS_DEV_LOCAL_PORT"`.

## Cloudflare

**Purpose:** edge traffic, WAF, rate-limit anomalies for Cloudflare-fronted sites (groups: DR, MM, CiviPlus, ASE, WHF, FWD).

**Auth:** `$CLOUDFLARE_API_TOKEN`. Read-only.

**Pattern:**
```bash
curl -sS -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" -H "Accept: application/json" \
  "https://api.cloudflare.com/client/v4/zones?name=example.com"
```

Useful for: zone IDs, recent firewall events, rate-limit hits, edge response codes. Do not modify zones, DNS records, or WAF rules — token is read-only and operations would fail anyway, but don't try.

## SendGrid (email delivery investigation)

**Purpose:** investigate "email didn't arrive" / "bounced" / "marked as spam" / "rate-limited" tickets.

**Auth:** `$SENDGRID_API_KEY` (Mail Activity read-only scope) and `$SENDGRID_BILLING_API_KEY` (billing/plan info, read-only). Both keys are scoped at the upstream service — the agent cannot send mail, change plans, or modify templates with them.

**Pattern (Mail Activity — was an email actually delivered?):**
```bash
# Last 24h of activity for a specific recipient:
curl -sS -H "Authorization: Bearer $SENDGRID_API_KEY" -H "Accept: application/json" \
  "https://api.sendgrid.com/v3/messages?query=to_email%3D%22user%40example.com%22&limit=20"

# A specific message's full event log:
curl -sS -H "Authorization: Bearer $SENDGRID_API_KEY" \
  "https://api.sendgrid.com/v3/messages/<msg_id>"
```

Useful for: confirming "did our send actually happen", looking at bounce reasons, checking suppression list, spotting deferral/dropped. Do NOT attempt `POST /mail/send` — token has no scope for it and the operation must come from a human.

**PII warning.** Mail Activity responses contain **recipient email addresses** and sometimes subject lines, both of which are PII. The full JSONL transcript of your run is persisted by the audit (`analyze-run.sh`) and visible to operators reviewing the run. When citing Mail Activity evidence in the PR description or Jira comment, **redact recipient emails** (`r***@example.com`) and avoid pasting subject lines verbatim. Quote only the structural evidence — timestamps, status codes, bounce reasons — that is needed to support the fix.

## Jenkins (build/deploy status)

**Purpose:** check CI build status for a PR / branch, fetch build logs when CI is the only test environment (per WORKFLOW.md verification step).

**Auth:** `$JENKINS_URL`, `$JENKINS_USER`, `$JENKINS_TOKEN`. Token is scoped to read; cannot trigger builds.

**Pattern:**
```bash
# Build status for a specific job + build number:
curl -sS -u "$JENKINS_USER:$JENKINS_TOKEN" \
  "$JENKINS_URL/job/<job-name>/<build-number>/api/json"

# Most recent build log (tail):
curl -sS -u "$JENKINS_USER:$JENKINS_TOKEN" \
  "$JENKINS_URL/job/<job-name>/lastBuild/consoleText" | tail -200
```

Useful for: cross-check that a CI build passed before declaring the fix "verified on CI" in PR Comments. Do NOT attempt build triggers (`POST .../build`) — invariant #5 prohibits, and the token has no scope for it.

## Netdata Cloud (infra metrics, alarms)

**Purpose:** infra-level investigation when Loki logs aren't enough — CPU/memory/IO pressure on a host, alarm history, anomaly windows.

**Auth:** `$NETDATA_CLOUD_TOKEN` (read-only), `$NETDATA_SPACE_SLUG` (= `compucorpcluster`), `$NETDATA_CLOUD_URL` (= `https://app.netdata.cloud`).

**Pattern:**
```bash
# List rooms in the space:
curl -sS -H "Authorization: Bearer $NETDATA_CLOUD_TOKEN" \
  "$NETDATA_CLOUD_URL/api/v2/spaces/$NETDATA_SPACE_SLUG/rooms"

# Active/recent alerts:
curl -sS -H "Authorization: Bearer $NETDATA_CLOUD_TOKEN" \
  "$NETDATA_CLOUD_URL/api/v3/alerts?scope_nodes=*&active=true"
```

Useful for: "did a spike correlate with this bug report's timeframe?", "what was the host doing at <T>?", confirming/refuting infrastructure-level hypotheses before assuming it's an app bug. Do NOT attempt to create/edit alarms, rooms, or dashboards — token is read-only.

## Compucorp dev sites (reproducing UI bugs)

Compucorp client sites have dev/test instances at hostnames like `<slug>.public.cc-test.site` or `<slug>.cc-staging.site`. These are fronted by a Traefik gateway that requires HTTP Basic Auth before the app login.

**Two layers of auth — distinct:**

1. **HTTP Basic Auth (Traefik gateway)** — required first. The credentials are per-site, stored encrypted (VAULT prefix) in MongoDB `compucorp.sites.<site>.basic_auth`. If the operator has provided them as env vars, they live in `$DEV_SITE_BASIC_USER` / `$DEV_SITE_BASIC_PASS` (and the relevant site URL in `$DEV_SITE_URL`). If those env vars are NOT set, you cannot reach the app — investigate via repo code only, do not try to brute-force or guess.

2. **App login (Drupal/CiviCRM admin)** — required after gateway. Default for dev sites is **`compucorp_admin` / `compucorp_admin`** (correct ~99% of the time per the operator).

**Pattern to reach an admin page** (when env vars are set):

```bash
# Sanity check the gateway:
curl -sS -o /dev/null -w "HTTP %{http_code}\n" \
  -u "$DEV_SITE_BASIC_USER:$DEV_SITE_BASIC_PASS" \
  "$DEV_SITE_URL/"

# Login to Drupal (carries cookies via -c / -b):
COOKIE_JAR=$(mktemp)
curl -sS -u "$DEV_SITE_BASIC_USER:$DEV_SITE_BASIC_PASS" -c "$COOKIE_JAR" \
  -F "name=compucorp_admin" -F "pass=compucorp_admin" -F "form_id=user_login" \
  "$DEV_SITE_URL/user/login" >/dev/null

# Now fetch the admin page (CiviCRM example):
curl -sS -u "$DEV_SITE_BASIC_USER:$DEV_SITE_BASIC_PASS" -b "$COOKIE_JAR" \
  "$DEV_SITE_URL/civicrm/contact/view?reset=1&cid=..."
```

**Do NOT modify state on the dev site.** Read-only inspection only — confirm bug exists, capture HTML/JSON for evidence, then propose code fix. Do not POST forms, change records, or trigger background jobs.

**Limitation:** if the agent doesn't have basic auth env vars, it must investigate via code reading alone. The agent should NOT decrypt VAULT values from Mongo — that's not in scope for Phase 1.

## sysPass (Compucorp self-hosted password manager)

**Purpose:** retrieve Drupal admin + Traefik Basic Auth credentials for staging sites, needed by the visual-repro skill (`prompts/visual-repro.md`).

**Endpoint:** `$SYSPASS_URL/api.php` — JSON-RPC 2.0.

**Auth:** sysPass uses per-action API tokens. The agent has two:
- `$SYSPASS_TOKEN_SEARCH` + `$SYSPASS_PASS_SEARCH` — authorized for `account/search`
- `$SYSPASS_TOKEN_VIEWPASS` + `$SYSPASS_PASS_VIEWPASS` — authorized for `account/viewPass`

All four env vars live in `~/.claude/settings.json` and are auto-forwarded by `start-symphony.sh`'s generic env-load (lines 67–83). Do NOT log them.

**Two-step credential lookup pattern:**

```python
# Step 1: account/search by site URL or name
search_response = {
  "jsonrpc": "2.0",
  "method": "account/search",
  "params": {
    "authToken": $SYSPASS_TOKEN_SEARCH,
    "tokenPass": $SYSPASS_PASS_SEARCH,
    "text": "ies2.cc-staging.site",   # search by hostname
  },
  "id": 1
}
# Returns: list of accounts with {id, login, url, name, ...}
# Multiple accounts per site are typical (Drupal admin + Basic HTTP Auth + DB + ...).
# Filter by `name` field to disambiguate: "Drupal" → admin login; "Basic HTTP Auth" → Traefik gate.

# Step 2: account/viewPass with the filtered id
viewpass_response = {
  "jsonrpc": "2.0",
  "method": "account/viewPass",
  "params": {
    "authToken": $SYSPASS_TOKEN_VIEWPASS,
    "tokenPass": $SYSPASS_PASS_VIEWPASS,
    "id": <filtered_account_id>,
  },
  "id": 1
}
# Returns: {"result": {"result": {"password": "<plain>"}}}
```

**Account naming convention observed (2026-05-15):**
- `Basic HTTP Auth` — Traefik gateway credentials (login is usually a single token like `ies`)
- `Drupal` — Drupal admin user (login is usually `compucorp_admin`)
- One pair per site, one pair per environment (staging / data / etc.)

**PII redaction:** passwords are production-equivalent secrets. Never include in Jira comments, PR bodies, logs, or transcripts.

**Helper:** `prompts/repro_helpers.get_syspass_cred(account_search, prefer_name=)` wraps the two-step flow.

## Workspace conventions

- Working directory is `~/symphony_workspaces/<JIRA-KEY>/`. The target repo is cloned into `./repo/`.
- A symlink `./.playbooks/` points to `~/projects/dev-ai-playbooks/` — read playbooks on demand via `cat ./.playbooks/.ai/<file>.md`.
- `./plan.md` is the per-ticket plan (created by `superpowers:writing-plans`).
- Do not write outside the workspace.
