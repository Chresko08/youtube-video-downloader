from flask import Flask, render_template, request, jsonify, send_from_directory, after_this_request
import yt_dlp
import os
import subprocess
from urllib.parse import quote

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

import time

def cleanup_downloads():
    now = time.time()
    try:
        for filename in os.listdir('downloads'):
            file_path = os.path.join('downloads', filename)
            if os.path.isfile(file_path):
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

    if format_id:
        ydl_opts = {
            'format': f'{format_id}+bestaudio/{format_id}', # Download selected video + best audio, or just the video if it has audio
            'outtmpl': 'downloads/%(title)s.%(ext)s',
            'postprocessors': [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4',
            }],
        }
    else:
        # Fallback or legacy behavior (default to best)
        ydl_opts = {
            'format': 'bestvideo+bestaudio/best',
            'outtmpl': 'downloads/%(title)s.%(ext)s',
            'postprocessors': [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4',
            }],
        }

    # Add comprehensive options to bypass bot detection and cookie requirements
    ydl_opts.update({
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios', 'web'],
                'player_skip': ['webpage', 'configs'],
                'skip': ['hls', 'dash'],
            }
        },
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-us,en;q=0.5',
            'Sec-Fetch-Mode': 'navigate',
        },
        'nocheckcertificate': True,
        'ignoreerrors': False,
        'no_warnings': False,
        'source_address': '0.0.0.0',
    })

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=True)
            video_title = ydl.prepare_filename(info_dict)
            filename = os.path.basename(video_title)
            
            # Check if the file exists, if not, check for mp4 (conversion)
            if not os.path.exists(os.path.join('downloads', filename)):
                base, _ = os.path.splitext(filename)
                mp4_filename = base + '.mp4'
                if os.path.exists(os.path.join('downloads', mp4_filename)):
                    filename = mp4_filename

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
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios', 'web'],
                'player_skip': ['webpage', 'configs'],
            }
        },
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        },
        'nocheckcertificate': True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            formats = []
            seen_resolutions = set()
            
            # Filter and process formats
            for f in info.get('formats', []):
                if f.get('vcodec') != 'none' and f.get('height'):
                    resolution = f'{f.get("height")}p'
                    
                    fmt = {
                        'format_id': f['format_id'],
                        'ext': f['ext'],
                        'resolution': resolution,
                        'filesize': f.get('filesize') or 0,
                        'note': f.get('format_note')
                    }
                    
                    # If we haven't seen this resolution, or if this format is larger (better quality)
                    if resolution not in seen_resolutions:
                        seen_resolutions.add(resolution)
                        formats.append(fmt)
                    else:
                        # Find existing format and replace if this one is better
                        for i, existing_fmt in enumerate(formats):
                            if existing_fmt['resolution'] == resolution:
                                if fmt['filesize'] > existing_fmt['filesize']:
                                    formats[i] = fmt
                                break
            
            # Sort by height (quality) descending
            formats.sort(key=lambda x: int(x['resolution'].replace('p', '')) if x['resolution'][:-1].isdigit() else 0, reverse=True)
            
            return jsonify({"success": True, "formats": formats, "title": info.get('title')})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/files/<path:filename>')
def serve_file(filename):
    return send_from_directory('downloads', filename, as_attachment=True, download_name=filename)

# Ensure downloads directory exists
if not os.path.exists('downloads'):
    os.makedirs('downloads')

if __name__ == '__main__':
    app.run(debug=True)
