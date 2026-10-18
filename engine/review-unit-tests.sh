#!/usr/bin/env bash
# Optional pre-review test runner. Prints a summary, or "skip (...)".
# Exit 0 on skip. The test runner's exit code otherwise.
#
# Discovers, in order:
#   1. a parent directory with bin/cli and bin/test/unit
#   2. a parent directory with bin/_common (compose exec)
# Remaining arguments are forwarded. -c is added when
# dev/tests/unit/phpunit.xml.dist exists and the caller did not pass one.
set -u

shop=$(pwd)
user=deployer
service=storeapp
args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --user)
      user=${2:-deployer}
      shift 2
      ;;
    --service)
      service=${2:-storeapp}
      shift 2
      ;;
    *)
      args+=("$1")
      shift
      ;;
  esac
done

parent=$(CDPATH= cd -- "$shop/.." && pwd)
if [[ -x "$parent/bin/cli" && -f "$parent/bin/test/unit" ]]; then
  if ! "$parent/bin/cli" true >/dev/null 2>&1; then
    echo "skip (docker not ready)"
    exit 0
  fi
  if [[ ${#args[@]} -eq 0 ]]; then
    echo "skip (bin/test/unit needs a path)"
    exit 0
  fi
  (cd "$parent" && bin/test/unit "${args[@]}")
  exit $?
fi

env=""
dir=$shop
for _ in 1 2 3 4 5 6; do
  if [[ -f "$dir/bin/_common" ]]; then
    env=$dir
    break
  fi
  next=$(dirname "$dir")
  [[ "$next" == "$dir" ]] && break
  dir=$next
done
if [[ -z "$env" ]]; then
  echo "skip (no bin/test/unit and no bin/_common)"
  exit 0
fi

set +e
# shellcheck disable=SC1091
source "$env/bin/_common"
ready=$?
set -e
if [[ "$ready" -ne 0 ]]; then
  echo "skip (environment not ready)"
  exit 0
fi

cid=$(compose ps -q "$service" 2>/dev/null || true)
if [[ -z "$cid" ]]; then
  echo "skip (docker not ready)"
  exit 0
fi

phpunit=(vendor/bin/phpunit)
has_config=0
for arg in "${args[@]}"; do
  if [[ "$arg" == "-c" || "$arg" == "--configuration" || "$arg" == --configuration=* ]]; then
    has_config=1
  fi
done
if [[ "$has_config" -eq 0 && -f "$shop/dev/tests/unit/phpunit.xml.dist" ]]; then
  phpunit+=(-c dev/tests/unit/phpunit.xml.dist)
fi
if [[ ${#args[@]} -gt 0 ]]; then
  phpunit+=("${args[@]}")
fi

compose exec -T -u "$user" "$service" "${phpunit[@]}"
