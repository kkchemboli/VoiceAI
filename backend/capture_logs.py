import subprocess
import time
import os

log_path = os.path.join(os.getcwd(), 'agent_output.log')
print(f"Logging to {log_path}")

with open(log_path, 'w') as f:
    process = subprocess.Popen(['venv\\Scripts\\python.exe', 'agent.py', 'dev'], stdout=f, stderr=f)
    print(f"Started agent with PID {process.pid}")
    time.sleep(10)
    process.terminate()
    print("Terminated agent")
