FROM python:3.12-slim
# Fixtures use unittest from the standard library: no runtime downloads needed.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp
WORKDIR /workspace
USER 65532:65532
CMD ["python", "--version"]
