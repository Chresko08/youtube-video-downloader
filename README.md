# YouTube Video Downloader

A simple web application to download YouTube videos with quality selection.

## Features

- 🎬 Download YouTube videos in multiple qualities
- 🎨 Light/Dark mode toggle
- 📱 Responsive design
- 💾 Direct browser downloads

## Tech Stack

- **Backend**: Flask (Python)
- **Frontend**: HTML, CSS, JavaScript
- **Video Processing**: yt-dlp, ffmpeg

## Local Setup

1. Clone the repository:
```bash
git clone <your-repo-url>
cd youtube-video-downloader
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Install ffmpeg (required for video conversion):
```bash
# macOS
brew install ffmpeg

# Ubuntu/Debian
sudo apt-get install ffmpeg

# Windows
# Download from https://ffmpeg.org/download.html
```

4. Run the application:
```bash
python3 app.py
```

5. Open in browser:
```
http://127.0.0.1:5000
```

## Deployment

### Railway

1. Push your code to GitHub
2. Connect your GitHub repository to Railway
3. Railway will automatically detect Flask and deploy
4. Make sure to install ffmpeg in Railway (add to nixpacks.toml or use buildpack)

## Important Notes

⚠️ **Legal Disclaimer**: This tool is for educational purposes only. Downloading copyrighted content without permission may violate YouTube's Terms of Service. Use responsibly.

## Credits

Created by **Shubham Srivastava** © 2024  
Improved using AI: Google Antigravity (Google DeepMind)

## License

MIT License - See LICENSE file for details
