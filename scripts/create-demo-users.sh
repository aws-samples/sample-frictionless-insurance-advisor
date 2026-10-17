#!/usr/bin/env bash
# Create the three demo advisors (Life, General, and one with every line) in the deployed Cognito user pool.
#
# The pool is closed to self sign-up, so the demo advisors have to be created by
# an administrator. Run this ONCE, after the first deploy. deploy.sh does not run
# it, and there is no need to run it again on later deploys. The password is
# printed ONCE on this terminal and is not stored anywhere: copy it now. Running
# the script again skips users that already exist (passwords are never changed).
#
# The seeded customers belong to these two addresses, so sign in as one of them
# to see data:
#   john.doe@example.com (all lines: life, general and health products)
#                        -> Marcus, Hannah, Carlos, Olivia, Ethan; prospects Sophie, Tom
#   life@example.com     (Life advisor: life and health products)
#                        -> Sarah, Emily, Robert, Amanda, David; prospect Nadia
#   general@example.com  (General / P&C advisor: motor, home, travel and health products)
#                        -> Lisa, Michael, Jessica, Priya; prospects Daniel, Leo
#
# Usage:
#   scripts/create-demo-users.sh
#   DEMO_USER_PASSWORD='your own 12+ char password' scripts/create-demo-users.sh   # same password for both, not printed
#   AWS_PROFILE=... AWS_REGION=... scripts/create-demo-users.sh
#
# Needs: AWS credentials allowed to read the SSM parameter below and to call
# cognito-idp:AdminGetUser, AdminCreateUser and AdminSetUserPassword on the pool.
set -euo pipefail

REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
# The seeded customers belong to these two addresses. DEMO_USER_EMAILS (comma-separated)
# overrides them, for example to test this script with a throwaway address.
IFS=',' read -r -a EMAILS <<< "${DEMO_USER_EMAILS:-john.doe@example.com,life@example.com,general@example.com}"

POOL_ID="$(aws ssm get-parameter --region "$REGION" --name /insurance-advisor/cognito/user-pool-id \
  --query 'Parameter.Value' --output text 2>/dev/null || true)"
if [ -z "$POOL_ID" ] || [ "$POOL_ID" = "None" ]; then
  echo "Could not read /insurance-advisor/cognito/user-pool-id in $REGION. Deploy the backend stacks first." >&2
  exit 1
fi

# 20 characters with upper, lower, digit and symbol, which satisfies the pool's
# password policy (12+ characters with all four classes).
generate_password() {
  python3 - <<'PY'
import secrets, string
classes = [string.ascii_uppercase, string.ascii_lowercase, string.digits, "!@#%^*-_=+"]
pw = [secrets.choice(c) for c in classes]
alphabet = "".join(classes)
pw += [secrets.choice(alphabet) for _ in range(16)]
secrets.SystemRandom().shuffle(pw)
print("".join(pw))
PY
}

created=()
for email in "${EMAILS[@]}"; do
  if aws cognito-idp admin-get-user --region "$REGION" --user-pool-id "$POOL_ID" --username "$email" >/dev/null 2>&1; then
    echo "exists   $email (password left unchanged)"
    continue
  fi
  user_supplied=false
  if [ -n "${DEMO_USER_PASSWORD:-}" ]; then
    password="$DEMO_USER_PASSWORD"; user_supplied=true
  else
    password="$(generate_password)"
  fi
  aws cognito-idp admin-create-user --region "$REGION" --user-pool-id "$POOL_ID" --username "$email" \
    --user-attributes Name=email,Value="$email" Name=email_verified,Value=true \
    --message-action SUPPRESS >/dev/null
  if ! aws cognito-idp admin-set-user-password --region "$REGION" --user-pool-id "$POOL_ID" --username "$email" \
       --password "$password" --permanent >/dev/null 2>&1; then
    # Do not leave a half-created account behind.
    aws cognito-idp admin-delete-user --region "$REGION" --user-pool-id "$POOL_ID" --username "$email" >/dev/null 2>&1 || true
    echo "failed   $email (the password was rejected by the pool's policy; nothing was created)" >&2
    exit 1
  fi
  if [ "$user_supplied" = true ]; then
    echo "created  $email (password: the value of DEMO_USER_PASSWORD)"
  else
    created+=("$email  $password")
    echo "created  $email"
  fi
done

if [ "${#created[@]}" -gt 0 ]; then
  echo
  echo "================ SAVE THESE NOW: shown once, not stored anywhere ================"
  for line in "${created[@]}"; do echo "  $line"; done
  echo "================================================================================="
fi
