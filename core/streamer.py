from flask import Flask, send_from_directory
import subprocess
import os
import shutil
import threading

class StreamServer:
    def __init__(self, cameras, config):
        self.cameras = cameras
        self.config = config
        self.app = Flask(__name__)
        self.hls_dir = config.get("hls_dir", "streams")
        self.streams = {}
        
        os.makedirs(self.hls_dir, exist_ok=True)
        self._setup_routes()

    def _setup_routes(self):
        @self.app.route('/camera/<id>/stream.m3u8')
        def stream(id):
            return send_from_directory(self.hls_dir, f"camera{id}.m3u8")

        @self.app.route('/camera/<id>/<segment>')
        def segment(id, segment):
            return send_from_directory(self.hls_dir, segment)

    def start_stream(self, camera):
        cam_id = camera["id"]
        
        ffmpeg_path = "ffmpeg"
        if shutil.which(ffmpeg_path) is None:
            possible_paths = [
                "C:/ffmpeg/bin/ffmpeg.exe",
                "C:/ffmpeg/ffmpeg.exe",
            ]
            for p in possible_paths:
                if os.path.exists(p):
                    ffmpeg_path = p
                    break
        
        cmd = [
            ffmpeg_path,
            "-rtsp_transport", "tcp",
            "-i", camera["rtsp_main"],
            "-c", "copy",
            "-hls_time", "2",
            "-hls_list_size", "5",
            "-hls_flags", "delete_segments",
            f"{self.hls_dir}/camera{cam_id}.m3u8"
        ]
        
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.streams[cam_id] = proc
            print(f"  {camera['name']} ")
        except FileNotFoundError:
            print(f" ffmpeg  !")

    def start_all(self):
        for cam in self.cameras:
            if cam.get("enabled", True):
                self.start_stream(cam)
        print(f" -   {self.config.get('port', 8081)}")
        self.app.run(host='0.0.0.0', port=self.config.get('port', 8081), debug=False)

    def stop_all(self):
        for proc in self.streams.values():
            proc.terminate()