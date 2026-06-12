# 공식 Advanced(OCR) Data Loader 이미지에는 redis 파이썬 라이브러리가 없어서
# Redis VectorDB 플러그인(plugins.vectordbs.redis) 로드 시
# "ModuleNotFoundError: No module named 'redis'" 오류 발생 → redis 패키지만 추가 설치
FROM public.ecr.aws/tds/data-loader-pdf-unstruct:latest

USER root
# 시스템 파이썬이 externally-managed 인 경우를 대비해 --break-system-packages 로 폴백
RUN python -m pip install --no-cache-dir redis \
    || python -m pip install --no-cache-dir --break-system-packages redis
USER spotuser
