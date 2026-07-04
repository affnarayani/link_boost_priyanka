import json
from datetime import datetime, timedelta

# 1. JSON file ko load karein
file_name = 'scraped_connections.json'

try:
    with open(file_name, 'r') as file:
        data = json.load(file)
except FileNotFoundError:
    print(f"Error: '{file_name}' file nahi mili. Please check karein.")
    exit()

# 2. Total elements calculate karein
total_elements = len(data)

# 3. Total withdrawn = True calculate karein
total_withdrawn_true = sum(1 for item in data if item.get('withdrawn') is True)

# 4. Remaining withdrawal calculate karein
remaining_withdrawal = total_elements - total_withdrawn_true

# 5. Latest timestamp find karein
timestamps = []
for item in data:
    if 'timestamp' in item and item['timestamp']:
        try:
            # String timestamp ko datetime object me convert karein
            dt = datetime.strptime(item['timestamp'], "%Y-%m-%d %H:%M:%S")
            timestamps.append(dt)
        except ValueError:
            pass # Agar koi timestamp galat format me ho toh use skip karein

# Target timestamp aur status logic
target_timestamp_str = "N/A"
if timestamps:
    latest_timestamp = max(timestamps)
    # Latest timestamp me 7 days add karke target timestamp banayein
    future_timestamp = latest_timestamp + timedelta(days=7)
    target_timestamp_str = future_timestamp.strftime('%Y-%m-%d %H:%M:%S')
    
    # Current system time
    current_time = datetime.now()
    
    # Decision check
    if future_timestamp > current_time:
        status_message = "You Need to Wait"
    else:
        status_message = "Go Ahead"
else:
    latest_timestamp = "N/A"
    status_message = "No valid timestamp found to check"

# --- Output Screen par print karein ---
print("--- RESULTS ---")
print(f"Total elements: {total_elements}")
print(f"Total withdraw true: {total_withdrawn_true}")
print(f"Remaining withdrawal: {remaining_withdrawal}")

if isinstance(latest_timestamp, datetime):
    print(f"Last/Latest timestamp: {latest_timestamp.strftime('%Y-%m-%d %H:%M:%S')}")
else:
    print(f"Last/Latest timestamp: {latest_timestamp}")

# Naya print statement target timestamp ke liye
print(f"Target timestamp (Latest + 7 days): {target_timestamp_str}")
print(f"Status: {status_message}")