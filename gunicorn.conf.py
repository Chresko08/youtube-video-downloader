import os

bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"
workers = 2
threads = 4
timeout = 300
keepalive = 5
max_requests = 100
max_requests_jitter = 10
