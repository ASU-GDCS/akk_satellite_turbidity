#!/usr/bin/env bash
# Print the (non-secret) claims of this job's CircleCI OIDC token, for comparing
# with the GCP provider's issuer, allowed audience and attribute condition.
# Only the decoded payload is printed; the token and its signature never are.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"${here}/oidc_token.sh" | python3 -c '
import base64, json, sys
out = json.load(sys.stdin)
if not out.get("success"):
    sys.exit("No OIDC token: " + out.get("message", "unknown error"))
payload = out["id_token"].split(".")[1]
claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
keys = ["iss", "aud", "sub", "oidc.circleci.com/project-id", "oidc.circleci.com/vcs-ref",
        "oidc.circleci.com/vcs-origin", "oidc.circleci.com/context-ids"]
for k in keys:
    print(k.ljust(32), claims.get(k, "<missing>"))
'
