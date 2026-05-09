#!/bin/bash

set -euo pipefail

cd "$(dirname "$0")"

echo "[try-on] setup.sh is a compatibility wrapper. Delegating to ./install.sh ..."
exec ./install.sh
