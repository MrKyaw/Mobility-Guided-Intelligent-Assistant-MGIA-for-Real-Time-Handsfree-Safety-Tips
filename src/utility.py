
"""
Utility Module

Authors			    :	Kyaw Kyaw Oo <br>
Emails              :   kyaw.consulting@gmail.com<br>
Group No			:   Sixth Smart Sense Generation<br>
Started Date        :   Feb 24, 2025<br>
Version             :   1<br>
Released Date    	: 	<br>>
Version             :   2 <br>

CopyRight@2024 by Sixth Smart Sense Generation<br>
"""

# loading basic library
import os
import requests
import json
from io import BytesIO

# loading visualization library
import matplotlib.pyplot as plt
from PIL import Image



# loading libraries
from dotenv import load_dotenv, find_dotenv

class Utility():
    def __init__(self):
        pass

    def load_env(self,env_file ):
        # Use the env_file instance variable to load the .env file
        _ = load_dotenv(env_file)

    def llama32(messages, model_size=11):
        model = f"meta-llama/Llama-3.2-{model_size}B-Vision-Instruct-Turbo"
        url = f"{os.getenv('DLAI_TOGETHER_API_BASE', 'https://api.together.xyz')}/v1/chat/completions"
        print(url)
        payload = {
            "model": model,
            "max_tokens": 4096,
            "temperature": 0.0,
            "stop": ["<|eot_id|>","<|eom_id|>"],
            "messages": messages
        }

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.getenv('TOGETHER_API_KEY')}"
        }
        res = json.loads(requests.request("POST", url, headers=headers, data=json.dumps(payload)).content)

        if 'error' in res:
            raise Exception(res['error'])

        return res['choices'][0]['message']['content']
    
    def disp_image(address):
        if address.startswith("http://") or address.startswith("https://"):
            response = requests.get(address)
            img = Image.open(BytesIO(response.content))
        else:
            img = Image.open(address)
        
        plt.imshow(img)
        plt.axis('off')
        plt.show()

    def resize_image(img, max_dimension = 1120):
        original_width, original_height = img.size

        if original_width > original_height:
            scaling_factor = max_dimension / original_width
        else:
            scaling_factor = max_dimension / original_height

        new_width = int(original_width * scaling_factor)
        new_height = int(original_height * scaling_factor)

        # Resize the image while maintaining aspect ratio
        resized_img = img.resize((new_width, new_height))

        resized_img.save("images/resized_image.jpg")

        print("Original size:", original_width, "x", original_height)
        print("New size:", new_width, "x", new_height)

        return resized_img
    

if __name__ == "__main__":
     # Initialize visual perception system
    util = Utility()
    
    # Detect objects
    env_file_path = os.path.join(os.getcwd(), 'config', '.env') 
    usage = util.load_env(env_file_path)
    print(usage)