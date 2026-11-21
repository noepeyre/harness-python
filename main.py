import requests
import time
import shutil

window = shutil.get_terminal_size()

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
    for model in rawdata['models']:
        if model['type'] in accepted_model_type:
            models.append(model)
    return response.status_code, models

def load(server, model_key):
    body = {
        "model": model_key
    }
    response = requests.post(server+"/api/v1/models/load",json=body)
    if response.status_code != 200:
        return response.status_code, None
    return response.status_code, response.json()
        
def unload(server, instance):
    body = {
        "instance_id": instance
    }
    response = requests.post(server+"/api/v1/models/unload",json=body)
    if response.status_code == 200:
        return response.status_code, response.json()
    else:
        return response.status_code, None

def get_instances(server,model_key='all'):
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

def model_selector(models):
    output = ["_"*window.columns]
    for model in models:
        output.append(f"- {model['display_name']}: {model['key']}")
    output.append("_"*window.columns)
    return output

def command(text):
    global previous_response_id, currentmodel, context
    text = text.split()
    status, models = get_instances(server)
    instances = []
    for model in models.values():
        for instance in model:
            instances.append(instance)

    if text == []:
        text = [""]

    if text == ["exit"]:
        for instance in instances:
            unload(server, instance)
        exit()

    elif text[0] == "unload":
        for key in text[1:]:
            if key not in models:
                print(key,"model not found")
            else:
                if not models[key]:
                    print(key, "is not loaded")
                else:
                    for instance in models[key]:
                        unload(server, instance["id"])

    elif text[0] == "load":
        for key in text[1:]:
            if key not in models:
                print(key,"model not found")
            else:
                load(server, key)

    elif text[0] == "model":
        key = text[1]
        if key not in models:
            print(key,"model not found")
        else:
            if not models[key]:
                print(key, "is not loaded")
                choice = input("> Load the model(Y/n)? :")
                if choice.lower() in ["y", "yes", ""]:
                    load(server, key)
                else:
                    return
            currentmodel = key

    elif text == ["new"]:
        previous_response_id = None
        context.clear()

    elif text == ["retry"]:
        if len(context)==0:
            print("You didn't send any messages yet")
        else:
            body = {
                "model": currentmodel,
                "input": context[-1]["input"],
                "system_prompt": "answer without markdown, in the user's language",
                "previous_response_id": context[-1]["parent_id"],
            }
            status, data = chat(body)
            if status == 200:
                context[-1]={
                    "input": context[-1]["input"],
                    "parent_id":context[-1]["parent_id"],
                    "response_id":data["response_id"]
                }
                previous_response_id = data["response_id"]
            else:
                print("ERROR",status)

    else:
        print(f"Unknown command:'{text[0]}'")

def chat(body):
    created = time.time()
    response = requests.post(server+"/api/v1/chat", json=body)
    received = time.time()
    if response.status_code==200:
        rawdata = response.json()
        rawdata['stats']['dt'] = received - created
        for item in rawdata["output"]:
            if item['type']== "message":
                answer = item['content']
        print(answer)
        print(f"~ {round(rawdata['stats']['dt'], 2)}s - {round(rawdata['stats']['tokens_per_second'],2)}tok/s")
        print("_"*window.columns)
        return response.status_code, rawdata
    else:
        return response.status_code, None

address = input("Enter the server address: ")
if address == "localhost":
    address = "127.0.0.1"
if address == "":
    address = "192.168.1.20"
port = 1234
server = f"http://{address}:{port}"

status, models = get_models(server)
if status != 200:
    print(f"Failed to reach server: {status}")
    exit(1)
buffer = model_selector(models)
for line in buffer:
    print(line)
currentmodel = input("MODEL> ")
status, response = load(server, currentmodel)
if status != 200:
    print(f"Failed to load model: {status}")
    exit(1)
print(f"{currentmodel} loaded in {response['load_time_seconds']}s.")

previous_response_id = None
context = []
while True:
    answer = None
    user_input = input("> ")
    if user_input.startswith("/"):
        user_input = user_input.removeprefix("/")
        command(user_input)
    else:
        body = {
            "model": currentmodel,
            "input": user_input,
            "system_prompt": "answer without markdown, in the user's language"
        }
        if previous_response_id is not None:
            body["previous_response_id"] = previous_response_id

        status, data = chat(body)
        if status == 200:
            context.append({
                "input":user_input,
                "parent_id":previous_response_id,
                "response_id":data["response_id"]
            })
            previous_response_id = data["response_id"]
        else:
            print("ERROR",status)

        

example = {
    'model_instance_id': 'google/gemma-4-e4b',
    'output': [
        {'type': 'reasoning', 'content': 'The user said "bonjour" (hello/good day) in French. I should respond with a friendly greeting and ask how they are or what they need in French.'},
        {'type': 'message', 'content': "Bonjour ! Comment puis-je vous aider aujourd'hui ?"}
    ],
    'stats': {
        'input_tokens': 28,
        'total_output_tokens': 52,
        'reasoning_output_tokens': 35,
        'tokens_per_second': 115.87195921307035, 
        'time_to_first_token_seconds': 0.068336
        },
    'response_id': 'resp_d18eb00bcddc0c84dcd342ed447dc5950f306dfda81a1569'
}