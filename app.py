from flask import Flask, render_template, request, jsonify, send_from_directory, after_this_request
import yt_dlp
import os
import subprocess
from urllib.parse import quote

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/download', methods=['POST'])
def download_video():
    url = request.form.get('url')
    if not url:
        return jsonify({"success": False, "error": "Missing URL parameter."})

    format_id = request.form.get('format_id')

    if format_id:
        ydl_opts = {
            'format': f'{format_id}+bestaudio/best', # Download selected video + best audio
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

    # Add common options to bypass bot detection
    ydl_opts.update({
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'web'],
                'player_skip': ['webpage', 'configs', 'js'],
                'zerorating': ['1'],
            }
        },
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        }
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

    ydl_opts = {'quiet': True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            formats = []
            seen_resolutions = set()
            
            # Filter and process formats
            for f in info.get('formats', []):
                # We want video formats. 
                # Note: yt-dlp separates video and audio often. 
                # We want to show distinct video qualities (e.g. 1080p, 720p).
                if f.get('vcodec') != 'none' and f.get('height'):
                    resolution = f'{f.get("height")}p'
                    # Avoid duplicates for the same resolution if possible, or show them?
                    # Let's show unique resolutions to keep it simple for the user, 
                    # picking the best bitrate for that resolution if multiple exist?
                    # Or just list them all. Listing all might be too much.
                    # Let's list unique resolutions + ext.
                    
                    # Simple approach: Just pass relevant data and let frontend render.
                    # But let's deduplicate by resolution for simplicity as requested "available options of video qualities"
                    
                    # Actually, let's just send them all but formatted nicely.
                    # Wait, user wants "video qualities".
                    
                    fmt = {
                        'format_id': f['format_id'],
                        'ext': f['ext'],
                        'resolution': resolution,
                        'filesize': f.get('filesize'),
                        'note': f.get('format_note')
                    }
                    formats.append(fmt)
            
            # Sort by height (quality) descending
            formats.sort(key=lambda x: int(x['resolution'].replace('p', '')) if x['resolution'][:-1].isdigit() else 0, reverse=True)
            
            return jsonify({"success": True, "formats": formats, "title": info.get('title')})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/files/<path:filename>')
def serve_file(filename):
    file_path = os.path.join('downloads', filename)
    
    @after_this_request
    def delete_file(response):
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                print(f"Deleted file: {file_path}")
        except Exception as e:
            print(f"Error deleting file {file_path}: {e}")
        return response
    
    return send_from_directory('downloads', filename, as_attachment=True, download_name=filename)

# Ensure downloads directory exists
if not os.path.exists('downloads'):
    os.makedirs('downloads')

if __name__ == '__main__':
    app.run(debug=True)
