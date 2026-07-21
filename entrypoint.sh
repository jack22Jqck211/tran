#!/bin/sh
set -e

# The /data path is a Railway volume mounted at container start,
# so any chown done at image-build time is overwritten by the mount.
# Fix ownership here, every time the container starts, before dropping
# from root down to the unprivileged appuser.
mkdir -p /data/hf
chown -R appuser:appuser /data

exec gosu appuser "$@"
