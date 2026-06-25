"""
LegionNVR - Unified Launcher
  : Web Server, Detector, Streamer
        
"""

# -*- coding: utf-8 -*-
import sys
import io
import os

#  
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

print("    UTF-8")
#   UTF-8  
if sys.stdout.encoding != 'UTF-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import subprocess
import time
import signal
import threading
import json
from datetime import datetime
from pathlib import Path

# ============================================================
# 
# ============================================================

PROJECT_ROOT = Path(__file__).parent.absolute()
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

COMPONENTS = {
    "web_server": {
        "script": "web_server.py",
        "dir": PROJECT_ROOT,
        "log_file": LOG_DIR / "web_server.log",
        "color": "\033[94m",  # 
        "icon": ""
    },
    "detector": {
        "script": "engine/detector.py",
        "dir": PROJECT_ROOT,
        "log_file": LOG_DIR / "detector.log",
        "color": "\033[92m",  # 
        "icon": ""
    },
    "streamer": {
        "script": "engine/streamer.py",
        "dir": PROJECT_ROOT,
        "log_file": LOG_DIR / "streamer.log",
        "color": "\033[93m",  # 
        "icon": ""
    }
}

# ============================================================
#   
# ============================================================

class Colors:
    RESET = "\033[0m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    PURPLE = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

# ============================================================
#   
# ============================================================

