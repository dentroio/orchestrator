#!/usr/bin/env bash
# check_iot_backends.sh - Verify IoT backend hosts (192.168.31.2, 192.168.20.3, 192.168.20.4)
# Run from orchestrator or any host that can reach those IPs.
# Usage: ./check_iot_backends.sh [host1 host2 ...]
# Default hosts: 192.168.31.2 192.168.20.3 192.168.20.4

if [ $# -eq 0 ]; then
    HOSTS=(192.168.31.2 192.168.20.3 192.168.20.4)
else
    HOSTS=("$@")
fi
PORTS=(9001 9002 9003 9004 9005 9006 9007 9008 9009 9010)
TIMEOUT=3

echo "=========================================="
echo "Clarion Lab - IoT Backend Connectivity Check"
echo "=========================================="
echo "Hosts: ${HOSTS[*]}"
echo "Ports: ${PORTS[*]}"
echo ""

for host in "${HOSTS[@]}"; do
    echo "--- $host ---"
    reachable=false
    for port in "${PORTS[@]}"; do
        if curl -s -m "$TIMEOUT" "http://${host}:${port}/" 2>/dev/null | grep -q '"status"\|"service"'; then
            echo "  OK   port $port"
            reachable=true
        else
            echo "  FAIL port $port (no response or wrong response)"
        fi
    done
    if [ "$reachable" = false ]; then
        echo "  >> Host $host: no ports responded. Check:"
        echo "     1. Is iot_backend_mock service running? (ssh $host 'sudo systemctl status iot_backend_mock')"
        echo "     2. Firewall: ssh $host 'sudo ufw status' (allow 9001:9010/tcp)"
        echo "     3. Deploy: run setup_iot_backend.sh on $host with lab/ present (e.g. ~/clarion/lab/iot_backend_mock.py)"
    fi
    echo ""
done

echo "=========================================="
echo "If all FAIL for a host: deploy or fix that host (see BACKEND_SERVERS.md)"
echo "=========================================="
