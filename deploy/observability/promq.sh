#!/bin/sh
# Instant Prometheus query against the observability container, one line per series.
# usage: deploy/observability/promq.sh 'sum by (group) (onramp_consumer_lag)'
docker compose exec -T observability sh -c \
  "curl -s --get localhost:9090/api/v1/query --data-urlencode 'query=$1'" | python3 -c "
import json, sys
hidden = {'__name__', 'instance', 'job', 'service_instance_id', 'service_namespace',
          'deployment_environment'}
for r in json.load(sys.stdin)['data']['result']:
    labels = {k: v for k, v in r['metric'].items() if k not in hidden}
    print(labels, r['value'][1])"
