import random
import re

def generate_random_time():
    minute = random.randint(0, 59)
    hour = random.randint(2, 22)
    return minute, hour

def generate_times(n):
    times = []
    attempts = 0
    max_attempts = 10000
    while len(times) < n and attempts < max_attempts:
        minute = random.randint(0, 59)
        hour = random.randint(2, 22)
        total_min = hour * 60 + minute
        if all(abs(total_min - (h * 60 + m)) >= 15 for h, m in times):
            times.append((minute, hour))
        attempts += 1
    if len(times) < n:
        raise ValueError(f"Could not generate {n} unique times within constraints")
    return times

def update_cron_times(file_path, n):
    if n == 1:
        times = [generate_random_time()]
    else:
        times = generate_times(n)

    with open(file_path, 'r') as f:
        lines = f.readlines()

    new_lines = []
    idx = 0
    for line in lines:
        if line.strip().startswith('- cron:'):
            minute, hour = times[idx]
            new_line = f"    - cron: '{minute} {hour} * * *'\n"
            new_lines.append(new_line)
            idx += 1
        else:
            new_lines.append(line)

    with open(file_path, 'w') as f:
        f.writelines(new_lines)

    print(f"Updated {file_path} with {n} cron times", flush=True)

if __name__ == "__main__":
    update_cron_times('.github/workflows/1a_search_post_to_comment.yml', 6)
    update_cron_times('.github/workflows/2a_generate_content.yml', 1)
    update_cron_times('.github/workflows/4a_send_connection.yml', 18)
    update_cron_times('.github/workflows/5a_withdraw_connection.yml', 18)