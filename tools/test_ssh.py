import paramiko

def test_ssh():
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        print("Connecting to 192.168.1.189...")
        client.connect('192.168.1.189', username='admin', password='C!sco#123', timeout=5, look_for_keys=False, allow_agent=False)
        print("Connected! Running 'show run interface e1/0' or 'show running-config'...")
        stdin, stdout, stderr = client.exec_command('show version | include Software')
        print('VERSION:', stdout.read().decode())
        
        stdin, stdout, stderr = client.exec_command('show run')
        config = stdout.read().decode()
        
        with open('switch_config.txt', 'w') as f:
            f.write(config)
            
        print("Saved config to switch_config.txt")
        num_lines = len(config.splitlines())
        print(f"Config lines: {num_lines}")
        client.close()
    except Exception as e:
        print('ERROR:', e)

if __name__ == "__main__":
    test_ssh()
