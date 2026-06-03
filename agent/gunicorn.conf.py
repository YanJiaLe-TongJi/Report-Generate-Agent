import os


bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:5001")
workers = int(os.environ.get("GUNICORN_WORKERS", "1"))
threads = int(os.environ.get("GUNICORN_THREADS", "8"))
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "600"))  # 增加超时到 600 秒
graceful_timeout = int(os.environ.get("GUNICORN_GRACEFUL_TIMEOUT", "60"))
keepalive = int(os.environ.get("GUNICORN_KEEPALIVE", "5"))

accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "debug")  # 临时启用 debug 日志

worker_class = "gthread"
preload_app = False

# 增加请求限制（禁用大部分请求大小限制以支持大文件上传）
limit_request_line = 0  # 禁用限制
limit_request_fields = 0  # 禁用限制  
limit_request_field_size = 0  # 禁用限制

