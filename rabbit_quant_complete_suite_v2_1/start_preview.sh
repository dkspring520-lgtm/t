#!/usr/bin/env bash
cd "$(dirname "$0")/frontend"
python -m http.server 8080
