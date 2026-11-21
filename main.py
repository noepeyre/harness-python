import requests
import time
import shutil

window = shutil.get_terminal_size()
timeout = 15

def get_models(server, model_type='llm'): #returns a list of each model's key
    accepted_model_type = ['llm','vlm','embedding','decision']
    if model_type == 'all':
        pass
    elif model_type in accepted_model_type:
        accepted_model_type = [model_type]
    else:
        return 22, None
        
    response = requests.get(
        server+"/api/v1/models",
        timeout=timeout
    )
    models = []
    if response.status_code != 200:
        return response.status_code, None
    
    rawdata = response.json()
    for model in rawdata['models']:
        if model['type'] in accepted_model_type:
            models.append(model)
    return response.status_code, models

def load(server, model_key): #load the specified model
    body = {
        "model": model_key
    }
    response = requests.post(
        server+"/api/v1/models/load",
        json=body,
        timeout=timeout
    )
    if response.status_code != 200:
        return response.status_code, None
    return response.status_code, response.json()
        
def unload(server, instance): #load the specified model
    body = {
        "instance_id": instance
    }
    response = requests.post(
        server+"/api/v1/models/unload",
        json=body,
        timeout=timeout
    )
    if response.status_code == 200:
        return response.status_code, response.json()
    else:
        return response.status_code, None

def get_instances(server,model_key='all'): #get a dict of all loaded instances {model_key:[loaded_instances]}
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

def model_selector(models): #displays all models
    output = ["_"*window.columns]
    output.append("")
    for model in models:
        output.append(f"- {model['display_name']}: {model['key']}")
    output.append("_"*window.columns)
    return output

def command(text): #manages "/" commands
    global previous_response_id, currentmodel, context
    text = text.split()
    
    if text == []:
        text = [""]

    if text == ["exit"]: #unloads all loaded models
        status, models = get_instances(server)
        if status != 200:
            print("Failed to retrieve models:", status)
            return
        for model in models.values():
            for instance in model:
                unload(server, instance["id"])
        exit()

    elif text[0] == "unload": #unload every model specified
        status, models = get_instances(server)
        if status != 200:
            print("Failed to retrieve models:", status)
            return
        for key in text[1:]: 
            if key not in models: #check if key is valid
                print(key,"model not found")
            else:
                if not models[key]: #check if model is loaded
                    print(key, "is not loaded")
                else: 
                    for instance in models[key]: #unload all models
                        unload(server, instance["id"])

    elif text[0] == "load": #load every model specified
        status, models = get_instances(server)
        if status != 200:
            print("Failed to retrieve models:", status)
            return
        for key in text[1:]:
            if key not in models: #check if key is valid
                print(key,"model not found")
            else:
                load(server, key) #load all models

    elif text[0] == "model": #change current model
        if len(text) < 2:
            print("No model specified.")
            return
        status, models = get_instances(server)
        if status != 200:
            print("Failed to retrieve models:", status)
            return
        key = text[1]
        if key not in models: #check if key is valid
            print(key,"model not found")
        else:
            if not models[key]: #check if model is loaded
                print(key, "is not loaded")
                choice = input("> Load the model(Y/n)? :")
                if choice.lower() in ["y", "yes", ""]: #load the model
                    load(server, key)
                else:
                    return
            currentmodel = key

    elif text == ["new"]: #start a new chat all over
        previous_response_id = None
        context.clear()

    elif text[0] == "retry": #retry from a message
        if len(context)==0:
            print("You didn't send any messages yet")
            return
        if len(text) == 1: #default is last message
            index = len(context) - 1
        else:
            try:
                index = int(text[1])
            except ValueError:
                print("Invalid id:", text[1])
                return
            
        if 0 <= index < len(context): 
            body = {
                "model": currentmodel,
                "input": context[index]["input"],
                "system_prompt": "answer without markdown, in the user's language",
                "previous_response_id": context[index]["parent_id"],
            } #rollback
            status, data = chat(body)
            if status == 200:
                context[index]={
                    "input": context[index]["input"],
                    "parent_id":context[index]["parent_id"],
                    "response_id":data["response_id"]
                }
                previous_response_id = data["response_id"]
                del context[index+1:] #update context
            else:
                print("ERROR",status)
        else:
            print("Message with id",index,"not found.")

    elif text == ["help"]: #prints help message
        print("_"*window.columns)
        print("")
        print("/model [model_key] : choose what model to use")
        print("/load [model_key] [optional_model_key]: choose what model to load")
        print("/unload [model_key] [optional_model_key]: choose what model to unload")
        print("/retry [message_id]: re-generate a message")
        print("/new : starts a new chat")
        print("/exit : unload all models and exits the app")
        print("_"*window.columns)
    else:
        print(f"Unknown command:'{text[0]}'")

def chat(body): #chat logic
    global context
    answer = ""
    created = time.time()
    response = requests.post(server+"/api/v1/chat", json=body)
    received = time.time()
    if response.status_code==200:
        rawdata = response.json()
        rawdata['stats']['dt'] = received - created
        for item in rawdata["output"]:
            if item['type']== "message":
                answer = item['content'] #retrieves content from json output
        print(answer)
        print(f"[{len(context)}]~ {round(rawdata['stats']['dt'], 2)}s - {round(rawdata['stats']['tokens_per_second'],2)}tok/s")
        print("_"*window.columns)
        return response.status_code, rawdata
    else:
        return response.status_code, None

#######################################################

address = input("Enter the server address: ") #basic config
if address == "localhost":
    address = "127.0.0.1"
if address == "":
    address = "192.168.1.20"
port = 1234
server = f"http://{address}:{port}"

status, models = get_models(server) #stores models
if status != 200:
    print(f"Failed to reach server: {status}")
    exit(1)
buffer = model_selector(models) #model selector
for line in buffer:
    print(line)
currentmodel = input("MODEL> ")
status, response = load(server, currentmodel) #load selected model
if status != 200:
    print(f"Failed to load model: {status}")
    exit(1)
print(f"{currentmodel} loaded in {response['load_time_seconds']}s.") #loading stats
print("")

previous_response_id = None
context = []
while True: #main chat loop
    user_input = input("> ")
    if user_input.startswith("/"): #detect command
        user_input = user_input.removeprefix("/")
        command(user_input)
    else:
        body = {
            "model": currentmodel,
            "input": user_input,
            "system_prompt": "answer without markdown, in the user's language"
        }
        if previous_response_id is not None: #store previous_response_id
            body["previous_response_id"] = previous_response_id

        status, data = chat(body)
        if status == 200: #update context
            context.append({
                "input":user_input,
                "parent_id":previous_response_id,
                "response_id":data["response_id"]
            })
            previous_response_id = data["response_id"] #store response_id
        else:
            print("ERROR",status)