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
        self.hls_dir = config["hls_dir"]
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

        @self.app.route('/')
        def index():
            links = ""
            for cam in self.cameras:
                links += f'<p>{cam["name"]}: <a href="/camera/{cam["id"]}/stream.m3u8">HLS</a></p>'
            return f"<h1>🛡️ Spartan NVR</h1>{links}"

    def start_stream(self, camera):
        cam_id = camera["id"]
        
        # Автоматически ищем ffmpeg
        ffmpeg_path = "ffmpeg"
        if shutil.which(ffmpeg_path) is None:
            possible_paths = [
                "C:/ffmpeg/bin/ffmpeg.exe",
                "C:/ffmpeg/ffmpeg.exe",
                "ffmpeg.exe"
            ]
            for p in possible_paths:
                if shutil.which(p) or os.path.exists(p):
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
            print(f"🎥 Стрим {camera['name']} запущен (ffmpeg: {ffmpeg_path})")
        except FileNotFoundError:
            print(f"❌ ffmpeg не найден! Путь: {ffmpeg_path}")
            print(f"   Проверь командой: ffmpeg -version")

    def start_all(self):
        for cam in self.cameras:
            self.start_stream(cam)
        print(f"🌐 Веб-сервер на порту {self.config['port']}")
        self.app.run(host='0.0.0.0', port=self.config['port'], debug=False)

    def stop_all(self):
        for proc in self.streams.values():
            proc.terminate()