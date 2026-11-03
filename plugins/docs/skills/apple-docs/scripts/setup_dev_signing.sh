#!/usr/bin/env bash
# Reference: https://github.com/Dimillian/Skills (macos-spm-app-packaging/assets/templates), MIT, Copyright (c) 2026 Thomas Ricouard. See LICENSE beside this file.
# Setup stable development code signing to reduce keychain prompts.
set -euo pipefail

APP_NAME=${APP_NAME:-MyApp}
CERT_NAME="${APP_NAME} Development"

if security find-certificate -c "$CERT_NAME" >/dev/null 2>&1; then
  echo "Certificate '$CERT_NAME' already exists."
  echo "Export this in your shell profile:"
  echo "  export APP_IDENTITY='$CERT_NAME'"
  exit 0
fi

echo "Creating self-signed certificate '$CERT_NAME'..."

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
TEMP_CONFIG="$WORK/cert.cnf"
P12_PASS=$(openssl rand -hex 16)
# OpenSSL 3 writes PKCS#12 files that macOS's `security import` cannot read; its legacy mode writes ones it can.
LEGACY=()
if openssl version | grep -q '^OpenSSL 3'; then LEGACY=(-legacy); fi

cat > "$TEMP_CONFIG" <<EOFCONF
[ req ]
distinguished_name = req_distinguished_name
x509_extensions = v3_req
prompt = no

[ req_distinguished_name ]
CN = $CERT_NAME
O = ${APP_NAME} Development
C = US

[ v3_req ]
keyUsage = critical,digitalSignature
extendedKeyUsage = codeSigning
EOFCONF

openssl req -x509 -newkey rsa:4096 -sha256 -days 3650 \
    -nodes -keyout "$WORK/dev.key" -out "$WORK/dev.crt" \
    -config "$TEMP_CONFIG"

openssl pkcs12 -export "${LEGACY[@]}" -out "$WORK/dev.p12" \
    -inkey "$WORK/dev.key" -in "$WORK/dev.crt" \
    -passout "pass:$P12_PASS"

security import "$WORK/dev.p12" -k ~/Library/Keychains/login.keychain-db \
  -P "$P12_PASS" -T /usr/bin/codesign -T /usr/bin/security

echo ""
echo "Trust this certificate for code signing in Keychain Access."
echo "Then export in your shell profile:"
echo "  export APP_IDENTITY='$CERT_NAME'"
