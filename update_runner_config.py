import json

config_file = '/home/admin/clarion/lab/orchestrator_config.json'

with open(config_file, 'r') as f:
    config = json.load(f)

for runner in config['runners']:
    if runner['name'] == 'pi-runner-2':
        runner['persona_set'] = ['Engineering']
        print("Set runner-2 to Engineering")
    elif runner['name'] == 'pi-runner-3':
        runner['persona_set'] = ['Finance']
        print("Set runner-3 to Finance")

with open(config_file, 'w') as f:
    json.dump(config, f, indent=4)
