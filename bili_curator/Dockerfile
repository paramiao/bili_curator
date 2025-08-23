FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 安装基础系统依赖（最小化，多镜像回退策略）
RUN set -eux; \
    echo 'APT::Acquire::Retries "3";' > /etc/apt/apt.conf.d/80-retries; \
    echo 'APT::Get::Assume-Yes "true";' > /etc/apt/apt.conf.d/90-assumeyes; \
    \
    # 函数：切换镜像源
    switch_mirror() { \
      local mirror_base="$1"; \
      echo "Switching to mirror: $mirror_base"; \
      if [ -f /etc/apt/sources.list ]; then \
        sed -i "s|http://deb.debian.org/debian|$mirror_base/debian|g" /etc/apt/sources.list || true; \
        sed -i "s|http://security.debian.org/debian-security|$mirror_base/debian-security|g" /etc/apt/sources.list || true; \
      fi; \
      if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i "s|http://deb.debian.org/debian|$mirror_base/debian|g" /etc/apt/sources.list.d/debian.sources || true; \
        sed -i "s|http://security.debian.org/debian-security|$mirror_base/debian-security|g" /etc/apt/sources.list.d/debian.sources || true; \
      fi; \
    }; \
    \
    # 尝试安装，多镜像回退
    apt-get update && apt-get install -y --no-install-recommends curl ffmpeg || ( \
      echo "Default mirror failed, trying USTC HTTPS..."; \
      switch_mirror "https://mirrors.ustc.edu.cn"; \
      apt-get update && apt-get install -y --no-install-recommends curl ffmpeg \
    ) || ( \
      echo "USTC failed, trying Aliyun HTTP..."; \
      switch_mirror "http://mirrors.aliyun.com"; \
      apt-get update && apt-get install -y --no-install-recommends curl ffmpeg \
    ) || ( \
      echo "Aliyun failed, trying Tsinghua HTTPS..."; \
      switch_mirror "https://mirrors.tuna.tsinghua.edu.cn"; \
      apt-get update && apt-get install -y --no-install-recommends curl ffmpeg \
    ); \
    rm -rf /var/lib/apt/lists/*

# 复制并安装Python依赖
COPY requirements.txt .
COPY requirements-v7.txt .

# 安装Python依赖 (根据VERSION环境变量决定)
RUN pip install --no-cache-dir -r requirements.txt && \
    if [ "${VERSION:-v6}" != "v6" ]; then \
        pip install --no-cache-dir -r requirements-v7.txt; \
    fi && \
    pip install --no-cache-dir --upgrade yt-dlp

# 复制应用代码
COPY . .

# 创建必要目录
RUN mkdir -p /app/data/downloads /app/logs /app/web/dist

# 创建非root用户
RUN useradd -m -u 1000 bili && \
    chown -R bili:bili /app
USER bili

# 暴露端口
EXPOSE 8080

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# 启动命令
CMD ["python", "main.py"]
