#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends nginx
cat > /etc/nginx/conf.d/beam-tunnel.conf <<'EOF'
map $http_upgrade $beam_tunnel_connection {
    default upgrade;
    '' close;
}
map $http_x_forwarded_proto $beam_tunnel_proto {
    default $scheme;
    https https;
    http http;
}
server {
    listen 8000 default_server;
    listen [::]:8000 default_server;
    server_name _;
    client_max_body_size 10m;
    location / {
        proxy_pass http://127.0.0.1:9000;
        proxy_http_version 1.1;
        proxy_set_header Host localhost;
        proxy_set_header X-Forwarded-Host $http_host;
        proxy_set_header X-Forwarded-Proto $beam_tunnel_proto;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $beam_tunnel_connection;
        proxy_buffering off;
        proxy_request_buffering off;
        proxy_connect_timeout 3s;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
EOF
nginx -t
systemctl enable --now nginx
systemctl reload nginx
systemctl is-active nginx
printf 'Relay ready on port 8000; waiting for the SSH forward on loopback port 9000.\n'
cat /etc/ssh/ssh_host_ed25519_key.pub
