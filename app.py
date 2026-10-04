from flask import Flask, render_template, request, jsonify, send_from_directory, make_response
import yt_dlp
import os
import time
import re
import urllib.request
import urllib.parse
import http.cookiejar
import json
import hashlib
import subprocess
from urllib.parse import quote

app = Flask(__name__)

DOWNLOADS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'downloads')
if not os.path.exists(DOWNLOADS_DIR):
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)

DEFAULT_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'

INVIDIOUS_INSTANCES = [
    "https://invidious.f5.si",
    "https://invidious.nerdvpn.de",
    "https://inv.nadeko.net",
    "https://invidious.tiekoetter.com"
]

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

def build_format_selector(format_id):
    if not format_id:
        return 'bestvideo+bestaudio/best'
    fid = str(format_id).strip()
    if fid.isdigit():
        return f'{fid}+bestaudio/{fid}+140/{fid}+ba/{fid}'
    height_match = fid.rstrip('pP')
    if height_match.isdigit():
        h = int(height_match)
        return f'bestvideo[height<={h}]+bestaudio/best[height<={h}]/best'
    return f'{fid}+bestaudio/{fid}'

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

def fetch_invidious_data(video_id):
    for inst in INVIDIOUS_INSTANCES:
        try:
            req = urllib.request.Request(
                f"{inst}/api/v1/videos/{video_id}",
                headers={'User-Agent': DEFAULT_UA}
            )
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                if 'adaptiveFormats' in data or 'formatStreams' in data:
                    return data
        except Exception:
            continue
    return None

class InvidiousProxyDownloader:
    def __init__(self, instance="https://invidious.f5.si"):
        self.instance = instance.rstrip('/')
        self.host = urllib.parse.urlparse(instance).netloc
        self.cj = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cj))
        self.user_agent = DEFAULT_UA
        self.solved = False

    def to_proxy_url(self, stream_url):
        p = urllib.parse.urlparse(stream_url)
        return urllib.parse.urlunparse(('https', self.host, p.path, p.params, p.query, p.fragment))

    def ensure_authenticated(self):
        if self.solved:
            return
        try:
            req = urllib.request.Request(self.instance + '/', headers={'User-Agent': self.user_agent})
            html = self.opener.open(req, timeout=8).read().decode('utf-8', errors='ignore')
            m = re.search(r'<script id="anubis_challenge" type="application/json">(.*?)</script>', html, re.DOTALL)
            if not m:
                self.solved = True
                return
            chal = json.loads(m.group(1))
            c_info = chal['challenge']
            c_rules = chal['rules']
            diff = int(c_rules.get('difficulty', 4))
            target = '0' * diff
            nonce = 0
            while True:
                h = hashlib.sha256((c_info['randomData'] + str(nonce)).encode('utf-8')).hexdigest()
                if h.startswith(target):
                    break
                nonce += 1
            pass_url = f'{self.instance}/.within.website/x/cmd/anubis/api/pass-challenge?id={c_info["id"]}&response={h}&nonce={nonce}&redir=/&elapsedTime=150'
            self.opener.open(urllib.request.Request(pass_url, headers={'User-Agent': self.user_agent}), timeout=8)
            self.solved = True
        except Exception as e:
            print(f"Anubis solve notice: {e}")

    def download_stream(self, stream_url, target_path):
        self.ensure_authenticated()
        proxy_url = self.to_proxy_url(stream_url)
        req = urllib.request.Request(proxy_url, headers={'User-Agent': self.user_agent})
        with self.opener.open(req, timeout=40) as resp:
            content_type = resp.headers.get('Content-Type', '')
            if 'text/html' in content_type:
                self.solved = False
                self.ensure_authenticated()
                req2 = urllib.request.Request(proxy_url, headers={'User-Agent': self.user_agent})
                resp = self.opener.open(req2, timeout=40)

            with open(target_path, 'wb') as f:
                while chunk := resp.read(65536):
                    f.write(chunk)

        if not os.path.exists(target_path) or os.path.getsize(target_path) == 0:
            raise Exception("Downloaded stream file is empty.")

    def download_and_merge(self, inv_data, format_id, out_file, ffmpeg_exe):
        adaptive = inv_data.get('adaptiveFormats', [])
        v_fmt = None
        if format_id:
            v_fmt = next((f for f in adaptive if str(f.get('itag')) == str(format_id) and f.get('url')), None)
        if not v_fmt:
            v_fmt = next((f for f in adaptive if 'video' in f.get('type', '') and f.get('url')), None)

        a_fmt = next((f for f in adaptive if 'audio' in f.get('type', '') and f.get('url')), None)
        if not v_fmt or not v_fmt.get('url'):
            raise Exception("No video stream found in metadata")

        timestamp = int(time.time())
        out_dir = os.path.dirname(out_file)
        v_temp = os.path.join(out_dir, f'temp_v_{timestamp}.mp4')
        a_temp = os.path.join(out_dir, f'temp_a_{timestamp}.m4a')

        try:
            self.download_stream(v_fmt['url'], v_temp)
            if a_fmt and a_fmt.get('url'):
                self.download_stream(a_fmt['url'], a_temp)
                cmd = [ffmpeg_exe, '-y', '-i', v_temp, '-i', a_temp, '-c:v', 'copy', '-c:a', 'aac', out_file]
                p = subprocess.run(cmd, capture_output=True, timeout=120)
                if p.returncode != 0:
                    raise Exception(f"FFmpeg mux error {p.returncode}: {p.stderr.decode('utf-8', errors='ignore')[:200]}")
            else:
                os.rename(v_temp, out_file)
        finally:
            if os.path.exists(v_temp):
                try: os.remove(v_temp)
                except Exception: pass
            if os.path.exists(a_temp):
                try: os.remove(a_temp)
                except Exception: pass

        return os.path.exists(out_file) and os.path.getsize(out_file) > 0

