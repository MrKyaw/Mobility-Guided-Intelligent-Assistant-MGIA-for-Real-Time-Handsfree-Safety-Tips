

"""
Text to Speech Module

Authors			    :	Zhengyang Cui
Emails              :   cuiz38@mcmaster.ca
Group No			:   Sixth Smart Sense Generation<br>
Started Date        :   Feb 20, 2025<br>
Version             :   1<br>
Released Date    	: 	<br>
Version             :   2 <br>

CopyRight@2024 by Sixth Smart Sense Generation<br>
"""
from pathlib import Path
from gtts import gTTS

def tts(text):
    if not isinstance(text, str):
        raise TypeError("input should be a string")
    this_file_path = Path(__file__).resolve()
    target_path = this_file_path.parent.parent / "data/download/speech/output.mp3"

    gTTS(text=text, lang="en").save(str(target_path))
    
if __name__ == "__main__":
    tts("Hello, World!")