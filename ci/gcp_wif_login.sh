#!/usr/bin/env bash
# Configure keyless Google credentials for this CircleCI job.
#
# Writes a Workload Identity Federation "external_account" credential config
# (no secrets in it) and points GOOGLE_APPLICATION_CREDENTIALS at it for the
# remaining steps. google-auth runs ci/oidc_token.sh whenever it needs a fresh
# CircleCI OIDC token, exchanges it with Google STS, and impersonates the
# service account named by the environment variable given as $1.
#
# Usage: ci/gcp_wif_login.sh GEE_SERVICE_ACCOUNT
set -euo pipefail

sa_var="${1:?usage: $0 <name of env var holding the service account email>}"
sa="${!sa_var:-}"
if [[ -z "${sa}" ]]; then
  echo "Environment variable ${sa_var} is not set (expected in the CircleCI context, see README)" >&2
  exit 1
fi
if [[ -z "${GCP_WIF_PROVIDER:-}" ]]; then
  echo "GCP_WIF_PROVIDER is not set (expected in the CircleCI context, see README)" >&2
  exit 1
fi

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cfg_dir="${HOME}/.config/akk-gcp"
cfg="${cfg_dir}/${sa_var}.json"
mkdir -p "${cfg_dir}"
chmod 700 "${cfg_dir}"

SA="${sa}" TOKEN_CMD="${here}/oidc_token.sh" CFG="${cfg}" python3 - <<'PY'
import json, os
provider = os.environ["GCP_WIF_PROVIDER"].removeprefix("//iam.googleapis.com/")
config = {
    "type": "external_account",
    "audience": f"//iam.googleapis.com/{provider}",
    "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
    "token_url": "https://sts.googleapis.com/v1/token",
    "service_account_impersonation_url":
        f"https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{os.environ['SA']}:generateAccessToken",
    "credential_source": {"executable": {"command": os.environ["TOKEN_CMD"], "timeout_millis": 30000}},
}
with open(os.environ["CFG"], "w") as f:
    json.dump(config, f, indent=1)
PY

{
  echo "export GOOGLE_APPLICATION_CREDENTIALS='${cfg}'"
  echo "export GOOGLE_EXTERNAL_ACCOUNT_ALLOW_EXECUTABLES=1"
} >> "${BASH_ENV}"
echo "Configured keyless Google credentials (service account from \$${sa_var})"
