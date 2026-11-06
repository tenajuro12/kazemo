#!/bin/sh
# Container demo: process the bundled synthetic posts and serve the dashboard on port 8000.
# Used by the staging deployment in the Jenkinsfile and by deploy/k8s/deployment.yaml.
set -eu
mkdir -p /data
cp /app/examples/demo_raw.jsonl /data/raw.jsonl
kazemo run /app/deploy/demo.toml --skip-collect > /data/run.log
exec kazemo serve /data --host 0.0.0.0 --port "${PORT:-8000}"
