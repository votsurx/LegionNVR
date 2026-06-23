import subprocess
import os
import shutil
import threading
import time
from datetime import datetime
from models.database import get_db

RECORDINGS_DIR = "recordings"
SEGMENT_DURATION = 300  # 5    
MOTION_PRE_RECORD = 5   #   
MOTION_POST_RECORD = 10 #   

class Recorder:
    def __init__(self, camera):
        self.camera = camera
        self.cam_id = str(camera["id"])
        self.cam_name = camera["name"]
        self.rtsp_url = camera["rtsp_main"]
        self.continuous_process = None
        self.motion_process = None
        self.recording_motion = False
        
        #  
        self.cam_dir = os.path.join(RECORDINGS_DIR, f"camera_{self.cam_id}")
        os.makedirs(self.cam_dir, exist_ok=True)
        
        #  ffmpeg
        self.ffmpeg_path = "ffmpeg"
        if shutil.which(self.ffmpeg_path) is None:
            for p in ["C:/ffmpeg/bin/ffmpeg.exe", "C:/ffmpeg/ffmpeg.exe"]:
                if os.path.exists(p):
                    self.ffmpeg_path = p
                    break
    
    def start_continuous(self):
        """    5 """
        if self.continuous_process:
            return
        
        today = datetime.now().strftime("%Y-%m-%d")
        date_dir = os.path.join(self.cam_dir, today)
        os.makedirs(date_dir, exist_ok=True)
        
        output_template = os.path.join(date_dir, f"%H-%M-%S_continuous.mp4")
        
        cmd = [
            self.ffmpeg_path,
            "-loglevel", "error",
            "-rtsp_transport", "tcp",
            "-i", self.rtsp_url,
            "-c:v", "copy",
            "-c:a", "aac",
            "-f", "segment",
            "-segment_time", str(SEGMENT_DURATION),
            "-segment_format", "mp4",
            "-reset_timestamps", "1",
            "-strftime", "1",
            output_template
        ]
        
        self.continuous_process = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        print(f"   : {self.cam_name}")
        
        #      
        threading.Thread(target=self._cleanup_old, daemon=True).start()
    
    def start_motion(self):
        """   (5   + 10  )"""
        if self.recording_motion:
            return
        
        self.recording_motion = True
        
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        timestamp = now.strftime("%H-%M-%S")
        date_dir = os.path.join(self.cam_dir, today)
        os.makedirs(date_dir, exist_ok=True)
        
        output_file = os.path.join(date_dir, f"{timestamp}_motion.mp4")
        
        #   : 5   + 10   =  15  
        cmd = [
            self.ffmpeg_path,
            "-loglevel", "error",
            "-rtsp_transport", "tcp",
            "-i", self.rtsp_url,
            "-c:v", "copy",
            "-c:a", "aac",
            "-t", str(MOTION_PRE_RECORD + MOTION_POST_RECORD),
            "-y",
            output_file
        ]
        
        self.motion_process = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        
        #   
        try:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO recordings (camera_id, filename, start_time, type) VALUES (?, ?, ?, ?)",
                (self.camera["id"], output_file, now.strftime("%Y-%m-%d %H:%M:%S"), "motion")
            )
            conn.commit()
            conn.close()
        except:
            pass
        
        print(f"  : {self.cam_name}  {timestamp}_motion.mp4")
        
        #   
        def wait_motion():
            time.sleep(MOTION_PRE_RECORD + MOTION_POST_RECORD + 2)
            self.recording_motion = False
            self.motion_process = None
            print(f"  : {timestamp}_motion.mp4")  #    
        
        threading.Thread(target=wait_motion, daemon=True).start()
    
    def stop_motion(self):
        """   """
        if self.motion_process:
            self.motion_process.terminate()
            self.motion_process = None
            self.recording_motion = False
    
    def stop_all(self):
        """  """
        if self.continuous_process:
            self.continuous_process.terminate()
            self.continuous_process = None
        self.stop_motion()
    
    def _cleanup_old(self):
        """   N """
        retention = self.camera.get("record_retention_days", 7)
        while True:
            try:
                cutoff = time.time() - (retention * 86400)
                for root, dirs, files in os.walk(self.cam_dir):
                    for f in files:
                        fpath = os.path.join(root, f)
                        if os.path.getmtime(fpath) < cutoff:
                            os.remove(fpath)
                            print(f"   : {f}")
            except:
                pass
            time.sleep(3600)  #    