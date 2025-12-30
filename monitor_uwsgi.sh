#!/usr/bin/env bash

base=$(dirname "$0")

cd "${base}" || exit 2

source "${base}"/.venv/bin/activate
monitor_uwsgi