class ProcessManager:
    def __init__(self):
        self.processes = {}
        self.running = True
        self.log_threads = []

        #  
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        #    
        for name, comp in COMPONENTS.items():
            comp["log_file"].parent.mkdir(parents=True, exist_ok=True)

    def _signal_handler(self, sig, frame):
        """ Ctrl+C"""
        print(f"\n{Colors.YELLOW}   ...{Colors.RESET}")
        self.stop_all()
        sys.exit(0)

    def _print_header(self):
        """  """
        os.system('cls' if os.name == 'nt' else 'clear')
        print(f"""
{Colors.CYAN}{'='*60}{Colors.RESET}
{Colors.BOLD}{Colors.PURPLE}     LEGION NVR  {Colors.RESET}
{Colors.CYAN}{'='*60}{Colors.RESET}
{Colors.DIM}    {PROJECT_ROOT}{Colors.RESET}
{Colors.DIM}    {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{Colors.RESET}
{Colors.CYAN}{'='*60}{Colors.RESET}
        """)

    def _print_component_status(self):
        """   """
        status = []
        for name, comp in COMPONENTS.items():
            is_running = name in self.processes and self.processes[name].poll() is None
            icon = comp["icon"]
            color = Colors.GREEN if is_running else Colors.RED
            status_text = " RUNNING" if is_running else " STOPPED"
            status.append(f"{icon} {name:<12} {color}{status_text}{Colors.RESET}")

        print(f"\n{Colors.CYAN}{''*60}{Colors.RESET}")
        print("\n".join(status))
        print(f"{Colors.CYAN}{''*60}{Colors.RESET}\n")

    def start_component(self, name):
        """  """
        if name not in COMPONENTS:
            print(f"{Colors.RED}  '{name}'  {Colors.RESET}")
            return False

        comp = COMPONENTS[name]
        script_path = comp["dir"] / comp["script"]

        if not script_path.exists():
            print(f"{Colors.RED}   : {script_path}{Colors.RESET}")
            return False

        if name in self.processes and self.processes[name].poll() is None:
            print(f"{Colors.YELLOW} {name}  {Colors.RESET}")
            return True

        try:
            #    cmd   
            cmd = f'python -u "{script_path}"'

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(comp["dir"]),
                shell=True,
                text=True,
                bufsize=0,  #   
                encoding='utf-8',
                errors='replace'
                creationflags=subprocess.CREATE_NO_WINDOW  # ← ДЛЯ WINDOWS!
            )

            self.processes[name] = process
            print(f"{comp['color']}{comp['icon']} {name}  (PID: {process.pid}){Colors.RESET}", flush=True)

            #     
            thread = threading.Thread(
                target=self._log_reader,
                args=(name, process, comp),
                daemon=True
            )
            thread.start()
            self.log_threads.append(thread)

            return True

        except Exception as e:
            print(f"{Colors.RED}   {name}: {e}{Colors.RESET}", flush=True)
            return False

    def _log_reader(self, name, process, comp):
        """       """
        color = comp["color"]
        icon = comp["icon"]
        prefix = f"{color}{icon} [{name}]{Colors.RESET}"

        try:
            for line in iter(process.stdout.readline, ''):
                if not self.running:
                    break
                if line.strip():
                    #
                    timestamp = datetime.now().strftime("%H:%M:%S")
                    print(f"{Colors.DIM}{timestamp}{Colors.RESET} {prefix} {line.rstrip()}")

                    #   -
                    try:
                        with open(comp["log_file"], 'a', encoding='utf-8') as f:
                            f.write(f"{timestamp} {line}")
                    except:
                        pass
        except Exception as e:
            print(f"{Colors.RED}    {name}: {e}{Colors.RESET}")

    def stop_component(self, name):
        """  """
        if name not in self.processes:
            print(f"{Colors.YELLOW} {name}  {Colors.RESET}")
            return

        process = self.processes[name]
        if process.poll() is None:
            print(f"{Colors.YELLOW}  {name} (PID: {process.pid})...{Colors.RESET}")
            try:
                process.terminate()
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            except Exception as e:
                print(f"{Colors.RED}   {name}: {e}{Colors.RESET}")

        del self.processes[name]
        print(f"{Colors.GREEN} {name} {Colors.RESET}")

    def start_all(self):
        """  """
        self._print_header()
        print(f"{Colors.BOLD}{Colors.CYAN}   ...{Colors.RESET}\n")

        #   : web_server  detector  streamer
        for name in ["web_server", "detector", "streamer"]:
            self.start_component(name)
            time.sleep(1)  #   

        self._print_component_status()
        print(f"{Colors.BOLD}{Colors.GREEN}   !{Colors.RESET}")
        print(f"{Colors.DIM} Ctrl+C    {Colors.RESET}")

    def stop_all(self):
        """  """
        print(f"\n{Colors.YELLOW}   ...{Colors.RESET}")
        for name in list(self.processes.keys()):
            self.stop_component(name)
        self.running = False
        print(f"{Colors.GREEN}   {Colors.RESET}")

    def restart_component(self, name):
        """  """
        self.stop_component(name)
        time.sleep(1)
        self.start_component(name)

    def restart_all(self):
        """  """
        self.stop_all()
        time.sleep(2)
        self.start_all()

    def status(self):
        """   """
        self._print_component_status()
        for name, process in self.processes.items():
            if process.poll() is None:
                print(f"  {COMPONENTS[name]['icon']} {name}: RUNNING (PID: {process.pid})")
            else:
                print(f"  {COMPONENTS[name]['icon']} {name}: STOPPED")

    def tail_logs(self, name=None, lines=50):
        """  N  """
        if name:
            comp = COMPONENTS.get(name)
            if comp and comp["log_file"].exists():
                print(f"\n{Colors.CYAN} : {name}{Colors.RESET}")
                print(f"{Colors.CYAN}{''*60}{Colors.RESET}")
                with open(comp["log_file"], 'r', encoding='utf-8') as f:
                    log_lines = f.readlines()
                    for line in log_lines[-lines:]:
                        print(line.rstrip())
                return

        #   
        for n, comp in COMPONENTS.items():
            if comp["log_file"].exists():
                print(f"\n{Colors.CYAN} : {n}{Colors.RESET}")
                print(f"{Colors.CYAN}{''*60}{Colors.RESET}")
                with open(comp["log_file"], 'r', encoding='utf-8') as f:
                    log_lines = f.readlines()
                    for line in log_lines[-lines:]:
                        print(line.rstrip())

# ============================================================
#  
# ============================================================

def main():
    """ """
    manager = ProcessManager()

    #    
    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()

        if cmd == "start":
            manager.start_all()
            #   
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                manager.stop_all()

        elif cmd == "stop":
            manager.stop_all()

        elif cmd == "restart":
            manager.restart_all()

        elif cmd == "status":
            manager.status()

        elif cmd == "logs":
            if len(sys.argv) > 2:
                manager.tail_logs(sys.argv[2])
            else:
                manager.tail_logs()

        elif cmd == "restart-detector":
            manager.restart_component("detector")

        elif cmd == "restart-streamer":
            manager.restart_component("streamer")

        elif cmd == "restart-web":
            manager.restart_component("web_server")

        else:
            print(f"""
{Colors.YELLOW}:{Colors.RESET}
  python run_legion.py start              -   
  python run_legion.py stop               -   
  python run_legion.py restart            -   
  python run_legion.py status             -  
  python run_legion.py logs []   -  
  python run_legion.py restart-detector   -  
  python run_legion.py restart-streamer   -  
  python run_legion.py restart-web        -  -
            """)
    else:
        #  
        manager.start_all()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            manager.stop_all()

if __name__ == "__main__":
    main()