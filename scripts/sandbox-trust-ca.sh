#!/bin/bash
# Test-environment-only: make Chrome in the selenium container trust the Claude Code sandbox's TLS-inspecting egress CAs
# (the O=Anthropic certificates inside /root/.ccr/ca-bundle.crt). Never turns certificate checking off.
# Run after `docker compose up`, and again whenever the selenium container is recreated.
set -e
BUNDLE="${1:-/root/.ccr/ca-bundle.crt}"
SEL=$(docker ps --format '{{.Names}}' | grep selenium-chrome | head -1)
DIR=$(mktemp -d)
python3 -I - "$BUNDLE" "$DIR" <<'PY'
import re, subprocess, sys
bundle, out_dir = sys.argv[1], sys.argv[2]
n = 0
for cert in re.findall(r'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----', open(bundle).read(), flags=re.S):
    subject = subprocess.run(['openssl', 'x509', '-noout', '-subject'], input=cert, capture_output=True, text=True).stdout
    if 'O = Anthropic' in subject or 'O=Anthropic' in subject:
        open(f'{out_dir}/sandbox-ca-{n}.pem', 'w').write(cert + '\n')
        n += 1
print(f'{n} sandbox CA certificates found')
PY
for pem in "$DIR"/*.pem; do
    name=$(basename "$pem" .pem)
    docker cp "$pem" "$SEL:/tmp/$name.pem"
    docker exec "$SEL" certutil -d sql:/home/seluser/.pki/nssdb -D -n "$name" 2>/dev/null || true
    docker exec "$SEL" certutil -d sql:/home/seluser/.pki/nssdb -A -t "C,," -n "$name" -i "/tmp/$name.pem"
done
rm -rf "$DIR"
docker exec "$SEL" certutil -d sql:/home/seluser/.pki/nssdb -L | grep sandbox-ca
