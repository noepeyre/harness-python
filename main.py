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

def get_models_dict(server,model_type='llm'):
    accepted_model_type = ['llm','vlm','embedding','decision']
    if model_type == 'all':
        pass
    elif model_type in accepted_model_type:
        accepted_model_type = [model_type]
    else:
        return 22, None
        
    response = requests.get(server+"/api/v1/models")
    models = {}
    if response.status_code != 200:
        return response.status_code, None
    
    rawdata = response.json()
    for model in rawdata['models']:
        if model['type'] in accepted_model_type:
            models[model['key']]=model['display_name']
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
    instances = []
    status, models_dict = get_models_dict(model_type='all') #gets every model as a dict key:name and only keeps the key
    keys = models_dict.keys()

    if status != 200:
        return status, None
    if model_key == 'all': 
        pass
    elif model_key in keys:
        keys = model_key
    else:
        return 22, None

    models = get_models(server, model_type='all')
    for key in keys:
        for model in models:
            if model['key'] == key:
                for instance in model['instances']:
                    instances.append(instance)