def download_with_ytdlp(url, format_id, ffmpeg_exe):
    outtmpl = os.path.join(DOWNLOADS_DIR, '%(title)s_%(id)s.%(ext)s')
    format_selector = build_format_selector(format_id)

    ydl_opts = {
        'format': format_selector,
        'outtmpl': outtmpl,
        'merge_output_format': 'mp4',
        'ffmpeg_location': ffmpeg_exe,
        'noplaylist': True,
        'socket_timeout': 30,
        'nocheckcertificate': True,
        'ignoreerrors': False,
        'no_warnings': False,
        'js_runtimes': {'node': {}},
        'extractor_args': {
            'youtube': {
                'player_client': ['web_embedded', 'android', 'ios', 'tv_simply', 'mweb'],
                'player_skip': ['webpage']
            }
        },
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(url, download=True)
        filename = None
        requested = info_dict.get('requested_downloads')
        if requested:
            for req_item in requested:
                fp = req_item.get('filepath')
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

        if filename and os.path.exists(os.path.join(DOWNLOADS_DIR, filename)):
            return filename
    return None

def download_with_pytubefix(url, format_id, ffmpeg_exe):
    from pytubefix import YouTube
    for client in ['ANDROID', 'WEB']:
        try:
            yt = YouTube(url, client=client)
            title = yt.title or f"video_{int(time.time())}"
            safe_title = re.sub(r'[\\/*?:\'\"<>|]', '', title)[:100].strip() or f"video_{int(time.time())}"
            timestamp = int(time.time())
            out_filename = f"{safe_title}_{timestamp}.mp4"
            out_file = os.path.join(DOWNLOADS_DIR, out_filename)

            stream = None
            if format_id and str(format_id).isdigit():
                try:
                    stream = yt.streams.get_by_itag(int(format_id))
                except Exception:
                    pass
            if not stream and format_id:
                itag_map = {
                    '401': '2160p', '400': '1440p', '399': '1080p', '398': '720p',
                    '397': '480p', '396': '360p', '395': '240p', '394': '144p',
                    '160': '144p', '133': '240p', '134': '360p', '135': '480p', '136': '720p'
                }
                target_res = itag_map.get(str(format_id), str(format_id))
                stream = yt.streams.filter(res=target_res, mime_type='video/mp4').first() or yt.streams.filter(res=target_res).first()

            if not stream:
                stream = yt.streams.get_highest_resolution() or yt.streams.first()

            if not stream:
                continue

            if stream.is_progressive:
                stream.download(output_path=DOWNLOADS_DIR, filename=out_filename)
                if os.path.exists(out_file) and os.path.getsize(out_file) > 0:
                    return out_filename
            else:
                temp_v = os.path.join(DOWNLOADS_DIR, f"temp_v_{timestamp}.{stream.subtype or 'mp4'}")
                stream.download(output_path=DOWNLOADS_DIR, filename=os.path.basename(temp_v))

                a_stream = yt.streams.get_audio_only()
                if a_stream:
                    temp_a = os.path.join(DOWNLOADS_DIR, f"temp_a_{timestamp}.{a_stream.subtype or 'm4a'}")
                    a_stream.download(output_path=DOWNLOADS_DIR, filename=os.path.basename(temp_a))

                    cmd = [ffmpeg_exe, '-y', '-i', temp_v, '-i', temp_a, '-c:v', 'copy', '-c:a', 'aac', out_file]
                    subprocess.run(cmd, capture_output=True, timeout=120)
                    if os.path.exists(temp_a):
                        try: os.remove(temp_a)
                        except Exception: pass
                    if os.path.exists(temp_v):
                        try: os.remove(temp_v)
                        except Exception: pass
                    if os.path.exists(out_file) and os.path.getsize(out_file) > 0:
                        return out_filename
                else:
                    os.rename(temp_v, out_file)
                    return out_filename
        except Exception as e:
            print(f"pytubefix ({client}) attempt failed: {e}")
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
    return jsonify({
        "status": "ok",
        "engines": ["yt-dlp", "pytubefix", "InvidiousProxyDownloader"],
        "ffmpeg": get_ffmpeg()
    })

@app.errorhandler(Exception)
def handle_exception(e):
    return jsonify({"success": False, "error": f"Server Error: {str(e)}"}), 500

@app.route('/download', methods=['POST'])
def download_video():
    cleanup_downloads()
    url = request.form.get('url')
    if not url:
        return jsonify({"success": False, "error": "Missing URL parameter."})

    format_id = request.form.get('format_id')
    ffmpeg_exe = get_ffmpeg()
    vid_id = extract_video_id(url)
    last_error = ""

    # Engine 1: yt-dlp with player_skip: ['webpage'] & web_embedded/android/ios/tv_simply
    try:
        filename = download_with_ytdlp(url, format_id, ffmpeg_exe)
        if filename:
            return jsonify({
                "success": True,
                "filename": filename,
                "download_url": f"/files/{quote(filename)}"
            })
    except Exception as ydl_err:
        last_error += f"yt-dlp: {ydl_err} | "
        print(f"Engine 1 (yt-dlp) failed: {ydl_err}. Trying Engine 2...")

    # Engine 2: pytubefix (ANDROID client)
    try:
        filename = download_with_pytubefix(url, format_id, ffmpeg_exe)
        if filename:
            return jsonify({
                "success": True,
                "filename": filename,
                "download_url": f"/files/{quote(filename)}"
            })
    except Exception as pt_err:
        last_error += f"pytubefix: {pt_err} | "
        print(f"Engine 2 (pytubefix) failed: {pt_err}. Trying Engine 3...")

    # Engine 3: Invidious Proxy with Anubis solver fallback
    if vid_id:
        try:
            inv_data = fetch_invidious_data(vid_id)
            if inv_data:
                title = inv_data.get('title', f"video_{vid_id}")
                safe_title = re.sub(r'[\\/*?:\'\"<>|]', '', title)[:100].strip() or f"video_{vid_id}"
                timestamp = int(time.time())
                out_filename = f"{safe_title}_{timestamp}.mp4"
                out_file = os.path.join(DOWNLOADS_DIR, out_filename)

                downloader = InvidiousProxyDownloader()
                if downloader.download_and_merge(inv_data, format_id, out_file, ffmpeg_exe):
                    return jsonify({
                        "success": True,
                        "filename": out_filename,
                        "download_url": f"/files/{quote(out_filename)}"
                    })
        except Exception as inv_err:
            last_error += f"InvidiousProxy: {inv_err} | "
            print(f"Engine 3 (Invidious) failed: {inv_err}")

    return jsonify({"success": False, "error": f"Download failed across all engines: {last_error}"})

@app.route('/get-formats', methods=['POST'])
def get_formats():
    url = request.form.get('url')
    if not url:
        return jsonify({"success": False, "error": "Missing URL parameter."})

    # Strategy 1: yt-dlp with player_skip: ['webpage']
    try:
        ffmpeg_exe = get_ffmpeg()
        ydl_opts = {
            'quiet': True,
            'noplaylist': True,
            'socket_timeout': 25,
            'nocheckcertificate': True,
            'ffmpeg_location': ffmpeg_exe,
            'js_runtimes': {'node': {}},
            'extractor_args': {
                'youtube': {
                    'player_client': ['web_embedded', 'android', 'ios', 'tv_simply', 'mweb'],
                    'player_skip': ['webpage']
                }
            },
        }
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
                        'format_id': str(f.get('format_id')),
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
        print(f"Strategy 1 (yt-dlp) get-formats failed: {ydl_err}. Trying Strategy 2...")

    # Strategy 2: pytubefix (ANDROID)
    try:
        from pytubefix import YouTube
        yt = YouTube(url, client='ANDROID')
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
        print(f"Strategy 2 (pytubefix) get-formats failed: {pt_err}. Trying Strategy 3...")

    # Strategy 3: Invidious API
    vid_id = extract_video_id(url)
    if vid_id:
        try:
            inv_data = fetch_invidious_data(vid_id)
            if inv_data:
                formats = []
                seen_res = set()
                for f in inv_data.get('adaptiveFormats', []):
                    if 'video' in f.get('type', '') and f.get('resolution'):
                        res = f.get('resolution')
                        clen = int(f.get('clen', 0)) if str(f.get('clen', '')).isdigit() else 0
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
        except Exception as inv_err:
            print(f"Strategy 3 (Invidious) get-formats failed: {inv_err}")

    return jsonify({"success": False, "error": "Unable to extract video formats. Please verify the URL or try again."})

@app.route('/files/<path:filename>')
def serve_file(filename):
    return send_from_directory(DOWNLOADS_DIR, filename, as_attachment=True, download_name=filename)

if __name__ == '__main__':
    app.run(debug=True)
