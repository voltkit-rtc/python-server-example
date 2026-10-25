FROM python:3.13-slim
WORKDIR /app
COPY app ./app
COPY test ./test
USER nobody
ENV PORT=7000 HOST=0.0.0.0 PYTHONUNBUFFERED=1
EXPOSE 7000
HEALTHCHECK --interval=15s --timeout=5s CMD python -c "import urllib.request,os,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT','7000')).status==200 else 1)"
CMD ["python", "-m", "app"]
