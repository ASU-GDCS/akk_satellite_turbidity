#!/usr/bin/env bash
# Executable credential source for google-auth (see ci/gcp_wif_login.sh).
# Prints a fresh CircleCI OIDC token in the format google-auth expects.
# The token goes to google-auth on stdout; it is never written to the job log.
set -euo pipefail

aud="${CIRCLECI_OIDC_AUDIENCE:-}"
token=""
if [[ -n "${aud}" ]] && command -v circleci >/dev/null 2>&1; then
  token="$(circleci run oidc get --claims "{\"aud\":\"${aud}\"}" 2>/dev/null || true)"
fi
# Fall back to the token CircleCI injects at job start (valid for 1 hour).
token="${token:-${CIRCLE_OIDC_TOKEN_V2:-${CIRCLE_OIDC_TOKEN:-}}}"

if [[ -z "${token}" ]]; then
  printf '{"version":1,"success":false,"code":"NO_TOKEN","message":"No CircleCI OIDC token: the job must use a context"}\n'
  exit 1
fi
printf '{"version":1,"success":true,"token_type":"urn:ietf:params:oauth:token-type:jwt","id_token":"%s"}\n' "${token}"
