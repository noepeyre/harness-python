import requests
import time
from copy import deepcopy

def get_models(model_type='llm'):
    response = requests.get("http://127.0.0.1:1234/api/v1/models")
    models = {}
    if response.status_code == 200:
        data = response.json()
        for model in data['models']:
            if model['type']==model_type:
                models[model['key']]=model['display_name']
    else:
        print(f"Error: {response.status_code}")
    return models

def load_model(model):
    global loaded_models
    loaded_models = []
    response = requests.post("http://localhost:1234/api/v1/models/load",json=model)
    if response.status_code == 200:
        loaded_models.append(response.json())
        return "Model successfully loaded."
    else:
        return response.status_code, "An error occurred"


def unload_model(model):
    global loaded_models
    response = requests.post("http://localhost:1234/api/v1/models/unload",json=model)
    if response.status_code == 200:
        loaded_models.remove(response.json()['instance_id'])
        return "Model successfully unloaded."
    else:
        return response.status_code, "An error occurred"


def switch_roles(context): # changes user to assistant and vice versa in the llm's context
    for message in context:
        if message["role"] == "user":
            message["role"] = "assistant"
        elif message["role"] == "assistant":
            message["role"] = "user"
    return context

def generate(context):
    ctx = deepcopy(context)
    del ctx[0]
    ctx.insert(0,{"role": "system", "content": "you are user and you will continue this chat normally, without using markdown, here is the context"})
    ctx = switch_roles(ctx)
    body = {
        "model": choice,
        "messages": ctx
    }
    response = requests.post("http://localhost:1234/v1/chat/completions", json=body)
    if response.status_code == 200:
        answer = response.json()["choices"][0]["message"]["content"]
        return answer
    else:
        return None

models = get_models()
items = list(models.items())
for i, (key, value) in enumerate(items):
    print(f"{i+1}: {value}")
choice= int(input("Enter the index of the model to load: "))-1
choice=items[choice][0]
print(choice,"is loading...")
choice = str(choice)

model = {
    "model": choice
}
print(load_model(model))

context = [{"role": "system", "content": "answer without markdown, in the user first message's language"}]
history = []
while True:
    chat = input("> ")
    if chat == "exit":
        break
    elif chat == "list":
        for value in items:
            print(f"{value}")
    elif chat == "info":
        print(rawdata[len(rawdata)])
    elif chat== "ctx":
        print(context)
    elif chat== "debug":
        print(rawdata)
    elif chat=="tok":
        print(f"~ {round(dt, 2)}s - {round(toks)}tok/s - {rawdata["usage"]["prompt_tokens"]}+{rawdata["usage"]["completion_tokens"]} / {tok} total tokens")

    elif chat == "":
        chat = generate(context)

        if chat is None:
            chat = input("> ")
            if chat == "exit":
                break
        else:
            print("=->",chat)
    else:
        context.append({"role": "user", "content": chat})
        body = {
            "model": choice,
            "messages": context
        }
        created = time.time()
        response = requests.post("http://localhost:1234/v1/chat/completions", json=body)
        received = time.time()
        if response.status_code==200:
            rawdata = response.json()
            history.append(rawdata)
            answer = rawdata["choices"][0]["message"]["content"]
            print(answer)
            context.append({"role": "assistant", "content": answer})
            dt = received - created
            tok = rawdata["usage"]["total_tokens"]
            toks = tok / dt
            print(f"~ {round(dt, 2)}s - {round(toks)}tok/s")
        else:
            print("ERROR",response.status_code)

for model in loaded_models: # unload all models
    model = {
        "instance_id": model['instance_id']
    }
    print(unload_model(model))



    