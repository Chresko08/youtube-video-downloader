from flask import Flask, render_template, request, jsonify, send_from_directory, make_response
import yt_dlp
import os
import time
from urllib.parse import quote

app = Flask(__name__)

DOWNLOADS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'downloads')
if not os.path.exists(DOWNLOADS_DIR):
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)

@app.route('/')
def index():
    response = make_response(render_template('index.html'))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.errorhandler(Exception)
def handle_exception(e):
    return jsonify({"success": False, "error": f"Server Error: {str(e)}"}), 500

def cleanup_downloads():
    now = time.time()
    try:
        if os.path.exists(DOWNLOADS_DIR):
            for filename in os.listdir(DOWNLOADS_DIR):
                file_path = os.path.join(DOWNLOADS_DIR, filename)
                if os.path.isfile(file_path):
                    # Delete files older than 1 hour
                    if os.stat(file_path).st_mtime < now - 3600:
                        os.remove(file_path)
                        print(f"Deleted old file: {file_path}")
    except Exception as e:
        print(f"Cleanup error: {e}")

@app.route('/download', methods=['POST'])
def download_video():
    cleanup_downloads()
    url = request.form.get('url')
    if not url:
        return jsonify({"success": False, "error": "Missing URL parameter."})

    format_id = request.form.get('format_id')
    outtmpl = os.path.join(DOWNLOADS_DIR, '%(title)s.%(ext)s')

    if format_id:
        ydl_opts = {
            'format': f'{format_id}+bestaudio/{format_id}',
            'outtmpl': outtmpl,
            'postprocessors': [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4',
            }],
        }
    else:
        ydl_opts = {
            'format': 'bestvideo+bestaudio/best',
            'outtmpl': outtmpl,
            'postprocessors': [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4',
            }],
        }

    ydl_opts.update({
        'noplaylist': True,
        'socket_timeout': 30,
        'nocheckcertificate': True,
        'ignoreerrors': False,
        'no_warnings': False,
        'extractor_args': {
            'youtube': {
                'player_client': ['visionos', 'android']
            }
        },
        'remote_components': ['ejs:github'],
    })

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=True)
            
            # Find the actual downloaded / converted file
            filename = None
            requested = info_dict.get('requested_downloads')
            if requested:
                for req in requested:
                    fp = req.get('filepath')
                    if fp and os.path.exists(fp):
                        filename = os.path.basename(fp)
                        break

            if not filename:
                video_title = ydl.prepare_filename(info_dict)
                candidate = os.path.basename(video_title)
                base, _ = os.path.splitext(candidate)
                for ext in ['.mp4', '.mkv', '.webm']:
                    candidate_path = os.path.join(DOWNLOADS_DIR, base + ext)
                    if os.path.exists(candidate_path):
                        filename = base + ext
                        break
                if not filename and os.path.exists(os.path.join(DOWNLOADS_DIR, candidate)):
                    filename = candidate

            if not filename:
                return jsonify({"success": False, "error": "File was downloaded but could not be located on server."})

            encoded_filename = quote(filename)
            return jsonify({"success": True, "filename": filename, "download_url": f"/files/{encoded_filename}"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/get-formats', methods=['POST'])
def get_formats():
    url = request.form.get('url')
    if not url:
        return jsonify({"success": False, "error": "Missing URL parameter."})

    ydl_opts = {
        'quiet': True,
        'noplaylist': True,
        'socket_timeout': 30,
        'nocheckcertificate': True,
        'extractor_args': {
            'youtube': {
                'player_client': ['visionos', 'android']
            }
        },
        'remote_components': ['ejs:github'],
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            formats = []
            seen_resolutions = set()
            
            for f in info.get('formats', []):
                if f.get('vcodec') != 'none' and f.get('height'):
                    resolution = f'{f.get("height")}p'
                    filesize = f.get('filesize') or f.get('filesize_approx') or 0
                    
                    fmt = {
                        'format_id': f['format_id'],
                        'ext': f.get('ext', 'mp4'),
                        'resolution': resolution,
                        'filesize': filesize,
                        'note': f.get('format_note')
                    }
                    
                    if resolution not in seen_resolutions:
                        seen_resolutions.add(resolution)
                        formats.append(fmt)
                    else:
                        for i, existing_fmt in enumerate(formats):
                            if existing_fmt['resolution'] == resolution:
                                if (fmt['ext'] == 'mp4' and existing_fmt['ext'] != 'mp4') or (fmt['filesize'] > existing_fmt['filesize']):
                                    formats[i] = fmt
                                break
            
            # Sort by resolution descending (e.g. 2160p, 1080p, 720p...)
            formats.sort(key=lambda x: int(x['resolution'].replace('p', '')) if x['resolution'][:-1].isdigit() else 0, reverse=True)
            
            return jsonify({"success": True, "formats": formats, "title": info.get('title')})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/files/<path:filename>')
def serve_file(filename):
    return send_from_directory(DOWNLOADS_DIR, filename, as_attachment=True, download_name=filename)

if __name__ == '__main__':
    app.run(debug=True)
