import json
import random

def randomize_linkedin_topics(input_file, output_file):
    try:
        # 1. JSON file ko read karna
        with open(input_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # 2. Elements ko shuffle/randomize karna
        # random.shuffle() list ko original jagah par hi shuffle kar deta hai
        random.shuffle(data)
        
        # 3. Output ko naye JSON file me save karna
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
            
        print(f"Success: Saare elements randomize ho gaye hain aur '{output_file}' me save ho chuke hain.")
        
    except FileNotFoundError:
        print(f"Error: '{input_file}' file nahi mili. Kripya check karein ki file sahi folder me hai.")
    except json.JSONDecodeError:
        print("Error: JSON file ka format sahi nahi hai.")

# Program ko chalane ke liye function call
randomize_linkedin_topics('linkedin_topics.json', 'priyanka_linkedin_topics.json')