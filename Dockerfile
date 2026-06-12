# 공식 Advanced(OCR) Data Loader 이미지에는 redis 파이썬 라이브러리가 없어서
# Redis VectorDB 플러그인(plugins.vectordbs.redis) 로드 시
# "ModuleNotFoundError: No module named 'redis'" 오류 발생 → redis 패키지만 추가 설치
FROM public.ecr.aws/tds/data-loader-pdf-unstruct:latest

# --trusted-host: 사내망 SSL 검사(self-signed 인증서) 환경에서 pip 인증서 오류 회피
# --break-system-packages 폴백: 시스템 파이썬이 externally-managed 인 경우 대비
# redis 5.x 고정: 이미지 내 langchain 코드가 redis-py 8.x 와 API 가 안 맞아
#                 /load 호출 시 AttributeError 발생 → 당시 호환 버전으로 설치
# 주의: 이 이미지에는 spotuser 계정이 없으므로 USER 전환 없이 기본 사용자(root)로 실행
RUN python -m pip install --no-cache-dir \
        --trusted-host pypi.org \
        --trusted-host files.pythonhosted.org \
        "redis==5.0.8" \
    || python -m pip install --no-cache-dir --break-system-packages \
        --trusted-host pypi.org \
        --trusted-host files.pythonhosted.org \
        "redis==5.0.8"

# 이미지에 내장된 Redis 플러그인은 getVectorDB 미구현(데이터 로더에서 사용 불가)
# → getVectorDB 를 구현한 패치 파일로 교체 (파일 상단 주석 참고)
COPY redis_vectordb_plugin.py /app/plugins/vectordbs/redis.py

# 사내망 SSL 검사 환경에서 hi_res 모델(HuggingFace)/NLTK 데이터 다운로드가
# CERTIFICATE_VERIFY_FAILED 로 실패하는 것을 회피 (파일 상단 주석 참고)
COPY sitecustomize_ssl_bypass.py /usr/lib/python3.11/site-packages/sitecustomize.py
