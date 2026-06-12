# 공식 Advanced(OCR) Data Loader 이미지에는 redis 파이썬 라이브러리가 없어서
# Redis VectorDB 플러그인(plugins.vectordbs.redis) 로드 시
# "ModuleNotFoundError: No module named 'redis'" 오류 발생 → redis 패키지만 추가 설치
FROM public.ecr.aws/tds/data-loader-pdf-unstruct:latest

USER root
# --trusted-host: 사내망 SSL 검사(self-signed 인증서) 환경에서 pip 인증서 오류 회피
# --break-system-packages 폴백: 시스템 파이썬이 externally-managed 인 경우 대비
RUN python -m pip install --no-cache-dir \
        --trusted-host pypi.org \
        --trusted-host files.pythonhosted.org \
        redis \
    || python -m pip install --no-cache-dir --break-system-packages \
        --trusted-host pypi.org \
        --trusted-host files.pythonhosted.org \
        redis
USER spotuser
