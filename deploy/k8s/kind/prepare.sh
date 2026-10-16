#!/bin/sh
# Prepares a kind deploy, called by `make k8s-deploy` after the images are built:
#
# * tags onramp-core:dev and onramp-ml:dev with their image IDs and loads those into the node,
# * writes deploy/k8s/overlays/kind/.generated/ (ignored by Git): those tags, and the Secret
#   values for the cluster.
#
# Tags are the image IDs, so an unchanged build rolls nothing and a changed one rolls only the
# roles whose image changed. Secrets come from .env (AUTH_JWT_SECRET, POSTGRES_PASSWORD), with
# database URLs pointing at the Compose Postgres by its Compose name.
set -eu
root=$(cd "$(dirname "$0")/../../.." && pwd)
out="$root/deploy/k8s/overlays/kind/.generated"
mkdir -p "$out"

env_value() {
    # Read one key from .env without evaluating the file as shell.
    sed -n "s/^$1=//p" "$root/.env" 2>/dev/null | tail -n 1 | sed 's/^"\(.*\)"$/\1/'
}

jwt=$(env_value AUTH_JWT_SECRET)
[ -n "$jwt" ] || { echo "AUTH_JWT_SECRET is not set in .env" >&2; exit 1; }
password=$(env_value POSTGRES_PASSWORD)
password=${password:-onramp-local}

cluster=${KIND_CLUSTER:-onramp}
tag() { docker image inspect --format '{{.Id}}' "$1:dev" | sed 's/^sha256://' | cut -c1-12; }
core=$(tag onramp-core)
ml=$(tag onramp-ml)
docker tag onramp-core:dev "onramp-core:$core"
docker tag onramp-ml:dev "onramp-ml:$ml"
kind load docker-image --name "$cluster" "onramp-core:$core" "onramp-ml:$ml"

umask 077
cat > "$out/secrets.env" <<SECRETS
DATABASE_URL=postgresql+psycopg://onramp:$password@postgres:5432/onramp
MARKETPLACE_DATABASE_URL=postgresql+psycopg://onramp:$password@postgres:5432/marketplace
AUTH_JWT_SECRET=$jwt
SECRETS

cat > "$out/kustomization.yaml" <<KUSTOMIZATION
apiVersion: kustomize.config.k8s.io/v1alpha1
kind: Component
images:
  - name: onramp-core
    newTag: "$core"
  - name: onramp-ml
    newTag: "$ml"
KUSTOMIZATION
echo "prepared core=$core ml=$ml"
