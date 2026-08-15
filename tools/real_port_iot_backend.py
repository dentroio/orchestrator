#!/usr/bin/env python3
"""
Simple TCP/protocol listeners on realistic IoT/OT ports.

This is lightweight but protocol-aware: it responds with valid headers for
HTTP, HTTPS, Modbus/TCP, MQTT, RTSP, SIP, and EtherNet/IP so Clarion's
DPI engine can detect realistic application signatures.
"""

import logging
import os
import socket
import ssl
import subprocess
import threading
import time

PORTS = {
    443: "Badge Reader",
    80: "Display",
    554: "Camera",
    1883: "Environmental Sensor",
    5060: "VoIP Phone",
    47808: "HVAC Controller",
    8883: "Door Lock",
    9100: "Printer",
    104: "Medical Device",
    502: "PLC",
    44818: "Industrial Gateway",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [RealPortIoT] - %(message)s",
    handlers=[logging.FileHandler("/tmp/real_port_iot_backend.log"), logging.StreamHandler()],
)
logger = logging.getLogger("RealPortIoT")

SSL_CONTEXT = None

def ensure_ssl_certs():
    global SSL_CONTEXT
    certfile = "/tmp/cert.pem"
    keyfile = "/tmp/key.pem"
    if not os.path.exists(certfile) or not os.path.exists(keyfile):
        logger.info("Generating self-signed SSL certificate...")
        try:
            subprocess.run([
                "openssl", "req", "-new", "-newkey", "rsa:2048", "-days", "365",
                "-nodes", "-x509", "-subj", "/C=US/O=ClarionLab/CN=192.168.30.4",
                "-keyout", keyfile, "-out", certfile
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            logger.info("SSL certificate generated successfully")
        except Exception as e:
            logger.error("Failed to generate SSL certificate: %s", e)
            return

    try:
        SSL_CONTEXT = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        SSL_CONTEXT.load_cert_chain(certfile=certfile, keyfile=keyfile)
        logger.info("SSL Context loaded successfully")
    except Exception as e:
        logger.error("Failed to load SSL context: %s", e)

def handle_client(conn: socket.socket, addr: tuple, port: int, label: str) -> None:
    try:
        # For SSL, wrap the connection
        if port == 443 and SSL_CONTEXT:
            try:
                conn = SSL_CONTEXT.wrap_socket(conn, server_side=True)
            except Exception as ssl_err:
                logger.debug("SSL handshake failed with %s: %s", addr, ssl_err)
                return

        # Read request data (non-blocking style via timeout)
        conn.settimeout(2.0)
        try:
            data = conn.recv(1024)
        except socket.timeout:
            data = b""

        # Protocol-aware response logic
        if port in (80, 443):
            # HTTP/HTTPS response
            resp = (
                "HTTP/1.1 200 OK\r\n"
                "Server: ClarionMock/2.0\r\n"
                "Content-Type: text/html; charset=UTF-8\r\n"
                "Content-Length: 140\r\n"
                "Connection: close\r\n\r\n"
                f"<html><head><title>IoT {label}</title></head>"
                f"<body><h1>Status: OK</h1><p>{label} online.</p></body></html>\n"
            )
            conn.sendall(resp.encode("utf-8"))
            logger.info("Sent HTTP 200 to %s for %s", addr[0], label)

        elif port == 502:
            # Modbus TCP Read Holding Registers Response
            if len(data) >= 12:
                tid = data[0:2]
                uid = data[6:7]
                # Function Code 3, Byte Count 2, Register Value 42 (0x002a)
                resp = tid + b"\x00\x00\x00\x05" + uid + b"\x03\x02\x00\x2a"
                conn.sendall(resp)
                logger.info("Sent Modbus/TCP response to %s", addr[0])
            else:
                conn.sendall(b"PLC tcp/502 ready\n")

        elif port == 554:
            # RTSP OPTIONS/DESCRIBE
            req_str = data.decode("utf-8", errors="ignore")
            cseq = "1"
            for line in req_str.splitlines():
                if line.lower().startswith("cseq:"):
                    cseq = line.split(":", 1)[1].strip()
            resp = (
                "RTSP/1.0 200 OK\r\n"
                f"CSeq: {cseq}\r\n"
                "Public: OPTIONS, DESCRIBE, SETUP, PLAY, TEARDOWN\r\n"
                "Content-Length: 0\r\n\r\n"
            )
            conn.sendall(resp.encode("utf-8"))
            logger.info("Sent RTSP response to %s", addr[0])

        elif port == 1883:
            # MQTT CONNACK (response to CONNECT)
            if len(data) > 0 and data[0] == 0x10:
                conn.sendall(b"\x20\x02\x00\x00")
                logger.info("Sent MQTT CONNACK to %s", addr[0])
            else:
                conn.sendall(b"MQTT Broker ready\n")

        elif port == 5060:
            # SIP OPTIONS/REGISTER
            req_str = data.decode("utf-8", errors="ignore")
            cseq = "1 REGISTER"
            for line in req_str.splitlines():
                if line.lower().startswith("cseq:"):
                    cseq = line.split(":", 1)[1].strip()
            resp = (
                "SIP/2.0 200 OK\r\n"
                f"CSeq: {cseq}\r\n"
                "Content-Length: 0\r\n\r\n"
            )
            conn.sendall(resp.encode("utf-8"))
            logger.info("Sent SIP response to %s", addr[0])

        elif port == 44818:
            # EtherNet/IP Register Session response
            if len(data) >= 24 and data[0:2] == b"\x65\x00":
                context = data[12:20]
                resp = b"\x65\x00\x04\x00\x01\x00\x00\x00\x00\x00\x00\x00" + context + b"\x00\x00\x00\x00"
                conn.sendall(resp)
                logger.info("Sent EtherNet/IP response to %s", addr[0])
            else:
                conn.sendall(b"EtherNet/IP gateway ready\n")

        else:
            # General fallback TCP response
            conn.sendall(f"{label} tcp/{port} ready\n".encode("utf-8"))

    except Exception as e:
        logger.error("Error handling client %s on port %s: %s", addr, port, e)
    finally:
        try:
            conn.close()
        except OSError:
            pass

def serve(port: int, label: str) -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind(("0.0.0.0", port))
        server.listen(128)
        logger.info("Listening on tcp/%s for %s", port, label)
    except Exception as e:
        logger.error("Failed to bind to port %s: %s", port, e)
        return

    while True:
        try:
            conn, addr = server.accept()
            # Handle client in a separate thread so multiple ports don't block
            t = threading.Thread(target=handle_client, args=(conn, addr, port, label), daemon=True)
            t.start()
        except Exception as e:
            logger.error("Accept error on port %s: %s", port, e)
            time.sleep(1)

def main() -> None:
    ensure_ssl_certs()
    threads = []
    for port, label in PORTS.items():
        thread = threading.Thread(target=serve, args=(port, label), daemon=True)
        thread.start()
        threads.append(thread)
    logger.info("All protocol-aware TCP listeners started")
    while True:
        time.sleep(60)

if __name__ == "__main__":
    main()
