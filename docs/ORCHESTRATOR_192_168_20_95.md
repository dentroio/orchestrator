# Orchestrator at 192.168.20.95 – what to run

The orchestrator uses the **database** (`clarion_lab.db`), not JSON files. To inspect or fix runner config (e.g. pi-runner-6), log into the orchestrator and use the lab repo there.

## 1. Log in

```bash
ssh admin@192.168.20.95
# or: ssh <your-user>@192.168.20.95
```

## 2. Go to the lab directory

```bash
cd ~/clarion/lab
# or wherever the repo is deployed, e.g. /home/admin/clarion/lab
```

## 3. See current runner config (including pi-runner-6)

```bash
python3 set_runner_interface.py --show
```

Example output:

```
  pi-runner-1: interface=eth0, management_interface=wlan0
  pi-runner-2: interface=wlan0, management_interface=eth0
  ...
  pi-runner-6: interface=eth0, management_interface=wlan0
```

## 4. Fix pi-runner-6 (lab=eth0, management=wlan0)

If pi-runner-6 has the wrong interface or management_interface:

```bash
python3 set_runner_interface.py --runner pi-runner-6 --interface eth0 --management-interface wlan0
```

## 5. Restart the orchestrator so it picks up the change

If the dashboard/orchestrator is run by systemd:

```bash
sudo systemctl restart clarion-orchestrator
# or whatever the service name is
```

If you run it manually, stop and start it again so it reloads config from the DB.

## Optional: Inspect the DB directly

```bash
cd ~/clarion/lab
sqlite3 clarion_lab.db "SELECT key, value FROM config WHERE key='runners';"
```

The `value` column is a JSON array of runner objects; each has `name`, `interface`, `management_interface`, etc.
