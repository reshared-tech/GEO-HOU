# GEO-HOU Web 服务。
# 本项目零第三方依赖,整个镜像不需要 pip install,构建秒级、无供应链面。
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    GEO_HOST=0.0.0.0 \
    GEO_PORT=8000

WORKDIR /app

# 只拷运行期真正需要的东西:引擎(scripts)、服务(web)、知识库与模板(source)
COPY scripts/ /app/scripts/
COPY web/     /app/web/
COPY source/  /app/source/
COPY VERSION  /app/VERSION

# 非 root 运行。容器内无任何可写业务目录——服务本身不落盘。
RUN useradd --system --create-home --shell /usr/sbin/nologin geo \
 && chown -R root:root /app && chmod -R a-w /app
USER geo

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python3 -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=2).read()==b'ok' else 1)"

CMD ["python3", "web/server.py"]
