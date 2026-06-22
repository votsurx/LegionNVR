import subprocess
from flask import Flask, Response

app = Flask(__name__)

@app.route('/mjpeg')
def mjpeg():
    def generate():
        cmd = [
            "ffmpeg",
            "-loglevel", "error",
            "-rtsp_transport", "tcp",
            "-i", "rtsp://admin:admin@192.168.0.11:554/11",
            "-vf", "fps=5,scale=640:360",
            "-f", "mjpeg",
            "-q:v", "5",
            "pipe:1"
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            while True:
                frame = proc.stdout.read(102400)
                if not frame:
                    break
                yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        except GeneratorExit:
            pass
        finally:
            proc.terminate()
    
    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == '__main__':
    print("Test MJPEG: http://localhost:9999/mjpeg")
    app.run(port=9999)