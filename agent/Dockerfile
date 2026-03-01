FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple \
    PIP_TRUSTED_HOST=mirrors.aliyun.com \
    TZ=Asia/Shanghai

WORKDIR /app

# 系统依赖：LaTeX 编译（xelatex）+ 中文字体 + 基础构建工具
RUN if [ -f /etc/apt/sources.list ]; then \
      sed -i 's@deb.debian.org@mirrors.aliyun.com@g; s@security.debian.org@mirrors.aliyun.com@g' /etc/apt/sources.list; \
    fi \
    && if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
      sed -i 's@deb.debian.org@mirrors.aliyun.com@g; s@security.debian.org@mirrors.aliyun.com@g' /etc/apt/sources.list.d/debian.sources; \
    fi \
    && apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    ca-certificates \
    curl \
    fonts-noto-cjk \
    texlive-xetex \
    texlive-lang-chinese \
    texlive-latex-extra \
    texlive-science \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

COPY . /app

# 运行时目录
RUN mkdir -p /app/uploads /app/outputs /app/instance

EXPOSE 5001

# SQLite + 本地文件任务，建议单 worker，多线程
CMD ["gunicorn", "-b", "0.0.0.0:5001", "--workers", "1", "--threads", "8", "--timeout", "300", "app:app"]
