import requests
address = input("Enter the server address: ")
if address == "":
    address = "127.0.0.1"
port = 1234
server = f"http://{address}:{port}"

def get_models(server, model_type='llm'):
    accepted_model_type = ['llm','vlm','embedding','decision']
    if model_type == 'all':
        pass
    elif model_type in accepted_model_type:
        accepted_model_type = [model_type]
    else:
        return 22, None
        
    response = requests.get(server+"/api/v1/models")
    models = []
    if response.status_code != 200:
        return response.status_code, None
    
    rawdata = response.json()
    print(rawdata)
    for model in rawdata['models']:
        if model['type'] in accepted_model_type:
            models.append(model)
    return response.status_code, models

def load(model_key):
    body = {
        "model": model_key
    }
    response = requests.post(server+"/api/v1/models/load",json=body)
    if response.status_code == 200:
        return response.status_code, response.json()
    else:
        return response.status_code, None
def unload(instance):
    body = {
        "instance_id": instance
    }
    response = requests.post(server+"/api/v1/models/unload",json=body)
    if response.status_code == 200:
        return response.status_code, response.json()
    else:
        return response.status_code, None

def get_instances(model_key='all'):
    instances = {}
    status, models = get_models(server,model_type='all')
    if status != 200:
        return status, None
    keys = [model['key'] for model in models]
    if model_key == 'all': 
        pass
    elif model_key in keys:
        keys = [model_key]
    else:
        return 22, None

    for model in models:
        if model['key'] in keys:
            instances[model['key']] = model['loaded_instances']

    return 200, instances