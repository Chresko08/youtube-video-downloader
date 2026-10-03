from flask import Flask, render_template, request, jsonify, send_from_directory, make_response
import yt_dlp
import os
import time
import re
import urllib.request
import json
import subprocess
from urllib.parse import quote

app = Flask(__name__)

DOWNLOADS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'downloads')
if not os.path.exists(DOWNLOADS_DIR):
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)

INVIDIOUS_INSTANCES = [
    "https://invidious.f5.si",
    "https://invidious.nerdvpn.de",
    "https://inv.nadeko.net",
    "https://invidious.tiekoetter.com"
]

DEFAULT_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'

def get_ffmpeg():
    import shutil
    sys_ffmpeg = shutil.which('ffmpeg')
    if sys_ffmpeg:
        return sys_ffmpeg
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return 'ffmpeg'

def extract_video_id(url):
    patterns = [
        r'(?:v=|\/)([0-9A-Za-z_-]{11}).*',
        r'(?:embed\/)([0-9A-Za-z_-]{11})',
        r'(?:watch\?v=)([0-9A-Za-z_-]{11})',
        r'youtu\.be\/([0-9A-Za-z_-]{11})',
        r'shorts\/([0-9A-Za-z_-]{11})'
    ]
    for pattern in patterns:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None

def fetch_invidious_data(video_id):
    for inst in INVIDIOUS_INSTANCES:
        try:
            req = urllib.request.Request(
                f"{inst}/api/v1/videos/{video_id}",
                headers={'User-Agent': DEFAULT_UA}
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                if 'adaptiveFormats' in data or 'formatStreams' in data:
                    return data
        except Exception:
            continue
    return None

@app.route('/')
def index():
    response = make_response(render_template('index.html'))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.route('/api/diag')
def diag():
    clients = []
    try:
        clients = list(yt_dlp.extractor.youtube.YoutubeIE._get_requested_clients.__globals__['INNERTUBE_CLIENTS'].keys())
    except Exception:
        pass

    test_res = {}
    test_url = request.args.get('url')
    if test_url:
        import traceback
        try:
            ydl_opts = {
                'quiet': True,
                'noplaylist': True,
                'socket_timeout': 30,
                'nocheckcertificate': True,
                'extractor_args': {
                    'youtube': {
                        'player_client': ['visionos', 'mweb']
                    }
                },
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(test_url, download=False)
                fmts = [f.get('format_id') for f in info.get('formats', [])]
                test_res = {"success": True, "title": info.get('title'), "formats": fmts}
        except Exception as e:
            test_res = {"success": False, "error": str(e), "traceback": traceback.format_exc()}

    return jsonify({
        "status": "ok",
        "ytdlp_version": yt_dlp.version.__version__,
        "ffmpeg": get_ffmpeg(),
        "clients": clients,
        "test": test_res
    })

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
                    if os.stat(file_path).st_mtime < now - 3600:
                        os.remove(file_path)
    except Exception as e:
        print(f"Cleanup error: {e}")

@app.route('/download', methods=['POST'])
def download_video():
    cleanup_downloads()
    url = request.form.get('url')
    if not url:
        return jsonify({"success": False, "error": "Missing URL parameter."})

    format_id = request.form.get('format_id')
    ffmpeg_exe = get_ffmpeg()
    last_error = ""

    # Engine 1: pytubefix (generates PO token automatically using Node.js)
    try:
        from pytubefix import YouTube
        yt = YouTube(url, client='WEB')
        title = yt.title or f"video_{int(time.time())}"
        safe_title = re.sub(r'[\\/*?:\'\"<>|]', '', title)[:100].strip() or f"video_{int(time.time())}"
        timestamp = int(time.time())
        out_filename = f"{safe_title}_{timestamp}.mp4"
        out_file = os.path.join(DOWNLOADS_DIR, out_filename)

        stream = None
        if format_id:
            try:
                stream = yt.streams.get_by_itag(int(format_id))
            except Exception:
                pass
            if not stream:
                itag_map = {
                    '401': '2160p', '400': '1440p', '399': '1080p', '398': '720p',
                    '397': '480p', '396': '360p', '395': '240p', '394': '144p',
                    '628': '2160p', '623': '1440p', '312': '1080p', '311': '720p',
                    '231': '480p', '230': '360p', '229': '240p', '269': '144p'
                }
                target_res = itag_map.get(str(format_id))
                if target_res:
                    stream = yt.streams.filter(res=target_res, mime_type='video/mp4').first() or yt.streams.filter(res=target_res).first()

        if not stream:
            stream = yt.streams.get_highest_resolution() or yt.streams.first()

        if stream:
            if stream.is_progressive:
                stream.download(output_path=DOWNLOADS_DIR, filename=out_filename)
                if os.path.exists(out_file) and os.path.getsize(out_file) > 0:
                    return jsonify({"success": True, "filename": out_filename, "download_url": f"/files/{quote(out_filename)}"})
            else:
                temp_v = os.path.join(DOWNLOADS_DIR, f"temp_v_{timestamp}.{stream.subtype or 'mp4'}")
                stream.download(output_path=DOWNLOADS_DIR, filename=os.path.basename(temp_v))

                a_stream = yt.streams.get_audio_only()
                if a_stream:
                    temp_a = os.path.join(DOWNLOADS_DIR, f"temp_a_{timestamp}.{a_stream.subtype or 'm4a'}")
                    a_stream.download(output_path=DOWNLOADS_DIR, filename=os.path.basename(temp_a))

                    cmd = [ffmpeg_exe, '-y', '-i', temp_v, '-i', temp_a, '-c:v', 'copy', '-c:a', 'aac', out_file]
                    p = subprocess.run(cmd, capture_output=True, timeout=180)
                    if os.path.exists(temp_a): os.remove(temp_a)
                    if os.path.exists(temp_v): os.remove(temp_v)
                    if p.returncode == 0 and os.path.exists(out_file) and os.path.getsize(out_file) > 0:
                        return jsonify({"success": True, "filename": out_filename, "download_url": f"/files/{quote(out_filename)}"})
                else:
                    os.rename(temp_v, out_file)
                    return jsonify({"success": True, "filename": out_filename, "download_url": f"/files/{quote(out_filename)}"})
    except Exception as pt_err:
        last_error = f"pytubefix: {pt_err}"
        print(f"pytubefix download failed: {pt_err}. Trying yt-dlp...")

    # Engine 2: yt-dlp
    outtmpl = os.path.join(DOWNLOADS_DIR, '%(title)s.%(ext)s')
    if format_id:
        format_selector = f'{format_id}+234/{format_id}+233/{format_id}+bestaudio/{format_id}'
    else:
        format_selector = 'bestvideo+234/bestvideo+233/bestvideo+bestaudio/best'

    ydl_opts = {
        'format': format_selector,
        'outtmpl': outtmpl,
        'merge_output_format': 'mp4',
        'ffmpeg_location': ffmpeg_exe,
        'noplaylist': True,
        'socket_timeout': 45,
        'nocheckcertificate': True,
        'ignoreerrors': False,
        'no_warnings': False,
        'js_runtimes': {'node': {}, 'deno': {}},
        'extractor_args': {
            'youtube': {
                'player_client': ['visionos', 'mweb']
            }
        },
        'remote_components': ['ejs:github'],
    }

    last_error = ""
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=True)
            
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

            if filename:
                encoded_filename = quote(filename)
                return jsonify({"success": True, "filename": filename, "download_url": f"/files/{encoded_filename}"})
    except Exception as ydl_err:
        last_error = str(ydl_err)
        print(f"yt-dlp download failed: {ydl_err}. Attempting fallback...")

    # Fallback via direct streams
    vid_id = extract_video_id(url)
    if vid_id:
        inv_data = fetch_invidious_data(vid_id)
        if inv_data:
            title = inv_data.get('title', f"video_{vid_id}")
            safe_title = re.sub(r'[\\/*?:"<>|]', "", title)[:100].strip() or f"video_{vid_id}"
            out_file = os.path.join(DOWNLOADS_DIR, f"{safe_title}.mp4")

            adaptive = inv_data.get('adaptiveFormats', [])
            v_fmt = None
            if format_id:
                v_fmt = next((f for f in adaptive if str(f.get('itag')) == str(format_id) and f.get('url')), None)
            if not v_fmt:
                v_fmt = next((f for f in adaptive if 'video' in f.get('type', '') and f.get('url')), None)

            a_fmt = next((f for f in adaptive if 'audio' in f.get('type', '') and f.get('url')), None)

            if v_fmt and v_fmt.get('url'):
                try:
                    if a_fmt and a_fmt.get('url'):
                        cmd = [
                            ffmpeg_exe, '-y',
                            '-user_agent', DEFAULT_UA,
                            '-i', v_fmt['url'],
                            '-user_agent', DEFAULT_UA,
                            '-i', a_fmt['url'],
                            '-c:v', 'copy',
                            '-c:a', 'aac',
                            out_file
                        ]
                    else:
                        cmd = [
                            ffmpeg_exe, '-y',
                            '-user_agent', DEFAULT_UA,
                            '-i', v_fmt['url'],
                            '-c', 'copy',
                            out_file
                        ]
                    p = subprocess.run(cmd, capture_output=True, timeout=180)
                    if p.returncode == 0 and os.path.exists(out_file) and os.path.getsize(out_file) > 0:
                        filename = f"{safe_title}.mp4"
                        return jsonify({"success": True, "filename": filename, "download_url": f"/files/{quote(filename)}"})
                    else:
                        err_out = p.stderr.decode('utf-8', errors='ignore')[-300:] if p.stderr else "Unknown ffmpeg error"
                        last_error += f" | Fallback FFmpeg returned code {p.returncode}: {err_out}"
                except Exception as fb_err:
                    last_error += f" | Fallback exception: {str(fb_err)}"

    return jsonify({"success": False, "error": f"Download failed: {last_error}"})

@app.route('/get-formats', methods=['POST'])
def get_formats():
    url = request.form.get('url')
    if not url:
        return jsonify({"success": False, "error": "Missing URL parameter."})

    # Engine 1: pytubefix
    try:
        from pytubefix import YouTube
        yt = YouTube(url, client='WEB')
        formats = []
        seen_resolutions = set()

        for s in yt.streams.filter(progressive=True):
            if s.resolution and s.resolution not in seen_resolutions:
                seen_resolutions.add(s.resolution)
                formats.append({
                    'format_id': str(s.itag),
                    'ext': s.subtype or 'mp4',
                    'resolution': s.resolution,
                    'filesize': s.filesize or 0,
                    'note': 'Progressive'
                })

        for s in yt.streams.filter(only_video=True):
            if s.resolution and s.resolution not in seen_resolutions:
                seen_resolutions.add(s.resolution)
                formats.append({
                    'format_id': str(s.itag),
                    'ext': s.subtype or 'mp4',
                    'resolution': s.resolution,
                    'filesize': s.filesize or 0,
                    'note': f"{s.fps}fps {s.video_codec}"
                })

        formats.sort(key=lambda x: int(x['resolution'].replace('p', '')) if x['resolution'][:-1].isdigit() else 0, reverse=True)
        if formats:
            return jsonify({"success": True, "formats": formats, "title": yt.title})
    except Exception as pt_err:
        print(f"pytubefix get_formats failed: {pt_err}. Trying yt-dlp...")

    # Engine 2: yt-dlp
    ffmpeg_exe = get_ffmpeg()
    ydl_opts = {
        'quiet': True,
        'noplaylist': True,
        'socket_timeout': 30,
        'nocheckcertificate': True,
        'ffmpeg_location': ffmpeg_exe,
        'js_runtimes': {'node': {}, 'deno': {}},
        'extractor_args': {
            'youtube': {
                'player_client': ['visionos', 'mweb']
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
                    if not filesize and f.get('tbr') and info.get('duration'):
                        filesize = int(info['duration'] * f['tbr'] * 128)
                    
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
            
            formats.sort(key=lambda x: int(x['resolution'].replace('p', '')) if x['resolution'][:-1].isdigit() else 0, reverse=True)
            if formats:
                return jsonify({"success": True, "formats": formats, "title": info.get('title')})
    except Exception as ydl_err:
        print(f"yt-dlp format extraction failed: {ydl_err}. Trying fallback...")

    # Fallback via Invidious API
    vid_id = extract_video_id(url)
    if vid_id:
        inv_data = fetch_invidious_data(vid_id)
        if inv_data:
            formats = []
            seen_res = set()
            for f in inv_data.get('adaptiveFormats', []):
                if 'video' in f.get('type', '') and f.get('resolution'):
                    res = f.get('resolution')
                    clen = int(f.get('clen', 0)) if f.get('clen', '').isdigit() else 0
                    container = f.get('container', 'mp4') or 'mp4'
                    
                    fmt = {
                        'format_id': str(f.get('itag', res)),
                        'ext': container,
                        'resolution': res,
                        'filesize': clen,
                        'note': f.get('encoding')
                    }
                    if res not in seen_res:
                        seen_res.add(res)
                        formats.append(fmt)
                    else:
                        for i, existing in enumerate(formats):
                            if existing['resolution'] == res and (fmt['ext'] == 'mp4' or fmt['filesize'] > existing['filesize']):
                                formats[i] = fmt
                                break
            formats.sort(key=lambda x: int(x['resolution'].replace('p', '')) if x['resolution'][:-1].isdigit() else 0, reverse=True)
            if formats:
                return jsonify({"success": True, "formats": formats, "title": inv_data.get('title')})

    return jsonify({"success": False, "error": "Unable to extract video formats. Please verify the URL or try again."})

@app.route('/files/<path:filename>')
def serve_file(filename):
    return send_from_directory(DOWNLOADS_DIR, filename, as_attachment=True, download_name=filename)

if __name__ == '__main__':
    app.run(debug=True)
