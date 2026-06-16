FROM python:3.12-slim

# 使用清华 pip 镜像源加速下载
WORKDIR /app
COPY requirements.txt .
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p uploads

EXPOSE 8000

# 多 worker 提升并发能力。WORKERS 默认 2，可通过环境变量调整。
# 使用 gunicorn + uvicorn worker，支持多进程并行处理请求。
# --max-requests 定期回收 worker 防止内存泄漏。
# 会话和比对任务进度通过文件系统共享（uploads/sessions/,
# uploads/compare_tasks/），多 worker 可正常协作。
# 高并发生产环境建议接入 Redis 替代文件系统共享存储。
CMD ["sh", "-c", "mkdir -p uploads/sessions uploads/compare_tasks && gunicorn -w ${WORKERS:-2} -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000 --max-requests 1000 --max-requests-jitter 100 --timeout 600 --graceful-timeout 30 app.main:app"]
