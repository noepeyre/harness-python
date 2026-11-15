import requests

def get_models(model_type='llm'):
    accepted_model_type = ['llm','vlm','embedding','decision']
    if model_type == 'all':
        pass
    elif model_type in accepted_model_type:
        accepted_model_type = [model_type]
    else:
        return 22, None
        
    response = requests.get("http://127.0.0.1:1234/api/v1/models")
    models = []
    if response.status_code != 200:
        return response.status_code, None
    
    rawdata = response.json()
    for model in rawdata['models']:
        if model['type'] in accepted_model_type:
            models.append(model)
    return response.status_code, models

def get_models_dict(model_type='llm'):
    accepted_model_type = ['llm','vlm','embedding','decision']
    if model_type == 'all':
        pass
    elif model_type in accepted_model_type:
        accepted_model_type = [model_type]
    else:
        return 22, None
        
    response = requests.get("http://127.0.0.1:1234/api/v1/models")
    models = {}
    if response.status_code != 200:
        return response.status_code, None
    
    rawdata = response.json()
    for model in rawdata['models']:
        if model['type'] in accepted_model_type:
            models[model['key']]=model['display_name']
    return response.status_code, models

def load(model):
    body = {
        "model": model
    }
    response = requests.post("http://localhost:1234/api/v1/models/load",json=body)
    if response.status_code == 200:
        return response.status_code, response.json()
    else:
        return response.status_code, None
def unload(instance):
    body = {
        "instance_id": instance
    }
    response = requests.post("http://localhost:1234/api/v1/models/unload",json=body)
    if response.status_code == 200:
        return response.status_code, response.json()
    else:
        return response.status_code, None

def get_instances(model='all'):
    status, models_dict = get_models_dict(model_type='all') #gets every model as a dict key:name and only keeps the key
    keys = models_dict.keys()

    if status != 200:
        return status, None
    if model == 'all': 
        pass
    elif model in keys:
        keys = model
    else:
        return 22, None
    #here i want to return the list instances of each model of the list keys